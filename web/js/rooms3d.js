// 公寓宿舍的伪 3D 场景：一点透视的「房间盒」（后墙、两侧墙、天花板、带纵深的地板），
// 家具按真实的长宽高投影，画出正面 / 顶面 / 侧面，带描边、接触阴影、灯光和窗光。
// 世界坐标：x 0～300（左→右）、y 0～120（天花板→地板）、z 0～1（房间开口→后墙）。
// 全部自绘 SVG，不用外部美术素材；颜色跟房主的主题色（o.tc）走。

export const W = 300;
export const H = 120;
const VX = 150; // 消失点
const VY = 44;
const K = 0.3; // 后墙相对开口缩小的比例

const r2 = (n) => Math.round(n * 100) / 100;
/** 世界坐标 → 画面坐标 */
export const P = (x, y, z = 0) => {
  const s = 1 - K * z;
  return [r2(VX + (x - VX) * s), r2(VY + (y - VY) * s)];
};
const A = (o) =>
  Object.entries(o)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => ` ${k}="${v}"`)
    .join('');
const pts = (ps) => ps.map((p) => p.join(',')).join(' ');
const poly = (ps, fill, more = {}) => `<polygon${A({ points: pts(ps), fill, ...more })}/>`;
const seg = (a, b, stroke, w = 0.5, more = {}) => `<line${A({ x1: a[0], y1: a[1], x2: b[0], y2: b[1], stroke, 'stroke-width': w, 'stroke-opacity': 1, ...more })}/>`;
const ell = (cx, cy, rx, ry, fill, more = {}) => `<ellipse${A({ cx, cy, rx, ry, fill, ...more })}/>`;
const NS = { stroke: 'none' };

/** 正对镜头的面（深度 z） */
const zq = (x0, y0, x1, y1, z, fill, more = {}) => poly([P(x0, y0, z), P(x1, y0, z), P(x1, y1, z), P(x0, y1, z)], fill, more);
/** 侧面（x 固定） */
const xq = (x, y0, y1, z0, z1, fill, more = {}) => poly([P(x, y0, z0), P(x, y0, z1), P(x, y1, z1), P(x, y1, z0)], fill, more);
/** 水平面（y 固定） */
const yq = (x0, x1, y, z0, z1, fill, more = {}) => poly([P(x0, y, z0), P(x1, y, z0), P(x1, y, z1), P(x0, y, z1)], fill, more);
const tint = (c, p) => `color-mix(in srgb, ${c}, #fff ${p}%)`;
const shade = (c, p) => `color-mix(in srgb, ${c}, #000 ${p}%)`;

/** 长方体：自动挑出看得见的面（顶 / 底 / 朝向消失点的侧面），正面最后画；顶边一道高光 */
function box(x0, x1, y0, y1, z0, z1, c, { top, side, under, front = c, rim = 0.22, more = {} } = {}) {
  let s = '';
  if (y1 < VY) s += yq(x0, x1, y1, z0, z1, under || shade(c, 38), more);
  if (y0 > VY) s += yq(x0, x1, y0, z0, z1, top || tint(c, 16), more);
  if (x0 > VX) s += xq(x0, y0, y1, z0, z1, side || shade(c, 26), more);
  if (x1 < VX) s += xq(x1, y0, y1, z0, z1, side || shade(c, 26), more);
  s += zq(x0, y0, x1, y1, z0, front, more);
  if (rim) s += seg(P(x0 + 0.4, y0 + 0.3, z0), P(x1 - 0.4, y0 + 0.3, z0), '#fff', 0.35, { opacity: rim, ...NS, stroke: '#fff' });
  return s;
}
/** 地上的接触阴影 */
const shadow = (u, x0, x1, z0, z1, y = H) => yq(x0 - 3, x1 + 3, y - 0.01, z0 - 0.03, z1 + 0.03, `url(#sh${u})`, NS);
/** 透视里的椭圆（水平面上），用多边形近似 */
function yEllipse(cx, y, cz, rx, rz, fill, more = {}, n = 28) {
  const ps = [];
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    ps.push(P(cx + Math.cos(a) * rx, y, cz + Math.sin(a) * rz));
  }
  return poly(ps, fill, more);
}
/** 竖直平面（z 固定）上的椭圆 */
const zEllipse = (cx, cy, z, rx, ry, fill, more = {}) => {
  const [x, y] = P(cx, cy, z);
  const s = 1 - K * z;
  return ell(x, y, r2(rx * s), r2(ry * s), fill, more);
};

// 可复现的伪随机（每间房自己的书本高度、颜色）
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const SKY = {
  day: ['#7fbfe6', '#cfeaf6', '#93b4c8'],
  dawn: ['#6a76b0', '#f0b48e', '#7a7b98'],
  dusk: ['#4e5590', '#ef9670', '#695a78'],
  night: ['#09102a', '#1f2950', '#141a36'],
};

/** 每间房共用的渐变：阴影、暖光、主题色光、窗光、天空 */
function defs(u, o) {
  const [s0, s1] = SKY[o.ph] || SKY.day;
  const stop = (off, c, op) => `<stop offset="${off}" style="stop-color:${c};stop-opacity:${op}"/>`;
  return (
    `<defs>` +
    `<radialGradient id="sh${u}">${stop(0, '#000', 0.55)}${stop(0.6, '#000', 0.25)}${stop(1, '#000', 0)}</radialGradient>` +
    `<radialGradient id="wm${u}">${stop(0, '#ffd99a', 0.6)}${stop(0.5, '#ffc070', 0.18)}${stop(1, '#ffb050', 0)}</radialGradient>` +
    `<radialGradient id="tg${u}">${stop(0, o.tc, 0.55)}${stop(0.55, o.tc, 0.15)}${stop(1, o.tc, 0)}</radialGradient>` +
    `<radialGradient id="cy${u}">${stop(0, '#7fe8ff', 0.5)}${stop(1, '#7fe8ff', 0)}</radialGradient>` +
    `<linearGradient id="top${u}" x1="0" y1="0" x2="0" y2="1">${stop(0, '#000', 0.5)}${stop(0.4, '#000', 0)}</linearGradient>` +
    `<linearGradient id="bot${u}" x1="0" y1="0" x2="0" y2="1">${stop(0, '#000', 0)}${stop(0.75, '#000', 0)}${stop(1, '#000', 0.35)}</linearGradient>` +
    `<linearGradient id="fl${u}" x1="0" y1="0" x2="0" y2="1">${stop(0, '#000', 0.45)}${stop(0.5, '#000', 0.08)}${stop(1, '#000', 0)}</linearGradient>` +
    `<linearGradient id="sl${u}" x1="0" y1="0" x2="1" y2="0">${stop(0, '#000', 0.4)}${stop(1, '#000', 0.05)}</linearGradient>` +
    `<linearGradient id="sr${u}" x1="1" y1="0" x2="0" y2="0">${stop(0, '#000', 0.4)}${stop(1, '#000', 0.05)}</linearGradient>` +
    `<linearGradient id="bm${u}" x1="0" y1="0" x2="0" y2="1">${stop(0, '#fff6dc', o.night ? 0 : 0.34)}${stop(1, '#fff6dc', 0)}</linearGradient>` +
    `<linearGradient id="cone${u}" x1="0" y1="0" x2="0" y2="1">${stop(0, '#ffe2a8', 0.32)}${stop(1, '#ffe2a8', 0)}</linearGradient>` +
    `<linearGradient id="sky${u}" x1="0" y1="0" x2="0" y2="1">${stop(0, s0, 1)}${stop(1, s1, 1)}</linearGradient>` +
    `<linearGradient id="gl${u}" x1="0" y1="0" x2="1" y2="1">${stop(0, '#fff', 0.35)}${stop(0.45, '#fff', 0)}</linearGradient>` +
    `<filter id="bl${u}" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="1.6"/></filter>` +
    `</defs>`
  );
}

