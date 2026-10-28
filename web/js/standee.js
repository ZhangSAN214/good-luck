// 终端首页的大立绘：一张立绘 + 表情差分，用代码让它「活着」。
// - 纯色底在浏览器里柔边抠掉（已经是透明底就跳过），结果只在内存里，不上传。
// - 清单里这一套写了 rig（scripts/standee_rig.py 生成的瞳孔层和全身权重图）且浏览器支持 WebGL2 时，
//   用显卡逐像素做全身形变（standee_gl.js）：转头带五官视差、眼球跟随、呼吸、手臂和书、左右长发、
//   前发、裙摆和饰品的弹簧摆动，外加点头、歪头、张望、翻书、伸懒腰等动作（standee_motion.js）。
// - 没有 rig 或不支持 WebGL2 时退回旧画法：把立绘按行切成细条逐条画到 canvas 上，近似网格形变：
//   呼吸（胸口起伏、肩膀上抬）、头部跟随鼠标、裙摆和发梢的波动、点击时的弹动和摸头晃动。
// - 眨眼用「闭眼」差分，说话时在「说话」和当前表情之间切换；没有差分时用动作代替。
// - 台词来自本机的 standee.json（和成员人设里的口头禅），不调用模型、不花钱。
// - 位置、大小、翻转、透明度、动态幅度、显示与否、用哪一套：只存在本机浏览器（localStorage）。
// - 系统开了「减少动态」时不呼吸不摆动，只换表情。

import { RigRenderer, glSupported } from './standee_gl.js';
import { IDLE_MOTIONS, Motion } from './standee_motion.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const KEY = 'roundtable.hub.standee';
const STRIPS = 96; // 切成多少条：越多越平滑，96 条在 4K 屏上也看不出台阶
const PAD = { x: 0.06, y: 0.03 }; // 画布四周留白（形变、头发摆动时不被裁掉）
const SNAP_PX = 14;
// 第一次显示时的位置：x 为水平中心、y 为底边（相对首页宽高，>1 表示底部被画框裁掉，像二游主界面），h 为高度
const DEFAULTS = { x: 0.29, y: 1.12, h: 1.08, flip: false, opacity: 1, motion: 1 };
const SQUARE = { x: 0.3, y: 1.0, h: 0.98 };
const EXPRS = ['smile', 'think', 'surprise'];
const LABEL = { smile: '微笑', think: '思考', surprise: '惊讶', blink: '闭眼', talk: '说话' };

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const smooth = (a, b, v) => {
  const t = clamp((v - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
};
const pick = (arr, rand = Math.random) => (arr?.length ? arr[Math.floor(rand() * arr.length)] : '');

function readStore() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '{}');
    return raw && typeof raw === 'object' ? raw : {};
  } catch {
    return {};
  }
}

function writeStore(v) {
  try {
    localStorage.setItem(KEY, JSON.stringify(v));
  } catch {
    // 存不了（隐私模式）就只在这次生效
  }
}

/** 一套立绘的默认摆放（竖图和方图不同）；用户改过的值覆盖它，非法值丢掉。 */
export function layoutOf(saved, aspect) {
  const base = { ...DEFAULTS, ...(aspect > 0.85 ? SQUARE : {}) };
  const out = { ...base };
  const num = (k, lo, hi) => {
    const v = Number(saved?.[k]);
    if (Number.isFinite(v)) out[k] = clamp(v, lo, hi);
  };
  num('x', -0.2, 1.2);
  num('y', 0.2, 1.8);
  num('h', 0.25, 2.4);
  num('opacity', 0.2, 1);
  num('motion', 0, 1.5);
  if (typeof saved?.flip === 'boolean') out.flip = saved.flip;
  return out;
}

/** 柔边抠底：从四边向内漫水，和底色相近的像素渐变透明，并把半透明边缘里混进去的底色去掉（不留蓝边）。 */
function keyOut(img) {
  const w = img.naturalWidth || img.width;
  const h = img.naturalHeight || img.height;
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, 0, 0);
  const data = ctx.getImageData(0, 0, w, h);
  const px = data.data;
  const corners = [0, w - 1, (h - 1) * w, h * w - 1].map((p) => p * 4);
  if (corners.some((i) => px[i + 3] < 250)) return { canvas, mask: maskOf(px, w, h) }; // 已经是透明底
  const bg = borderColor(px, w, h);
  const LO = 12;
  const HI = 34;
  const dist = (i) => Math.hypot(px[i] - bg[0], px[i + 1] - bg[1], px[i + 2] - bg[2]);
  const seen = new Uint8Array(w * h);
  const stack = new Int32Array(w * h);
  let top = 0;
  const push = (p) => {
    if (!seen[p]) {
      seen[p] = 1;
      stack[top++] = p;
    }
  };
  for (let x = 0; x < w; x++) push(x), push((h - 1) * w + x);
  for (let y = 0; y < h; y++) push(y * w), push(y * w + w - 1);
  while (top) {
    const p = stack[--top];
    const i = p * 4;
    const d = dist(i);
    if (d >= HI) continue; // 到了角色的边，不再往里走
    const a = smooth(LO, HI, d);
    px[i + 3] = Math.round(a * 255);
    if (a > 0) for (let k = 0; k < 3; k++) px[i + k] = clamp(Math.round((px[i + k] - (1 - a) * bg[k]) / a), 0, 255);
    const x = p % w;
    if (x > 0) push(p - 1);
    if (x < w - 1) push(p + 1);
    if (p >= w) push(p - w);
    if (p < w * (h - 1)) push(p + w);
  }
  // 被头发和手臂围住、漫水进不去的小块底色也去掉（只去掉几乎就是底色的像素）
  for (let p = 0; p < w * h; p++) if (!seen[p] && dist(p * 4) < LO * 0.75) px[p * 4 + 3] = 0;
  ctx.putImageData(data, 0, 0);
  return { canvas, mask: maskOf(px, w, h) };
}

