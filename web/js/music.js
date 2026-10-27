// 终端背景音乐：自己从电脑里挑音频文件（mp3 / ogg / wav / flac / m4a）加进播放列表，
// 可以删除、排序、调音量、上一首 / 下一首、列表循环 / 单曲循环 / 随机，设置记在本机。
// 音乐文件存在这台电脑的浏览器本地库（IndexedDB）里，不经服务器、不上传、不联网下载；删掉只删这里的副本。
// 默认不自动播放（浏览器也不允许），点了才放；切到其他页面时淡出暂停、回到终端再接着放（可以改成一直放）；
// 网页里别的声音（文件预览里的音频、视频）开始放时也先淡出让开，放完再接着放。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const DB_NAME = 'roundtable-music';
const STORE = 'tracks';
const KEY = 'roundtable.music'; // 播放顺序、音量、循环方式、切页是否继续、上次放到哪首
const FADE_MS = 600;
const ACCEPT = 'audio/*,.mp3,.ogg,.oga,.wav,.flac,.m4a,.aac,.opus,.webm';
export const MODES = [
  ['all', '列表循环'],
  ['one', '单曲循环'],
  ['shuffle', '随机'],
];

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

// 极细线性图标（和终端首页同一套，线宽在 CSS 里统一）
const ICON = {
  note: '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z"/>',
  pause: '<path d="M8.5 5.5v13M15.5 5.5v13"/>',
  prev: '<path d="M6 6v12M18 6l-9 6 9 6z"/>',
  next: '<path d="M18 6v12M6 6l9 6-9 6z"/>',
  all: '<path d="M4 11V9a3 3 0 0 1 3-3h12l-3-3M20 13v2a3 3 0 0 1-3 3H5l3 3"/>',
  one: '<path d="M4 11V9a3 3 0 0 1 3-3h12l-3-3M20 13v2a3 3 0 0 1-3 3H5l3 3M12 10v5"/>',
  shuffle: '<path d="M4 7h3c5 0 5 10 10 10h3M4 17h3c2 0 3-1.5 4-3.5M13 9.5C14 8 15 7 17 7h3M18 4l3 3-3 3M18 14l3 3-3 3"/>',
  vol: '<path d="M4 9.5h4l5-4v13l-5-4H4zM16.5 9a4 4 0 0 1 0 6"/>',
  mute: '<path d="M4 9.5h4l5-4v13l-5-4H4zM17 10l4 4M21 10l-4 4"/>',
  up: '<path d="m7 14 5-5 5 5"/>',
  down: '<path d="m7 10 5 5 5-5"/>',
  del: '<path d="M6 6l12 12M18 6 6 18"/>',
};
const icon = (k) => `<svg class="mu-i" viewBox="0 0 24 24" aria-hidden="true">${ICON[k]}</svg>`;

