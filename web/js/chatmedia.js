// 聊天、私聊、朋友圈共用的「富媒体」：表情面板（Emoji + 动态表情包）、图片 / 视频 / 语音的发送与显示。
// 文件只存在本机（POST /api/chat/media → data/chat_media/），发消息时带上返回的 id。
// 成员只看到「[图片]」「[语音 5 秒]」这样的文字占位，看不到文件本身。

import { EMOJI, STICKERS, TOKEN_RE, findSticker, recent, remember, setMemberPacks, soloSticker, stickerSVG, token } from './stickers.js';
import { ApiError } from './api.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

export const mediaUrl = (id) => `/api/chat/media/${encodeURIComponent(id)}`;
const MAX_VOICE_S = 60;
const KIND_OF = (mime) => (mime.startsWith('image/') ? 'image' : mime.startsWith('video/') ? 'video' : mime.startsWith('audio/') ? 'voice' : null);
const fmt = (s) => {
  s = Math.max(0, Math.round(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

// --- 图标（极细线性） --------------------------------------------------------------

const I = {
  smile: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M8.5 14.2q3.5 3.2 7 0"/><circle cx="9.2" cy="10" r=".6" class="f"/><circle cx="14.8" cy="10" r=".6" class="f"/></svg>',
  image: '<svg viewBox="0 0 24 24"><rect x="3.5" y="5" width="17" height="14" rx="3"/><circle cx="9" cy="10" r="1.6"/><path d="M4 17l5-4.5 4 3.5 3-2.5 4 3.5"/></svg>',
  video: '<svg viewBox="0 0 24 24"><rect x="3" y="6.5" width="12.5" height="11" rx="2.6"/><path d="M15.5 10.5l5-3v9l-5-3"/></svg>',
  mic: '<svg viewBox="0 0 24 24"><rect x="9" y="3.5" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5"/></svg>',
  sticker: '<svg viewBox="0 0 24 24"><path d="M20.5 12.5V7a3.5 3.5 0 0 0-3.5-3.5H7A3.5 3.5 0 0 0 3.5 7v10A3.5 3.5 0 0 0 7 20.5h5.5z"/><path d="M12.5 20.5v-4a4 4 0 0 1 4-4h4"/><path d="M8.5 13.5q2 2 4 .5"/><circle cx="9" cy="9.5" r=".6" class="f"/><circle cx="14" cy="9.5" r=".6" class="f"/></svg>',
  clock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>',
  play: '<svg viewBox="0 0 24 24"><path d="M8 5.5v13l10.5-6.5z" class="f"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M8 5.5h3v13H8zM13 5.5h3v13h-3z" class="f"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/></svg>',
  left: '<svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"/></svg>',
  right: '<svg viewBox="0 0 24 24"><path d="M9 5l7 7-7 7"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5"/></svg>',
};
export const ICONS = I;

// --- 显示 ----------------------------------------------------------------------

/** 文字转 HTML：转义、换行、表情包记号画成小表情；mentions 为真时高亮 @昵称。 */
export function richText(text, { mentions = false } = {}) {
  let html = esc(text);
  if (mentions) html = html.replace(/@(\S{1,12})/g, '<b class="at">@$1</b>');
  html = html.replace(/\[表情:([^\]·]{1,6})·([^\]]{1,6})\]/g, (all, pack, name) => stickerSVG(pack, name) || all);
  return html.replace(/\n/g, '<br>');
}

/** 消息 / 动态里真正给人看的文字：只发了图片时不显示占位，有说明文字时只显示说明。 */
export function visibleText(text, meta) {
  if (meta?.card && !meta?.media?.length) return cardFree(text, meta.card);
  if (!meta?.media?.length) return text || '';
  if (meta.auto_text) return '';
  return meta.caption ?? text ?? '';
}

export { setMemberPacks, soloSticker, stickerSVG };

// --- 转发卡片（帖子 / 文章 / 视频） ------------------------------------------------------

const CARD_LABEL = { article: '链接', post: '帖子', video: '视频' };

/** 正文里给成员看的「[链接] 标题」占位不再显示（卡片本身已经显示了）。 */
function cardFree(text, card) {
  const holder = `[${CARD_LABEL[card.kind] || '链接'}] ${card.title || ''}`;
  return String(text || '').replace(holder, '').trim();
}

/** 微信式链接卡片：标题、摘要、来源；视频带播放角标。只接受 http(s) 网址，新窗口打开。 */
export function cardHTML(card, { layout = 'chat' } = {}) {
  if (!card || !/^https?:\/\//i.test(card.url || '')) return '';
  const kind = CARD_LABEL[card.kind] ? card.kind : 'article';
  const when = card.published ? new Date(card.published).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) : '';
  const flag = card.unverified ? '<span class="gc-tag away">未核实</span>' : '';
  const icon = kind === 'video' ? `<span class="cm-card-ic vid">${I.play}</span>` : `<span class="cm-card-ic">${kind === 'post' ? '帖' : '文'}</span>`;
  const sum = card.summary ? `<span class="cm-card-sum">${esc(card.summary)}</span>` : '';
  return `<a class="cm-card ${layout} ${kind}" href="${esc(card.url)}" target="_blank" rel="noopener noreferrer nofollow"><span class="cm-card-main"><b class="cm-card-title">${esc(card.title)}</b>${sum}</span>${icon}<small class="cm-card-src">${esc(CARD_LABEL[kind])} · ${esc(card.source || '')}${when ? ` · ${esc(when)}` : ''}${flag}</small></a>`;
}

function hashBars(id, n) {
  let h = 2166136261;
  for (const ch of String(id)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  const out = [];
  for (let i = 0; i < n; i++) {
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    const r = ((h >>> 0) % 1000) / 1000;
    const env = Math.sin((Math.PI * (i + 0.5)) / n) * 0.55 + 0.45; // 中间高两头低，像一句话
    out.push(Math.round((0.22 + r * 0.78 * env) * 100));
  }
  return out;
}

export function voiceHTML(item, { mine = false } = {}) {
  const d = item.duration || 0;
  const n = Math.max(12, Math.min(36, Math.round(10 + d * 1.2)));
  const bars = hashBars(item.id, n)
    .map((h, i) => `<i style="--h:${h}%;--k:${i}"></i>`)
    .join('');
  const w = Math.round(Math.min(280, 140 + d * 5));
  return `<button type="button" class="cm-voice${mine ? ' mine' : ''}" data-voice="${esc(item.id)}" data-dur="${d}" style="--w:${w}px" aria-label="语音 ${Math.max(1, Math.round(d))} 秒，点击播放">
    <span class="cm-vplay">${I.play}${I.pause}</span><span class="cm-wave" style="--n:${n}">${bars}</span><span class="cm-vdur">${Math.max(1, Math.round(d))}″</span></button>`;
}

/** group：同一组里的图片 / 视频可以在大图里左右翻。layout：chat 气泡里 / moment 九宫格。 */
export function mediaHTML(items, { group = '', layout = 'chat', mine = false } = {}) {
  if (!items?.length) return '';
  // 生成的自制表情包（sticker: true）：大图、没有气泡，不进九宫格
  const stickers = items.filter((m) => m.kind === 'image' && m.sticker);
  const visual = items.filter((m) => (m.kind === 'image' && !m.sticker) || m.kind === 'video');
  const voices = items.filter((m) => m.kind === 'voice');
  const n = visual.length;
  const cells = visual
    .map((m) => {
      const ratio = m.w && m.h ? ` style="--ar:${(m.w / m.h).toFixed(3)}"` : '';
      if (m.kind === 'image')
        return `<button type="button" class="cm-cell img" data-view="${esc(m.id)}" data-kind="image" data-group="${esc(group)}"${ratio} aria-label="查看图片"><img src="${mediaUrl(m.id)}" alt="" loading="lazy" decoding="async"></button>`;
      return `<button type="button" class="cm-cell vid" data-view="${esc(m.id)}" data-kind="video" data-group="${esc(group)}"${ratio} aria-label="播放视频"><video src="${mediaUrl(m.id)}#t=0.1" preload="metadata" muted playsinline></video><span class="cm-vbadge">${I.play}</span>${m.duration ? `<span class="cm-vtime">${fmt(m.duration)}</span>` : ''}</button>`;
    })
    .join('');
  const grid = n ? `<div class="cm-grid ${layout} n${Math.min(n, 9)}${n === 4 ? ' four' : ''}">${cells}</div>` : '';
  const own = stickers.map((m) => `<img class="cm-gstk" src="${mediaUrl(m.id)}" alt="表情包" loading="lazy" decoding="async">`).join('');
  return `${own}${grid}${voices.map((v) => voiceHTML(v, { mine })).join('')}`;
}

// --- 播放：语音（同时只放一条）、大图 / 视频 --------------------------------------------

let player = null; // {audio, el}

function stopVoice() {
  if (!player) return;
  player.audio.pause();
  player.el?.classList.remove('playing');
  player.el?.style.setProperty('--p', '0');
  player = null;
}

function playVoice(el) {
  const same = player && player.el === el;
  stopVoice();
  if (same) return;
  const audio = new Audio(mediaUrl(el.dataset.voice));
  player = { audio, el };
  el.classList.add('playing', 'heard');
  audio.addEventListener('timeupdate', () => {
    const d = audio.duration && Number.isFinite(audio.duration) ? audio.duration : Number(el.dataset.dur) || 1;
    el.style.setProperty('--p', String(Math.min(1, audio.currentTime / d)));
  });
  audio.addEventListener('ended', () => player?.el === el && stopVoice());
  audio.addEventListener('error', () => player?.el === el && stopVoice());
  audio.play().catch(() => player?.el === el && stopVoice());
}

class Lightbox {
  constructor() {
    this.dlg = null;
    this.list = [];
    this.i = 0;
  }

  build() {
    const d = document.createElement('dialog');
    d.className = 'cm-lightbox';
    d.innerHTML = `<div class="cm-lb-stage" data-lb-stage></div>
      <button type="button" class="cm-lb-btn close" data-lb-close aria-label="关闭">${I.x}</button>
      <button type="button" class="cm-lb-btn prev" data-lb-prev aria-label="上一张">${I.left}</button>
      <button type="button" class="cm-lb-btn next" data-lb-next aria-label="下一张">${I.right}</button>
      <div class="cm-lb-dots" data-lb-dots></div>`;
    d.addEventListener('click', (e) => {
      if (e.target.closest('[data-lb-close]') || e.target === d || e.target.matches('[data-lb-stage]')) return this.close();
      if (e.target.closest('[data-lb-prev]')) return this.go(-1);
      if (e.target.closest('[data-lb-next]')) return this.go(1);
      const img = e.target.closest('.cm-lb-stage img');
      if (img) img.classList.toggle('zoom');
    });
    d.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft') this.go(-1);
      if (e.key === 'ArrowRight') this.go(1);
    });
    d.addEventListener('close', () => d.querySelector('video')?.pause());
    document.body.append(d);
    this.dlg = d;
  }

  open(list, i) {
    if (!this.dlg) this.build();
    stopVoice();
    this.list = list;
    this.i = i;
    this.render(0);
    if (!this.dlg.open) this.dlg.showModal();
  }

  close() {
    this.dlg?.close();
  }

  go(step) {
    if (this.list.length < 2) return;
    this.i = (this.i + step + this.list.length) % this.list.length;
    this.render(step);
  }

  render(step) {
    const m = this.list[this.i];
    const stage = this.dlg.querySelector('[data-lb-stage]');
    stage.querySelector('video')?.pause();
    stage.style.setProperty('--from', `${step * 40}px`);
    stage.innerHTML =
      m.kind === 'video'
        ? `<video src="${mediaUrl(m.id)}" controls autoplay playsinline class="cm-lb-item"></video>`
        : `<img src="${mediaUrl(m.id)}" alt="" class="cm-lb-item">`;
    const many = this.list.length > 1;
    this.dlg.querySelector('[data-lb-prev]').hidden = !many;
    this.dlg.querySelector('[data-lb-next]').hidden = !many;
    this.dlg.querySelector('[data-lb-dots]').innerHTML = many ? this.list.map((_, k) => `<i${k === this.i ? ' class="on"' : ''}></i>`).join('') : '';
  }
}

