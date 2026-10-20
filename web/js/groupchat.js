// 群聊：像微信群一样和成员聊天。成员只回应你，不主动发消息；@昵称 指定谁回应，@全员 大家都说。
// 发出后成员在后台依次回应，这里轮询 /api/chat?after=… 拿新消息和「正在输入」。
// 键盘：Enter 发送、Shift+Enter 换行（输入法选字时不发送）；输入 @ 弹出成员候选，↑↓ 选择、Enter / Tab 确认、Esc 关闭。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const FAST_MS = 900; // 有人正在输入或刚发出消息时
const SLOW_MS = 8000;
const FAST_FOR_MS = 25_000; // 发出消息后这么久内保持快速轮询
const EVERYONE = '全员';

export const composing = (e) => e.isComposing || e.keyCode === 229;

/** 光标前正在输入的 @ 片段（没有返回 null） */
export function atQuery(text, caret) {
  const m = /@([^\s@]{0,12})$/.exec(text.slice(0, caret));
  return m ? { start: caret - m[0].length, q: m[1] } : null;
}

export class GroupChat {
  constructor(root, { api, now = () => Date.now() } = {}) {
    this.root = root;
    this.api = api; // {get(after, q), post(text), clear()}
    this.now = now;
    this.art = null;
    this.members = [];
    this.messages = [];
    this.typing = [];
    this.lastId = 0;
    this.fastUntil = 0;
    this.timer = null;
    this.search = null; // {q, results}
    this.suggest = null; // {start, items, index}
    this.armedClear = 0;
    this.error = '';
    this.sending = false;
    this.built = false;
  }

  setArt(art) {
    this.art = art && art.available ? art : null;
    if (this.built) this.renderAll();
  }

  look(member) {
    const id = this.art?.models?.[member?.model_id];
    return (this.art?.members || []).find((m) => m.id === id) || null;
  }

  async show() {
    this.root.hidden = false;
    if (!this.built) this.build();
    await this.poll();
    this.$('#chat-input').focus({ preventScroll: true });
  }

  hide() {
    this.root.hidden = true;
    clearTimeout(this.timer);
    this.timer = null;
  }

  $(q) {
    return this.root.querySelector(q);
  }

