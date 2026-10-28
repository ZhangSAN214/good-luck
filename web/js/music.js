// 终端背景音乐：顶栏一颗小胶囊，点开是一张竖版播放器卡片（画框封面、进度条、大按钮）。
// 两种来源：
// 1. 本地音乐：自己从电脑里挑音频文件（mp3 / ogg / wav / flac / m4a）加进播放列表，可以删除、排序、调音量、
//    列表循环 / 单曲循环 / 随机。文件存在这台电脑的浏览器本地库（IndexedDB）里，不经服务器、不上传、不联网下载。
//    默认不自动播放（浏览器也不允许）；切到其他页面时淡出暂停、回到终端再接着放（可以改成一直放）；
//    网页里别的声音（文件预览里的音频、视频）开始放时也先淡出让开，放完再接着放。
// 2. QQ 音乐（电脑上的播放器）：圆桌服务读 Windows 系统媒体控制里正在放的歌（歌名、歌手、封面、进度），
//    按钮通过同一个系统接口发给播放器（/api/nowplaying）。不登录、不抓音频，声音还是 QQ 音乐自己放。
// 卡片的底色、主色、文字颜色、顶部小字和封面都可以自己改，设置只存本机浏览器。
// 封面：歌曲自带的（mp3 内嵌封面 / QQ 音乐的专辑图）、本机 data/avatars/music_covers/ 里的成员侧脸、
// 自己上传的一张图，或者终端大立绘；都没有时是一块素净的画框。
// 封面会动：头发那一侧轻轻摆（music.css 的 .mu-hair，脸不动），上面飘一层雨、雪、落叶、花瓣、星屑或气泡（coverfx.js）；
// 成员封面配哪种写在封面目录的 effects.json，外观里可以换或关掉，系统开了「减少动态」时不动。

import { api } from './api.js';
import { CoverFx, FX, isFx } from './coverfx.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const DB_NAME = 'roundtable-music';
const STORE = 'tracks';
const KEY = 'roundtable.music'; // 播放顺序、音量、循环方式、切页是否继续、上次放到哪首、来源、外观、封面
const MY_COVER = '__cover'; // 自己上传的封面也放在曲库里（不算一首歌）
const FADE_MS = 600;
const ACCEPT = 'audio/*,.mp3,.ogg,.oga,.wav,.flac,.m4a,.aac,.opus,.webm';
const MAX_ART_BYTES = 8 * 1024 * 1024;
export const FX_MODES = [['auto', '跟着封面'], ['off', '不要动'], ...FX];
// 成员封面地址带 ?v=版本（换了图就变），记住的选择只比路径，换图后照样选中、不读旧缓存
const coverPath = (u) => String(u || '').split('?')[0];
const cleanFx = (v) => (v === 'auto' || v === 'off' || isFx(v) ? v : 'auto');

export const MODES = [
  ['all', '列表循环'],
  ['one', '单曲循环'],
  ['shuffle', '随机'],
];
export const SOURCES = [
  ['local', '本地音乐'],
  ['system', 'QQ 音乐'],
];
export const COVER_MODES = [
  ['song', '歌曲自带优先'],
  ['fixed', '固定用我选的'],
  ['rotate', '每首轮换成员'],
];
// 外观预设：底色、主色（画框描边、进度条、按钮）、文字色、底色不透明度。
// 「墨蓝」是默认：底色和通透没改时直接用首页那层液态玻璃（ark.css 的 --hb-glass），跟时钟、右边的卡片同一种材质。
export const SKINS = {
  ink: { label: '墨蓝', bg: '#182c4e', accent: '#b4c6e6', text: '#eceff3', alpha: 0.82, glass: true },
  mist: { label: '雾青', bg: '#eef2f2', accent: '#5e8e91', text: '#1f3236', alpha: 0.86 },
  night: { label: '夜蓝', bg: '#121620', accent: '#8eb0e4', text: '#eceff3', alpha: 0.8 },
  sakura: { label: '樱灰', bg: '#f3eef0', accent: '#a9788a', text: '#3a2a30', alpha: 0.86 },
  amber: { label: '琥珀', bg: '#f4efe6', accent: '#b0823f', text: '#3b2e1c', alpha: 0.88 },
};
const HEX = /^#[0-9a-f]{6}$/i;

/** 下一首的位置：列表循环和单曲循环都按顺序往后（单曲循环只在一首放完时重放，由调用方处理）；随机挑一首不是当前的。 */
export function nextIndex(mode, cur, count, rand = Math.random) {
  if (!count) return -1;
  if (mode === 'shuffle' && count > 1) {
    const k = Math.floor(rand() * (count - 1));
    return k >= cur ? k + 1 : k;
  }
  return cur < 0 ? 0 : (cur + 1) % count;
}

