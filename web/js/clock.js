// 终端时钟：最多同时显示三个地方的时间（默认曼彻斯特和中国），点开可以增删城市、改显示名、
// 挑一个当「主时区」（排在最前面、加粗，其他钟的时差都按它算；也可以选「跟随这台电脑」，出门换了时区自动跟着变），
// 选 12 / 24 小时、显示日期和时差；面板里还有秒表（可计次）和倒计时（常用时长一键开始、到点响三声）。
// 时间全靠浏览器自带的 IANA 时区表（Intl）算，夏令时自动切换（曼彻斯特 10 月最后一个周日回冬令时）；
// 不联网、不花钱，设置和正在走的秒表 / 倒计时只存在这台电脑的浏览器里（localStorage），刷新页面不丢。
// 主时区同时存到服务器（hometz.js），公寓、群聊、成员作息都按它过日子。
// 只管显示时间和计时；日期、节假日归日历。

import { homeZone, loadHomeZone, setHomeZone } from './hometz.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const KEY = 'roundtable.clock';
const TIMERS_KEY = 'roundtable.clock.timers'; // 秒表与倒计时：存开始 / 结束的时间点，刷新、切页面都接着走
export const MAX_ZONES = 3;
export const LOCAL = 'local'; // 「跟随这台电脑」：每次都用浏览器当前的时区
const NAME_MAX = 8;
const MAX_LAPS = 99;
const DAY_MS = 86_400_000;
const WEEK = '日一二三四五六';
export const PRESETS = [1, 3, 5, 10, 25, 60]; // 倒计时常用时长（分钟）

// 常用城市放在最前面（中文名），后面跟浏览器认识的全部时区
export const CITIES = [
  ['Europe/London', '曼城'],
  ['Asia/Shanghai', '中国'],
  ['Asia/Hong_Kong', '香港'],
  ['Asia/Taipei', '台北'],
  ['Asia/Tokyo', '东京'],
  ['Asia/Seoul', '首尔'],
  ['Asia/Singapore', '新加坡'],
  ['Asia/Kolkata', '新德里'],
  ['Asia/Dubai', '迪拜'],
  ['Europe/Paris', '巴黎'],
  ['Europe/Berlin', '柏林'],
  ['Europe/Moscow', '莫斯科'],
  ['America/New_York', '纽约'],
  ['America/Chicago', '芝加哥'],
  ['America/Los_Angeles', '洛杉矶'],
  ['America/Vancouver', '温哥华'],
  ['Australia/Sydney', '悉尼'],
  ['Pacific/Auckland', '奥克兰'],
  ['UTC', '协调世界时'],
];
export const DEFAULTS = {
  zones: [
    { tz: 'Europe/London', name: '曼城' },
    { tz: 'Asia/Shanghai', name: '中国' },
  ],
  main: 0,
  h12: false,
  date: true,
  diff: true,
};

