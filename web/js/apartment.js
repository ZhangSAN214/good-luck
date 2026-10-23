// 公寓楼：员工下班后住的地方（像素风剖面图，类似基建）。住户按作息在各房间活动，正在办事的在办公室 / 会议室。
// 数据来自 /api/apartment（按浏览器本地时间）；小人用 /api/members 的 chibi，没有素材时用昵称首字。
// 匿名未揭晓的会话：服务端只给数量，这里只在会议室门上挂牌，不显示是谁。

import { cutout } from './cutout.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const REFRESH_MS = 30_000;

// 活动 → 头顶的小气泡（一个字，闲着时不显示）
const BUBBLES = { sleep: 'z', eat: '吃', work: '忙', wait: '等', exercise: '练', chat: '聊', read: '读', stargaze: '望', game: '玩' };
const WEEK = '一二三四五六日';
const ROOM_VERB = { office: '在办公室', meeting: '在会议室', canteen: '在食堂', lounge: '在休息室', gym: '在健身房', rooftop: '在天台' };
const ROLE = { manager: '经理', staff: '员工' };

// 像素家具：每个房间一张 SVG（viewBox 96×32，地板在 y=32，crispEdges），颜色用 CSS 变量，深浅主题都能看清。
// o = {lit 开灯, night 夜里, accent 房主的主题色（宿舍）}
const W = 96;
const H = 32;
const R = (x, y, w, h, c) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${c}"/>`;
const legs = (x, w, y, c = 'var(--px-wood-2)') => R(x + 1, y, 2, H - y, c) + R(x + w - 3, y, 2, H - y, c);
const win = (x, y, w, h, o) =>
  R(x, y, w, h, 'var(--px-frame)') +
  R(x + 1, y + 1, w - 2, h - 2, o.night ? 'var(--px-nightsky)' : 'var(--px-glass)') +
  (o.night ? R(x + 3, y + 2, 1, 1, 'var(--px-star)') + R(x + w - 4, y + 4, 2, 2, 'var(--px-moon)') : R(x + 2, y + 2, 3, 1, 'var(--px-cloth)')) +
  R(x + Math.floor(w / 2), y + 1, 1, h - 2, 'var(--px-frame)');
const plant = (x) => R(x, 26, 6, 6, 'var(--px-wood)') + R(x - 1, 20, 8, 6, 'var(--px-leaf)') + R(x + 1, 17, 4, 3, 'var(--px-leaf-2)');
const books = (x, y, n) =>
  R(x, y + 5, n * 3 + 2, 1, 'var(--px-wood-2)') +
  Array.from({ length: n }, (_, i) => R(x + 1 + i * 3, y + (i % 2), 2, 5 - (i % 2), ['var(--px-accent)', 'var(--px-leaf)', 'var(--px-glass)', 'var(--px-accent-2)'][i % 4])).join('');
const chair = (x, flip = false) => R(x, 24, 6, 2, 'var(--px-accent-2)') + R(flip ? x : x + 4, 18, 2, 6, 'var(--px-accent-2)') + legs(x - 1, 8, 26, 'var(--px-frame)');
const lamp = (x, on) => R(x + 2, 12, 1, 20, 'var(--px-frame)') + R(x, 9, 5, 3, on ? 'var(--px-glow)' : 'var(--px-cloth)') + (on ? R(x - 1, 12, 7, 1, 'var(--px-glow-soft)') : '');

