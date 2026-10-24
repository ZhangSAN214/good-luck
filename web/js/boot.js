// 开机动画：打开圆桌时先放「晨会协议」片头，再接 GMU（早上好联合）访问授权，然后淡入首页。
// 普通脚本（不是模块），放在 <body> 最前面同步执行：首页还没画出来就先盖住，不会闪一下。
// 设置都存在本机 localStorage（roundtable.boot.*），在「片头」设置里改；网址加 ?boot=0 不放。
// 声音都在这一层：浏览器要先有一次点击才肯出声，所以没点过时先显示「按下开机」；
// 动画页只发 boot:sfx（音效名）和 boot:say（中英两句台词），这里用 WebAudio 合成音效、用系统女声朗读。
// 授权页改编自一个 MIT 许可的开源开机动画，片头是圆桌原创，出处见 web/boot/CREDITS.md。
(() => {
  const PREFIX = 'roundtable.boot.';
  const MODES = ['full', 'intro', 'permit', 'off'];
  const read = (key, fallback) => {
    try {
      const v = window.localStorage.getItem(PREFIX + key);
      return v === null || v === '' ? fallback : v;
    } catch {
      return fallback;
    }
  };
  const FADE_MS = 600;
  const MAX_MS = 90_000; // 兜底：动画页出错没通知时，最多盖这么久

  /* ── 音效：全部现场合成，不带任何音频文件 ──
     军事科幻、严肃：低频冲击、锯齿铜管般的轰鸣、继电器咔哒、低沉的声呐、电台底噪；不用清脆的高音。
     总线：各声部 → 干声 + 一路空间回声（延迟 + 反馈 + 低通）→ 压缩器 → 总音量。
     背景另有一层电流嗡声、静电噼啪和远处的电报码（ambient），整段开机都在，结束时淡出。 */
  let ctx = null;
  let master = null;
  let bed = null; // 背景声的音量节点
  function audio() {
    if (ctx) return ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try {
      ctx = new AC();
      const comp = ctx.createDynamicsCompressor();
      comp.threshold.value = -18;
      comp.knee.value = 8;
      comp.ratio.value = 4;
      comp.attack.value = 0.004;
      comp.release.value = 0.25;
      comp.connect(ctx.destination);
      master = ctx.createGain();
      master.gain.value = 0.42;
      master.connect(comp);
      // 空间感：一路带反馈的延迟，低通后混回去
      const send = ctx.createGain();
      send.gain.value = 0.22;
      const delay = ctx.createDelay(1);
      delay.delayTime.value = 0.26;
      const fb = ctx.createGain();
      fb.gain.value = 0.38;
      const damp = ctx.createBiquadFilter();
      damp.type = 'lowpass';
      damp.frequency.value = 1800;
      master.connect(send).connect(delay).connect(damp).connect(fb).connect(delay);
      damp.connect(comp);
    } catch {
      ctx = null;
    }
    return ctx;
  }
  // D 小调低音区：七个席位的声呐
  const SEAT_HZ = [146.83, 174.61, 196, 220, 261.63, 293.66, 349.23];
  function env(g, at, dur, peak, attack) {
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(peak, at + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  }
  /** 单个振荡器，可选低通滤波（扫频）。 */
  function tone(hz, at, dur, { type = 'sine', gain = 0.3, attack = 0.01, to = null, lp = 0, lpTo = 0, detune = 0, out = null } = {}) {
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = type;
    o.detune.value = detune;
    o.frequency.setValueAtTime(hz, at);
    if (to) o.frequency.exponentialRampToValueAtTime(to, at + dur);
    env(g, at, dur, gain, attack);
    let node = o;
    if (lp) {
      const f = ctx.createBiquadFilter();
      f.type = 'lowpass';
      f.Q.value = 2;
      f.frequency.setValueAtTime(lp, at);
      if (lpTo) f.frequency.exponentialRampToValueAtTime(lpTo, at + dur * 0.6);
      node = node.connect(f);
    }
    node.connect(g).connect(out || master);
    o.start(at);
    o.stop(at + dur + 0.05);
  }
  let noiseBuf = null;
  function noise(at, dur, { gain = 0.12, from = 400, to = 4000, q = 0.8, type = 'bandpass', attack = null, out = null } = {}) {
    if (!noiseBuf) {
      const len = ctx.sampleRate * 2;
      noiseBuf = ctx.createBuffer(1, len, ctx.sampleRate);
      const d = noiseBuf.getChannelData(0);
      for (let i = 0; i < len; i += 1) d[i] = Math.random() * 2 - 1;
    }
    const src = ctx.createBufferSource();
    src.buffer = noiseBuf;
    src.loop = true;
    const f = ctx.createBiquadFilter();
    f.type = type;
    f.Q.value = q;
    f.frequency.setValueAtTime(from, at);
    f.frequency.exponentialRampToValueAtTime(to, at + dur);
    const g = ctx.createGain();
    env(g, at, dur, gain, attack ?? dur * 0.4);
    src.connect(f).connect(g).connect(out || master);
    src.start(at, Math.random());
    src.stop(at + dur + 0.05);
  }
  /** 低频冲击：正弦从高处急坠，像远处的重物落地。 */
  const thump = (at, { gain = 0.5, from = 120, to = 38, dur = 0.7 } = {}) =>
    tone(from, at, dur, { gain, attack: 0.004, to });
  /** 继电器 / 机械咔哒：极短的带通噪声，压在中频，不刺耳。 */
  const click = (at, gain = 0.07, hz = 1600, out = null) =>
    noise(at, 0.02, { gain, from: hz, to: hz * 1.3, q: 3, attack: 0.001, out });
  /** 锯齿铜管轰鸣：几层失谐锯齿 + 低通慢开。 */
  function braam(at, root, dur, gain = 0.12) {
    for (const [mul, det] of [[1, -9], [1, 9], [2, 0], [1.5, 4]]) {
      tone(root * mul, at, dur, { type: 'sawtooth', gain: gain / (mul > 1 ? 2.2 : 1), attack: 0.08, lp: 160, lpTo: 1100, detune: det });
    }
  }

  /** 失真曲线（tanh），给电吉他用。 */
  let driveCurve = null;
  function drive() {
    if (!driveCurve) {
      driveCurve = new Float32Array(1024);
      for (let i = 0; i < 1024; i += 1) {
        const x = (i / 1023) * 2 - 1;
        driveCurve[i] = Math.tanh(x * 6) / Math.tanh(6);
      }
    }
    const ws = ctx.createWaveShaper();
    ws.curve = driveCurve;
    ws.oversample = '2x';
    return ws;
  }
  /** 电吉他：失谐锯齿 → 失真 → 箱体（低通 + 去低频）。mute=true 为闷音短切。 */
  function guitar(at, notes, dur, { gain = 0.05, mute = false, attack = 0.01 } = {}) {
    const pre = ctx.createGain();
    pre.gain.value = 0.6;
    const ws = drive();
    const hp = ctx.createBiquadFilter();
    hp.type = 'highpass';
    hp.frequency.value = 90;
    const cab = ctx.createBiquadFilter();
    cab.type = 'lowpass';
    cab.Q.value = 0.9;
    cab.frequency.setValueAtTime(mute ? 900 : 2400, at);
    if (mute) cab.frequency.exponentialRampToValueAtTime(380, at + dur);
    const g = ctx.createGain();
    env(g, at, dur, gain, attack);
    pre.connect(ws).connect(hp).connect(cab).connect(g).connect(master);
    for (const hz of notes) {
      for (const det of [-7, 7]) {
        const o = ctx.createOscillator();
        o.type = 'sawtooth';
        o.frequency.value = hz;
        o.detune.value = det;
        o.connect(pre);
        o.start(at);
        o.stop(at + dur + 0.05);
      }
    }
  }
  /** 贝斯：锯齿 + 正弦打底，低通随拨弦收拢。 */
  function bass(at, hz, dur, gain = 0.09) {
    const f = ctx.createBiquadFilter();
    f.type = 'lowpass';
    f.Q.value = 4;
    f.frequency.setValueAtTime(700, at);
    f.frequency.exponentialRampToValueAtTime(140, at + dur);
    const g = ctx.createGain();
    env(g, at, dur, gain, 0.006);
    f.connect(g).connect(master);
    // 锯齿给拨弦的颗粒感，低八度的正弦给分量
    for (const [type, mul] of [['sawtooth', 1], ['sine', 0.5]]) {
      const o = ctx.createOscillator();
      o.type = type;
      o.frequency.value = hz * mul;
      o.connect(f);
      o.start(at);
      o.stop(at + dur + 0.05);
    }
  }
  // D5 强力和弦（D3 + A3 + D4），与贝斯的 D2 同调
  const D5 = [146.83, 220, 293.66];

  /* ── 背景：电流嗡声 + 静电噼啪 + 远处的电报码 ── */
  const MORSE = { A: '.-', C: '-.-.', E: '.', G: '--.', M: '--', N: '-.', O: '---', Q: '--.-', R: '.-.', S: '...', T: '-', U: '..-', 7: '--...', ' ': ' ' };
  const CABLES = ['GMU', 'CQ CQ GMU', 'GM 7', 'GMU QRV', 'SEATS 7'];
  let bedTimers = [];
  function startBed() {
    if (bed || read('sound', 'on') === 'off' || !audio() || ctx.state !== 'running') return;
    const t = ctx.currentTime;
    bed = ctx.createGain();
    bed.gain.setValueAtTime(0.0001, t);
    bed.gain.exponentialRampToValueAtTime(1, t + 1.2);
    bed.connect(master);
    const nodes = [];
    // 电流嗡声：50Hz 与谐波，低通，慢慢起伏
    const humLp = ctx.createBiquadFilter();
    humLp.type = 'lowpass';
    humLp.frequency.value = 420;
    const humGain = ctx.createGain();
    humGain.gain.value = 0.05;
    const lfo = ctx.createOscillator();
    const lfoGain = ctx.createGain();
    lfo.frequency.value = 0.27;
    lfoGain.gain.value = 0.018;
    lfo.connect(lfoGain).connect(humGain.gain);
    for (const [hz, type] of [[50, 'sine'], [100, 'sawtooth'], [150, 'sine']]) {
      const o = ctx.createOscillator();
      o.type = type;
      o.frequency.value = hz;
      o.connect(humLp);
      nodes.push(o);
    }
    humLp.connect(humGain).connect(bed);
    nodes.push(lfo);
    // 电台底噪：一直在、很轻
    if (!noiseBuf) noise(t, 0.01, { gain: 0.0001 });
    const hiss = ctx.createBufferSource();
    hiss.buffer = noiseBuf;
    hiss.loop = true;
    const hissF = ctx.createBiquadFilter();
    hissF.type = 'bandpass';
    hissF.frequency.value = 1400;
    hissF.Q.value = 0.6;
    const hissG = ctx.createGain();
    hissG.gain.value = 0.012;
    hiss.connect(hissF).connect(hissG).connect(bed);
    nodes.push(hiss);
    nodes.forEach((n) => n.start(t));
    bed._nodes = nodes;
    // 静电噼啪：不规则的小爆点
    const crackle = () => {
      if (!bed) return;
      const at = ctx.currentTime + 0.01;
      const n = 1 + Math.floor(Math.random() * 3);
      for (let i = 0; i < n; i += 1) click(at + i * 0.012, 0.02 + Math.random() * 0.04, 2500 + Math.random() * 3000, bed);
      bedTimers.push(window.setTimeout(crackle, 120 + Math.random() * 520));
    };
    crackle();
    // 电报：远处有人在拍发早安电码
    const cable = () => {
      if (!bed) return;
      const text = CABLES[Math.floor(Math.random() * CABLES.length)];
      const unit = 0.055;
      let at = ctx.currentTime + 0.05;
      const hz = 640 + Math.random() * 60;
      for (const ch of text) {
        const code = MORSE[ch] ?? '';
        if (code === ' ') {
          at += unit * 4;
          continue;
        }
        for (const sym of code) {
          const len = sym === '-' ? unit * 3 : unit;
          tone(hz, at, len, { gain: 0.022, attack: 0.004, lp: 1400, out: bed });
          at += len + unit;
        }
        at += unit * 2;
      }
      bedTimers.push(window.setTimeout(cable, (at - ctx.currentTime) * 1000 + 1400 + Math.random() * 2600));
    };
    bedTimers.push(window.setTimeout(cable, 900));
    // 贝斯：很轻的八分音符律动（约 96 拍），D 和 C、降 B 之间慢慢走，压在底下不抢戏
    const ROOTS = [73.42, 73.42, 65.41, 58.27];
    let beat = 0;
    const pulse = () => {
      if (!bed) return;
      const at = ctx.currentTime + 0.02;
      const root = ROOTS[Math.floor(beat / 8) % ROOTS.length];
      bass(at, beat % 8 === 6 ? root * 1.5 : root, 0.26, beat % 2 ? 0.03 : 0.045);
      beat += 1;
      bedTimers.push(window.setTimeout(pulse, 312));
    };
    bedTimers.push(window.setTimeout(pulse, 2600));
  }
  function stopBed() {
    bedTimers.forEach((id) => window.clearTimeout(id));
    bedTimers = [];
    if (!bed) return;
    const g = bed;
    bed = null;
    try {
      const t = ctx.currentTime;
      g.gain.cancelScheduledValues(t);
      g.gain.setValueAtTime(Math.max(0.0001, g.gain.value), t);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.9);
      g._nodes.forEach((n) => n.stop(t + 1));
    } catch {
      /* 已经停了 */
    }
  }

  const SFX = {
    power(t) {
      // 系统通电：继电器连响 → 低频冲击 + 铜管轰鸣 → 两声低沉的确认
      for (let i = 0; i < 4; i += 1) click(t + i * 0.07, 0.1, 1200 + i * 250);
      thump(t + 0.3, { gain: 0.6 });
      braam(t + 0.32, 73.42, 2.8, 0.11);
      guitar(t + 0.32, D5, 2.6, { gain: 0.035, attack: 0.25 });
      tone(110, t + 1.5, 0.5, { type: 'triangle', gain: 0.07, lp: 700 });
      tone(82.41, t + 1.75, 0.8, { type: 'triangle', gain: 0.07, lp: 700 });
    },
    whoosh(t) {
      // 硬切：低通扫下的气流 + 底部一记闷击 + 贝斯滑音
      noise(t, 0.45, { gain: 0.18, from: 3200, to: 220, q: 0.7, type: 'lowpass', attack: 0.03 });
      thump(t + 0.02, { gain: 0.35, from: 90, to: 40, dur: 0.4 });
      bass(t, 146.83, 0.4, 0.05);
    },
    tick(t) {
      click(t, 0.04, 1300 + Math.random() * 700);
    },
    pop(t) {
      // 弹窗：机械落位 + 一声低沉的提示
      click(t, 0.09, 1100);
      thump(t, { gain: 0.22, from: 180, to: 70, dur: 0.22 });
      tone(220, t + 0.03, 0.35, { type: 'triangle', gain: 0.05, lp: 900 });
    },
    chime(t, i = 0) {
      // 席位接入：低沉的声呐脉冲（长尾交给空间回声）
      const hz = SEAT_HZ[i % SEAT_HZ.length];
      tone(hz, t, 1.6, { gain: 0.17, attack: 0.006 });
      tone(hz * 2, t, 0.5, { gain: 0.025, attack: 0.006, detune: 12 });
      thump(t, { gain: 0.2, from: 70, to: 45, dur: 0.28 });
    },
    link(t, i = 0) {
      // 数据链：低频锯齿短促上推
      tone(90 + i * 8, t, 0.14, { type: 'sawtooth', gain: 0.04, to: 180 + i * 8, lp: 600 });
    },
    confirm(t) {
      // 锁定确认：两声低音 + 冲击
      tone(196, t, 0.22, { type: 'triangle', gain: 0.08, lp: 900 });
      tone(146.83, t + 0.2, 0.6, { type: 'triangle', gain: 0.08, lp: 900 });
      thump(t + 0.2, { gain: 0.42, from: 100, to: 40, dur: 0.7 });
    },
    sunrise(t) {
      // 长线升起：低音铜管慢开 + 电吉他长音从远处推上来 + 噪声缓缓上扫
      braam(t, 73.42, 4.5, 0.08);
      guitar(t + 0.4, D5, 4, { gain: 0.04, attack: 2.2 });
      noise(t, 3.5, { gain: 0.03, from: 160, to: 1600, q: 0.4 });
    },
    chord(t) {
      // 落定：冲击 + D 小调铜管和弦
      thump(t, { gain: 0.6, from: 140, to: 35, dur: 1.2 });
      braam(t, 73.42, 3.2, 0.1);
      guitar(t, [146.83, 220, 293.66, 349.23], 3.2, { gain: 0.05, attack: 0.02 });
      bass(t, 73.42, 1.6, 0.12);
      tone(174.61, t + 0.05, 2.6, { type: 'sawtooth', gain: 0.03, lp: 700, attack: 0.3 });
    },
    // 以下是过场（花切、圆环）用的
    riffle(t, n = 16) {
      // 洗牌：一串越来越密的咔哒
      const count = Math.max(4, Math.min(40, Number(n) || 16));
      let at = t;
      for (let i = 0; i < count; i += 1) {
        click(at, 0.05 + Math.random() * 0.03, 1300 + Math.random() * 1400);
        at += 0.034 - (i / count) * 0.018;
      }
    },
    snap(t) {
      // 切牌落定：短促的脆响 + 一下电吉他闷音
      noise(t, 0.05, { gain: 0.16, from: 2200, to: 1100, q: 1.2, attack: 0.002 });
      thump(t, { gain: 0.2, from: 160, to: 60, dur: 0.14 });
      guitar(t, [73.42, 110], 0.16, { gain: 0.05, mute: true, attack: 0.003 });
    },
    flip(t) {
      noise(t, 0.16, { gain: 0.08, from: 500, to: 2600, q: 0.9, attack: 0.02 });
    },
    ring(t) {
      // 牌排成圆环：上升的低音锯齿
      tone(73.42, t, 1.6, { type: 'sawtooth', gain: 0.07, to: 146.83, lp: 220, lpTo: 1400, attack: 0.2 });
      tone(146.83, t + 0.6, 1.4, { gain: 0.04, attack: 0.3, detune: 7 });
    },
    lock(t) {
      // 圆环锁定、化成徽记：重冲击 + 短铜管 + 电吉他与贝斯同拍落下
      thump(t, { gain: 0.65, from: 150, to: 32, dur: 1.4 });
      braam(t, 73.42, 1.6, 0.1);
      guitar(t, D5, 1.8, { gain: 0.05, attack: 0.008 });
      bass(t, 73.42, 1.2, 0.12);
      click(t, 0.1, 1200);
    },
    halo(t) {
      // 播报下面垫一层很轻的气声和长音，让女声更空
      noise(t, 2.6, { gain: 0.018, from: 2400, to: 3600, q: 0.5, attack: 0.6 });
      tone(587.33, t, 2.6, { gain: 0.012, attack: 0.7, detune: -5 });
      tone(880, t + 0.1, 2.4, { gain: 0.008, attack: 0.8, detune: 6 });
    },
  };
  function sfx(name, arg) {
    if (read('sound', 'on') === 'off' || !SFX[name] || !audio() || ctx.state !== 'running') return;
    try {
      SFX[name](ctx.currentTime + 0.01, arg);
    } catch {
      /* 音效只是锦上添花 */
    }
  }

  /* ── 播报：挑系统里清脆的女声 ── */
  // 按顺序优先：Edge / Windows 的自然女声 → 浏览器在线女声 → 系统自带女声
  const FEMALE = {
    zh: [/Xiaoxiao/i, /Xiaoyi/i, /晓晓|晓伊/, /普通话|Mandarin/i, /Huihui|Yaoyao|Tingting|Meijia|婷婷|美嘉/i, /female|女/i],
    en: [/Aria/i, /Jenny/i, /Libby|Sonia|Ava|Emma/i, /UK English Female/i, /US English/i, /Zira|Samantha|Karen|Serena/i, /female/i],
  };
  const speech = window.speechSynthesis ?? null;
  function voices() {
    try {
      return speech ? speech.getVoices() : [];
    } catch {
      return [];
    }
  }
  function pickVoice(lang = read('lang', 'zh')) {
    const all = voices();
    const want = read('voiceName', '');
    const named = want && all.find((v) => v.name === want);
    if (named) return named;
    const mine = all.filter((v) => (v.lang || '').toLowerCase().startsWith(lang === 'en' ? 'en' : 'zh'));
    for (const re of FEMALE[lang === 'en' ? 'en' : 'zh']) {
      const hit = mine.find((v) => re.test(v.name));
      if (hit) return hit;
    }
    return mine[0] || null;
  }
  function say(line) {
    if (read('voice', 'on') === 'off' || !speech || typeof window.SpeechSynthesisUtterance !== 'function') return;
    const lang = read('lang', 'zh') === 'en' ? 'en' : 'zh';
    const text = (line && line[lang]) || '';
    if (!text) return;
    try {
      speech.cancel();
      const u = new window.SpeechSynthesisUtterance(text);
      const v = pickVoice(lang);
      if (v) u.voice = v;
      u.lang = v?.lang || (lang === 'en' ? 'en-GB' : 'zh-CN');
      // 慢一点、高一点、轻一点，听起来更空灵；下面再垫一层气声（halo）
      u.rate = Number(read('rate', '0.9')) || 0.9;
      u.pitch = 1.22;
      u.volume = 0.85;
      speech.speak(u);
      sfx('halo');
    } catch {
      /* 朗读只是锦上添花 */
    }
  }
  try {
    speech?.getVoices(); // 有的浏览器要先问一次才开始加载声音列表
  } catch {
    /* 没有朗读也不要紧 */
  }

  let live = null;

  function permitUrl(handoff = false) {
    const q = new URLSearchParams();
    if (handoff) q.set('handoff', '1');
    if (read('auto', 'on') === 'off') q.set('auto', '0');
    if (read('voice', 'on') === 'off') q.set('audio', '0');
    const s = q.toString();
    return 'boot/gmu.html' + (s ? '?' + s : '');
  }

  const wantsSound = () => read('sound', 'on') !== 'off' || read('voice', 'on') !== 'off';

  function play(mode = read('mode', 'full')) {
    if (!MODES.includes(mode) || mode === 'off') return null;
    if (live) live.finish();
    const host = document.createElement('div');
    host.className = 'rt-boot';
    host.setAttribute('role', 'dialog');
    host.setAttribute('aria-label', '开机动画（Esc 跳过）');
    host.style.cssText =
      'position:fixed;inset:0;z-index:2147483000;background:#030814;opacity:1;transition:opacity ' + FADE_MS + 'ms ease';
    const frame = document.createElement('iframe');
    frame.title = '开机动画';
    frame.setAttribute('allow', 'autoplay');
    frame.style.cssText = 'width:100%;height:100%;border:0;display:block;background:#030814';
    const skip = document.createElement('button');
    skip.type = 'button';
    skip.className = 'rt-boot-skip';
    skip.textContent = '跳过 ✕';
    skip.style.cssText =
      'position:absolute;right:18px;bottom:16px;z-index:2;font:12px/1.4 ui-monospace,Consolas,monospace;letter-spacing:.12em;' +
      'color:rgba(214,232,255,.75);background:rgba(3,8,20,.55);border:1px solid rgba(120,180,255,.3);border-radius:4px;padding:5px 10px;cursor:pointer';
    host.append(frame, skip);

    let stage = mode === 'permit' ? 'permit' : 'intro';
    let done = false;
    const go = (which, handoff = false) => {
      stage = which;
      startBed();
      frame.src = which === 'intro' ? 'boot/dawn.html' : permitUrl(handoff);
    };
    // 片头最后一帧是居中的徽记；换页那一下 iframe 会空白一瞬间，
    // 所以先在外层盖一枚同位置、同尺寸的徽记和同色背景，授权页画出来后再撤掉。
    const BRIDGE_BG = 'radial-gradient(120% 90% at 50% 48%,#0b1730 0%,#040914 64%)';
    const handoff = () => {
      host.style.background = BRIDGE_BG;
      const seal = document.createElement('img');
      seal.className = 'rt-boot-bridge';
      seal.alt = '';
      seal.src = 'boot/assets/gmu-seal.svg';
      seal.style.cssText =
        'position:absolute;left:50%;top:50%;width:min(52vh,480px);height:min(52vh,480px);transform:translate(-50%,-50%);' +
        'z-index:1;pointer-events:none;transition:opacity 120ms linear;background:transparent';
      const cover = document.createElement('div');
      cover.style.cssText = 'position:absolute;inset:0;z-index:0;pointer-events:none;background:' + BRIDGE_BG;
      host.append(cover, seal);
      frame.style.visibility = 'hidden';
      const reveal = () => {
        frame.removeEventListener('load', reveal);
        // 等授权页真正画出第一帧
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            frame.style.visibility = '';
            window.setTimeout(() => {
              seal.style.opacity = '0';
              cover.remove();
              window.setTimeout(() => seal.remove(), 160);
            }, 40);
          }),
        );
      };
      frame.addEventListener('load', reveal);
      go('permit', true);
    };
    const finish = () => {
      if (done) return;
      done = true;
      window.removeEventListener('message', onMessage);
      document.removeEventListener('keydown', onKey, true);
      window.clearTimeout(guard);
      try {
        speech?.cancel();
      } catch {
        /* 没在念 */
      }
      stopBed();
      host.style.opacity = '0';
      host.style.pointerEvents = 'none';
      window.setTimeout(() => host.remove(), FADE_MS + 50);
      if (live === record) live = null;
      document.dispatchEvent(new CustomEvent('roundtable:boot-done'));
    };
    const next = () => (stage === 'intro' && mode === 'full' ? handoff() : finish());
    const onMessage = (e) => {
      if (e.source !== frame.contentWindow) return;
      const d = e.data;
      if (d === 'boot:done' && stage === 'intro') next();
      else if (d === 'gmu:done' && stage === 'permit') finish();
      else if (d && typeof d === 'object' && d.type === 'boot:sfx') sfx(String(d.name), Number(d.arg) || 0);
      else if (d && typeof d === 'object' && d.type === 'boot:say') say({ zh: String(d.zh || ''), en: String(d.en || '') });
    };
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        finish();
      }
    };
    skip.addEventListener('click', finish);
    // 按键要送进动画页（Enter / 空格推进），所以每次载入后把焦点交给它
    frame.addEventListener('load', () => {
      try {
        frame.contentWindow.focus();
      } catch {
        /* 拿不到焦点也不要紧：外层的 Esc 和「跳过」照样能用 */
      }
    });
    window.addEventListener('message', onMessage);
    document.addEventListener('keydown', onKey, true);
    const guard = window.setTimeout(finish, MAX_MS);
    const record = { finish, host };
    live = record;

    // 浏览器还不让出声（这一页没被点过）时先放一道「按下开机」，点一下再开始，音效和播报才不会被吞掉
    const ac = wantsSound() ? audio() : null;
    if (ac && ac.state !== 'running') {
      const gate = document.createElement('button');
      gate.type = 'button';
      gate.className = 'rt-boot-gate';
      gate.innerHTML =
        '<span style="display:block;width:64px;height:64px;margin:0 auto 18px;border-radius:50%;border:1.5px solid #5ee0ff;' +
        'box-shadow:0 0 24px rgba(94,224,255,.45),inset 0 0 18px rgba(94,224,255,.25);position:relative">' +
        '<i style="position:absolute;left:50%;top:14px;width:2px;height:20px;margin-left:-1px;background:#5ee0ff;border-radius:1px"></i>' +
        '<i style="position:absolute;left:17px;top:19px;width:30px;height:30px;border:2px solid #5ee0ff;border-top-color:transparent;border-radius:50%"></i></span>' +
        '<b style="display:block;font:600 18px/1.4 system-ui,sans-serif;letter-spacing:.5em;color:#eaf4ff">按下开机</b>' +
        '<small style="display:block;margin-top:8px;font:11px/1.4 ui-monospace,Consolas,monospace;letter-spacing:.3em;color:#6f8db3">PRESS TO POWER ON · ENTER</small>';
      gate.style.cssText =
        'position:absolute;inset:0;z-index:1;width:100%;border:0;cursor:pointer;color:inherit;' +
        'background:radial-gradient(90% 70% at 50% 60%,#0b2350 0%,#061228 45%,#030814 85%)';
      const start = (e) => {
        e?.preventDefault?.();
        document.removeEventListener('keydown', onGateKey, true);
        ac.resume().then(() => !done && startBed()).catch(() => {});
        gate.remove();
        go(stage);
      };
      const onGateKey = (e) => {
        if (e.key === 'Enter' || e.key === ' ') start(e);
      };
      gate.addEventListener('click', start);
      document.addEventListener('keydown', onGateKey, true);
      host.append(gate);
      window.setTimeout(() => {
        try {
          gate.focus();
        } catch {
          /* 没有焦点也能点 */
        }
      }, 0);
    } else {
      go(stage);
    }
    (document.body || document.documentElement).appendChild(host);
    return record;
  }

  window.RoundtableBoot = { play, read, PREFIX, MODES, say, sfx, voices, pickVoice };

  if (new URLSearchParams(window.location.search).get('boot') === '0') return;
  // 浏览器禁用了本机存储时不放：设置存不下、也关不掉，每次都盖一遍太烦
  try {
    window.localStorage.getItem(PREFIX + 'mode');
  } catch {
    return;
  }
  if (read('mode', 'full') === 'off') return;
  if (read('when', 'session') === 'session') {
    try {
      if (window.sessionStorage.getItem(PREFIX + 'played') === '1') return;
      window.sessionStorage.setItem(PREFIX + 'played', '1');
    } catch {
      /* 标签级存储不可用时每次都放 */
    }
  }
  play();
})();
