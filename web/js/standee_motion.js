// 大立绘的动作与物理：把鼠标、呼吸、待机小动作、点击动作和弹簧摆动算成一帧的「姿势」，交给
// standee_gl.js 去画。单位：原图像素和弧度。所有幅度乘以「动态幅度」设置（0 时完全静止）。

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const ease = (u) => u * u * (3 - 2 * u);
// 动作的包络：前 20% 淡入，后 25% 淡出
const env = (u) => ease(clamp(u / 0.2, 0, 1)) * ease(clamp((1 - u) / 0.25, 0, 1));

/** 一个点击 / 待机动作：时长（秒）和它在时间 u（0～1）时叠加到姿势上的量。 */
export const MOTIONS = {
  nod: { dur: 0.9, f: (u) => ({ pitch: 1.3 * Math.sin(Math.PI * u) * (1 - 0.4 * u) }) },
  tilt: { dur: 2.4, f: (u) => ({ roll: 0.075 * env(u), gazeY: -1.2 * env(u) }) },
  look: {
    dur: 2.8,
    f: (u) => {
      const k = Math.sin(2 * Math.PI * u) * env(u);
      return { yaw: 1.1 * k, gazeX: 3.2 * k };
    },
  },
  read: {
    dur: 3.2,
    f: (u) => {
      const e = env(u);
      return { arm: -0.035 * e, armY: -7 * e, pitch: 0.7 * e, gazeX: -3 * e, gazeY: 2.4 * e, roll: -0.02 * e };
    },
  },
  shake: { dur: 1.3, f: (u) => ({ roll: 0.045 * Math.sin(u * 26) * (1 - u) ** 1.5, pitch: 0.4 * (1 - u) }) },
  stretch: {
    dur: 2.6,
    f: (u) => {
      const e = env(u);
      return { breath: 3.5 * e, pitch: -0.9 * e, roll: 0.03 * Math.sin(Math.PI * u) };
    },
  },
};
export const IDLE_MOTIONS = ['tilt', 'look', 'read', 'nod', 'stretch'];

/** 阻尼弹簧：x 追 target，k 越大越硬，c 越小晃得越久。 */
class Spring {
  constructor(k, c) {
    Object.assign(this, { k, c, x: 0, v: 0, y: 0, vy: 0 });
  }

  step(dt, tx, ty = 0) {
    // 固定小步长积分：掉帧（dt 变大）时也不会发散
    const n = Math.max(1, Math.ceil(dt / 0.008));
    const h = dt / n;
    for (let i = 0; i < n; i++) {
      this.v += (this.k * (tx - this.x) - this.c * this.v) * h;
      this.vy += (this.k * (ty - this.y) - this.c * this.vy) * h;
      this.x += this.v * h;
      this.y += this.vy * h;
    }
    return [this.x, this.y];
  }
}

export class Motion {
  constructor(rand = Math.random) {
    this.rand = rand;
    this.active = []; // {name, t0}
    this.head = new Spring(60, 13); // 头追鼠标：快而不晃
    this.headRoll = new Spring(45, 10);
    this.gaze = new Spring(260, 30); // 眼球最快
    this.hairL = new Spring(11, 2.2);
    this.hairR = new Spring(9, 1.9);
    this.front = new Spring(24, 3.2);
    this.acc = new Spring(30, 2.6);
    this.skirt = new Spring(6, 1.6);
    this.hop = new Spring(90, 11);
    this.saccade = { x: 0, y: 0, at: 0 };
    this.prevRoll = 0;
    this.prevSway = 0;
  }

  /** 播放一个动作（同名动作在播时不重复叠加）。 */
  play(name, now) {
    if (!MOTIONS[name]) return;
    this.active = this.active.filter((a) => a.name !== name);
    this.active.push({ name, t0: now });
  }

  /** 原地小跳一下（点身体、打招呼）。 */
  bounce(v = -1.4) {
    this.hop.vy += v * 60;
  }