const FURNITURE = {
  dorm: (o) =>
    // 床（被子是房主的主题色）、窗帘、书桌台灯、墙上书架、地毯、绿植
    R(4, 14, 3, 18, 'var(--px-wood-2)') + R(4, 22, 36, 4, 'var(--px-wood)') + legs(4, 36, 26) +
    R(7, 18, 8, 4, 'var(--px-cloth)') + R(15, 19, 25, 3, o.accent) + R(15, 19, 25, 1, 'var(--px-cloth-2)') +
    win(46, 5, 16, 12, o) + R(44, 4, 3, 14, o.accent) + R(61, 4, 3, 14, o.accent) +
    R(68, 21, 18, 2, 'var(--px-wood)') + legs(68, 18, 23) + R(71, 17, 3, 4, 'var(--px-glow)') + R(80, 18, 4, 3, 'var(--px-board)') +
    books(68, 8, 5) + R(18, 30, 30, 2, 'var(--px-rug)') + plant(88),
  office: (o) =>
    // 三组工位（显示器亮 = 有人上班 / 开灯）、白板图表、饮水机、绿植、挂钟
    [6, 30, 54].map((x) => R(x, 20, 18, 2, 'var(--px-wood)') + legs(x, 18, 22) + R(x + 5, 11, 9, 7, 'var(--px-frame)') + R(x + 6, 12, 7, 5, o.lit ? 'var(--px-screen)' : 'var(--px-glass)') + R(x + 8, 18, 3, 2, 'var(--px-frame)') + R(x + 1, 19, 4, 1, 'var(--px-cloth)') + chair(x + 12)).join('') +
    R(78, 4, 14, 10, 'var(--px-board)') + R(80, 10, 2, 3, 'var(--px-accent)') + R(83, 8, 2, 5, 'var(--px-leaf)') + R(86, 6, 2, 7, 'var(--px-glass)') +
    R(80, 20, 6, 12, 'var(--px-cloth)') + R(80, 16, 6, 4, 'var(--px-glass)') + plant(88) + R(40, 2, 6, 6, 'var(--px-cloth)') + R(42, 4, 2, 1, 'var(--px-frame)'),
  meeting: (o) =>
    // 投影幕、长桌和一圈椅子、窗
    R(6, 3, 30, 14, 'var(--px-board)') + R(6, 2, 30, 1, 'var(--px-frame)') + R(9, 6, 12, 1, 'var(--px-accent)') + R(9, 9, 18, 1, 'var(--px-frame)') + R(9, 12, 8, 1, 'var(--px-leaf)') +
    R(26, 20, 44, 3, 'var(--px-wood)') + legs(28, 40, 23) + chair(22) + chair(70, true) + R(34, 17, 4, 3, 'var(--px-accent-2)') + R(46, 17, 4, 3, 'var(--px-accent-2)') + R(58, 17, 4, 3, 'var(--px-accent-2)') +
    win(76, 5, 16, 12, o),
  canteen: (o) =>
    // 灶台（冒热气）、菜单板、冰箱、两张桌子带凳子
    R(2, 18, 24, 14, 'var(--px-frame)') + R(2, 16, 24, 2, 'var(--px-wood)') + R(6, 12, 7, 4, 'var(--px-glass)') + R(16, 13, 6, 3, 'var(--px-accent-2)') +
    (o.night ? '' : R(8, 8, 1, 3, 'var(--px-cloth)') + R(11, 6, 1, 4, 'var(--px-cloth)')) +
    R(4, 3, 16, 9, 'var(--px-board-dark)') + R(6, 5, 8, 1, 'var(--px-cloth)') + R(6, 8, 10, 1, 'var(--px-cloth)') +
    R(28, 8, 9, 24, 'var(--px-cloth)') + R(28, 18, 9, 1, 'var(--px-frame)') + R(35, 12, 1, 4, 'var(--px-frame)') +
    [44, 70].map((x) => R(x, 21, 20, 2, 'var(--px-wood)') + legs(x + 2, 16, 23) + R(x - 4, 26, 4, 1, 'var(--px-accent)') + R(x - 3, 27, 2, 5, 'var(--px-frame)') + R(x + 20, 26, 4, 1, 'var(--px-accent)') + R(x + 21, 27, 2, 5, 'var(--px-frame)') + R(x + 6, 19, 4, 2, 'var(--px-cloth)')).join(''),
  lounge: (o) =>
    // 沙发、地毯、电视和游戏机、书架、落地灯、绿植
    R(6, 20, 30, 7, 'var(--px-accent)') + R(6, 15, 30, 5, 'var(--px-accent-2)') + R(3, 18, 4, 10, 'var(--px-accent-2)') + R(35, 18, 4, 10, 'var(--px-accent-2)') + R(12, 18, 6, 3, 'var(--px-cloth)') + R(5, 27, 2, 5, 'var(--px-wood-2)') + R(35, 27, 2, 5, 'var(--px-wood-2)') +
    R(8, 30, 40, 2, 'var(--px-rug)') +
    R(46, 6, 22, 14, 'var(--px-frame)') + R(47, 7, 20, 12, o.lit ? 'var(--px-screen)' : 'var(--px-glass)') + R(55, 20, 4, 3, 'var(--px-frame)') + R(48, 23, 18, 9, 'var(--px-wood)') + R(53, 25, 8, 2, 'var(--px-frame)') +
    books(72, 6, 4) + books(72, 14, 4) + R(72, 20, 14, 12, 'var(--px-wood-2)') + lamp(40, o.lit) + plant(89),
  gym: (o) =>
    // 跑步机、哑铃架、沙袋、瑜伽垫、镜子
    R(4, 26, 26, 3, 'var(--px-frame)') + R(4, 29, 2, 3, 'var(--px-frame)') + R(28, 29, 2, 3, 'var(--px-frame)') + R(26, 12, 2, 14, 'var(--px-frame)') + R(23, 10, 8, 3, 'var(--px-accent)') +
    R(36, 22, 18, 2, 'var(--px-frame)') + legs(36, 18, 24, 'var(--px-frame)') + [38, 43, 48].map((x) => R(x, 19, 4, 3, 'var(--px-accent-2)')).join('') +
    R(60, 0, 1, 6, 'var(--px-frame)') + R(57, 6, 7, 14, 'var(--px-accent)') + R(57, 8, 7, 1, 'var(--px-accent-2)') +
    R(36, 30, 24, 2, 'var(--px-leaf-2)') + R(70, 4, 20, 26, 'var(--px-frame)') + R(71, 5, 18, 24, 'var(--px-glass)') + R(74, 8, 2, 8, 'var(--px-cloth)'),
  rooftop: (o) =>
    // 栏杆、水箱、天线、太阳能板、长椅、花盆、串灯（夜里亮）
    R(0, 23, W, 1, 'var(--px-frame)') + Array.from({ length: 13 }, (_, i) => R(i * 8, 23, 1, 9, 'var(--px-frame)')).join('') +
    R(4, 6, 12, 18, 'var(--px-frame)') + R(4, 4, 12, 2, 'var(--px-wood-2)') + R(6, 24, 2, 8, 'var(--px-wood-2)') + R(12, 24, 2, 8, 'var(--px-wood-2)') +
    R(22, 0, 1, 24, 'var(--px-frame)') + R(19, 3, 7, 1, 'var(--px-frame)') + R(22, 0, 1, 1, 'var(--px-accent)') +
    [30, 42].map((x) => R(x, 18, 10, 2, 'var(--px-solar)') + R(x + 1, 20, 8, 4, 'var(--px-solar-2)') + R(x + 4, 24, 2, 8, 'var(--px-frame)')).join('') +
    R(58, 24, 18, 2, 'var(--px-wood)') + R(58, 20, 18, 2, 'var(--px-wood)') + legs(58, 18, 26, 'var(--px-frame)') +
    R(80, 26, 10, 6, 'var(--px-wood)') + R(81, 20, 8, 6, 'var(--px-leaf)') + R(83, 17, 4, 3, 'var(--px-flower)') +
    R(52, 8, 44, 1, 'var(--px-frame)') + Array.from({ length: 10 }, (_, i) => R(54 + i * 4, 9, 2, 2, o.night ? 'var(--px-glow)' : 'var(--px-cloth-2)')).join(''),
};

