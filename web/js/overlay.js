// 一起看的悬浮窗 / 画中画共用的界面（docs/WATCH.md「悬浮窗」）：最近几条聊天、点一条就回那个人、打字说话、
// 调透明度、收成胶囊、投屏停了自己关。只通过 HTTP 和圆桌说话（项目规则 §2.7）。
// host 决定窗口本身怎么变：电脑上的应用小窗（pywebview）能调整个窗口的透明度和大小；画中画只能调背景深浅。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const POLL_MS = 1500;
const STATUS_MS = 4000;
const CLOSE_AFTER_STOP_MS = 6000;
const SHOW = 8;
// 头像底色：按名字依次分配（和是谁无关）
const COLORS = ['#e8c98a', '#9cc4e8', '#b3d9a6', '#e8aec2', '#c9b5ea', '#eab595'];

async function call(path, body) {
  const init = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch(path, init);
  const data = await r.json().catch(() => null);
  if (!r.ok) throw new Error((data && typeof data.detail === 'string' && data.detail) || `请求失败（${r.status}）`);
  return data;
}

/** 电脑上的应用小窗：Python 那边的 js_api（透明度、收起时改窗口大小、关窗、记住设置）。 */
export function pywebviewHost() {
  const api = window.pywebview?.api;
  return {
    opacity: Boolean(api),
    async state() {
      return api ? await api.get_state() : {};
    },
    setOpacity: (a) => api?.set_opacity(a),
    setSmall: (small) => api?.set_collapsed(small),
    close: () => (api ? api.close() : window.close()),
  };
}

export class OverlayView {
  /**
   * @param {HTMLElement} el 挂载点（悬浮窗页面里的 #ov，或画中画文档里的一个 div）
   * @param {{host: object, doc?: Document, onClose?: Function}} opts
   */
  constructor(el, { host, doc = document, onClose } = {}) {
    Object.assign(this, { el, host, doc, onClose });
    this.msgs = [];
    this.lastId = 0;
    this.typing = [];
    this.unread = 0;
    this.small = false;
    this.shade = 0.78;
    this.alpha = 0.92;
    this.to = null; // 正在回复谁：{name, id}
    this.colors = new Map();
    this.st = { running: true };
    this.timers = [];
  }

  async start() {
    const saved = (await this.host.state?.().catch(() => ({}))) || {};
    if (typeof saved.alpha === 'number') this.alpha = saved.alpha;
    if (typeof saved.collapsed === 'boolean') this.small = saved.collapsed;
    this.render();
    await this.refresh();
    await this.pull();
    this.timers.push(setInterval(() => this.pull(), POLL_MS));
    this.timers.push(setInterval(() => this.refresh(), STATUS_MS));
    return this;
  }

  stop() {
    this.timers.forEach(clearInterval);
    this.timers = [];
  }

  color(name) {
    if (!this.colors.has(name)) this.colors.set(name, COLORS[this.colors.size % COLORS.length]);
    return this.colors.get(name);
  }

  async refresh() {
    try {
      this.st = await call('/api/watch');
    } catch {
      return;
    }
    if (!this.st.running && !this.closing) {
      this.closing = true;
      this.note = '投屏已停止，小窗马上关掉';
      if (this.small) this.setSmall(false);
      else this.paintFeed();
      setTimeout(() => this.close(), CLOSE_AFTER_STOP_MS);
    }
    const src = this.el.querySelector('.ov-src');
    if (src) src.textContent = this.st.source?.title || '';
  }

  async pull() {
    try {
      const data = await call(`/api/watch/messages?after=${this.lastId}`);
      const fresh = data.messages.filter((m) => m.id > this.lastId);
      if (fresh.length) {
        this.msgs = [...this.msgs, ...fresh].slice(-100);
        this.lastId = fresh[fresh.length - 1].id;
        if (this.small) this.unread += fresh.filter((m) => m.author !== 'user' && m.author !== 'system').length;
      }
      this.typing = data.typing || [];
    } catch {
      return;
    }
    if (this.small) this.paintPill();
    else this.paintFeed();
  }