/** 浏览器认不认识这个时区（「跟随这台电脑」也算认识）。 */
export function validZone(tz) {
  if (tz === LOCAL) return true;
  if (typeof tz !== 'string' || !tz) return false;
  try {
    new Intl.DateTimeFormat('en', { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/** 本机时区（拿不到就当 UTC）。 */
export function localZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/** 设置里存的时区 → 真正拿去算的时区（「跟随这台电脑」换成本机当前时区）。 */
export function realZone(tz, local = localZone()) {
  return tz === LOCAL ? local : tz;
}

/** 某个时区在 date 这一刻的墙上时间：年月日、时分、星期（0 = 周日）。 */
export function zoneParts(date, tz) {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    hourCycle: 'h23',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: 'numeric',
    weekday: 'short',
  });
  const p = Object.fromEntries(fmt.formatToParts(date).map((x) => [x.type, x.value]));
  return {
    y: Number(p.year),
    m: Number(p.month),
    d: Number(p.day),
    h: Number(p.hour) % 24,
    mi: Number(p.minute),
    wd: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(p.weekday),
  };
}

/** 某个时区在 date 这一刻比 UTC 快多少分钟（夏令时由浏览器的时区表决定）。 */
export function offsetMinutes(date, tz) {
  const p = zoneParts(date, tz);
  const wall = Date.UTC(p.y, p.m - 1, p.d, p.h, p.mi);
  const utc = Math.floor(date.getTime() / 60000) * 60000;
  return Math.round((wall - utc) / 60000);
}

/** 「+8 小时」「−4 小时 30 分」「本机时间」：tz 比 base 快多少。 */
export function diffLabel(date, tz, base = localZone()) {
  const d = offsetMinutes(date, tz) - offsetMinutes(date, base);
  if (!d) return '本机时间';
  const a = Math.abs(d);
  const h = Math.floor(a / 60);
  const m = a % 60;
  return `${d > 0 ? '+' : '−'}${h ? `${h} 小时` : ''}${h && m ? ' ' : ''}${m ? `${m} 分` : ''}`;
}

/** 和主时区比的时差：一样就写「同主时区」，不是「本机时间」（主时区不一定是本机）。 */
export function mainDiffLabel(date, tz, main) {
  const s = diffLabel(date, tz, main);
  return s === '本机时间' ? '同主时区' : s;
}

/** 「09:05」或「上午 9:05」。 */
export function timeText(p, h12 = false) {
  const mm = String(p.mi).padStart(2, '0');
  if (!h12) return `${String(p.h).padStart(2, '0')}:${mm}`;
  const h = p.h % 12 || 12;
  return `${p.h < 12 ? '上午' : '下午'} ${h}:${mm}`;
}

/** 「10.27 周二」。 */
export function dateText(p) {
  return `${p.m}.${p.d} 周${WEEK[p.wd] ?? ''}`;
}

/** 把本机存的设置收拾干净：认不出的时区去掉、最多三个、名字截短、主时区落在范围里；什么都不剩就回到默认。 */
export function cleanSettings(raw) {
  const v = raw && typeof raw === 'object' ? raw : {};
  const zones = (Array.isArray(v.zones) ? v.zones : [])
    .filter((z) => z && validZone(z.tz))
    .slice(0, MAX_ZONES)
    .map((z) => ({ tz: z.tz, name: String(z.name ?? '').trim().slice(0, NAME_MAX) || cityName(z.tz) }));
  const list = zones.length ? zones : DEFAULTS.zones.map((z) => ({ ...z }));
  const main = Number.isInteger(v.main) && v.main >= 0 && v.main < list.length ? v.main : 0;
  return { zones: list, main, h12: v.h12 === true, date: v.date !== false, diff: v.diff !== false };
}

/** 时区的默认显示名：常用城市用中文名，其他取「Asia/Kuala_Lumpur」的最后一段。 */
export function cityName(tz) {
  if (tz === LOCAL) return '本机';
  const hit = CITIES.find(([k]) => k === tz);
  if (hit) return hit[1];
  return String(tz).split('/').pop().replace(/_/g, ' ').slice(0, NAME_MAX);
}

/** 主时区排最前面，其余保持设置里的顺序。 */
export function faceOrder(zones, main) {
  const i = Math.min(Math.max(0, main | 0), zones.length - 1);
  return [zones[i], ...zones.filter((_, k) => k !== i)];
}

// --- 秒表与倒计时（纯函数：状态里只存时间点，现在走到哪由 now 算出来） ---------------------

export const TIMERS0 = { sw: { start: null, acc: 0, laps: [] }, cd: { dur: 5 * 60_000, end: null, left: null, done: false } };

/** 「02:03」「1:02:03」；cs 为真时带百分之一秒「02:03.45」。负数当 0。 */
export function fmtDuration(ms, cs = false) {
  const t = Math.max(0, Math.floor(ms));
  const h = Math.floor(t / 3_600_000);
  const m = Math.floor(t / 60_000) % 60;
  const s = Math.floor(t / 1000) % 60;
  const p = (n) => String(n).padStart(2, '0');
  const main = h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
  return cs ? `${main}.${p(Math.floor(t / 10) % 100)}` : main;
}

/** 倒计时剩余时间的显示：不到一小时「04:59」，否则「1:04:59」；向上取整到秒（还剩 0.2 秒时显示 00:01）。 */
export function fmtLeft(ms) {
  return fmtDuration(Math.ceil(Math.max(0, ms) / 1000) * 1000);
}

/** 秒表一共走了多久。 */
export function swElapsed(sw, now) {
  return (sw.acc || 0) + (sw.start != null ? Math.max(0, now - sw.start) : 0);
}

/** 倒计时还剩多久：在走就看结束时间点，暂停就看存下的剩余，没开始就是设定时长。 */
export function cdLeft(cd, now) {
  if (cd.done) return 0;
  if (cd.end != null) return Math.max(0, cd.end - now);
  return cd.left ?? cd.dur;
}

/** 把本机存的计时状态收拾干净（坏数据就回到初始）。 */
export function cleanTimers(raw) {
  const v = raw && typeof raw === 'object' ? raw : {};
  const num = (x) => (Number.isFinite(x) ? x : null);
  const sw = v.sw && typeof v.sw === 'object' ? v.sw : {};
  const cd = v.cd && typeof v.cd === 'object' ? v.cd : {};
  const dur = num(cd.dur);
  return {
    sw: {
      start: num(sw.start),
      acc: Math.max(0, num(sw.acc) ?? 0),
      laps: (Array.isArray(sw.laps) ? sw.laps : []).filter(Number.isFinite).slice(-MAX_LAPS),
    },
    cd: {
      dur: dur && dur > 0 && dur < DAY_MS ? dur : TIMERS0.cd.dur,
      end: num(cd.end),
      left: num(cd.left),
      done: cd.done === true,
    },
  };
}

function allZones() {
  try {
    return Intl.supportedValuesOf('timeZone');
  } catch {
    return CITIES.map(([k]) => k);
  }
}

/** 到点提醒：三声短促的「嘀」（WebAudio 现合成，不用音频文件）；浏览器不让出声就算了。 */
function beep() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const t0 = ctx.currentTime + 0.02;
    for (let i = 0; i < 3; i += 1) {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = 'sine';
      o.frequency.value = 880;
      const t = t0 + i * 0.32;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.2);
      o.connect(g).connect(ctx.destination);
      o.start(t);
      o.stop(t + 0.22);
    }
    setTimeout(() => ctx.close?.(), 1400);
  } catch {
    // 没有声音也不影响「时间到」的显示
  }
}

