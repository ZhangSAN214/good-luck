// Live2D 行为驱动器：把「情绪 / 注意点 / 说话状态」变成模型参数。纯逻辑，不碰 DOM，可在 node 里测试。
//
// 用法：
//   const d = new BehaviorDriver({ random, ranges });   // ranges: {参数 id: [最小, 最大]}（可选）
//   d.setEmotion('happy'); d.lookAt(0.4, -0.2); d.setSpeaking(true); d.setAudioLevel(rms);
//   const params = d.update(dt);                          // {标准参数 id: 值}
//   const map = new ParamMap(模型的参数列表); map.apply(params, (id, v) => 设置参数);
//
// 设计要点（老板要「二游主界面那种灵动」，不能一眼看出是循环脚本）：
// - 视线先动、头再跟：眼睛用快弹簧，头在视线停留一会儿或偏得够远时才跟上，用慢弹簧；
// - 情绪是状态机：平静、开心、害羞、思考、惊讶、坏笑，切换时按各自的时长缓动混合，惊讶会自己退回；
// - 眨眼间隔随机（偶尔连眨两下，大幅扫视时更容易眨），闲置时视线做小幅随机扫视，头和身体用平滑噪声漂移；
// - 口型按音量包络（快开慢合）；没有音频时可以按随机音节假装说话；
// - 留好接口：setMood（群聊心情）、setAudioLevel / setSpeaking（以后接 TTS）。

export const EMOTIONS = {
  calm: { hold: 0, enter: 0.6, params: {} },
  happy: {
    hold: 0,
    enter: 0.45,
    params: {
      ParamEyeLSmile: 0.85, ParamEyeRSmile: 0.85, ParamMouthForm: 1, ParamCheek: 0.35,
      ParamBrowLY: 0.35, ParamBrowRY: 0.35, eyeOpen: 0.82, headY: 3, bounce: 1,
    },
  },
  shy: {
    hold: 0,
    enter: 0.7,
    params: {
      ParamCheek: 1, ParamMouthForm: 0.35, ParamEyeLSmile: 0.3, ParamEyeRSmile: 0.3,
      ParamBrowLY: 0.15, ParamBrowRY: 0.15, eyeOpen: 0.72, gazeX: 0.45, gazeY: -0.45,
      headY: -9, headZ: 7,
    },
  },
  thinking: {
    hold: 0,
    enter: 0.8,
    params: {
      ParamMouthForm: -0.25, ParamBrowLY: 0.3, ParamBrowRY: -0.15, eyeOpen: 0.9,
      gazeX: -0.55, gazeY: 0.55, headZ: -7, headX: -6,
    },
  },
  surprised: {
    hold: [1.2, 2.4],
    enter: 0.12,
    params: {
      ParamBrowLY: 1, ParamBrowRY: 1, ParamMouthOpenY: 0.45, ParamMouthForm: -0.4,
      eyeOpen: 1.25, headY: 5,
    },
  },
  smirk: {
    hold: 0,
    enter: 0.5,
    params: {
      ParamEyeLSmile: 0.65, ParamEyeRSmile: 0.15, ParamMouthForm: 0.85, ParamBrowLY: 0.35,
      ParamBrowRY: -0.35, eyeOpen: 0.74, gazeX: 0.35, headZ: 8, headY: -4,
    },
  },
};

// 群聊心情（以后从成员引擎来）→ 情绪；不认识的词回到平静
export const MOOD_TO_EMOTION = {
  calm: 'calm', neutral: 'calm', 平静: 'calm',
  happy: 'happy', joy: 'happy', excited: 'happy', 开心: 'happy', 高兴: 'happy',
  shy: 'shy', embarrassed: 'shy', 害羞: 'shy',
  thinking: 'thinking', curious: 'thinking', 思考: 'thinking', 好奇: 'thinking',
  surprised: 'surprised', shocked: 'surprised', 惊讶: 'surprised',
  smirk: 'smirk', teasing: 'smirk', mischievous: 'smirk', 坏笑: 'smirk', 腹黑: 'smirk',
};

// 随机挑模型自带表情时不要抽到的（流冷汗、哭、阴沉、生气这类）
export const EXPRESSION_DENYLIST = /sweat|tear|cry|gloom|angry|sad|dark|汗|泪|哭|阴沉|生气/i;

export function pickIdleExpression(names, random = Math.random) {
  const ok = (names || []).filter((n) => !EXPRESSION_DENYLIST.test(n));
  return ok.length ? ok[Math.floor(random() * ok.length) % ok.length] : null;
}

