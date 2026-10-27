// 公寓楼：员工下班后住的地方。基建式剖面图：深色金属楼体、两侧电梯井、空房是暗槽，每个房间是一幕有自己主题和灯光的场景。
// 住户按作息在各房间活动，正在办事的在办公室 / 会议室；另有俯视「布局图」，点房间放大看详情。
// 数据来自 /api/apartment（按浏览器本地时间）；小人用 /api/members 的 chibi，没有素材时用昵称首字。
// 匿名未揭晓的会话：服务端只给数量，这里只在会议室门上挂牌，不显示是谁。
// 所有图形都在这里（宿舍的伪 3D 场景在 rooms3d.js）用 SVG / CSS 自绘，不用外部美术素材。
// 互动：拖小人到别的房间（落地弹跳、按房间换动作、串门打招呼）、点小人（摸头 / 聊一句 / 看状态）、放大后点家具。
// 这些都只在浏览器里：不调用模型、不花钱、不改真实作息，几分钟后小人自己回到按作息的位置。

import { cutout } from './cutout.js';
import { THEMES, bedAnchor, dormScene } from './rooms3d.js';

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

// --- 互动台词与规则 ----------------------------------------------------------------------------
const PLACE_MS = 3 * 60_000; // 拖过去 / 叫去用家具的小人，过这么久自己回到按作息的位置
const SAY_MS = 3200; // 头顶对话气泡显示多久
const pick = (a) => a[Math.floor(Math.random() * a.length)];
const calm = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
const clampPct = (v) => Math.min(94, Math.max(6, v));
// 放进各类房间后做什么；{o} 换成房主的称呼。label 前面会自动加上「在食堂」这类地点（whereText）
const DROP = {
  office: { act: 'work', label: '被你拎来帮忙', say: ['好，我看看手头的活。', '工位就位！', '先把屏幕打开。'] },
  meeting: { act: 'chat', label: '开小会', say: ['要开会吗？我带本子了。', '白板借我用用。'] },
  canteen: { act: 'eat', label: '吃点东西', say: ['正好有点饿了。', '今天的菜单看着不错！'] },
  lounge: { act: 'game', label: '看电视', say: ['沙发好软……', '遥控器在谁那儿？'] },
  gym: { act: 'exercise', label: '锻炼', say: ['好吧，动一动！', '再来一组！'] },
  rooftop: { act: 'stargaze', label: '看风景', say: ['风好舒服。', '从这儿能看好远。'] },
  home: { act: 'read', label: '歇着', say: ['还是自己屋里自在。', '回家啦。'] },
  visit: { act: 'chat', label: '去{o}屋里串门', say: ['嗨 {o}，我来串门啦！', '{o}，打扰一下～'] },
  away: { act: 'idle', label: '在{o}屋里等 TA 回来', say: ['{o}不在家，我在这儿等等。'] },
  hush: { act: 'idle', label: '在{o}屋里（{o}在睡觉）', say: ['嘘……{o}睡着了，我轻点。'] },
};
const LIFT = ['哇！', '飞起来了！', '要带我去哪？'];
const WOKEN = ['唔……我刚睡着呢。', '（揉眼睛）去哪儿？'];
const PAT = ['嘿嘿～', '（眯起眼睛）', '再摸要长不高了！', '被摸头了，开心。'];
const PAT_SLEEP = ['呼……（翻了个身）', 'zzz……'];
const BACK = ['我回原来的地方啦～', '该回去了。'];
// 聊一句：按正在做的事；成员设定里有口头禅时一半概率说口头禅
const TALK = {
  work: ['这个问题有点意思。', '等我把这段弄完。'],
  wait: ['在等结果出来。', '马上就好。'],
  sleep: ['zzz……'],
  eat: ['这个好吃！', '吃饱了才有力气。'],
  exercise: ['呼——再坚持一下。', '今天也要动起来！'],
  read: ['这本书讲得真好。', '再看一章就睡。'],
  chat: ['我们刚聊到哪了？', '你也来坐坐？'],
  game: ['这局我能赢！', '这集好看。'],
  stargaze: ['从这儿能看到好远。', '风好舒服。'],
  idle: ['今天过得怎么样？', '要不要去天台吹吹风？', '今天也记得和朋友、家人聊聊天哦。'],
};

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