/** 房间盒：后墙、两侧墙、天花板、地板（地板纹路朝消失点汇聚），墙角压暗 */
function shellBox(u, { wall, side, ceil, floor, plank = 16, seams = true }) {
  let s = zq(0, 0, W, H, 1, wall, NS);
  s += poly([P(0, 0, 0), P(0, 0, 1), P(0, H, 1), P(0, H, 0)], side, NS) + poly([P(W, 0, 0), P(W, 0, 1), P(W, H, 1), P(W, H, 0)], side, NS);
  s += poly([P(0, 0, 0), P(W, 0, 0), P(W, 0, 1), P(0, 0, 1)], ceil, NS);
  s += poly([P(0, H, 1), P(W, H, 1), P(W, H, 0), P(0, H, 0)], floor, NS);
  // 地板：纵向木条朝消失点汇聚，横向错缝
  if (plank) {
    for (let x = 0; x <= W; x += plank) s += seg(P(x, H, 0), P(x, H, 1), '#000', 0.35, { opacity: 0.22, ...NS, stroke: '#000' });
    if (seams) {
      const r = rng(7);
      for (let x = 0; x < W; x += plank) for (let z = 0.1 + r() * 0.3; z < 1; z += 0.35 + r() * 0.2) s += seg(P(x, H, z), P(x + plank, H, z), '#000', 0.3, { opacity: 0.18 });
    }
  }
  // 环境光遮蔽：天花板压暗、墙角线、地板靠后墙更暗
  s += zq(0, 0, W, H, 1, `url(#top${u})`, NS) + zq(0, 0, W, H, 1, `url(#bot${u})`, NS);
  s += poly([P(0, H, 1), P(W, H, 1), P(W, H, 0), P(0, H, 0)], `url(#fl${u})`, NS);
  s += poly([P(0, 0, 0), P(0, 0, 1), P(0, H, 1), P(0, H, 0)], `url(#sl${u})`, NS) + poly([P(W, 0, 0), P(W, 0, 1), P(W, H, 1), P(W, H, 0)], `url(#sr${u})`, NS);
  for (const [a, b] of [
    [P(0, 0, 1), P(0, H, 1)],
    [P(W, 0, 1), P(W, H, 1)],
    [P(0, H, 1), P(W, H, 1)],
    [P(0, 0, 1), P(W, 0, 1)],
  ])
    s += seg(a, b, '#000', 0.8, { opacity: 0.35, stroke: '#000' });
  return s;
}

/** 踢脚线、护墙板顶线（沿后墙和两侧墙） */
function trim(y0, y1, c, d = 0.012) {
  return (
    box(0, W, y0, y1, 1 - d, 1, c, { rim: 0.25 }) +
    poly([P(0.01, y0, 0), P(0.01, y0, 1), P(0.01, y1, 1), P(0.01, y1, 0)], shade(c, 10), NS) +
    poly([P(W - 0.01, y0, 0), P(W - 0.01, y0, 1), P(W - 0.01, y1, 1), P(W - 0.01, y1, 0)], shade(c, 10), NS)
  );
}

/** 后墙上的窗（凹进墙里，四边有窗洞的厚度），窗外的天和远处的楼 */
function backWindow(u, o, x0, y0, x1, y1, { frame = '#e9e2d3', arch = false, blinds = 0, mull = 2 } = {}) {
  const zd = 1.06;
  let s = '';
  // 窗外
  const [a, b] = [P(x0, y0, zd), P(x1, y1, zd)];
  s += `<g clip-path="url(#wc${u})">`;
  s += `<rect x="${a[0]}" y="${a[1]}" width="${r2(b[0] - a[0])}" height="${r2(b[1] - a[1])}" fill="url(#sky${u})"/>`;
  const far = (SKY[o.ph] || SKY.day)[2];
  const r = rng(x0 * 13 + 3);
  for (let x = a[0]; x < b[0]; ) {
    const bw = 4 + r() * 7;
    const bh = (b[1] - a[1]) * (0.15 + r() * 0.35);
    s += `<rect x="${r2(x)}" y="${r2(b[1] - bh)}" width="${r2(bw)}" height="${r2(bh)}" fill="${far}" opacity=".9"/>`;
    if (o.night) for (let k = 0; k < 3; k++) if (r() > 0.4) s += `<rect x="${r2(x + 1 + r() * (bw - 2))}" y="${r2(b[1] - bh + 1.5 + r() * (bh - 3))}" width=".9" height=".9" fill="#ffd774"/>`;
    x += bw + 0.6;
  }
  if (o.night) s += `<circle cx="${r2(a[0] + (b[0] - a[0]) * 0.72)}" cy="${r2(a[1] + (b[1] - a[1]) * 0.22)}" r="2.6" fill="#f6eab0"/><circle cx="${r2(a[0] + (b[0] - a[0]) * 0.72)}" cy="${r2(a[1] + (b[1] - a[1]) * 0.22)}" r="6" fill="#f6eab0" opacity=".15"/>`;
  else s += ell(r2(a[0] + (b[0] - a[0]) * 0.3), r2(a[1] + (b[1] - a[1]) * 0.25), r2((b[0] - a[0]) * 0.15), 1.6, '#fff', { opacity: 0.75 });
  s += `</g>`;
  // 窗洞的厚度：看得见的两条边
  const reveal = shade(frame, 22);
  if (x0 < VX) s += xq(x0, y0, y1, 1, zd, reveal, NS);
  if (x1 > VX) s += xq(x1, y0, y1, 1, zd, reveal, NS);
  s += yq(x0, x1, y0, 1, zd, shade(frame, 35), NS);
  // 窗框、窗棂
  const fz = 1.04;
  const mx = (x0 + x1) / 2;
  s += `<g stroke="${shade(frame, 45)}" stroke-width=".3">`;
  s += zq(x0, y0, x0 + mull, y1, fz, frame) + zq(x1 - mull, y0, x1, y1, fz, frame) + zq(x0, y0, x1, y0 + mull, fz, frame) + zq(x0, y1 - mull, x1, y1, fz, frame);
  s += zq(mx - mull / 2, y0, mx + mull / 2, y1, fz, frame) + zq(x0, (y0 + y1) / 2 - 0.8, x1, (y0 + y1) / 2 + 0.8, fz, frame);
  s += `</g>`;
  // 玻璃反光
  s += poly([P(x0 + 4, y1, fz), P(x0 + 18, y0, fz), P(x0 + 26, y0, fz), P(x0 + 12, y1, fz)], '#fff', { opacity: 0.1, ...NS });
  // 百叶
  for (let i = 0; i < blinds; i++) {
    const y = y0 + 2 + i * ((y1 - y0 - 4) / blinds);
    s += zq(x0 + 1, y, x1 - 1, y + 1.4, 1.01, '#c7cdd6', { opacity: 0.9, stroke: '#5b6270', 'stroke-width': 0.15 });
  }
  // 窗台
  s += box(x0 - 4, x1 + 4, y1, y1 + 2.4, 0.95, 1, frame, { rim: 0.4 });
  if (arch) s += zq(x0 - 3, y0 - 5, x1 + 3, y0, 1, shade(frame, 10), { stroke: shade(frame, 45), 'stroke-width': 0.3 });
  return { svg: s, clip: `<clipPath id="wc${u}"><rect x="${a[0]}" y="${a[1]}" width="${r2(b[0] - a[0])}" height="${r2(b[1] - a[1])}"/></clipPath>` };
}

