// 公寓楼：员工下班后住的地方。基建式剖面图：深色金属楼体、两侧电梯井、空房是暗槽，每个房间是一幕有自己主题和灯光的场景。
// 住户按作息在各房间活动，正在办事的在办公室 / 会议室；另有俯视「布局图」，点房间放大看详情。
// 数据来自 /api/apartment（按浏览器本地时间）；小人用 /api/members 的 chibi，没有素材时用昵称首字。
// 匿名未揭晓的会话：服务端只给数量，这里只在会议室门上挂牌，不显示是谁。
// 所有图形都在这里用 SVG / CSS 自绘，不用外部美术素材。

import { cutout } from './cutout.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const REFRESH_MS = 30_000;
const VIEW_KEY = 'roundtable.apartment.view';
const SIDE_KEY = 'roundtable.apartment.side';

// 活动 → 头顶的橙色标记（一个字，闲着时不显示）
const BUBBLES = { sleep: 'z', eat: '吃', work: '忙', wait: '等', exercise: '练', chat: '聊', read: '读', stargaze: '望', game: '玩' };
const WEEK = '一二三四五六日';
const ROOM_VERB = { office: '在办公室', meeting: '在会议室', canteen: '在食堂', lounge: '在休息室', gym: '在健身房', rooftop: '在天台' };
const ROLE = { manager: '经理', staff: '员工' };
const ROOM_EN = { dorm: 'DORMITORY', office: 'OFFICE', meeting: 'MEETING ROOM', canteen: 'CANTEEN', lounge: 'LOUNGE', gym: 'GYM', rooftop: 'ROOFTOP' };
// 剖面图里各类房间的宽度比例
const FLEX = { office: 1.5, meeting: 1, canteen: 1.3, lounge: 1, gym: 1 };

// --- SVG 小工具 -------------------------------------------------------------------------------
// 每个房间一张 SVG：地板线在 y=FY；墙和地板画得比 viewBox 宽得多（overflow 可见），房间多宽都铺满。
const H = 120;
const FY = 100;
const VB = { dorm: 300, office: 560, meeting: 400, canteen: 460, lounge: 340, gym: 340, rooftop: 1000 };
const attrs = (o) =>
  Object.entries(o)
    .map(([k, v]) => ` ${k}="${v}"`)
    .join('');
const rect = (x, y, w, h, fill, more = {}) => `<rect${attrs({ x, y, width: w, height: h, fill, ...more })}/>`;
const rnd = (x, y, w, h, rx, fill, more = {}) => rect(x, y, w, h, fill, { rx, ...more });
const circ = (cx, cy, r, fill, more = {}) => `<circle${attrs({ cx, cy, r, fill, ...more })}/>`;
const ell = (cx, cy, rx, ry, fill, more = {}) => `<ellipse${attrs({ cx, cy, rx, ry, fill, ...more })}/>`;
const path = (d, fill, more = {}) => `<path${attrs({ d, fill, ...more })}/>`;
const line = (x1, y1, x2, y2, stroke, w = 1, more = {}) => `<line${attrs({ x1, y1, x2, y2, stroke, 'stroke-width': w, ...more })}/>`;
const dark = (x, y, w, h, a = 0.2) => rect(x, y, w, h, '#000', { opacity: a });
const glint = (x, y, w, h, a = 0.22) => rect(x, y, w, h, '#fff', { opacity: a });

// 窗外的天：按时段
const SKY = {
  day: ['#8ccbec', '#d4eef8', '#9dbfd2'],
  dawn: ['#6f7cb4', '#f2b994', '#7d7f9e'],
  dusk: ['#565c98', '#f19d74', '#6f5f7e'],
  night: ['#0c1330', '#26305a', '#161d3c'],
};

/** 房间的背景：墙（上下两段 + 竖向护墙板）和地板（木纹 / 地胶）。 */
function shell(w, wall, wall2, floor, { panels = 0, skirting = '#00000033', boards = true } = {}) {
  let s = rect(-800, -400, w + 1600, FY + 400, wall) + rect(-800, FY - 30, w + 1600, 30, wall2);
  for (let x = -800 + (panels || 1); panels && x < w + 800; x += panels) s += rect(x, -400, 1, FY + 400, '#000', { opacity: 0.08 });
  s += rect(-800, FY - 31, w + 1600, 1.5, '#fff', { opacity: 0.08 });
  s += rect(-800, FY, w + 1600, 80, floor) + rect(-800, FY, w + 1600, 2, skirting);
  if (boards) for (let x = -800; x < w + 800; x += 28) s += rect(x, FY + 2, 1, 80, '#000', { opacity: 0.1 });
  return s;
}

function windowPane(x, y, w, h, o, curtain = null) {
  const [s0, s1, far] = SKY[o.ph] || SKY.day;
  let s = rnd(x - 3, y - 3, w + 6, h + 6, 2, '#2b3038');
  s += rect(x, y, w, h * 0.55, s0) + rect(x, y + h * 0.55, w, h * 0.45, s1);
  if (o.night) s += circ(x + w * 0.72, y + h * 0.26, 3.2, '#f4e7a8') + circ(x + w * 0.2, y + h * 0.18, 0.7, '#fff') + circ(x + w * 0.45, y + h * 0.1, 0.6, '#fff');
  else s += ell(x + w * 0.3, y + h * 0.22, w * 0.14, 2.2, '#fff', { opacity: 0.7 });
  // 远处的楼
  for (let i = 0, bx = x; bx < x + w; i++) {
    const bw = 6 + ((i * 7) % 9);
    const bh = 6 + ((i * 13) % 14);
    s += rect(bx, y + h - bh, Math.min(bw, x + w - bx), bh, far, { opacity: 0.85 });
    if (o.night && bh > 10) s += rect(bx + 2, y + h - bh + 3, 1.2, 1.2, '#ffd774');
    bx += bw + 1;
  }
  s += path(`M${x + w * 0.15} ${y + h}L${x + w * 0.55} ${y}H${x + w * 0.7}L${x + w * 0.3} ${y + h}Z`, '#fff', { opacity: 0.1 });
  s += rect(x + w / 2 - 1, y, 2, h, '#2b3038') + rect(x, y + h * 0.5 - 1, w, 2, '#2b3038');
  s += rnd(x - 6, y + h + 3, w + 12, 3, 1, '#3a3f47');
  if (curtain) {
    s += rect(x - 12, y - 8, w + 24, 2, '#3a3f47');
    for (const cx of [x - 12, x + w + 2]) s += path(`M${cx} ${y - 6}h10l2 ${h + 12}h-14z`, curtain) + dark(cx + 3, y - 6, 1.5, h + 12, 0.18) + dark(cx + 7, y - 6, 1.5, h + 12, 0.12);
  }
  return s;
}