// 标准参数 id → 常见别名（Cubism 2 风格、各工具的写法）。模型里实际叫什么以 ParamMap 的匹配结果为准。
export const PARAM_ALIASES = {
  ParamAngleX: ['PARAM_ANGLE_X'], ParamAngleY: ['PARAM_ANGLE_Y'], ParamAngleZ: ['PARAM_ANGLE_Z'],
  ParamEyeBallX: ['PARAM_EYE_BALL_X'], ParamEyeBallY: ['PARAM_EYE_BALL_Y'],
  ParamEyeLOpen: ['PARAM_EYE_L_OPEN'], ParamEyeROpen: ['PARAM_EYE_R_OPEN'],
  ParamEyeLSmile: ['PARAM_EYE_L_SMILE'], ParamEyeRSmile: ['PARAM_EYE_R_SMILE'],
  ParamBrowLY: ['PARAM_BROW_L_Y'], ParamBrowRY: ['PARAM_BROW_R_Y'],
  ParamMouthOpenY: ['PARAM_MOUTH_OPEN_Y'], ParamMouthForm: ['PARAM_MOUTH_FORM'],
  ParamCheek: ['PARAM_CHEEK'], ParamBreath: ['PARAM_BREATH'],
  ParamBodyAngleX: ['PARAM_BODY_ANGLE_X'], ParamBodyAngleY: ['PARAM_BODY_ANGLE_Y'],
  ParamBodyAngleZ: ['PARAM_BODY_ANGLE_Z'],
};

// 模型没给范围时用的默认范围（Cubism 标准）
export const DEFAULT_RANGES = {
  ParamAngleX: [-30, 30], ParamAngleY: [-30, 30], ParamAngleZ: [-30, 30],
  ParamEyeBallX: [-1, 1], ParamEyeBallY: [-1, 1], ParamEyeLOpen: [0, 1], ParamEyeROpen: [0, 1],
  ParamEyeLSmile: [0, 1], ParamEyeRSmile: [0, 1], ParamBrowLY: [-1, 1], ParamBrowRY: [-1, 1],
  ParamMouthOpenY: [0, 1], ParamMouthForm: [-1, 1], ParamCheek: [0, 1], ParamBreath: [0, 1],
  ParamBodyAngleX: [-10, 10], ParamBodyAngleY: [-10, 10], ParamBodyAngleZ: [-10, 10],
};

export const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

// 可复现的随机数（测试和录演示用）
export function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// 一维平滑噪声：随机控制点 + smoothstep 插值，比几个正弦叠加更不像循环
export class SmoothNoise {
  constructor(random, period = 2) {
    this.random = random;
    this.period = period;
    this.t = 0;
    this.a = random() * 2 - 1;
    this.b = random() * 2 - 1;
    this.len = period * (0.6 + random() * 0.8);
  }
  step(dt) {
    this.t += dt;
    while (this.t >= this.len) {
      this.t -= this.len;
      this.a = this.b;
      this.b = this.random() * 2 - 1;
      this.len = this.period * (0.6 + this.random() * 0.8);
    }
    const u = this.t / this.len;
    return this.a + (this.b - this.a) * u * u * (3 - 2 * u);
  }
}

// 二阶临界阻尼附近的弹簧（ω 越大越快，ζ<1 有一点过冲）
export class Spring {
  constructor(omega, zeta = 1, value = 0) {
    this.omega = omega;
    this.zeta = zeta;
    this.value = value;
    this.vel = 0;
  }
  step(target, dt) {
    const n = Math.max(1, Math.ceil(dt / 0.008));
    const h = dt / n;
    for (let i = 0; i < n; i++) {
      const acc = this.omega * this.omega * (target - this.value) - 2 * this.zeta * this.omega * this.vel;
      this.vel += acc * h;
      this.value += this.vel * h;
    }
    return this.value;
  }
}

const between = (random, lo, hi) => lo + (hi - lo) * random();