/** 原图在左右下三边被画框截断的头发和裙摆：在边上淡出，不留一刀切的直边（只做一次）。 */
function fadeEdges(canvas) {
  const { width: w, height: h } = canvas;
  const ctx = canvas.getContext('2d');
  ctx.globalCompositeOperation = 'destination-out';
  const band = (x0, y0, x1, y1, rx, ry, rw, rh) => {
    const g = ctx.createLinearGradient(x0, y0, x1, y1);
    g.addColorStop(0, 'rgba(0,0,0,1)');
    g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g;
    ctx.fillRect(rx, ry, rw, rh);
  };
  const e = Math.round(w * 0.05);
  band(0, 0, e, 0, 0, 0, e, h);
  band(w, 0, w - e, 0, w - e, 0, e, h);
  const f = Math.round(h * 0.05);
  band(0, h, 0, h - f, 0, h - f, w, f);
  ctx.globalCompositeOperation = 'source-over';
}

/** 底色：四条边上出现最多的颜色（人物的裙摆、头发伸到边上也不受影响）。 */
function borderColor(px, w, h) {
  const counts = new Map();
  const add = (p) => {
    const i = p * 4;
    const k = ((px[i] >> 3) << 10) | ((px[i + 1] >> 3) << 5) | (px[i + 2] >> 3);
    const c = counts.get(k) || [0, 0, 0, 0];
    c[0]++;
    c[1] += px[i];
    c[2] += px[i + 1];
    c[3] += px[i + 2];
    counts.set(k, c);
  };
  for (let x = 0; x < w; x += 2) add(x), add((h - 1) * w + x);
  for (let y = 0; y < h; y += 2) add(y * w), add(y * w + w - 1);
  let best = [1, 0, 0, 0];
  for (const c of counts.values()) if (c[0] > best[0]) best = c;
  return [best[1] / best[0], best[2] / best[0], best[3] / best[0]];
}

/** 点击判定用的缩小版不透明度（每 4×4 像素取一个点）。 */
function maskOf(px, w, h) {
  const mw = Math.ceil(w / 4);
  const mh = Math.ceil(h / 4);
  const mask = new Uint8Array(mw * mh);
  for (let y = 0; y < mh; y++) for (let x = 0; x < mw; x++) mask[y * mw + x] = px[((y * 4) * w + x * 4) * 4 + 3];
  return { w: mw, h: mh, data: mask };
}

async function loadImage(url) {
  const img = new Image();
  img.decoding = 'async';
  img.src = url;
  await img.decode();
  return img;
}

/** 脸部贴片：四周羽化成椭圆，贴上去看不出方框。 */
function feather(img) {
  const w = img.naturalWidth;
  const h = img.naturalHeight;
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  const ctx = c.getContext('2d');
  ctx.drawImage(img, 0, 0);
  ctx.globalCompositeOperation = 'destination-in';
  ctx.translate(w / 2, h / 2);
  ctx.scale(w / 2, h / 2);
  const g = ctx.createRadialGradient(0, 0, 0.75, 0, 0, 1);
  g.addColorStop(0, '#000');
  g.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = g;
  ctx.fillRect(-1, -1, 2, 2);
  return c;
}

/** 所有表情差分都是脸部贴片时，它们合起来的像素范围（换表情只需重传这一块）；有整张差分时 null。 */
function patchRect(list, W, H) {
  if (!list.length || list.some((o) => !o.box)) return null;
  const x0 = Math.floor(Math.min(...list.map((o) => o.box[0])) * W);
  const y0 = Math.floor(Math.min(...list.map((o) => o.box[1])) * H);
  const x1 = Math.ceil(Math.max(...list.map((o) => o.box[0] + o.box[2])) * W);
  const y1 = Math.ceil(Math.max(...list.map((o) => o.box[1] + o.box[3])) * H);
  return [x0, y0, Math.min(x1, W) - x0, Math.min(y1, H) - y0];
}

