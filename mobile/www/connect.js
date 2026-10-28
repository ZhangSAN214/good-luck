// 圆桌安卓外壳的连接页：记住电脑的地址，打开 App 时自动连过去（数据只在电脑上，这里只存地址）。
// 地址可以填 192.168.1.23 / 192.168.1.23:8000 / http://…，也可以粘贴电脑「平板」面板里二维码的配对链接。
// 每次打开 App 依次试「家里」「离家（Tailscale）」两个地址，哪个通就进哪个。

export const DEFAULT_PORT = 8000;
const KEY = 'roundtable.remote.v1';
const PROBE_MS = 2500;

// 把用户填的内容变成 { base: 'http://host:port', open: 要打开的完整地址 }；不合法返回 null
export function normalize(input) {
  let text = String(input ?? '').trim();
  if (!text) return null;
  if (!/^[a-z]+:\/\//i.test(text)) text = `http://${text}`;
  let url;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
  if (!url.hostname || url.username || url.password) return null;
  const port = url.port || (url.protocol === 'https:' ? '' : String(DEFAULT_PORT));
  const base = `${url.protocol}//${url.hostname}${port ? `:${port}` : ''}`;
  // 配对链接（一次性，10 分钟有效）：保存地址，直接打开链接完成登录
  const pair = url.pathname === '/lan/pair' && url.searchParams.get('c');
  return { base, open: pair ? `${base}/lan/pair?c=${encodeURIComponent(pair)}` : `${base}/` };
}

export function load(storage = globalThis.localStorage) {
  try {
    const data = JSON.parse(storage.getItem(KEY) || '{}');
    return { home: String(data.home || ''), away: String(data.away || '') };
  } catch {
    return { home: '', away: '' };
  }
}

export function save(conf, storage = globalThis.localStorage) {
  try {
    storage.setItem(KEY, JSON.stringify({ home: conf.home || '', away: conf.away || '' }));
  } catch {
    /* 存不了也能用，只是下次要重填 */
  }
}

// 电脑上的圆桌通不通：跨域请求拿不到内容，但连不上会抛错；manifest 不用登录就能取
export async function reachable(base, fetchImpl = globalThis.fetch, ms = PROBE_MS) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  try {
    await fetchImpl(`${base}/manifest.webmanifest`, { mode: 'no-cors', cache: 'no-store', signal: ctrl.signal });
    return true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

// 依次试几个地址，返回第一个通的 { base, open }，都不通返回 null
export async function pick(targets, probe = reachable) {
  for (const t of targets) {
    if (t && (await probe(t.base))) return t;
  }
  return null;
}

// ---------- 页面 ----------

function ui(doc = document, nav = (u) => location.assign(u)) {
  const $ = (id) => doc.getElementById(id);
  const home = $('home');
  const away = $('away');
  const msg = $('msg');
  const go = $('go');
  const conf = load();
  home.value = conf.home;
  away.value = conf.away;

  const say = (text, bad = false) => {
    msg.textContent = text;
    msg.classList.toggle('bad', bad);
  };

  async function connect() {
    const a = normalize(home.value);
    const b = normalize(away.value);
    if (home.value.trim() && !a) return say('「家里」地址看不懂，例如 192.168.1.23:8000', true);
    if (away.value.trim() && !b) return say('「离家」地址看不懂，例如 100.64.1.2:8000', true);
    if (!a && !b) return say('先填电脑的地址（在电脑上圆桌顶部「平板」里能看到）', true);
    // 配对链接只用一次：存下地址部分
    save({ home: a ? a.base : '', away: b ? b.base : '' });
    go.disabled = true;
    say('正在连接电脑上的圆桌…');
    const hit = await pick([a, b]);
    go.disabled = false;
    if (!hit) {
      return say(
        '连不上。请确认：电脑开着、用「圆桌（平板可连）」或 start_lan.bat 启动；手机和电脑在同一个 Wi-Fi（离家要用 Tailscale）。',
        true,
      );
    }
    sessionStorage.setItem('roundtable.remote.left', '1');
    nav(hit.open);
  }

  doc.getElementById('f').addEventListener('submit', (e) => {
    e.preventDefault();
    connect();
  });

  // 打开 App 时自动连；从圆桌按返回键回到这页时不自动连（让你能改地址）
  const returning = sessionStorage.getItem('roundtable.remote.left') === '1';
  doc.defaultView?.addEventListener('pageshow', (e) => {
    if (e.persisted) say('想换地址就改一下再点「连接」。');
  });
  if ((conf.home || conf.away) && !returning) connect();
  else if (returning) say('想换地址就改一下再点「连接」。');
}

if (typeof document !== 'undefined' && document.getElementById('f')) ui();