// 天花板上的灯：开着时往下打一道光
function ceilingLamp(x, on, { y = 12, cone = 70, warm = '#fff1cf' } = {}) {
  let s = line(x, -60, x, y - 4, '#20242b', 0.8) + path(`M${x - 9} ${y}L${x - 5} ${y - 5}H${x + 5}L${x + 9} ${y}Z`, '#2a2f37');
  if (on) s += path(`M${x - 8} ${y}L${x - cone / 2} ${FY}H${x + cone / 2}L${x + 8} ${y}Z`, warm, { opacity: 0.1 }) + ell(x, y + 1, 7, 2, warm) + ell(x, FY + 3, cone / 2.4, 3, warm, { opacity: 0.18 });
  else s += ell(x, y + 1, 7, 1.5, '#6b6f76');
  return s;
}
function ledStrip(x, w, color, on) {
  return rect(x, 4, w, 2, on ? color : '#3b4049') + (on ? rect(x, 6, w, 14, color, { opacity: 0.08 }) : '');
}
function shelf(x, y, w, tc) {
  const cols = [tc, '#c9b48a', '#5d7a8c', '#8c4a3c', '#e3dccb', '#3f5b46', '#b36b3c'];
  let s = rect(x, y, w, 2.5, '#4a3628') + dark(x, y + 2.5, w, 1.2, 0.25);
  let bx = x + 3;
  for (let i = 0; bx < x + w - 6; i++) {
    const bw = 3 + (i % 3);
    const bh = 9 + ((i * 5) % 6);
    if (i % 6 === 4) {
      s += path(`M${bx} ${y}l${bh * 0.5} -${bh}h${bw}l-${bh * 0.5} ${bh}z`, cols[i % cols.length]);
      bx += bw + 6;
      continue;
    }
    s += rect(bx, y - bh, bw, bh, cols[i % cols.length]) + glint(bx, y - bh, 0.8, bh, 0.25) + rect(bx, y - bh + 2, bw, 0.8, '#fff', { opacity: 0.2 });
    bx += bw + 0.6;
  }
  return s;
}
function plant(x, s = 1, pot = '#c97a4a') {
  const k = (n) => n * s;
  let o = path(`M${x - k(7)} ${FY - k(14)}h${k(14)}l-${k(2)} ${k(14)}h-${k(10)}z`, pot) + dark(x, FY - k(14), k(7), k(14), 0.15);
  o += ell(x - k(6), FY - k(24), k(5), k(11), '#2f6a43', { transform: `rotate(-25 ${x - k(6)} ${FY - k(24)})` });
  o += ell(x + k(6), FY - k(26), k(5), k(12), '#3f7f4f', { transform: `rotate(28 ${x + k(6)} ${FY - k(26)})` });
  o += ell(x, FY - k(30), k(5), k(14), '#5a9e5e') + ell(x - k(1), FY - k(34), k(1.5), k(7), '#7fbf7a', { opacity: 0.6 });
  return o;
}
const picture = (x, y, w, h, c, mat = '#e9e2d3') => rect(x - 2, y - 2, w + 4, h + 4, '#3a2b20') + rect(x, y, w, h, mat) + rect(x + 3, y + 3, w - 6, h - 6, c) + glint(x + 3, y + 3, w - 6, 2, 0.2);
const rug = (cx, w, c) => ell(cx, FY + 5, w / 2, 4.5, c, { opacity: 0.9 }) + ell(cx, FY + 5, w / 2 - 5, 2.6, '#fff', { opacity: 0.1 });
function desk(x, w, top = '#6b4b35') {
  return rnd(x, FY - 26, w, 4, 1, top) + glint(x, FY - 26, w, 1, 0.2) + rect(x + 3, FY - 22, 3, 22, '#3c2b20') + rect(x + w - 6, FY - 22, 3, 22, '#3c2b20') + rnd(x + w - 28, FY - 22, 22, 11, 1, '#58402e') + rect(x + w - 19, FY - 17, 4, 1, '#c9b48a');
}
function deskLamp(x, on) {
  let s = rect(x - 4, FY - 28, 8, 2, '#2a2f37') + line(x, FY - 28, x + 4, FY - 40, '#2a2f37', 1.4) + line(x + 4, FY - 40, x - 3, FY - 46, '#2a2f37', 1.4) + path(`M${x - 9} ${FY - 42}l6 -7 6 4z`, '#c7a24d');
  if (on) s += path(`M${x - 9} ${FY - 42}l-8 14h24l-10 -10z`, '#ffe4a3', { opacity: 0.22 }) + circ(x - 5, FY - 41, 2, '#fff1c4');
  return s;
}
function monitor(x, y, w, h, on, glow = '#7fe3ff') {
  let s = rnd(x, y, w, h, 1.5, '#15191f') + rect(x + 2, y + 2, w - 4, h - 4, on ? '#0f2a35' : '#262d36');
  if (on) {
    s += rect(x + 2, y + 2, w - 4, h - 4, glow, { opacity: 0.18 });
    for (let i = 0; i < 3; i++) s += rect(x + 4, y + 4 + i * 3, (w - 8) * (0.4 + ((i * 3) % 5) / 10), 1, glow, { opacity: 0.8 });
    s += rect(x - 6, y - 4, w + 12, h + 8, glow, { opacity: 0.05 });
  }
  return s + rect(x + w / 2 - 1.5, y + h, 3, 4, '#15191f') + rect(x + w / 2 - 6, y + h + 4, 12, 1.5, '#15191f');
}
function chair(x, c = '#2f3540', flip = false) {
  const bx = flip ? x : x + 12;
  return rnd(x, FY - 16, 16, 3, 1, c) + rnd(bx, FY - 34, 4, 19, 1.5, c) + rect(x + 7, FY - 13, 2, 9, '#1b1f25') + rect(x + 2, FY - 4, 12, 1.5, '#1b1f25');
}
function sofa(x, w, c) {
  return (
    rnd(x, FY - 36, w, 16, 4, c) +
    dark(x, FY - 22, w, 2, 0.15) +
    rnd(x - 4, FY - 26, 10, 22, 3, c) +
    rnd(x + w - 6, FY - 26, 10, 22, 3, c) +
    rnd(x + 4, FY - 22, w - 8, 12, 2, c) +
    glint(x + 4, FY - 22, w - 8, 1.5, 0.25) +
    line(x + w / 2, FY - 22, x + w / 2, FY - 10, '#000', 0.6, { opacity: 0.25 }) +
    rnd(x + 6, FY - 33, 12, 9, 3, '#efe7d6', { transform: `rotate(-8 ${x + 12} ${FY - 28})` }) +
    rect(x + 2, FY - 4, 3, 4, '#2a1f18') +
    rect(x + w - 5, FY - 4, 3, 4, '#2a1f18')
  );
}
function clock(x, y) {
  return circ(x, y, 6, '#e9e4da') + circ(x, y, 6, 'none', { stroke: '#2a2f37', 'stroke-width': 1.2 }) + line(x, y, x, y - 4, '#2a2f37', 0.9) + line(x, y, x + 3, y, '#2a2f37', 0.9);
}

// 宿舍的床：头靠在左边枕头上；睡觉的住户画在这个位置（sleeper）
const BED_X = 20;
function bed(x, tc) {
  return (
    rnd(x, FY - 42, 8, 42, 2, '#4a3426') +
    glint(x + 1, FY - 41, 1, 40, 0.15) +
    rect(x, FY - 16, 112, 10, '#5a4030') +
    dark(x, FY - 8, 112, 2, 0.25) +
    rect(x + 4, FY - 6, 4, 6, '#3c2b20') +
    rect(x + 102, FY - 6, 4, 6, '#3c2b20') +
    rnd(x + 6, FY - 24, 104, 9, 3, '#f2efe8') +
    rnd(x + 10, FY - 30, 22, 9, 4, '#ffffff') +
    dark(x + 10, FY - 23, 22, 2, 0.08) +
    rnd(x + 34, FY - 26, 76, 12, 3, tc) +
    glint(x + 34, FY - 26, 76, 2, 0.28) +
    dark(x + 34, FY - 16, 76, 2, 0.2) +
    path(`M${x + 70} ${FY - 26}q4 6 0 12`, 'none', { stroke: '#000', 'stroke-opacity': 0.15, 'stroke-width': 1 }) +
    rnd(x + 108, FY - 30, 6, 30, 2, '#4a3426')
  );
}