// 睡觉：头靠在宿舍床的枕头上，盖着被子（直接画在 SVG 里，和床对齐）；可以点击 / 用键盘选中
function sleeper(r, m, label, pressed) {
  const color = m?.theme_color || 'var(--brass)';
  const name = m?.display || r.name;
  const head = m?.images?.avatar_64 || m?.images?.avatar_128;
  const face = head
    ? `<clipPath id="hc-${esc(r.id)}"><circle cx="11" cy="16" r="4.5"/></clipPath><image href="${esc(head)}" x="6.5" y="11.5" width="9" height="9" clip-path="url(#hc-${esc(r.id)})" preserveAspectRatio="xMidYMid slice"/>`
    : `<circle cx="11" cy="16" r="4.5" fill="${esc(color)}"/><text x="11" y="17.6" font-size="4.5" text-anchor="middle" fill="#fff" font-weight="700">${esc(name.slice(0, 1))}</text>`;
  return `<g class="tenant sleeper act-sleep${pressed ? ' on' : ''}" data-r="${esc(r.id)}" role="button" tabindex="0" aria-pressed="${pressed}" aria-label="${esc(label)}"><title>${esc(label)}</title>${face}<rect x="14" y="17" width="26" height="5" fill="${esc(color)}"/><rect x="14" y="17" width="26" height="1" fill="var(--px-cloth-2)"/><text class="zz" x="17" y="9" font-size="5" fill="var(--ink)">z<tspan dx="1" dy="-2" font-size="4">z</tspan></text></g>`;
}