// 放大房间时可以点的家具：viewBox 里的范围、叫人去做什么、头上冒的小符号；owner：只有房主能用（床）
const HOTS = {
  dorm: [{ id: 'bed', x: BED_X, y: FY - 42, w: 114, h: 42, name: '床', act: 'sleep', label: '睡觉', say: ['晚安～', '先眯一会儿。'], fx: 'z', owner: true }],
  office: [{ id: 'screen', x: 170, y: 10, w: 220, h: 58, name: '屏幕墙', act: 'work', label: '盯着大屏看数据', say: ['曲线又涨了。', '这个图有意思。'], fx: '!' }],
  meeting: [{ id: 'board', x: 44, y: 11, w: 112, h: 62, name: '投影幕', act: 'chat', label: '对着投影讲方案', say: ['看下一页！', '我来讲两句。'], fx: '!' }],
  canteen: [
    { id: 'stove', x: 14, y: FY - 46, w: 120, h: 46, name: '灶台', act: 'eat', label: '炒菜', say: ['火候正好。', '香不香？'], fx: '~' },
    { id: 'fridge', x: 222, y: FY - 72, w: 34, h: 72, name: '冰箱', act: 'eat', label: '翻冰箱找吃的', say: ['还剩一盒酸奶！', '冰箱里有什么呢……'], fx: '*' },
  ],
  lounge: [
    { id: 'tv', x: 234, y: 22, w: 50, h: 30, name: '电视', act: 'game', label: '看电视', say: ['这集好看！', '换个台吧。'], fx: '♪' },
    { id: 'fire', x: 30, y: FY - 60, w: 78, h: 60, name: '壁炉', act: 'idle', label: '烤火', say: ['好暖和～'], fx: '~' },
    { id: 'sofa', x: 114, y: FY - 36, w: 98, h: 36, name: '沙发', act: 'read', label: '窝在沙发上', say: ['不想动了……'], fx: '♥' },
  ],
  gym: [
    { id: 'run', x: 20, y: FY - 56, w: 82, h: 56, name: '跑步机', act: 'exercise', label: '跑步', say: ['再跑一公里！'], fx: '!' },
    { id: 'bar', x: 166, y: FY - 56, w: 64, h: 56, name: '杠铃', act: 'exercise', label: '举杠铃', say: ['嘿——咻！'], fx: '!' },
    { id: 'bag', x: 280, y: 6, w: 32, h: 70, name: '沙袋', act: 'exercise', label: '打沙袋', say: ['看我组合拳！'], fx: '!' },
  ],
  rooftop: [
    { id: 'scope', x: 814, y: 18, w: 36, h: 32, name: '望远镜', act: 'stargaze', label: '用望远镜看远处', say: ['那边的楼也亮灯了。', '看到星星了！'], fx: '*' },
    { id: 'bench', x: 516, y: 28, w: 68, h: 22, name: '长椅', act: 'idle', label: '坐在长椅上发呆', say: ['坐一会儿。'], fx: '~' },
  ],
};
// 宿舍有伪 3D 场景（rooms3d.js）的房号；其余还是平面剖面
const is3d = (kind, o) => kind === 'dorm' && o.style < THEMES.length;
const hotsOf = (kind, o) => (is3d(kind, o) ? HOTS.dorm.map((h) => ({ ...h, ...bedAnchor().hot })) : HOTS[kind] || []);
const hotSvg = (kind, o) =>
  hotsOf(kind, o)
    .map((h) => `<rect class="hot" data-hot="${h.id}" x="${h.x}" y="${h.y}" width="${h.w}" height="${h.h}" rx="3" fill="transparent" role="button" tabindex="0" aria-label="${h.name}"><title>${h.name}</title></rect>`)
    .join('');