export class Standee {
  /**
   * @param {{load:Function, pointer?:Function, now?:Function, rand?:Function}} opts
   * load() 返回 /api/standee 的结果；pointer() 返回首页的鼠标位置（-1～1，已平滑）。
   */
  constructor({ load, pointer = () => ({ x: 0, y: 0 }), now = () => new Date(), rand = Math.random }) {
    Object.assign(this, { fetcher: load, pointer, now, rand });
    this.info = null; // /api/standee
    this.store = readStore();
    this.ready = new Map(); // id → {canvas, mask, overlays, aspect, head}
    this.cur = null;
    this.src = null; // 当前合成好的一帧素材（立绘 + 表情差分）
    this.expr = 'default';
    this.exprUntil = 0;
    this.shown = { key: 'default', from: 'default', t0: 0, dur: 1 };
    this.blinkAt = 0;
    this.blinkUntil = 0;
    this.talk = { until: 0, open: false, next: 0 };
    this.fx = { hop: 0, hopV: 0, shake: 0, shakeT: 0, think: 0 };
    this.motion = new Motion(rand);
    this.nextMotion = 0; // 下一次待机小动作的时间
    this.srcVer = 0; // 立绘本体（含表情差分）每重画一次加一，网格形变据此决定要不要重新上传
    this.line = { base: '', text: '', shown: '', typedAt: 0, until: 0 };
    this.lastTap = 0;
    this.idleAt = 0;
    this.raf = 0;
    this.running = false;
    this.editing = false;
    this.drag = null;
    this.reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.el = document.createElement('div');
    this.el.className = 'sd-layer';
    this.el.innerHTML = `
      <div class="sd-stage" hidden>
        <canvas class="sd-cv" tabindex="0" role="img"></canvas>
        <span class="sd-guide sd-gx" hidden></span><span class="sd-guide sd-gy" hidden></span>
        <span class="sd-grip" title="拖动缩放" hidden></span>
      </div>
      <p class="sd-say hb-glass" hidden><b class="sd-name"></b><span class="sd-text"></span></p>
      <div class="sd-panel hb-glass" role="dialog" aria-label="立绘设置" hidden></div>`;
    this.stage = this.el.querySelector('.sd-stage');
    this.cv = this.el.querySelector('.sd-cv');
    this.ctx = this.cv.getContext('2d');
    this.sayEl = this.el.querySelector('.sd-say');
    this.panel = this.el.querySelector('.sd-panel');
    this.bind();
  }

  /** 有立绘并且没被隐藏：首页据此把原来那句提示换成立绘的对话框。 */
  get active() {
    return Boolean(this.info?.available && !this.store.hidden && this.cur);
  }

  async load() {
    try {
      this.info = await this.fetcher();
    } catch {
      this.info = { available: false, reason: '读不到立绘设置' };
    }
    if (!this.info?.available) return this.refresh();
    const want = this.info.variants.find((v) => v.id === this.store.variant) || this.info.variants[0];
    await this.use(want.id);
  }

  /** 网格形变素材：瞳孔层和三张权重图；任何一张读不出来或不支持 WebGL2 就返回 null（用旧画法）。 */
  async prepareRig(v) {
    if (!v.rig || this.noGl || !glSupported()) return null;
    try {
      const [iris, w1, w2, w3] = await Promise.all([v.rig.iris, ...v.rig.weights].map(loadImage));
      return new RigRenderer(v.rig, { iris, w1, w2, w3 });
    } catch {
      return null;
    }
  }

  async prepare(v) {
    if (this.ready.has(v.id)) return this.ready.get(v.id);
    const gl = await this.prepareRig(v);
    const img = await loadImage(gl ? v.rig.base : v.image);
    const { canvas, mask } = v.cutout ? keyOut(img) : { canvas: img, mask: null };
    const W = canvas.width || img.naturalWidth;
    const H = canvas.height || img.naturalHeight;
    if (v.cutout) fadeEdges(canvas);
    const overlays = {};
    await Promise.all(
      Object.entries(v.expressions || {}).map(async ([k, e]) => {
        try {
          const im = await loadImage(e.image);
          if (e.box) overlays[k] = { img: feather(im), box: e.box };
          else overlays[k] = { img: v.cutout ? keyOut(im).canvas : im, box: null };
        } catch {
          // 这张差分读不出来就当没有，用动作代替
        }
      }),
    );
    const prep = { v, canvas, mask, overlays, W, H, aspect: W / H, head: v.head || [0.47, W / H > 0.85 ? 0.2 : 0.17], gl };
    prep.patchRect = patchRect(Object.values(overlays), W, H);
    this.ready.set(v.id, prep);
    return prep;
  }

  async use(id) {
    const v = this.info?.variants?.find((x) => x.id === id);
    if (!v) return;
    this.stage.classList.add('sd-swap');
    try {
      this.cur = await this.prepare(v);
    } catch {
      this.cur = null;
      this.info = { ...this.info, available: false, reason: `立绘「${v.label}」读不出来` };
    }
    this.store.variant = id;
    writeStore(this.store);
    this.stage.dataset.render = this.cur?.gl ? 'mesh' : 'strips';
    this.src = null;
    this.expr = 'default';
    this.shown = { key: 'default', from: 'default', t0: 0, dur: 1 };
    this.refresh();
    requestAnimationFrame(() => this.stage.classList.remove('sd-swap'));
  }

  settings() {
    if (!this.cur) return layoutOf(null, 0.66);
    return layoutOf(this.store.per?.[this.cur.v.id], this.cur.aspect);
  }

  save(patch) {
    if (!this.cur) return;
    const id = this.cur.v.id;
    this.store.per = { ...(this.store.per || {}), [id]: { ...this.settings(), ...patch } };
    writeStore(this.store);
    this.place();
  }

  /** 首页每次重画后把立绘层放回去（立绘层本身不重建，动画不断）。 */
  mount(slot) {
    if (!slot) return;
    slot.replaceWith(this.el);
    this.hub = this.el.closest('.hub');
    if (this.editing) this.place(); // 正在调设置时首页的定时刷新不打断面板
    else this.refresh();
  }

  refresh() {
    const on = this.active;
    this.stage.hidden = !on;
    this.hub?.classList.toggle('hb-has-sd', on);
    if (this.cur) {
      this.cv.setAttribute('aria-label', `${this.info.name}的立绘（点一下说话）`);
      this.el.querySelector('.sd-name').textContent = this.info.name;
    }
    this.place();
    this.renderPanel();
    this.renderSay();
  }

