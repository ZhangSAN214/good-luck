// 聊天：像微信一样和成员聊天。左边是会话列表（生活群、工作群、每位成员的私聊，带未读数）。
// 成员只回应你，不主动找你；群里 @昵称 指定谁回应，@全员 大家都说；私聊里只有对方回应。
// 发出后成员在后台依次回应，这里轮询 /api/chat?room=…&after=… 拿新消息和「正在输入」，并刷新会话列表。
// 表情包、图片、视频、语音：输入框上方的工具条（chatmedia.js）；表情包和语音点完直接发出。
// 键盘：Enter 发送、Shift+Enter 换行（输入法选字时不发送）；输入 @ 弹出成员候选，↑↓ 选择、Enter / Tab 确认、Esc 关闭。
// 微信式动作：右键 / 长按消息 → 引用、复制、撤回（自己的、几分钟内）；双击成员头像 → 拍一拍。
// 成员一次回复可能拆成几条气泡陆续出现；私聊顶部显示「对方正在输入…」。

import { tzOpt } from './hometz.js';
import { MediaComposer, bindMedia, cardHTML, mediaHTML, richText, soloSticker, stickerSVG, visibleText } from './chatmedia.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const snip = (t, n = 40) => {
  const s = String(t || '').replace(/\s+/g, ' ');
  return s.length > n ? `${s.slice(0, n)}…` : s;
};

const FAST_MS = 900; // 有人正在输入或刚发出消息时
const SLOW_MS = 8000;
const FAST_FOR_MS = 25_000; // 发出消息后这么久内保持快速轮询
const EVERYONE = '全员';
const ROOM_KEY = 'roundtable.chat.room';
const SENSES = '感官'; // 「正在输入」里的这一项表示感官正在看 / 听你发的内容

const storage = {
  get() {
    try {
      return localStorage.getItem(ROOM_KEY) || 'main';
    } catch {
      return 'main';
    }
  },
  set(v) {
    try {
      localStorage.setItem(ROOM_KEY, v);
    } catch {
      /* 存不了就算了 */
    }
  },
};

export const composing = (e) => e.isComposing || e.keyCode === 229;

/** 光标前正在输入的 @ 片段（没有返回 null） */
export function atQuery(text, caret) {
  const m = /@([^\s@]{0,12})$/.exec(text.slice(0, caret));
  return m ? { start: caret - m[0].length, q: m[1] } : null;
}