function sceneSvg(kind, o, extra = '') {
  // 伪 3D 房间盒按比例铺满（多出的部分裁掉天花板），不能像平面墙那样往两边无限延伸
  if (is3d(kind, o)) return `<svg class="scene s3d" viewBox="0 0 ${VB.dorm} ${H}" preserveAspectRatio="xMidYMax slice" aria-hidden="${extra ? 'false' : 'true'}">${dormScene(o, o.uid)}${extra}</svg>`;
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
function sleeper(r, m, label, pressed, deep = false) {
  const color = m?.theme_color || '#c9733f';
  const name = m?.display || r.name;
  const head = m?.images?.avatar_64 || m?.images?.avatar_128;
  const at = deep ? bedAnchor() : null;
  const hx = at ? at.hx : BED_X + 22;
  const hy = at ? at.hy : FY - 32;
  const hr = at ? at.hr : 12;
  const quilt = at
    ? at.quilt.map((d, i) => `<polygon points="${d}" fill="${esc(color)}"${i ? ' style="filter:brightness(.82)"' : ''}/>`).join('')
    : `<rect x="${BED_X + 30}" y="${FY - 28}" width="80" height="14" rx="3" fill="${esc(color)}"/><rect x="${BED_X + 30}" y="${FY - 28}" width="80" height="2.5" rx="1" fill="#fff" opacity=".3"/>`;
  const face = head
    ? `<clipPath id="hc-${esc(r.id)}"><circle cx="${hx}" cy="${hy}" r="${hr}"/></clipPath><image href="${esc(head)}" x="${hx - hr}" y="${hy - hr}" width="${hr * 2}" height="${hr * 2}" clip-path="url(#hc-${esc(r.id)})" preserveAspectRatio="xMidYMid slice"/>`
    : `<circle cx="${hx}" cy="${hy}" r="${hr}" fill="${esc(color)}"/><text x="${hx}" y="${hy + 4}" font-size="11" text-anchor="middle" fill="#fff" font-weight="700">${esc(name.slice(0, 1))}</text>`;
  return (
    `<g class="tenant sleeper act-sleep${pressed ? ' on' : ''}" data-r="${esc(r.id)}" role="button" tabindex="0" aria-pressed="${pressed}" aria-label="${esc(label)}"><title>${esc(label)}</title>${face}` +
    `${quilt}</g>` +
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
  constructor(root, { api, budget = null, now = () => new Date(), openSession = null, chatWith = null, dmWith = null, momentsOf = null, personas = null, setupOf = null } = {}) {
    this.root = root;
    this.api = api;
    this.budgetApi = budget; // 顶部资源栏的今日 / 本月花费（可选）
    this.now = now;
    this.openSession = openSession;
    this.chatWith = chatWith;
    this.dmWith = dmWith;
    this.momentsOf = momentsOf; // 看 TA 的朋友圈
    this.personasApi = personas; // 聊一句时用成员设定里的口头禅（可选）
    this.setupOf = setupOf; // 打开成员设定（可选）
    this.moved = new Map(); // 临时摆放：住户 id → { patch, until }，只盖在显示上
    this.says = new Map(); // 头顶气泡：住户 id → { text, until }
    this.popOpen = false; // 点小人弹出的互动菜单
    this.drag = null;
    this.eatClick = false; // 拖完松手时浏览器紧接着补的那个 click 不算点击
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
    root.addEventListener('pointerdown', (e) => this.onDown(e));
    root.addEventListener('scroll', () => this.placePop(), true);
    window.addEventListener('resize', () => this.placePop());
    this._move = (e) => this.onMove(e);
    this._up = (e) => this.onUp(e);
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
    if (this.drag) this.onUp({ type: 'pointercancel', pointerId: this.drag.pid });
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
    // 被拖过去 / 叫去用家具的站在放下的位置
    const x = r._x ?? (n <= 1 ? 50 : 12 + (76 * i) / (n - 1));
    const k = (this.data?.residents || []).findIndex((p) => p.id === r.id);
    const pressed = this.selected === r.id;
    const mark = BUBBLES[r.activity] ? `<i class="bub mk">${BUBBLES[r.activity]}</i>` : '';
    const s = this.says.get(r.id);
    const say = s && s.until > Date.now() ? `<span class="say" aria-hidden="true" data-until="${s.until}">${esc(s.text)}</span>` : '';
    const placed = this.moved.has(r.id) ? ' placed' : '';
    return `<button type="button" class="tenant act-${esc(r.activity)}${pressed ? ' on' : ''}${placed}" data-r="${esc(r.id)}" aria-pressed="${pressed}" aria-label="${esc(label)}" title="${esc(label)}" style="--x:${x.toFixed(1)}%;--d:${6 + (k % 4) * 1.7}s;--c:${esc(color)}">${img}${mark}${say}<b class="tag">${esc(name)}</b></button>`;
  }

  // 小人的形象（拖起来时跟着手走的那个）
  figure(r) {
    const m = this.look(r);
    const src = m?.images?.chibi ? this.cut?.get(m.images.chibi) || m.images.chibi : m?.images?.avatar_128;
    return src ? `<img src="${esc(src)}" alt="" draggable="false">` : `<span class="ini" style="background:${esc(m?.theme_color || '#c9733f')}">${esc((m?.display || r.name).slice(0, 1))}</span>`;
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
    const o = { ph: this.phase, night: info.night, on: info.on, tc: esc(info.tc), style: info.style, uid: room.id + (big ? 'z' : '') };
    const beds = info.asleep.map((p) => sleeper(p, this.look(p), this.label(p), this.selected === p.id, is3d(room.kind, o))).join('');
    const tenants = awake.map((p, i) => this.tenant(p, i, awake.length)).join('');
    const cls = `room k-${esc(room.kind)}${info.busy ? ' busy' : ''}${info.dark ? ' dark' : ''}${info.asleep.length && !awake.length ? ' asleep' : ''}${info.on ? ' lit' : ''}${big ? ' big' : ''}`;
    const flex = room.kind === 'dorm' ? 1 : FLEX[room.kind] || 1;
    const head = `<span class="rname"><i class="rbar"></i><b>${esc(info.title)}</b><small>${esc(info.state)}${people.length ? ` · ${people.length} 人` : ''}</small></span>`;
    const zbtn = big ? '' : `<button type="button" class="zbtn" data-zoom="${esc(room.id)}" aria-label="放大看${esc(info.title)}" title="放大看">${icon('zoom')}</button>`;
    const ar = ((VB[room.kind] || 300) / (room.kind === 'rooftop' ? 70 : H)).toFixed(3);
    return `<div class="${cls}" data-room="${esc(room.id)}" style="--tc:${esc(info.tc)};--fx:${flex};--ar:${ar}">${sceneSvg(room.kind, o, (big ? hotSvg(room.kind, o) : '') + beds)}<div class="people${awake.length >= 5 ? ' crowd' : ''}">${tenants}</div>${head}${sign}${zbtn}</div>`;
  }

  // 空房：暗色空槽，顶上一盏射灯
  slot() {
    return '<div class="slot" aria-hidden="true"><i class="spot"></i><span>空房 · 待入住</span></div>';
  }

  residentsIn(id) {
    return this.people().filter((r) => r.room === id);
  }

  // 显示用的住户：临时摆放的盖在作息上（只在这个页面里，不回写）
  people() {
    return this.res || this.data?.residents || [];
  }

  // 过期的摆放撤掉；返回刚回去的住户 id
  applyMoves() {
    const now = Date.now();
    const back = [];
    for (const [id, m] of this.moved)
      if (m.until <= now) {
        this.moved.delete(id);
        back.push(id);
      }
    this.res = (this.data?.residents || []).map((r) => (this.moved.has(r.id) ? { ...r, ...this.moved.get(r.id).patch } : r));
    return back;
  }

  move(id, patch) {
    const cur = this.moved.get(id)?.patch || {};
    this.moved.set(id, { patch: { ...cur, ...patch }, until: Date.now() + PLACE_MS });
    const next = Math.min(...[...this.moved.values()].map((m) => m.until));
    clearTimeout(this.backTimer);
    this.backTimer = setTimeout(() => this.data && !this.root.hidden && this.render(), Math.max(0, next - Date.now()) + 50);
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
    const sel = this.people().find((r) => r.id === this.selected);
    let h = `<div class="sh"><b>进驻总览</b><span>RESIDENTS · ${(d.residents || []).length}</span></div><div class="clock"><span class="big">${esc(this.clock)}</span><span class="day">${esc(this.day || '')}</span><span class="hint">按你电脑的时间作息，30 秒刷新一次</span></div>`;
    if (sel) {
      const m = this.look(sel);
      h += `<div class="card" id="resident-card"><div class="who">${m?.images?.avatar_128 ? `<img src="${esc(m.images.avatar_128)}" alt="">` : ''}<div><b>${esc(m?.display || sel.name)}</b><div class="hint">${esc(ROLE[sel.role] || sel.role)} · 住 ${esc(dorms[sel.dorm] || '')}</div></div></div>`;
      h += `<p>${esc(whereText(sel, dorms[sel.dorm]))}</p>`;
      const mv = this.moved.get(sel.id);
      if (mv) h += `<p class="hint placed-note">这是你临时摆放的，约 ${Math.max(1, Math.ceil((mv.until - Date.now()) / 60_000))} 分钟后自己回去 <button type="button" class="back" data-back="${esc(sel.id)}">现在就回去</button></p>`;
      h += `<p class="hint">本月工资 $${Number(sel.month_paid_usd || 0).toFixed(4)} · ${sel.model_ids.length} 种工作模式</p><div class="acts">`;
      if (sel.session_id) h += `<button type="button" class="btn" data-open="${esc(sel.session_id)}">去看 TA 在办的事</button>`;
      if (this.dmWith) h += `<button type="button" class="btn" data-dm="${esc(sel.id)}">私聊</button>`;
      if (this.momentsOf) h += `<button type="button" class="btn" data-moments="${esc(sel.id)}">朋友圈</button>`;
      if (this.chatWith) h += `<button type="button" class="btn" data-chat="${esc(sel.name)}">在群聊里 @ TA</button>`;
      h += '</div></div>';
    }
    h += '<ul class="roster">';
    for (const r of this.people()) {
      const m = this.look(r);
      const av = m?.images?.avatar_64
        ? `<img class="av" src="${esc(m.images.avatar_64)}" alt="">`
        : `<span class="av ini" style="background:${esc(m?.theme_color || 'var(--c-coord)')}">${esc((m?.display || r.name).slice(0, 1))}</span>`;
      const st = r.activity === 'work' || r.activity === 'wait' ? 'w' : r.activity === 'sleep' ? 's' : 'i';
      h += `<li><button type="button" class="link${this.selected === r.id ? ' on' : ''}" data-r="${esc(r.id)}" style="--c:${esc(m?.theme_color || '#c9733f')}">${av}<span class="tx"><span class="nm"><b>${esc(m?.display || r.name)}</b>${r.role === 'manager' ? '<span class="role">经理</span>' : ''}</span><span class="hint">${esc(whereText(r, dorms[r.dorm]))}</span></span><i class="st st-${st}" aria-hidden="true"></i></button></li>`;
    }
    h += '</ul><p class="hint foot">住户就是办公室的员工，同一位的全力和节电两种模式住一间。拖动小人可以带 TA 去别的房间，放大房间后家具也能点；这些只是逗着玩，几分钟后各回各处，不调用模型，也不花钱。</p>';
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
    return `<div class="zoom" data-zclose><div class="zbox" role="dialog" aria-modal="true" aria-label="${esc(info.title)}"><header><i class="rbar"></i><div><b>${esc(info.title)}</b><span>${ROOM_EN[room.kind] || ''} · ${esc(info.state)}</span><em class="ztip">拖动小人换位置 · 点家具试试</em></div><button type="button" class="zx" data-zclose aria-label="关闭">${icon('close')}</button></header><div class="zstage">${this.room(room, people, { big: true })}</div><ul class="zlist">${list}</ul></div></div>`;
  }

  render() {
    if (!this.data) {
      this.root.innerHTML = `<p class="empty">${esc(this.error || '加载中…')}</p>`;
      return;
    }
    // 正在拖：等放下再重画，不然手里的小人会被换掉
    if (this.drag?.on) {
      this.stale = true;
      return;
    }
    const back = this.applyMoves();
    this.phase = phase(this.hour ?? 12);
    const err = this.error ? `<p class="hint badp" role="alert">${esc(this.error)}</p>` : '';
    const body = this.view === 'map' ? this.map() : this.section();
    this.root.classList.toggle('noside', !this.sideOpen);
    this.root.innerHTML = `<div class="base ph-${this.phase} v-${this.view}">${this.topbar()}<div class="bstage">${body}</div>${this.zoom ? this.zoomed() : ''}${this.pop()}</div><aside class="home-side" id="home-side" aria-label="住户"${this.sideOpen ? '' : ' hidden'}>${err}${this.side()}</aside>`;
    for (const s of this.root.querySelectorAll('.say[data-until]')) {
      this.fit(s);
      setTimeout(() => s.remove(), Number(s.dataset.until) - Date.now());
    }
    this.placePop();
    for (const id of back) this.say(id, pick(BACK));
  }

  onClick(e) {
    if (this.eatClick) return;
    const act = e.target.closest('[data-act]');
    if (act) return this.interact(act.dataset.act);
    const setup = e.target.closest('[data-setup]');
    if (setup && this.setupOf) return this.setupOf(setup.dataset.setup);
    const back = e.target.closest('[data-back]');
    if (back) {
      this.moved.delete(back.dataset.back);
      this.render();
      return this.say(back.dataset.back, pick(BACK));
    }
    const hot = e.target.closest('[data-hot]');
    if (hot) return this.useHot(hot);
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
      // 点楼里的小人（或放大房间下面的名单）弹出互动菜单；点右侧名单只选中
      this.popOpen = !!this.selected && !!r.closest('.tenant, .zlist');
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
    const g = e.target.closest?.('g[data-r], rect[data-hot]');
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
  // --- 互动 -------------------------------------------------------------------------------------

  tenantEl(id) {
    if (!id) return null;
    const q = `.tenant[data-r="${CSS.escape(id)}"]`;
    return this.root.querySelector(`.zstage ${q}`) || (this.zoom ? null : this.root.querySelector(`.bstage ${q}`));
  }

  // 头顶冒一句话：站着的小人放在按钮里（跟着走动），床上的放在房间的住户层
  say(id, text) {
    const until = Date.now() + SAY_MS;
    this.says.set(id, { text, until });
    const el = this.tenantEl(id);
    if (!el) return;
    if (el.tagName === 'BUTTON') {
      el.querySelector(':scope > .say')?.remove();
      const s = document.createElement('span');
      s.className = 'say';
      s.setAttribute('aria-hidden', 'true');
      s.textContent = text;
      el.append(s);
      this.fit(s);
      setTimeout(() => s.remove(), SAY_MS);
    } else this.floatAt(el, 'say free', text, SAY_MS);
  }

  // 在某个东西头顶的住户层里放一个元素（气泡 / 小符号），过一会儿自己消失
  floatAt(anchor, cls, text, ms, dx = 0.5, delay = 0) {
    const layer = anchor.closest('.room')?.querySelector('.people');
    if (!layer) return;
    const a = anchor.getBoundingClientRect();
    const l = layer.getBoundingClientRect();
    const s = document.createElement(cls.startsWith('say') ? 'span' : 'i');
    s.className = cls;
    s.setAttribute('aria-hidden', 'true');
    s.textContent = text;
    s.style.left = `${a.left + a.width * dx - l.left}px`;
    s.style.top = `${a.top - l.top + (cls === 'hfx' ? a.height * 0.2 : 0)}px`;
    if (delay) s.style.animationDelay = `${delay}ms`;
    layer.append(s);
    if (cls !== 'hfx') this.fit(s);
    setTimeout(() => s.remove(), ms + delay);
  }

  // 气泡别被房间左右边裁掉：超出时往里挪。按布局位置算（不受走动和弹出动画的变形影响）
  fit(s) {
    const t = s.closest('.tenant');
    const layer = s.closest('.people');
    if (!layer) return;
    const c = t ? t.offsetLeft + t.offsetWidth / 2 : s.offsetLeft;
    const half = s.offsetWidth / 2;
    const pad = t ? 18 : 4; // 站着的小人会左右走动 ±14px，多留点
    const w = layer.clientWidth;
    const dx = c - half < pad ? pad - (c - half) : c + half > w - pad ? w - pad - (c + half) : 0;
    if (dx) s.style.marginLeft = `${dx}px`;
  }

  burst(anchor, glyph, n = 1) {
    for (let i = 0; i < n; i++) this.floatAt(anchor, 'hfx', glyph, 1300, 0.3 + Math.random() * 0.4, i * 140);
  }

  // 互动菜单：浮在小人头顶（放在 .base 里，不被房间裁掉）
  pop() {
    if (!this.popOpen || !this.selected) return '';
    const r = this.people().find((p) => p.id === this.selected);
    if (!r) return '';
    const name = this.look(r)?.display || r.name;
    const b = (attr, label) => `<button type="button" ${attr}>${label}</button>`;
    let acts = b('data-act="pat"', '摸摸头') + b('data-act="talk"', '聊一句') + b('data-act="status"', '看状态');
    if (this.dmWith) acts += b(`data-dm="${esc(r.id)}"`, '私聊');
    if (this.setupOf) acts += b(`data-setup="${esc(r.id)}"`, '设定');
    return `<div class="tpop" role="group" aria-label="和 ${esc(name)} 互动" style="visibility:hidden"><b>${esc(name)}</b><div class="tacts">${acts}</div></div>`;
  }

  placePop() {
    const pop = this.root.querySelector('.tpop');
    if (!pop) return;
    const el = this.tenantEl(this.selected);
    const base = this.root.querySelector('.base');
    if (!el || !base) {
      pop.style.visibility = 'hidden';
      return;
    }
    const a = el.getBoundingClientRect();
    const b = base.getBoundingClientRect();
    const w = pop.offsetWidth;
    const h = pop.offsetHeight;
    const left = Math.min(b.width - w - 8, Math.max(8, a.left + a.width / 2 - b.left - w / 2));
    // 默认放在脚下，头顶留给对话气泡；下面放不下时放头顶
    let top = a.bottom - b.top + 6;
    if (top + h > b.height - 8) top = Math.max(8, a.top - b.top - h - 10);
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
    pop.style.visibility = 'visible';
  }

  async interact(kind) {
    const r = this.people().find((p) => p.id === this.selected);
    if (!r) return;
    const el = this.tenantEl(r.id);
    if (kind === 'pat') {
      const body = el?.querySelector('img, .ini');
      if (body && !calm())
        body.animate(
          [{ transform: 'none' }, { transform: 'scale(1.1, .86)', offset: 0.3 }, { transform: 'scale(.95, 1.06)', offset: 0.6 }, { transform: 'none' }],
          { duration: 520, easing: 'ease-out' },
        );
      if (el) this.burst(el, '♥', 3);
      this.say(r.id, pick(r.activity === 'sleep' ? PAT_SLEEP : PAT));
    } else if (kind === 'talk') this.say(r.id, await this.line(r));
    else if (kind === 'status') {
      const where = whereText(r, this.dormNames()[r.dorm]);
      this.say(r.id, `${where}${this.moved.has(r.id) ? '（你摆的）' : ''} · 本月工资 $${Number(r.month_paid_usd || 0).toFixed(2)}`);
    }
  }

  async line(r) {
    if (this.personasApi && !this.catches) {
      try {
        const res = await this.personasApi();
        this.catches = Object.fromEntries((res.members || []).map((m) => [m.id, (m.persona?.catchphrases || []).filter(Boolean)]));
      } catch {
        this.catches = {};
      }
    }
    const own = this.catches?.[r.id] || [];
    if (own.length && Math.random() < 0.5) return pick(own);
    const h = this.hour ?? 12;
    if (Math.random() < 0.3 && (h >= 23 || h < 5)) return '这么晚了，早点休息呀。';
    if (Math.random() < 0.3 && h >= 5 && h < 10) return '早上好！';
    return pick(TALK[r.activity] || TALK.idle);
  }

  // 在一张 img 上播成员逐帧素材里的某个动作；没有这个动作时什么也不做。返回停止的函数（停下时换回原图）
  frames(img, r, action, loop) {
    const clip = this.look(r)?.pet?.actions?.[action];
    if (!img || !clip?.frames?.length || calm()) return null;
    const orig = img.src;
    let i = 0;
    img.src = clip.frames[0];
    const timer = setInterval(() => {
      i += 1;
      if (i >= clip.frames.length && !loop) return stop();
      img.src = clip.frames[i % clip.frames.length];
    }, 1000 / (clip.fps || 8));
    const stop = () => {
      clearInterval(timer);
      if (img.isConnected) img.src = orig;
    };
    return stop;
  }

  // --- 拖拽：按住小人拖到别的房间，松手落地 -------------------------------------------------------

  onDown(e) {
    if (e.button !== 0 || !this.data) return;
    const t = e.target.closest('.tenant[data-r]');
    if (!t) return;
    this.drag = { id: t.dataset.r, el: t, x0: e.clientX, y0: e.clientY, pid: e.pointerId, on: false };
    window.addEventListener('pointermove', this._move);
    window.addEventListener('pointerup', this._up);
    window.addEventListener('pointercancel', this._up);
  }

  onMove(e) {
    const d = this.drag;
    if (!d || e.pointerId !== d.pid) return;
    if (!d.on) {
      if (Math.hypot(e.clientX - d.x0, e.clientY - d.y0) < 6) return; // 手抖不算拖
      this.lift(d);
    }
    e.preventDefault();
    d.ghost.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
    const room = this.dropTarget(e.clientX, e.clientY);
    if (room !== d.over) {
      d.over?.classList.remove('drop-on');
      room?.classList.add('drop-on');
      d.over = room;
    }
  }

  lift(d) {
    const r = this.people().find((p) => p.id === d.id);
    if (!r) return;
    d.on = true;
    const box = d.el.getBoundingClientRect();
    const size = d.el.tagName === 'BUTTON' ? Math.max(48, box.width) : 64;
    const g = document.createElement('div');
    g.className = 'drag-ghost';
    g.setAttribute('aria-hidden', 'true');
    g.style.setProperty('--gs', `${size}px`);
    g.innerHTML = `<div class="gb">${this.figure(r)}</div><span class="say">${esc(pick(r.activity === 'sleep' ? WOKEN : LIFT))}</span>`;
    document.body.append(g);
    d.ghost = g;
    // 有逐帧素材的成员：拎起来时播「被拎起」的帧
    d.stop = this.frames(g.querySelector('img'), r, 'drag', true);
    d.el.classList.add('lifted');
    this.root.classList.add('dragging');
    this.popOpen = false;
    this.root.querySelector('.tpop')?.remove();
  }

  // 松手处的房间；放大查看时只能在放大的房间里挪
  dropTarget(x, y) {
    const room = document.elementFromPoint(x, y)?.closest?.('.room[data-room]');
    if (!room || !this.root.contains(room)) return null;
    if (this.zoom && !room.closest('.zstage')) return null;
    return room;
  }

  onUp(e) {
    const d = this.drag;
    if (!d || e.pointerId !== d.pid) return;
    window.removeEventListener('pointermove', this._move);
    window.removeEventListener('pointerup', this._up);
    window.removeEventListener('pointercancel', this._up);
    this.drag = null;
    if (!d.on) return;
    this.eatClick = true;
    setTimeout(() => (this.eatClick = false), 0);
    d.stop?.();
    d.ghost?.remove();
    d.over?.classList.remove('drop-on');
    d.el.classList.remove('lifted');
    this.root.classList.remove('dragging');
    const room = e.type === 'pointerup' ? this.dropTarget(e.clientX, e.clientY) : null;
    if (room) this.place(d.id, room, e.clientX, e.clientY);
    else if (this.stale) this.render();
    this.stale = false;
  }

  // 放进房间：按房间换动作（只盖在显示上），落地弹一下，房主 / 同屋的打招呼
  place(id, roomEl, cx, cy) {
    const room = (this.data.floors || []).flat().find((x) => x.id === roomEl.dataset.room);
    const r = this.people().find((p) => p.id === id);
    if (!room || !r) return;
    const name = (p) => this.look(p)?.display || p.name;
    const box = roomEl.querySelector('.people').getBoundingClientRect();
    const x = clampPct(((cx - box.left) / box.width) * 100);
    const owner = this.ownerOf(room);
    const host = owner && owner.id !== r.id ? this.people().find((p) => p.id === owner.id) : null;
    let spec = DROP[room.kind] || DROP.lounge;
    if (room.kind === 'dorm') spec = !host ? DROP.home : host.room !== room.id ? DROP.away : host.activity === 'sleep' ? DROP.hush : DROP.visit;
    const fill = (t) => t.replaceAll('{o}', host ? name(host) : '');
    const others = this.people().filter((p) => p.room === room.id && p.id !== id && p.activity !== 'sleep');
    this.move(id, { room: room.id, activity: spec.act, activity_label: fill(spec.label), _x: x });
    // 串门：醒着的房主放下手里的事陪聊（正在办事的不打扰）
    const greeter = spec === DROP.visit ? host : others[0];
    if (spec === DROP.visit && greeter === host && host.activity !== 'work' && host.activity !== 'wait') this.move(host.id, { activity: 'chat', activity_label: `和 ${name(r)} 聊天` });
    this.render();
    const el = this.tenantEl(id);
    if (el && !calm()) {
      const b = el.getBoundingClientRect();
      const dx = cx - (b.left + b.width / 2);
      const dy = cy - (b.top + b.height * 0.3);
      el.animate(
        [
          { transform: `translate(${dx}px, ${dy}px) rotate(-6deg)` },
          { transform: 'translate(0, 0) scale(1.14, .82)', offset: 0.62 },
          { transform: 'translate(0, -7px) scale(.95, 1.07)', offset: 0.8 },
          { transform: 'none' },
        ],
        { duration: 600, easing: 'ease-in' },
      );
      this.burst(el, '✦', 2);
      this.frames(el.querySelector('img'), r, 'land', false);
    }
    const lit = this.root.querySelector(`${this.zoom ? '.zstage' : '.bstage'} .room[data-room="${CSS.escape(room.id)}"]`);
    lit?.classList.add('drop-glow');
    setTimeout(() => lit?.classList.remove('drop-glow'), 900);
    this.say(id, fill(pick(spec.say)));
    if (greeter) setTimeout(() => this.say(greeter.id, spec === DROP.visit && greeter === host ? `欢迎欢迎，${name(r)}！` : `嗨，${name(r)}！`), 900);
  }

  // 放大房间里点家具：在场的人（优先选中的那位）走过去用；床只有房主能睡；没人时家具自己冒个符号
  useHot(hotEl) {
    const roomEl = hotEl.closest('.room[data-room]');
    const room = (this.data.floors || []).flat().find((x) => x.id === roomEl?.dataset.room);
    const h = room && (HOTS[room.kind] || []).find((x) => x.id === hotEl.dataset.hot);
    if (!h) return;
    const name = (p) => this.look(p)?.display || p.name;
    const here = this.people().filter((p) => p.room === room.id);
    const awake = here.filter((p) => p.activity !== 'sleep');
    let who = awake.find((p) => p.id === this.selected) || awake.find((p) => p.activity !== 'work' && p.activity !== 'wait') || awake[0];
    if (h.owner) {
      const owner = this.ownerOf(room);
      const mine = owner && here.find((p) => p.id === owner.id);
      if (mine?.activity === 'sleep') return this.say(mine.id, pick(PAT_SLEEP));
      if (!mine) {
        this.burst(hotEl, h.fx);
        return this.floatAt(hotEl, 'say free', owner ? `这是 ${name(owner)} 的床。` : `${h.name}空着。`, SAY_MS);
      }
      who = mine;
    }
    if (!who) {
      this.burst(hotEl, h.fx);
      return this.floatAt(hotEl, 'say free', `${h.name}空着，没人用。`, SAY_MS);
    }
    const before = this.tenantEl(who.id)?.getBoundingClientRect();
    const l = roomEl.querySelector('.people').getBoundingClientRect();
    const hb = hotEl.getBoundingClientRect();
    this.move(who.id, { room: room.id, activity: h.act, activity_label: h.label, _x: clampPct(((hb.left + hb.width / 2 - l.left) / l.width) * 100) });
    this.render();
    const el = this.tenantEl(who.id);
    if (el?.tagName === 'BUTTON' && before && !calm()) {
      const dx = before.left - el.getBoundingClientRect().left;
      const ms = Math.min(1200, Math.max(300, Math.abs(dx) * 2.5));
      el.classList.add('walking');
      el.animate([{ transform: `translateX(${dx}px)` }, { transform: 'none' }], { duration: ms, easing: 'ease-in-out' });
      setTimeout(() => el.classList.remove('walking'), ms);
    }
    const spot = this.root.querySelector(`.zstage [data-hot="${h.id}"]`);
    if (spot) this.burst(spot, h.fx, 2);
    this.say(who.id, pick(h.say));
  }
}
