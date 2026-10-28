// 音乐卡片封面上的动态：画框里一层画布，飘雨、雪、落叶、花瓣、星屑或气泡；头发的轻轻飘动由 music.css 的 SVG 滤镜负责。
// 画法跟着封面的画风走：细的深灰线条、很淡的平涂、不发光不闪。每张封面配哪种写在封面目录的 effects.json，外观里也能自己换或关掉。

export const FX = [
  ['rain', '细雨'],
  ['snow', '雪'],
  ['leaves', '落叶'],
  ['petals', '花瓣'],
  ['stars', '星屑'],
  ['bubbles', '气泡'],
];
// 每种特效的默认淡色（effects.json 没写颜色时用）和同屏数量
const KIND = {
  rain: { color: '#9fb2cc', count: 34 },
  snow: { color: '#ffffff', count: 26 },
  leaves: { color: '#d9b98c', count: 7 },
  petals: { color: '#f3c6c6', count: 9 },
  stars: { color: '#fff6d8', count: 14 },
  bubbles: { color: '#d8ecf6', count: 10 },
};
const LINE = 'rgba(70,78,96,.42)'; // 跟封面线稿一样的深灰细线

export const isFx = (k) => FX.some(([x]) => x === k);

/** 生成一颗粒子；fresh=false 时撒满整个画面（刚打开时不是从顶上一起掉下来）。 */
export function spawn(kind, w, h, rand = Math.random, fresh = true) {
  const up = kind === 'bubbles';
  const y = fresh ? (up ? h + 10 : -10) : rand() * h;
  const s = { rain: 1, snow: 1.6 + rand() * 1.8, leaves: 6 + rand() * 3, petals: 5 + rand() * 3, stars: 2 + rand() * 2.2, bubbles: 2.5 + rand() * 4 }[kind];
  const speed = { rain: 150 + rand() * 60, snow: 12 + rand() * 14, leaves: 16 + rand() * 12, petals: 14 + rand() * 12, stars: 5 + rand() * 6, bubbles: 12 + rand() * 10 }[kind];
  return { x: rand() * w, y, s, vy: up ? -speed : speed, a: rand() * Math.PI * 2, spin: (rand() - 0.5) * 1.6, ph: rand() * Math.PI * 2, len: 9 + rand() * 9 };
}

/** 往前走 dt 秒；出了画面就换一颗新的。 */
export function step(kind, list, dt, w, h, rand = Math.random) {
  for (let i = 0; i < list.length; i++) {
    const p = list[i];
    p.ph += dt;
    p.a += p.spin * dt;
    p.y += p.vy * dt;
    if (kind === 'rain') p.x -= p.vy * 0.18 * dt;
    else if (kind !== 'stars') p.x += Math.sin(p.ph * (kind === 'snow' ? 0.9 : 1.3)) * (kind === 'bubbles' ? 6 : 14) * dt;
    if (p.y > h + 20 || p.y < -20 || p.x < -20 || p.x > w + 20) list[i] = spawn(kind, w, h, rand);
  }
  return list;
}

function petal(ctx, s) {
  ctx.beginPath();
  ctx.moveTo(0, -s);
  ctx.bezierCurveTo(s * 0.9, -s * 0.5, s * 0.6, s * 0.8, 0, s);
  ctx.bezierCurveTo(-s * 0.6, s * 0.8, -s * 0.9, -s * 0.5, 0, -s);
}

function leaf(ctx, s) {
  ctx.beginPath();
  ctx.moveTo(0, -s);
  ctx.quadraticCurveTo(s * 0.7, 0, 0, s);
  ctx.quadraticCurveTo(-s * 0.7, 0, 0, -s);
}

function star(ctx, s) {
  ctx.beginPath();
  for (let i = 0; i < 8; i++) {
    const r = i % 2 ? s * 0.32 : s;
    const t = (i * Math.PI) / 4 - Math.PI / 2;
    ctx.lineTo(Math.cos(t) * r, Math.sin(t) * r);
  }
  ctx.closePath();
}

/** 画一帧：淡色平涂 + 细线描边。 */
export function draw(ctx, kind, list, color) {
  const fill = color || KIND[kind].color;
  ctx.lineWidth = 0.7;
  ctx.strokeStyle = LINE;
  for (const p of list) {
    ctx.save();
    ctx.translate(p.x, p.y);
    if (kind === 'rain') {
      ctx.strokeStyle = fill;
      ctx.globalAlpha = 0.75;
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(p.len * 0.18, -p.len);
      ctx.stroke();
    } else if (kind === 'snow' || kind === 'bubbles') {
      ctx.globalAlpha = kind === 'snow' ? 0.9 : 0.7;
      ctx.beginPath();
      ctx.arc(0, 0, p.s, 0, Math.PI * 2);
      ctx.fillStyle = fill;
      if (kind === 'snow') ctx.fill();
      else ctx.globalAlpha = 0.55;
      ctx.stroke();
      if (kind === 'bubbles') {
        ctx.beginPath();
        ctx.arc(-p.s * 0.35, -p.s * 0.35, p.s * 0.28, Math.PI, Math.PI * 1.5);
        ctx.stroke();
      }
    } else {
      ctx.rotate(p.a);
      ctx.globalAlpha = kind === 'stars' ? 0.45 + 0.4 * Math.abs(Math.sin(p.ph * 0.8)) : 0.85;
      if (kind === 'petals') petal(ctx, p.s);
      else if (kind === 'leaves') leaf(ctx, p.s);
      else star(ctx, p.s);
      ctx.fillStyle = fill;
      ctx.fill();
      ctx.stroke();
      if (kind === 'leaves') {
        ctx.beginPath();
        ctx.moveTo(0, -p.s * 0.8);
        ctx.lineTo(0, p.s * 0.8);
        ctx.stroke();
      }
    }
    ctx.restore();
  }
}

/** 一块画布上的特效：只在画面可见、没开「减少动态」时动。 */
export class CoverFx {
  constructor() {
    this.kind = '';
    this.color = '';
    this.list = [];
    this.raf = 0;
    this.last = 0;
    this.canvas = null;
    this.calm = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  /** 换画布或特效；kind 为空就停。 */
  set(canvas, kind, color) {
    const k = isFx(kind) && canvas && !this.calm ? kind : '';
    if (k !== this.kind) this.list = [];
    this.kind = k;
    this.color = color || '';
    this.canvas = canvas;
    if (!k) return this.stop();
    if (!this.raf) {
      this.last = 0;
      this.raf = requestAnimationFrame((t) => this.frame(t));
    }
  }

  stop() {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    const c = this.canvas;
    c?.getContext('2d')?.clearRect(0, 0, c.width, c.height);
  }

  frame(t) {
    this.raf = 0;
    const c = this.canvas;
    if (!this.kind || !c?.isConnected) return;
    const w = c.clientWidth;
    const h = c.clientHeight;
    if (!w || !h || document.hidden) {
      this.raf = requestAnimationFrame((x) => this.frame(x));
      return;
    }
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(h * dpr);
    }
    const n = KIND[this.kind].count;
    while (this.list.length < n) this.list.push(spawn(this.kind, w, h, Math.random, false));
    const dt = this.last ? Math.min(0.05, (t - this.last) / 1000) : 0;
    this.last = t;
    step(this.kind, this.list, dt, w, h);
    const ctx = c.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    draw(ctx, this.kind, this.list, this.color);
    this.raf = requestAnimationFrame((x) => this.frame(x));
  }
}
