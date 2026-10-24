// 开机动画：打开圆桌时先放 550C 片头，再接 GMU（早上好联合）访问授权，然后淡入首页。
// 普通脚本（不是模块），放在 <body> 最前面同步执行：首页还没画出来就先盖住，不会闪一下。
// 设置都存在本机 localStorage（roundtable.boot.*），在「片头」设置里改；网址加 ?boot=0 不放。
// 动画页来自 dsh-550c-boot（MIT），见 web/boot/CREDITS.md。
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

  let live = null;

  function permitUrl() {
    const q = new URLSearchParams();
    if (read('auto', 'on') === 'off') q.set('auto', '0');
    if (read('voice', 'on') === 'off') q.set('audio', '0');
    const s = q.toString();
    return 'boot/gmu.html' + (s ? '?' + s : '');
  }

  function play(mode = read('mode', 'full')) {
    if (!MODES.includes(mode) || mode === 'off') return null;
    if (live) live.finish();
    const host = document.createElement('div');
    host.className = 'rt-boot';
    host.setAttribute('role', 'dialog');
    host.setAttribute('aria-label', '开机动画（Esc 跳过）');
    host.style.cssText =
      'position:fixed;inset:0;z-index:2147483000;background:#050403;opacity:1;transition:opacity ' + FADE_MS + 'ms ease';
    const frame = document.createElement('iframe');
    frame.title = '开机动画';
    frame.style.cssText = 'width:100%;height:100%;border:0;display:block;background:#050403';
    const skip = document.createElement('button');
    skip.type = 'button';
    skip.className = 'rt-boot-skip';
    skip.textContent = '跳过 ✕';
    skip.style.cssText =
      'position:absolute;right:18px;bottom:16px;z-index:1;font:12px/1.4 ui-monospace,Consolas,monospace;letter-spacing:.12em;' +
      'color:rgba(238,243,248,.72);background:rgba(5,4,3,.55);border:1px solid rgba(238,243,248,.25);border-radius:4px;padding:5px 10px;cursor:pointer';
    host.append(frame, skip);

    let stage = mode === 'permit' ? 'permit' : 'intro';
    let done = false;
    const go = (which) => {
      stage = which;
      frame.src = which === 'intro' ? 'boot/550c.html' : permitUrl();
    };
    const finish = () => {
      if (done) return;
      done = true;
      window.removeEventListener('message', onMessage);
      document.removeEventListener('keydown', onKey, true);
      window.clearTimeout(guard);
      host.style.opacity = '0';
      host.style.pointerEvents = 'none';
      window.setTimeout(() => host.remove(), FADE_MS + 50);
      if (live === record) live = null;
      document.dispatchEvent(new CustomEvent('roundtable:boot-done'));
    };
    const next = () => (stage === 'intro' && mode === 'full' ? go('permit') : finish());
    const onMessage = (e) => {
      if (e.source !== frame.contentWindow) return;
      if (e.data === 'boot:done' && stage === 'intro') next();
      else if (e.data === 'gmu:done' && stage === 'permit') finish();
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
    go(stage);
    (document.body || document.documentElement).appendChild(host);
    return record;
  }

  window.RoundtableBoot = { play, read, PREFIX, MODES };

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