function loadSettings() {
  const d = { order: [], volume: 0.5, mode: 'all', keep: false, cur: null };
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || '{}');
    return {
      order: Array.isArray(v.order) ? v.order.filter((x) => typeof x === 'string') : d.order,
      volume: Number.isFinite(v.volume) ? Math.min(1, Math.max(0, v.volume)) : d.volume,
      mode: MODES.some(([k]) => k === v.mode) ? v.mode : d.mode,
      keep: v.keep === true,
      cur: typeof v.cur === 'string' ? v.cur : null,
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
    this.error = '';
    this.away = false; // 离开终端时被我们暂停的
    this.yield = null; // 网页里别的声音正在放
    this.fading = 0;
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
    this.ready = this.init();
  }

  async init() {
    this.db = await openDb();
    let rows = [];
    if (this.db) rows = (await run(this.db, 'readonly', (s) => s.getAll()).catch(() => [])) || [];
    const rank = new Map(this.set.order.map((id, i) => [id, i]));
    rows.sort((a, b) => (rank.get(a.id) ?? 1e9) - (rank.get(b.id) ?? 1e9) || (a.added || 0) - (b.added || 0));
    this.tracks = rows.map(({ id, name, size, type }) => ({ id, name, size, type }));
    this.cur = this.tracks.findIndex((t) => t.id === this.set.cur);
    this.save();
    this.paint();
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

  playing() {
    return !this.audio.paused && !!this.audio.getAttribute('src');
  }

  /** 把播放条挂到终端首页顶栏的空位上（首页每次重画都会再挂一次，状态不丢）。 */
  mount(slot) {
    if (slot) slot.replaceWith(this.el);
  }

  /** 回到终端：之前是因为离开才停的，就淡入接着放。 */
  enter() {
    if (!this.away) return;
    this.away = false;
    if (!this.yield) this.resume();
  }

  /** 离开终端：没勾「切到其他页面时继续播放」就淡出暂停，回来再接着放。 */
  leave() {
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
    try {
      await this.audio.play();
    } catch {
      // 浏览器不让放（比如还没点过页面）就停在这首，等用户再点
    }
  }

  async playPause() {
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
    if (!this.tracks.length) return;
    this.playAt(nextIndex(this.set.mode, this.cur, this.tracks.length));
  }

  prev() {
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
    if (added && this.cur < 0) this.cur = this.tracks.length - added;
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

  toggle(open = !this.open, refocus = true) {
    if (this.open === open) return;
    this.open = open;
    this.paint();
    if (open) this.el.querySelector('.mu-panel [data-mu="play"]')?.focus();
    else if (refocus) this.el.querySelector('.mu-btn')?.focus();
  }

  /** 只刷新进度和时间（不重画，免得拖进度条时被打断）。 */
  tick() {
    const a = this.audio;
    const seek = this.el.querySelector('[data-mu-seek]');
    if (seek && !this.seeking) {
      seek.max = Number.isFinite(a.duration) ? String(a.duration) : '0';
      seek.value = String(a.currentTime || 0);
    }
    const time = this.el.querySelector('.mu-time');
    if (time) time.textContent = `${clock(a.currentTime)} / ${clock(a.duration)}`;
  }

  paint() {
    const t = this.tracks[this.cur];
    const on = this.playing() && !this.away;
    const [mode, modeLabel] = MODES.find(([k]) => k === this.set.mode);
    const vol = Math.round(this.set.volume * 100);
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
    // 重画后把焦点放回原来那颗按钮（键盘操作不会掉到页面最上面）
    const had = this.el.contains(document.activeElement) ? document.activeElement : null;
    const refocus = had?.dataset.mu
      ? `${had.closest('[data-mu-row]') ? `[data-mu-row="${had.closest('[data-mu-row]').dataset.muRow}"] ` : ''}[data-mu="${had.dataset.mu}"]`
      : null;
    this.el.classList.toggle('on', on);
    this.el.innerHTML = `
      <button type="button" class="mu-btn" data-mu="panel" aria-expanded="${this.open}" aria-label="背景音乐${on && t ? `：正在放 ${esc(t.name)}` : ''}">${on ? '<span class="mu-eq" aria-hidden="true"><i></i><i></i><i></i></span>' : icon('note')}<span class="mu-label">${on && t ? esc(t.name) : '音乐'}</span></button>
      <div class="mu-panel hb-glass" role="dialog" aria-label="背景音乐"${this.open ? '' : ' hidden'}>
        <b class="mu-title">背景音乐</b>
        <div class="mu-now">${t ? esc(t.name) : this.tracks.length ? '点播放开始' : '还没有音乐，先添加几首'}</div>
        <div class="mu-prog">
          <input type="range" data-mu-seek min="0" max="0" step="0.1" value="0" aria-label="播放进度"${t ? '' : ' disabled'}>
          <span class="mu-time">0:00 / --:--</span>
        </div>
        <div class="mu-ctl">
          <button type="button" class="mu-ib" data-mu="mode" title="${modeLabel}（点一下换）" aria-label="循环方式：${modeLabel}">${icon(mode)}</button>
          <button type="button" class="mu-ib" data-mu="prev" aria-label="上一首"${this.tracks.length ? '' : ' disabled'}>${icon('prev')}</button>
          <button type="button" class="mu-ib mu-play" data-mu="play" aria-label="${on ? '暂停' : '播放'}">${icon(on ? 'pause' : 'play')}</button>
          <button type="button" class="mu-ib" data-mu="next" aria-label="下一首"${this.tracks.length ? '' : ' disabled'}>${icon('next')}</button>
          <span class="mu-mode">${modeLabel}</span>
        </div>
        <label class="mu-vol">${icon(vol ? 'vol' : 'mute')}<input type="range" data-mu-vol min="0" max="100" step="1" value="${vol}" aria-label="音量"><span>${vol}</span></label>
        ${rows ? `<ol class="mu-list" aria-label="播放列表">${rows}</ol>` : ''}
        <div class="mu-acts">
          <button type="button" class="hb-chip" data-mu="add">添加音乐…</button>
          <span class="mu-count">${this.tracks.length ? `共 ${this.tracks.length} 首` : '可以一次选好几首，也可以拖进来'}</span>
        </div>
        <input type="file" accept="${ACCEPT}" multiple data-mu-file hidden>
        <label class="mu-keep"><input type="checkbox" data-mu-keep${this.set.keep ? ' checked' : ''}> 切到其他页面时继续播放</label>
        <p class="hb-hint">音乐只存在这台电脑的浏览器里，不上传；删除只删这里的副本，电脑上的原文件不动。${this.error ? `<span class="hb-err" role="alert">${esc(this.error)}</span>` : ''}</p>
      </div>`;
    if (refocus) this.el.querySelector(refocus)?.focus();
    this.tick();
  }

  onClick(e) {
    const b = e.target.closest('[data-mu]');
    if (!b || b.disabled) return;
    const i = Number(b.closest('[data-mu-row]')?.dataset.muRow);
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
    }
  }

  onInput(e) {
    if (e.target.matches('[data-mu-vol]')) {
      this.set.volume = Number(e.target.value) / 100;
      cancelAnimationFrame(this.fading);
      this.audio.volume = this.set.volume;
      e.target.nextElementSibling.textContent = e.target.value;
      e.target.previousElementSibling.outerHTML = icon(this.set.volume ? 'vol' : 'mute');
      return;
    }
    if (e.target.matches('[data-mu-seek]')) {
      this.seeking = true;
      const time = this.el.querySelector('.mu-time');
      if (time) time.textContent = `${clock(Number(e.target.value))} / ${clock(this.audio.duration)}`;
    }
  }

  onChange(e) {
    if (e.target.matches('[data-mu-vol]')) return this.save();
    if (e.target.matches('[data-mu-seek]')) {
      this.seeking = false;
      if (Number.isFinite(this.audio.duration)) this.audio.currentTime = Number(e.target.value);
      return;
    }
    if (e.target.matches('[data-mu-keep]')) {
      this.set.keep = e.target.checked;
      return this.save();
    }
    if (e.target.matches('[data-mu-file]')) {
      const files = [...(e.target.files || [])];
      e.target.value = '';
      if (files.length) this.add(files);
    }
  }
}