/** 「3:07」这样的时长；读不出来时给「--:--」。 */
export function clock(sec) {
  if (!Number.isFinite(sec) || sec < 0) return '--:--';
  const s = Math.floor(sec);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** 文件名去掉扩展名当歌名。 */
export function trackName(fileName) {
  return String(fileName || '未命名').replace(/\.[^.]{1,5}$/, '') || '未命名';
}

/** 外观设置清理：预设名或自定义颜色（#rrggbb），不认识的回到默认「墨蓝」。 */
export function cleanSkin(v) {
  const base = SKINS[v?.preset] ? v.preset : 'ink';
  const p = SKINS[base];
  const pick = (k) => (typeof v?.[k] === 'string' && HEX.test(v[k]) ? v[k].toLowerCase() : p[k]);
  const alpha = Number(v?.alpha);
  return {
    preset: base,
    bg: pick('bg'),
    accent: pick('accent'),
    text: pick('text'),
    alpha: Number.isFinite(alpha) ? Math.min(1, Math.max(0.35, alpha)) : p.alpha,
    tag: typeof v?.tag === 'string' ? v.tag.slice(0, 40) : '',
  };
}

/** 用首页的玻璃材质吗：玻璃预设，且底色和通透都没改过。 */
export function isGlass(s) {
  const p = SKINS[s?.preset];
  return !!p?.glass && s.bg === p.bg && s.alpha === p.alpha;
}

/** 旧版存下的外观（没有 skinV）：没自己调过颜色的换成新默认「墨蓝」，调过的原样保留。 */
export function upgradeSkin(v, ver) {
  const s = cleanSkin(v);
  if (ver >= 2) return s;
  const p = SKINS[s.preset];
  return s.bg === p.bg && s.accent === p.accent && s.text === p.text ? { ...cleanSkin(null), tag: s.tag } : s;
}

/** #rrggbb + 不透明度 → rgba()。 */
export function rgba(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

/** 主色上的图标用白还是深色（看主色亮度）。 */
export function onColor(hex) {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
    const x = c / 255;
    return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.42 ? '#14181f' : '#ffffff';
}

const be32 = (b, p) => ((b[p] << 24) | (b[p + 1] << 16) | (b[p + 2] << 8) | b[p + 3]) >>> 0;
const syncsafe = (b, p) => ((b[p] & 0x7f) << 21) | ((b[p + 1] & 0x7f) << 14) | ((b[p + 2] & 0x7f) << 7) | (b[p + 3] & 0x7f);

/** 图片的真实类型（看文件头，不信标签里写的）；不是常见图片返回 null。 */
export function imageType(b) {
  if (b.length > 8 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47) return 'image/png';
  if (b.length > 3 && b[0] === 0xff && b[1] === 0xd8) return 'image/jpeg';
  if (b.length > 12 && String.fromCharCode(...b.slice(0, 4)) === 'RIFF' && String.fromCharCode(...b.slice(8, 12)) === 'WEBP') return 'image/webp';
  return null;
}

/** mp3 里 ID3v2.3 / 2.4 标签内嵌的封面（APIC）：返回 { type, data }，没有就 null。只读本机文件，不联网。 */
export function id3Picture(buf) {
  const b = new Uint8Array(buf);
  if (b.length < 10 || b[0] !== 0x49 || b[1] !== 0x44 || b[2] !== 0x33) return null;
  const ver = b[3];
  const flags = b[5];
  if (ver < 3 || ver > 4 || (flags & 0x80 && ver === 3)) return null; // 2.2 和整体反同步的旧标签不管
  const end = Math.min(b.length, 10 + syncsafe(b, 6));
  let p = 10;
  if (flags & 0x40) p += ver === 4 ? syncsafe(b, p) : be32(b, p) + 4;
  while (p + 10 <= end) {
    const id = String.fromCharCode(b[p], b[p + 1], b[p + 2], b[p + 3]);
    if (!/^[A-Z0-9]{4}$/.test(id)) break;
    const len = ver === 4 ? syncsafe(b, p + 4) : be32(b, p + 4);
    const ff = b[p + 9];
    let s = p + 10;
    const e = s + len;
    if (!len || e > end) break;
    p = e;
    if (id !== 'APIC') continue;
    if (ver === 4) {
      if (ff & 0x0e) continue; // 压缩、加密、反同步：跳过
      if (ff & 0x01) s += 4;
    } else if (ff & 0xc0) continue;
    const enc = b[s++];
    while (s < e && b[s] !== 0) s++; // MIME
    s += 2; // 结束的 0 和图片类别
    if (enc === 1 || enc === 2) {
      while (s + 1 < e && !(b[s] === 0 && b[s + 1] === 0)) s += 2;
      s += 2;
    } else {
      while (s < e && b[s] !== 0) s++;
      s += 1;
    }
    const data = b.slice(s, e);
    const type = imageType(data);
    if (type) return { type, data };
  }
  return null;
}

// 极细线性图标（和终端首页同一套，线宽在 CSS 里统一）；大按钮用实心
const ICON = {
  note: '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
  play: '<path class="f" d="M8 5.2v13.6L19 12z"/>',
  pause: '<rect class="f" x="7" y="5" width="3.6" height="14" rx="1"/><rect class="f" x="13.4" y="5" width="3.6" height="14" rx="1"/>',
  prev: '<rect class="f" x="5" y="5.5" width="2.6" height="13" rx=".8"/><path class="f" d="M19 5.5v13L8.6 12z"/>',
  next: '<rect class="f" x="16.4" y="5.5" width="2.6" height="13" rx=".8"/><path class="f" d="M5 5.5v13L15.4 12z"/>',
  all: '<path d="M4 11V9a3 3 0 0 1 3-3h12l-3-3M20 13v2a3 3 0 0 1-3 3H5l3 3"/>',
  one: '<path d="M4 11V9a3 3 0 0 1 3-3h12l-3-3M20 13v2a3 3 0 0 1-3 3H5l3 3M12 10v5"/>',
  shuffle: '<path d="M4 7h3c5 0 5 10 10 10h3M4 17h3c2 0 3-1.5 4-3.5M13 9.5C14 8 15 7 17 7h3M18 4l3 3-3 3M18 14l3 3-3 3"/>',
  vol: '<path d="M4 9.5h4l5-4v13l-5-4H4zM16.5 9a4 4 0 0 1 0 6"/>',
  mute: '<path d="M4 9.5h4l5-4v13l-5-4H4zM17 10l4 4M21 10l-4 4"/>',
  up: '<path d="m7 14 5-5 5 5"/>',
  down: '<path d="m7 10 5 5 5-5"/>',
  del: '<path d="M6 6l12 12M18 6 6 18"/>',
  list: '<path d="M4 6h11M4 12h11M4 18h7M18 15v-9l3 1.5"/><circle cx="16.5" cy="17" r="2"/>',
  skin: '<circle cx="12" cy="12" r="8.5"/><circle cx="8.5" cy="10" r="1.2"/><circle cx="12" cy="7.5" r="1.2"/><circle cx="15.5" cy="10" r="1.2"/><path d="M12 20.5c-1.5 0-2-1-2-2s1-2 2.5-2H15"/>',
  back: '<path d="M15 5l-7 7 7 7"/>',
  collapse: '<path d="M5 12h14"/>',
  expand: '<path d="M8 4H4v4M16 4h4v4M8 20H4v-4M16 20h4v-4"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/>',
};
const icon = (k) => `<svg class="mu-i" viewBox="0 0 24 24" aria-hidden="true">${ICON[k]}</svg>`;

function loadSettings() {
  const d = { order: [], volume: 0.5, mode: 'all', keep: false, cur: null, src: 'local', player: 'qqmusic', skin: cleanSkin(null), cover: { mode: 'song', pick: null }, dock: true, skinV: 2, fx: 'auto' };
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || '{}');
    return {
      order: Array.isArray(v.order) ? v.order.filter((x) => typeof x === 'string') : d.order,
      volume: Number.isFinite(v.volume) ? Math.min(1, Math.max(0, v.volume)) : d.volume,
      mode: MODES.some(([k]) => k === v.mode) ? v.mode : d.mode,
      keep: v.keep === true,
      cur: typeof v.cur === 'string' ? v.cur : null,
      src: SOURCES.some(([k]) => k === v.src) ? v.src : d.src,
      player: typeof v.player === 'string' && /^[a-z]{1,20}$/.test(v.player) ? v.player : d.player,
      skin: upgradeSkin(v.skin, v.skinV),
      skinV: 2,
      fx: cleanFx(v.fx),
      dock: v.dock !== false,
      cover: {
        mode: COVER_MODES.some(([k]) => k === v.cover?.mode) ? v.cover.mode : 'song',
        pick: typeof v.cover?.pick === 'string' && (v.cover.pick === MY_COVER || v.cover.pick.startsWith('/api/music/covers/')) ? v.cover.pick : null,
      },
    };
  } catch {
    return d;
  }
}