export class BehaviorDriver {
  constructor({ random = Math.random, ranges = {} } = {}) {
    this.random = random;
    this.ranges = { ...DEFAULT_RANGES, ...ranges };
    this.time = 0;
    // 情绪：当前状态 + 每种情绪的混合权重
    this.emotion = 'calm';
    this.returnTo = 'calm';
    this.emotionTimer = 0;
    this.weights = Object.fromEntries(Object.keys(EMOTIONS).map((k) => [k, k === 'calm' ? 1 : 0]));
    // 注意点
    this.target = null; // {x, y}，-1～1；null = 闲置扫视
    this.idleGaze = { x: 0, y: 0 };
    this.saccadeIn = between(random, 0.6, 2.0);
    this.eyeX = new Spring(38, 0.9);
    this.eyeY = new Spring(38, 0.9);
    this.headGoal = { x: 0, y: 0 };
    this.headDwell = 0;
    this.headX = new Spring(5.5, 0.82);
    this.headY = new Spring(5.5, 0.85);
    this.bodyX = new Spring(2.6, 0.9);
    // 自然漂移
    this.drift = [0.9, 1.3, 1.7, 2.3, 3.1].map((p) => new SmoothNoise(random, p * 1.8));
    // 眨眼
    this.blinkIn = between(random, 1.2, 3.5);
    this.blink = null; // {t, close, hold, open}
    this.queuedBlink = false;
    // 呼吸
    this.breathPhase = random();
    this.breathPeriod = between(random, 3.4, 4.6);
    // 说话
    this.speaking = false;
    this.audioLevel = null; // 有音量就按音量，没有就按随机音节
    this.mouth = 0;
    this.syllable = null;
    this.emphasis = new Spring(14, 0.6);
    this.formNoise = new SmoothNoise(random, 0.5);
  }

  // ---------- 输入接口 ----------
  setEmotion(name) {
    if (!EMOTIONS[name]) return false;
    if (name === this.emotion) return true;
    const spec = EMOTIONS[name];
    if (spec.hold) {
      if (!EMOTIONS[this.emotion].hold) this.returnTo = this.emotion;
      this.emotionTimer = between(this.random, spec.hold[0], spec.hold[1]);
      this.queuedBlink = true; // 吃惊时先眨一下
    }
    this.emotion = name;
    return true;
  }

  setMood(mood) {
    // 群聊心情的接口：字符串，或 {label} / {valence, arousal}
    if (typeof mood === 'string') return this.setEmotion(MOOD_TO_EMOTION[mood] || 'calm');
    if (mood && mood.label) return this.setMood(String(mood.label));
    if (mood && typeof mood.valence === 'number') {
      const v = mood.valence;
      const a = mood.arousal ?? 0;
      if (a > 0.7) return this.setEmotion('surprised');
      if (v > 0.4) return this.setEmotion(a > 0.2 ? 'happy' : 'smirk');
      if (v < -0.2) return this.setEmotion('shy');
      return this.setEmotion(a < -0.3 ? 'thinking' : 'calm');
    }
    return this.setEmotion('calm');
  }

  lookAt(x, y) {
    this.target = x == null ? null : { x: clamp(x, -1, 1), y: clamp(y ?? 0, -1, 1) };
  }

  setSpeaking(on) {
    this.speaking = !!on;
    if (!on) this.syllable = null;
  }

  setAudioLevel(level) {
    // 0～1 的音量（RMS）；传 null 回到随机音节
    this.audioLevel = level == null ? null : clamp(level, 0, 1);
  }