/** 白天从后墙窗户照到地板上的光斑（斜投下来） */
function windowBeam(u, o, x0, x1, y1) {
  if (o.night) return '';
  const top = [P(x0, y1, 1), P(x1, y1, 1)];
  const bot = [P(x1 + 34, H, 0.42), P(x0 + 34, H, 0.42)];
  return poly([...top, ...bot], `url(#bm${u})`, { ...NS, style: 'mix-blend-mode:screen' }) + yq(x0 + 14, x1 + 30, H - 0.02, 0.46, 0.84, '#fff4d8', { opacity: 0.16, ...NS });
}

/** 吊灯：天花板挂下来，开着时一圈暖光 + 照到地上的光池 */
function pendant(u, o, x, z, { drop = 22, c = '#c79a4a', glow = true } = {}) {
  const top = P(x, 0, z);
  const at = P(x, drop, z);
  let s = seg(top, at, '#1c1712', 0.5, { stroke: '#1c1712' });
  s += poly([P(x - 8, drop + 6, z), P(x - 3, drop, z), P(x + 3, drop, z), P(x + 8, drop + 6, z)], c, { stroke: shade(c, 50), 'stroke-width': 0.35 });
  s += yEllipse(x, drop + 6, z, 8, 0.03, o.on ? '#fff3cf' : shade(c, 40), NS);
  if (o.on && glow) {
    s += poly([P(x - 7, drop + 6, z), P(x + 7, drop + 6, z), P(x + 34, H, z - 0.08), P(x - 34, H, z - 0.08)], `url(#cone${u})`, NS);
    s += yEllipse(x, H - 0.03, z - 0.06, 46, 0.22, `url(#wm${u})`, NS);
    const [cx, cy] = P(x, drop + 7, z);
    s += ell(cx, cy, 14, 10, `url(#wm${u})`, NS);
  }
  return s;
}

/** 床：床头靠在左墙，侧着朝向镜头；返回睡觉小人的头和被子的位置 */
export const BED = { x0: 8, x1: 112, y: 98, z0: 0.16, z1: 0.6 };
function bed3d(u, { frame, sheet = '#f3efe6', duvet, pillow = '#ffffff', head = 66, legs = '#2a1c14', glow }) {
  const { x0, x1, y, z0, z1 } = BED;
  let s = shadow(u, x0, x1, z0, z1);
  if (glow) s += yq(x0 - 4, x1 + 6, H - 0.02, z0 - 0.05, z1 + 0.05, `url(#tg${u})`, NS);
  // 床头板
  s += box(2, 8, head, H, z0 - 0.02, z1 + 0.04, frame, { rim: 0.3 });
  s += xq(8.01, head + 4, H - 6, z0 + 0.02, z1, tint(frame, 8), { stroke: shade(frame, 40), 'stroke-width': 0.25 });
  // 床架、床垫
  s += box(x0, x1, y + 8, H - 3, z0, z1, frame);
  s += box(x0 + 2, x0 + 5, H - 3, H, z0 + 0.01, z0 + 0.04, legs, { rim: 0 }) + box(x1 - 5, x1 - 2, H - 3, H, z0 + 0.01, z0 + 0.04, legs, { rim: 0 });
  s += box(x0 + 1, x1 - 1, y, y + 8, z0 + 0.01, z1 - 0.01, sheet, { top: tint(sheet, 30) });
  // 枕头
  s += box(x0 + 4, x0 + 28, y - 5, y, z0 + 0.1, z1 - 0.08, pillow, { rim: 0.5, top: tint(pillow, 20) });
  // 被子（没人睡时铺好，被角掀开一点）
  s += box(x0 + 30, x1 - 1, y - 2.5, y + 9, z0 - 0.005, z1 - 0.005, duvet, { top: tint(duvet, 14) });
  s += poly([P(x0 + 30, y - 2.5, z0 - 0.005), P(x0 + 40, y - 2.5, z0 - 0.005), P(x0 + 30, y - 2.5, z0 + 0.12)], tint(duvet, 45), NS);
  for (const k of [0.33, 0.66]) s += seg(P(x0 + 30 + (x1 - x0 - 31) * k, y - 2.5, z1 - 0.01), P(x0 + 30 + (x1 - x0 - 31) * k, y - 2.5, z0), '#000', 0.3, { opacity: 0.15, stroke: '#000' });
  return s;
}
export function bedAnchor() {
  const { x0, x1, y, z0, z1 } = BED;
  const [hx, hy] = P(x0 + 16, y - 7, (z0 + z1) / 2);
  const quilt = [
    [P(x0 + 26, y - 4, z0 + 0.01), P(x1 - 1, y - 4, z0 + 0.01), P(x1 - 1, y - 4, z1 - 0.02), P(x0 + 26, y - 4, z1 - 0.02)],
    [P(x0 + 26, y - 4, z0 + 0.01), P(x1 - 1, y - 4, z0 + 0.01), P(x1 - 1, y + 8, z0 + 0.01), P(x0 + 26, y + 8, z0 + 0.01)],
  ].map(pts);
  const a = P(2, 66, z0 - 0.02);
  const b = P(x1, H, z0);
  return { hx, hy, hr: 9.5, quilt, hot: { x: a[0], y: a[1], w: r2(b[0] - a[0]), h: r2(b[1] - a[1]) } };
}

