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
     军事科幻、严肃：低频冲击、锯齿铜管般的轰鸣、继电器咔哒、雷达与声呐脉冲、电台底噪。
     总线：各声部 → 压缩器（压住峰值、让低频更"实"）→ 总音量。 */
  let ctx = null;
  let master = null;
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
      master = ctx.createGain();
      master.gain.value = 0.42;
      master.connect(comp).connect(ctx.destination);
    } catch {
      ctx = null;
    }
    return ctx;
  }
  // D 小调：七个席位的声呐音，低沉不甜
  const SEAT_HZ = [293.66, 349.23, 392, 440, 523.25, 587.33, 698.46];
  function env(g, at, dur, peak, attack) {
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(peak, at + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  }
  /** 单个振荡器，可选低通滤波（扫频）。 */
  function tone(hz, at, dur, { type = 'sine', gain = 0.3, attack = 0.01, to = null, lp = 0, lpTo = 0, detune = 0 } = {}) {
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
    node.connect(g).connect(master);
    o.start(at);
    o.stop(at + dur + 0.05);
  }
  let noiseBuf = null;
  function noise(at, dur, { gain = 0.12, from = 400, to = 4000, q = 0.8, type = 'bandpass', attack = null } = {}) {
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
    src.connect(f).connect(g).connect(master);
    src.start(at, Math.random());
    src.stop(at + dur + 0.05);
  }
  /** 低频冲击：正弦从高处急坠，像远处的重物落地。 */
  const thump = (at, { gain = 0.5, from = 120, to = 38, dur = 0.7 } = {}) =>
    tone(from, at, dur, { gain, attack: 0.004, to });
  /** 继电器 / 机械咔哒：极短的高通噪声。 */
  const click = (at, gain = 0.09, hz = 3200) => noise(at, 0.018, { gain, from: hz, to: hz * 1.4, q: 3, attack: 0.001 });
  /** 锯齿铜管轰鸣：几层失谐锯齿 + 低通慢开。 */
  function braam(at, root, dur, gain = 0.12) {
    for (const [mul, det] of [[1, -9], [1, 9], [2, 0], [1.5, 4]]) {
      tone(root * mul, at, dur, { type: 'sawtooth', gain: gain / (mul > 1 ? 2.2 : 1), attack: 0.08, lp: 180, lpTo: 1400, detune: det });
    }
  }
  const SFX = {
    power(t) {
      // 系统通电：继电器连响 → 低频冲击 + 铜管轰鸣 → 电台底噪
      for (let i = 0; i < 4; i += 1) click(t + i * 0.06, 0.12, 2200 + i * 500);
      thump(t + 0.28, { gain: 0.6 });
      braam(t + 0.3, 73.42, 2.6, 0.11);
      noise(t + 0.3, 2.2, { gain: 0.03, from: 1800, to: 900, q: 0.5 });
      tone(1760, t + 1.4, 0.09, { type: 'square', gain: 0.03, lp: 3000 });
      tone(1760, t + 1.55, 0.09, { type: 'square', gain: 0.03, lp: 3000 });
    },
    whoosh(t) {
      // 硬切：低通扫下的气流 + 底部一记闷击
      noise(t, 0.45, { gain: 0.2, from: 5000, to: 300, q: 0.7, type: 'lowpass', attack: 0.03 });
      thump(t + 0.02, { gain: 0.35, from: 90, to: 40, dur: 0.4 });
    },
    tick(t) {
      click(t, 0.05, 2600 + Math.random() * 1400);
    },
    pop(t) {
      // 浮窗：雷达双短音 + 咔哒
      click(t, 0.08);
      tone(1318.5, t, 0.06, { type: 'square', gain: 0.035, lp: 2600 });
      tone(987.77, t + 0.075, 0.08, { type: 'square', gain: 0.035, lp: 2600 });
    },
    chime(t, i = 0) {
      // 席位接入：声呐脉冲（正弦 + 轻微失谐的长尾）+ 低频点
      const hz = SEAT_HZ[i % SEAT_HZ.length];
      tone(hz, t, 1.4, { gain: 0.16, attack: 0.004 });
      tone(hz, t, 1.1, { gain: 0.05, attack: 0.004, detune: 14 });
      thump(t, { gain: 0.18, from: 70, to: 45, dur: 0.25 });
    },
    link(t, i = 0) {
      // 数据链：方波快速上扫
      tone(600 + i * 70, t, 0.07, { type: 'square', gain: 0.025, to: 2400 + i * 70, lp: 3500 });
    },
    confirm(t) {
      // 锁定确认：两声电子音 + 冲击
      tone(1046.5, t, 0.09, { type: 'square', gain: 0.04, lp: 3000 });
      tone(1046.5, t + 0.13, 0.22, { type: 'square', gain: 0.04, lp: 3000 });
      thump(t + 0.13, { gain: 0.4, from: 100, to: 40, dur: 0.6 });
    },
    sunrise(t) {
      // 长线升起：低音铜管慢开 + 噪声上扫
      braam(t, 73.42, 4.5, 0.08);
      noise(t, 3.5, { gain: 0.04, from: 200, to: 3000, q: 0.4 });
    },
    chord(t) {
      // 落定：冲击 + D 小调铜管和弦（根 + 五度 + 小三度高八度）
      thump(t, { gain: 0.6, from: 140, to: 35, dur: 1.2 });
      braam(t, 73.42, 3.2, 0.1);
      tone(349.23, t + 0.05, 2.6, { type: 'sawtooth', gain: 0.025, lp: 900, attack: 0.3 });
    },
    // 以下是给过场（花切、发牌、圆环）用的
    riffle(t, n = 16) {
      // 洗牌：一串越来越密的咔哒
      const count = Math.max(4, Math.min(40, Number(n) || 16));
      let at = t;
      for (let i = 0; i < count; i += 1) {
        click(at, 0.06 + Math.random() * 0.03, 2400 + Math.random() * 2400);
        at += 0.034 - (i / count) * 0.018;
      }
    },
    snap(t) {
      // 花切的一记脆响
      noise(t, 0.05, { gain: 0.22, from: 4500, to: 2500, q: 1.2, attack: 0.002 });
      thump(t, { gain: 0.15, from: 160, to: 70, dur: 0.12 });
    },
    flip(t) {
      // 翻牌 / 甩牌：短促的气流
      noise(t, 0.16, { gain: 0.1, from: 1200, to: 6000, q: 0.9, attack: 0.02 });
    },
    ring(t) {
      // 牌排成圆环：上升的合成音 + 金属泛音
      tone(146.83, t, 1.6, { type: 'sawtooth', gain: 0.06, to: 293.66, lp: 300, lpTo: 2400, attack: 0.2 });
      tone(1174.66, t + 0.9, 1.2, { gain: 0.03, attack: 0.05, detune: 6 });
    },
    lock(t) {
      // 圆环锁定、化成徽记：重冲击 + 短铜管
      thump(t, { gain: 0.65, from: 150, to: 32, dur: 1.4 });
      braam(t, 73.42, 1.6, 0.1);
      click(t, 0.12, 2000);
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
      u.rate = Number(read('rate', '1.02')) || 1.02;
      u.pitch = 1.12; // 稍高一点更清脆
      u.volume = 1;
      speech.speak(u);
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

  function permitUrl() {
    const q = new URLSearchParams();
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
    const go = (which) => {
      stage = which;
      frame.src = which === 'intro' ? 'boot/dawn.html' : permitUrl();
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
      host.style.opacity = '0';
      host.style.pointerEvents = 'none';
      window.setTimeout(() => host.remove(), FADE_MS + 50);
      if (live === record) live = null;
      document.dispatchEvent(new CustomEvent('roundtable:boot-done'));
    };
    const next = () => (stage === 'intro' && mode === 'full' ? go('permit') : finish());
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
        ac.resume().catch(() => {});
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