/** 本机浏览器里的曲库：一条记录 = { id, name, size, type, blob }。浏览器不给用时返回 null，只在这次打开里有效。 */
function openDb() {
  return new Promise((resolve) => {
    try {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: 'id' });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
      req.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

function run(db, mode, fn) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const out = fn(tx.objectStore(STORE));
    tx.oncomplete = () => resolve(out?.result);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

export class Music {
  constructor() {
    this.el = document.createElement('div');
    this.el.className = 'mu';
    this.audio = new Audio();
    this.audio.preload = 'auto';
    this.set = loadSettings();
    this.tracks = []; // 按播放顺序
    this.memory = new Map(); // 存不进本地库时，这次打开里先放内存
    this.cur = -1;
    this.url = '';
    this.history = []; // 随机时「上一首」往回走
    this.open = false;
    this.view = 'player'; // player / list / skin
    this.error = '';
    this.away = false; // 离开终端时被我们暂停的
    this.onHub = true;
    this.yield = null; // 网页里别的声音正在放
    this.fading = 0;
    this.art = new Map(); // 歌曲 id → 内嵌封面的地址（没有为 ''）
    this.myCover = ''; // 自己上传的封面
    this.covers = null; // 本机成员封面 [{name, url}]
    this.standeeArt = null;
    this.sys = null; // /api/nowplaying 的回复
    this.sysAt = 0;
    this.sysKey = '';
    this.sysBusy = false;
    this.pollT = 0;
    this.fx = new CoverFx();
    this.audio.volume = this.set.volume;
    this.audio.addEventListener('ended', () => this.onEnded());
    this.audio.addEventListener('play', () => this.paint());
    this.audio.addEventListener('pause', () => this.paint());
    this.audio.addEventListener('timeupdate', () => this.tick());
    this.audio.addEventListener('loadedmetadata', () => this.tick());
    this.audio.addEventListener('error', () => {
      if (!this.audio.getAttribute('src')) return;
      this.error = '这首放不了（格式这台电脑的浏览器不支持），换一首试试。';
      this.paint();
    });
    this.el.addEventListener('click', (e) => this.onClick(e));
    this.el.addEventListener('input', (e) => this.onInput(e));
    this.el.addEventListener('change', (e) => this.onChange(e));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.open) {
        e.stopPropagation();
        this.toggle(false, this.el.contains(document.activeElement) || document.activeElement === document.body);
      }
    });
    this.el.addEventListener('dragover', (e) => {
      if (this.open && e.dataTransfer?.types?.includes('Files')) e.preventDefault();
    });
    this.el.addEventListener('drop', (e) => {
      if (!this.open || !e.dataTransfer?.files?.length) return;
      e.preventDefault();
      this.add([...e.dataTransfer.files]);
    });
    document.addEventListener('pointerdown', (e) => {
      if (this.open && !this.el.contains(e.target)) this.toggle(false, false);
    });
    // 别的声音（文件预览里的音频 / 视频）开始放：先淡出让开，它停了再接着放
    document.addEventListener(
      'play',
      (e) => {
        const m = e.target;
        if (m === this.audio || !(m instanceof HTMLMediaElement) || m.muted) return;
        if (!this.playing()) return;
        this.yield = m;
        this.fadeOut();
      },
      true,
    );
    const back = (e) => {
      if (e.target !== this.yield) return;
      this.yield = null;
      if (!this.away) this.resume();
    };
    document.addEventListener('pause', back, true);
    document.addEventListener('ended', back, true);
    this.mediaKeys();
    this.ready = this.init();
  }

  async init() {
    this.db = await openDb();
    let rows = [];
    if (this.db) rows = (await run(this.db, 'readonly', (s) => s.getAll()).catch(() => [])) || [];
    const mine = rows.find((r) => r.id === MY_COVER);
    if (mine?.blob) this.myCover = URL.createObjectURL(mine.blob);
    rows = rows.filter((r) => r.id !== MY_COVER);
    const rank = new Map(this.set.order.map((id, i) => [id, i]));
    rows.sort((a, b) => (rank.get(a.id) ?? 1e9) - (rank.get(b.id) ?? 1e9) || (a.added || 0) - (b.added || 0));
    this.tracks = rows.map(({ id, name, size, type }) => ({ id, name, size, type }));
    this.cur = this.tracks.findIndex((t) => t.id === this.set.cur);
    this.save();
    this.paint();
    this.loadArt(this.cur);
    this.loadCovers();
    this.schedule(0);
  }

  save() {
    this.set.order = this.tracks.map((t) => t.id);
    this.set.cur = this.tracks[this.cur]?.id || null;
    try {
      localStorage.setItem(KEY, JSON.stringify(this.set));
    } catch {
      // 存不了（隐私模式）就只在这次生效
    }
  }

  system() {
    return this.set.src === 'system';
  }

  playing() {
    return !this.audio.paused && !!this.audio.getAttribute('src');
  }

  sysNow() {
    return this.sys?.available ? this.sys.now : null;
  }

  /** 电脑上的播放器正在放，进度按上次读到的位置往后推。 */
  sysPos() {
    const n = this.sysNow();
    if (!n || !Number.isFinite(n.position)) return NaN;
    const run = n.status === 'playing' ? (performance.now() - this.sysAt) / 1000 : 0;
    return Math.min(n.duration ?? Infinity, n.position + run);
  }

  /** 把播放条挂到终端首页顶栏的空位上（首页每次重画都会再挂一次，状态不丢）。 */
  mount(slot) {
    if (slot) slot.replaceWith(this.el);
  }

  /** 回到终端：之前是因为离开才停的，就淡入接着放。 */
  enter() {
    this.onHub = true;
    this.schedule(0);
    if (!this.away) return;
    this.away = false;
    if (!this.yield) this.resume();
  }

  /** 离开终端：没勾「切到其他页面时继续播放」就淡出暂停，回来再接着放。（QQ 音乐是它自己在放，不去停它。） */
  leave() {
    this.onHub = false;
    this.toggle(false, false);
    if (this.set.keep || !this.playing()) return;
    this.away = true;
    this.fadeOut();
  }

  fade(to, ms = FADE_MS) {
    cancelAnimationFrame(this.fading);
    const a = this.audio;
    const from = a.volume;
    const t0 = performance.now();
    return new Promise((resolve) => {
      const step = (now) => {
        const k = Math.min(1, (now - t0) / ms);
        a.volume = from + (to - from) * k;
        if (k < 1) this.fading = requestAnimationFrame(step);
        else resolve();
      };
      this.fading = requestAnimationFrame(step);
    });
  }

  /** 淡出后暂停（离开终端、或者让别的声音先放）；淡出途中回到终端会被淡入打断，不会停。 */
  async fadeOut() {
    await this.fade(0);
    if (this.away || this.yield) this.audio.pause();
    this.audio.volume = this.set.volume;
  }

  async resume() {
    if (!this.audio.getAttribute('src')) return;
    this.audio.volume = 0;
    try {
      await this.audio.play();
    } catch {
      this.audio.volume = this.set.volume;
      return;
    }
    this.fade(this.set.volume);
  }

  async blob(id) {
    if (this.memory.has(id)) return this.memory.get(id);
    if (!this.db) return null;
    const row = await run(this.db, 'readonly', (s) => s.get(id)).catch(() => null);
    return row?.blob || null;
  }

  /** 读第 i 首里内嵌的封面（只读文件开头的标签部分），读完重画一次。 */
  async loadArt(i) {
    const t = this.tracks[i];
    if (!t || this.art.has(t.id)) return;
    this.art.set(t.id, '');
    try {
      const blob = await this.blob(t.id);
      if (!blob) return;
      const head = new Uint8Array(await blob.slice(0, 10).arrayBuffer());
      if (head.length < 10 || head[0] !== 0x49) return;
      const size = Math.min(10 + syncsafe(head, 6), MAX_ART_BYTES);
      const pic = id3Picture(await blob.slice(0, size).arrayBuffer());
      if (!pic) return;
      this.art.set(t.id, URL.createObjectURL(new Blob([pic.data], { type: pic.type })));
      if (this.tracks[this.cur]?.id === t.id) this.paint();
    } catch {
      // 读不出就当没有
    }
  }

  /** 本机成员封面和大立绘：第一次打开卡片时取一次。 */
  async loadCovers() {
    if (this.covers) return;
    this.covers = [];
    const [c, s] = await Promise.all([api('/api/music/covers').catch(() => null), api('/api/standee').catch(() => null)]);
    this.covers = Array.isArray(c?.covers) ? c.covers : [];
    this.standeeArt = s?.available ? s.variants?.[0]?.image || null : null;
    this.paint();
  }

  /** 封面上飘什么：[特效, 颜色]；「跟着封面」时看这张成员封面在 effects.json 里配的。 */
  fxOf(cover = this.coverUrl()) {
    const m = this.set.fx;
    if (m === 'off') return ['', ''];
    if (isFx(m)) return [m, ''];
    const c = this.covers?.find((x) => x.url === cover);
    return isFx(c?.fx) ? [c.fx, c.color || ''] : ['', ''];
  }

  /** 卡片开着、在播放器页时让封面动起来，否则停下。 */
  syncFx() {
    const c = this.open && this.view === 'player' ? this.el.querySelector('.mu-panel .mu-fx') : null;
    const [k, color] = c ? this.fxOf() : ['', ''];
    this.fx.set(c, k, color);
  }

  /** 这一首用哪张封面（按「封面」设置的优先顺序）。 */
  coverUrl() {
    const sys = this.system();
    const n = this.sysNow();
    const song = sys ? n?.cover || '' : this.art.get(this.tracks[this.cur]?.id) || '';
    const mine = this.covers?.find((c) => coverPath(c.url) === coverPath(this.set.cover.pick));
    const pick = this.set.cover.pick === MY_COVER ? this.myCover : mine?.url || '';
    const list = this.covers || [];
    let k = sys ? [...String(n?.title || '')].reduce((a, ch) => a + ch.charCodeAt(0), 0) : this.cur;
    k = Math.max(0, k);
    const rot = list.length ? list[k % list.length].url : '';
    const order = { song: [song, pick, rot], fixed: [pick, song, rot], rotate: [rot, song, pick] }[this.set.cover.mode];
    return order.find(Boolean) || this.standeeArt || '';
  }

  /** 放第 i 首（从头开始）。 */
  async playAt(i, record = true) {
    const t = this.tracks[i];
    if (!t) return;
    if (record && this.cur >= 0 && this.cur !== i) this.history.push(this.cur);
    this.history = this.history.slice(-50);
    this.error = '';
    const blob = await this.blob(t.id);
    if (!blob) {
      this.error = '这首的文件找不到了，删掉后重新添加试试。';
      this.paint();
      return;
    }
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = URL.createObjectURL(blob);
    this.cur = i;
    this.away = false;
    this.yield = null;
    cancelAnimationFrame(this.fading);
    this.audio.src = this.url;
    this.audio.volume = this.set.volume;
    this.save();
    this.paint();
    this.loadArt(i);
    try {
      await this.audio.play();
    } catch {
      // 浏览器不让放（比如还没点过页面）就停在这首，等用户再点
    }
  }

  async playPause() {
    if (this.system()) return this.sysDo('toggle');
    if (!this.tracks.length) return this.pick();
    if (this.playing()) {
      this.away = false;
      this.audio.pause();
      return;
    }
    if (this.audio.getAttribute('src') && this.tracks[this.cur]) return this.resume();
    return this.playAt(Math.max(0, this.cur));
  }

  next() {
    if (this.system()) return this.sysDo('next');
    if (!this.tracks.length) return;
    this.playAt(nextIndex(this.set.mode, this.cur, this.tracks.length));
  }

  prev() {
    if (this.system()) return this.sysDo('prev');
    if (!this.tracks.length) return;
    // 放了好几秒就回到这首开头（和手机上一样）；随机时往回走
    if (this.audio.currentTime > 3 && this.playing()) {
      this.audio.currentTime = 0;
      return;
    }
    if (this.set.mode === 'shuffle' && this.history.length) return this.playAt(this.history.pop(), false);
    const n = this.tracks.length;
    this.playAt(this.cur <= 0 ? n - 1 : this.cur - 1);
  }

  onEnded() {
    if (this.set.mode === 'one') {
      this.audio.currentTime = 0;
      this.audio.play().catch(() => {});
      return;
    }
    this.next();
  }

  /** 键盘媒体键 / 系统媒体卡片控制本地音乐（浏览器支持时）。 */
  mediaKeys() {
    const ms = navigator.mediaSession;
    if (!ms) return;
    const on = (k, fn) => {
      try {
        ms.setActionHandler(k, () => !this.system() && fn());
      } catch {
        // 不支持这个动作
      }
    };
    on('play', () => this.playPause());
    on('pause', () => this.playPause());
    on('nexttrack', () => this.next());
    on('previoustrack', () => this.prev());
  }

  // ---------- QQ 音乐（电脑上的播放器，经圆桌服务读系统媒体控制） ----------

  schedule(ms) {
    clearTimeout(this.pollT);
    if (!this.system() || (!this.onHub && !this.open)) return;
    this.pollT = setTimeout(() => this.poll(), ms ?? (this.open ? 1000 : 4000));
  }

  async poll() {
    try {
      this.sys = await api(`/api/nowplaying?player=${encodeURIComponent(this.set.player)}`);
    } catch (e) {
      this.sys = { available: false, reason: `连不上圆桌服务：${e.message}`, now: null, players: this.sys?.players || [] };
    }
    this.sysAt = performance.now();
    const n = this.sys.now;
    const key = JSON.stringify([this.sys.available, this.sys.reason, n?.title, n?.artist, n?.status, n?.cover, n?.can, this.sys.found]);
    if (key !== this.sysKey) {
      this.sysKey = key;
      this.paint();
    } else this.tick();
    this.schedule();
  }

  async sysDo(action, position) {
    if (this.sysBusy) return;
    this.sysBusy = true;
    this.error = '';
    try {
      this.sys = await api('/api/nowplaying/control', { method: 'POST', body: { player: this.set.player, action, ...(position != null ? { position } : {}) } });
      this.sysAt = performance.now();
      this.sysKey = '';
    } catch (e) {
      this.error = e.message;
    } finally {
      this.sysBusy = false;
    }
    this.paint();
    this.schedule(400); // 切歌后播放器要一会儿才报新歌
  }

  setSource(src) {
    if (this.set.src === src) return;
    this.set.src = src;
    this.error = '';
    if (src === 'system' && this.playing()) {
      this.away = false;
      this.audio.pause();
    }
    this.save();
    this.paint();
    this.schedule(0);
  }

  // ---------- 曲库 ----------

  /** 加进播放列表：只收浏览器能放的音频；存进本机浏览器的曲库，存不下就只在这次打开里有效。 */
  async add(files) {
    this.error = '';
    let bad = 0;
    let added = 0;
    for (const f of files) {
      const ok = (f.type.startsWith('audio/') || /\.(mp3|ogg|oga|wav|flac|m4a|aac|opus|webm)$/i.test(f.name)) &&
        (!f.type || this.audio.canPlayType(f.type) !== '');
      if (!ok) {
        bad++;
        continue;
      }
      const id = `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
      const t = { id, name: trackName(f.name), size: f.size, type: f.type };
      let stored = false;
      if (this.db) {
        stored = await run(this.db, 'readwrite', (s) => s.put({ ...t, blob: f, added: Date.now() }))
          .then(() => true)
          .catch(() => false);
      }
      if (!stored) {
        this.memory.set(id, f);
        this.error = '这台电脑的浏览器存不下了：新加的歌这次能放，关掉网页后要重新添加。';
      }
      this.tracks.push(t);
      added++;
    }
    if (bad) this.error = `有 ${bad} 个文件不是这台电脑的浏览器能放的音频，没有加进来。${this.error}`;
    if (added && this.cur < 0) {
      this.cur = this.tracks.length - added;
      this.loadArt(this.cur);
    }
    this.save();
    this.paint();
  }

  async remove(i) {
    const t = this.tracks[i];
    if (!t) return;
    if (i === this.cur) {
      this.audio.pause();
      this.audio.removeAttribute('src');
      this.audio.load();
      if (this.url) URL.revokeObjectURL(this.url);
      this.url = '';
    }
    this.tracks.splice(i, 1);
    this.memory.delete(t.id);
    this.history = this.history.filter((h) => h !== i).map((h) => (h > i ? h - 1 : h));
    if (i < this.cur) this.cur--;
    else if (i === this.cur) this.cur = this.tracks.length ? Math.min(i, this.tracks.length - 1) : -1;
    if (this.db) await run(this.db, 'readwrite', (s) => s.delete(t.id)).catch(() => {});
    this.save();
    this.paint();
  }

  move(i, d) {
    const j = i + d;
    if (j < 0 || j >= this.tracks.length) return;
    [this.tracks[i], this.tracks[j]] = [this.tracks[j], this.tracks[i]];
    if (this.cur === i) this.cur = j;
    else if (this.cur === j) this.cur = i;
    this.history = [];
    this.save();
    this.paint();
    this.el.querySelector(`[data-mu-row="${j}"] [data-mu="${d < 0 ? 'up' : 'down'}"]`)?.focus();
  }

  pick() {
    if (!this.open) this.toggle(true);
    this.el.querySelector('[data-mu-file]')?.click();
  }

  /** 自己上传一张封面：存进本机浏览器，不上传。 */
  async setMyCover(f) {
    const head = new Uint8Array(await f.slice(0, 16).arrayBuffer());
    if (!imageType(head) || f.size > MAX_ART_BYTES) {
      this.error = '封面只收 8 MB 以内的 png / jpg / webp 图片。';
      return this.paint();
    }
    if (this.db) await run(this.db, 'readwrite', (s) => s.put({ id: MY_COVER, blob: f })).catch(() => {});
    if (this.myCover) URL.revokeObjectURL(this.myCover);
    this.myCover = URL.createObjectURL(f);
    this.set.cover.pick = MY_COVER;
    this.save();
    this.paint();
  }

  toggle(open = !this.open, refocus = true) {
    if (this.open === open) return;
    this.open = open;
    if (open) {
      this.view = 'player';
      this.loadCovers();
    }
    this.paint();
    this.schedule(0);
    if (open) this.el.querySelector('.mu-panel [data-mu="play"]')?.focus();
    else if (refocus) this.el.querySelector('.mu-btn')?.focus();
  }

  /** 只刷新进度和时间（不重画，免得拖进度条时被打断）。 */
  tick() {
    const sys = this.system();
    const a = this.audio;
    const cur = sys ? this.sysPos() : a.currentTime;
    const dur = sys ? this.sysNow()?.duration : a.duration;
    const seek = this.el.querySelector('[data-mu-seek]');
    if (seek && !this.seeking) {
      seek.max = Number.isFinite(dur) ? String(dur) : '0';
      seek.value = String(Number.isFinite(cur) ? cur : 0);
    }
    if (seek) {
      const v = Number(seek.value);
      const m = Number(seek.max);
      seek.style.setProperty('--p', `${m > 0 ? Math.min(100, (v / m) * 100) : 0}%`);
    }
    const bar = this.el.querySelector('.mu-mbar i');
    if (bar) bar.style.width = `${Number.isFinite(cur) && dur > 0 ? Math.min(100, (cur / dur) * 100) : 0}%`;
    const c = this.el.querySelector('.mu-cur');
    const d = this.el.querySelector('.mu-dur');
    if (c) c.textContent = clock(this.seeking ? Number(seek?.value) : cur);
    if (d) d.textContent = clock(dur);
    if (sys && this.sysNow()?.status === 'playing' && (this.open || this.set.dock)) {
      clearTimeout(this.tickT);
      this.tickT = setTimeout(() => this.tick(), 500);
    }
  }

  skinStyle() {
    const s = this.set.skin;
    const bg = isGlass(s) ? 'var(--hb-glass,rgba(255,255,255,.055))' : rgba(s.bg, s.alpha);
    return `--mu-bg:${bg};--mu-solid:${s.bg};--mu-accent:${s.accent};--mu-text:${s.text};--mu-on:${onColor(s.accent)}`;
  }

  /** 卡片主体：画框封面、歌名、进度、大按钮。 */
  playerHtml() {
    const sys = this.system();
    const n = this.sysNow();
    const t = this.tracks[this.cur];
    const on = sys ? n?.status === 'playing' : this.playing() && !this.away;
    const [mode, modeLabel] = MODES.find(([k]) => k === this.set.mode);
    const vol = Math.round(this.set.volume * 100);
    const cover = this.coverUrl();
    let title;
    let sub;
    if (sys) {
      title = n ? n.title || '（没有歌名）' : this.sys ? (this.sys.available ? '没在放歌' : '还没连上') : '正在连接…';
      sub = n ? [n.artist, n.album].filter(Boolean).join(' · ') : this.sys?.reason || (this.sys?.available ? '打开 QQ 音乐放一首歌，这里就会跟着显示' : '');
    } else {
      title = t ? t.name : this.tracks.length ? '点播放开始' : '还没有音乐，先添加几首';
      sub = t ? `第 ${this.cur + 1} / ${this.tracks.length} 首` : '';
    }
    const can = (k) => (sys ? !!n?.can?.includes(k) : this.tracks.length > 0);
    const playOk = sys ? !!n && (on ? can('pause') : can('play')) : true;
    const seekOk = sys ? can('seek') && Number.isFinite(n?.duration) : !!t;
    const tag = this.set.skin.tag || (sys ? (n?.source ? `${n.source} · 电脑上正在放` : 'QQ 音乐') : '本地音乐');
    return `
      <figure class="mu-art${cover ? '' : ' empty'}">${cover ? `<img src="${esc(cover)}" alt="" draggable="false">${this.set.fx === 'off' ? '' : `<img class="mu-hair" src="${esc(cover)}" alt="" draggable="false" aria-hidden="true">`}<canvas class="mu-fx" aria-hidden="true"></canvas>` : `<span class="mu-art-ph">${icon('note')}</span>`}</figure>
      <div class="mu-meta">
        <span class="mu-tag">${esc(tag)}</span>
        <b class="mu-now" title="${esc(title)}">${esc(title)}</b>
        <span class="mu-sub">${esc(sub)}</span>
      </div>
      <div class="mu-prog">
        <input type="range" data-mu-seek min="0" max="0" step="0.1" value="0" aria-label="播放进度"${seekOk ? '' : ' disabled'}>
        <div class="mu-times"><span class="mu-cur">0:00</span>${sys ? '' : `<span class="mu-mode">${modeLabel}</span>`}<span class="mu-dur">--:--</span></div>
      </div>
      <div class="mu-ctl">
        ${sys ? '<span class="mu-gap"></span>' : `<button type="button" class="mu-ib mu-sm" data-mu="mode" title="${modeLabel}（点一下换）" aria-label="循环方式：${modeLabel}">${icon(mode)}</button>`}
        <button type="button" class="mu-ib mu-big" data-mu="prev" aria-label="上一首"${can('prev') ? '' : ' disabled'}>${icon('prev')}</button>
        <button type="button" class="mu-ib mu-play" data-mu="play" aria-label="${on ? '暂停' : '播放'}"${playOk ? '' : ' disabled'}>${icon(on ? 'pause' : 'play')}</button>
        <button type="button" class="mu-ib mu-big" data-mu="next" aria-label="下一首"${can('next') ? '' : ' disabled'}>${icon('next')}</button>
        <button type="button" class="mu-ib mu-sm" data-mu="view" data-v="list" aria-label="${sys ? '连接设置' : '播放列表'}" title="${sys ? '连接设置' : '播放列表'}">${icon(sys ? 'link' : 'list')}</button>
      </div>
      ${sys ? '' : `<label class="mu-vol">${icon(vol ? 'vol' : 'mute')}<input type="range" data-mu-vol min="0" max="100" step="1" value="${vol}" aria-label="音量" style="--p:${vol}%"><span>${vol}</span></label>`}`;
  }

  /** 播放列表（本地）或连接设置（QQ 音乐）。 */
  listHtml() {
    if (this.system()) {
      const players = this.sys?.players || [{ key: 'qqmusic', label: 'QQ 音乐' }];
      const found = this.sys?.found || [];
      return `
        <b class="mu-h">连接电脑上的播放器</b>
        <label class="mu-field">跟随<select data-mu-player>${players.map((p) => `<option value="${esc(p.key)}"${p.key === this.set.player ? ' selected' : ''}>${esc(p.label)}</option>`).join('')}</select></label>
        <p class="mu-state">${this.sys ? (this.sys.available ? (found.length ? `检测到：${esc(found.join('、'))}` : '没检测到在放歌的播放器') : esc(this.sys.reason || '')) : '正在连接…'}</p>
        <ol class="mu-steps">
          <li>在这台电脑上装好 QQ 音乐桌面版，登录你自己的账号，随便放一首歌。</li>
          <li>按一下键盘上的音量键，屏幕上出现带歌名的小卡片，就说明它连上了系统媒体控制。</li>
          <li>这里会显示同一首歌，按钮直接控制 QQ 音乐；声音还是 QQ 音乐自己放。</li>
        </ol>
        <p class="hb-hint">圆桌只读歌名、歌手、封面和进度，不登录、不碰你的账号，也不下载或保存任何音频。选「任意播放器」时会跟随系统当前在放的那一个（包括浏览器里的视频）。</p>`;
    }
    const on = this.playing() && !this.away;
    const rows = this.tracks
      .map(
        (x, i) => `<li class="mu-row${i === this.cur ? ' cur' : ''}" data-mu-row="${i}">
          <button type="button" class="mu-name" data-mu="at" aria-current="${i === this.cur}" title="${esc(x.name)}">${i === this.cur && on ? '<span class="mu-eq" aria-hidden="true"><i></i><i></i><i></i></span>' : `<span class="mu-no">${i + 1}</span>`}<span>${esc(x.name)}</span></button>
          <button type="button" class="mu-ib" data-mu="up" aria-label="上移 ${esc(x.name)}"${i === 0 ? ' disabled' : ''}>${icon('up')}</button>
          <button type="button" class="mu-ib" data-mu="down" aria-label="下移 ${esc(x.name)}"${i === this.tracks.length - 1 ? ' disabled' : ''}>${icon('down')}</button>
          <button type="button" class="mu-ib" data-mu="del" aria-label="删除 ${esc(x.name)}">${icon('del')}</button>
        </li>`,
      )
      .join('');
    return `
      <b class="mu-h">播放列表</b>
      ${rows ? `<ol class="mu-list" aria-label="播放列表">${rows}</ol>` : '<p class="mu-state">还没有音乐。可以一次选好几首，也可以直接拖进来。</p>'}
      <div class="mu-acts">
        <button type="button" class="mu-chip" data-mu="add">添加音乐…</button>
        <span class="mu-count">${this.tracks.length ? `共 ${this.tracks.length} 首` : ''}</span>
      </div>
      <label class="mu-keep"><input type="checkbox" data-mu-keep${this.set.keep ? ' checked' : ''}> 切到其他页面时继续播放</label>
      <p class="hb-hint">音乐只存在这台电脑的浏览器里，不上传；删除只删这里的副本，电脑上的原文件不动。</p>`;
  }

  /** 外观：预设、三种颜色、通透度、顶部小字、封面。 */
  skinHtml() {
    const s = this.set.skin;
    const covers = this.covers || [];
    const pick = coverPath(this.set.cover.pick);
    const thumb = (url, label, val) =>
      `<button type="button" class="mu-thumb" data-mu="cover" data-v="${esc(val)}" aria-pressed="${pick === coverPath(val)}" title="${esc(label)}"><img src="${esc(url)}" alt="${esc(label)}" loading="lazy"></button>`;
    return `
      <b class="mu-h">外观</b>
      <div class="mu-presets" role="group" aria-label="配色">${Object.entries(SKINS)
        .map(([k, p]) => `<button type="button" class="mu-preset" data-mu="preset" data-v="${k}" aria-pressed="${s.preset === k && s.bg === p.bg && s.accent === p.accent && s.text === p.text}" style="--a:${p.accent};--b:${p.bg}"><i></i>${p.label}</button>`)
        .join('')}</div>
      <div class="mu-colors">
        <label>底色<input type="color" data-mu-color="bg" value="${s.bg}"></label>
        <label>主色<input type="color" data-mu-color="accent" value="${s.accent}"></label>
        <label>文字<input type="color" data-mu-color="text" value="${s.text}"></label>
      </div>
      <label class="mu-field">通透<input type="range" data-mu-alpha min="35" max="100" step="1" value="${Math.round(s.alpha * 100)}" aria-label="底色不透明度" style="--p:${((s.alpha - 0.35) / 0.65) * 100}%"></label>
      <label class="mu-field">小字<input type="text" data-mu-tag maxlength="40" value="${esc(s.tag)}" placeholder="封面下方那行小字，留空用默认"></label>
      <b class="mu-h">封面</b>
      <label class="mu-field">动态<select data-mu-fx>${FX_MODES.map(([k, l]) => `<option value="${k}"${this.set.fx === k ? ' selected' : ''}>${l}</option>`).join('')}</select></label>
      <label class="mu-field">优先<select data-mu-covermode>${COVER_MODES.map(([k, l]) => `<option value="${k}"${this.set.cover.mode === k ? ' selected' : ''}>${l}</option>`).join('')}</select></label>
      <div class="mu-thumbs">${covers.map((c) => thumb(c.url, c.name, c.url)).join('')}${this.myCover ? thumb(this.myCover, '我上传的', MY_COVER) : ''}<button type="button" class="mu-thumb add" data-mu="mycover" aria-label="上传一张封面">＋</button></div>
      <input type="file" accept="image/png,image/jpeg,image/webp" data-mu-coverfile hidden>
      <p class="hb-hint">${covers.length ? '' : '成员侧脸封面放进电脑上的 data\\avatars\\music_covers\\ 后就能在这里选。'}外观和上传的封面只存在这台电脑的浏览器里。</p>
      <button type="button" class="mu-chip" data-mu="reset">恢复默认外观</button>`;
  }

  /** 首页上的入口：展开时是时钟下面的一张小卡（封面、歌名、播放键、细进度），收起时是一颗小胶囊。 */
  capsuleHtml(on, name) {
    const label = `<span class="mu-label">${on && name ? esc(name) : this.set.dock && name ? esc(name) : '音乐'}</span>`;
    const aria = `背景音乐${on && name ? `：正在放 ${esc(name)}` : ''}（点开播放器）`;
    if (!this.set.dock) {
      return `<button type="button" class="mu-btn" data-mu="panel" aria-expanded="${this.open}" aria-label="${aria}">${on ? '<span class="mu-eq" aria-hidden="true"><i></i><i></i><i></i></span>' : icon('note')}${label}</button>
        <button type="button" class="mu-ib mu-pin" data-mu="dock" aria-label="在首页展开音乐小卡" title="展开成小卡">${icon('expand')}</button>`;
    }
    const sys = this.system();
    const n = this.sysNow();
    const t = this.tracks[this.cur];
    const cover = this.coverUrl();
    const sub = sys ? (n ? n.artist || n.source : this.sys?.available === false ? '还没连上 QQ 音乐' : 'QQ 音乐') : t ? '本地音乐' : this.tracks.length ? '点播放开始' : '点开添加音乐';
    const can = (k) => (sys ? !!n?.can?.includes(k) : this.tracks.length > 0);
    const playOk = sys ? !!n && (on ? can('pause') : can('play')) : true;
    return `<div class="mu-mini" style="${this.skinStyle()}">
        <button type="button" class="mu-btn" data-mu="panel" aria-expanded="${this.open}" aria-label="${aria}">
          <span class="mu-mthumb">${cover ? `<img src="${esc(cover)}" alt="" draggable="false">` : icon('note')}</span>
          <span class="mu-mtxt">${label}<span class="mu-msub">${on ? '<span class="mu-eq" aria-hidden="true"><i></i><i></i><i></i></span>' : ''}${esc(this.set.skin.tag || sub)}</span></span>
        </button>
        <button type="button" class="mu-ib" data-mu="prev" aria-label="上一首"${can('prev') ? '' : ' disabled'}>${icon('prev')}</button>
        <button type="button" class="mu-ib mu-mplay" data-mu="play" aria-label="${on ? '暂停' : '播放'}"${playOk ? '' : ' disabled'}>${icon(on ? 'pause' : 'play')}</button>
        <button type="button" class="mu-ib" data-mu="next" aria-label="下一首"${can('next') ? '' : ' disabled'}>${icon('next')}</button>
        <button type="button" class="mu-ib mu-pin" data-mu="dock" aria-label="收起成小按钮" title="收起">${icon('collapse')}</button>
        <span class="mu-mbar" aria-hidden="true"><i></i></span>
      </div>`;
  }

  paint() {
    const sys = this.system();
    const n = this.sysNow();
    const t = this.tracks[this.cur];
    const on = sys ? n?.status === 'playing' : this.playing() && !this.away;
    const name = sys ? n?.title : t?.name;
    // 重画后把焦点放回原来那颗按钮（键盘操作不会掉到页面最上面）
    const had = this.el.contains(document.activeElement) ? document.activeElement : null;
    const refocus = had?.dataset.mu
      ? `${had.closest('[data-mu-row]') ? `[data-mu-row="${had.closest('[data-mu-row]').dataset.muRow}"] ` : ''}[data-mu="${had.dataset.mu}"]${had.dataset.v ? `[data-v="${had.dataset.v}"]` : ''}`
      : null;
    this.el.classList.toggle('on', !!on);
    this.el.classList.toggle('docked', this.set.dock);
    const body = { player: () => this.playerHtml(), list: () => this.listHtml(), skin: () => this.skinHtml() }[this.view]();
    const tab = (v, ic, label) => `<button type="button" class="mu-ib mu-sm" data-mu="view" data-v="${v}" aria-pressed="${this.view === v}" aria-label="${label}" title="${label}">${icon(ic)}</button>`;
    this.el.innerHTML = `
      ${this.capsuleHtml(on, name)}
      <div class="mu-panel mu-${this.view}" role="dialog" aria-label="背景音乐" style="${this.skinStyle()}"${this.open ? '' : ' hidden'}>
        <div class="mu-head">
          ${this.view === 'player' ? '' : `<button type="button" class="mu-ib mu-sm" data-mu="view" data-v="player" aria-label="回到播放器">${icon('back')}</button>`}
          <div class="mu-seg" role="group" aria-label="音乐来源">${SOURCES.map(([k, l]) => `<button type="button" data-mu="src" data-v="${k}" aria-pressed="${this.set.src === k}">${l}</button>`).join('')}</div>
          ${tab('skin', 'skin', '外观')}
        </div>
        ${body}
        <input type="file" accept="${ACCEPT}" multiple data-mu-file hidden>
        ${this.error ? `<p class="hb-err" role="alert">${esc(this.error)}</p>` : ''}
      </div>`;
    if (refocus) this.el.querySelector(refocus)?.focus();
    this.tick();
    this.syncFx();
    this.mediaMeta(t, on);
  }

  mediaMeta(t, on) {
    const ms = navigator.mediaSession;
    if (!ms || this.system() || !t || typeof MediaMetadata === 'undefined') return;
    try {
      const art = this.coverUrl();
      ms.metadata = new MediaMetadata({ title: t.name, artist: '圆桌', artwork: art ? [{ src: art }] : [] });
      ms.playbackState = on ? 'playing' : 'paused';
    } catch {
      // 不支持就算了
    }
  }

  onClick(e) {
    const b = e.target.closest('[data-mu]');
    if (!b || b.disabled) return;
    const i = Number(b.closest('[data-mu-row]')?.dataset.muRow);
    const v = b.dataset.v;
    switch (b.dataset.mu) {
      case 'panel':
        return this.toggle();
      case 'play':
        return this.playPause();
      case 'prev':
        return this.prev();
      case 'next':
        return this.next();
      case 'mode': {
        const k = MODES.findIndex(([m]) => m === this.set.mode);
        this.set.mode = MODES[(k + 1) % MODES.length][0];
        this.save();
        return this.paint();
      }
      case 'dock':
        this.set.dock = !this.set.dock;
        this.save();
        this.paint();
        return this.el.querySelector('[data-mu="dock"]')?.focus();
      case 'view':
        this.view = this.view === v ? 'player' : v;
        return this.paint();
      case 'src':
        return this.setSource(v);
      case 'add':
        return this.pick();
      case 'at':
        return i === this.cur && this.audio.getAttribute('src') ? this.playPause() : this.playAt(i);
      case 'up':
        return this.move(i, -1);
      case 'down':
        return this.move(i, 1);
      case 'del':
        return this.remove(i);
      case 'preset':
        this.set.skin = cleanSkin({ preset: v, tag: this.set.skin.tag });
        this.save();
        return this.paint();
      case 'reset':
        this.set.skin = cleanSkin(null);
        this.set.cover = { mode: 'song', pick: null };
        this.set.fx = 'auto';
        this.save();
        return this.paint();
      case 'cover':
        this.set.cover.pick = coverPath(this.set.cover.pick) === coverPath(v) ? null : coverPath(v);
        this.save();
        return this.paint();
      case 'mycover':
        return this.el.querySelector('[data-mu-coverfile]')?.click();
    }
  }

  onInput(e) {
    const el = e.target;
    if (el.matches('[data-mu-vol]')) {
      this.set.volume = Number(el.value) / 100;
      cancelAnimationFrame(this.fading);
      this.audio.volume = this.set.volume;
      el.style.setProperty('--p', `${el.value}%`);
      el.nextElementSibling.textContent = el.value;
      el.previousElementSibling.outerHTML = icon(this.set.volume ? 'vol' : 'mute');
      return;
    }
    if (el.matches('[data-mu-seek]')) {
      this.seeking = true;
      return this.tick();
    }
    // 外观：改颜色时只换卡片的变量，不重画（取色器不会被关掉）
    if (el.matches('[data-mu-color]')) {
      this.set.skin = cleanSkin({ ...this.set.skin, [el.dataset.muColor]: el.value });
      this.el.querySelectorAll('.mu-panel, .mu-mini').forEach((x) => x.setAttribute('style', this.skinStyle()));
      return;
    }
    if (el.matches('[data-mu-alpha]')) {
      this.set.skin = cleanSkin({ ...this.set.skin, alpha: Number(el.value) / 100 });
      el.style.setProperty('--p', `${((this.set.skin.alpha - 0.35) / 0.65) * 100}%`);
      this.el.querySelectorAll('.mu-panel, .mu-mini').forEach((x) => x.setAttribute('style', this.skinStyle()));
      return;
    }
    if (el.matches('[data-mu-tag]')) {
      this.set.skin = cleanSkin({ ...this.set.skin, tag: el.value });
      return this.save();
    }
  }

  onChange(e) {
    const el = e.target;
    if (el.matches('[data-mu-vol]')) return this.save();
    if (el.matches('[data-mu-seek]')) {
      this.seeking = false;
      if (this.system()) return this.sysDo('seek', Number(el.value));
      if (Number.isFinite(this.audio.duration)) this.audio.currentTime = Number(el.value);
      return;
    }
    if (el.matches('[data-mu-keep]')) {
      this.set.keep = el.checked;
      return this.save();
    }
    if (el.matches('[data-mu-color], [data-mu-alpha]')) {
      this.save();
      return this.paint();
    }
    if (el.matches('[data-mu-player]')) {
      this.set.player = el.value;
      this.sys = null;
      this.sysKey = '';
      this.save();
      this.paint();
      return this.schedule(0);
    }
    if (el.matches('[data-mu-fx]')) {
      this.set.fx = cleanFx(el.value);
      this.save();
      return this.paint();
    }
    if (el.matches('[data-mu-covermode]')) {
      this.set.cover.mode = el.value;
      this.save();
      return this.paint();
    }
    if (el.matches('[data-mu-coverfile]')) {
      const f = el.files?.[0];
      el.value = '';
      if (f) this.setMyCover(f);
      return;
    }
    if (el.matches('[data-mu-file]')) {
      const files = [...(el.files || [])];
      el.value = '';
      if (files.length) this.add(files);
    }
  }
}