/** 地毯：平铺在地上，带边框和图案 */
function rug3d(x0, x1, z0, z1, c, edge, { medallion = true } = {}) {
  let s = yq(x0, x1, H - 0.05, z0, z1, c, { stroke: shade(c, 40), 'stroke-width': 0.3 });
  s += yq(x0 + 3, x1 - 3, H - 0.06, z0 + 0.02, z1 - 0.02, 'none', { stroke: edge, 'stroke-width': 0.9 });
  s += yq(x0 + 6, x1 - 6, H - 0.06, z0 + 0.04, z1 - 0.04, 'none', { stroke: edge, 'stroke-width': 0.35, 'stroke-dasharray': '1.2 1' });
  if (medallion) {
    const cx = (x0 + x1) / 2;
    const cz = (z0 + z1) / 2;
    s += yEllipse(cx, H - 0.07, cz, (x1 - x0) * 0.22, (z1 - z0) * 0.3, edge, { opacity: 0.85, ...NS });
    s += yEllipse(cx, H - 0.08, cz, (x1 - x0) * 0.14, (z1 - z0) * 0.18, c, NS);
    s += yEllipse(cx, H - 0.09, cz, (x1 - x0) * 0.05, (z1 - z0) * 0.07, edge, NS);
  }
  // 流苏
  for (let x = x0 + 2; x < x1; x += 3) s += seg(P(x, H - 0.05, z0), P(x, H - 0.05, z0 - 0.025), tint(edge, 30), 0.35, { stroke: tint(edge, 30) });
  return s;
}

/** 盆栽：花盆是圆柱，叶子一片片画 */
function plant3d(u, x, z, s = 1, pot = '#b7643c', leaf = '#3f7f4f') {
  const k = (n) => n * s;
  const [bx, by] = P(x, H, z);
  const sc = 1 - K * z;
  let o = shadow(u, x - k(8), x + k(8), z - 0.04, z + 0.04);
  o += `<g transform="translate(${bx} ${by}) scale(${r2(sc * s)})" stroke="#1a2a1c" stroke-width=".35">`;
  o += `<path d="M-7 -14h14l-2 14h-10z" fill="${pot}"/><path d="M-7.5 -16h15v2.5h-15z" fill="${tint(pot, 15)}"/><path d="M3 -14h4l-2 14h-3z" fill="#000" opacity=".2" stroke="none"/>`;
  const leaves = [
    [-14, -30, -40],
    [12, -34, 38],
    [-6, -46, -12],
    [6, -50, 14],
    [-16, -48, -32],
    [16, -46, 30],
    [0, -60, 0],
    [-10, -36, -60],
    [10, -24, 62],
  ];
  for (const [lx, ly, rot] of leaves) o += `<path d="M0 -14Q${lx * 0.4} ${ly * 0.5} ${lx} ${ly}" fill="none" stroke="#2c4a2f" stroke-width=".7"/><ellipse cx="${lx}" cy="${ly}" rx="4.4" ry="8" fill="${ly < -44 ? tint(leaf, 18) : leaf}" transform="rotate(${rot} ${lx} ${ly})"/><path d="M${lx} ${ly + 7}L${lx} ${ly - 6}" stroke="#fff" stroke-opacity=".25" stroke-width=".5" transform="rotate(${rot} ${lx} ${ly})"/>`;
  return o + '</g>';
}

/** 挂在后墙上的画框 */
function frame3d(x0, y0, x1, y1, art, { c = '#3a2716', mat = '#efe6d2', z = 1 } = {}) {
  let s = box(x0, x1, y0, y1, z - 0.012, z, c, { rim: 0.35 });
  s += zq(x0 + 1.5, y0 + 1.5, x1 - 1.5, y1 - 1.5, z - 0.0125, mat, NS);
  s += `<g>${art(P(x0 + 3, y0 + 3, z - 0.013), P(x1 - 3, y1 - 3, z - 0.013))}</g>`;
  return s + poly([P(x0 + 2, y0 + 2, z - 0.014), P(x0 + (x1 - x0) * 0.6, y0 + 2, z - 0.014), P(x0 + 2, y0 + (y1 - y0) * 0.6, z - 0.014)], '#fff', { opacity: 0.12, ...NS });
}
const landscape = (sky, hill) => (a, b) => {
  const w = b[0] - a[0];
  const h = b[1] - a[1];
  return (
    `<rect x="${a[0]}" y="${a[1]}" width="${r2(w)}" height="${r2(h)}" fill="${sky}"/>` +
    `<path d="M${a[0]} ${b[1]}L${r2(a[0] + w * 0.3)} ${r2(a[1] + h * 0.45)}L${r2(a[0] + w * 0.55)} ${r2(a[1] + h * 0.7)}L${r2(a[0] + w * 0.8)} ${r2(a[1] + h * 0.35)}L${b[0]} ${r2(a[1] + h * 0.6)}V${b[1]}z" fill="${hill}"/>` +
    `<path d="M${a[0]} ${b[1]}L${r2(a[0] + w * 0.5)} ${r2(a[1] + h * 0.78)}L${b[0]} ${r2(a[1] + h * 0.85)}V${b[1]}z" fill="${shade(hill, 30)}"/>` +
    `<circle cx="${r2(a[0] + w * 0.75)}" cy="${r2(a[1] + h * 0.25)}" r="${r2(h * 0.1)}" fill="#f7e3a6"/>`
  );
};

/** 书柜：贴着后墙的整面柜子，一层层摆满书和小摆件 */
function bookcase(u, o, x0, x1, y0, rows, { wood = '#4a2e1c', inner = '#26180f', seed = 1, deco = [] } = {}) {
  const z0 = 0.84;
  const r = rng(seed);
  const books = ['#7d2a26', '#2f4f6f', '#3f6a4a', '#c9a861', '#5a3b6e', '#e7dcc4', '#93582e', '#1f2d3d', o.tc];
  let s = shadow(u, x0, x1, z0, 1);
  s += zq(x0, y0, x1, H, 0.995, inner, NS);
  const step = (H - 6 - y0) / rows;
  for (let i = 0; i < rows; i++) {
    const sy = y0 + 4 + (i + 1) * step; // 这一层的板面
    let x = x0 + 4;
    const dx = deco[i];
    while (x < x1 - 6) {
      if (dx && x > dx.at && x < dx.at + 4) {
        s += dx.draw(x, sy);
        x += dx.w;
        continue;
      }
      if (r() < 0.08) {
        // 平放的一摞
        const n = 2 + Math.floor(r() * 3);
        for (let k = 0; k < n; k++) s += box(x, x + 12, sy - (k + 1) * 2.2, sy - k * 2.2, 0.88, 0.97, books[Math.floor(r() * books.length)], { rim: 0.3 });
        x += 13.5;
        continue;
      }
      const bw = 2.2 + r() * 2.2;
      const bh = step * (0.55 + r() * 0.32);
      const c = books[Math.floor(r() * books.length)];
      s += box(x, x + bw, sy - bh, sy, 0.87 + r() * 0.03, 0.98, c, { rim: 0.15 });
      if (r() > 0.4) s += zq(x + 0.3, sy - bh * 0.8, x + bw - 0.3, sy - bh * 0.72, 0.869, '#e9cf8a', { opacity: 0.7, ...NS });
      x += bw + 0.25;
    }
    s += box(x0 + 2, x1 - 2, sy, sy + 2.2, z0 + 0.01, 0.995, wood, { rim: 0.3 });
  }
  // 两侧立板、顶板、底座
  s += box(x0, x0 + 3, y0, H, z0, 1, wood) + box(x1 - 3, x1, y0, H, z0, 1, wood);
  s += box(x0 - 2, x1 + 2, y0 - 3, y0 + 1, z0 - 0.015, 1, tint(wood, 6), { rim: 0.4 });
  s += box(x0, x1, H - 6, H, z0, 1, shade(wood, 8));
  return s;
}