  // ---------- 每帧 ----------
  update(dt) {
    dt = clamp(dt, 0, 0.1);
    this.time += dt;
    const out = {};
    const emo = this._updateEmotion(dt);
    const gaze = this._updateGaze(dt, emo);
    const eyeOpen = this._updateBlink(dt) * (emo.eyeOpen ?? 1);
    const mouth = this._updateMouth(dt);

    const [ax0, ax1] = this.ranges.ParamAngleX;
    const reachX = Math.min(Math.abs(ax0), Math.abs(ax1)) * 0.92; // 左右尽量转大
    const reachY = Math.min(Math.abs(this.ranges.ParamAngleY[0]), this.ranges.ParamAngleY[1]) * 0.7;
    const d = this.drift.map((n) => n.step(dt));
    const hx = this.headX.value;
    const hy = this.headY.value;
    out.ParamAngleX = hx * reachX + d[0] * 2.5 + (emo.headX ?? 0);
    out.ParamAngleY = hy * reachY + d[1] * 2 + (emo.headY ?? 0) + this.emphasis.value * 4;
    out.ParamAngleZ = -hx * hy * 8 + d[2] * 3 + (emo.headZ ?? 0);
    // 眼球相对头：头转过去以后眼睛回正一些
    out.ParamEyeBallX = clamp((gaze.x - hx * 0.65) * 1.35, -1, 1);
    out.ParamEyeBallY = clamp((gaze.y - hy * 0.65) * 1.35, -1, 1);
    out.ParamBodyAngleX = this.bodyX.step(hx * 0.45, dt) * 10 + d[3] * 1.2;
    out.ParamBodyAngleZ = d[4] * 1.6 + (emo.headZ ?? 0) * 0.25;
    out.ParamBodyAngleY = (emo.bounce ?? 0) * Math.max(0, Math.sin(this.time * 5)) * 1.5;
    this.breathPhase = (this.breathPhase + dt / this.breathPeriod) % 1;
    if (this.breathPhase < dt / this.breathPeriod) this.breathPeriod = between(this.random, 3.4, 4.6);
    out.ParamBreath = 0.5 + 0.5 * Math.sin(this.breathPhase * 2 * Math.PI);
    out.ParamEyeLOpen = eyeOpen;
    out.ParamEyeROpen = eyeOpen;
    for (const key of ['ParamEyeLSmile', 'ParamEyeRSmile', 'ParamCheek', 'ParamBrowLY', 'ParamBrowRY']) {
      out[key] = emo[key] ?? 0;
    }
    const form = (emo.ParamMouthForm ?? 0.15) + (this.speaking ? this.formNoise.step(dt) * 0.3 : 0);
    out.ParamMouthForm = clamp(form, -1, 1);
    out.ParamMouthOpenY = clamp(Math.max(mouth, emo.ParamMouthOpenY ?? 0), 0, 1);
    for (const [k, v] of Object.entries(out)) {
      const r = this.ranges[k];
      if (r) out[k] = clamp(v, r[0], r[1]);
    }
    return out;
  }

  _updateEmotion(dt) {
    if (EMOTIONS[this.emotion].hold) {
      this.emotionTimer -= dt;
      if (this.emotionTimer <= 0) this.emotion = this.returnTo;
    }
    const tau = EMOTIONS[this.emotion].enter;
    const k = 1 - Math.exp(-dt / Math.max(tau / 3, 1e-3));
    let total = 0;
    for (const name of Object.keys(this.weights)) {
      const goal = name === this.emotion ? 1 : 0;
      this.weights[name] += (goal - this.weights[name]) * k;
      total += this.weights[name];
    }
    const mix = {};
    for (const [name, w] of Object.entries(this.weights)) {
      const p = EMOTIONS[name].params;
      for (const [key, v] of Object.entries(p)) {
        const base = key === 'eyeOpen' ? 1 : 0;
        mix[key] = (mix[key] ?? base) + (v - base) * (w / total);
      }
    }
    return mix;
  }

  _updateGaze(dt, emo) {
    let goal;
    if (this.target) {
      goal = { x: this.target.x, y: this.target.y };
    } else {
      this.saccadeIn -= dt;
      if (this.saccadeIn <= 0) {
        // 闲置扫视：大多是小跳，偶尔看远一点
        const far = this.random() < 0.2;
        const amp = far ? between(this.random, 0.35, 0.7) : between(this.random, 0.05, 0.25);
        const ang = this.random() * Math.PI * 2;
        const nx = clamp(Math.cos(ang) * amp, -1, 1);
        const ny = clamp(Math.sin(ang) * amp * 0.6, -1, 1);
        if (Math.hypot(nx - this.idleGaze.x, ny - this.idleGaze.y) > 0.45 && this.random() < 0.35) {
          this.queuedBlink = true; // 大幅扫视时常伴一次眨眼
        }
        this.idleGaze = { x: nx, y: ny };
        this.saccadeIn = between(this.random, 0.5, 2.6);
      }
      goal = { ...this.idleGaze };
    }
    goal.x = clamp(goal.x + (emo.gazeX ?? 0), -1, 1);
    goal.y = clamp(goal.y + (emo.gazeY ?? 0), -1, 1);
    const ex = this.eyeX.step(goal.x, dt);
    const ey = this.eyeY.step(goal.y, dt);
    // 头：视线偏离当前头的目标够远、或停留够久才重新定目标（视线先动，头后跟）
    const off = Math.hypot(goal.x * 0.85 - this.headGoal.x, goal.y * 0.7 - this.headGoal.y);
    this.headDwell += dt;
    if (off > 0.3 || (off > 0.08 && this.headDwell > 0.45)) {
      this.headGoal = { x: goal.x * 0.85, y: goal.y * 0.7 };
      this.headDwell = 0;
      this.headDelay = between(this.random, 0.12, 0.28);
    }
    if (this.headDelay > 0) {
      this.headDelay -= dt;
      this.headX.step(this.headX.target ?? 0, dt);
      this.headY.step(this.headY.target ?? 0, dt);
    } else {
      this.headX.target = this.headGoal.x;
      this.headY.target = this.headGoal.y;
      this.headX.step(this.headGoal.x, dt);
      this.headY.step(this.headGoal.y, dt);
    }
    return { x: ex, y: ey };
  }