export class GroupChat {
  constructor(root, { api, now = () => Date.now(), openSession = null, openPeople = null, openFeeds = null, openMoments = null } = {}) {
    this.root = root;
    this.api = api; // {get(after, q, room), post(text, room, media, quote), pat(member, room), recall(id, room), clear(room), rooms(), handoff(text), look(id, room)}
    this.openSession = openSession; // 工作群里点「查看」：回到办公室里的这件事
    this.openPeople = openPeople; // 成员设定与记忆
    this.openFeeds = openFeeds; // 上网：订阅源
    this.openMoments = openMoments; // 朋友圈
    this.momentsUnseen = 0;
    this.now = now;
    this.art = null;
    this.room = storage.get();
    this.roomInfo = null; // {id, kind, title}
    this.rooms = [];
    this.budget = null;
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
    this.quote = null; // 正在引用的消息
    this.recallSeconds = 120;
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
      <nav class="gc-rooms" aria-label="会话"><div id="gc-rooms"></div>
        <button type="button" class="btn gc-people" id="gc-people">成员设定与记忆</button>
        <button type="button" class="btn gc-people" id="gc-feeds">上网：看哪些内容</button>
        <button type="button" class="btn gc-people" id="gc-selfcheck" title="花了多少、谁说得多、有没有重复和出错">生活自检</button>
        <p class="hint" id="gc-budget"></p></nav>
      <dialog class="gc-selfcheck" id="gc-selfcheck-dlg" aria-label="生活自检"></dialog>
      <section class="gc-main" aria-label="聊天">
        <header class="gc-head"><b id="gc-title">大家庭</b><span class="gc-count" id="gc-count"></span>
          <form id="gc-search" role="search"><input type="search" id="gc-q" placeholder="搜索聊天记录" aria-label="搜索聊天记录"></form>
          <button type="button" class="btn" id="gc-clear" title="删除这个会话的全部聊天记录（花费记录保留）">清空记录</button></header>
        <div class="gc-feed" id="gc-feed" aria-live="polite"></div>
        <div class="gc-menu" id="gc-menu" role="menu" hidden></div>
        <div class="gc-typing" id="gc-typing" hidden></div>
        <form class="gc-compose" id="gc-compose" autocomplete="off">
          <div class="gc-suggest" id="gc-suggest" role="listbox" hidden></div>
          <div class="gc-quotebar" id="gc-quotebar" hidden></div>
          <div id="gc-tools"></div>
          <div id="gc-tray" hidden></div>
          <textarea id="chat-input" rows="2" maxlength="2000" placeholder="说点什么：Enter 发送，Shift+Enter 换行；输入 @ 点名" aria-label="聊天消息"></textarea>
          <button class="btn" type="button" id="gc-handoff" hidden title="把输入框里的事按办公室模式交办（经理分诊派活），进度会发到工作群">交给办公室</button>
          <button class="btn primary" type="submit" id="gc-send">发送</button>
        </form>
        <p class="hint gc-readonly" id="gc-readonly" hidden>这是成员之间的私聊，你只能看，不能发言。成员不会因为你看了而来找你。</p>
        <p class="hint gc-err" id="gc-err" role="alert" hidden></p>
      </section>
      <aside class="gc-members" id="gc-aside" aria-label="群成员"><h3>成员 <span id="gc-online"></span></h3><ul id="gc-members"></ul>
        <p class="hint">成员都是 AI，只在你发言后回应。点名字可以 @ 对方；写「大家」或「所有人」时每位在线成员都会回。双击聊天里的头像拍一拍，右键（长按）消息可以引用、撤回。</p></aside>`;
    this.$('#gc-rooms').addEventListener('click', (e) => {
      if (e.target.closest('#gc-moments')) return this.openMoments?.(null);
      const b = e.target.closest('[data-room]');
      if (b) this.openRoom(b.dataset.room);
    });
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
    const feed = this.$('#gc-feed');
    feed.addEventListener('contextmenu', (e) => {
      const m = e.target.closest('.gc-msg[data-id]');
      if (!m || this.search) return;
      e.preventDefault();
      this.openMenu(Number(m.dataset.id), e.clientX, e.clientY);
    });
    let press = null; // 长按（触屏）
    feed.addEventListener('touchstart', (e) => {
      const m = e.target.closest('.gc-msg[data-id]');
      if (!m || this.search) return;
      const t = e.touches[0];
      press = setTimeout(() => this.openMenu(Number(m.dataset.id), t.clientX, t.clientY), 500);
    }, { passive: true });
    for (const ev of ['touchend', 'touchmove', 'touchcancel']) feed.addEventListener(ev, () => clearTimeout(press), { passive: true });
    feed.addEventListener('dblclick', (e) => {
      const av = e.target.closest('.gc-msg[data-author] .gc-av');
      if (av) this.pat(av.closest('.gc-msg').dataset.author);
    });
    this.$('#gc-menu').addEventListener('click', (e) => {
      const b = e.target.closest('[data-act]');
      if (b) this.menuAction(b.dataset.act, Number(b.dataset.id));
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('#gc-menu') && this.now() - (this.menuAt || 0) > 400) this.closeMenu();
    });
    this.$('#gc-quotebar').addEventListener('click', (e) => {
      if (e.target.closest('[data-unquote]')) this.setQuote(null);
    });
    feed.addEventListener('click', (e) => {
      const q = e.target.closest('[data-goto]');
      if (q) return this.flash(Number(q.dataset.goto));
      if (e.target.closest('#gc-exit-search')) this.runSearch('');
      const open = e.target.closest('[data-session]');
      if (open && this.openSession) this.openSession(open.dataset.session);
      const look = e.target.closest('[data-look]');
      if (look) this.lookCloser(Number(look.dataset.look), look);
    });
    this.$('#gc-handoff').addEventListener('click', () => this.handoff());
    this.$('#gc-people').hidden = !this.openPeople;
    this.$('#gc-feeds').hidden = !this.openFeeds;
    this.$('#gc-feeds').addEventListener('click', () => this.openFeeds?.());
    this.$('#gc-selfcheck').hidden = !this.api.lifeReports;
    this.$('#gc-selfcheck').addEventListener('click', () => this.openSelfCheck());
    this.$('#gc-selfcheck-dlg').addEventListener('click', (e) => {
      if (e.target.closest('[data-close]')) this.$('#gc-selfcheck-dlg').close();
    });
    this.$('#gc-people').addEventListener('click', () => {
      const dm = this.isDm() ? this.room.slice(3) : null; // 私聊里打开时直接看对方
      this.openPeople?.(dm);
    });
    this.$('#gc-clear').addEventListener('click', () => this.clear());
    this.composer = new MediaComposer({
      input,
      bar: this.$('#gc-tools'),
      tray: this.$('#gc-tray'),
      onSticker: (tok) => this.send({ text: tok, media: [] }),
      onVoice: (item) => this.send({ text: '', media: [item] }),
      onChange: () => this.syncSend(),
      onError: (msg) => {
        this.error = msg;
        this.renderError();
      },
    });
    bindMedia(this.$('#gc-feed'));
    this.renderAll();
  }

  // --- 数据 ------------------------------------------------------------------

  schedule() {
    clearTimeout(this.timer);
    if (this.root.hidden) return;
    const fast = this.typing.length || this.watchSeen || this.now() < this.fastUntil;
    this.timer = setTimeout(() => this.poll(), fast ? FAST_MS : SLOW_MS);
  }

  async poll() {
    try {
      const room = this.room;
      const [data, rooms] = await Promise.all([this.api.get(this.lastId, '', room), this.api.rooms ? this.api.rooms() : null]);
      if (room !== this.room) throw new Error(''); // 拉取期间换了会话：丢掉这次结果
      if (rooms) {
        this.rooms = rooms.rooms || [];
        this.everyone = rooms.members || [];
        this.budget = rooms.budget || null;
        this.life = rooms.life || null;
        this.momentsUnseen = rooms.moments_unseen || 0;
        this.renderRooms();
      }
      if (data.room && (!this.roomInfo || this.roomInfo.id !== data.room.id)) {
        this.roomInfo = data.room;
        this.renderHead();
      }
      this.error = '';
      const roster = JSON.stringify(data.members || []);
      const newMembers = roster !== this.roster;
      this.roster = roster;
      this.members = data.members || [];
      if (data.recall_seconds != null) this.recallSeconds = data.recall_seconds;
      if ((data.messages || []).length) {
        this.messages.push(...data.messages);
        for (const m of data.messages) {
          const i = (this.pending || []).findIndex((n) => n === m.name || (m.author === 'system' && m.text.startsWith(n)));
          if (i >= 0) this.pending.splice(i, 1);
          else if (m.author === 'system' && m.text.includes('预算用完')) this.pending = [];
        }
        this.lastId = this.messages[this.messages.length - 1].id;
      }
      if (Array.isArray(data.queued)) {
        // 服务器说还有谁没开口（不接话的成员也会从这里消失）
        const queued = new Set(data.queued);
        this.pending = (this.pending || []).filter((n) => queued.has(n) || this.typing.includes(n) || (data.typing || []).includes(n));
      }
      if ((this.pending || []).length) this.fastUntil = Math.max(this.fastUntil, this.now() + FAST_MS * 3);
      let changed = newMembers || (data.messages || []).length || String(data.typing) !== String(this.typing);
      this.typing = data.typing || [];
      // 感官看完（发图后或点了「细看」）：那条消息的「已看过」标记变了，整页重拉，直到标记出现（最多等 60 秒）
      const want = this.watchSeen;
      if (want && !this.typing.includes(SENSES)) {
        const all = await this.api.get(0, '', room);
        if (room === this.room && (all.messages || []).length) {
          this.messages = all.messages;
          this.lastId = this.messages[this.messages.length - 1].id;
          changed = true;
          const msg = this.messages.find((m) => m.id === want.id);
          const cards = msg?.meta?.perception || [];
          const done = cards.length && (want.mode !== 'fine' || cards.every((c) => c.mode === 'fine'));
          if (done || this.now() > want.until) this.watchSeen = null;
        }
      }
      if (changed) this.renderAll();
    } catch (e) {
      if (e.message) {
        this.error = e.message;
        this.renderError();
      }
    }
    this.schedule();
  }

  /** 换到另一个会话（群或私聊）：清掉当前消息，从头拉取。 */
  async openRoom(id) {
    if (!id || id === this.room) return;
    this.room = id;
    storage.set(id);
    this.roomInfo = this.rooms.find((r) => r.id === id) || { id, kind: this.kind(id), title: '' };
    this.messages = [];
    this.lastId = 0;
    this.search = null;
    this.pending = [];
    this.typing = [];
    this.watchSeen = null;
    this.$('#gc-q').value = '';
    this.closeSuggest();
    this.quote = null;
    this.renderQuote();
    this.renderAll();
    clearTimeout(this.timer);
    await this.poll();
    this.$('#chat-input').focus({ preventScroll: true });
  }

  /** 附件还在上传时不能发。 */
  syncSend() {
    const b = this.$('#gc-send');
    if (!b) return;
    const busy = this.composer?.busy();
    b.disabled = this.sending || busy;
    b.textContent = busy ? '上传中…' : '发送';
  }

  /** direct：直接发出的表情包 / 语音（不动输入框）；省略时发输入框里的文字和待发附件。 */
  async send(direct = null) {
    const input = this.$('#chat-input');
    const text = direct ? direct.text : input.value.trim();
    const media = direct ? direct.media : this.composer?.ready() || [];
    if ((!text && !media.length) || this.sending) return;
    if (!direct && this.composer?.busy()) {
      this.error = '附件还在上传，稍等一下';
      return this.renderError();
    }
    this.sending = true;
    this.$('#gc-send').disabled = true;
    try {
      const quote = this.quote?.id ?? null;
      const res = await this.api.post(text, this.room, media, quote);
      this.setQuote(null);
      if (res && res.look) this.watchSeen = { id: res.id, mode: res.look, until: this.now() + 60_000 };
      // 排队等着回应的人：收到 TA 的消息（或系统说明 TA 为什么没回）后划掉
      this.pending = [...(this.pending || []), ...((res && res.speakers) || [])];
      if (!direct) {
        input.value = '';
        this.composer?.clear();
      }
      this.closeSuggest();
      this.fastUntil = this.now() + FAST_FOR_MS;
      this.error = '';
    } catch (e) {
      this.error = e.message || '发送失败';
    }
    this.sending = false;
    this.syncSend();
    this.renderError();
    await this.poll();
  }

  /** 工作群：把输入框里的事交给办公室。 */
  async handoff() {
    const input = this.$('#chat-input');
    const text = input.value.trim();
    if (!text || this.sending || !this.api.handoff) return;
    this.sending = true;
    this.$('#gc-handoff').disabled = true;
    try {
      await this.api.handoff(text);
      input.value = '';
      this.fastUntil = this.now() + FAST_FOR_MS;
      this.error = '';
    } catch (e) {
      this.error = e.message || '交办失败';
    }
    this.sending = false;
    this.$('#gc-handoff').disabled = false;
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
      const data = await this.api.get(0, q, this.room);
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
      await this.api.clear(this.room);
      this.messages = [];
      this.search = null;
      this.renderFeed();
    } catch (e) {
      this.error = e.message || '清空失败';
      this.renderError();
    }
  }

  // --- 微信式动作：引用、复制、撤回、拍一拍 ----------------------------------------

  find(id) {
    return this.messages.find((m) => m.id === id) || null;
  }

  canRecall(msg) {
    if (!msg || msg.author !== 'user' || msg.meta?.recalled || !this.api.recall) return false;
    return this.now() - new Date(msg.created_at).getTime() <= this.recallSeconds * 1000;
  }

  openMenu(id, x, y) {
    const msg = this.find(id);
    if (!msg || msg.author === 'system' || msg.meta?.recalled) return this.closeMenu();
    const items = [];
    if (this.kind() !== 'pair') items.push(['quote', '引用']);
    items.push(['copy', '复制']);
    if (this.api.forward && this.forwardable(msg)) items.push(['forward', '转发']);
    if (this.canRecall(msg)) items.push(['recall', '撤回']);
    const menu = this.$('#gc-menu');
    menu.innerHTML = items.map(([act, label]) => `<button type="button" role="menuitem" data-act="${act}" data-id="${id}">${label}</button>`).join('');
    menu.hidden = false;
    this.menuAt = this.now();
    const box = this.$('.gc-main').getBoundingClientRect();
    const w = menu.offsetWidth || 120;
    const h = menu.offsetHeight || 40;
    menu.style.left = `${Math.max(4, Math.min(x - box.left, box.width - w - 4))}px`;
    menu.style.top = `${Math.max(4, Math.min(y - box.top, box.height - h - 4))}px`;
    menu.querySelector('button')?.focus({ preventScroll: true });
  }

  closeMenu() {
    const menu = this.$('#gc-menu');
    if (menu) menu.hidden = true;
  }

  async menuAction(act, id) {
    this.closeMenu();
    const msg = this.find(id);
    if (!msg) return;
    if (act === 'quote') {
      this.setQuote(msg);
      this.$('#chat-input').focus();
    } else if (act === 'copy') {
      try {
        await navigator.clipboard.writeText(visibleText(msg.text, msg.meta));
      } catch {
        /* 复制不了就算了 */
      }
    } else if (act === 'recall') {
      await this.recall(msg);
    } else if (act === 'forward') {
      this.forwardMenu(msg);
    } else if (act.startsWith('to:')) {
      await this.forward(msg, act.slice(3));
    }
  }

  /** 能转发的：图片 / 视频 / 语音 / 表情包、链接卡片，或有文字的消息。 */
  forwardable(msg) {
    return Boolean(msg.meta?.media?.length || msg.meta?.card || msg.meta?.share || visibleText(msg.text, msg.meta));
  }

  /** 第二步：选转发到哪个群或私聊（成员之间的聊天只能看，不能转进去）。 */
  forwardMenu(msg) {
    const targets = this.rooms.filter((r) => r.kind === 'group' || r.kind === 'dm');
    const menu = this.$('#gc-menu');
    const head = '<small class="gc-menu-h">转发给</small>';
    menu.innerHTML = head + targets.map((r) => `<button type="button" role="menuitem" data-act="to:${esc(r.id)}" data-id="${msg.id}">${esc(r.title)}</button>`).join('');
    menu.hidden = false;
    this.menuAt = this.now();
    menu.querySelector('button')?.focus({ preventScroll: true });
  }

  async forward(msg, room) {
    try {
      const res = await this.api.forward(msg.id, room);
      this.error = '';
      if (room === this.room) {
        this.pending = [...(this.pending || []), ...((res && res.speakers) || [])];
        this.fastUntil = this.now() + FAST_FOR_MS;
        this.renderError();
      } else {
        this.notice(`已转发到「${this.rooms.find((r) => r.id === room)?.title || '聊天'}」`);
      }
      this.poll();
    } catch (e) {
      this.error = e.message || '转发失败';
      this.renderError();
    }
  }

  setQuote(msg) {
    this.quote = msg ? { id: msg.id, author: msg.author, text: visibleText(msg.text, msg.meta) || msg.text } : null;
    this.renderQuote();
  }

  renderQuote() {
    const bar = this.$('#gc-quotebar');
    if (!bar) return;
    const q = this.quote;
    bar.hidden = !q;
    bar.innerHTML = q ? `<span>引用 ${esc(this.nameOf(q.author))}：${richText(snip(q.text))}</span><button type="button" class="link" data-unquote aria-label="取消引用">×</button>` : '';
  }

  nameOf(author) {
    if (author === 'user') return '我';
    const m = [...(this.members || []), ...(this.everyone || [])].find((x) => x.id === author);
    return m?.name || author;
  }

  /** 撤回自己的消息：成功后就地换成「你撤回了一条消息」。 */
  async recall(msg) {
    try {
      await this.api.recall(msg.id, this.room);
      msg.text = '';
      msg.meta = { ...(msg.meta || {}), recalled: true, media: undefined, caption: undefined, quote: undefined };
      if (this.quote?.id === msg.id) this.setQuote(null);
      this.error = '';
      this.renderFeed();
    } catch (e) {
      this.error = e.message || '撤回失败';
    }
    this.renderError();
  }

  /** 拍一拍：双击成员头像。 */
  async pat(member) {
    if (!member || !this.api.pat || this.kind() === 'pair') return;
    try {
      const res = await this.api.pat(member, this.room);
      this.pending = [...(this.pending || []), ...((res && res.speakers) || [])];
      this.fastUntil = this.now() + FAST_FOR_MS;
      this.error = '';
    } catch (e) {
      this.error = e.message || '拍一拍失败';
    }
    this.renderError();
    await this.poll();
  }

  /** 点引用框：滚到原消息并闪一下。 */
  flash(id) {
    const el = this.$(`#gc-feed .gc-msg[data-id="${id}"]`);
    if (!el) return;
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    el.classList.remove('flash');
    void el.offsetWidth;
    el.classList.add('flash');
  }