// ------------------------------------------------------------------------------------------------
// 各间房的主题。按房号轮换；同一个主题里的小物件颜色跟房主主题色走。
// ------------------------------------------------------------------------------------------------

/** 书房：深绿壁纸、胡桃木护墙板、整面书柜和梯子、拱形窗、写字台和绿罩台灯、地球仪、皮扶手椅、波斯地毯 */
function study(u, o) {
  const wall = '#2b4a3c';
  const wood = '#5a3722';
  let s = shellBox(u, { wall, side: shade(wall, 18), ceil: '#1d2a24', floor: '#7a5233', plank: 12 });
  // 壁纸暗纹
  let pat = '';
  for (let x = 10; x < W; x += 16) for (let y = 8; y < 80; y += 14) pat += zq(x - 1.2, y + ((x / 16) % 2) * 7, x + 1.2, y + 2.4 + ((x / 16) % 2) * 7, 1, '#d8c48a', { opacity: 0.09, ...NS, transform: '' });
  s += pat;
  // 天花板的方格藻井
  for (const z of [0.12, 0.42, 0.72]) s += box(0, W, 0, 3, z, z + 0.05, '#3a2516', { under: '#24170e', rim: 0 });
  for (const x of [40, 260]) s += box(x - 2, x + 2, 0, 3.2, 0, 1, '#3a2516', { under: '#24170e', rim: 0 });
  s += box(0, W, 0, 4, 0.985, 1, '#c9a861', { rim: 0.5 }); // 顶角线
  // 护墙板
  s += zq(0, 82, W, H, 0.999, wood, NS);
  for (let x = 6; x < W - 10; x += 26) s += zq(x, 87, x + 22, H - 8, 0.998, shade(wood, 12), { stroke: tint(wood, 25), 'stroke-width': 0.35 });
  s += poly([P(0, 82, 0), P(0, 82, 1), P(0, H, 1), P(0, H, 0)], shade(wood, 22), NS) + poly([P(W, 82, 0), P(W, 82, 1), P(W, H, 1), P(W, H, 0)], shade(wood, 22), NS);
  s += trim(80, 82.5, '#3e2516') + trim(H - 4, H, '#2e1b10');
  // 拱形窗 + 窗帘
  const win = backWindow(u, o, 26, 22, 92, 74, { frame: '#d9ccb0', arch: true });
  s += win.svg;
  for (const [cx, dir] of [
    [18, 1],
    [100, -1],
  ]) {
    const c = shade(o.tc, 25);
    s += `<g stroke="${shade(c, 45)}" stroke-width=".3">` + zq(cx - 6, 14, cx + 6, 80, 0.97, c) + `</g>`;
    for (let k = -4; k <= 4; k += 2.6) s += zq(cx + k, 14, cx + k + 0.9, 80, 0.969, '#000', { opacity: 0.18, ...NS });
    s += zq(cx - 6, 14, cx + 6, 17, 0.968, tint(c, 25), NS);
    s += zq(cx - 5 * dir - 1, 58, cx - 5 * dir + 1, 62, 0.966, '#c9a861', NS); // 绑带
  }
  s += box(8, 110, 12, 14, 0.96, 0.97, '#a07c3c', { rim: 0.5 }); // 窗帘杆
  s += windowBeam(u, o, 26, 92, 74);
  // 两幅画
  s += frame3d(108, 24, 140, 46, landscape('#9cc0c9', '#5f7f58'), { c: '#8a6a2a' });
  s += frame3d(146, 30, 162, 50, landscape('#e1c9a0', '#8b5a3c'), { c: '#3a2716' });
  // 壁灯
  for (const x of [104, 166]) {
    s += box(x - 1, x + 1, 50, 56, 0.985, 1, '#a07c3c');
    s += zEllipse(x, 48, 0.98, 3, 4, o.on ? '#ffe9b8' : '#d8c8a8', { stroke: '#7a5a2a', 'stroke-width': 0.3 });
    if (o.on) s += zEllipse(x, 48, 0.99, 14, 16, `url(#wm${u})`, NS);
  }
  // 书柜 + 梯子
  s += bookcase(u, o, 176, 292, 8, 4, {
    seed: 11,
    deco: [
      null,
      { at: 230, w: 14, draw: (x, y) => zEllipse(x + 6, y - 5, 0.92, 5, 5, '#2f5f7f', { stroke: '#1a2a3a', 'stroke-width': 0.3 }) + zq(x + 5, y - 1, x + 7, y, 0.92, '#a07c3c', NS) },
      { at: 200, w: 12, draw: (x, y) => box(x + 1, x + 10, y - 9, y, 0.9, 0.96, '#c9a861', { rim: 0.4 }) + zq(x + 2.5, y - 7.5, x + 8.5, y - 1.5, 0.899, '#2c4a3e', NS) },
      null,
    ],
  });
  s += seg(P(196, 4, 0.8), P(204, H, 0.62), '#3e2516', 1.4, { stroke: '#3e2516' }) + seg(P(212, 4, 0.8), P(220, H, 0.62), '#3e2516', 1.4, { stroke: '#3e2516' });
  for (let y = 14; y < H; y += 12) {
    const t = y / H;
    s += seg(P(196 + 8 * t, y, 0.8 - 0.18 * t), P(212 + 8 * t, y, 0.8 - 0.18 * t), '#5a3722', 1, { stroke: '#5a3722' });
  }
  s += box(194, 214, 3, 6, 0.8, 0.86, '#a07c3c', { rim: 0.4 }); // 梯子的滑轨
  // 地毯
  s += rug3d(70, 238, 0.12, 0.72, '#7a2421', '#d2a65a');
  // 写字台（靠后墙中间）、椅子、台灯、书本
  s += shadow(u, 112, 170, 0.66, 0.9);
  s += box(112, 170, 92, 95, 0.66, 0.9, wood, { top: tint(wood, 22), rim: 0.4 });
  s += box(114, 132, 95, H, 0.68, 0.88, shade(wood, 6)) + box(150, 168, 95, H, 0.68, 0.88, shade(wood, 6));
  for (const [x, y] of [
    [116, 99],
    [116, 107],
    [152, 99],
    [152, 107],
  ])
    s += zq(x, y, x + 14, y + 6, 0.679, shade(wood, 15), { stroke: tint(wood, 20), 'stroke-width': 0.3 }) + zq(x + 6, y + 2.6, x + 8, y + 3.4, 0.678, '#c9a861', NS);
  s += yq(118, 138, 91.9, 0.72, 0.84, '#efe6d2', NS) + yq(122, 136, 91.8, 0.74, 0.82, '#e6dcc4', NS) + seg(P(129, 91.7, 0.74), P(129, 91.7, 0.82), '#9a8a6a', 0.3, { stroke: '#9a8a6a' }); // 摊开的书
  s += seg(P(140, 91.5, 0.8), P(146, 84, 0.84), '#efe6d2', 0.6, { stroke: '#efe6d2' }); // 羽毛笔
  // 绿罩台灯
  s += box(157, 161, 90, 92, 0.78, 0.82, '#a07c3c') + box(158.6, 159.4, 80, 90, 0.79, 0.81, '#a07c3c', { rim: 0 });
  s += poly([P(152, 82, 0.8), P(155, 77, 0.8), P(163, 77, 0.8), P(166, 82, 0.8)], '#1f6b45', { stroke: '#0f3a24', 'stroke-width': 0.35 }) + poly([P(153, 81, 0.8), P(156, 77.6, 0.8), P(158, 77.6, 0.8), P(156, 81, 0.8)], '#fff', { opacity: 0.25, ...NS });
  if (o.on) s += poly([P(152, 82, 0.8), P(166, 82, 0.8), P(176, 92, 0.8), P(142, 92, 0.8)], '#fff1c0', { opacity: 0.25, ...NS }) + yEllipse(159, 91.6, 0.78, 16, 0.08, `url(#wm${u})`, NS);
  // 椅子（背对镜头）
  s += shadow(u, 130, 152, 0.54, 0.66);
  s += box(131, 151, 98, 101, 0.54, 0.66, '#5c1f1a', { top: '#7a2a22' });
  for (const x of [132, 148]) s += box(x, x + 2, 101, H, 0.55, 0.57, '#2e1b10', { rim: 0 });
  s += box(131, 151, 76, 98, 0.53, 0.55, '#5c1f1a', { rim: 0.3 }) + zq(134, 79, 148, 95, 0.529, '#6e2620', { stroke: '#3a1210', 'stroke-width': 0.3 });
  for (let x = 136; x < 148; x += 4) for (let y = 82; y < 94; y += 4) s += zEllipse(x, y, 0.528, 0.5, 0.5, '#c9a861', NS);
  // 地球仪（右前方）
  s += shadow(u, 248, 266, 0.3, 0.4);
  s += box(255, 259, 110, H, 0.33, 0.37, '#3e2516', { rim: 0.3 }) + yEllipse(257, H - 0.1, 0.35, 8, 0.05, '#3e2516', NS);
  s += zEllipse(257, 100, 0.35, 9, 9, '#2f6f8f', { stroke: '#163444', 'stroke-width': 0.4 });
  s += poly([P(251, 95, 0.349), P(256, 93, 0.349), P(259, 97, 0.349), P(256, 102, 0.349), P(252, 100, 0.349)], '#7fae6a', NS) + poly([P(260, 101, 0.349), P(264, 99, 0.349), P(264, 105, 0.349), P(261, 106, 0.349)], '#7fae6a', NS);
  s += zEllipse(257, 100, 0.348, 9.6, 9.6, 'none', { stroke: '#c9a861', 'stroke-width': 0.9 }) + zEllipse(254, 96, 0.347, 3, 2, '#fff', { opacity: 0.3, ...NS });
  // 皮扶手椅（右侧，斜着朝屋里）
  s += shadow(u, 266, 298, 0.42, 0.64);
  s += box(268, 296, 100, 112, 0.42, 0.64, '#6e3420', { top: '#8a4428' });
  s += box(270, 298, 74, 104, 0.6, 0.66, '#5c2a18', { top: '#7a3a22' }) + zEllipse(284, 75, 0.6, 14, 3, '#6e3420', NS);
  s += box(266, 271, 92, 112, 0.42, 0.64, '#5c2a18', { top: '#8a4428' }) + yq(266, 271, 91.9, 0.42, 0.64, '#9a5030', NS);
  s += box(272, 294, 98, 101, 0.44, 0.6, '#8a4428', { top: '#a85a34', rim: 0.4 });
  for (const x of [270, 293]) s += box(x, x + 2, 112, H, 0.44, 0.46, '#2e1b10', { rim: 0 });
  for (let x = 274; x < 294; x += 5) s += zEllipse(x, 90, 0.599, 0.5, 0.5, '#c9a861', NS);
  // 床（左）与床头柜
  s += shadow(u, 4, 28, 0.62, 0.8);
  s += box(4, 26, 98, H, 0.63, 0.8, wood, { top: tint(wood, 20) });
  s += zq(7, 102, 23, 110, 0.629, shade(wood, 14), { stroke: tint(wood, 20), 'stroke-width': 0.3 }) + zEllipse(15, 106, 0.628, 0.8, 0.8, '#c9a861', NS);
  s += box(12, 18, 94, 98, 0.7, 0.74, '#d8c8a8') + poly([P(10, 86, 0.72), P(12, 81, 0.72), P(18, 81, 0.72), P(20, 86, 0.72)], o.on ? '#fbe6b6' : '#d8c8a8', { stroke: '#7a5a2a', 'stroke-width': 0.3 });
  if (o.on) s += zEllipse(15, 85, 0.72, 16, 14, `url(#wm${u})`, NS);
  s += bed3d(u, { frame: '#4a2e1c', duvet: o.tc, sheet: '#f1ebdc' });
  // 前景：右前角的大盆栽（压在画面最前，拉出纵深）
  s += plant3d(u, 290, 0.04, 1, '#8a5a32', '#3a6e46');
  s += pendant(u, o, 170, 0.46, { c: '#a07c3c' });
  // 光里的浮尘
  if (!o.night) for (const [x, y] of [[70, 80], [86, 88], [96, 74], [104, 92], [80, 96]]) { const [px, py] = P(x, y, 0.7); s += `<circle cx="${px}" cy="${py}" r=".45" fill="#fff6d8" class="twinkle"/>`; }
  return { svg: s, clip: win.clip };
}