  /** 按设置算出立绘在首页里的位置和画布尺寸。手机宽度时居中放在卡片上方。 */
  place() {
    if (!this.cur || !this.hub) return;
    const r = this.hub.getBoundingClientRect();
    const W = r.width || innerWidth;
    const narrow = W <= 860;
    const s = this.settings();
    // 手机宽度：立绘放在顶栏下面、卡片上面的一块区域里
    const tb = narrow ? (this.hub.querySelector('.hb-top')?.getBoundingClientRect().bottom ?? r.top) - r.top + 6 : 0;
    const area = Math.min(innerHeight * 0.58, 560);
    const H = narrow ? tb + area : r.height || innerHeight;
    const k = clamp(s.h / layoutOf(null, this.cur.aspect).h, 0.6, 1.4);
    const h = narrow ? Math.min(area * k, (W * 1.05) / this.cur.aspect) : s.h * H;
    const w = h * this.cur.aspect;
    const left = narrow ? (W - w) / 2 : s.x * W - w / 2;
    const top = narrow ? H - h + 8 : s.y * H - h;
    const px = w * PAD.x;
    const py = h * PAD.y;
    Object.assign(this.stage.style, {
      left: `${left - px}px`,
      top: `${top - py}px`,
      width: `${w + 2 * px}px`,
      height: `${h + 2 * py}px`,
      opacity: String(s.opacity),
    });
    this.fadeAt = (H - (top - py)) / (h + 2 * py); // 首页底边在画布里的位置（0～1）
    this.hub.style.setProperty('--sd-h', `${H}px`);
    const ratio = Math.min(devicePixelRatio || 1, 2);
    const cw = Math.round(Math.min((w + 2 * px) * ratio, this.cur.W * (1 + 2 * PAD.x)));
    const ch = Math.round(cw / ((w + 2 * px) / (h + 2 * py)));
    if (this.cv.width !== cw || this.cv.height !== ch) {
      this.cv.width = cw;
      this.cv.height = ch;
    }
    const col = this.hub.querySelector('.hb-col')?.getBoundingClientRect();
    this.box = { left, top, w, h, W, H, narrow, flip: s.flip, colLeft: col ? col.left - r.left : W, sayAt: performance.now() };
    this.draw(performance.now());
  }

  start() {
    if (this.running) return;
    this.running = true;
    const t = performance.now();
    this.blinkAt = t + 1500 + this.rand() * 2500;
    this.idleAt = t + 40_000;
    this.nextMotion = t + 6000 + this.rand() * 4000;
    const step = (now) => {
      if (!this.running) return;
      this.tick(now);
      this.raf = requestAnimationFrame(step);
    };
    this.raf = requestAnimationFrame(step);
  }

  stop() {
    this.running = false;
    cancelAnimationFrame(this.raf);
    this.edit(false);
  }

  /** 首页那句「欢迎回来……」：立绘在时由她来说。 */
  setLine(text) {
    if (text === this.line.base) return;
    const first = !this.line.base;
    this.line.base = text;
    if (!this.line.until) this.say(text, { keep: true, quiet: !first && !this.running });
  }

  /** 说一句话：逐字打出来，打字时嘴动；keep=常驻（首页提示），否则几秒后回到常驻那句。 */
  say(text, { expr = null, keep = false, quiet = false } = {}) {
    const now = performance.now();
    this.line.text = text;
    this.line.typedAt = quiet ? now - 1e6 : now;
    this.line.until = keep ? 0 : now + 2600 + text.length * 140;
    if (!quiet) this.talk.until = now + text.length * 55 + 150;
    if (expr) this.setExpr(expr, 2600 + text.length * 80);
    this.renderSay(now);
  }

  setExpr(name, ms = 2500) {
    this.expr = name;
    this.exprUntil = performance.now() + ms;
    if (name === 'think') this.fx.think = 1;
  }

  renderSay(now = performance.now()) {
    const on = this.active && this.line.text;
    this.sayEl.hidden = !on;
    if (!on) return;
    const n = Math.floor((now - this.line.typedAt) / 55);
    const shown = this.line.text.slice(0, Math.max(0, n));
    if (shown !== this.line.shown) {
      this.line.shown = shown;
      this.el.querySelector('.sd-text').textContent = shown;
    }
    if (this.box && this.box.sayAt !== this.sayAt) {
      // 对话框放在头部右侧（不挡脸），右边不压到卡片；手机宽度时放在立绘脚下
      const { left, top, w, h, W, H, narrow, flip, colLeft } = this.box;
      const hx = left + (flip ? 1 - this.cur.head[0] : this.cur.head[0]) * w;
      const hy = top + this.cur.head[1] * h;
      const maxW = Math.min(320, W - 32);
      let x = hx + w * 0.14;
      let y = hy - h * 0.01;
      if (narrow) {
        x = 16;
        y = H - 76;
      } else x = Math.min(x, (colLeft || W) - maxW - 16);
      x = clamp(x, 16, Math.max(16, W - maxW - 16));
      Object.assign(this.sayEl.style, { left: `${x}px`, top: `${Math.max(56, y)}px`, maxWidth: `${maxW}px` });
      this.sayAt = this.box.sayAt;
    }
  }