// 宿舍四种陈设（按房号轮换），颜色都跟房主的主题色走
const DORM_STYLES = [
  // 书房：深绿墙、两层书架、挂画、书桌台灯
  {
    wall: ['#2f3f37', '#27352e', '#5b4636'],
    draw: (o) =>
      windowPane(150, 18, 50, 46, o, o.tc) +
      shelf(212, 30, 60, o.tc) +
      shelf(212, 50, 60, o.tc) +
      picture(60, 22, 18, 22, '#7f9a7c') +
      picture(84, 28, 12, 14, o.tc) +
      desk(212, 64) +
      deskLamp(262, o.on) +
      chair(226, '#3a2b20') +
      plant(286, 0.9) +
      rug(80, 110, '#8a6a4a') +
      ceilingLamp(170, o.on),
  },
  // 阁楼：浅色墙、大落地窗、绿植很多
  {
    wall: ['#e8e3d9', '#dcd4c6', '#b89a7a'],
    draw: (o) =>
      windowPane(140, 12, 90, 58, o) +
      rect(136, 8, 98, 3, '#c8beae') +
      plant(250, 1.15, '#e3ddd2') +
      plant(132, 0.8, '#e3ddd2') +
      shelf(30, 30, 50, o.tc) +
      desk(232, 56, '#cdbba3') +
      monitor(246, 50, 26, 18, o.on, '#9fe0ff') +
      rug(70, 120, o.tc) +
      ceilingLamp(185, o.on, { warm: '#fff7e6' }),
  },
  // 机房风：深蓝墙、灯带、两块屏、手办架
  {
    wall: ['#1c2433', '#171e2b', '#2b3442'],
    draw: (o) =>
      ledStrip(-50, 400, o.tc, true) +
      windowPane(160, 22, 40, 36, o) +
      desk(208, 80, '#2b3442') +
      monitor(214, 46, 30, 22, o.on, o.tc) +
      monitor(248, 44, 34, 24, o.on, '#7fe3ff') +
      chair(232, '#11151b') +
      rnd(30, 22, 44, 30, 1, '#11151b') +
      [0, 1, 2].map((i) => rect(34 + i * 14, 30, 8, 12, [o.tc, '#e3dccb', '#7fe3ff'][i]) + circ(38 + i * 14, 28, 3, '#e3dccb')).join('') +
      rect(30, 43, 44, 2, '#3b4554') +
      plant(140, 0.7, '#3b4554') +
      rug(80, 100, '#2b3442'),
  },
  // 木屋：木墙板、落地灯、吉他、厚地毯
  {
    wall: ['#5c4636', '#4c392c', '#7a5a40'],
    draw: (o) =>
      windowPane(150, 20, 46, 42, o, '#d9c6a5') +
      picture(60, 24, 30, 20, o.tc) +
      shelf(214, 34, 58, o.tc) +
      rnd(226, FY - 34, 40, 34, 2, '#3c2b20') +
      glint(226, FY - 34, 40, 1.5, 0.2) +
      rect(231, FY - 28, 30, 1, '#c9b48a') +
      rect(231, FY - 16, 30, 1, '#c9b48a') +
      line(284, FY - 2, 284, FY - 70, '#2a2f37', 1.4) +
      path(`M274 ${FY - 70}h20l-4 -12h-12z`, o.on ? '#ffe2a0' : '#d9c6a5') +
      (o.on ? path(`M276 ${FY - 70}l-12 70h40l-12 -70z`, '#ffe2a0', { opacity: 0.12 }) : '') +
      ell(130, FY - 18, 7, 10, '#b5793f') +
      ell(130, FY - 30, 5, 7, '#b5793f') +
      rect(129, FY - 66, 2, 34, '#3c2b20') +
      circ(130, FY - 22, 2.2, '#3c2b20') +
      rug(80, 130, '#8c3a2e'),
  },
];

