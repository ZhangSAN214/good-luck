// 网页小人的大脑：动作 / 表情表、状态机、位置计算、安全的本地存储。纯逻辑，不碰 DOM，
// 时间和随机数都可注入（测试用 node 直接跑）。名字与 core/members_art.py 的 PET_ACTIONS / PET_EXPRESSIONS 一致。

// 动作：prio 越大越不容易被打断；ms 为持续时间（没有 ms 的一直持续，直到换别的动作）；face 为默认表情
export const ACTIONS = {
  idle: { label: '待机', prio: 0 },
  walk: { label: '走动', prio: 1, ms: 2600 },
  jump: { label: '跳', prio: 2, ms: 750 },
  wave: { label: '挥手', prio: 2, ms: 1800, face: 'happy' },
  think: { label: '思考', prio: 2, ms: 3200, face: 'thinking' },
  talk: { label: '说话', prio: 2, ms: 2800 },
  type: { label: '打字', prio: 2, ms: 3200 },
  celebrate: { label: '庆祝', prio: 3, ms: 2400, face: 'happy' },
  sleepy: { label: '困', prio: 1, face: 'sleepy' },
  sleep: { label: '睡', prio: 1 },
  drag: { label: '被拖起', prio: 9, face: 'surprised' },
  land: { label: '落地', prio: 8, ms: 500 },
};

// 眨眼不是动作，叠在任何动作上
export const BLINK_MS = 160;

// 表情：emote 是只有一张图时头顶冒出的符号
export const EXPRESSIONS = {
  happy: { label: '开心', emote: '♪' },
  smug: { label: '得意', emote: '✧' },
  thinking: { label: '思考', emote: '…' },
  surprised: { label: '惊讶', emote: '!' },
  confused: { label: '困惑', emote: '?' },
  shy: { label: '害羞', emote: '///' },
  angry: { label: '生气', emote: '💢' },
  sad: { label: '难过', emote: '💧' },
  sleepy: { label: '困', emote: 'z' },
};

export const FACE_MS = 2600;
export const SLEEPY_AFTER = 90_000; // 这么久没有操作就犯困
export const SLEEP_AFTER = 240_000; // 再久一点就睡着

// 待机时随机做的小动作（权重）
const IDLE_PICKS = [
  ['blink', 55],
  ['walk', 14],
  ['wave', 8],
  ['jump', 8],
  ['think', 7],
  ['look', 8], // 换个表情看看四周
];
const IDLE_FACES = ['happy', 'smug', 'confused', 'shy'];

// 演示：依次展示所有动作和表情（双击小人）
export const DEMO = [
  ...['wave', 'jump', 'walk', 'think', 'talk', 'type', 'celebrate', 'sleepy', 'sleep'].map((a) => ['act', a]),
  ...Object.keys(EXPRESSIONS).map((f) => ['feel', f]),
];

export class Brain {
  constructor({ now = () => Date.now(), random = Math.random, calm = false } = {}) {
    this.now = now;
    this.random = random;
    this.calm = calm; // 减少动态效果：不自己走动、跳
    const t = now();
    this.action = 'idle';
    this.actionUntil = Infinity;
    this.face = null;
    this.faceUntil = 0;
    this.actionFace = false;
    this.blinkUntil = 0;
    this.dir = 1; // 走动方向：1 向右，-1 向左
    this.lastActive = t;
    this.nextIdle = t + this.gap();
    this.demo = [];
    this.nextDemo = 0;
  }

  gap() {
    return 1800 + Math.floor(this.random() * 4200);
  }

  // 做一个动作；正在做更重要的动作时拒绝（返回 false）
  act(name, { ms } = {}) {
    const spec = ACTIONS[name];
    if (!spec) return false;
    const t = this.now();
    const cur = ACTIONS[this.action];
    if (name !== this.action && t < this.actionUntil && cur.prio > spec.prio) return false;
    this.action = name;
    const dur = ms ?? spec.ms;
    this.actionUntil = dur ? t + dur : Infinity;
    if (name === 'walk' && this.random() < 0.5) this.dir = -this.dir;
    // 动作自带的表情跟着动作结束；换成没有表情的动作时去掉上一个动作带来的表情
    if (spec.face) {
      this.feel(spec.face, { ms: dur ?? Infinity, force: true });
      this.actionFace = true;
    } else if (this.actionFace || name === 'sleep') {
      this.face = null;
      this.actionFace = false;
    }
    return true;
  }

  feel(name, { ms = FACE_MS, force = false } = {}) {
    if (!EXPRESSIONS[name]) return false;
    // 被拖着时一直是惊讶
    if (!force && this.action === 'drag') return false;
    this.face = name;
    this.faceUntil = this.now() + ms;
    this.actionFace = false;
    return true;
  }

  blink() {
    this.blinkUntil = this.now() + BLINK_MS;
  }

  // 用户有操作：记下时间；在困或睡就醒过来
  poke() {
    const t = this.now();
    this.lastActive = t;
    if (this.action === 'sleep' || this.action === 'sleepy') {
      const wasAsleep = this.action === 'sleep';
      this.action = 'idle';
      this.actionUntil = Infinity;
      this.face = null;
      if (wasAsleep) this.feel('surprised', { ms: 900 });
      this.nextIdle = t + this.gap();
    }
  }