  /** 点立绘：摸头（头部附近）或普通点击，换表情、说一句；只认不透明的地方。 */
  tap(u, v) {
    const now = performance.now();
    if (now - this.lastTap < 500) return;
    this.lastTap = now;
    this.idleAt = now + 40_000;
    const lines = this.info.lines || {};
    const head = v < this.cur.head[1] + 0.09 && Math.abs(u - this.cur.head[0]) < 0.2;
    if (head) {
      this.fx.shake = 1;
      this.fx.shakeT = now;
      this.motion.play(this.rand() < 0.5 ? 'shake' : 'tilt', now);
      const text = pick(lines.head, this.rand) || pick(lines.tap, this.rand);
      const expr = this.rand() < 0.35 ? 'surprise' : 'smile';
      if (text) this.say(text, { expr });
      else this.setExpr(expr);
    } else {
      this.fx.hopV = -1.4;
      // 点到书和手：翻书；点别处：小跳一下再点个头
      const book = this.cur.gl && u < this.cur.head[0] - 0.1 && v > this.cur.head[1] + 0.1 && v < this.cur.head[1] + 0.35;
      if (book) this.motion.play('read', now);
      else {
        this.motion.bounce(-1.2);
        this.motion.play('nod', now);
      }
      const expr = EXPRS[Math.floor(this.rand() * EXPRS.length)];
      const text = pick(lines.tap, this.rand);
      if (text) this.say(text, { expr });
      else this.setExpr(expr);
    }
  }

  /** 进入首页时打招呼：早上 / 深夜有专门的台词，没有就说首页提示。 */
  greet() {
    const h = this.now().getHours();
    const lines = this.info?.lines || {};
    const special = h >= 5 && h < 10 ? lines.morning : h >= 23 || h < 5 ? lines.night : null;
    const text = pick(special, this.rand) || pick(lines.greet, this.rand);
    this.fx.hopV = -0.8;
    this.motion.bounce(-0.8);
    this.motion.play('nod', performance.now());
    if (text) {
      this.say(text, { expr: 'smile' });
    } else if (this.line.base) this.say(this.line.base, { keep: true, expr: 'smile' });
  }

  tick(now) {
    if (!this.active) return;
    const dt = Math.min((now - (this.last || now)) / 1000, 0.05);
    this.last = now;
    // 眨眼：2～6 秒一次，偶尔连眨两下
    if (now >= this.blinkAt) {
      this.blinkUntil = now + 110;
      this.blinkAt = now + (this.rand() < 0.18 ? 260 : 2200 + this.rand() * 3800);
    }
    // 说话：嘴在张开和当前表情之间切换
    if (now < this.talk.until) {
      if (now >= this.talk.next) {
        this.talk.open = !this.talk.open;
        this.talk.next = now + 90 + this.rand() * 90;
      }
    } else this.talk.open = false;
    if (this.expr !== 'default' && now > this.exprUntil) this.expr = 'default';
    if (this.line.until && now > this.line.until) {
      this.line.until = 0;
      this.line.text = this.line.base;
      this.line.typedAt = now - 1e6;
    }
    // 久不理她，偶尔自言自语一句（只在有 idle 台词时）
    if (now > this.idleAt) {
      this.idleAt = now + 60_000 + this.rand() * 60_000;
      const text = pick(this.info.lines?.idle, this.rand);
      if (text && !this.editing && !document.hidden) this.say(text, { expr: this.rand() < 0.5 ? 'think' : null });
    }
    // 待机小动作（网格形变时）：8～16 秒一个，说话和调设置时不做
    if (this.cur.gl && now > this.nextMotion) {
      this.nextMotion = now + 8000 + this.rand() * 8000;
      if (!this.editing && now > this.talk.until) this.motion.play(pick(IDLE_MOTIONS, this.rand), now);
    }
    // 弹动：阻尼弹簧
    const f = this.fx;
    f.hopV += (-f.hop * 90 - f.hopV * 11) * dt;
    f.hop += f.hopV * dt;
    f.think += ((this.expr === 'think' ? 1 : 0) - f.think) * (1 - Math.exp(-dt * 3));
    this.renderSay(now);
    this.draw(now);
  }

  /** 这一帧要显示的表情：眨眼 > 说话 > 当前表情；没有对应差分时退回默认。 */
  wanted(now) {
    const o = this.cur.overlays;
    if (now < this.blinkUntil && o.blink && this.expr !== 'surprise') return 'blink';
    if (this.talk.open && o.talk) return 'talk';
    return o[this.expr] ? this.expr : 'default';
  }

  compose(now) {
    const key = this.wanted(now);
    const s = this.shown;
    if (key !== s.key) {
      const fast = key === 'blink' || s.key === 'blink' || key === 'talk' || s.key === 'talk';
      this.shown = { key, from: s.key, t0: now, dur: fast ? 50 : 180 };
      this.src = null;
    }
    const a = clamp((now - this.shown.t0) / this.shown.dur, 0, 1);
    if (this.src && this.srcAlpha === a) return this.src;
    const { canvas, W, H, overlays } = this.cur;
    if (!this.buf) this.buf = document.createElement('canvas');
    const b = this.buf;
    if (b.width !== W || b.height !== H) {
      b.width = W;
      b.height = H;
    }
    const ctx = b.getContext('2d');
    ctx.clearRect(0, 0, W, H);
    ctx.globalAlpha = 1;
    ctx.drawImage(canvas, 0, 0);
    const lay = (k, alpha) => {
      const o = overlays[k];
      if (!o || alpha <= 0) return;
      ctx.globalAlpha = alpha;
      if (o.box) ctx.drawImage(o.img, o.box[0] * W, o.box[1] * H, o.box[2] * W, o.box[3] * H);
      else ctx.drawImage(o.img, 0, 0, W, H);
    };
    lay(this.shown.from, 1 - a);
    lay(this.shown.key, a);
    ctx.globalAlpha = 1;
    this.src = b;
    this.srcAlpha = a;
    this.srcVer++;
    // 表情差分里自带眼睛：差分显示多少，单独的瞳孔层就隐去多少
    this.irisOn = (this.shown.key === 'default' ? a : 0) + (this.shown.from === 'default' ? 1 - a : 0);
    return b;
  }

