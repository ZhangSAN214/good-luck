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
  // 有逐帧素材（pet.json）的成员才会用到：没有对应的帧就什么也不做
  talk_book: { label: '书角强调', prio: 2, ms: 1600 },
  letter: { label: '写信', prio: 1 }, // 特殊待机：一直持续，直到用户再次活动
  gift: { label: '收到来信', prio: 3, ms: 5000 }, // 彩蛋；实际时长按帧数算
  curtsy: { label: '行礼', prio: 3, ms: 2600 }, // 特殊点击：摸头
  flip: { label: '翻书思考', prio: 2, ms: 3200 },
  bookface: { label: '书盖脸', prio: 1 },
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
export const LONG_PRESS_MS = 300; // 触摸屏上要按住这么久才能拖动，避免和页面滚动冲突
export const RETURN_AFTER_MS = 600_000; // 没有特殊待机的成员：离开这么久回来也算「长时间离开」
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
  ...['talk_book', 'curtsy', 'gift', 'letter', 'flip', 'bookface'].map((a) => ['act', a]),
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
    this.special = null; // {action, after_s}：长时间无操作后一直做的动作，取代犯困 / 睡觉
    this.eggs = []; // [{action, probability, cooldown_s, max_per_day, triggers, ms}]
    this.eggState = {}; // {动作: {lastAt, day, count}}，由 pet.js 存进 localStorage
    this.busy = () => false; // 外部有事（任务进行中、有确认卡片…）时不进入特殊待机、不掷彩蛋
  }

  // 成员的特殊待机和彩蛋（来自 pet.json，已由服务端校验）；没有对应素材时传 null / []
  configure({ special = null, eggs = [], eggState = {} } = {}) {
    this.special = special && ACTIONS[special.action] ? { action: special.action, after_s: special.after_s } : null;
    this.eggs = eggs.filter((e) => ACTIONS[e.action]);
    this.eggState = eggState && typeof eggState === 'object' ? eggState : {};
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
  awayMs() {
    return this.special ? this.special.after_s * 1000 : RETURN_AFTER_MS;
  }

  // 返回 {quiet, long}：long 为 true 表示这是「长时间没操作之后的第一次活动」（写信 / 睡着 / 离开很久）
  poke() {
    const t = this.now();
    const quiet = t - this.lastActive;
    const wasSpecial = !!this.special && this.action === this.special.action;
    const wasAsleep = this.action === 'sleep';
    this.lastActive = t;
    if (wasSpecial) {
      // 立刻停笔，回到站立，眨一下眼
      this.action = 'idle';
      this.actionUntil = Infinity;
      this.face = null;
      this.actionFace = false;
      this.blink();
      this.nextIdle = t + this.gap();
    } else if (this.action === 'sleep' || this.action === 'sleepy') {
      this.action = 'idle';
      this.actionUntil = Infinity;
      this.face = null;
      if (wasAsleep) this.feel('surprised', { ms: 900 });
      this.nextIdle = t + this.gap();
    }
    return { quiet, wasSpecial, long: wasSpecial || wasAsleep || quiet >= this.awayMs() };
  }

  // 正在做不能打断的一次性动作（摸头行礼、彩蛋、庆祝…）或被拖着
  oneShot() {
    if (this.action === 'drag') return true;
    return this.now() < this.actionUntil && ACTIONS[this.action].prio >= 3;
  }

  // 掷一次彩蛋。trigger：user_return / task_done。返回 {fired, why, roll, action}
  // 条件：忙碌、冷却未过、今天已达上限时不掷；随机数可注入，结果可复现。触不触发都没有任何奖惩。
  rollEgg(trigger, { busy = false } = {}) {
    const t = this.now();
    const egg = this.eggs.find((e) => e.triggers.includes(trigger));
    if (!egg) return { fired: false, why: 'none' };
    if (busy || this.busy() || this.oneShot() || ACTIONS[this.action].prio >= 2) return { fired: false, why: 'busy' };
    const d = new Date(t);
    const day = `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
    const st = { lastAt: -Infinity, day: '', count: 0, ...(this.eggState[egg.action] || {}) };
    if (typeof st.lastAt !== 'number') st.lastAt = -Infinity;
    const count = st.day === day ? st.count : 0;
    if (t - st.lastAt < egg.cooldown_s * 1000) return { fired: false, why: 'cooldown' };
    if (count >= egg.max_per_day) return { fired: false, why: 'daily' };
    const roll = this.random();
    if (roll >= egg.probability) return { fired: false, why: 'miss', roll };
    this.eggState[egg.action] = { lastAt: t, day, count: count + 1 };
    this.action = 'idle';
    this.actionUntil = 0;
    this.act(egg.action, { ms: egg.ms });
    return { fired: true, why: 'hit', roll, action: egg.action };
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
        this.act(name, { ms: ['sleepy', 'sleep', 'letter', 'bookface'].includes(name) ? 2400 : undefined });
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
    if (this.special) {
      // 有特殊待机的成员：不犯困、不睡觉；时间一到就一直做这个动作，直到下一次活动
      if (this.action === this.special.action) return;
      if (this.action === 'idle' && quiet >= this.special.after_s * 1000 && !this.busy()) {
        return void this.act(this.special.action);
      }
    } else if (this.action === 'idle' || this.action === 'sleepy') {
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

// 触摸屏要长按 LONG_PRESS_MS 才能拖动；鼠标 / 笔按住就能拖
export function canDrag(pointerType, heldMs) {
  return pointerType !== 'touch' || heldMs >= LONG_PRESS_MS;
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
  // 贴在角上时算左右边（小人靠着侧边）
  const edge = edges.has('left') ? 'left' : edges.has('right') ? 'right' : gaps[0][0];
  return { ...p, edge };
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
