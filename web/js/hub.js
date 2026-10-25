// 终端首页：今日助理（每天按日期轮换一位成员，人人机会相同）+ 拼贴入口。
// 只读：数字来自 /api/sessions、/api/chat/rooms 与 /api/status，不调用模型、不花钱。
// 成员名字和立绘都来自 /api/members，代码里不写任何成员或模型的名字。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const WEEK = '日一二三四五六';
const REFRESH_MS = 60_000;

/** 今天轮到第几位：日期字符串的简单散列对人数取模，同一天结果固定，和成员是谁无关。 */
export function dutyIndex(date, count) {
  if (!count) return -1;
  const key = `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
  let h = 0;
  for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h % count;
}

/** 助理的一句话：按今天的数字拼出来（本地模板，不是模型生成的）。 */
export function dutyLine({ waiting = 0, running = 0, unread = 0 }) {
  const parts = [];
  if (waiting) parts.push(`办公室有 ${waiting} 件事等你拍板`);
  else if (running) parts.push(`办公室有 ${running} 件事正在办`);
  if (unread) parts.push(`群聊里有 ${unread} 条未读`);
  return parts.length ? `欢迎回来。${parts.join('，')}。` : '欢迎回来。今天没有要你处理的事，随便逛逛吧。';
}

const ICON = {
  daily: '<path d="M10 14h44v40H10zM10 24h44M22 8v10M42 8v10"/>',
  chat: '<path d="M8 12h36v26H24l-10 8v-8H8zM44 22h12v22h-6v8l-8-8H28"/>',
  home: '<path d="M12 56V20l20-12 20 12v36M24 56V40h16v16M20 26h6M38 26h6"/>',
};
const icon = (k) => `<svg class="hb-ico" viewBox="0 0 64 64" aria-hidden="true">${ICON[k]}</svg>`;

export class Hub {
  /**
   * @param {HTMLElement} root
   * @param {{sessions:Function, rooms:Function, go:Function, openPeople:Function, openHistory:Function, status:Function, now?:Function}} opts
   */
  constructor(root, { sessions, rooms, go, openPeople, openHistory, status, now = () => new Date() }) {
    Object.assign(this, { root, sessions, rooms, go, openPeople, openHistory, status, now });
    this.art = null;
    this.counts = { waiting: 0, running: 0, unread: 0 };
    this.timer = null;
    root.addEventListener('click', (e) => this.onClick(e));
    root.addEventListener('change', (e) => {
      if (!e.target.matches('[data-land]')) return;
      try {
        localStorage.setItem('roundtable.landing', e.target.checked ? 'work' : 'hub');
      } catch {
        /* 存不了就算了 */
      }
    });
  }

  landWork() {
    try {
      return localStorage.getItem('roundtable.landing') === 'work';
    } catch {
      return false;
    }
  }

  setArt(art) {
    this.art = art && art.available ? art : null;
    if (!this.root.hidden) this.render();
  }

  async show() {
    this.root.hidden = false;
    this.render();
    await this.load();
    clearInterval(this.timer);
    this.timer = setInterval(() => this.load(), REFRESH_MS);
  }

  hide() {
    this.root.hidden = true;
    clearInterval(this.timer);
    this.timer = null;
  }

  async load() {
    const [list, chat] = await Promise.all([this.sessions().catch(() => null), this.rooms().catch(() => null)]);
    const items = list?.sessions || list || [];
    this.counts = {
      waiting: items.filter((s) => s.status === 'awaiting_confirmation').length,
      running: items.filter((s) => s.status === 'running').length,
      unread: (chat?.rooms || []).reduce((n, r) => n + (r.unread || 0), 0),
    };
    if (!this.root.hidden) this.render();
  }

  duty() {
    const members = (this.art?.members || []).filter((m) => m.images?.chibi);
    const i = dutyIndex(this.now(), members.length);
    return i < 0 ? null : members[i];
  }

  render() {
    const t = this.now();
    const night = t.getHours() < 6 || t.getHours() >= 18;
    const m = this.duty();
    const c = this.counts;
    const st = this.status?.() || null;
    const month = st?.budget?.month;
    const pad = (n) => String(n).padStart(2, '0');
    const big = c.waiting || c.running;
    const aide = m
      ? `<div class="hb-aide"><img src="${esc(m.images.chibi)}" alt="今日助理${esc(m.display)}的立绘" draggable="false"></div>
         <div class="hb-who"><b>${esc(m.display)}</b><span>今日轮值助理</span></div>`
      : '';
    this.root.innerHTML = `
      <div class="hb-scene${night ? ' night' : ''}" aria-hidden="true"></div>
      <div class="hb-ghost" aria-hidden="true">RT</div>
      <div class="hb-top">
        <span class="hb-date">${t.getFullYear()}/${pad(t.getMonth() + 1)}/${pad(t.getDate())} 周${WEEK[t.getDay()]} · ${pad(t.getHours())}:${pad(t.getMinutes())}</span>
        <label class="hb-land"><input type="checkbox" data-land${this.landWork() ? ' checked' : ''}> 打开网页时直接进办公室</label>
        ${month ? `<span class="hb-res" title="本月已用 / 本月上限"><i></i>$${Number(month.spent_usd).toFixed(2)}<small>/ $${Number(month.limit_usd).toFixed(0)}</small></span>` : ''}
      </div>
      ${aide}
      <div class="hb-voice"><span class="hb-voice-tag">今日助理</span>${esc(dutyLine(c))}</div>
      <nav class="hb-grid" aria-label="入口">
        <button type="button" class="hb-t hb-hero" data-go="work">
          <span class="hb-num"><b>${pad(big)}</b><span>${c.waiting ? '等你拍板' : '正在办'} · 进行中 <em>${c.running}</em></span></span>
          <span class="hb-body"><span><span class="hb-zh">工作</span><span class="hb-en">OFFICE</span></span>
            <span class="hb-now">${c.waiting ? '<i>待确认</i>点进去处理' : c.running ? '<i>进行中</i>去看看进度' : '提一个新题目'}</span></span>
        </button>
        <button type="button" class="hb-t hb-daily" disabled title="日常模式还没开放"><span class="hb-zh">日常</span><span class="hb-en">DAILY · 未开放</span>${icon('daily')}</button>
        <button type="button" class="hb-t hb-chat" data-go="chat"><span class="hb-zh">群聊</span><span class="hb-en">CHAT</span>${c.unread ? `<span class="hb-dot">${c.unread > 99 ? '99+' : c.unread}</span>` : ''}${icon('chat')}</button>
        <button type="button" class="hb-t hb-home" data-go="home"><span class="hb-zh">公寓</span><span class="hb-en">APARTMENT</span><span class="hb-sub">看看大家在做什么</span>${icon('home')}</button>
        <button type="button" class="hb-t hb-mem" data-act="people"><span class="hb-zh">成员</span><span class="hb-en">MEMBERS</span></button>
        <button type="button" class="hb-t hb-arc" data-act="history"><span class="hb-zh">档案</span><span class="hb-en">ARCHIVE</span></button>
      </nav>`;
  }

  onClick(e) {
    const go = e.target.closest('[data-go]');
    if (go) return this.go(go.dataset.go);
    const act = e.target.closest('[data-act]');
    if (act?.dataset.act === 'people') return this.openPeople();
    if (act?.dataset.act === 'history') return this.openHistory();
  }
}