const ICON = {
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  del: '<path d="M6 6l12 12M18 6 6 18"/>',
  add: '<path d="M12 5v14M5 12h14"/>',
  star: '<path d="m12 4.5 2.3 4.7 5.2.8-3.8 3.6.9 5.2-4.6-2.4-4.6 2.4.9-5.2L4.5 10l5.2-.8z"/>',
  sw: '<circle cx="12" cy="13.5" r="7"/><path d="M12 13.5V10M10 3.5h4M18.5 7l1.2-1.2"/>',
  cd: '<path d="M7 3.5h10M7 20.5h10M8 3.5c0 4 8 5 8 8.5s-8 4.5-8 8.5M16 3.5c0 4-8 5-8 8.5s8 4.5 8 8.5"/>',
};
const icon = (k) => `<svg class="ck-i" viewBox="0 0 24 24" aria-hidden="true">${ICON[k]}</svg>`;
const TABS = [
  ['clock', '时钟'],
  ['sw', '秒表'],
  ['cd', '倒计时'],
];

export class Clock {
  /** @param {{now?: () => Date, beep?: () => void}} [opts] */
  constructor({ now = () => new Date(), beep: ring = beep } = {}) {
    this.now = now;
    this.ring = ring;
    this.el = document.createElement('div');
    this.el.className = 'ck';
    this.set = this.load();
    this.timers = this.loadTimers();
    this.open = false;
    this.tab = 'clock';
    this.minute = -1;
    this.el.addEventListener('click', (e) => this.onClick(e));
    this.el.addEventListener('change', (e) => this.onChange(e));
    this.el.addEventListener('input', (e) => {
      if (e.target.matches('[data-name]')) this.rename(Number(e.target.dataset.name), e.target.value, false);
    });
    this.el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.matches('[data-cdin]')) {
        e.preventDefault();
        this.cdStart(this.cdInput());
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.open) {
        e.stopPropagation();
        this.toggle(false);
      }
    });
    document.addEventListener('pointerdown', (e) => {
      if (this.open && !this.el.contains(e.target)) this.toggle(false, false);
    });
    // 刷新前倒计时已经到点了：直接显示「时间到」，不补响
    const cd = this.timers.cd;
    if (cd.end != null && cd.end <= this.ms()) Object.assign(cd, { end: null, left: null, done: true });
    this.paint();
    // 每 0.25 秒看一眼：分钟变了才重画钟面；秒表 / 倒计时在走时刷新读数，倒计时到点就响（页面看不见时也照样到点）
    this.timer = setInterval(() => this.pulse(), 250);
    this.syncHome();
  }

  mainTz() {
    return (this.set.zones[this.set.main] || this.set.zones[0]).tz;
  }

  /** 主时区变了：告诉服务器（整个圆桌跟着换）。 */
  pushHome() {
    const tz = this.mainTz();
    if (tz !== homeZone()) setHomeZone(tz);
  }

  /** 开页面时和服务器对一下：服务器上没选过就把这里的主时区报上去，选过就以服务器为准（别的设备改过）。 */
  async syncHome() {
    const { zone, chosen } = await loadHomeZone();
    if (!chosen) return setHomeZone(this.mainTz());
    if (zone === this.mainTz()) return;
    const i = this.set.zones.findIndex((z) => z.tz === zone);
    this.update((s) => {
      const z = { tz: zone, name: cityName(zone) };
      if (i >= 0) s.main = i;
      else if (s.zones.length < MAX_ZONES) {
        s.zones.unshift(z);
        s.main = 0;
      } else s.zones[s.main] = z;
    }, this.open);
  }

  ms() {
    return this.now().getTime();
  }

  load() {
    try {
      return cleanSettings(JSON.parse(localStorage.getItem(KEY) || 'null'));
    } catch {
      return cleanSettings(null);
    }
  }

  loadTimers() {
    try {
      return cleanTimers(JSON.parse(localStorage.getItem(TIMERS_KEY) || 'null'));
    } catch {
      return cleanTimers(null);
    }
  }

  save() {
    try {
      localStorage.setItem(KEY, JSON.stringify(this.set));
    } catch {
      // 存不了（隐私模式）就只在这次生效
    }
  }

  saveTimers() {
    try {
      localStorage.setItem(TIMERS_KEY, JSON.stringify(this.timers));
    } catch {
      // 存不了就只在这次生效
    }
  }

  /** 挂到终端首页顶栏的空位上（首页每次重画都会再挂一次，状态不丢）。 */
  mount(slot) {
    if (slot) slot.replaceWith(this.el);
    this.tick(true);
  }

  running() {
    const { sw, cd } = this.timers;
    return { sw: sw.start != null, cd: cd.end != null, done: cd.done };
  }

  pulse() {
    const cd = this.timers.cd;
    if (cd.end != null && cd.end <= this.ms()) {
      Object.assign(cd, { end: null, left: null, done: true });
      this.saveTimers();
      this.ring();
      this.paintRun();
      if (this.open && this.tab === 'cd') this.paintTab();
    }
    this.tick();
    const r = this.running();
    if (r.sw || r.cd) this.readouts();
  }

  tick(force = false) {
    if (!this.el.isConnected || (!force && this.el.offsetParent === null)) return;
    const t = this.now();
    const k = Math.floor(t.getTime() / 60000);
    if (!force && k === this.minute) return;
    this.minute = k;
    this.el.querySelector('.ck-faces')?.replaceChildren(...this.faces(t));
  }

  faces(t) {
    const local = localZone();
    const z0 = this.set.zones[this.set.main] || this.set.zones[0];
    const mainTz = realZone(z0.tz, local);
    return faceOrder(this.set.zones, this.set.main).map((z, i) => {
      const tz = realZone(z.tz, local);
      const p = zoneParts(t, tz);
      const span = document.createElement('span');
      span.className = i === 0 ? 'ck-face ck-main' : 'ck-face';
      span.dataset.tz = z.tz;
      span.dataset.real = tz;
      const diff = i === 0 ? '主时区' : mainDiffLabel(t, tz, mainTz);
      const sub = [this.set.date ? dateText(p) : '', this.set.diff ? diff : ''].filter(Boolean);
      span.innerHTML = `<span class="ck-name">${esc(z.name)}</span><b class="ck-time">${timeText(p, this.set.h12)}</b>${
        sub.length ? `<span class="ck-sub">${esc(sub.join(' · '))}</span>` : ''
      }`;
      return span;
    });
  }

  /** 钟面右边的小牌子：秒表 / 倒计时在走或到点时显示，点它直接打开对应的页。 */
  runHTML() {
    const now = this.ms();
    const { sw, cd } = this.timers;
    const out = [];
    if (cd.done) out.push(`<span class="ck-run ring" data-jump="cd" title="倒计时到了，点开处理">${icon('cd')}时间到</span>`);
    else if (cd.end != null) out.push(`<span class="ck-run" data-jump="cd" title="倒计时">${icon('cd')}<b data-ro="cdmini">${fmtLeft(cdLeft(cd, now))}</b></span>`);
    if (sw.start != null) out.push(`<span class="ck-run" data-jump="sw" title="秒表">${icon('sw')}<b data-ro="swmini">${fmtDuration(swElapsed(sw, now))}</b></span>`);
    return out.join('');
  }

  paintRun() {
    const box = this.el.querySelector('.ck-runs');
    if (box) box.innerHTML = this.runHTML();
  }

  /** 只改数字，不重画面板（输入框不丢焦点）。 */
  readouts() {
    const now = this.ms();
    const { sw, cd } = this.timers;
    const set = (k, v) => {
      const el = this.el.querySelector(`[data-ro="${k}"]`);
      if (el && el.textContent !== v) el.textContent = v;
    };
    set('swmini', fmtDuration(swElapsed(sw, now)));
    set('cdmini', fmtLeft(cdLeft(cd, now)));
    if (!this.open) return;
    set('sw', fmtDuration(swElapsed(sw, now), true));
    set('cd', fmtLeft(cdLeft(cd, now)));
    const bar = this.el.querySelector('[data-ro="cdbar"]');
    if (bar) bar.style.width = `${(100 * (1 - cdLeft(cd, now) / (cd.dur || 1))).toFixed(2)}%`;
  }

  clockTab() {
    const s = this.set;
    const full = s.zones.length >= MAX_ZONES;
    const common = CITIES.map(([k, n]) => `<option value="${k}">${esc(n)}（${esc(k)}）</option>`).join('');
    const rest = allZones()
      .filter((k) => !CITIES.some(([c]) => c === k))
      .map((k) => `<option value="${esc(k)}">${esc(k)}</option>`)
      .join('');
    const local = `<option value="${LOCAL}">跟随这台电脑（现在是 ${esc(localZone())}）</option>`;
    const rows = s.zones
      .map(
        (z, i) => `<li class="ck-row">
          <button type="button" class="ck-ib ck-star" data-main="${i}" aria-pressed="${i === s.main}" title="${i === s.main ? '这是主时区' : '设为主时区'}" aria-label="把${esc(z.name)}设为主时区">${icon('star')}</button>
          <input type="text" data-name="${i}" value="${esc(z.name)}" maxlength="${NAME_MAX}" aria-label="第 ${i + 1} 个时钟的显示名">
          <select data-tz="${i}" aria-label="第 ${i + 1} 个时钟的时区">${local}<optgroup label="常用">${common}</optgroup><optgroup label="全部时区">${rest}</optgroup></select>
          <button type="button" class="ck-ib" data-del="${i}" title="去掉" aria-label="去掉${esc(z.name)}"${s.zones.length <= 1 ? ' disabled' : ''}>${icon('del')}</button>
        </li>`,
      )
      .join('');
    return `
      <ul class="ck-list">${rows}</ul>
      <p class="hb-hint ck-mainhint">点星星设主时区：它排最前面、加粗显示，其他钟的时差按它算；公寓的天色和作息、群聊时间、成员几点睡觉也都按它。时区选「跟随这台电脑」，出门换了时区会自己跟着变。</p>
      <button type="button" class="hb-chip ck-add" data-add${full ? ' disabled' : ''}>${icon('add')}${full ? `最多 ${MAX_ZONES} 个` : '添加城市'}</button>
      <div class="hb-chips" role="group" aria-label="小时制">
        <button type="button" class="hb-chip" data-h12="0" aria-pressed="${!s.h12}">24 小时</button>
        <button type="button" class="hb-chip" data-h12="1" aria-pressed="${s.h12}">12 小时</button>
      </div>
      <label class="ck-opt"><input type="checkbox" data-opt="date"${s.date ? ' checked' : ''}> 显示日期</label>
      <label class="ck-opt"><input type="checkbox" data-opt="diff"${s.diff ? ' checked' : ''}> 显示时差（和主时区比）</label>
      <p class="hb-hint">夏令时按浏览器自带的时区表自动切换。不联网，设置只存在这台电脑的浏览器里。</p>`;
  }

  swTab() {
    const sw = this.timers.sw;
    const now = this.ms();
    const on = sw.start != null;
    const total = swElapsed(sw, now);
    const laps = sw.laps
      .map((at, i) => [i + 1, at - (sw.laps[i - 1] || 0), at])
      .reverse()
      .map(([n, d, at]) => `<li><span>第 ${n} 圈</span><b>${fmtDuration(d, true)}</b><span>${fmtDuration(at, true)}</span></li>`)
      .join('');
    return `
      <div class="ck-big" data-ro="sw" aria-live="off">${fmtDuration(total, true)}</div>
      <div class="ck-ctl">
        <button type="button" class="ck-go${on ? ' stop' : ''}" data-sw="${on ? 'pause' : 'start'}">${on ? '暂停' : total ? '继续' : '开始'}</button>
        <button type="button" class="hb-chip" data-sw="${on ? 'lap' : 'reset'}"${!on && !total ? ' disabled' : ''}>${on ? '计次' : '清零'}</button>
      </div>
      ${laps ? `<ol class="ck-laps" aria-label="计次">${laps}</ol>` : '<p class="hb-hint">开始后点「计次」记下每一圈；切到别的页面、刷新网页，秒表都接着走。</p>'}`;
  }

  cdTab() {
    const cd = this.timers.cd;
    const now = this.ms();
    const left = cdLeft(cd, now);
    const on = cd.end != null;
    const paused = !on && !cd.done && cd.left != null;
    const s = Math.round(cd.dur / 1000);
    const presets = PRESETS.map(
      (m) => `<button type="button" class="hb-chip" data-preset="${m}" aria-pressed="${cd.dur === m * 60_000}">${m >= 60 ? `${m / 60} 小时` : `${m} 分钟`}</button>`,
    ).join('');
    if (cd.done) {
      return `
        <div class="ck-big ring" role="alert">时间到</div>
        <p class="hb-hint">设定的 ${fmtDuration(cd.dur)} 已经走完。</p>
        <div class="ck-ctl">
          <button type="button" class="ck-go" data-cd="again">再来一次</button>
          <button type="button" class="hb-chip" data-cd="reset">知道了</button>
        </div>`;
    }
    return `
      <div class="ck-big" data-ro="cd" aria-live="off">${fmtLeft(left)}</div>
      <div class="ck-bar"><i data-ro="cdbar" style="width:${(100 * (1 - left / (cd.dur || 1))).toFixed(2)}%"></i></div>
      <div class="ck-ctl">
        ${
          on
            ? '<button type="button" class="ck-go stop" data-cd="pause">暂停</button><button type="button" class="hb-chip" data-cd="reset">取消</button>'
            : paused
              ? '<button type="button" class="ck-go" data-cd="resume">继续</button><button type="button" class="hb-chip" data-cd="reset">取消</button>'
              : '<button type="button" class="ck-go" data-cd="start">开始</button>'
        }
      </div>
      ${
        on || paused
          ? ''
          : `<div class="ck-cdin" role="group" aria-label="自定义时长">
        <label><input type="number" data-cdin="h" min="0" max="23" value="${Math.floor(s / 3600)}" aria-label="小时">时</label>
        <label><input type="number" data-cdin="m" min="0" max="59" value="${Math.floor(s / 60) % 60}" aria-label="分钟">分</label>
        <label><input type="number" data-cdin="s" min="0" max="59" value="${s % 60}" aria-label="秒">秒</label>
      </div>
      <div class="hb-chips" role="group" aria-label="常用时长（点了直接开始）">${presets}</div>`
      }
      <p class="hb-hint">到点响三声、钟面旁边显示「时间到」。网页要开着才会响；刷新网页倒计时接着走。</p>`;
  }

  paintTab() {
    const box = this.el.querySelector('.ck-tview');
    if (!box) return;
    box.innerHTML = this.tab === 'sw' ? this.swTab() : this.tab === 'cd' ? this.cdTab() : this.clockTab();
    if (this.tab === 'clock') {
      this.set.zones.forEach((z, i) => {
        const sel = this.el.querySelector(`[data-tz="${i}"]`);
        if (sel) sel.value = z.tz;
      });
    }
    this.el.querySelectorAll('[data-tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === this.tab)));
  }

  paint() {
    const tabs = TABS.map(
      ([k, n]) => `<button type="button" role="tab" class="ck-tab" data-tab="${k}" aria-selected="${this.tab === k}">${n}</button>`,
    ).join('');
    this.el.innerHTML = `
      <button type="button" class="ck-btn" aria-expanded="${this.open}" aria-label="时钟（点开设置城市、秒表、倒计时）" title="点开设置城市、秒表、倒计时">${icon('clock')}<span class="ck-faces"></span><span class="ck-runs">${this.runHTML()}</span></button>
      <div class="ck-panel" role="dialog" aria-label="时钟"${this.open ? '' : ' hidden'}>
        <div class="ck-tabs" role="tablist" aria-label="时钟功能">${tabs}</div>
        <div class="ck-tview"></div>
      </div>`;
    this.paintTab();
    this.tick(true);
  }

  toggle(open, refocus = true) {
    this.open = open;
    const panel = this.el.querySelector('.ck-panel');
    const btn = this.el.querySelector('.ck-btn');
    if (!panel) return;
    panel.hidden = !open;
    btn?.setAttribute('aria-expanded', String(open));
    if (open) {
      this.paintTab();
      panel.querySelector('.ck-tab[aria-selected="true"]')?.focus();
    } else if (refocus) btn?.focus();
  }

  update(fn, repaint = true) {
    fn(this.set);
    this.set = cleanSettings(this.set);
    this.save();
    this.pushHome();
    if (repaint) {
      this.paintTab();
      this.tick(true);
    } else this.tick(true);
  }

  /** 改了计时状态：存起来，刷新钟面旁的小牌子和当前页。 */
  timersChanged() {
    this.saveTimers();
    this.paintRun();
    if (this.open && this.tab !== 'clock') this.paintTab();
  }

  rename(i, name, repaint) {
    if (!this.set.zones[i]) return;
    this.set.zones[i].name = String(name).slice(0, NAME_MAX);
    // 打字时只存、只刷新时间，不重画面板，光标不跳；名字清空时先不回填默认名
    try {
      localStorage.setItem(KEY, JSON.stringify(this.set));
    } catch {
      // 存不了就只在这次生效
    }
    if (repaint) this.update(() => {});
    else if (name.trim()) this.tick(true);
  }

  cdInput() {
    const v = (k) => Math.max(0, Number(this.el.querySelector(`[data-cdin="${k}"]`)?.value) || 0);
    return Math.min(DAY_MS - 1000, (v('h') * 3600 + Math.min(59, v('m')) * 60 + Math.min(59, v('s'))) * 1000);
  }

  cdStart(dur) {
    if (!(dur > 0)) return;
    this.timers.cd = { dur, end: this.ms() + dur, left: null, done: false };
    this.timersChanged();
  }

  swAct(act) {
    const sw = this.timers.sw;
    const now = this.ms();
    if (act === 'start') sw.start = now;
    else if (act === 'pause') Object.assign(sw, { acc: swElapsed(sw, now), start: null });
    else if (act === 'lap' && sw.laps.length < MAX_LAPS) sw.laps.push(swElapsed(sw, now));
    else if (act === 'reset') this.timers.sw = { start: null, acc: 0, laps: [] };
    this.timersChanged();
  }

  cdAct(act) {
    const cd = this.timers.cd;
    const now = this.ms();
    if (act === 'start') return this.cdStart(this.cdInput());
    if (act === 'again') return this.cdStart(cd.dur);
    if (act === 'pause') Object.assign(cd, { left: cdLeft(cd, now), end: null });
    else if (act === 'resume') Object.assign(cd, { end: now + (cd.left ?? cd.dur), left: null });
    else if (act === 'reset') Object.assign(cd, { end: null, left: null, done: false });
    this.timersChanged();
  }

  onChange(e) {
    const t = e.target;
    if (t.matches('[data-name]')) return this.rename(Number(t.dataset.name), t.value, true);
    if (t.matches('[data-tz]')) {
      const i = Number(t.dataset.tz);
      return this.update((s) => {
        const z = s.zones[i];
        if (!z) return;
        // 名字还是旧时区的默认名就跟着换，自己改过的名字保留
        if (z.name === cityName(z.tz)) z.name = cityName(t.value);
        z.tz = t.value;
      });
    }
    if (t.matches('[data-opt]')) return this.update((s) => (s[t.dataset.opt] = t.checked), false);
    if (t.matches('[data-cdin]')) {
      const dur = this.cdInput();
      if (dur > 0) {
        this.timers.cd.dur = dur;
        this.saveTimers();
        this.readouts();
        const big = this.el.querySelector('[data-ro="cd"]');
        if (big) big.textContent = fmtLeft(dur);
      }
    }
  }

  onClick(e) {
    const jump = e.target.closest('[data-jump]');
    if (jump) {
      e.stopPropagation();
      this.tab = jump.dataset.jump;
      if (this.open) this.paintTab();
      else this.toggle(true);
      return;
    }
    if (e.target.closest('.ck-btn')) return this.toggle(!this.open);
    const tab = e.target.closest('[data-tab]');
    if (tab) {
      this.tab = tab.dataset.tab;
      return this.paintTab();
    }
    const sw = e.target.closest('[data-sw]');
    if (sw) return this.swAct(sw.dataset.sw);
    const cd = e.target.closest('[data-cd]');
    if (cd) return this.cdAct(cd.dataset.cd);
    const preset = e.target.closest('[data-preset]');
    if (preset) return this.cdStart(Number(preset.dataset.preset) * 60_000);
    const star = e.target.closest('[data-main]');
    if (star) return this.update((s) => (s.main = Number(star.dataset.main)));
    const del = e.target.closest('[data-del]');
    if (del) {
      return this.update((s) => {
        const i = Number(del.dataset.del);
        if (s.zones.length <= 1) return;
        s.zones.splice(i, 1);
        if (s.main === i) s.main = 0;
        else if (s.main > i) s.main -= 1;
      });
    }
    if (e.target.closest('[data-add]')) {
      return this.update((s) => {
        if (s.zones.length >= MAX_ZONES) return;
        const used = new Set(s.zones.map((z) => z.tz));
        const [tz, name] = CITIES.find(([k]) => !used.has(k)) || CITIES[0];
        s.zones.push({ tz, name });
      });
    }
    const h = e.target.closest('[data-h12]');
    if (h) return this.update((s) => (s.h12 = h.dataset.h12 === '1'));
  }
}