  /** 画一帧：按行切条，每条单独位移 / 伸缩，近似 Live2D 的网格形变。 */
  draw(now) {
    if (!this.cur || !this.box || this.stage.hidden) return;
    const src = this.compose(now);
    if (this.cur.gl) return this.drawGl(now, src);
    const { W, H, head } = this.cur;
    const cw = this.cv.width;
    const ch = this.cv.height;
    const ctx = this.ctx;
    const s = this.settings();
    const m = this.reduce ? 0 : s.motion;
    const t = now / 1000;
    const p = this.reduce || this.editing ? { x: 0, y: 0 } : this.pointer();
    const px = (s.flip ? -1 : 1) * p.x;
    const f = this.fx;
    const shake = f.shake * Math.exp(-(now - f.shakeT) / 380) * (now - f.shakeT < 1500 ? 1 : 0);
    const talkBob = !this.cur.overlays.talk && this.talk.open ? 1 : 0;
    const chest = head[1] + 0.17;
    const waist = head[1] + 0.38;
    const breath = (1 - Math.cos((t * 2 * Math.PI) / 4.4)) / 2;
    const ox = cw * PAD.x / (1 + 2 * PAD.x);
    const oy = ch * PAD.y / (1 + 2 * PAD.y);
    const dw = cw - 2 * ox;
    const dh = ch - 2 * oy;
    const hw = (v) => 1 - smooth(head[1] + 0.06, chest + 0.06, v); // 头部权重
    const Y = (v) =>
      v -
      m * 0.0045 * breath * (1 - smooth(chest, waist, v)) +
      m * 0.004 * p.y * hw(v) +
      0.0012 * talkBob * hw(v) +
      0.003 * f.think * hw(v) -
      // 弹动：以脚为支点上下伸缩
      f.hop * 0.012 * (1 - v);
    const X = (v) =>
      m * (0.006 * Math.sin(t * 1.1 - v * 3.2) * smooth(waist, 1, v) + 0.0028 * Math.sin(t * 0.37) * (1 - v)) +
      m * px * (0.012 * hw(v) + 0.004 * (1 - v)) +
      0.009 * f.think * hw(v) +
      0.008 * shake * Math.sin((now - f.shakeT) / 32) * hw(v);
    const S = (v) => 1 + m * 0.006 * breath * (smooth(chest - 0.1, chest, v) - smooth(chest, waist, v));
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, cw, ch);
    if (s.flip) ctx.setTransform(-1, 0, 0, 1, cw, 0);
    ctx.imageSmoothingQuality = 'high';
    const cx = head[0];
    for (let i = 0; i < STRIPS; i++) {
      const v0 = i / STRIPS;
      const v1 = (i + 1) / STRIPS;
      const vm = (v0 + v1) / 2;
      const y0 = oy + Y(v0) * dh;
      const y1 = oy + Y(v1) * dh;
      const sc = S(vm);
      const w = dw * sc;
      const x = ox + X(vm) * dw - (w - dw) * cx;
      ctx.drawImage(src, 0, v0 * H, W, (v1 - v0) * H, x, y0, w, y1 - y0 + 0.75);
    }
    // 首页底边：立绘渐隐进去，不在页面底部留一道直边
    if (this.fadeAt < 1) {
      const y1 = this.fadeAt * ch;
      const y0 = y1 - 70 * this.dprY();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.globalCompositeOperation = 'destination-out';
      const g = ctx.createLinearGradient(0, y0, 0, y1);
      g.addColorStop(0, 'rgba(0,0,0,0)');
      g.addColorStop(1, 'rgba(0,0,0,1)');
      ctx.fillStyle = g;
      ctx.fillRect(0, y0, cw, ch - y0);
      ctx.globalCompositeOperation = 'source-over';
    }
  }

  /** 画布像素 / 页面像素（纵向）；画布还没布局好（高度 0，比如开场动画盖着时）按 1 算。 */
  dprY() {
    const h = this.cv.clientHeight;
    return h > 0 ? this.cv.height / h : 1;
  }

  /** 网格形变画一帧：显卡算好后整张贴到页面上的画布里（点击判定、截图都照旧）。 */
  drawGl(now, src) {
    const r = this.cur.gl;
    if (r.ver !== this.srcVer) {
      r.setBase(src, this.cur.patchRect);
      r.ver = this.srcVer;
    }
    const s = this.settings();
    const dt = Math.min((now - (this.glLast || now)) / 1000, 0.05);
    this.glLast = now;
    const p = this.reduce || this.editing ? { x: 0, y: 0 } : this.pointer();
    const pose = this.motion.update(dt, now, {
      pointer: { x: (s.flip ? -1 : 1) * p.x, y: p.y },
      motion: this.reduce ? 0 : s.motion,
      think: this.fx.think,
      talk: this.talk.open && !this.cur.overlays.talk,
      irisOn: this.irisOn ?? 1,
    });
    const cw = this.cv.width;
    const ch = this.cv.height;
    try {
      const out = r.render(cw, ch, pose, {
        pad: PAD,
        flip: s.flip,
        fadeAt: this.fadeAt ?? 1,
        fadePx: 70 * this.dprY(),
      });
      this.ctx.setTransform(1, 0, 0, 1, 0, 0);
      this.ctx.clearRect(0, 0, cw, ch);
      this.ctx.drawImage(out, 0, 0);
    } catch {
      // 显卡上下文丢了之类：这一套改用旧画法
      this.cur.gl = null;
      this.noGl = true;
      this.stage.dataset.render = 'strips';
    }
  }

  /** 屏幕坐标 → 立绘上的 (u, v)；落在透明处返回 null。 */
  hit(e) {
    if (!this.cur || !this.box) return null;
    const r = this.cv.getBoundingClientRect();
    const ox = r.width * PAD.x / (1 + 2 * PAD.x);
    const oy = r.height * PAD.y / (1 + 2 * PAD.y);
    let u = (e.clientX - r.left - ox) / (r.width - 2 * ox);
    const v = (e.clientY - r.top - oy) / (r.height - 2 * oy);
    if (this.box.flip) u = 1 - u;
    if (u < 0 || u > 1 || v < 0 || v > 1) return null;
    const mk = this.cur.mask;
    if (mk && mk.data[Math.floor(v * (mk.h - 1)) * mk.w + Math.floor(u * (mk.w - 1))] < 60) return null;
    return { u, v };
  }

  bind() {
    this.cv.addEventListener('pointermove', (e) => {
      if (this.drag) return;
      this.cv.style.cursor = this.editing ? 'move' : this.hit(e) ? 'pointer' : '';
    });
    this.cv.addEventListener('click', (e) => {
      if (this.editing || this.moved) return;
      const h = this.hit(e);
      if (h) this.tap(h.u, h.v);
    });
    this.cv.addEventListener('keydown', (e) => {
      if (this.editing) return this.nudge(e);
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        this.tap(this.cur.head[0] + 0.3, 0.6);
      }
    });
    // 编辑：拖动移动、拖右下角缩放、滚轮缩放
    this.stage.addEventListener('pointerdown', (e) => {
      if (!this.editing || this.box?.narrow) return;
      e.preventDefault();
      const s = this.settings();
      this.drag = { kind: e.target.closest('.sd-grip') ? 'size' : 'move', x: e.clientX, y: e.clientY, s };
      this.stage.setPointerCapture(e.pointerId);
    });
    this.stage.addEventListener('pointermove', (e) => {
      const d = this.drag;
      if (!d) return;
      const { W, H } = this.box;
      const dx = e.clientX - d.x;
      const dy = e.clientY - d.y;
      if (d.kind === 'size') {
        this.save({ h: clamp(d.s.h + dy / H, 0.25, 2.4) });
        return;
      }
      let x = d.s.x + dx / W;
      let y = d.s.y + dy / H;
      let gx = null;
      let gy = null;
      if (this.store.snap !== false) {
        // 吸附：水平中心对齐左半区中线 / 三分线 / 正中，底边对齐首页底部或略低处
        for (const g of [0.25, 1 / 3, 0.5]) if (Math.abs(x - g) * W < SNAP_PX) (x = g), (gx = g);
        if (Math.abs(x * W - this.box.w / 2) < SNAP_PX) (x = this.box.w / 2 / W), (gx = x);
        for (const g of [1, layoutOf(null, this.cur.aspect).y]) if (Math.abs(y - g) * H < SNAP_PX) (y = g), (gy = g);
      }
      this.guides(gx, gy);
      this.save({ x, y });
    });
    const end = () => {
      if (!this.drag) return;
      this.drag = null;
      this.guides(null, null);
      this.renderPanel();
    };
    this.stage.addEventListener('pointerup', end);
    this.stage.addEventListener('pointercancel', end);
    this.stage.addEventListener(
      'wheel',
      (e) => {
        if (!this.editing || this.box?.narrow) return;
        e.preventDefault();
        const s = this.settings();
        this.save({ h: clamp(s.h * Math.exp(-e.deltaY * 0.0012), 0.25, 2.4) });
        this.renderPanel();
      },
      { passive: false },
    );
    this.panel.addEventListener('input', (e) => this.onPanel(e));
    this.panel.addEventListener('change', (e) => this.onPanel(e));
    this.panel.addEventListener('click', (e) => this.onPanel(e));
    document.addEventListener('pointerdown', (e) => {
      if (!this.editing || this.panel.contains(e.target) || this.stage.contains(e.target)) return;
      if (e.target.closest('[data-act="standee"]')) return;
      this.edit(false);
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.editing) this.edit(false);
    });
    addEventListener('resize', () => this.hub && !this.hub.hidden && this.place());
  }

  guides(gx, gy) {
    const [ex, ey] = this.stage.querySelectorAll('.sd-guide');
    ex.hidden = gx == null;
    ey.hidden = gy == null;
    // 参考线画在首页坐标里：减去立绘层的偏移
    if (gx != null) ex.style.left = `${gx * this.box.W - parseFloat(this.stage.style.left)}px`;
    if (gy != null) ey.style.top = `${gy * this.box.H - parseFloat(this.stage.style.top)}px`;
  }

  nudge(e) {
    const step = e.shiftKey ? 10 : 1;
    const s = this.settings();
    const { W, H } = this.box;
    const move = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
    if (move) {
      e.preventDefault();
      this.save({ x: s.x + move[0] / W, y: s.y + move[1] / H });
    } else if (e.key === '+' || e.key === '=' || e.key === '-') {
      e.preventDefault();
      this.save({ h: clamp(s.h * (e.key === '-' ? 0.97 : 1.03), 0.25, 2.4) });
      this.renderPanel();
    } else if (e.key === 'Escape') this.edit(false);
  }

  /** 打开 / 关闭立绘设置；打开时立绘可以直接拖。 */
  edit(on = !this.editing) {
    this.editing = on;
    this.panel.hidden = !on;
    this.stage.classList.toggle('sd-edit', on && this.active);
    this.stage.querySelector('.sd-grip').hidden = !(on && this.active);
    this.hub?.querySelector('[data-act="standee"]')?.setAttribute('aria-expanded', String(on));
    if (on) {
      this.renderPanel();
      this.panel.querySelector('input,button')?.focus();
    }
  }

  renderPanel() {
    if (this.panel.hidden) return;
    const info = this.info;
    if (!info?.available || !this.cur) {
      this.panel.innerHTML = `<b>立绘</b><p class="hb-hint">${esc(info?.reason || '正在读取立绘…')}</p>
        <p class="hb-hint">立绘是这台电脑上的图片，清单写在 data/avatars/standee.json（格式见说明文档），不上传。</p>`;
      return;
    }
    const s = this.settings();
    const cur = this.cur?.v;
    const chips = info.variants
      .map((v) => `<button type="button" class="hb-chip" data-sd-variant="${esc(v.id)}" aria-pressed="${cur?.id === v.id}">${esc(v.label)}</button>`)
      .join('');
    const have = cur ? Object.keys(this.cur.overlays) : [];
    const diff = have.length ? `已接上表情：${have.map((k) => LABEL[k] || k).join('、')}` : '还没有表情差分，先用动作代替（差分到了写进清单就会用上）';
    const range = (key, label, min, max, val, unit = '%') =>
      `<label class="sd-range"><span>${label}</span><input type="range" min="${min}" max="${max}" value="${Math.round(val)}" data-sd="${key}"><output>${Math.round(val)}${unit}</output></label>`;
    const narrow = this.box?.narrow;
    this.panel.innerHTML = `
      <b>立绘</b>
      <label class="sd-row"><input type="checkbox" data-sd="show"${this.store.hidden ? '' : ' checked'}> 显示立绘</label>
      <div class="hb-chips">${chips}</div>
      ${range('size', '大小', 40, 200, (s.h / layoutOf(null, this.cur.aspect).h) * 100)}
      ${range('opacity', '不透明度', 20, 100, s.opacity * 100)}
      ${range('motion', '动态幅度', 0, 150, s.motion * 100)}
      <label class="sd-row"><input type="checkbox" data-sd="flip"${s.flip ? ' checked' : ''}> 水平翻转</label>
      <label class="sd-row"><input type="checkbox" data-sd="snap"${this.store.snap === false ? '' : ' checked'}> 拖动时吸附对齐</label>
      <div class="sd-btns"><button type="button" class="hb-chip" data-sd="reset">恢复默认</button><button type="button" class="hb-chip sd-done" data-sd="done">完成</button></div>
      <p class="hb-hint">${narrow ? '手机宽度下立绘自动居中，只能调大小、透明度和翻转。' : '直接拖动立绘移动位置；拖右下角或滚轮缩放；选中立绘后方向键微调（Shift 一次 10 像素）。'}设置只存在这台电脑的浏览器里。</p>
      <p class="hb-hint">${esc(diff)}${this.reduce ? '；系统开了「减少动态」，立绘不呼吸不摆动' : ''}</p>
      <p class="hb-hint">${this.cur.gl ? '动态：全身网格形变（显卡），点头发以外的地方会有不同动作，点书会翻书。' : cur?.rig ? '动态：这台电脑的浏览器不支持 WebGL2，用简化动态。' : '动态：简化动态（清单里加上 rig 素材可换成全身网格形变）。'}</p>`;
  }

  onPanel(e) {
    const t = e.target;
    const variant = t.closest?.('[data-sd-variant]');
    if (variant && e.type === 'click') return this.use(variant.dataset.sdVariant).then(() => this.renderPanel());
    const key = t.dataset?.sd;
    if (!key) return;
    if (e.type === 'click' && key === 'done') return this.edit(false);
    if (e.type === 'click' && key === 'reset') {
      if (this.cur) delete this.store.per?.[this.cur.v.id];
      this.store.hidden = false;
      this.store.snap = true;
      writeStore(this.store);
      this.refresh();
      this.onToggle?.();
      return;
    }
    if (t.type === 'range') {
      const val = Number(t.value);
      t.nextElementSibling.textContent = `${val}%`;
      if (key === 'size') this.save({ h: (layoutOf(null, this.cur.aspect).h * val) / 100 });
      if (key === 'opacity') this.save({ opacity: val / 100 });
      if (key === 'motion') this.save({ motion: val / 100 });
      return;
    }
    if (e.type !== 'change') return;
    if (key === 'show') {
      this.store.hidden = !t.checked;
      writeStore(this.store);
      this.refresh();
      this.onToggle?.();
    } else if (key === 'flip') this.save({ flip: t.checked });
    else if (key === 'snap') {
      this.store.snap = t.checked;
      writeStore(this.store);
    }
  }
}