const svg = (kind, o, extra = '') =>
  `<svg class="furni" viewBox="0 0 ${W} ${H}" shape-rendering="crispEdges" preserveAspectRatio="xMidYMax meet" aria-hidden="${extra ? 'false' : 'true'}">${(FURNITURE[kind] || (() => ''))(o)}${extra}</svg>`;

/** 白天 / 黄昏 / 夜里（决定天空和窗户灯） */
export function phase(hour) {
  if (hour >= 7 && hour < 17) return 'day';
  if (hour >= 17 && hour < 19) return 'dusk';
  if (hour >= 5 && hour < 7) return 'dawn';
  return 'night';
}

/** 住户现在在哪儿、干什么的一句话 */
export function whereText(r, dormName) {
  // 中文与数字之间留一个空格：在 01 号房看书
  const at = r.room === r.dorm ? `在${/^\d/.test(dormName || '') ? ' ' : ''}${dormName || '宿舍'}` : ROOM_VERB[r.room] || '';
  return `${at}${r.activity_label}`;
}

export class Apartment {
  constructor(root, { api, now = () => new Date(), openSession = null, chatWith = null } = {}) {
    this.root = root;
    this.api = api;
    this.now = now;
    this.openSession = openSession;
    this.chatWith = chatWith;
    this.art = null;
    this.data = null;
    this.selected = null;
    this.timer = null;
    this.error = '';
    root.addEventListener('click', (e) => this.onClick(e));
    root.addEventListener('keydown', (e) => this.onKey(e));
  }

  setArt(art) {
    this.art = art && art.available ? art : null;
    this.cut = new Map();
    // 小人素材的浅色底在浏览器里抠掉（一次），抠好后换上
    for (const m of this.art?.members || []) {
      const src = m.images?.chibi;
      if (!src) continue;
      cutout(src).then((out) => {
        if (out === src || this.art?.members?.indexOf(m) < 0) return;
        this.cut.set(src, out);
        clearTimeout(this.cutTimer);
        this.cutTimer = setTimeout(() => this.data && !this.root.hidden && this.render(), 50);
      });
    }
    if (this.data) this.render();
  }

  async show() {
    this.root.hidden = false;
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
    const t = this.now();
    // 后端按周一 = 0；JS 的 getDay() 周日 = 0
    const weekday = (t.getDay() + 6) % 7;
    try {
      this.data = await this.api(t.getHours(), weekday);
      this.error = '';
    } catch (e) {
      this.error = e.message || '加载失败';
    }
    this.hour = t.getHours();
    this.clock = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
    this.day = `${t.getMonth() + 1} 月 ${t.getDate()} 日 · 周${WEEK[weekday]}`;
    this.render();
  }

  look(r) {
    const memberId = this.art?.models?.[r.model_ids[0]];
    return (this.art?.members || []).find((m) => m.id === memberId) || null;
  }

