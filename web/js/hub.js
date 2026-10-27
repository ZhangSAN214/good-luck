// 终端首页：背景（可自定义）上悬浮几张液态玻璃卡片：工作（等你拍板的件数）、日常、群聊、余额、公寓、成员、档案。
// 只读：数字来自 /api/sessions、/api/chat/rooms 与 /api/status，不调用模型、不花钱。
// 卡片随鼠标轻微视差、静止时轻轻浮动（Live2D 式的「活着」），系统开了「减少动态」时不动。
// 自定义背景只存在本机浏览器（localStorage），不上传。顶栏有背景音乐播放条（music.js，音乐同样只存本机浏览器）。
// 左侧是大立绘（standee.js，伪 Live2D）：有立绘时首页那句提示改由她在对话框里说；没有立绘时照旧。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const WEEK = '日一二三四五六';
const REFRESH_MS = 60_000;
const BG_KEY = 'roundtable.hub.bg'; // default | night | day | custom
const BG_DATA = 'roundtable.hub.bgData'; // 自定义背景（压缩后的 data URL）
const BG_MAX = 1920; // 自定义背景最长边，压缩后通常几百 KB
const PRESETS = [
  ['default', '默认'],
  ['night', '夜景'],
  ['day', '白天'],
];

/** 今天轮到第几位：日期字符串的简单散列对人数取模，同一天结果固定，和成员是谁无关。 */
export function dutyIndex(date, count) {
  if (!count) return -1;
  const key = `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
  let h = 0;
  for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h % count;
}

/** 首页的一句话：按今天的数字拼出来（本地模板，不是模型生成的）。 */
export function dutyLine({ waiting = 0, running = 0, unread = 0 }) {
  const parts = [];
  if (waiting) parts.push(`办公室有 ${waiting} 件事等你拍板`);
  else if (running) parts.push(`办公室有 ${running} 件事正在办`);
  if (unread) parts.push(`群聊里有 ${unread} 条未读`);
  return parts.length ? `欢迎回来。${parts.join('，')}。` : '欢迎回来。今天没有要你处理的事，随便逛逛吧。';
}

// 极细线性图标（24×24，线宽在 CSS 里统一）
const ICON = {
  daily: '<path d="M5 5h14v15H5zM5 9h14M9 3v4M15 3v4"/>',
  chat: '<path d="M4 6h16v10H9l-5 4z"/>',
  home: '<path d="M4 20V10l8-6 8 6v10M10 20v-5h4v5"/>',
  people: '<circle cx="9" cy="9" r="3"/><path d="M3 19c1-3 3-5 6-5s5 2 6 5M16 7a3 3 0 0 1 0 6M18 14c2 1 3 3 3 5"/>',
  archive: '<path d="M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h5"/>',
  more: '<path d="m9 6 6 6-6 6"/>',
  image: '<path d="M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4"/><circle cx="15" cy="9" r="1.5"/>',
  wallet: '<path d="M4 7h14a2 2 0 0 1 2 2v9H4zM4 7l11-3v3M16 13h1.5"/>',
  standee: '<circle cx="12" cy="6" r="3"/><path d="M7 21l1.5-8.5L12 10l3.5 2.5L17 21M9 14l-2 3M15 14l2 3"/>',
};
const icon = (k, cls = 'hb-i') => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICON[k]}</svg>`;

function store(key, value) {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
    return true;
  } catch {
    return false; // 存不了（隐私模式、空间满）就只在这次生效
  }
}