const ROOMS = {
  dorm: (o) => {
    const st = DORM_STYLES[o.style % DORM_STYLES.length];
    return shell(VB.dorm, `color-mix(in srgb, ${o.tc} 10%, ${st.wall[0]})`, st.wall[1], st.wall[2], { panels: o.style === 3 ? 18 : 0 }) + st.draw(o) + bed(BED_X, o.tc);
  },
  office: (o) => {
    const w = VB.office;
    const cy = '#7fe3ff';
    let s = shell(w, '#1a222d', '#151c25', '#232b36', { panels: 40, boards: false });
    s += rect(-800, FY + 6, w + 1600, 1, cy, { opacity: 0.12 });
    // 中间一整面屏幕墙
    s += rnd(170, 10, 220, 58, 2, '#0b1016') + rect(174, 14, 212, 50, o.on ? '#0d2633' : '#1c242e');
    if (o.on) {
      s += path('M182 52l14 -8 12 4 16 -16 14 10 14 -6 16 -12', 'none', { stroke: cy, 'stroke-width': 1.2 });
      s += circ(316, 38, 15, 'none', { stroke: cy, 'stroke-opacity': 0.7 }) + circ(316, 38, 9, 'none', { stroke: cy, 'stroke-opacity': 0.4 }) + line(316, 38, 328, 30, cy, 1, { class: 'sweep' });
      for (let i = 0; i < 5; i++) s += rect(344, 20 + i * 8, 34 - ((i * 9) % 20), 3, i === 2 ? '#ffb15c' : cy, { opacity: 0.7 });
      s += rect(170, 8, 220, 64, cy, { opacity: 0.05 });
    }
    s += rect(176, 15, 70, 1, '#fff', { opacity: 0.08 });
    // 两侧小屏、服务器机柜
    for (const x of [130, 404]) s += monitor(x, 18, 24, 16, o.on, cy) + monitor(x, 40, 24, 16, o.on, x > 200 ? '#ffb15c' : cy);
    for (const x of [16, 52]) {
      s += rnd(x, FY - 74, 30, 74, 1, '#10151b') + rect(x + 2, FY - 72, 26, 70, '#161d26');
      for (let i = 0; i < 8; i++) s += rect(x + 4, FY - 68 + i * 8, 22, 5, '#1f2833') + circ(x + 23, FY - 65.5 + i * 8, 1, i % 3 ? '#4cd18a' : '#ffb15c', { class: o.on ? 'blink' : '' });
    }
    // 前排三个工位
    for (const x of [136, 266, 396]) s += desk(x, 70, '#2b3442') + monitor(x + 10, FY - 44, 24, 16, o.on, cy) + monitor(x + 38, FY - 44, 24, 16, o.on, cy) + chair(x + 26, '#0f1318');
    s += plant(530, 1, '#3b4554') + clock(110, 20);
    return s + ceilingLamp(100, o.on, { warm: '#e4f6ff', cone: 60 }) + ceilingLamp(470, o.on, { warm: '#e4f6ff', cone: 60 });
  },
  meeting: (o) => {
    const w = VB.meeting;
    let s = shell(w, '#d5dbe1', '#c6cdd5', '#8a8f96', { panels: 50, boards: false });
    // 玻璃隔断
    for (const x of [8, 380]) s += rect(x, 6, 12, FY - 6, '#a9c4d4', { opacity: 0.45 }) + rect(x, 6, 1.5, FY - 6, '#6b7a86');
    // 投影幕：柱状图
    s += rect(40, 8, 120, 3, '#2a2f37') + rect(44, 11, 112, 62, '#f7f8fa') + dark(44, 71, 112, 2, 0.1);
    [18, 30, 24, 40, 34].forEach((h, i) => (s += rect(58 + i * 18, 64 - h, 10, h, i === 3 ? '#e8762b' : '#5b7fb8')));
    s += rect(54, 64, 96, 1, '#2a2f37') + rect(52, 16, 40, 2, '#2a2f37', { opacity: 0.6 });
    s += windowPane(296, 16, 64, 48, o);
    // 长桌和椅背
    for (const x of [130, 170, 210, 250, 290]) s += rnd(x, FY - 46, 14, 22, 3, '#2f3540') + rect(x + 6, FY - 24, 2, 10, '#1b1f25');
    s += rnd(110, FY - 28, 210, 6, 2, '#7a5236') + glint(110, FY - 28, 210, 1.5, 0.25) + rect(124, FY - 22, 5, 22, '#3c2b20') + rect(300, FY - 22, 5, 22, '#3c2b20');
    for (const x of [140, 200, 268]) s += rect(x, FY - 31, 10, 3, '#f2efe8') + rect(x + 16, FY - 32, 3, 4, '#c9b48a');
    for (const x of [160, 240]) s += ceilingLamp(x, o.on, { warm: '#fffaf0', cone: 90 });
    return s + plant(360, 0.9, '#e3ddd2');
  },
  canteen: (o) => {
    const w = VB.canteen;
    let s = shell(w, '#6a4532', '#e8dccb', '#9b6b47');
    // 白瓷砖墙裙
    for (let x = -800; x < w + 800; x += 10) s += rect(x, FY - 30, 0.6, 30, '#000', { opacity: 0.08 });
    for (let y = FY - 20; y < FY; y += 10) s += rect(-800, y, w + 1600, 0.6, '#000', { opacity: 0.08 });
    // 灶台、油烟机、热气
    s += rect(20, 18, 70, 12, '#3a3f47') + path('M30 30h50l8 10h-66z', '#4a5058');
    s += rnd(14, FY - 38, 120, 38, 1, '#3a3f47') + rect(14, FY - 40, 120, 4, '#c9cfd6') + rect(28, FY - 46, 22, 6, '#2a2f37') + rect(60, FY - 44, 18, 4, '#7a5236');
    if (!o.night) for (const x of [36, 44]) s += path(`M${x} ${FY - 50}q-4 -6 0 -12t0 -12`, 'none', { stroke: '#fff', 'stroke-opacity': 0.55, 'stroke-width': 1.2, class: 'steam' });
    for (let i = 0; i < 4; i++) s += rect(24 + i * 26, FY - 30, 20, 24, '#30353c');
    // 菜单黑板、冰箱
    s += rnd(150, 14, 64, 40, 2, '#5a3e2c') + rect(153, 17, 58, 34, '#26332d');
    for (let i = 0; i < 4; i++) s += rect(158, 23 + i * 7, 26 + ((i * 11) % 18), 1.5, '#e9e4da', { opacity: 0.8 }) + rect(198, 23 + i * 7, 8, 1.5, '#ffd774', { opacity: 0.8 });
    s += rnd(222, FY - 72, 34, 72, 2, '#e9ecef') + rect(222, FY - 46, 34, 1, '#9aa1a8') + rect(250, FY - 64, 2, 12, '#9aa1a8') + rect(250, FY - 40, 2, 16, '#9aa1a8') + dark(246, FY - 72, 10, 72, 0.06);
    // 两张餐桌带凳子
    for (const x of [280, 370]) {
      s += rnd(x, FY - 26, 66, 5, 1, '#c88a52') + glint(x, FY - 26, 66, 1.2, 0.3) + rect(x + 30, FY - 21, 6, 21, '#4a3426') + rect(x + 20, FY - 1, 26, 1.5, '#4a3426');
      for (const sx of [x - 12, x + 68]) s += rnd(sx, FY - 15, 12, 3, 1.5, '#e8762b') + rect(sx + 5, FY - 12, 2, 12, '#3a3f47');
      s += ell(x + 16, FY - 27, 6, 1.6, '#f2efe8') + ell(x + 48, FY - 27, 6, 1.6, '#f2efe8');
    }
    for (const x of [313, 403, 180]) s += ceilingLamp(x, o.on, { warm: '#ffdca0', cone: 64 });
    return s;
  },
  lounge: (o) => {
    const w = VB.lounge;
    let s = shell(w, '#3a2230', '#2f1b27', '#5a3424', { panels: 22 });
    s += rect(-800, FY + 2, w + 1600, 80, '#7a2a2a', { opacity: 0.55 });
    // 壁炉
    s += rnd(30, FY - 56, 70, 56, 1, '#6e6258') + rect(26, FY - 60, 78, 6, '#8a7d70') + rnd(44, FY - 38, 42, 38, 12, '#1d1512');
    s += `<g class="${o.on || o.night ? 'fire' : ''}">${path(`M52 ${FY}q4 -18 13 -26q-2 10 6 14q4 -8 2 -16q12 10 9 28z`, '#ff8a3d')}${path(`M58 ${FY}q3 -10 8 -14q0 8 6 8q2 -4 1 -8q6 8 4 14z`, '#ffd774')}</g>`;
    s += ell(65, FY - 18, 30, 26, '#ff9a4a', { opacity: o.on || o.night ? 0.12 : 0.04 });
    s += picture(48, 10, 34, 20, '#c9a46a', '#3a2b20') + rect(60, FY - 64, 4, 4, '#c9b48a') + rect(70, FY - 66, 3, 6, '#7fbf7a');
    // 沙发、茶几、地毯
    s += rug(170, 170, '#9e3b3b') + sofa(118, 90, '#c9b8a0');
    s += rnd(150, FY - 14, 44, 4, 1, '#3c2b20') + rect(154, FY - 10, 3, 10, '#3c2b20') + rect(186, FY - 10, 3, 10, '#3c2b20') + rect(162, FY - 18, 8, 4, '#f2efe8');
    // 电视柜、书架、落地灯
    s += rnd(234, 22, 50, 30, 1.5, '#0e1116') + rect(237, 25, 44, 24, o.on ? '#2b4a6a' : '#1c2229') + (o.on ? rect(237, 25, 44, 24, '#7fb5ff', { opacity: 0.15 }) : '');
    s += rnd(230, FY - 20, 60, 20, 1, '#2a1f18') + rect(240, FY - 14, 20, 3, '#111');
    s += shelf(296, 30, 38, '#c9a46a') + shelf(296, 52, 38, '#7f9a7c') + rect(296, 52, 38, FY - 52, '#2a1f18', { opacity: 0.6 });
    s += line(110, FY, 110, FY - 70, '#1d1512', 1.4) + path(`M101 ${FY - 70}h18l-4 -12h-10z`, o.on ? '#ffe2a0' : '#c9b8a0');
    if (o.on) s += path(`M103 ${FY - 70}l-16 70h46l-14 -70z`, '#ffe2a0', { opacity: 0.1 });
    return s;
  },
  gym: (o) => {
    const w = VB.gym;
    let s = shell(w, '#7a7f86', '#6b7077', '#3a3f47', { panels: 0, boards: false });
    s += rect(-800, 0, w + 1600, 6, '#2a2f37');
    for (let x = -800; x < w + 800; x += 24) s += rect(x, 6, 0.8, FY - 6, '#000', { opacity: 0.08 });
    s += rect(-800, FY + 2, w + 1600, 80, '#a3302e', { opacity: 0.8 });
    // 镜子、标语条
    s += rnd(210, 10, 100, 56, 1, '#2a2f37') + rect(213, 13, 94, 50, '#b9cbd6') + path('M220 63l30 -50h10l-30 50z', '#fff', { opacity: 0.2 }) + rect(10, 18, 70, 6, '#e8762b');
    // 跑步机
    s += path(`M20 ${FY - 4}h70l-4 -8h-62z`, '#2a2f37') + rect(22, FY - 12, 62, 3, '#11151b') + line(80, FY - 12, 92, FY - 50, '#2a2f37', 3) + rnd(84, FY - 56, 16, 8, 2, '#2a2f37') + rect(87, FY - 54, 10, 4, o.on ? '#7fe3ff' : '#444');
    // 哑铃架
    s += rect(110, FY - 30, 60, 3, '#2a2f37') + rect(112, FY - 27, 3, 27, '#2a2f37') + rect(165, FY - 27, 3, 27, '#2a2f37');
    for (let i = 0; i < 5; i++) s += rnd(116 + i * 10, FY - 36, 8, 6, 2, i % 2 ? '#e8762b' : '#11151b');
    // 卧推凳、杠铃
    s += rnd(178, FY - 20, 40, 6, 2, '#11151b') + rect(196, FY - 14, 4, 14, '#2a2f37') + rect(176, FY - 50, 3, 50, '#2a2f37') + rect(216, FY - 50, 3, 50, '#2a2f37') + rect(166, FY - 48, 64, 2, '#9aa1a8') + rnd(168, FY - 56, 5, 18, 1, '#11151b') + rnd(223, FY - 56, 5, 18, 1, '#11151b');
    // 沙袋
    s += line(296, 6, 296, 26, '#2a2f37', 1.2) + rnd(287, 26, 18, 44, 7, '#a3302e') + rect(287, 34, 18, 2, '#11151b', { opacity: 0.6 }) + glint(290, 30, 2, 36, 0.2);
    s += rnd(320, FY - 14, 6, 14, 2, '#7fe3ff', { opacity: 0.8 }) + plant(250, 0.8, '#3a3f47');
    return s + ledStrip(-100, 540, '#fff4e0', o.on);
  },
  rooftop: (o) => {
    const w = VB.rooftop;
    const ry = 50; // 天台地面
    let s = rect(-800, ry, w + 1600, 40, '#2a2f37') + rect(-800, ry, w + 1600, 2, '#3b424c');
    // 栏杆
    s += rect(-800, ry - 16, w + 1600, 2, '#4a525d');
    for (let x = -800; x < w + 800; x += 14) s += rect(x, ry - 16, 1.4, 16, '#4a525d');
    // 水箱、天线（顶上闪红灯）
    s += rnd(70, ry - 40, 34, 34, 2, '#5d6874') + rect(70, ry - 42, 34, 4, '#47515c') + rect(76, ry - 6, 3, 6, '#3b424c') + rect(95, ry - 6, 3, 6, '#3b424c');
    s += rect(132, ry - 54, 1.6, 54, '#3b424c') + rect(124, ry - 46, 18, 1.2, '#3b424c') + circ(133, ry - 55, 1.8, '#ff4d4d', { class: 'blink' });
    // 太阳能板
    for (const x of [180, 230, 280]) s += path(`M${x} ${ry - 10}l8 -14h36l-8 14z`, '#2f4a7a') + path(`M${x + 4} ${ry - 17}h36`, 'none', { stroke: '#5b7fb8', 'stroke-width': 0.6 }) + rect(x + 16, ry - 10, 2, 10, '#3b424c');
    // 串灯
    s += path(`M420 ${ry - 34}q90 16 180 0t180 0`, 'none', { stroke: '#3b424c', 'stroke-width': 0.8 });
    for (let i = 0; i <= 18; i++) {
      const x = 420 + i * 20;
      const t = (i % 9) / 9;
      const y = ry - 34 + Math.sin(t * Math.PI) * 8;
      s += circ(x, y + 2, 1.8, o.night || o.ph === 'dusk' ? '#ffd774' : '#e9e4da', { class: o.night ? 'twinkle' : '' });
    }
    // 长椅、花箱、望远镜
    s += rnd(520, ry - 12, 60, 3, 1, '#9a6a3a') + rnd(520, ry - 20, 60, 3, 1, '#9a6a3a') + rect(524, ry - 9, 2, 9, '#3b424c') + rect(574, ry - 9, 2, 9, '#3b424c');
    for (const x of [640, 690]) s += rnd(x, ry - 10, 40, 10, 1, '#7a5236') + ell(x + 10, ry - 13, 8, 5, '#3f7f4f') + ell(x + 26, ry - 14, 9, 6, '#5a9e5e') + circ(x + 18, ry - 17, 2, '#e87a9a') + circ(x + 32, ry - 16, 1.6, '#ffd774');
    s += line(820, ry, 830, ry - 18, '#3b424c', 1.2) + line(840, ry, 830, ry - 18, '#3b424c', 1.2) + rnd(820, ry - 26, 26, 6, 2, '#c9cfd6', { transform: `rotate(-24 830 ${ry - 22})` });
    s += rnd(890, ry - 30, 40, 30, 1, '#3b424c') + rect(896, ry - 24, 28, 18, '#2a2f37') + rect(890, ry - 32, 40, 3, '#5d6874');
    return s;
  },
};

