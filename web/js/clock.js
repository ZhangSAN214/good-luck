// 终端时钟：最多同时显示三个地方的时间（默认曼彻斯特和中国），点开可以增删城市、改显示名、
// 选 12 / 24 小时、显示日期和与本机的时差。
// 时间全靠浏览器自带的 IANA 时区表（Intl）算，夏令时自动切换（曼彻斯特 10 月最后一个周日回冬令时）；
// 不联网、不花钱，设置只存在这台电脑的浏览器里（localStorage）。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const KEY = 'roundtable.clock';
export const MAX_ZONES = 3;
const NAME_MAX = 8;
const WEEK = '日一二三四五六';

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
export const DEFAULTS = { zones: [{ tz: 'Europe/London', name: '曼城' }, { tz: 'Asia/Shanghai', name: '中国' }], h12: false, date: true, diff: true };

/** 浏览器认不认识这个时区。 */
export function validZone(tz) {
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

/** 把本机存的设置收拾干净：认不出的时区去掉、最多三个、名字截短；什么都不剩就回到默认。 */
export function cleanSettings(raw) {
  const v = raw && typeof raw === 'object' ? raw : {};
  const zones = (Array.isArray(v.zones) ? v.zones : [])
    .filter((z) => z && validZone(z.tz))
    .slice(0, MAX_ZONES)
    .map((z) => ({ tz: z.tz, name: String(z.name ?? '').trim().slice(0, NAME_MAX) || cityName(z.tz) }));
  return {
    zones: zones.length ? zones : DEFAULTS.zones.map((z) => ({ ...z })),
    h12: v.h12 === true,
    date: v.date !== false,
    diff: v.diff !== false,
  };
}

/** 时区的默认显示名：常用城市用中文名，其他取「Asia/Kuala_Lumpur」的最后一段。 */
export function cityName(tz) {
  const hit = CITIES.find(([k]) => k === tz);
  if (hit) return hit[1];
  return String(tz).split('/').pop().replace(/_/g, ' ').slice(0, NAME_MAX);
}

function allZones() {
  try {
    return Intl.supportedValuesOf('timeZone');
  } catch {
    return CITIES.map(([k]) => k);
  }
}

const ICON = {
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  del: '<path d="M6 6l12 12M18 6 6 18"/>',
  add: '<path d="M12 5v14M5 12h14"/>',
};
const icon = (k) => `<svg class="ck-i" viewBox="0 0 24 24" aria-hidden="true">${ICON[k]}</svg>`;

export class Clock {
  /** @param {{now?: () => Date}} [opts] */
  constructor({ now = () => new Date() } = {}) {
    this.now = now;
    this.el = document.createElement('div');
    this.el.className = 'ck';
    this.set = this.load();
    this.open = false;
    this.minute = -1;
    this.el.addEventListener('click', (e) => this.onClick(e));
    this.el.addEventListener('change', (e) => this.onChange(e));
    this.el.addEventListener('input', (e) => {
      if (e.target.matches('[data-name]')) this.rename(Number(e.target.dataset.name), e.target.value, false);
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
    this.paint();
    // 每秒看一眼，分钟变了才重画时间（页面看不见时不动）
    this.timer = setInterval(() => this.tick(), 1000);
  }

  load() {
    try {
      return cleanSettings(JSON.parse(localStorage.getItem(KEY) || 'null'));
    } catch {
      return cleanSettings(null);
    }
  }

  save() {
    try {
      localStorage.setItem(KEY, JSON.stringify(this.set));
    } catch {
      // 存不了（隐私模式）就只在这次生效
    }
  }

  /** 挂到终端首页顶栏的空位上（首页每次重画都会再挂一次，状态不丢）。 */
  mount(slot) {
    if (slot) slot.replaceWith(this.el);
    this.tick(true);
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
    const base = localZone();
    return this.set.zones.map((z) => {
      const p = zoneParts(t, z.tz);
      const span = document.createElement('span');
      span.className = 'ck-face';
      span.dataset.tz = z.tz;
      const sub = [this.set.date ? dateText(p) : '', this.set.diff ? diffLabel(t, z.tz, base) : ''].filter(Boolean);
      span.innerHTML = `<span class="ck-name">${esc(z.name)}</span><b class="ck-time">${timeText(p, this.set.h12)}</b>${
        sub.length ? `<span class="ck-sub">${esc(sub.join(' · '))}</span>` : ''
      }`;
      return span;
    });
  }

  paint() {
    const s = this.set;
    const full = s.zones.length >= MAX_ZONES;
    const common = CITIES.map(([k, n]) => `<option value="${k}">${esc(n)}（${esc(k)}）</option>`).join('');
    const rest = allZones()
      .filter((k) => !CITIES.some(([c]) => c === k))
      .map((k) => `<option value="${esc(k)}">${esc(k)}</option>`)
      .join('');
    const rows = s.zones
      .map(
        (z, i) => `<li class="ck-row">
          <input type="text" data-name="${i}" value="${esc(z.name)}" maxlength="${NAME_MAX}" aria-label="第 ${i + 1} 个时钟的显示名">
          <select data-tz="${i}" aria-label="第 ${i + 1} 个时钟的时区"><optgroup label="常用">${common}</optgroup><optgroup label="全部时区">${rest}</optgroup></select>
          <button type="button" class="ck-ib" data-del="${i}" title="去掉" aria-label="去掉${esc(z.name)}"${s.zones.length <= 1 ? ' disabled' : ''}>${icon('del')}</button>
        </li>`,
      )
      .join('');
    this.el.innerHTML = `
      <button type="button" class="ck-btn" aria-expanded="${this.open}" aria-label="时钟（点开设置城市）" title="点开设置城市">${icon('clock')}<span class="ck-faces"></span></button>
      <div class="ck-panel" role="dialog" aria-label="时钟设置"${this.open ? '' : ' hidden'}>
        <b class="ck-title">时钟</b>
        <ul class="ck-list">${rows}</ul>
        <button type="button" class="hb-chip ck-add" data-add${full ? ' disabled' : ''}>${icon('add')}${full ? `最多 ${MAX_ZONES} 个` : '添加城市'}</button>
        <div class="hb-chips" role="group" aria-label="小时制">
          <button type="button" class="hb-chip" data-h12="0" aria-pressed="${!s.h12}">24 小时</button>
          <button type="button" class="hb-chip" data-h12="1" aria-pressed="${s.h12}">12 小时</button>
        </div>
        <label class="ck-opt"><input type="checkbox" data-opt="date"${s.date ? ' checked' : ''}> 显示日期</label>
        <label class="ck-opt"><input type="checkbox" data-opt="diff"${s.diff ? ' checked' : ''}> 显示与本机的时差</label>
        <p class="hb-hint">夏令时按浏览器自带的时区表自动切换。不联网，设置只存在这台电脑的浏览器里。</p>
      </div>`;
    s.zones.forEach((z, i) => {
      const sel = this.el.querySelector(`[data-tz="${i}"]`);
      if (sel) sel.value = z.tz;
    });
    this.tick(true);
  }

  toggle(open, refocus = true) {
    this.open = open;
    const panel = this.el.querySelector('.ck-panel');
    const btn = this.el.querySelector('.ck-btn');
    if (!panel) return;
    panel.hidden = !open;
    btn?.setAttribute('aria-expanded', String(open));
    if (open) panel.querySelector('input,select,button')?.focus();
    else if (refocus) btn?.focus();
  }

  update(fn, repaint = true) {
    fn(this.set);
    this.set = cleanSettings(this.set);
    this.save();
    if (repaint) {
      this.paint();
      if (this.open) this.toggle(true);
    } else this.tick(true);
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
  }

  onClick(e) {
    if (e.target.closest('.ck-btn')) return this.toggle(!this.open);
    const del = e.target.closest('[data-del]');
    if (del) return this.update((s) => s.zones.length > 1 && s.zones.splice(Number(del.dataset.del), 1));
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
