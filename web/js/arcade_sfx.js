// 游戏区音效：全部用 WebAudio 现场合成（不带音频文件，原创）。
// 发牌是轻快的「嗒」，坦克开炮是短促的「嘭」，出牌是牌拍在呢面上的「啪」，炸弹是低沉的轰，筹码是清脆的叮，赢了一串上行的琶音。
// 开关存在本机 localStorage（roundtable.arcade.sfx），默认开；浏览器要先有一次点击才肯出声。

const KEY = 'roundtable.arcade.sfx';
let ctx = null;
let bus = null;
let noiseBuf = null;

export function sfxOn() {
  try {
    return window.localStorage.getItem(KEY) !== 'off';
  } catch {
    return true;
  }
}

export function setSfx(on) {
  try {
    window.localStorage.setItem(KEY, on ? 'on' : 'off');
  } catch {
    /* 本机存不了就只管这一次 */
  }
}

function audio() {
  if (ctx) return ctx;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  try {
    ctx = new AC();
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -16;
    comp.ratio.value = 3;
    comp.connect(ctx.destination);
    bus = ctx.createGain();
    bus.gain.value = 0.5;
    bus.connect(comp);
    noiseBuf = ctx.createBuffer(1, ctx.sampleRate * 0.6, ctx.sampleRate);
    const d = noiseBuf.getChannelData(0);
    for (let k = 0; k < d.length; k += 1) d[k] = Math.random() * 2 - 1;
  } catch {
    ctx = null;
  }
  return ctx;
}

function env(g, at, dur, peak, attack = 0.004) {
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(peak, at + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
}

function tone(hz, at, dur, { type = 'sine', gain = 0.2, to = 0, attack = 0.004 } = {}) {
  const o = ctx.createOscillator();
  const g = ctx.createGain();
  o.type = type;
  o.frequency.setValueAtTime(hz, at);
  if (to) o.frequency.exponentialRampToValueAtTime(to, at + dur);
  env(g, at, dur, gain, attack);
  o.connect(g).connect(bus);
  o.start(at);
  o.stop(at + dur + 0.02);
}

function noise(at, dur, { gain = 0.2, type = 'bandpass', hz = 2000, q = 0.8, to = 0 } = {}) {
  const src = ctx.createBufferSource();
  src.buffer = noiseBuf;
  const f = ctx.createBiquadFilter();
  f.type = type;
  f.frequency.setValueAtTime(hz, at);
  if (to) f.frequency.exponentialRampToValueAtTime(to, at + dur);
  f.Q.value = q;
  const g = ctx.createGain();
  env(g, at, dur, gain, 0.002);
  src.connect(f).connect(g).connect(bus);
  src.start(at);
  src.stop(at + dur + 0.02);
}

const SOUNDS = {
  /** 发一张牌：轻快的纸面擦过。 */
  deal(at) {
    noise(at, 0.06, { gain: 0.16, type: 'highpass', hz: 3200 });
    tone(900, at, 0.03, { type: 'triangle', gain: 0.03 });
  },
  /** 出牌：牌拍在呢面上。 */
  play(at) {
    noise(at, 0.09, { gain: 0.32, type: 'lowpass', hz: 2600, to: 700 });
    tone(150, at, 0.12, { gain: 0.22, to: 70 });
  },
  /** 不要 / 过牌：指节轻敲桌面。 */
  pass(at) {
    tone(320, at, 0.07, { type: 'triangle', gain: 0.07, to: 240 });
    noise(at, 0.04, { gain: 0.06, hz: 900, q: 2 });
  },
  /** 炸弹：低沉的轰 + 碎响。 */
  bomb(at) {
    tone(90, at, 0.9, { gain: 0.5, to: 32, attack: 0.01 });
    tone(45, at + 0.02, 1.1, { type: 'triangle', gain: 0.25, to: 28 });
    noise(at, 0.7, { gain: 0.35, type: 'lowpass', hz: 4200, to: 300 });
    noise(at + 0.05, 0.25, { gain: 0.2, hz: 6000, q: 0.5 });
  },
  /** 下注 / 金币：两声清脆的叮。 */
  chip(at) {
    tone(2350, at, 0.12, { type: 'triangle', gain: 0.09 });
    tone(3120, at + 0.05, 0.14, { type: 'triangle', gain: 0.07 });
    noise(at, 0.03, { gain: 0.05, hz: 5000, q: 3 });
  },
  /** 坦克开炮：短促的「嘭」。 */
  shoot(at) {
    tone(220, at, 0.09, { type: 'square', gain: 0.05, to: 90 });
    noise(at, 0.07, { gain: 0.12, type: 'lowpass', hz: 1800, to: 500 });
  },
  /** 坦克被击毁：中等的爆炸。 */
  hit(at) {
    tone(120, at, 0.45, { gain: 0.3, to: 40, attack: 0.006 });
    noise(at, 0.4, { gain: 0.25, type: 'lowpass', hz: 3000, to: 250 });
  },
  /** 捡到补给：上行的两声。 */
  power(at) {
    tone(880, at, 0.12, { type: 'triangle', gain: 0.08, to: 1320 });
    tone(1320, at + 0.08, 0.16, { type: 'triangle', gain: 0.07 });
  },
  /** 选中一张手牌。 */
  pick(at) {
    tone(1500, at, 0.035, { type: 'triangle', gain: 0.05, to: 1900 });
  },
  /** 轮到你：柔和的两声提示。 */
  turn(at) {
    tone(660, at, 0.22, { type: 'sine', gain: 0.08 });
    tone(990, at + 0.11, 0.3, { type: 'sine', gain: 0.07 });
  },
  /** 赢了：上行琶音 + 一点闪光。 */
  win(at) {
    [523.25, 659.25, 783.99, 1046.5, 1318.5].forEach((hz, k) => tone(hz, at + k * 0.07, 0.4, { type: 'triangle', gain: 0.09 }));
    noise(at + 0.3, 0.4, { gain: 0.05, type: 'highpass', hz: 7000 });
  },
  /** 输了：两声下行。 */
  lose(at) {
    tone(392, at, 0.3, { type: 'triangle', gain: 0.08 });
    tone(311.1, at + 0.18, 0.45, { type: 'triangle', gain: 0.08 });
  },
};

/** 放一个音效；n > 1 时连着放（发牌）。 */
export function sfx(name, n = 1) {
  if (!sfxOn() || !SOUNDS[name]) return;
  const c = audio();
  if (!c) return;
  if (c.state === 'suspended') c.resume().catch(() => {});
  const at = c.currentTime + 0.01;
  for (let k = 0; k < Math.min(n, 30); k += 1) SOUNDS[name](at + k * 0.045);
}