  grab() {
    this.lastActive = this.now();
    this.demo = [];
    this.action = 'idle';
    this.actionUntil = 0;
    this.act('drag');
  }

  drop() {
    this.lastActive = this.now();
    this.action = 'idle';
    this.actionUntil = 0;
    this.act('land');
    this.face = null;
  }

  startDemo() {
    this.demo = DEMO.slice();
    this.nextDemo = 0;
    this.poke();
  }

  // 推进时间：到期的动作 / 表情结束，空闲时随机做小动作，久不操作就困 / 睡。返回当前状态
  tick() {
    const t = this.now();
    if (t >= this.actionUntil) {
      this.action = 'idle';
      this.actionUntil = Infinity;
      this.nextIdle = t + this.gap();
      if (this.actionFace) this.face = null;
      this.actionFace = false;
    }
    if (this.face && t >= this.faceUntil) this.face = null;
    if (this.demo.length && t >= this.nextDemo && this.action !== 'drag') {
      const [kind, name] = this.demo.shift();
      this.lastActive = t;
      if (kind === 'act') {
        this.action = 'idle';
        this.actionUntil = 0;
        this.act(name, { ms: name === 'sleepy' || name === 'sleep' ? 2400 : undefined });
        this.nextDemo = t + Math.min(this.actionUntil - t, 3200);
      } else {
        this.action = 'idle';
        this.actionUntil = Infinity;
        this.feel(name, { ms: 1800 });
        this.nextDemo = t + 2000;
      }
    } else if (!this.demo.length) {
      this.idleTick(t);
    }
    return this.state();
  }

  idleTick(t) {
    const quiet = t - this.lastActive;
    if (this.action === 'idle' || this.action === 'sleepy') {
      if (quiet >= SLEEP_AFTER) return void this.act('sleep');
      if (quiet >= SLEEPY_AFTER && this.action === 'idle') return void this.act('sleepy');
    }
    if (this.action === 'sleep') return;
    if (t < this.nextIdle) return;
    this.nextIdle = t + this.gap();
    if (this.action === 'sleepy') return void this.blink();
    if (this.action !== 'idle') return;
    const pick = this.pick();
    if (pick === 'blink') this.blink();
    else if (pick === 'look') this.feel(IDLE_FACES[Math.floor(this.random() * IDLE_FACES.length)], { ms: 1600 });
    else if (!(this.calm && (pick === 'walk' || pick === 'jump'))) this.act(pick);
  }

  pick() {
    const total = IDLE_PICKS.reduce((s, [, w]) => s + w, 0);
    let r = this.random() * total;
    for (const [name, w] of IDLE_PICKS) {
      if ((r -= w) < 0) return name;
    }
    return 'blink';
  }

  state() {
    return {
      action: this.action,
      face: this.face,
      blink: this.now() < this.blinkUntil,
      dir: this.dir,
    };
  }
}

// --- 位置 ---------------------------------------------------------------------------

// 把小人夹回可见区域（size / view 为 {w, h}）
export function clamp(pos, size, view) {
  const maxX = Math.max(0, view.w - size.w);
  const maxY = Math.max(0, view.h - size.h);
  const n = (v, d) => (Number.isFinite(v) ? v : d);
  return {
    x: Math.round(Math.min(Math.max(n(pos.x, maxX), 0), maxX)),
    y: Math.round(Math.min(Math.max(n(pos.y, maxY), 0), maxY)),
  };
}

// 松手时离边不到 dist 就贴过去；返回 {x, y, edge}（edge 为 left / right / top / bottom 或 null）
export function snap(pos, size, view, dist = 48) {
  const p = clamp(pos, size, view);
  const gaps = [
    ['left', p.x],
    ['right', view.w - size.w - p.x],
    ['top', p.y],
    ['bottom', view.h - size.h - p.y],
  ].filter(([, g]) => g <= dist);
  if (!gaps.length) return { ...p, edge: null };
  gaps.sort((a, b) => a[1] - b[1]);
  const edges = new Set(gaps.map(([e]) => e));
  if (edges.has('left')) p.x = 0;
  if (edges.has('right') && !edges.has('left')) p.x = Math.max(0, view.w - size.w);
  if (edges.has('top')) p.y = 0;
  if (edges.has('bottom') && !edges.has('top')) p.y = Math.max(0, view.h - size.h);
  return { ...p, edge: gaps[0][0] };
}

// 默认位置：右下角，离边留一点
export function home(size, view) {
  return clamp({ x: view.w - size.w - 24, y: view.h - size.h - 24 }, size, view);
}

// --- 本地存储（隐私模式、被禁用时读写都可能抛错）--------------------------------------------

export function loadJSON(getStorage, key) {
  try {
    const raw = getStorage().getItem(key);
    if (!raw) return null;
    const v = JSON.parse(raw);
    return v && typeof v === 'object' ? v : null;
  } catch {
    return null;
  }
}

export function saveJSON(getStorage, key, value) {
  try {
    getStorage().setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}