  _updateBlink(dt) {
    if (!this.blink) {
      this.blinkIn -= dt;
      if (this.blinkIn <= 0 || this.queuedBlink) {
        this.queuedBlink = false;
        this.blink = {
          t: 0,
          close: between(this.random, 0.06, 0.1),
          hold: between(this.random, 0.02, 0.06),
          open: between(this.random, 0.12, 0.2),
        };
        // 间隔：2～7 秒，偏短；15% 接着再眨一次
        this.blinkIn =
          this.random() < 0.15 ? between(this.random, 0.25, 0.4) : 2 + Math.min(5, -Math.log(1 - this.random()) * 1.8);
      }
    }
    if (!this.blink) return 1;
    const b = this.blink;
    b.t += dt;
    let v;
    if (b.t < b.close) v = 1 - b.t / b.close;
    else if (b.t < b.close + b.hold) v = 0;
    else if (b.t < b.close + b.hold + b.open) {
      const u = (b.t - b.close - b.hold) / b.open;
      v = 1 - (1 - u) * (1 - u); // 睁开先快后慢
    } else {
      this.blink = null;
      v = 1;
    }
    return v;
  }

  _updateMouth(dt) {
    let goal = 0;
    if (this.speaking) {
      if (this.audioLevel != null) {
        // 音量 → 开口：去掉底噪，压一下大音量
        goal = clamp((this.audioLevel - 0.02) * 6, 0, 1) ** 0.8;
      } else {
        // 没音频：随机音节（开合时长、大小、停顿都随机）
        if (!this.syllable || this.syllable.t >= this.syllable.len) {
          const pause = this.random() < 0.12;
          this.syllable = {
            t: 0,
            len: pause ? between(this.random, 0.15, 0.35) : between(this.random, 0.11, 0.22),
            peak: pause ? 0 : between(this.random, 0.35, 1),
          };
          if (!pause && this.syllable.peak > 0.8) this.emphasis.vel += 2.2;
        }
        this.syllable.t += dt;
        goal = this.syllable.peak * Math.sin(Math.PI * clamp(this.syllable.t / this.syllable.len, 0, 1));
      }
    }
    // 快开慢合
    const tau = goal > this.mouth ? 0.035 : 0.09;
    this.mouth += (goal - this.mouth) * (1 - Math.exp(-dt / tau));
    this.emphasis.step(0, dt);
    return this.mouth;
  }
}

// 标准参数 → 模型实际参数：按 id 精确匹配，其次别名；对不上的列出来，方便核对
export class ParamMap {
  constructor(modelParams) {
    // modelParams: [{id, min, max}]
    this.byId = new Map(modelParams.map((p) => [p.id, p]));
    this.map = {};
    this.missing = [];
    for (const std of Object.keys(DEFAULT_RANGES)) {
      const hit = [std, ...(PARAM_ALIASES[std] || [])].find((id) => this.byId.has(id));
      if (hit) this.map[std] = hit;
      else this.missing.push(std);
    }
  }
  ranges() {
    const out = {};
    for (const [std, id] of Object.entries(this.map)) {
      const p = this.byId.get(id);
      if (p && Number.isFinite(p.min) && Number.isFinite(p.max)) out[std] = [p.min, p.max];
    }
    return out;
  }
  apply(params, set) {
    let n = 0;
    for (const [std, v] of Object.entries(params)) {
      const id = this.map[std];
      if (!id) continue;
      const p = this.byId.get(id);
      set(id, p ? clamp(v, p.min, p.max) : v);
      n += 1;
    }
    return n;
  }
}

// 音量计：从 WebAudio 的时域数据算 RMS（播放页和 TTS 用）
export function rms(samples) {
  if (!samples || !samples.length) return 0;
  let s = 0;
  for (let i = 0; i < samples.length; i++) s += samples[i] * samples[i];
  return Math.sqrt(s / samples.length);
}