/** 赛博机房：深蓝墙、吸音板、主题色灯带、三联屏电竞桌、电竞椅、服务器机柜、手办展示柜、霓虹灯牌、发光地台床 */
function neon(u, o) {
  const tc = o.tc;
  const cy = '#57d8ff';
  const wall = '#151c2e';
  let s = shellBox(u, { wall, side: shade(wall, 15), ceil: '#0c111d', floor: '#121827', plank: 20, seams: false });
  // 地板：方砖缝 + 反光
  for (let z = 0.1; z < 1; z += 0.15) s += seg(P(0, H, z), P(W, H, z), '#7fa8ff', 0.3, { opacity: 0.12, stroke: '#7fa8ff' });
  // 天花板：灯槽 + 线槽
  s += poly([P(0, 0, 0.92), P(W, 0, 0.92), P(W, 0, 1), P(0, 0, 1)], '#0a0e17', NS);
  s += box(0, W, 0, 2.5, 0.97, 1, '#0a0e17', { rim: 0 }) + zq(0, 2.5, W, 3.6, 0.969, tc, NS) + zq(0, 3.6, W, 22, 0.968, `url(#tg${u})`, { ...NS, opacity: 0.6 });
  for (const x of [60, 150, 240]) s += box(x - 18, x + 18, 0, 1.4, 0.2, 0.6, '#1b2234', { rim: 0 }) + yq(x - 16, x + 16, 1.45, 0.24, 0.56, o.on ? '#dff4ff' : '#33405a', NS);
  // 吸音板（后墙右半：六边形）
  for (let i = 0; i < 6; i++)
    for (let j = 0; j < 3; j++) {
      const cx = 196 + i * 15 + (j % 2) * 7.5;
      const cy0 = 22 + j * 12;
      const hex = [0, 1, 2, 3, 4, 5].map((k) => P(cx + Math.cos((k * Math.PI) / 3) * 7, cy0 + Math.sin((k * Math.PI) / 3) * 6.4, 0.99));
      s += poly(hex, (i + j) % 4 === 1 ? shade(tc, 35) : '#20283c', { stroke: '#0a0e17', 'stroke-width': 0.4 });
    }
  // 窗（左，百叶半拉）
  const win = backWindow(u, o, 30, 18, 84, 62, { frame: '#2a3246', blinds: 6, mull: 1.6 });
  s += win.svg;
  s += windowBeam(u, o, 30, 84, 62);
  // 霓虹灯牌：尖括号 + 斜杠
  const neonPath = (d, c) => `<path d="${d}" fill="none" stroke="${c}" stroke-opacity="1" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" opacity=".55" filter="url(#bl${u})"/><path d="${d}" fill="none" stroke="${tint(c, 55)}" stroke-opacity="1" stroke-width=".9" stroke-linecap="round" stroke-linejoin="round"/>`;
  {
    const q = (x, y) => P(x, y, 0.995).join(' ');
    s += neonPath(`M${q(116, 22)}L${q(106, 30)}L${q(116, 38)}M${q(138, 22)}L${q(148, 30)}L${q(138, 38)}`, o.on || o.night ? cy : shade(cy, 30));
    s += neonPath(`M${q(132, 20)}L${q(122, 40)}`, o.on || o.night ? tc : shade(tc, 30));
  }
  // 海报
  s += zq(160, 14, 182, 44, 0.995, '#1d1530', { stroke: '#000', 'stroke-width': 0.3 }) + zq(162, 16, 180, 34, 0.994, shade(tc, 20), NS);
  s += `<g opacity=".9">` + zEllipse(171, 25, 0.993, 5, 5, tint(tc, 40), NS) + zq(163, 36, 179, 38, 0.993, cy, NS) + zq(163, 40, 174, 41, 0.993, '#e6e9f2', NS) + `</g>`;
  // 地上发光的地毯（主题色）
  s += yq(96, 210, H - 0.04, 0.2, 0.64, '#0d1220', { stroke: tc, 'stroke-width': 0.6 });
  s += yq(100, 206, H - 0.05, 0.24, 0.6, 'none', { stroke: cy, 'stroke-width': 0.3, 'stroke-dasharray': '3 2' });
  s += yEllipse(153, H - 0.06, 0.42, 30, 0.12, `url(#tg${u})`, NS);
  // 电竞桌（靠后墙）
  s += shadow(u, 100, 206, 0.74, 0.94);
  s += box(100, 206, 90, 93, 0.74, 0.94, '#1c2333', { top: '#2a3348', rim: 0.4 });
  s += zq(100, 93, 206, 94.2, 0.739, tc, NS) + yq(102, 204, H - 0.04, 0.72, 0.9, `url(#tg${u})`, NS);
  for (const x of [104, 198]) s += box(x, x + 4, 93, H, 0.76, 0.92, '#0e1320', { rim: 0 });
  // 主机（玻璃侧透 + 风扇灯）
  s += shadow(u, 184, 198, 0.66, 0.82);
  s += box(184, 198, 94, H, 0.66, 0.82, '#10151f', { side: '#1a2030' });
  s += zq(186, 96, 196, H - 2, 0.659, '#0b1830', { stroke: '#333c52', 'stroke-width': 0.3 });
  for (const y of [101, 109]) s += zEllipse(191, y, 0.658, 3, 3, 'none', { stroke: o.on ? tc : shade(tc, 40), 'stroke-width': 0.8 }) + zEllipse(191, y, 0.657, 1, 1, cy, NS);
  // 三联屏
  const screen = (x0, x1, y0, y1, z, glow, content) => {
    let t = box(x0, x1, y0, y1, z, z + 0.012, '#0b0e15', { rim: 0.3 });
    t += zq(x0 + 1, y0 + 1, x1 - 1, y1 - 1, z - 0.001, o.on ? shade(glow, 72) : '#1b2230', NS);
    if (o.on) t += content + zq(x0 - 6, y0 - 6, x1 + 6, y1 + 8, z + 0.02, `url(#cy${u})`, { ...NS, opacity: 0.5 });
    return t;
  };
  const codeLines = (x0, y0, n, w) => {
    let t = '';
    const r = rng(x0);
    for (let i = 0; i < n; i++) t += zq(x0 + (i % 3) * 2, y0 + i * 2, x0 + (i % 3) * 2 + w * (0.3 + r() * 0.6), y0 + i * 2 + 0.8, 0.78, [cy, '#9cf5b5', '#ffb86b', '#e6e9f2'][i % 4], { opacity: 0.85, ...NS });
    return t;
  };
  s += box(150, 156, 82, 90, 0.82, 0.86, '#0b0e15', { rim: 0 }) + yq(144, 162, 89.9, 0.8, 0.88, '#0b0e15', NS);
  s += screen(112, 140, 64, 84, 0.8, cy, codeLines(115, 67, 8, 20));
  s += screen(141, 177, 58, 84, 0.79, tc, zq(144, 61, 174, 81, 0.785, shade(tc, 55), NS) + `<path d="M${P(145, 78, 0.784).join(' ')}L${P(152, 70, 0.784).join(' ')}L${P(158, 74, 0.784).join(' ')}L${P(165, 64, 0.784).join(' ')}L${P(173, 68, 0.784).join(' ')}" fill="none" stroke="${tint(tc, 40)}" stroke-width=".8"/>`);
  s += screen(178, 204, 64, 84, 0.8, cy, zEllipse(191, 74, 0.785, 6, 6, 'none', { stroke: cy, 'stroke-width': 0.6 }) + `<line x1="${P(191, 74, 0.785)[0]}" y1="${P(191, 74, 0.785)[1]}" x2="${P(196, 70, 0.785)[0]}" y2="${P(196, 70, 0.785)[1]}" stroke="${cy}" stroke-width=".6" class="sweep3d"/>`);
  // 键盘（RGB）、鼠标、耳机架、杯子
  s += box(130, 166, 89.2, 90, 0.75, 0.78, '#0b0e15', { top: '#141a28' });
  if (o.on) for (let k = 0; k < 12; k++) s += yq(131 + k * 3, 133 + k * 3, 89.1, 0.755, 0.775, [tc, cy, '#9cf5b5'][k % 3], { opacity: 0.8, ...NS });
  s += yEllipse(172, 89.4, 0.765, 2.2, 0.015, '#0b0e15', { stroke: tc, 'stroke-width': 0.3 });
  s += box(118, 121, 80, 90, 0.82, 0.84, '#2a3348', { rim: 0 }) + zEllipse(119.5, 80, 0.82, 4, 3.6, 'none', { stroke: '#0b0e15', 'stroke-width': 1.4 });
  s += box(106, 110, 85, 90, 0.8, 0.83, '#e6e9f2', { top: '#3a2a20' });
  // 电竞椅（背对镜头）
  s += shadow(u, 136, 166, 0.5, 0.64);
  s += yEllipse(151, H - 0.1, 0.57, 12, 0.06, '#0b0e15', NS);
  s += box(150, 152, 104, H - 2, 0.56, 0.58, '#2a3348', { rim: 0 });
  s += box(138, 164, 99, 104, 0.5, 0.64, '#141a28', { top: '#20283c' });
  s += box(140, 162, 74, 100, 0.49, 0.51, '#141a28', { rim: 0.3 });
  s += zq(146, 76, 156, 98, 0.489, tc, NS) + zq(142, 78, 145, 96, 0.488, '#20283c', NS) + zq(157, 78, 160, 96, 0.488, '#20283c', NS);
  s += box(144, 158, 68, 75, 0.495, 0.505, '#141a28', { rim: 0.3 }); // 头枕
  for (const x of [134, 164]) s += box(x, x + 4, 92, 95, 0.52, 0.62, '#20283c', { rim: 0.3 });
  // 服务器机柜（右）
  s += shadow(u, 248, 292, 0.8, 0.98);
  s += box(250, 290, 26, H, 0.8, 0.98, '#0e1320', { side: '#161d2e' });
  s += zq(252, 28, 288, H - 2, 0.799, '#0a0e17', { stroke: '#2a3348', 'stroke-width': 0.4 });
  for (let i = 0; i < 9; i++) {
    const y = 31 + i * 9.6;
    s += zq(254, y, 286, y + 7, 0.798, '#161d2e', { stroke: '#232c42', 'stroke-width': 0.25 });
    for (let k = 0; k < 4; k++) s += zEllipse(258 + k * 3, y + 3.5, 0.617, 0.7, 0.7, [o.on ? '#4cd18a' : '#244a36', cy, '#ffb15c', tc][(i + k) % 4], { class: (i + k) % 3 ? '' : 'blink' });
    s += zq(272, y + 2.6, 284, y + 4.2, 0.797, '#0a0e17', NS);
  }
  s += zq(252, 28, 288, H - 2, 0.796, `url(#gl${u})`, { ...NS, opacity: 0.4 });
  // 手办展示柜（后墙左下，窗下）
  s += shadow(u, 30, 88, 0.84, 0.98);
  s += box(30, 88, 74, H, 0.84, 0.98, '#141a28', { side: '#1a2030' });
  for (const y of [74, 90, 106]) s += zq(32, y + 1, 86, y + 15, 0.839, '#0b1222', NS) + zq(32, y + 14, 86, y + 15, 0.838, o.on ? cy : '#2a3348', { opacity: 0.7, ...NS });
  const fig = (x, y, c) => box(x - 2.5, x + 2.5, y - 8, y - 2, 0.9, 0.92, c, { rim: 0.4 }) + zEllipse(x, y - 10.4, 0.9, 2.6, 2.6, '#f2dccb', NS) + zEllipse(x, y - 11.6, 0.899, 2.8, 1.6, c, NS) + box(x - 3, x + 3, y - 2, y, 0.9, 0.93, '#2a3348', { rim: 0 });
  s += fig(40, 89, tc) + fig(54, 89, cy) + fig(68, 89, '#e6e9f2') + fig(80, 89, '#ff6b8b');
  s += fig(46, 105, '#9cf5b5') + fig(62, 105, tc) + box(70, 82, 98, 105, 0.88, 0.93, '#2a3348') + zq(72, 99, 80, 104, 0.879, cy, { opacity: 0.6, ...NS });
  s += zq(32, 75, 86, H - 2, 0.837, `url(#gl${u})`, { ...NS, opacity: 0.35 });
  // 线缆
  s += `<path d="M${P(200, H, 0.86).join(' ')}Q${P(220, H, 0.7).join(' ')} ${P(250, H, 0.8).join(' ')}" fill="none" stroke="#05070c" stroke-width=".7"/>`;
  // 懒人沙发（右前）
  s += shadow(u, 212, 248, 0.16, 0.34);
  {
    const [cx, cy0] = P(230, 108, 0.25);
    const c = shade(tc, 25);
    s += ell(cx, cy0 + 2, 17, 10, shade(c, 25), { stroke: '#0b0e15', 'stroke-width': 0.4 });
    s += ell(cx - 1, cy0, 15, 9, c, { stroke: '#0b0e15', 'stroke-width': 0.4 });
    s += ell(cx + 3, cy0 - 3, 8, 4, tint(c, 18), NS) + ell(cx - 6, cy0 - 4, 5, 2, '#fff', { opacity: 0.2, ...NS });
    s += `<path d="M${cx - 12} ${cy0 + 3}q12 5 24 0" fill="none" stroke="#000" stroke-opacity=".25" stroke-width=".6"/>`;
  }
  // 床（发光地台）
  s += bed3d(u, { frame: '#1c2333', duvet: '#3a4258', sheet: '#d9deea', pillow: '#eef1f8', head: 70, legs: '#0b0e15', glow: true });
  s += zq(8, H - 3.2, 112, H - 2.4, BED.z0 - 0.001, tc, NS);
  return { svg: s, clip: win.clip };
}

export const THEMES = [
  { key: 'study', name: '书房', draw: study },
  { key: 'neon', name: '赛博机房', draw: neon },
];

/** 宿舍场景（SVG 内容）；uid 让每间房的渐变 id 不冲突 */
export function dormScene(o, uid) {
  const u = String(uid).replace(/[^\w-]/g, '');
  const t = THEMES[o.style % THEMES.length];
  const { svg, clip } = t.draw(u, o);
  return defs(u, o).replace('</defs>', `${clip || ''}</defs>`) + `<g stroke="#0b0806" stroke-opacity=".38" stroke-width=".28" stroke-linejoin="round">${svg}</g>`;
}