function load(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** 把上传的图片压到最长边 BG_MAX 的 JPEG data URL。 */
async function shrink(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    const k = Math.min(1, BG_MAX / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(img.naturalWidth * k);
    canvas.height = Math.round(img.naturalHeight * k);
    canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.85);
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** 整站背景跟首页一致：预设写进 html 的 data-rtbg，自定义图片写进 --rt-bg-img（CSS 在 ark.css）。 */
function applyPageBackground(bg) {
  const root = document.documentElement;
  root.dataset.rtbg = bg.key;
  if (bg.data) root.style.setProperty('--rt-bg-img', `url("${bg.data}")`);
  else root.style.removeProperty('--rt-bg-img');
}

export class Hub {
  /**
   * @param {HTMLElement} root
   * @param {{sessions:Function, rooms:Function, go:Function, openPeople:Function, openUsage?:Function, openHistory:Function, status:Function, music?:import('./music.js').Music, standee?:import('./standee.js').Standee, now?:Function}} opts
   */
  constructor(root, { sessions, rooms, go, openPeople, openUsage = null, openHistory, status, music = null, standee = null, now = () => new Date() }) {
    Object.assign(this, { root, sessions, rooms, go, openPeople, openUsage, openHistory, status, music, standee, now });
    if (standee) standee.onToggle = () => this.render();
    this.art = null;
    this.counts = { waiting: 0, running: 0, unread: 0 };
    this.timer = null;
    this.raf = 0;
    this.pointer = { x: 0, y: 0 };
    this.follow = { x: 0, y: 0 };
    this.bgError = '';
    applyPageBackground(this.background());
    root.addEventListener('click', (e) => this.onClick(e));
    root.addEventListener('change', (e) => this.onChange(e));
    root.addEventListener('pointermove', (e) => {
      const r = root.getBoundingClientRect();
      this.pointer = { x: ((e.clientX - r.left) / r.width) * 2 - 1, y: ((e.clientY - r.top) / r.height) * 2 - 1 };
    });
    root.addEventListener('pointerleave', () => (this.pointer = { x: 0, y: 0 }));
    // 点背景面板以外的地方就收起，免得它一直盖着卡片
    document.addEventListener('pointerdown', (e) => {
      const pick = this.root.querySelector('.hb-bgpick:not([hidden])');
      if (pick && !pick.contains(e.target) && !e.target.closest('[data-act="bg"]')) this.togglePicker(false, false);
    });
    root.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.root.querySelector('.hb-bgpick:not([hidden])')) this.togglePicker(false);
    });
  }

  landWork() {
    return load('roundtable.landing') === 'work';
  }

  setArt(art) {
    this.art = art && art.available ? art : null;
    if (!this.root.hidden) this.render();
  }

  async show() {
    this.root.hidden = false;
    this.render();
    // 卡片依次浮上来（只在进入首页时播一次，每分钟的刷新不重播）
    this.root.classList.add('hb-enter');
    clearTimeout(this.enterTimer);
    this.enterTimer = setTimeout(() => this.root.classList.remove('hb-enter'), 900);
    this.animate();
    this.music?.enter();
    if (this.standee) {
      this.standee.start();
      this.standee.greet();
    }
    await this.load();
    clearInterval(this.timer);
    this.timer = setInterval(() => this.load(), REFRESH_MS);
  }

  hide() {
    if (!this.root.hidden) this.music?.leave();
    this.root.hidden = true;
    this.standee?.stop();
    document.documentElement.style.removeProperty('--rt-bx');
    document.documentElement.style.removeProperty('--rt-by');
    clearInterval(this.timer);
    this.timer = null;
    cancelAnimationFrame(this.raf);
    this.raf = 0;
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

  /** 当前背景：自定义图片优先；预设按 key 交给 CSS（data-bg）。 */
  background() {
    const key = load(BG_KEY) || 'default';
    if (key === 'custom') {
      const data = load(BG_DATA);
      if (data && data.startsWith('data:image/')) return { key, data };
      return { key: 'default', data: null };
    }
    return { key: PRESETS.some(([k]) => k === key) ? key : 'default', data: null };
  }

  render() {
    const t = this.now();
    const c = this.counts;
    const st = this.status?.() || null;
    const month = st?.budget?.month;
    const pad = (n) => String(n).padStart(2, '0');
    const big = c.waiting || c.running;
    const bg = this.background();
    applyPageBackground(bg);
    const pickerOpen = this.root.querySelector('.hb-bgpick:not([hidden])') != null;
    const preset = PRESETS.map(
      ([k, label]) => `<button type="button" class="hb-chip" data-bg="${k}" aria-pressed="${bg.key === k}">${label}</button>`,
    ).join('');
    this.root.dataset.bgkey = bg.key;
    this.root.innerHTML = `
      <div class="hb-top">
        <span class="hb-date">${t.getFullYear()}.${pad(t.getMonth() + 1)}.${pad(t.getDate())} 周${WEEK[t.getDay()]} ${pad(t.getHours())}:${pad(t.getMinutes())}</span>
        ${month ? `<button type="button" class="hb-res" data-act="usage" title="本月已用 / 本月上限（点开看余额与用量）">本月已用 $${Number(month.spent_usd).toFixed(2)} / $${Number(month.limit_usd).toFixed(0)}</button>` : ''}
        <label class="hb-land"><input type="checkbox" data-land${this.landWork() ? ' checked' : ''}> 打开网页时直接进办公室</label>
        <span data-music-slot></span>
        ${this.standee ? `<button type="button" class="hb-bgbtn" data-act="standee" aria-expanded="${Boolean(this.standee.editing)}" title="立绘：显示、换一套、调位置大小">${icon('standee', 'hb-i')}立绘</button>` : ''}
        <button type="button" class="hb-bgbtn" data-act="bg" aria-expanded="${pickerOpen}">${icon('image', 'hb-i')}背景</button>
      </div>
      <div class="hb-bgpick hb-glass" role="dialog" aria-label="更换背景"${pickerOpen ? '' : ' hidden'}>
        <b>背景</b>
        <div class="hb-chips">${preset}<button type="button" class="hb-chip" data-act="upload" aria-pressed="${bg.key === 'custom'}">上传图片…</button></div>
        <input type="file" accept="image/*" data-upload hidden>
        <p class="hb-hint">图片只存在这台电脑的浏览器里，不上传。${this.bgError ? `<span class="hb-err" role="alert">${esc(this.bgError)}</span>` : ''}</p>
      </div>
      <span data-standee-slot></span>
      <div class="hb-col">
        <div class="hb-layer" data-depth="1">
          <button type="button" class="hb-glass hb-t hb-hero" data-go="work" aria-label="进入工作：${c.waiting ? `${c.waiting} 件等你拍板` : c.running ? `${c.running} 件正在办` : '提一个新题目'}">
            <span class="hb-tag">${c.waiting ? '待确认' : c.running ? '进行中' : '空闲'}</span>
            <span class="hb-zh">工作</span>
            <span class="hb-num"><b>${pad(big)}</b></span>
            <span class="hb-en">OFFICE</span>
            <span class="hb-say">${c.waiting ? '等你拍板' : c.running ? '正在办' : '提一个新题目'}</span>
            <span class="hb-sub">进行中 ${c.running} 件</span>
            <span class="hb-go">进入 ›</span>
          </button>
        </div>
        <div class="hb-layer hb-row2" data-depth="0.7">
          <button type="button" class="hb-glass hb-t hb-daily" disabled title="日常模式还没开放">${icon('daily')}<span class="hb-zh">日常</span><span class="hb-en">未开放</span></button>
          <button type="button" class="hb-glass hb-t hb-chat" data-go="chat">${icon('chat')}<span class="hb-zh">群聊</span><span class="hb-en">${c.unread ? `${c.unread > 99 ? '99+' : c.unread} 条未读` : '没有未读'}</span>${c.unread ? `<span class="hb-dot" aria-hidden="true">${c.unread > 99 ? '99+' : c.unread}</span>` : ''}${icon('more', 'hb-ar')}</button>
          ${this.openUsage ? `<button type="button" class="hb-glass hb-t hb-bal" data-act="usage">${icon('wallet')}<span class="hb-zh">余额</span><span class="hb-en">${month ? `剩 $${Math.max(0, month.limit_usd - month.spent_usd).toFixed(2)}` : '用量与花费'}</span></button>` : ''}
        </div>
        <div class="hb-layer hb-row3" data-depth="0.5">
          <button type="button" class="hb-glass hb-t hb-home" data-go="home">${icon('home')}<span class="hb-zh">公寓</span><span class="hb-en">看看大家</span>${icon('more', 'hb-ar')}</button>
          <button type="button" class="hb-glass hb-t hb-mem" data-act="people">${icon('people')}<span class="hb-zh">成员</span><span class="hb-en">设定</span>${icon('more', 'hb-ar')}</button>
          <button type="button" class="hb-glass hb-t hb-arc" data-act="history">${icon('archive')}<span class="hb-zh">档案</span><span class="hb-en">历史</span>${icon('more', 'hb-ar')}</button>
        </div>
      </div>
      <p class="hb-voice hb-glass"${this.standee?.active ? ' hidden' : ''}>${esc(dutyLine(c))}</p>`;
    this.music?.mount(this.root.querySelector('[data-music-slot]'));
    if (this.standee) {
      this.standee.mount(this.root.querySelector('[data-standee-slot]'));
      this.standee.setLine(dutyLine(c));
    }
  }

  /** Live2D 式的「活着」：图层按深度跟随鼠标、静止时轻轻浮动；背景反向微移。只在首页可见时跑。 */
  animate() {
    cancelAnimationFrame(this.raf);
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let last = performance.now();
    const step = (now) => {
      if (this.root.hidden) return;
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      // 平滑跟随（临界阻尼式的指数逼近），鼠标再快画面也不会抖
      const k = 1 - Math.exp(-dt * 4);
      this.follow.x += (this.pointer.x - this.follow.x) * k;
      this.follow.y += (this.pointer.y - this.follow.y) * k;
      const { x, y } = this.follow;
      const s = now / 1000;
      // 背景就是整站那一张（body::before），只在首页时跟着微移，画面不被切开
      const doc = document.documentElement.style;
      doc.setProperty('--rt-bx', `${(-x * 14).toFixed(2)}px`);
      doc.setProperty('--rt-by', `${(-y * 9 + Math.sin(s * 0.15) * 3).toFixed(2)}px`);
      this.root.querySelectorAll('.hb-layer').forEach((el, i) => {
        const d = Number(el.dataset.depth) || 1;
        const breathe = Math.sin(s * 0.9 + i * 0.8) * 2.2 * d;
        el.style.transform = `perspective(1400px) translate3d(${(x * 10 * d).toFixed(2)}px,${(y * 7 * d + breathe).toFixed(2)}px,0) rotateX(${(-y * 1.8 * d).toFixed(2)}deg) rotateY(${(x * 2.4 * d).toFixed(2)}deg)`;
      });
      this.root.style.setProperty('--hb-sx', `${(50 + x * 40 + Math.sin(s * 0.25) * 18).toFixed(1)}%`);
      this.root.style.setProperty('--hb-sy', `${(-20 + y * 30 + Math.cos(s * 0.2) * 12).toFixed(1)}%`);
      this.raf = requestAnimationFrame(step);
    };
    this.raf = requestAnimationFrame(step);
  }

  togglePicker(open, refocus = true) {
    const pick = this.root.querySelector('.hb-bgpick');
    const btn = this.root.querySelector('[data-act="bg"]');
    if (!pick) return;
    pick.hidden = !open;
    btn?.setAttribute('aria-expanded', String(open));
    if (open) pick.querySelector('.hb-chip')?.focus();
    else if (refocus) btn?.focus();
  }

  setBackground(key, data = null) {
    this.bgError = '';
    if (key === 'custom') {
      if (!store(BG_DATA, data)) {
        this.bgError = '这台电脑的浏览器存不下这张图，换一张小一点的试试。';
        this.render();
        return;
      }
    }
    store(BG_KEY, key);
    this.render();
  }

  async onChange(e) {
    if (e.target.matches('[data-land]')) {
      store('roundtable.landing', e.target.checked ? 'work' : 'hub');
      return;
    }
    if (e.target.matches('[data-upload]')) {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        this.setBackground('custom', await shrink(file));
      } catch {
        this.bgError = '这张图片读不出来，换一张试试。';
        this.render();
      }
    }
  }

  onClick(e) {
    const go = e.target.closest('[data-go]');
    if (go) return this.go(go.dataset.go, go);
    const preset = e.target.closest('[data-bg]');
    if (preset) return this.setBackground(preset.dataset.bg);
    const act = e.target.closest('[data-act]');
    if (!act) return;
    if (act.dataset.act === 'people') return this.openPeople();
    if (act.dataset.act === 'usage') return this.openUsage?.();
    if (act.dataset.act === 'history') return this.openHistory();
    if (act.dataset.act === 'standee') {
      this.togglePicker(false, false);
      return this.standee?.edit();
    }
    if (act.dataset.act === 'bg') return this.togglePicker(this.root.querySelector('.hb-bgpick')?.hidden);
    if (act.dataset.act === 'upload') return this.root.querySelector('[data-upload]')?.click();
  }
}