const lightbox = new Lightbox();

/** 在一块区域里接管：点语音播放、点图片 / 视频看大图。只需要调一次。 */
export function bindMedia(root) {
  root.addEventListener('click', (e) => {
    const v = e.target.closest('[data-voice]');
    if (v && root.contains(v)) {
      e.preventDefault();
      return playVoice(v);
    }
    const cell = e.target.closest('[data-view]');
    if (cell && root.contains(cell)) {
      e.preventDefault();
      const scope = cell.closest('.cm-grid')?.parentElement || root;
      const all = [...scope.querySelectorAll(`[data-view][data-group="${CSS.escape(cell.dataset.group)}"]`)];
      const list = all.map((c) => ({ id: c.dataset.view, kind: c.dataset.kind }));
      lightbox.open(list, Math.max(0, all.indexOf(cell)));
    }
  });
}

// --- 上传 ----------------------------------------------------------------------

/** 上传一个文件（带进度）。返回 {id, kind, mime, size}。 */
export function uploadMedia(blob, onProgress = () => {}) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', '/api/chat/media');
    x.setRequestHeader('Content-Type', blob.type || 'application/octet-stream');
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    x.onload = () => {
      let data = null;
      try {
        data = JSON.parse(x.responseText);
      } catch {
        data = null;
      }
      if (x.status >= 200 && x.status < 300) return resolve(data);
      const d = data && data.detail;
      reject(new ApiError(typeof d === 'string' ? d : `上传失败（${x.status}）`, x.status));
    };
    x.onerror = () => reject(new ApiError('上传失败：连不上服务', 0));
    x.send(blob);
  });
}