  build() {
    this.built = true;
    this.root.innerHTML = `
      <aside class="gc-members" aria-label="群成员"><h3>群成员</h3><ul id="gc-members"></ul>
        <p class="hint">成员都是 AI，只回应你、不会主动发消息。点名字可以 @ TA。</p></aside>
      <section class="gc-main" aria-label="群聊">
        <header class="gc-head"><b id="gc-title">大家庭</b>
          <form id="gc-search" role="search"><input type="search" id="gc-q" placeholder="搜索聊天记录" aria-label="搜索聊天记录"></form>
          <button type="button" class="btn" id="gc-clear" title="删除全部聊天记录（花费记录保留）">清空记录</button></header>
        <div class="gc-feed" id="gc-feed" aria-live="polite"></div>
        <div class="gc-typing" id="gc-typing" hidden></div>
        <form class="gc-compose" id="gc-compose" autocomplete="off">
          <div class="gc-suggest" id="gc-suggest" role="listbox" hidden></div>
          <textarea id="chat-input" rows="2" maxlength="2000" placeholder="说点什么：Enter 发送，Shift+Enter 换行；输入 @ 点名" aria-label="群聊消息"></textarea>
          <button class="btn primary" type="submit" id="gc-send">发送</button>
        </form>
        <p class="hint gc-err" id="gc-err" role="alert" hidden></p>
      </section>`;
    this.$('#gc-compose').addEventListener('submit', (e) => {
      e.preventDefault();
      this.send();
    });
    const input = this.$('#chat-input');
    input.addEventListener('keydown', (e) => this.onKey(e));
    input.addEventListener('input', () => this.updateSuggest());
    input.addEventListener('blur', () => setTimeout(() => this.closeSuggest(), 150));
    this.$('#gc-suggest').addEventListener('mousedown', (e) => {
      const b = e.target.closest('[data-name]');
      if (!b) return;
      e.preventDefault();
      this.applySuggest(b.dataset.name);
    });
    this.$('#gc-members').addEventListener('click', (e) => {
      const b = e.target.closest('[data-name]');
      if (b) this.insertMention(b.dataset.name);
    });
    this.$('#gc-search').addEventListener('submit', (e) => {
      e.preventDefault();
      this.runSearch(this.$('#gc-q').value.trim());
    });
    this.$('#gc-q').addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.target.value = '';
        this.runSearch('');
      }
    });
    this.$('#gc-feed').addEventListener('click', (e) => {
      if (e.target.closest('#gc-exit-search')) this.runSearch('');
    });
    this.$('#gc-clear').addEventListener('click', () => this.clear());
    this.renderAll();
  }

  // --- 数据 ------------------------------------------------------------------

  schedule() {
    clearTimeout(this.timer);
    if (this.root.hidden) return;
    const fast = this.typing.length || this.now() < this.fastUntil;
    this.timer = setTimeout(() => this.poll(), fast ? FAST_MS : SLOW_MS);
  }

  async poll() {
    try {
      const data = await this.api.get(this.lastId);
      this.error = '';
      const roster = JSON.stringify(data.members || []);
      const newMembers = roster !== this.roster;
      this.roster = roster;
      this.members = data.members || [];
      if ((data.messages || []).length) {
        this.messages.push(...data.messages);
        this.lastId = this.messages[this.messages.length - 1].id;
      }
      const changed = newMembers || (data.messages || []).length || String(data.typing) !== String(this.typing);
      this.typing = data.typing || [];
      if (changed) this.renderAll();
    } catch (e) {
      this.error = e.message || '连接失败';
      this.renderError();
    }
    this.schedule();
  }

  async send() {
    const input = this.$('#chat-input');
    const text = input.value.trim();
    if (!text || this.sending) return;
    this.sending = true;
    this.$('#gc-send').disabled = true;
    try {
      await this.api.post(text);
      input.value = '';
      this.closeSuggest();
      this.fastUntil = this.now() + FAST_FOR_MS;
      this.error = '';
    } catch (e) {
      this.error = e.message || '发送失败';
    }
    this.sending = false;
    this.$('#gc-send').disabled = false;
    this.renderError();
    await this.poll();
  }

  async runSearch(q) {
    if (!q) {
      this.search = null;
      this.renderFeed();
      return;
    }
    try {
      const data = await this.api.get(0, q);
      this.search = { q, results: data.messages || [] };
    } catch (e) {
      this.error = e.message || '搜索失败';
    }
    this.renderFeed();
    this.renderError();
  }

  async clear() {
    const b = this.$('#gc-clear');
    // 不用浏览器的确认框：第一次点击变成「再点一次」，4 秒内再点才清空
    if (this.now() > this.armedClear) {
      this.armedClear = this.now() + 4000;
      b.textContent = '再点一次确认清空';
      b.classList.add('gold');
      setTimeout(() => {
        if (this.now() > this.armedClear) {
          b.textContent = '清空记录';
          b.classList.remove('gold');
        }
      }, 4100);
      return;
    }
    this.armedClear = 0;
    b.textContent = '清空记录';
    b.classList.remove('gold');
    try {
      await this.api.clear();
      this.messages = [];
      this.search = null;
      this.renderFeed();
    } catch (e) {
      this.error = e.message || '清空失败';
      this.renderError();
    }
  }

  // --- @ 候选 ----------------------------------------------------------------

  names() {
    return [...this.members.filter((m) => m.available).map((m) => m.name), EVERYONE];
  }

  updateSuggest() {
    const input = this.$('#chat-input');
    const at = atQuery(input.value, input.selectionStart);
    if (!at) return this.closeSuggest();
    const items = this.names().filter((n) => n.toLowerCase().includes(at.q.toLowerCase()));
    if (!items.length) return this.closeSuggest();
    this.suggest = { start: at.start, items, index: 0 };
    this.renderSuggest();
  }

  renderSuggest() {
    const box = this.$('#gc-suggest');
    const s = this.suggest;
    if (!s) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    box.innerHTML = s.items
      .map((n, i) => `<button type="button" role="option" data-name="${esc(n)}" aria-selected="${i === s.index}">@${esc(n)}</button>`)
      .join('');
  }

  closeSuggest() {
    this.suggest = null;
    this.renderSuggest();
  }

  applySuggest(name) {
    const input = this.$('#chat-input');
    const s = this.suggest;
    const caret = input.selectionStart;
    const start = s ? s.start : caret;
    input.value = `${input.value.slice(0, start)}@${name} ${input.value.slice(caret)}`;
    const pos = start + name.length + 2;
    input.setSelectionRange(pos, pos);
    this.closeSuggest();
    input.focus();
  }

  insertMention(name) {
    const input = this.$('#chat-input');
    const caret = input.selectionStart ?? input.value.length;
    this.suggest = { start: caret, items: [name], index: 0 };
    this.applySuggest(name);
  }

  onKey(e) {
    if (composing(e)) return;
    const s = this.suggest;
    if (s && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault();
      s.index = (s.index + (e.key === 'ArrowDown' ? 1 : -1) + s.items.length) % s.items.length;
      this.renderSuggest();
      return;
    }
    if (s && (e.key === 'Enter' || e.key === 'Tab') && !e.shiftKey) {
      e.preventDefault();
      this.applySuggest(s.items[s.index]);
      return;
    }
    if (s && e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      this.closeSuggest();
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.altKey) {
      e.preventDefault();
      this.send();
    }
  }

  // --- 画面 ------------------------------------------------------------------

  avatar(member, name) {
    const m = this.look(member);
    if (m?.images?.avatar_64) return `<img class="gc-av" src="${esc(m.images.avatar_64)}" alt="">`;
    const color = m?.theme_color || 'var(--c-coord)';
    return `<span class="gc-av ini" style="background:${esc(color)}">${esc((name || '?').slice(0, 1))}</span>`;
  }

  bubble(msg) {
    if (msg.author === 'system') return `<div class="gc-sys" data-id="${msg.id}">${esc(msg.text)}</div>`;
    const time = new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const text = esc(msg.text).replace(/@(\S{1,12})/g, '<b class="at">@$1</b>').replace(/\n/g, '<br>');
    if (msg.author === 'user') return `<div class="gc-msg me" data-id="${msg.id}"><div class="gc-body"><div class="gc-bub">${text}</div><time>${esc(time)}</time></div></div>`;
    const member = this.members.find((m) => m.id === msg.author);
    return `<div class="gc-msg" data-id="${msg.id}" data-author="${esc(msg.author)}">${this.avatar(member, msg.name)}<div class="gc-body"><span class="gc-name">${esc(msg.name)}</span><div class="gc-bub">${text}</div><time>${esc(time)}</time></div></div>`;
  }

  renderFeed() {
    const feed = this.$('#gc-feed');
    if (this.search) {
      const r = this.search.results;
      feed.innerHTML = `<div class="gc-sys">搜索「${esc(this.search.q)}」：${r.length} 条 <button type="button" class="link" id="gc-exit-search">返回聊天</button></div>${r.map((m) => this.bubble(m)).join('')}`;
      return;
    }
    const near = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
    feed.innerHTML = this.messages.length
      ? this.messages.map((m) => this.bubble(m)).join('')
      : '<p class="empty">还没有消息。打个招呼吧：成员都是 AI，会坦诚自己是 AI。</p>';
    if (near || this.stick !== false) feed.scrollTop = feed.scrollHeight;
    this.stick = near;
  }

  renderMembers() {
    this.$('#gc-title').textContent = `大家庭（${this.members.length + 1} 人）`;
    this.$('#gc-members').innerHTML = this.members
      .map((m) => `<li><button type="button" class="link${m.available ? '' : ' off'}" data-name="${esc(m.name)}" title="${m.available ? `@${esc(m.name)}` : '没有可用渠道'}">${this.avatar(m, m.name)}<span>${esc(m.name)}</span>${m.available ? '' : '<span class="pill badp">离线</span>'}</button></li>`)
      .join('');
  }

  renderTyping() {
    const t = this.$('#gc-typing');
    t.hidden = !this.typing.length;
    t.textContent = this.typing.length ? `${this.typing.join('、')} 正在输入…` : '';
  }

  renderError() {
    const el = this.$('#gc-err');
    if (!el) return;
    el.hidden = !this.error;
    el.textContent = this.error;
  }

  renderAll() {
    this.renderMembers();
    this.renderFeed();
    this.renderTyping();
    this.renderError();
  }
}