  // --- @ 候选 ----------------------------------------------------------------

  names() {
    if (this.isDm()) return [];
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

  /** 在生活群里 @ 某人（公寓楼里点住户「找 TA 聊天」）。 */
  async mentionInGroup(name) {
    if (this.room !== 'main') await this.openRoom('main');
    this.insertMention(name);
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

  /** prev：上一条消息。同一个人三分钟内连续发言时不再重复头像和名字。 */
  bubble(msg, prev) {
    const sid = msg.meta?.session_id;
    const look = sid && this.openSession ? ` <button type="button" class="link" data-session="${esc(sid)}">查看</button>` : '';
    if (msg.author === 'system') return `<div class="gc-sys${sid ? ' work' : ''}${msg.meta?.pat ? ' pat' : ''}" data-id="${msg.id}"><span>${esc(msg.text)}</span>${look}</div>`;
    if (msg.meta?.recalled) return `<div class="gc-sys recalled" data-id="${msg.id}"><span>${msg.author === 'user' ? '你' : esc(msg.name)}撤回了一条消息</span></div>`;
    const at = new Date(msg.created_at);
    const time = at.toLocaleTimeString('zh-CN', tzOpt({ hour: '2-digit', minute: '2-digit', hour12: false }));
    const shown = visibleText(msg.text, msg.meta);
    const solo = !msg.meta?.media?.length && !msg.meta?.card && soloSticker(msg.text);
    const media = mediaHTML(msg.meta?.media, { group: `m${msg.id}`, mine: msg.author === 'user' });
    const words = shown ? `<div class="gc-words">${richText(shown, { mentions: true })}</div>` : '';
    const card = cardHTML(msg.meta?.card);
    const fwd = msg.meta?.forwarded ? '<small class="gc-fwd">转发</small>' : '';
    const text = solo ? stickerSVG(solo.pack, solo.name, { big: true }) : `${fwd}${words}${media}${card}`;
    const stkOnly = msg.meta?.media?.length && msg.meta.media.every((m) => m.sticker) && !shown;
    const kindCls = solo || stkOnly ? ' stk-only' : msg.meta?.media?.length ? (shown ? ' has-media' : ' media-only') : card && !shown ? ' card-only' : '';
    const follow = prev && prev.author === msg.author && at - new Date(prev.created_at) < 180000;
    const cls = `gc-msg${msg.author === 'user' ? ' me' : ''}${follow ? ' follow' : ''}`;
    const stamp = `<time datetime="${esc(msg.created_at)}">${esc(time)}</time>`;
    const tag = msg.meta?.event === 'handoff' ? `<span class="gc-tag">已交给办公室${look}</span>` : '';
    const seen = this.seenTag(msg);
    const quote = this.quoteHTML(msg.meta?.quote);
    if (msg.author === 'user') return `<div class="${cls}" data-id="${msg.id}"><div class="gc-body"><div class="gc-meta">${tag}${seen}${stamp}</div><div class="gc-bub${kindCls}">${text}</div>${quote}</div></div>`;
    const member = this.members.find((m) => m.id === msg.author);
    const away = msg.meta?.offline ? '<span class="gc-tag away" title="你不在的时候发生的">你不在时</span>' : '';
    return `<div class="${cls}" data-id="${msg.id}" data-author="${esc(msg.author)}">${this.avatar(member, msg.name)}<div class="gc-body"><div class="gc-meta"><span class="gc-name">${esc(msg.name)}</span>${away}${stamp}</div><div class="gc-bub${kindCls}">${text}${card ? '' : this.source(msg.meta?.share)}</div>${quote}</div></div>`;
  }

  /** 微信式引用框：放在气泡下面，灰底一行「名字：开头一段」；原消息已撤回时说明。点击跳到原消息。 */
  quoteHTML(q) {
    if (!q) return '';
    const orig = this.find(q.id);
    const body = orig?.meta?.recalled ? '引用的内容已撤回' : `${esc(this.nameOf(q.author))}：${richText(snip(q.text))}`;
    return `<button type="button" class="gc-quote" data-goto="${Number(q.id)}" title="查看原消息">${body}</button>`;
  }

  /** 你发的图片 / 视频 / 语音：感官看过后标「已看过 · 快速 / 精细」，并给一个「细看」按钮（精细模式重看一次）。 */
  seenTag(msg) {
    if (msg.author !== 'user' || !msg.meta?.media?.length || !this.api.look) return '';
    const cards = msg.meta.perception || [];
    const fine = cards.length && cards.every((c) => c.mode === 'fine');
    const state = cards.length ? `<span class="gc-tag seen" title="成员读的是感官整理出的内容">已看过 · ${fine ? '精细' : '快速'}</span>` : '';
    const btn = fine ? '' : `<button type="button" class="link gc-look" data-look="${msg.id}" title="用精细模式重新看一遍，再请一位成员回应">细看</button>`;
    return `${state}${btn}`;
  }

  async lookCloser(id, btn) {
    if (!id || btn.disabled) return;
    btn.disabled = true;
    btn.textContent = '细看中…';
    try {
      const res = await this.api.look(id, this.room);
      this.pending = [...(this.pending || []), ...((res && res.speakers) || [])];
      this.fastUntil = this.now() + FAST_FOR_MS;
      this.watchSeen = { id, mode: 'fine', until: this.now() + 60_000 };
      this.error = '';
    } catch (e) {
      this.error = e.message || '细看失败';
      btn.disabled = false;
      btn.textContent = '细看';
    }
    this.renderError();
    await this.poll();
  }

  /** 上网分享的来源：标题链接、来源名、发布时间、未核实。只接受 http(s) 网址。 */
  source(share) {
    if (!share || !/^https?:\/\//i.test(share.url || '')) return '';
    const when = share.published ? new Date(share.published).toLocaleString('zh-CN', tzOpt({ month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })) : '';
    const flag = share.unverified ? '<span class="gc-tag away">未核实</span>' : '';
    return `<a class="gc-src" href="${esc(share.url)}" target="_blank" rel="noopener noreferrer nofollow">${esc(share.title)}</a><small class="gc-srcm">${esc(share.feed)}${when ? ` · ${esc(when)}` : ''}${flag}</small>`;
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
      ? this.messages.map((m, i) => this.bubble(m, this.messages[i - 1])).join('')
      : this.kind() === 'pair'
        ? '<div class="gc-empty"><b>还没有消息</b><p>成员私下聊过的话会出现在这里。</p></div>'
        : this.isDm()
        ? '<div class="gc-empty"><b>还没有消息</b><p>打个招呼吧。对方是 AI，被问到时会直说；只在你发消息后回复。</p></div>'
        : '<div class="gc-empty"><b>还没有消息</b><p>打个招呼，或者写「大家自我介绍一下」。成员都是 AI，被问到时会直说。</p></div>';
    if (near || this.stick !== false) feed.scrollTop = feed.scrollHeight;
    this.stick = near;
  }

  kind(id = null) {
    if (!id && this.roomInfo?.kind) return this.roomInfo.kind;
    const room = id || this.room;
    return room.startsWith('dm:') ? 'dm' : room.startsWith('pair:') ? 'pair' : 'group';
  }

  isDm() {
    return this.kind() === 'dm';
  }

  roomTitle(r) {
    if (!r) return '';
    return r.kind === 'dm' ? `与 ${r.title} 私聊` : r.title;
  }

  renderHead() {
    const r = this.roomInfo;
    this.$('#gc-title').textContent = this.roomTitle(r) || '聊天';
    const dm = this.isDm();
    this.root.classList.toggle('dm', dm);
    const pair = r?.kind === 'pair';
    this.$('#gc-aside').hidden = dm || pair;
    this.$('#gc-compose').hidden = pair;
    this.$('#gc-readonly').hidden = !pair;
    this.$('#gc-handoff').hidden = !(r?.kind === 'work' && this.api.handoff);
    this.$('#chat-input').placeholder = dm ? '说点什么：Enter 发送，Shift+Enter 换行' : '说点什么：Enter 发送，Shift+Enter 换行；输入 @ 点名';
  }

  renderRooms() {
    const el = this.$('#gc-rooms');
    if (!el) return;
    const item = (r) => {
      const member = r.kind === 'dm' ? (this.everyone || []).find((m) => m.id === r.members?.[0]) || { model_id: null } : null;
      const icon = r.kind === 'dm' ? this.avatar(member, r.title) : `<span class="gc-av ini grp">${{ work: '工', pair: '聊' }[r.kind] || '群'}</span>`;
      const lastText = (r.last?.text || '').replace(/\[表情:[^\]·]{1,6}·([^\]]{1,6})\]/g, '[$1]');
      const last = r.last ? `${r.kind === 'dm' ? '' : `${esc(r.last.name)}：`}${esc(lastText)}` : r.kind === 'dm' ? '还没聊过' : '';
      const badge = r.unread ? `<i class="gc-unread" aria-label="${r.unread} 条未读">${r.unread > 99 ? '99+' : r.unread}</i>` : '';
      const cur = r.id === this.room;
      return `<button type="button" class="gc-room${cur ? ' cur' : ''}" data-room="${esc(r.id)}"${cur ? ' aria-current="true"' : ''}>${icon}<span class="gc-rt"><b>${esc(r.title)}</b><small>${last}</small></span>${badge}</button>`;
    };
    const groups = this.rooms.filter((r) => r.kind === 'group' || r.kind === 'work');
    const dms = this.rooms.filter((r) => r.kind === 'dm');
    const pairs = this.rooms.filter((r) => r.kind === 'pair');
    const unseen = this.momentsUnseen ? `<i class="gc-unread" aria-label="${this.momentsUnseen} 条新动态">${this.momentsUnseen > 99 ? '99+' : this.momentsUnseen}</i>` : '';
    const mo = this.openMoments ? `<button type="button" class="gc-room" id="gc-moments"><span class="gc-av ini grp">圈</span><span class="gc-rt"><b>朋友圈</b><small>成员的动态</small></span>${unseen}</button>` : '';
    el.innerHTML = `${mo}<h3>群聊</h3>${groups.map(item).join('')}<h3>私聊</h3>${dms.map(item).join('')}${pairs.length ? `<h3 title="成员私下的聊天，你只能看">成员之间</h3>${pairs.map(item).join('')}` : ''}`;
    const b = this.budget;
    const l = this.life;
    const halted = l?.halted ? `（今天已停：${l.halted}）` : '';
    const life = l?.enabled ? `；今天生活 $${l.spent_today_usd.toFixed(3)} / $${l.daily_usd.toFixed(2)}，互聊 ${l.messages_today ?? 0}/${l.daily_messages ?? '-'} 句${halted}` : '';
    this.$('#gc-budget').textContent = b ? `本月聊天 $${b.spent_usd.toFixed(2)} / $${b.limit_usd.toFixed(2)}${life}` : '';
  }

  async openSelfCheck() {
    const dlg = this.$('#gc-selfcheck-dlg');
    dlg.innerHTML = '<p class="hint">正在统计…</p>';
    if (!dlg.open) dlg.showModal();
    try {
      dlg.innerHTML = selfCheckHtml(await this.api.lifeReports());
    } catch (e) {
      dlg.innerHTML = `<p class="hint">没取到：${esc(e.message || String(e))}</p><button type="button" class="btn" data-close>关闭</button>`;
    }
  }

  markMomentsSeen() {
    this.momentsUnseen = 0;
    this.renderRooms();
  }

  renderMembers() {
    const online = this.members.filter((m) => m.available).length;
    this.$('#gc-count').textContent = `${this.members.length + 1} 人`;
    this.$('#gc-online').textContent = `${online}/${this.members.length} 在线`;
    this.$('#gc-members').innerHTML = this.members
      .map((m) => `<li data-member="${esc(m.id)}"><button type="button" class="link${m.available ? '' : ' off'}" data-name="${esc(m.name)}" title="${m.available ? `@${esc(m.name)}` : '离线：没有可用渠道（检查 .env 里的 key）'}"><span class="gc-avwrap">${this.avatar(m, m.name)}<i class="dot${m.available ? ' on' : ''}" aria-label="${m.available ? '在线' : '离线'}"></i></span><span class="nm">${esc(m.name)}</span></button></li>`)
      .join('');
  }

  renderTyping() {
    const title = this.$('#gc-title');
    if (title) title.textContent = this.isDm() && this.typing.length ? '对方正在输入…' : this.roomTitle(this.roomInfo) || '聊天';
    const t = this.$('#gc-typing');
    const waiting = (this.pending || []).filter((n) => !this.typing.includes(n));
    const parts = [];
    const looking = this.typing.includes(SENSES);
    const typing = this.typing.filter((n) => n !== SENSES);
    if (looking) parts.push('正在看你发的图片 / 视频 / 语音…');
    if (typing.length) parts.push(`${typing.join('、')} 正在输入…`);
    if (waiting.length) parts.push(`接下来：${waiting.join('、')}`);
    t.hidden = !parts.length;
    t.innerHTML = `${this.typing.length ? '<i></i><i></i><i></i>' : ''}<span>${esc(parts.join('　'))}</span>`;
  }

  /** 一句提示（不是错误），几秒后消失。 */
  notice(text) {
    const el = this.$('#gc-err');
    if (!el) return;
    el.hidden = false;
    el.textContent = text;
    el.classList.add('ok');
    clearTimeout(this.noticeTimer);
    this.noticeTimer = setTimeout(() => {
      el.classList.remove('ok');
      this.renderError();
    }, 2500);
  }

  renderError() {
    const el = this.$('#gc-err');
    if (!el) return;
    if (el.classList.contains('ok') && !this.error) return;
    el.classList.remove('ok');
    el.hidden = !this.error;
    el.textContent = this.error;
  }

  renderAll() {
    this.renderHead();
    this.renderRooms();
    this.renderMembers();
    this.renderFeed();
    this.renderTyping();
    this.renderError();
  }
}

// 生活自检（docs/CHAT.md G9）：最近 7 天的实时报告在前，下面是每周存下的报告
function reportHtml(r, title) {
  const usd = (v) => `$${Number(v || 0).toFixed(3)}`;
  const talk = (r.speakers || [])
    .map((s) => `<li>${esc(s.name)}：互聊 ${s.life} 句、回应你 ${s.replies} 句（占 ${Math.round((s.share || 0) * 100)}%）</li>`)
    .join('');
  const reps = (r.repeats?.examples || []).map((x) => `<li>${esc(x.name)}：${esc(x.text)}</li>`).join('');
  const halts = (r.guard?.halts || []).map((h) => `<li>${esc(h.at.slice(0, 10))}：${esc(h.reason)}</li>`).join('');
  return `<section><h3>${esc(title)}</h3>
    <p><b>${(r.notes || []).map(esc).join('；')}</b></p>
    <p>花费：生活 ${usd(r.cost?.life_usd)}（平均每天 ${usd(r.cost?.life_per_day_usd)}，上限 $${Number(r.cost?.life_daily_limit_usd || 0).toFixed(2)}），聊天合计 ${usd(r.cost?.total_usd)}</p>
    <p>出错：${r.errors?.failed || 0} / ${r.errors?.calls || 0} 次调用；没新内容收尾 ${r.guard?.stale || 0} 次；到每日条数上限 ${r.guard?.cap_days || 0} 天</p>
    ${talk ? `<p>谁说得多：</p><ul>${talk}</ul>` : '<p>这段时间没人说话。</p>'}
    ${r.repeats?.count ? `<p>重复：${r.repeats.count} 句和之前很像，例如：</p><ul>${reps}</ul>` : ''}
    ${halts ? `<p>熔断：</p><ul>${halts}</ul>` : ''}</section>`;
}

export function selfCheckHtml(data) {
  const now = data.halted ? `<p class="hint">今天的生活已停：${esc(data.halted)}（明天自动恢复）</p>` : '';
  const weeks = (data.reports || []).map((r) => reportHtml(r, `${r.week} 周报`)).join('');
  return `<h2>生活自检</h2>${now}${reportHtml(data.recent || {}, '最近 7 天')}${weeks || '<p class="hint">每周一会存一份上一周的报告。</p>'}
    <form method="dialog"><button type="button" class="btn" data-close>关闭</button></form>`;
}