function measure(file, url) {
  const kind = KIND_OF(file.type);
  return new Promise((resolve) => {
    const done = (v) => resolve(v);
    setTimeout(() => done({}), 4000);
    if (kind === 'image') {
      const img = new Image();
      img.onload = () => done({ w: img.naturalWidth, h: img.naturalHeight });
      img.onerror = () => done({});
      img.src = url;
    } else if (kind === 'video') {
      const v = document.createElement('video');
      v.preload = 'metadata';
      v.muted = true;
      v.onloadedmetadata = () => done({ w: v.videoWidth, h: v.videoHeight, duration: Number.isFinite(v.duration) ? v.duration : undefined });
      v.onerror = () => done({});
      v.src = url;
    } else done({});
  });
}

// --- 编辑区：工具条、表情面板、待发附件、录音 -----------------------------------------------

let seq = 0;

/**
 * 挂在一个输入框旁边的富媒体工具。
 * opts.input：textarea / input；opts.bar：放工具条的元素；opts.tray：放待发附件的元素（可省）；
 * opts.kinds：允许的附件种类；opts.onSticker(token)：点表情包时（省略则插入输入框）；
 * opts.onVoice(item)：录完语音时（省略则放进待发附件）；opts.onChange()：待发附件变化时。
 */
export class MediaComposer {
  constructor({ input, bar, tray = null, kinds = ['image', 'video', 'voice'], max = 9, onSticker = null, onVoice = null, onChange = () => {}, onError = () => {}, compact = false, down = false }) {
    this.input = input;
    this.bar = bar;
    this.tray = tray;
    this.kinds = kinds;
    this.max = max;
    this.onSticker = onSticker;
    this.onVoice = onVoice;
    this.onChange = onChange;
    this.onError = onError;
    this.items = []; // {key, kind, url, id?, progress, error?, w?, h?, duration?}
    this.tab = recent().length ? 'recent' : 'face';
    this.rec = null;
    this.uid = `cm${++seq}`;
    this.build(compact, down);
  }