function sceneSvg(kind, o, extra = '') {
  const w = VB[kind] || 300;
  const h = kind === 'rooftop' ? 70 : H;
  return `<svg class="scene" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMax meet" aria-hidden="${extra ? 'false' : 'true'}">${(ROOMS[kind] || (() => ''))(o)}${extra}</svg>`;
}

// 细线图标（资源栏、布局图）
const ICONS = {
  coin: '<circle cx="12" cy="12" r="8"/><path d="M12 7v10M9.5 9.5c0-1 1-1.6 2.5-1.6s2.5.7 2.5 1.7-1 1.4-2.5 1.6-2.5.6-2.5 1.7 1 1.7 2.5 1.7 2.5-.6 2.5-1.6"/>',
  cal: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
  badge: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
  people: '<circle cx="9" cy="9" r="3"/><path d="M3.5 19c.6-3 2.8-4.6 5.5-4.6s4.9 1.6 5.5 4.6"/><path d="M15 6.5a3 3 0 0 1 0 5.6M17 14.6c1.8.6 3 2 3.4 4.4"/>',
  zoom: '<circle cx="11" cy="11" r="6"/><path d="M15.5 15.5L20 20M8.5 11h5M11 8.5v5"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  lift: '<rect x="6" y="3" width="12" height="18" rx="1.5"/><path d="M9.5 9.5L12 7l2.5 2.5M9.5 14.5L12 17l2.5-2.5"/>',
  dorm: '<path d="M3 18v-8M3 14h18v4M21 18v-3"/><rect x="5" y="10.5" width="5" height="3.5" rx="1.5"/>',
  office: '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M9 20h6M12 16v4"/>',
  meeting: '<path d="M4 11h16M6 11v7M18 11v7"/><circle cx="8" cy="6.5" r="1.8"/><circle cx="16" cy="6.5" r="1.8"/>',
  canteen: '<path d="M7 3v8a2 2 0 0 0 2 2v8M7 3v5M11 3v5M7 8h4M17 3c-1.6 1-2.5 3-2.5 6s1 3.5 2.5 3.5V21"/>',
  lounge: '<path d="M4 18v-6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v6M4 15h16M6 10V7a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v3"/>',
  gym: '<path d="M3 12h18M6 8v8M18 8v8M3.5 10v4M20.5 10v4"/>',
  rooftop: '<path d="M4 20V10M20 20V10M4 13h16M12 3v7M9 5l3-2 3 2"/>',
};
const icon = (k, cls = 'ic') => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[k] || ''}</svg>`;

// 睡觉：头靠在宿舍床的枕头上，盖着被子（画在 SVG 里，和床对齐）；可以点击 / 用键盘选中
function sleeper(r, m, label, pressed) {
  const color = m?.theme_color || '#c9733f';
  const name = m?.display || r.name;
  const head = m?.images?.avatar_64 || m?.images?.avatar_128;
  const hx = BED_X + 22;
  const hy = FY - 32;
  const hr = 12;
  const face = head
    ? `<clipPath id="hc-${esc(r.id)}"><circle cx="${hx}" cy="${hy}" r="${hr}"/></clipPath><image href="${esc(head)}" x="${hx - hr}" y="${hy - hr}" width="${hr * 2}" height="${hr * 2}" clip-path="url(#hc-${esc(r.id)})" preserveAspectRatio="xMidYMid slice"/>`
    : `<circle cx="${hx}" cy="${hy}" r="${hr}" fill="${esc(color)}"/><text x="${hx}" y="${hy + 4}" font-size="11" text-anchor="middle" fill="#fff" font-weight="700">${esc(name.slice(0, 1))}</text>`;
  return (
    `<g class="tenant sleeper act-sleep${pressed ? ' on' : ''}" data-r="${esc(r.id)}" role="button" tabindex="0" aria-pressed="${pressed}" aria-label="${esc(label)}"><title>${esc(label)}</title>${face}` +
    `<rect x="${BED_X + 30}" y="${FY - 28}" width="80" height="14" rx="3" fill="${esc(color)}"/><rect x="${BED_X + 30}" y="${FY - 28}" width="80" height="2.5" rx="1" fill="#fff" opacity=".3"/></g>` +
    // 头顶的 z 标记放在可点的组外面：它在上下浮动，放在组里会让整组的位置一直变
    `<g class="mk-s" aria-hidden="true"><circle cx="${hx + 16}" cy="${hy - 20}" r="8" fill="#e8762b" stroke="#fff" stroke-opacity=".85" stroke-width="1.6"/><text x="${hx + 16}" y="${hy - 16.6}" font-size="9.5" text-anchor="middle" fill="#fff" font-weight="700">z</text></g>`
  );
}

/** 白天 / 黄昏 / 夜里（决定窗外的天和灯） */
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

const ACT_STATE = [
  ['eat', '用餐中'],
  ['exercise', '锻炼中'],
  ['read', '读书中'],
  ['game', '娱乐中'],
  ['chat', '聊天中'],
  ['stargaze', '赏景中'],
];

/** 房间现在的状态（一个词）：工作中 / 休息中 / 用餐中 … */
export function roomState(kind, people, dark) {
  if (!people.length) return dark ? '已熄灯' : kind === 'dorm' ? '房主外出' : '空闲';
  const acts = new Set(people.map((p) => p.activity));
  if (acts.has('work') || acts.has('wait')) return '工作中';
  if ([...acts].every((a) => a === 'sleep')) return '休息中';
  for (const [a, word] of ACT_STATE) if (acts.has(a)) return word;
  return kind === 'office' ? '待命中' : '活动中';
}

const money = (v) => `$${Number(v || 0).toFixed(2)}`;
const store = {
  get(k) {
    try {
      return localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set(k, v) {
    try {
      localStorage.setItem(k, v);
    } catch {
      /* 隐私模式下存不了，不影响显示 */
    }
  },
};

export class Apartment {
  constructor(root, { api, budget = null, now = () => new Date(), openSession = null, chatWith = null, dmWith = null, momentsOf = null } = {}) {
    this.root = root;
    this.api = api;
    this.budgetApi = budget; // 顶部资源栏的今日 / 本月花费（可选）
    this.now = now;
    this.openSession = openSession;
    this.chatWith = chatWith;
    this.dmWith = dmWith;
    this.momentsOf = momentsOf; // 看 TA 的朋友圈
    this.art = null;
    this.data = null;
    this.budget = null;
    this.selected = null;
    this.zoom = null; // 放大查看的房间 id
    this.view = store.get(VIEW_KEY) === 'map' ? 'map' : 'section';
    this.sideOpen = store.get(SIDE_KEY) !== 'off';
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
    const budget = this.budgetApi ? this.budgetApi().catch(() => null) : null;
    try {
      this.data = await this.api(t.getHours(), weekday);
      this.error = '';
    } catch (e) {
      this.error = e.message || '加载失败';
    }
    if (budget) this.budget = (await budget) || this.budget;
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
    const color = m?.theme_color || '#c9733f';
    const name = m?.display || r.name;
    const img = m?.images?.chibi
      ? `<img src="${esc(this.cut?.get(m.images.chibi) || m.images.chibi)}" alt="" loading="lazy" draggable="false">`
      : `<span class="ini" style="background:${esc(color)}">${esc(name.slice(0, 1))}</span>`;
    const label = this.label(r);
    const x = n <= 1 ? 50 : 12 + (76 * i) / (n - 1);
    const k = (this.data?.residents || []).indexOf(r);
    const pressed = this.selected === r.id;
    const mark = BUBBLES[r.activity] ? `<i class="bub mk">${BUBBLES[r.activity]}</i>` : '';
    return `<button type="button" class="tenant act-${esc(r.activity)}${pressed ? ' on' : ''}" data-r="${esc(r.id)}" aria-pressed="${pressed}" aria-label="${esc(label)}" title="${esc(label)}" style="--x:${x.toFixed(1)}%;--d:${6 + (k % 4) * 1.7}s;--c:${esc(color)}">${img}${mark}<b class="tag">${esc(name)}</b></button>`;
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

  ownerOf(room) {
    return room.kind === 'dorm' ? (this.data?.residents || []).find((r) => r.dorm === room.id) || null : null;
  }

  // 一个房间的状态：标题、谁在、开不开灯
  roomInfo(room, people) {
    const owner = this.ownerOf(room);
    const om = owner && this.look(owner);
    const title = owner ? `${room.name} · ${om?.display || owner.name}` : room.name;
    const p = this.phase;
    const night = p === 'night' || p === 'dawn';
    const lit = night || p === 'dusk';
    const asleep = people.filter((x) => x.activity === 'sleep' && x.room === x.dorm);
    const busy = people.some((x) => x.activity === 'work' || x.activity === 'wait');
    // 夜里没人的公共房间熄灯；有人在、或宿舍有人醒着就开灯
    const dark = night && !people.length && room.kind !== 'rooftop';
    const on = (lit && people.length > asleep.length) || busy || (!night && room.kind === 'office' && people.length > 0);
    const tc = om?.theme_color || '#c9733f';
    const dorms = Object.keys(this.dormNames());
    return { owner, title, night, asleep, busy, dark, on, tc, style: Math.max(0, dorms.indexOf(room.id)), state: roomState(room.kind, people, dark) };
  }

  room(room, people, { big = false } = {}) {
    const d = this.data;
    const info = this.roomInfo(room, people);
    let sign = '';
    if (room.kind === 'meeting' && d.hidden_meetings) sign = `<span class="sign" title="匿名会议不显示是谁">匿名会议进行中 ×${d.hidden_meetings}</span>`;
    // 在自己宿舍睡觉的画在床上，其余的小人站在地板上
    const awake = people.filter((p) => !info.asleep.includes(p));
    const beds = info.asleep.map((p) => sleeper(p, this.look(p), this.label(p), this.selected === p.id)).join('');
    const tenants = awake.map((p, i) => this.tenant(p, i, awake.length)).join('');
    const o = { ph: this.phase, night: info.night, on: info.on, tc: esc(info.tc), style: info.style };
    const cls = `room k-${esc(room.kind)}${info.busy ? ' busy' : ''}${info.dark ? ' dark' : ''}${info.asleep.length && !awake.length ? ' asleep' : ''}${info.on ? ' lit' : ''}${big ? ' big' : ''}`;
    const flex = room.kind === 'dorm' ? 1 : FLEX[room.kind] || 1;
    const head = `<span class="rname"><i class="rbar"></i><b>${esc(info.title)}</b><small>${esc(info.state)}${people.length ? ` · ${people.length} 人` : ''}</small></span>`;
    const zbtn = big ? '' : `<button type="button" class="zbtn" data-zoom="${esc(room.id)}" aria-label="放大看${esc(info.title)}" title="放大看">${icon('zoom')}</button>`;
    const ar = ((VB[room.kind] || 300) / (room.kind === 'rooftop' ? 70 : H)).toFixed(3);
    return `<div class="${cls}" data-room="${esc(room.id)}" style="--tc:${esc(info.tc)};--fx:${flex};--ar:${ar}">${sceneSvg(room.kind, o, beds)}<div class="people${awake.length >= 5 ? ' crowd' : ''}">${tenants}</div>${head}${sign}${zbtn}</div>`;
  }

  // 空房：暗色空槽，顶上一盏射灯
  slot() {
    return '<div class="slot" aria-hidden="true"><i class="spot"></i><span>空房 · 待入住</span></div>';
  }

  residentsIn(id) {
    return (this.data?.residents || []).filter((r) => r.room === id);
  }

  section() {
    const floors = this.data.floors || [];
    const roof = floors.find((f) => f[0]?.kind === 'rooftop');
    const rest = floors.filter((f) => f !== roof);
    const width = Math.max(...rest.filter((f) => f[0]?.kind === 'dorm').map((f) => f.length), 0);
    let rows = '';
    for (const floor of rest) {
      let rooms = floor.map((room) => this.room(room, this.residentsIn(room.id))).join('');
      // 宿舍层不满的补空房，楼层对齐
      if (floor[0]?.kind === 'dorm') for (let i = floor.length; i < width; i++) rooms += this.slot();
      rows += `<div class="floor">${rooms}</div>`;
    }
    const roofRow = roof ? `<div class="floor roof"><div class="celestial" aria-hidden="true"></div>${roof.map((room) => this.room(room, this.residentsIn(room.id))).join('')}</div>` : '';
    const n = rest.length;
    return `<div class="bldg" aria-label="公寓楼" style="--floors:${floors.length}">${roofRow}<div class="core"><div class="shaft l" aria-hidden="true"><i class="car" style="--n:${n}"></i></div><div class="floors">${rows}</div><div class="shaft r" aria-hidden="true"><i class="car slow" style="--n:${n}"></i></div></div><div class="footing"></div></div>`;
  }

  // 俯视布局图：每个房间一块深色牌子（类别色条、状态、在场人数和头像）
  map() {
    let rows = '';
    for (const floor of this.data.floors || []) {
      const tiles = floor
        .map((room) => {
          const people = this.residentsIn(room.id);
          const info = this.roomInfo(room, people);
          const avs = people
            .slice(0, 5)
            .map((p) => {
              const m = this.look(p);
              const src = m?.images?.avatar_64;
              return src ? `<img src="${esc(src)}" alt="">` : `<span style="background:${esc(m?.theme_color || '#c9733f')}">${esc((m?.display || p.name).slice(0, 1))}</span>`;
            })
            .join('');
          const sign = room.kind === 'meeting' && this.data.hidden_meetings ? `<em class="msign">匿名会议 ×${this.data.hidden_meetings}</em>` : '';
          return `<button type="button" class="tile k-${esc(room.kind)}${info.busy ? ' busy' : ''}${info.dark ? ' dark' : ''}" data-zoom="${esc(room.id)}" style="--tc:${esc(info.tc)};--fx:${room.kind === 'dorm' ? 1 : FLEX[room.kind] || 1}" aria-label="${esc(info.title)}：${esc(info.state)}，${people.length} 人"><i class="tbar"></i>${icon(room.kind, 'tico')}<span class="tname">${esc(info.title)}</span><span class="ten">${ROOM_EN[room.kind] || ''}</span><span class="tstate">${esc(info.state)}</span>${sign}<span class="tavs">${avs}</span><span class="tocc">${icon('badge', 'ic')}${people.length}</span></button>`;
        })
        .join('');
      const lift = `<span class="mlift" aria-hidden="true">${icon('lift')}</span>`;
      rows += `<div class="mrow${floor[0]?.kind === 'rooftop' ? ' roof' : ''}">${lift}${tiles}${lift}</div>`;
    }
    return `<div class="map" aria-label="公寓楼布局图">${rows}</div>`;
  }

  topbar() {
    const res = this.data?.residents || [];
    const working = res.filter((r) => r.activity === 'work' || r.activity === 'wait').length;
    const b = this.budget;
    const chip = (ic, k, v, cap, cls) => `<span class="rchip ${cls}"><i class="ci">${icon(ic)}</i><span class="ck">${k}</span><b>${esc(v)}</b>${cap ? `<small>/ ${esc(cap)}</small>` : ''}</span>`;
    let chips = '';
    if (b?.day) chips += chip('coin', '今日', money(b.day.spent_usd), money(b.day.limit_usd), b.day.warn ? 'c1 warn' : 'c1');
    if (b?.month) chips += chip('cal', '本月', money(b.month.spent_usd), money(b.month.limit_usd), b.month.warn ? 'c2 warn' : 'c2');
    chips += chip('badge', '在岗', String(working), String(res.length), 'c3');
    chips += chip('clock', '', this.clock || '--:--', '', 'c4');
    const seg = (v, label) => `<button type="button" data-view="${v}" aria-pressed="${this.view === v}">${label}</button>`;
    return `<header class="base-top"><button type="button" class="ovw" data-ovw aria-pressed="${this.sideOpen}" aria-controls="home-side"><span>进驻总览</span>${icon('people')}</button><div class="vseg" role="group" aria-label="视图">${seg('section', '剖面')}${seg('map', '布局')}</div><div class="res" aria-label="资源">${chips}</div></header>`;
  }

  side() {
    const d = this.data;
    const dorms = this.dormNames();
    const sel = (d.residents || []).find((r) => r.id === this.selected);
    let h = `<div class="sh"><b>进驻总览</b><span>RESIDENTS · ${(d.residents || []).length}</span></div><div class="clock"><span class="big">${esc(this.clock)}</span><span class="day">${esc(this.day || '')}</span><span class="hint">按你电脑的时间作息，30 秒刷新一次</span></div>`;
    if (sel) {
      const m = this.look(sel);
      h += `<div class="card" id="resident-card"><div class="who">${m?.images?.avatar_128 ? `<img src="${esc(m.images.avatar_128)}" alt="">` : ''}<div><b>${esc(m?.display || sel.name)}</b><div class="hint">${esc(ROLE[sel.role] || sel.role)} · 住 ${esc(dorms[sel.dorm] || '')}</div></div></div>`;
      h += `<p>${esc(whereText(sel, dorms[sel.dorm]))}</p>`;
      h += `<p class="hint">本月工资 $${Number(sel.month_paid_usd || 0).toFixed(4)} · ${sel.model_ids.length} 种工作模式</p><div class="acts">`;
      if (sel.session_id) h += `<button type="button" class="btn" data-open="${esc(sel.session_id)}">去看 TA 在办的事</button>`;
      if (this.dmWith) h += `<button type="button" class="btn" data-dm="${esc(sel.id)}">私聊</button>`;
      if (this.momentsOf) h += `<button type="button" class="btn" data-moments="${esc(sel.id)}">朋友圈</button>`;
      if (this.chatWith) h += `<button type="button" class="btn" data-chat="${esc(sel.name)}">在群聊里 @ TA</button>`;
      h += '</div></div>';
    }
    h += '<ul class="roster">';
    for (const r of d.residents || []) {
      const m = this.look(r);
      const av = m?.images?.avatar_64
        ? `<img class="av" src="${esc(m.images.avatar_64)}" alt="">`
        : `<span class="av ini" style="background:${esc(m?.theme_color || 'var(--c-coord)')}">${esc((m?.display || r.name).slice(0, 1))}</span>`;
      const st = r.activity === 'work' || r.activity === 'wait' ? 'w' : r.activity === 'sleep' ? 's' : 'i';
      h += `<li><button type="button" class="link${this.selected === r.id ? ' on' : ''}" data-r="${esc(r.id)}" style="--c:${esc(m?.theme_color || '#c9733f')}">${av}<span class="tx"><span class="nm"><b>${esc(m?.display || r.name)}</b>${r.role === 'manager' ? '<span class="role">经理</span>' : ''}</span><span class="hint">${esc(whereText(r, dorms[r.dorm]))}</span></span><i class="st st-${st}" aria-hidden="true"></i></button></li>`;
    }
    h += '</ul><p class="hint foot">住户就是办公室的员工，同一位的全力和节电两种模式住一间。这里只是看看，不调用模型，也不花钱。</p>';
    return h;
  }

  // 放大查看一个房间：大场景 + 在场的人
  zoomed() {
    const room = (this.data.floors || []).flat().find((r) => r.id === this.zoom);
    if (!room) return '';
    const people = this.residentsIn(room.id);
    const info = this.roomInfo(room, people);
    const dorms = this.dormNames();
    const list = people.length
      ? people
          .map((p) => {
            const m = this.look(p);
            const av = m?.images?.avatar_64 ? `<img src="${esc(m.images.avatar_64)}" alt="">` : `<span class="av0" style="background:${esc(m?.theme_color || '#c9733f')}">${esc((m?.display || p.name).slice(0, 1))}</span>`;
            return `<li><button type="button" data-r="${esc(p.id)}" class="${this.selected === p.id ? 'on' : ''}">${av}<b>${esc(m?.display || p.name)}</b><span>${esc(whereText(p, dorms[p.dorm]))}</span></button></li>`;
          })
          .join('')
      : `<li class="none">${room.kind === 'meeting' && this.data.hidden_meetings ? `匿名会议进行中 ×${this.data.hidden_meetings}，不显示是谁` : '现在没有人'}</li>`;
    return `<div class="zoom" data-zclose><div class="zbox" role="dialog" aria-modal="true" aria-label="${esc(info.title)}"><header><i class="rbar"></i><div><b>${esc(info.title)}</b><span>${ROOM_EN[room.kind] || ''} · ${esc(info.state)}</span></div><button type="button" class="zx" data-zclose aria-label="关闭">${icon('close')}</button></header><div class="zstage">${this.room(room, people, { big: true })}</div><ul class="zlist">${list}</ul></div></div>`;
  }

  render() {
    if (!this.data) {
      this.root.innerHTML = `<p class="empty">${esc(this.error || '加载中…')}</p>`;
      return;
    }
    this.phase = phase(this.hour ?? 12);
    const err = this.error ? `<p class="hint badp" role="alert">${esc(this.error)}</p>` : '';
    const body = this.view === 'map' ? this.map() : this.section();
    this.root.classList.toggle('noside', !this.sideOpen);
    this.root.innerHTML = `<div class="base ph-${this.phase} v-${this.view}">${this.topbar()}<div class="bstage">${body}</div>${this.zoom ? this.zoomed() : ''}</div><aside class="home-side" id="home-side" aria-label="住户"${this.sideOpen ? '' : ' hidden'}>${err}${this.side()}</aside>`;
  }

  onClick(e) {
    const open = e.target.closest('[data-open]');
    if (open && this.openSession) return this.openSession(open.dataset.open);
    const dm = e.target.closest('[data-dm]');
    if (dm && this.dmWith) return this.dmWith(dm.dataset.dm);
    const mo = e.target.closest('[data-moments]');
    if (mo && this.momentsOf) return this.momentsOf(mo.dataset.moments);
    const talk = e.target.closest('[data-chat]');
    if (talk && this.chatWith) return this.chatWith(talk.dataset.chat);
    const view = e.target.closest('[data-view]');
    if (view) {
      this.view = view.dataset.view === 'map' ? 'map' : 'section';
      store.set(VIEW_KEY, this.view);
      return this.render();
    }
    if (e.target.closest('[data-ovw]')) {
      this.sideOpen = !this.sideOpen;
      store.set(SIDE_KEY, this.sideOpen ? 'on' : 'off');
      return this.render();
    }
    const r = e.target.closest('[data-r]');
    if (r) {
      this.selected = this.selected === r.dataset.r ? null : r.dataset.r;
      // 选中住户时总览收起着就打开，好看到卡片
      if (this.selected && !this.sideOpen) this.sideOpen = true;
      this.render();
      this.root.querySelector(`.tenant[data-r="${CSS.escape(r.dataset.r)}"]`)?.focus({ preventScroll: true });
      return;
    }
    // 只有点在遮罩或关闭按钮上才关；点放大的房间本身不关
    const close = e.target.closest('[data-zclose]');
    if (close && (close.classList.contains('zx') || e.target === close)) {
      this.zoom = null;
      return this.render();
    }
    if (e.target.closest('.zbox')) return;
    // 点房间（放大按钮或房间空白处）：放大看
    const z = e.target.closest('[data-zoom]') || e.target.closest('.room[data-room]');
    if (z) {
      this.zoom = z.dataset.zoom || z.dataset.room;
      this.render();
      this.root.querySelector('.zoom .zx')?.focus({ preventScroll: true });
    }
  }

  onKey(e) {
    // 床上的住户是 SVG 元素，不是按钮：Enter / 空格当作点击
    const g = e.target.closest?.('g[data-r]');
    if (g && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      g.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      return;
    }
    if (e.key !== 'Escape') return;
    if (this.zoom) {
      this.zoom = null;
      this.render();
    } else if (this.selected) {
      this.selected = null;
      this.render();
    }
  }
}