  // i：在这个房间里的第几位；n：房间里一共几位。均匀排开，各自的走动节奏按住户序号错开
  tenant(r, i, n) {
    const m = this.look(r);
    const color = m?.theme_color || 'var(--brass)';
    const name = m?.display || r.name;
    const img = m?.images?.chibi
      ? `<img src="${esc(this.cut?.get(m.images.chibi) || m.images.chibi)}" alt="" loading="lazy" draggable="false">`
      : `<span class="ini" style="background:${esc(color)}">${esc(name.slice(0, 1))}</span>`;
    const dorms = this.dormNames();
    const label = `${name}（${ROLE[r.role] || r.role}）：${whereText(r, dorms[r.dorm])}`;
    const x = n <= 1 ? 50 : 10 + (80 * i) / (n - 1);
    const k = (this.data?.residents || []).indexOf(r);
    const pressed = this.selected === r.id;
    return `<button type="button" class="tenant act-${esc(r.activity)}${pressed ? ' on' : ''}" data-r="${esc(r.id)}" aria-pressed="${pressed}" aria-label="${esc(label)}" title="${esc(label)}" style="--x:${x.toFixed(1)}%;--d:${6 + (k % 4) * 1.7}s;--c:${esc(color)}">${img}${BUBBLES[r.activity] ? `<i class="bub">${BUBBLES[r.activity]}</i>` : ''}<b class="tag">${esc(name)}</b></button>`;
  }

  dormNames() {
    const map = {};
    for (const floor of this.data?.floors || []) for (const room of floor) if (room.kind === 'dorm') map[room.id] = room.name;
    return map;
  }

  label(r) {
    const m = this.look(r);
    const dorms = this.dormNames();
    return `${m?.display || r.name}（${ROLE[r.role] || r.role}）：${whereText(r, dorms[r.dorm])}`;
  }

  room(room, people, lit, night) {
    const d = this.data;
    const owner = room.kind === 'dorm' ? (d.residents || []).find((r) => r.dorm === room.id) : null;
    const title = owner ? `${room.name} · ${this.look(owner)?.display || owner.name}` : room.name;
    const busy = people.some((p) => p.activity === 'work' || p.activity === 'wait');
    let sign = '';
    if (room.kind === 'meeting' && d.hidden_meetings) sign = `<span class="sign" title="匿名会议不显示是谁">匿名会议进行中 ×${d.hidden_meetings}</span>`;
    // 在自己宿舍睡觉的画在床上，其余的小人站在地板上
    const asleep = people.filter((p) => p.activity === 'sleep' && p.room === p.dorm);
    const awake = people.filter((p) => !asleep.includes(p));
    const beds = asleep.map((p) => sleeper(p, this.look(p), this.label(p), this.selected === p.id)).join('');
    const tenants = awake.map((p, i) => this.tenant(p, i, awake.length)).join('');
    const accent = (owner && this.look(owner)?.theme_color) || 'var(--px-accent)';
    // 夜里没人的公共房间熄灯；有人在、或宿舍有人醒着就开灯
    const dark = night && !people.length && room.kind !== 'rooftop';
    const on = (lit && people.length > asleep.length) || busy;
    const cls = `room k-${esc(room.kind)}${busy ? ' busy' : ''}${dark ? ' dark' : ''}${asleep.length && !awake.length ? ' asleep' : ''}`;
    return `<div class="${cls}" data-room="${esc(room.id)}"><span class="rname">${esc(title)}</span>${sign}${svg(room.kind, { lit: on, night, accent: esc(accent) }, beds)}<div class="people${awake.length >= 5 ? ' crowd' : ''}">${tenants}</div></div>`;
  }