  build(compact, down) {
    const k = (x) => this.kinds.includes(x);
    this.bar.classList.add('cm-bar');
    if (compact) this.bar.classList.add('compact');
    if (down) this.bar.classList.add('down');
    this.bar.innerHTML = `
      <button type="button" class="cm-tool" data-cm="panel" aria-expanded="false" aria-controls="${this.uid}-panel" title="表情">${I.smile}<span>表情</span></button>
      ${k('image') ? `<button type="button" class="cm-tool" data-cm="image" title="图片">${I.image}<span>图片</span></button>` : ''}
      ${k('video') ? `<button type="button" class="cm-tool" data-cm="video" title="视频">${I.video}<span>视频</span></button>` : ''}
      ${k('voice') ? `<button type="button" class="cm-tool" data-cm="voice" title="语音：点一下开始录，最长 ${MAX_VOICE_S} 秒">${I.mic}<span>语音</span></button>` : ''}
      ${k('image') ? '<input type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden data-cm-file="image">' : ''}
      ${k('video') ? '<input type="file" accept="video/mp4,video/webm,video/quicktime" hidden data-cm-file="video">' : ''}
      <div class="cm-panel" id="${this.uid}-panel" role="dialog" aria-label="表情" hidden></div>
      <div class="cm-rec" hidden role="group" aria-label="正在录音"></div>`;
    this.panel = this.bar.querySelector('.cm-panel');
    this.recEl = this.bar.querySelector('.cm-rec');
    this.bar.addEventListener('click', (e) => this.onBarClick(e));
    this.bar.querySelectorAll('[data-cm-file]').forEach((f) =>
      f.addEventListener('change', () => {
        this.addFiles([...f.files]);
        f.value = '';
      }),
    );
    if (this.tray) {
      this.tray.classList.add('cm-tray');
      this.tray.addEventListener('click', (e) => {
        const rm = e.target.closest('[data-rm]');
        if (rm) this.remove(rm.dataset.rm);
      });
    }
    // 粘贴 / 拖进来的图片和视频
    this.input.addEventListener('paste', (e) => {
      const files = [...(e.clipboardData?.files || [])].filter((f) => this.accepts(f));
      if (files.length) {
        e.preventDefault();
        this.addFiles(files);
      }
    });
    const zone = this.input.closest('form') || this.input.parentElement;
    zone.addEventListener('dragover', (e) => {
      if ([...(e.dataTransfer?.items || [])].some((i) => i.kind === 'file')) {
        e.preventDefault();
        zone.classList.add('cm-drop');
      }
    });
    zone.addEventListener('dragleave', () => zone.classList.remove('cm-drop'));
    zone.addEventListener('drop', (e) => {
      zone.classList.remove('cm-drop');
      const files = [...(e.dataTransfer?.files || [])].filter((f) => this.accepts(f));
      if (files.length) {
        e.preventDefault();
        this.addFiles(files);
      }
    });
    this.outside = (e) => {
      if (!this.panel.hidden && !this.bar.contains(e.target)) this.togglePanel(false);
    };
    document.addEventListener('click', this.outside, true);
    this.bar.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !this.panel.hidden) {
        e.preventDefault();
        e.stopPropagation();
        this.togglePanel(false);
        this.input.focus({ preventScroll: true });
      }
    });
  }

  accepts(f) {
    const kind = KIND_OF(f.type || '');
    return kind && kind !== 'voice' && this.kinds.includes(kind);
  }

  onBarClick(e) {
    const t = e.target;
    const tool = t.closest('[data-cm]');
    if (tool) {
      const what = tool.dataset.cm;
      if (what === 'panel') return this.togglePanel();
      if (what === 'voice') return this.startRec();
      this.togglePanel(false);
      return this.bar.querySelector(`[data-cm-file="${what}"]`).click();
    }
    const tab = t.closest('[data-tab]');
    if (tab) {
      this.tab = tab.dataset.tab;
      return this.renderPanel();
    }
    const emo = t.closest('[data-emoji]');
    if (emo) {
      remember(`e:${emo.dataset.emoji}`);
      return this.insert(emo.dataset.emoji);
    }
    const stk = t.closest('[data-stk]');
    if (stk) {
      const [pack, name] = stk.dataset.stk.split('·');
      remember(`s:${pack}·${name}`);
      const tok = token(pack, name);
      if (this.onSticker) {
        this.togglePanel(false);
        return this.onSticker(tok);
      }
      return this.insert(tok);
    }
    if (t.closest('[data-rec-cancel]')) return this.stopRec(false);
    if (t.closest('[data-rec-done]')) return this.stopRec(true);
  }

  insert(text) {
    const el = this.input;
    const a = el.selectionStart ?? el.value.length;
    const b = el.selectionEnd ?? a;
    el.value = el.value.slice(0, a) + text + el.value.slice(b);
    const pos = a + text.length;
    el.focus({ preventScroll: true });
    el.setSelectionRange(pos, pos);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }

  togglePanel(force) {
    const open = force ?? this.panel.hidden;
    this.panel.hidden = !open;
    this.bar.querySelector('[data-cm="panel"]').setAttribute('aria-expanded', String(open));
    if (open) this.renderPanel();
  }

  renderPanel() {
    const tabs = [
      { id: 'recent', label: I.clock, title: '最近使用' },
      ...EMOJI.map((c) => ({ id: c.id, label: `<span class="cm-tabemo">${c.icon}</span>`, title: c.name })),
      ...STICKERS.map((p) => ({ id: `pack:${p.id}`, label: `<span class="cm-tabstk">${stickerSVG(p.id, p.items[0].name, { label: false })}</span>`, title: p.id, pack: true })),
    ];
    let body = '';
    let title = '';
    if (this.tab === 'recent') {
      title = '最近使用';
      const list = recent();
      body = list.length
        ? `<div class="cm-grid-emo mixed">${list
            .map((r) => {
              if (r.startsWith('e:')) return `<button type="button" class="cm-emo" data-emoji="${esc(r.slice(2))}">${esc(r.slice(2))}</button>`;
              const [pack, name] = r.slice(2).split('·');
              return findSticker(pack, name) ? `<button type="button" class="cm-stk sm" data-stk="${esc(`${pack}·${name}`)}" title="${esc(name)}">${stickerSVG(pack, name, { label: false })}</button>` : '';
            })
            .join('')}</div>`
        : '<p class="cm-empty">还没用过表情。挑一个试试吧。</p>';
    } else if (this.tab.startsWith('pack:')) {
      const pack = STICKERS.find((p) => `pack:${p.id}` === this.tab) || STICKERS[0];
      title = `${pack.id}${this.onSticker ? ' · 点一下直接发送' : ''}`;
      body = `<div class="cm-grid-stk">${pack.items
        .map((s) => `<button type="button" class="cm-stk" data-stk="${esc(`${pack.id}·${s.name}`)}" title="${esc(s.name)}">${stickerSVG(pack.id, s.name, { label: false })}<small>${esc(s.name)}</small></button>`)
        .join('')}</div>`;
    } else {
      const cat = EMOJI.find((c) => c.id === this.tab) || EMOJI[0];
      title = cat.name;
      body = `<div class="cm-grid-emo">${cat.list.map((e) => `<button type="button" class="cm-emo" data-emoji="${esc(e)}">${esc(e)}</button>`).join('')}</div>`;
    }
    this.panel.innerHTML = `<div class="cm-ptitle">${esc(title)}</div><div class="cm-pbody" data-tabkey="${esc(this.tab)}">${body}</div>
      <nav class="cm-tabs" aria-label="表情分类">${tabs
        .map((t) => `<button type="button" class="cm-tab${t.pack ? ' pack' : ''}${t.id === this.tab ? ' cur' : ''}" data-tab="${esc(t.id)}" title="${esc(t.title)}" aria-pressed="${t.id === this.tab}">${t.label}</button>`)
        .join('')}</nav>`;
  }

  // 附件

  async addFiles(files) {
    for (const f of files) {
      const kind = KIND_OF(f.type || '');
      if (!kind || !this.kinds.includes(kind)) {
        this.onError('只支持 png / jpg / gif / webp 图片和 mp4 / webm / mov 视频');
        continue;
      }
      if (this.items.length >= this.max) {
        this.onError(`一次最多 ${this.max} 个`);
        break;
      }
      this.addBlob(f, kind);
    }
  }

  async addBlob(blob, kind, extra = {}) {
    const item = { key: `${this.uid}-${++seq}`, kind, url: URL.createObjectURL(blob), progress: 0, ...extra };
    this.items.push(item);
    this.renderTray();
    this.onChange();
    Object.assign(item, await measure(blob, item.url), extra);
    try {
      const up = await uploadMedia(blob, (p) => {
        item.progress = p;
        this.paintProgress(item);
      });
      item.id = up.id;
      item.kind = up.kind;
      item.progress = 1;
    } catch (e) {
      item.error = e.message || '上传失败';
      this.onError(item.error);
    }
    this.renderTray();
    this.onChange();
    return item;
  }

  remove(key) {
    const i = this.items.findIndex((m) => m.key === key);
    if (i < 0) return;
    URL.revokeObjectURL(this.items[i].url);
    this.items.splice(i, 1);
    this.renderTray();
    this.onChange();
  }

  busy() {
    return this.items.some((m) => !m.id && !m.error);
  }

  /** 已上传好的附件（发消息时带上） */
  ready() {
    return this.items.filter((m) => m.id).map(({ id, kind, w, h, duration }) => ({ id, kind, ...(w ? { w } : {}), ...(h ? { h } : {}), ...(duration ? { duration } : {}) }));
  }

  clear() {
    for (const m of this.items) URL.revokeObjectURL(m.url);
    this.items = [];
    this.renderTray();
    this.togglePanel(false);
  }

  paintProgress(item) {
    const el = this.tray?.querySelector(`[data-key="${item.key}"]`);
    el?.style.setProperty('--up', String(item.progress));
  }

  renderTray() {
    if (!this.tray) return;
    this.tray.hidden = !this.items.length;
    this.tray.innerHTML = this.items
      .map((m) => {
        const state = m.error ? ' err' : m.id ? ' done' : ' up';
        const face =
          m.kind === 'image'
            ? `<img src="${m.url}" alt="">`
            : m.kind === 'video'
              ? `<video src="${m.url}#t=0.1" muted preload="metadata"></video><span class="cm-tbadge">${I.play}</span>`
              : `<span class="cm-tvoice">${I.mic}<b>${Math.max(1, Math.round(m.duration || 0))}″</b></span>`;
        return `<div class="cm-thumb${state}" data-key="${m.key}" style="--up:${m.progress || 0}" title="${esc(m.error || '')}">${face}<span class="cm-ring" aria-hidden="true"></span><button type="button" class="cm-rm" data-rm="${m.key}" aria-label="移除">${I.x}</button></div>`;
      })
      .join('');
  }

  // 录音

  async startRec() {
    if (this.rec) return;
    this.togglePanel(false);
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      this.onError('这个浏览器不支持录音');
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      this.onError('没有麦克风权限：在浏览器地址栏左边允许使用麦克风后再试');
      return;
    }
    const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'].find((t) => MediaRecorder.isTypeSupported?.(t)) || '';
    const rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
    const chunks = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    ctx.createMediaStreamSource(stream).connect(analyser);
    this.rec = { rec, stream, chunks, ctx, analyser, start: performance.now(), levels: [], keep: false };
    rec.start(250);
    this.recEl.hidden = false;
    this.bar.classList.add('recording');
    this.recEl.innerHTML = `<span class="cm-recdot"></span><span class="cm-rectime">0:00</span><span class="cm-reclv">${'<i></i>'.repeat(28)}</span>
      <span class="cm-rechint">正在录音 · 最长 ${MAX_VOICE_S} 秒</span>
      <button type="button" class="cm-recbtn" data-rec-cancel aria-label="取消">${I.trash}</button>
      <button type="button" class="cm-recbtn ok" data-rec-done aria-label="完成">${I.check}</button>`;
    const bars = [...this.recEl.querySelectorAll('.cm-reclv i')];
    const buf = new Uint8Array(analyser.frequencyBinCount);
    const tick = () => {
      if (!this.rec) return;
      analyser.getByteTimeDomainData(buf);
      let peak = 0;
      for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
      this.rec.levels.push(Math.min(1, peak / 64));
      const lv = this.rec.levels.slice(-bars.length);
      bars.forEach((b, i) => b.style.setProperty('--h', `${Math.round(12 + (lv[i - (bars.length - lv.length)] || 0) * 88)}%`));
      const s = (performance.now() - this.rec.start) / 1000;
      this.recEl.querySelector('.cm-rectime').textContent = fmt(s);
      if (s >= MAX_VOICE_S) return this.stopRec(true);
      this.rec.raf = requestAnimationFrame(tick);
    };
    this.rec.raf = requestAnimationFrame(tick);
  }

  stopRec(keep) {
    const r = this.rec;
    if (!r) return;
    this.rec = null;
    cancelAnimationFrame(r.raf);
    const duration = (performance.now() - r.start) / 1000;
    r.rec.onstop = async () => {
      r.stream.getTracks().forEach((t) => t.stop());
      r.ctx.close?.();
      if (!keep) return;
      if (duration < 0.8) return this.onError('说话时间太短');
      const blob = new Blob(r.chunks, { type: (r.rec.mimeType || 'audio/webm').split(';')[0] });
      if (this.onVoice) {
        const item = await this.addBlob(blob, 'voice', { duration });
        if (item.id) {
          this.remove(item.key);
          this.onVoice({ id: item.id, kind: 'voice', duration: Math.round(duration * 10) / 10 });
        }
      } else this.addBlob(blob, 'voice', { duration });
    };
    r.rec.stop();
    this.recEl.hidden = true;
    this.recEl.innerHTML = '';
    this.bar.classList.remove('recording');
  }

  destroy() {
    document.removeEventListener('click', this.outside, true);
    if (this.rec) this.stopRec(false);
  }
}