  /**
   * 算一帧姿势。input：{pointer:{x,y}（-1～1），motion（幅度），think（0～1），talk（布尔），irisOn}。
   */
  update(dt, now, input) {
    const m = input.motion;
    const t = now / 1000;
    const add = { pitch: 0, yaw: 0, roll: 0, gazeX: 0, gazeY: 0, arm: 0, armY: 0, breath: 0 };
    this.active = this.active.filter((a) => {
      const u = (now - a.t0) / 1000 / MOTIONS[a.name].dur;
      if (u >= 1) return false;
      const d = MOTIONS[a.name].f(clamp(u, 0, 1));
      for (const k in d) add[k] += d[k];
      return true;
    });
    const p = input.pointer;
    // 不看鼠标时偶尔转一下眼睛（扫视）
    if (now > this.saccade.at) {
      this.saccade = { x: (this.rand() - 0.5) * 2.2, y: (this.rand() - 0.5) * 1.2, at: now + 900 + this.rand() * 2600 };
    }
    const [yaw, pitch] = this.head.step(dt, p.x + add.yaw, p.y * 0.8 + add.pitch + input.think * -0.6 + (input.talk ? 0.15 : 0));
    const [roll] = this.headRoll.step(
      dt,
      p.x * 0.03 + Math.sin(t * 0.5) * 0.012 + add.roll + input.think * 0.045,
    );
    const [gx, gy] = this.gaze.step(
      dt,
      p.x * 3.2 + add.gazeX + this.saccade.x * (1 - Math.min(1, Math.abs(p.x) * 2)),
      p.y * 2 + add.gazeY + this.saccade.y - input.think * 2.2,
    );
    const sway = Math.sin(t * 0.37) * 0.0032 + Math.sin(t * 0.23 + 1.3) * 0.0015 + p.x * 0.0018;
    const breath = ((1 - Math.cos((t * 2 * Math.PI) / 4.2)) / 2) * 2.6 + add.breath;
    // 头发和饰品：被头和身体的动作带着走，有滞后和回弹
    const rollV = (roll - this.prevRoll) / Math.max(dt, 1e-3);
    const swayV = (sway - this.prevSway) / Math.max(dt, 1e-3);
    this.prevRoll = roll;
    this.prevSway = sway;
    const drive = -roll * 120 - sway * 500 - yaw * 3;
    const hairL = this.hairL.step(dt, drive - rollV * 5, Math.abs(rollV) * 1.5);
    const hairR = this.hairR.step(dt, drive * 1.1 - rollV * 5.5, Math.abs(rollV) * 1.5);
    const front = this.front.step(dt, -roll * 50 - yaw * 2, pitch);
    const acc = this.acc.step(dt, drive * 0.9 - rollV * 5 - swayV * 200, 0);
    const skirt = this.skirt.step(dt, -sway * 900 - swayV * 600, 0);
    const [, hop] = this.hop.step(dt, 0, 0);
    const k = m;
    return {
      time: t,
      irisOn: input.irisOn,
      roll: roll * k,
      sway: sway * k,
      arm: (Math.sin(t * 0.6) * 0.004 + add.arm) * k,
      breath: breath * k,
      wind: 2.6 * k,
      headT: [yaw * 5 * k, pitch * 3.2 * k],
      faceT: [yaw * 6 * k, pitch * 3 * k],
      armT: [0, add.armY * k],
      gaze: [clamp(gx, -4, 4) * Math.min(k, 1), clamp(gy, -3, 3) * Math.min(k, 1)],
      hop: [0, hop * Math.min(k, 1)],
      hairL: [hairL[0] * k, hairL[1] * k],
      hairR: [hairR[0] * k, hairR[1] * k],
      front: [front[0] * k, front[1] * k],
      acc: [acc[0] * k, acc[1] * k],
      skirtD: [skirt[0] * k, 0],
    };
  }
}