  render() {
    const root = this.doc.createElement('div');
    root.className = `ov-root${this.small ? ' small' : ''}`;
    root.style.setProperty('--ov-shade', String(this.shade));
    if (this.small) {
      root.innerHTML = `<div class="ov-pillwrap pywebview-drag-region"><span class="ov-dot" aria-hidden="true"></span>
        <button type="button" class="ov-pill" data-expand title="展开">一起看<span class="ov-badge" hidden></span></button></div>`;
    } else {
      const slider = this.host.opacity
        ? `<input class="ov-shade" type="range" min="30" max="100" value="${Math.round(this.alpha * 100)}" data-alpha title="透明度" aria-label="透明度">`
        : `<input class="ov-shade" type="range" min="20" max="100" value="${Math.round(this.shade * 100)}" data-shade title="背景深浅" aria-label="背景深浅">`;
      root.innerHTML = `
        <div class="ov-bar pywebview-drag-region">
          <span class="ov-dot" aria-hidden="true"></span><b class="ov-title">一起看</b>
          <span class="ov-src">${esc(this.st.source?.title || '')}</span>
          ${slider}
          <button type="button" class="ov-icon" data-small title="收起" aria-label="收起">－</button>
          <button type="button" class="ov-icon" data-close title="关掉小窗（不停投屏）" aria-label="关掉小窗">×</button>
        </div>
        <div class="ov-feed" aria-live="polite"></div>
        <form class="ov-say" autocomplete="off">
          <div class="ov-to" hidden>回复 <b></b><button type="button" data-unto aria-label="不回复了">×</button></div>
          <div class="ov-row"><input maxlength="1000" placeholder="说点什么；点一条消息可以回她" aria-label="说话"><button class="ov-send" type="submit">发送</button></div>
        </form>`;
    }
    this.el.replaceChildren(root);
    root.addEventListener('click', (e) => this.onClick(e));
    root.addEventListener('input', (e) => this.onInput(e));
    root.addEventListener('submit', (e) => this.onSubmit(e));
    if (this.small) this.paintPill();
    else {
      this.paintTo();
      this.paintFeed();
    }
  }

  paintPill() {
    const badge = this.el.querySelector('.ov-badge');
    if (!badge) return;
    badge.hidden = !this.unread;
    badge.textContent = String(this.unread);
  }

  paintTo() {
    const box = this.el.querySelector('.ov-to');
    if (!box) return;
    box.hidden = !this.to;
    box.querySelector('b').textContent = this.to ? this.to.name : '';
  }

  paintFeed() {
    const feed = this.el.querySelector('.ov-feed');
    if (!feed) return;
    const byId = new Map(this.msgs.map((m) => [m.id, m]));
    const rows = this.msgs.slice(-SHOW).map((m) => {
      if (m.author === 'system') return `<p class="ov-sys">${esc(m.text)}</p>`;
      const ref = m.reply_to ? byId.get(m.reply_to) : null;
      const quote = ref && ref.author !== 'system' ? `<span class="ov-quote">↪ ${esc(ref.author === 'user' ? '我' : ref.name)}：${esc(ref.text)}</span>` : '';
      if (m.author === 'user') {
        return `<div class="ov-msg me"><div class="ov-body">${quote}<span class="ov-text">${esc(m.text)}</span></div></div>`;
      }
      const name = m.name || m.author;
      return `<button type="button" class="ov-msg" data-reply="${esc(name)}" data-id="${m.id}" title="回复 ${esc(name)}">
        <span class="ov-av" style="background:${this.color(name)}" aria-hidden="true">${esc(name.slice(0, 1))}</span>
        <span class="ov-body">${quote}<span class="ov-name">${esc(name)}</span><span class="ov-text">${esc(m.text)}</span></span></button>`;
    });
    if (this.typing.length) rows.push(`<p class="ov-sys">${this.typing.map(esc).join('、')} 正在输入…</p>`);
    if (this.note) rows.push(`<p class="ov-sys" role="status">${esc(this.note)}</p>`);
    const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 40;
    feed.innerHTML = rows.join('') || '<p class="ov-sys">她们说的话会出现在这里。</p>';
    if (atBottom) feed.scrollTop = feed.scrollHeight;
  }

  setSmall(small) {
    this.small = small;
    this.unread = 0;
    this.host.setSmall?.(small);
    this.render();
  }

  close() {
    this.stop();
    if (this.onClose) this.onClose();
    else this.host.close?.();
  }

  onClick(e) {
    const t = e.target;
    if (t.closest('[data-expand]')) return this.setSmall(false);
    if (t.closest('[data-small]')) return this.setSmall(true);
    if (t.closest('[data-close]')) return this.close();
    if (t.closest('[data-unto]')) {
      this.to = null;
      return this.paintTo();
    }
    const msg = t.closest('[data-reply]');
    if (msg) {
      this.to = { name: msg.dataset.reply, id: Number(msg.dataset.id) };
      this.paintTo();
      this.el.querySelector('.ov-row input')?.focus();
    }
  }

  onInput(e) {
    const t = e.target;
    if (t.matches('[data-alpha]')) {
      this.alpha = Number(t.value) / 100;
      this.host.setOpacity?.(this.alpha);
    } else if (t.matches('[data-shade]')) {
      this.shade = Number(t.value) / 100;
      this.el.querySelector('.ov-root')?.style.setProperty('--ov-shade', String(this.shade));
    }
  }

  async onSubmit(e) {
    e.preventDefault();
    const input = this.el.querySelector('.ov-row input');
    let text = input.value.trim();
    if (!text) return;
    // 回复某人：句首加 @名字，服务端照常只让她回（已经 @ 了就不重复加）
    if (this.to && !text.includes(`@${this.to.name}`)) text = `@${this.to.name} ${text}`;
    input.value = '';
    try {
      await call('/api/watch/say', { text });
      this.to = null;
      this.note = '';
      this.paintTo();
    } catch (x) {
      input.value = text;
      this.note = `没发出去：${x.message}`;
    }
    await this.pull();
  }
}