  side() {
    const d = this.data;
    const dorms = this.dormNames();
    const sel = (d.residents || []).find((r) => r.id === this.selected);
    let h = `<div class="clock"><span class="big">${esc(this.clock)}</span><span class="day">${esc(this.day || '')}</span><span class="hint">按你电脑的时间作息，30 秒刷新一次</span></div>`;
    if (sel) {
      const m = this.look(sel);
      h += `<div class="card" id="resident-card"><div class="who">${m?.images?.avatar_128 ? `<img src="${esc(m.images.avatar_128)}" alt="">` : ''}<div><b>${esc(m?.display || sel.name)}</b><div class="hint">${esc(ROLE[sel.role] || sel.role)} · 住 ${esc(dorms[sel.dorm] || '')}</div></div></div>`;
      h += `<p>${esc(whereText(sel, dorms[sel.dorm]))}</p>`;
      h += `<p class="hint">本月工资 $${Number(sel.month_paid_usd || 0).toFixed(4)} · ${sel.model_ids.length} 种工作模式</p>`;
      if (sel.session_id) h += `<button type="button" class="btn" data-open="${esc(sel.session_id)}">去看 TA 在办的事</button> `;
      if (this.chatWith) h += `<button type="button" class="btn" data-chat="${esc(sel.name)}">在群聊里 @ TA</button>`;
      h += '</div>';
    }
    h += '<ul class="roster">';
    for (const r of d.residents || []) {
      const m = this.look(r);
      const av = m?.images?.avatar_64
        ? `<img class="av" src="${esc(m.images.avatar_64)}" alt="">`
        : `<span class="av ini" style="background:${esc(m?.theme_color || 'var(--c-coord)')}">${esc((m?.display || r.name).slice(0, 1))}</span>`;
      h += `<li><button type="button" class="link${this.selected === r.id ? ' on' : ''}" data-r="${esc(r.id)}">${av}<span class="tx"><span class="nm"><b>${esc(m?.display || r.name)}</b>${r.role === 'manager' ? '<span class="role">经理</span>' : ''}</span><span class="hint">${esc(whereText(r, dorms[r.dorm]))}</span></span></button></li>`;
    }
    h += '</ul><p class="hint foot">住户就是办公室的员工，同一位的全力和节电两种模式住一间。这里只是看看，不调用模型，也不花钱。</p>';
    return h;
  }

  render() {
    if (!this.data) {
      this.root.innerHTML = `<p class="empty">${esc(this.error || '加载中…')}</p>`;
      return;
    }
    const d = this.data;
    const p = phase(this.hour ?? 12);
    const lit = p === 'night' || p === 'dusk' || p === 'dawn';
    const night = p === 'night' || p === 'dawn';
    const where = new Map();
    for (const r of d.residents || []) {
      const list = where.get(r.room) || [];
      list.push(r);
      where.set(r.room, list);
    }
    let floors = '';
    for (const floor of d.floors || []) {
      floors += `<div class="floor${floor[0]?.kind === 'rooftop' ? ' roof' : ''}">${floor.map((room) => this.room(room, where.get(room.id) || [], lit, night)).join('')}</div>`;
    }
    const err = this.error ? `<p class="hint badp" role="alert">${esc(this.error)}</p>` : '';
    this.root.innerHTML = `<div class="sky ph-${p}"><div class="celestial" aria-hidden="true"></div><div class="skyline" aria-hidden="true"></div><div class="bldg" aria-label="公寓楼" style="--floors:${(d.floors || []).length}">${floors}<div class="ground"></div></div></div><aside class="home-side" aria-label="住户">${err}${this.side()}</aside>`;
  }

  onClick(e) {
    const open = e.target.closest('[data-open]');
    if (open && this.openSession) return this.openSession(open.dataset.open);
    const talk = e.target.closest('[data-chat]');
    if (talk && this.chatWith) return this.chatWith(talk.dataset.chat);
    const r = e.target.closest('[data-r]');
    if (!r) return;
    this.selected = this.selected === r.dataset.r ? null : r.dataset.r;
    this.render();
    this.root.querySelector(`.tenant[data-r="${CSS.escape(r.dataset.r)}"]`)?.focus({ preventScroll: true });
  }

  onKey(e) {
    // 床上的住户是 SVG 元素，不是按钮：Enter / 空格当作点击
    const g = e.target.closest?.('g[data-r]');
    if (g && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      g.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      return;
    }
    if (e.key === 'Escape' && this.selected) {
      this.selected = null;
      this.render();
    }
  }
}
