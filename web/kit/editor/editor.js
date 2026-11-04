// 编辑器底座（docs/KIT.md「K2 编辑器底座」）：修图台、Live2D 台这类「以画布为中心」的应用都在它上面加功能。
// 自带：画布（缩放、平移、棋盘格底）、图层（显示 / 隐藏、排序、不透明度、改名、复制、删除）、
// 撤销重做（至少 20 步）、工具栏（移动、选区、画笔、橡皮）、属性面板、导入图片（按钮 / 拖进 / 粘贴）、
// 导出 PNG（原尺寸）、快捷键、出错提示、页内自检（selftest.js，K5 企业级验收）。
// 原生 ES 模块，不依赖任何库；颜色只用 --k-* 变量。功能（亮度对比度、裁剪、去背景……）用下面的接口加：
//   editor.addTool / addAction / addPanel / addTest / paint / applyFilter / crop / on

import { esc, icon, toast } from '../kit.js';

export const VERSION = 1;
export const UNDO_MIN = 20; // 撤销至少保证这么多步（内存预算超了也不少于它）

const KEYS_HELP = [
  ['Ctrl+Z', '撤销'], ['Ctrl+Shift+Z / Ctrl+Y', '重做'], ['Ctrl+O', '打开图片'], ['Ctrl+S', '导出 PNG'],
  ['Ctrl+J', '复制图层'], ['Delete', '删除图层'], ['V / M / B / E', '移动 / 选区 / 画笔 / 橡皮'],
  ['[ ]', '画笔大小'], ['+ / -', '放大 / 缩小'], ['0', '适应窗口'], ['Esc', '取消选区'],
];

function newCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.round(w));
  c.height = Math.max(1, Math.round(h));
  return c;
}

function cloneCanvas(src) {
  const c = newCanvas(src.width, src.height);
  c.getContext('2d').drawImage(src, 0, 0);
  return c;
}

function cssVar(el, name, fallback) {
  const v = getComputedStyle(el).getPropertyValue(name).trim();
  return v || fallback;
}

/** 把快捷键写法（'mod+shift+z'）和按键事件对上。mod = Ctrl（苹果电脑上是 ⌘）。 */
function matchKey(spec, ev) {
  const parts = spec.toLowerCase().split('+');
  const key = parts.pop();
  const want = { mod: parts.includes('mod'), shift: parts.includes('shift'), alt: parts.includes('alt') };
  const mod = ev.ctrlKey || ev.metaKey;
  if (want.mod !== mod || want.alt !== ev.altKey) return false;
  const k = (ev.key || '').toLowerCase();
  // 符号键（[ ] + -）按下 shift 时 key 会变，不比较 shift
  if (/^[a-z0-9]$/.test(key) || key.length > 1) {
    if (want.shift !== ev.shiftKey) return false;
  }
  return k === key || (key === 'delete' && k === 'backspace');
}

/**
 * createEditor(root, { title?, icon?, logo?（HTML）, undoLimit?, memoryMB?, maxPixels?, fileName? }) → editor
 * root：空的容器（应用外壳里是整页）。返回的 editor 也挂在 window.__editor 上（自检和端到端测试用）。
 */
export function createEditor(root, opts = {}) {
  const undoLimit = Math.max(UNDO_MIN, opts.undoLimit || 60);
  const memoryBytes = (opts.memoryMB || 1200) * 1024 * 1024;
  const maxPixels = opts.maxPixels || 64e6; // 8000 × 8000
  const fileName = opts.fileName || 'image';
  const doc = { width: 0, height: 0, layers: [], active: null, selection: null };
  const past = [];
  const future = [];
  const listeners = new Map();
  const tools = new Map();
  const actions = [];
  const tests = [];
  const shortcuts = [];
  const view = { zoom: 0, panX: 0, panY: 0 }; // zoom 0 = 适应窗口
  const brush = { size: 24, color: '' };
  let seq = 0;
  let tool = 'move';
  let frame = 0;
  let alertTimer = 0;

  root.classList.add('k-ie');
  root.innerHTML = `
    <header class="k-ie-bar">
      ${opts.title ? `<div class="k-ie-brand">${opts.logo || `<span class="k-logo">${icon(opts.icon || 'image', { size: 18 })}</span>`}<h1>${esc(opts.title)}</h1></div>` : ''}
      <div class="k-ie-group">
        <button type="button" class="k-btn k-primary k-sm" data-cmd="open">${icon('image-plus', { size: 16 })}<span>打开图片</span></button>
        <button type="button" class="k-btn k-sm" data-cmd="export">${icon('download', { size: 16 })}<span>导出 PNG</span></button>
      </div>
      <div class="k-ie-group" role="group" aria-label="撤销重做">
        <button type="button" class="k-btn k-ghost k-icon k-sm" data-cmd="undo" aria-label="撤销（Ctrl+Z）" title="撤销（Ctrl+Z）">${icon('undo-2', { size: 16 })}</button>
        <button type="button" class="k-btn k-ghost k-icon k-sm" data-cmd="redo" aria-label="重做（Ctrl+Shift+Z）" title="重做（Ctrl+Shift+Z）">${icon('redo-2', { size: 16 })}</button>
      </div>
      <div class="k-ie-group k-ie-actions" role="group" aria-label="功能"></div>
      <div class="k-ie-group k-ie-zoom" role="group" aria-label="缩放">
        <button type="button" class="k-btn k-ghost k-icon k-sm" data-cmd="zoom-out" aria-label="缩小">${icon('zoom-out', { size: 16 })}</button>
        <span class="k-ie-pct k-num" aria-live="polite">100%</span>
        <button type="button" class="k-btn k-ghost k-icon k-sm" data-cmd="zoom-in" aria-label="放大">${icon('zoom-in', { size: 16 })}</button>
        <button type="button" class="k-btn k-ghost k-icon k-sm" data-cmd="fit" aria-label="适应窗口（0）">${icon('scan', { size: 16 })}</button>
        <button type="button" class="k-btn k-ghost k-icon k-sm" data-cmd="keys" aria-label="快捷键" aria-expanded="false">${icon('info', { size: 16 })}</button>
      </div>
    </header>
    <div class="k-ie-alert" role="alert" hidden></div>
    <div class="k-ie-main">
      <nav class="k-ie-tools" role="toolbar" aria-label="工具" data-k-scroll></nav>
      <div class="k-ie-stage">
        <canvas class="k-ie-view" role="img" aria-label="画布"></canvas>
        <div class="k-ie-drop">
          <div class="k-empty">
            <div class="k-ico-wrap">${icon('image', { size: 22 })}</div>
            <strong>把图片拖到这里</strong>
            <p>支持 PNG、JPG、WebP；也可以直接粘贴</p>
            <button type="button" class="k-btn k-primary" data-cmd="open">${icon('upload', { size: 16 })}<span>选择图片</span></button>
          </div>
        </div>
        <div class="k-ie-busy" hidden><span class="k-ie-spin">${icon('loader-circle', { size: 18 })}</span><span class="k-ie-busy-t"></span></div>
      </div>
      <aside class="k-ie-side" aria-label="面板">
        <section class="k-ie-panel" aria-labelledby="k-ie-layers-t">
          <div class="k-ie-ph"><h2 id="k-ie-layers-t">图层</h2>
            <div class="k-row">
              <button type="button" class="k-btn k-ghost k-icon k-sm" data-cmd="add-layer" aria-label="新建空白图层">${icon('plus', { size: 16 })}</button>
              <button type="button" class="k-btn k-ghost k-icon k-sm" data-cmd="dup-layer" aria-label="复制图层（Ctrl+J）">${icon('copy', { size: 16 })}</button>
              <button type="button" class="k-btn k-ghost k-icon k-sm" data-cmd="del-layer" aria-label="删除图层（Delete）">${icon('trash-2', { size: 16 })}</button>
            </div>
          </div>
          <ul class="k-ie-layers" aria-label="图层列表"></ul>
        </section>
        <section class="k-ie-panel k-ie-props" aria-labelledby="k-ie-props-t">
          <div class="k-ie-ph"><h2 id="k-ie-props-t">属性</h2></div>
          <div class="k-ie-props-b"></div>
        </section>
        <div class="k-ie-panels"></div>
      </aside>
    </div>
    <footer class="k-ie-status k-num" aria-live="polite"></footer>
    <div class="k-ie-keys" hidden>${KEYS_HELP.map(([k, v]) => `<div><kbd>${esc(k)}</kbd><span>${esc(v)}</span></div>`).join('')}</div>
    <input type="file" accept="image/*" hidden aria-label="选择图片文件">`;

  const $ = (s) => root.querySelector(s);
  const stage = $('.k-ie-stage');
  const cvs = $('.k-ie-view');
  const g = cvs.getContext('2d');
  const fileInput = $('input[type=file]');
  const alertBox = $('.k-ie-alert');
  const layerList = $('.k-ie-layers');
  const propsBox = $('.k-ie-props-b');
  const panelsBox = $('.k-ie-panels');
  const toolsBox = $('.k-ie-tools');
  const actionsBox = $('.k-ie-actions');

  // ---------------- 事件 ----------------
  function on(name, fn) {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(fn);
    return () => listeners.get(name).delete(fn);
  }
  function emit(name, detail) {
    for (const fn of listeners.get(name) || []) {
      try { fn(detail); } catch (err) { console.error(`编辑器事件 ${name} 的处理出错：`, err); }
    }
  }

  // ---------------- 提示 ----------------
  function notify(text, tone = 'good') {
    if (tone === 'bad') {
      alertBox.hidden = false;
      alertBox.innerHTML = `${icon('triangle-alert', { size: 16 })}<span>${esc(text)}</span><button type="button" class="k-btn k-ghost k-icon k-sm" data-cmd="close-alert" aria-label="关闭提示">${icon('x', { size: 15 })}</button>`;
      clearTimeout(alertTimer);
      alertTimer = setTimeout(() => { alertBox.hidden = true; }, 8000);
    }
    toast(text, { icon: tone === 'bad' ? 'triangle-alert' : 'circle-check' });
  }
  async function busy(text, work) {
    const box = $('.k-ie-busy');
    box.hidden = false;
    $('.k-ie-busy-t').textContent = text;
    await new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0))); // 先让提示画出来
    try { return await work(); } finally { box.hidden = true; }
  }

  // ---------------- 撤销重做 ----------------
  function used() { return past.reduce((s, x) => s + (x.bytes || 0), 0); }
  function trim() {
    while (past.length > undoLimit) past.shift();
    while (past.length > UNDO_MIN && used() > memoryBytes) past.shift();
  }
  /** 记一步可撤销的修改：apply() 立即执行（done: true 表示已经执行过），revert() 撤销。 */
  function edit(label, apply, revert, { bytes = 0, done = false } = {}) {
    if (!done) apply();
    past.push({ label, apply, revert, bytes });
    future.length = 0;
    trim();
    changed();
  }
  function undo() {
    const step = past.pop();
    if (!step) return false;
    step.revert();
    future.push(step);
    changed();
    return true;
  }
  function redo() {
    const step = future.pop();
    if (!step) return false;
    step.apply();
    past.push(step);
    changed();
    return true;
  }

  // ---------------- 图层 ----------------
  const layerById = (id) => doc.layers.find((l) => l.id === id) || null;
  const activeLayer = () => layerById(doc.active);
  function select(id) {
    if (doc.active === id) return;
    doc.active = id;
    renderPanels();
    emit('select', activeLayer());
  }
  function addLayer({ name, canvas, x = 0, y = 0, opacity = 1, visible = true, index } = {}) {
    if (!doc.width) throw new Error('先打开一张图片');
    const layer = { id: `l${++seq}`, name: name || `图层 ${seq}`, canvas: canvas || newCanvas(doc.width, doc.height), x, y, opacity, visible };
    const at = index ?? doc.layers.length;
    const prev = doc.active;
    edit(`新建「${layer.name}」`, () => { doc.layers.splice(at, 0, layer); doc.active = layer.id; }, () => {
      doc.layers.splice(doc.layers.indexOf(layer), 1);
      doc.active = prev;
    }, { bytes: canvas ? 0 : layer.canvas.width * layer.canvas.height * 4 });
    return layer;
  }
  function removeLayer(id = doc.active) {
    const layer = layerById(id);
    if (!layer) return false;
    const at = doc.layers.indexOf(layer);
    edit(`删除「${layer.name}」`, () => {
      doc.layers.splice(at, 1);
      if (doc.active === id) doc.active = (doc.layers[Math.min(at, doc.layers.length - 1)] || {}).id || null;
    }, () => { doc.layers.splice(at, 0, layer); doc.active = id; });
    return true;
  }
  function duplicateLayer(id = doc.active) {
    const src = layerById(id);
    if (!src) return null;
    return addLayer({ name: `${src.name} 副本`, canvas: cloneCanvas(src.canvas), x: src.x, y: src.y, opacity: src.opacity, index: doc.layers.indexOf(src) + 1 });
  }
  function moveLayer(id, delta) {
    const layer = layerById(id);
    if (!layer) return false;
    const from = doc.layers.indexOf(layer);
    const to = Math.max(0, Math.min(doc.layers.length - 1, from + delta));
    if (to === from) return false;
    const swap = (a, b) => () => { doc.layers.splice(a, 1); doc.layers.splice(b, 0, layer); };
    edit(delta > 0 ? `上移「${layer.name}」` : `下移「${layer.name}」`, swap(from, to), swap(to, from));
    return true;
  }
  /** 改图层属性（visible / opacity / name / x / y），可撤销。 */
  function setLayer(id, patch, { label } = {}) {
    const layer = layerById(id);
    if (!layer) return false;
    const before = {};
    const after = {};
    for (const [k, v] of Object.entries(patch)) {
      if (!['visible', 'opacity', 'name', 'x', 'y'].includes(k) || layer[k] === v) continue;
      before[k] = layer[k];
      after[k] = k === 'opacity' ? Math.max(0, Math.min(1, Number(v))) : v;
    }
    if (!Object.keys(after).length) return false;
    edit(label || `改「${layer.name}」`, () => Object.assign(layer, after), () => Object.assign(layer, before));
    return true;
  }

  // ---------------- 像素操作 ----------------
  /** 在图层的一份拷贝上画（fn(ctx, canvas, layer)），画完换上去；撤销时换回原来那份。 */
  function paint(label, fn, { layer: id = doc.active } = {}) {
    const layer = layerById(id);
    if (!layer) throw new Error('先选一个图层');
    const before = layer.canvas;
    const after = cloneCanvas(before);
    const ctx = after.getContext('2d');
    ctx.save();
    fn(ctx, after, layer, before);
    ctx.restore();
    edit(label, () => { layer.canvas = after; }, () => { layer.canvas = before; }, { bytes: after.width * after.height * 4 });
    return layer;
  }
  /** 用 CSS 滤镜处理当前图层（如 'brightness(120%) contrast(110%)'），可撤销。 */
  function applyFilter(label, filter, opt) {
    return paint(label, (ctx, c, layer, before) => {
      ctx.clearRect(0, 0, c.width, c.height);
      ctx.filter = filter;
      ctx.drawImage(before, 0, 0);
    }, opt);
  }
  /** 把整张画布裁成 rect（文档坐标），所有图层一起裁，可撤销。 */
  function crop(rect = doc.selection) {
    if (!rect || !doc.width) throw new Error('先用选区工具框出要保留的范围');
    const r = {
      x: Math.max(0, Math.round(rect.x)), y: Math.max(0, Math.round(rect.y)),
    };
    r.w = Math.min(doc.width - r.x, Math.round(rect.w));
    r.h = Math.min(doc.height - r.y, Math.round(rect.h));
    if (r.w < 1 || r.h < 1) throw new Error('选区太小了');
    const old = { width: doc.width, height: doc.height, selection: doc.selection, layers: doc.layers.map((l) => ({ l, canvas: l.canvas, x: l.x, y: l.y })) };
    const next = doc.layers.map((l) => {
      const c = newCanvas(r.w, r.h);
      c.getContext('2d').drawImage(l.canvas, l.x - r.x, l.y - r.y);
      return { l, canvas: c };
    });
    edit('裁剪', () => {
      doc.width = r.w; doc.height = r.h; doc.selection = null;
      for (const n of next) { n.l.canvas = n.canvas; n.l.x = 0; n.l.y = 0; }
    }, () => {
      doc.width = old.width; doc.height = old.height; doc.selection = old.selection;
      for (const o of old.layers) { o.l.canvas = o.canvas; o.l.x = o.x; o.l.y = o.y; }
    }, { bytes: r.w * r.h * 4 * next.length });
    view.zoom = 0;
    return r;
  }
  function setSelection(rect) {
    doc.selection = rect && rect.w >= 1 && rect.h >= 1 ? rect : null;
    invalidate();
    emit('selection', doc.selection);
  }

  // ---------------- 导入导出 ----------------
  async function decode(blob) {
    if (!blob || !blob.size) throw new Error('文件是空的');
    if (blob.type && !blob.type.startsWith('image/')) throw new Error('这不是图片文件（只支持 PNG、JPG、WebP、GIF）');
    try {
      return await createImageBitmap(blob);
    } catch {
      throw new Error('这张图片解不开，文件可能损坏或格式不支持');
    }
  }
  /** 导入一张图：第一张定下画布尺寸，之后的作为新图层。失败时提示原因并返回 null（不抛错）。 */
  async function importFile(file, { name } = {}) {
    try {
      return await busy('正在打开图片…', async () => {
        const bmp = await decode(file);
        if (bmp.width * bmp.height > maxPixels) {
          bmp.close?.();
          throw new Error(`图片太大了（${bmp.width}×${bmp.height}），最多 ${Math.round(maxPixels / 1e4)} 万像素`);
        }
        const c = newCanvas(bmp.width, bmp.height);
        c.getContext('2d').drawImage(bmp, 0, 0);
        bmp.close?.();
        const label = name || (file && file.name ? file.name.replace(/\.[^.]+$/, '') : '图片');
        if (!doc.width) {
          const prev = { width: doc.width, height: doc.height };
          const layer = { id: `l${++seq}`, name: label, canvas: c, x: 0, y: 0, opacity: 1, visible: true };
          edit(`打开「${label}」`, () => { doc.width = c.width; doc.height = c.height; doc.layers.push(layer); doc.active = layer.id; }, () => {
            doc.layers.splice(doc.layers.indexOf(layer), 1); doc.width = prev.width; doc.height = prev.height; doc.active = null;
          });
          view.zoom = 0;
          return layer;
        }
        return addLayer({ name: label, canvas: c });
      });
    } catch (err) {
      notify(`打开失败：${err.message || err}`, 'bad');
      emit('error', err);
      return null;
    }
  }
  async function open(url, opt) {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`读取失败（${res.status}）`);
      return await importFile(await res.blob(), opt);
    } catch (err) {
      notify(`打开失败：${err.message || err}`, 'bad');
      return null;
    }
  }
  /** 合成整张图（原尺寸，只算看得见的图层）。 */
  function exportCanvas() {
    const out = newCanvas(doc.width, doc.height);
    const ctx = out.getContext('2d');
    for (const l of doc.layers) {
      if (!l.visible || l.opacity <= 0) continue;
      ctx.globalAlpha = l.opacity;
      ctx.drawImage(l.canvas, l.x, l.y);
    }
    return out;
  }
  function exportBlob(type = 'image/png') {
    if (!doc.width) return Promise.reject(new Error('还没有图片'));
    return new Promise((done, fail) => exportCanvas().toBlob((b) => (b ? done(b) : fail(new Error('导出失败'))), type));
  }
  async function download(name = fileName) {
    if (!doc.width) { notify('还没有图片，先打开一张', 'bad'); return false; }
    try {
      const blob = await busy('正在导出…', () => exportBlob());
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${name}.png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      notify(`已导出 ${doc.width}×${doc.height} 的 PNG`);
      return true;
    } catch (err) {
      notify(`导出失败：${err.message || err}`, 'bad');
      return false;
    }
  }
  function reset() {
    doc.width = 0; doc.height = 0; doc.layers.length = 0; doc.active = null; doc.selection = null;
    past.length = 0; future.length = 0; view.zoom = 0;
    changed();
  }

  // ---------------- 画布显示 ----------------
  function metrics() {
    const w = cvs.clientWidth;
    const h = cvs.clientHeight;
    const fit = doc.width ? Math.min((w - 32) / doc.width, (h - 32) / doc.height, 4) : 1;
    const s = view.zoom || Math.max(fit, 0.01);
    return { w, h, s, ox: (w - doc.width * s) / 2 + view.panX, oy: (h - doc.height * s) / 2 + view.panY };
  }
  function invalidate() {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; draw(); });
  }
  function draw() {
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(cvs.clientWidth * dpr));
    const h = Math.max(1, Math.round(cvs.clientHeight * dpr));
    if (cvs.width !== w || cvs.height !== h) { cvs.width = w; cvs.height = h; }
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    stage.classList.toggle('k-ie-empty', !doc.width);
    if (!doc.width) return;
    const { s, ox, oy } = metrics();
    // 棋盘格：透明的地方看得出来
    const a = cssVar(root, '--k-surface', 'white');
    const b = cssVar(root, '--k-surface-3', 'lightgray');
    g.fillStyle = a;
    g.fillRect(ox, oy, doc.width * s, doc.height * s);
    g.save();
    g.beginPath(); g.rect(ox, oy, doc.width * s, doc.height * s); g.clip();
    g.fillStyle = b;
    const cell = 10;
    for (let y = 0; y * cell < doc.height * s; y++) {
      for (let x = (y % 2); x * cell < doc.width * s; x += 2) g.fillRect(ox + x * cell, oy + y * cell, cell, cell);
    }
    g.imageSmoothingQuality = 'high';
    for (const l of doc.layers) {
      if (!l.visible || l.opacity <= 0) continue;
      g.globalAlpha = l.opacity;
      g.drawImage(l.canvas, ox + l.x * s, oy + l.y * s, l.canvas.width * s, l.canvas.height * s);
    }
    g.restore();
    g.globalAlpha = 1;
    g.strokeStyle = cssVar(root, '--k-line-strong', 'gray');
    g.lineWidth = 1;
    g.strokeRect(ox - 0.5, oy - 0.5, doc.width * s + 1, doc.height * s + 1);
    const sel = doc.selection;
    if (sel) {
      g.save();
      g.strokeStyle = cssVar(root, '--k-accent', 'blue');
      g.setLineDash([6, 4]);
      g.lineWidth = 1.5;
      g.strokeRect(ox + sel.x * s, oy + sel.y * s, sel.w * s, sel.h * s);
      g.restore();
    }
  }

  // ---------------- 面板 ----------------
  function renderPanels() {
    const top = [...doc.layers].reverse();
    layerList.innerHTML = top.length
      ? top.map((l) => `<li class="k-ie-layer${l.id === doc.active ? ' k-on' : ''}" data-id="${l.id}">
          <button type="button" class="k-btn k-ghost k-icon k-sm" data-act="vis" aria-pressed="${l.visible}" aria-label="${l.visible ? '隐藏' : '显示'}「${esc(l.name)}」">${icon(l.visible ? 'eye' : 'eye-off', { size: 15 })}</button>
          <button type="button" class="k-ie-lname" data-act="pick" aria-pressed="${l.id === doc.active}">${esc(l.name)}<span class="k-muted k-num">${Math.round(l.opacity * 100)}%</span></button>
          <button type="button" class="k-btn k-ghost k-icon k-sm" data-act="up" aria-label="上移「${esc(l.name)}」">${icon('arrow-up', { size: 15 })}</button>
          <button type="button" class="k-btn k-ghost k-icon k-sm" data-act="down" aria-label="下移「${esc(l.name)}」">${icon('arrow-down', { size: 15 })}</button>
        </li>`).join('')
      : `<li class="k-ie-none k-muted">还没有图层</li>`;
    const l = activeLayer();
    const toolOpt = tools.get(tool)?.options;
    propsBox.innerHTML = (l
      ? `<label class="k-field">名称<input class="k-input" data-prop="name" value="${esc(l.name)}" maxlength="40"></label>
        <label class="k-field">不透明度 <span class="k-num" data-out="opacity">${Math.round(l.opacity * 100)}%</span><input type="range" class="k-range" data-prop="opacity" min="0" max="100" value="${Math.round(l.opacity * 100)}"></label>
        <div class="k-ie-xy"><label class="k-field">X<input class="k-input k-num" type="number" data-prop="x" value="${Math.round(l.x)}"></label><label class="k-field">Y<input class="k-input k-num" type="number" data-prop="y" value="${Math.round(l.y)}"></label></div>`
      : `<p class="k-muted">选中一个图层后在这里调整</p>`)
      + (toolOpt ? `<div class="k-ie-topt" data-tool-opt="${esc(tool)}"></div>` : '');
    if (toolOpt) toolOpt(propsBox.querySelector('.k-ie-topt'));
    const has = !!doc.width;
    for (const b of root.querySelectorAll('[data-cmd="dup-layer"],[data-cmd="del-layer"],[data-cmd="add-layer"]')) b.disabled = !has || (b.dataset.cmd !== 'add-layer' && !l);
    $('[data-cmd="undo"]').disabled = !past.length;
    $('[data-cmd="redo"]').disabled = !future.length;
    $('[data-cmd="export"]').disabled = !has;
    const { s } = metrics();
    $('.k-ie-pct').textContent = `${Math.round(s * 100)}%`;
    $('.k-ie-status').textContent = has
      ? `${doc.width} × ${doc.height} 像素 · ${doc.layers.length} 个图层 · 可撤销 ${past.length} 步${doc.selection ? ` · 选区 ${Math.round(doc.selection.w)} × ${Math.round(doc.selection.h)}` : ''}`
      : '还没有图片';
  }
  function changed() {
    renderPanels();
    invalidate();
    emit('change', { width: doc.width, height: doc.height, layers: doc.layers.length });
  }

  layerList.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-act]');
    const li = ev.target.closest('[data-id]');
    if (!btn || !li) return;
    const id = li.dataset.id;
    const l = layerById(id);
    if (btn.dataset.act === 'vis') setLayer(id, { visible: !l.visible }, { label: l.visible ? `隐藏「${l.name}」` : `显示「${l.name}」` });
    else if (btn.dataset.act === 'up') moveLayer(id, 1);
    else if (btn.dataset.act === 'down') moveLayer(id, -1);
    else select(id);
  });
  propsBox.addEventListener('input', (ev) => {
    const el = ev.target.closest('[data-prop="opacity"]');
    const l = activeLayer();
    if (!el || !l) return;
    l.opacity = Number(el.value) / 100; // 拖动时只预览，松手（change）才记一步
    propsBox.querySelector('[data-out="opacity"]').textContent = `${el.value}%`;
    invalidate();
  });
  propsBox.addEventListener('pointerdown', (ev) => {
    const el = ev.target.closest('[data-prop="opacity"]');
    if (el && activeLayer()) el.dataset.start = String(activeLayer().opacity);
  });
  propsBox.addEventListener('change', (ev) => {
    const el = ev.target.closest('[data-prop]');
    const l = activeLayer();
    if (!el || !l) return;
    const k = el.dataset.prop;
    if (k === 'opacity') {
      const start = el.dataset.start != null ? Number(el.dataset.start) : null;
      const to = Number(el.value) / 100;
      if (start != null && start !== to) { l.opacity = start; setLayer(l.id, { opacity: to }, { label: `不透明度 ${el.value}%` }); }
      else if (start == null) { setLayer(l.id, { opacity: to }); }
      delete el.dataset.start;
    } else if (k === 'name') {
      const v = el.value.trim();
      if (v) setLayer(l.id, { name: v }); else el.value = l.name;
    } else {
      const v = Number(el.value);
      if (Number.isFinite(v)) setLayer(l.id, { [k]: v });
    }
  });

  // ---------------- 工具 ----------------
  function renderTools() {
    toolsBox.innerHTML = [...tools.values()].map((t) => `<button type="button" class="k-btn k-ghost k-icon" data-tool="${esc(t.id)}" aria-pressed="${t.id === tool}" aria-label="${esc(t.label)}${t.key ? `（${t.key.toUpperCase()}）` : ''}" title="${esc(t.label)}${t.key ? `（${t.key.toUpperCase()}）` : ''}">${icon(t.icon || 'pencil', { size: 18 })}</button>`).join('');
  }
  function setTool(id) {
    if (!tools.has(id)) return;
    tool = id;
    renderTools();
    renderPanels();
    stage.dataset.tool = id;
    emit('tool', id);
  }
  /** 加一个工具：{ id, label, icon, key?, down?(p, ev), move?(p, ev), up?(p, ev), options?(el) }；p 是文档坐标。 */
  function addTool(t) {
    if (!t || !t.id || !t.label) throw new Error('工具要有 id 和 label');
    tools.set(t.id, t);
    if (t.key) shortcuts.push({ key: t.key, run: () => setTool(t.id), label: t.label });
    renderTools();
    return () => { tools.delete(t.id); renderTools(); };
  }
  toolsBox.addEventListener('click', (ev) => { const b = ev.target.closest('[data-tool]'); if (b) setTool(b.dataset.tool); });

  function docPoint(ev) {
    const r = cvs.getBoundingClientRect();
    const { s, ox, oy } = metrics();
    return { x: (ev.clientX - r.left - ox) / s, y: (ev.clientY - r.top - oy) / s };
  }
  let drag = null;
  let spaceDown = false;
  cvs.addEventListener('pointerdown', (ev) => {
    if (!doc.width) return;
    cvs.setPointerCapture?.(ev.pointerId);
    if (ev.button === 1 || spaceDown) { drag = { pan: true, x: ev.clientX, y: ev.clientY, px: view.panX, py: view.panY }; return; }
    const t = tools.get(tool);
    drag = { tool: t };
    try { t?.down?.(docPoint(ev), ev); } catch (err) { drag = null; notify(err.message || String(err), 'bad'); }
  });
  cvs.addEventListener('pointermove', (ev) => {
    if (!drag) return;
    if (drag.pan) { view.panX = drag.px + ev.clientX - drag.x; view.panY = drag.py + ev.clientY - drag.y; invalidate(); return; }
    try { drag.tool?.move?.(docPoint(ev), ev); } catch (err) { console.error(err); }
  });
  const endDrag = (ev) => {
    if (!drag) return;
    const d = drag;
    drag = null;
    if (!d.pan) { try { d.tool?.up?.(docPoint(ev), ev); } catch (err) { notify(err.message || String(err), 'bad'); } }
  };
  cvs.addEventListener('pointerup', endDrag);
  cvs.addEventListener('pointercancel', endDrag);
  cvs.addEventListener('wheel', (ev) => {
    if (!doc.width) return;
    ev.preventDefault();
    if (ev.ctrlKey || ev.metaKey) zoomBy(ev.deltaY < 0 ? 1.15 : 1 / 1.15);
    else { view.panX -= ev.deltaX; view.panY -= ev.deltaY; invalidate(); }
  }, { passive: false });

  function zoomBy(f) {
    const { s } = metrics();
    view.zoom = Math.max(0.02, Math.min(16, s * f));
    renderPanels();
    invalidate();
  }
  function fit() { view.zoom = 0; view.panX = 0; view.panY = 0; renderPanels(); invalidate(); }

  // 自带工具：移动、选区、画笔、橡皮
  {
    let from = null; // 拖动时直接改位置预览，松手才记一步
    addTool({
      id: 'move', label: '移动图层', icon: 'move', key: 'v',
      down(p) { const l = activeLayer(); if (!l) throw new Error('先选一个图层'); from = { p, x: l.x, y: l.y }; },
      move(p) { const l = activeLayer(); if (!l || !from) return; l.x = Math.round(from.x + p.x - from.p.x); l.y = Math.round(from.y + p.y - from.p.y); invalidate(); },
      up() {
        const l = activeLayer();
        if (!l || !from) return;
        const to = { x: l.x, y: l.y };
        const back = { x: from.x, y: from.y };
        from = null;
        if (to.x === back.x && to.y === back.y) return;
        edit(`移动「${l.name}」`, () => Object.assign(l, to), () => Object.assign(l, back), { done: true });
      },
    });
  }
  {
    let start = null;
    addTool({
      id: 'select', label: '矩形选区', icon: 'square-dashed', key: 'm',
      down(p) { start = p; setSelection(null); },
      move(p) { if (!start) return; setSelection(rectOf(start, p)); renderPanels(); },
      up(p) { if (start) setSelection(rectOf(start, p)); start = null; renderPanels(); },
    });
  }
  function rectOf(a, b) {
    const x = Math.max(0, Math.min(a.x, b.x));
    const y = Math.max(0, Math.min(a.y, b.y));
    return { x, y, w: Math.min(doc.width, Math.max(a.x, b.x)) - x, h: Math.min(doc.height, Math.max(a.y, b.y)) - y };
  }
  for (const kind of ['brush', 'eraser']) {
    let stroke = null;
    addTool({
      id: kind, label: kind === 'brush' ? '画笔' : '橡皮', icon: kind, key: kind === 'brush' ? 'b' : 'e',
      down(p) {
        const l = activeLayer();
        if (!l) throw new Error('先选一个图层');
        if (!l.visible) throw new Error('这个图层隐藏着，先显示出来再画');
        const before = l.canvas;
        const work = cloneCanvas(before);
        l.canvas = work;
        const ctx = work.getContext('2d');
        ctx.lineCap = 'round'; ctx.lineJoin = 'round';
        ctx.lineWidth = brush.size;
        ctx.strokeStyle = brush.color || cssVar(root, '--k-accent', 'black');
        ctx.globalCompositeOperation = kind === 'eraser' ? 'destination-out' : 'source-over';
        stroke = { l, before, work, ctx, last: { x: p.x - l.x, y: p.y - l.y } };
        ctx.beginPath(); ctx.moveTo(stroke.last.x, stroke.last.y); ctx.lineTo(stroke.last.x + 0.01, stroke.last.y); ctx.stroke();
        invalidate();
      },
      move(p) {
        if (!stroke) return;
        const q = { x: p.x - stroke.l.x, y: p.y - stroke.l.y };
        stroke.ctx.beginPath(); stroke.ctx.moveTo(stroke.last.x, stroke.last.y); stroke.ctx.lineTo(q.x, q.y); stroke.ctx.stroke();
        stroke.last = q;
        invalidate();
      },
      up() {
        if (!stroke) return;
        const { l, before, work } = stroke;
        stroke = null;
        edit(kind === 'brush' ? '画笔' : '橡皮', () => { l.canvas = work; }, () => { l.canvas = before; }, { done: true, bytes: work.width * work.height * 4 });
      },
      options(el) {
        el.innerHTML = `<label class="k-field">笔刷大小 <span class="k-num" data-out="size">${brush.size}px</span><input type="range" class="k-range" data-opt="size" min="1" max="200" value="${brush.size}"></label>`
          + (kind === 'brush' ? `<label class="k-field">颜色<input type="color" class="k-ie-color" data-opt="color" value="${toHex(brush.color || cssVar(root, '--k-accent', '#000000'))}" aria-label="画笔颜色"></label>` : '');
        el.addEventListener('input', (ev) => {
          const o = ev.target.dataset.opt;
          if (o === 'size') { brush.size = Number(ev.target.value); el.querySelector('[data-out="size"]').textContent = `${brush.size}px`; }
          if (o === 'color') brush.color = ev.target.value;
        });
      },
    });
  }
  function toHex(c) {
    if (/^#[0-9a-f]{6}$/i.test(c)) return c;
    const m = c.match(/\d+(\.\d+)?/g);
    if (!m) return '#000000';
    return `#${m.slice(0, 3).map((v) => Math.round(Number(v)).toString(16).padStart(2, '0')).join('')}`;
  }

  // ---------------- 功能按钮、面板、自检 ----------------
  /** 加一个功能按钮（顶栏）：{ id, label, icon?, key?, run(editor) }。run 抛错时自动提示。 */
  function addAction(a) {
    if (!a || !a.id || !a.label || typeof a.run !== 'function') throw new Error('功能按钮要有 id、label 和 run');
    actions.push(a);
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'k-btn k-sm';
    b.dataset.action = a.id;
    b.innerHTML = `${a.icon ? icon(a.icon, { size: 16 }) : ''}<span>${esc(a.label)}</span>`;
    if (a.key) b.title = `${a.label}（${a.key}）`;
    b.addEventListener('click', () => runAction(a));
    actionsBox.appendChild(b);
    if (a.key) shortcuts.push({ key: a.key, run: () => runAction(a), label: a.label });
    return b;
  }
  async function runAction(a) {
    try { await a.run(api); } catch (err) { notify(err.message || String(err), 'bad'); }
  }
  /** 加一个面板（右侧）：{ id, title, icon? } → 面板内容区（空的 div）。 */
  function addPanel({ id, title, icon: ic } = {}) {
    const sec = document.createElement('section');
    sec.className = 'k-ie-panel';
    sec.dataset.panel = id || `p${panelsBox.children.length + 1}`;
    const hid = `k-ie-p-${sec.dataset.panel}`;
    sec.setAttribute('aria-labelledby', hid);
    sec.innerHTML = `<div class="k-ie-ph"><h2 id="${esc(hid)}">${ic ? icon(ic, { size: 15 }) : ''}${esc(title || '功能')}</h2></div><div class="k-ie-pb"></div>`;
    panelsBox.appendChild(sec);
    return sec.querySelector('.k-ie-pb');
  }
  /** 加快捷键：shortcut('mod+k', run, '说明')。 */
  function shortcut(key, run, label = '') { shortcuts.push({ key, run, label }); }
  /** 功能自带的测试：addTest(名字, async (t) => { … })，t.assert(条件, 说明)、t.editor、t.sample(w, h)。 */
  function addTest(name, fn, { feature = '' } = {}) { tests.push({ name, fn, feature }); }

  // ---------------- 快捷键 ----------------
  const builtin = [
    { key: 'mod+z', run: () => undo() },
    { key: 'mod+shift+z', run: () => redo() },
    { key: 'mod+y', run: () => redo() },
    { key: 'mod+o', run: () => fileInput.click() },
    { key: 'mod+s', run: () => download() },
    { key: 'mod+j', run: () => duplicateLayer() },
    { key: 'delete', run: () => removeLayer(), plain: true },
    { key: '[', run: () => { brush.size = Math.max(1, brush.size - 4); renderPanels(); }, plain: true },
    { key: ']', run: () => { brush.size = Math.min(200, brush.size + 4); renderPanels(); }, plain: true },
    { key: '=', run: () => zoomBy(1.25), plain: true },
    { key: '+', run: () => zoomBy(1.25), plain: true },
    { key: '-', run: () => zoomBy(0.8), plain: true },
    { key: '0', run: () => fit(), plain: true },
    { key: 'escape', run: () => setSelection(null), plain: true },
  ];
  function onKey(ev) {
    const typing = ev.target.closest?.('input,textarea,select,[contenteditable="true"]');
    if (ev.key === ' ' && !typing) { spaceDown = ev.type === 'keydown'; if (spaceDown) ev.preventDefault(); return; }
    if (ev.type !== 'keydown' || typing) return;
    for (const s of [...builtin, ...shortcuts]) {
      if (matchKey(s.key, ev)) {
        ev.preventDefault();
        try { s.run(); } catch (err) { notify(err.message || String(err), 'bad'); }
        return;
      }
    }
  }
  document.addEventListener('keydown', onKey);
  document.addEventListener('keyup', onKey);

  // ---------------- 顶栏、拖放、粘贴 ----------------
  root.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-cmd]');
    if (!b) return;
    const cmd = b.dataset.cmd;
    try {
      if (cmd === 'open') fileInput.click();
      else if (cmd === 'export') download();
      else if (cmd === 'undo') undo();
      else if (cmd === 'redo') redo();
      else if (cmd === 'zoom-in') zoomBy(1.25);
      else if (cmd === 'zoom-out') zoomBy(0.8);
      else if (cmd === 'fit') fit();
      else if (cmd === 'add-layer') addLayer();
      else if (cmd === 'dup-layer') duplicateLayer();
      else if (cmd === 'del-layer') removeLayer();
      else if (cmd === 'close-alert') alertBox.hidden = true;
      else if (cmd === 'keys') { const k = $('.k-ie-keys'); k.hidden = !k.hidden; b.setAttribute('aria-expanded', String(!k.hidden)); }
    } catch (err) { notify(err.message || String(err), 'bad'); }
  });
  fileInput.addEventListener('change', async () => {
    for (const f of [...fileInput.files]) await importFile(f);
    fileInput.value = '';
  });
  stage.addEventListener('dragover', (ev) => { ev.preventDefault(); stage.classList.add('k-ie-over'); });
  stage.addEventListener('dragleave', () => stage.classList.remove('k-ie-over'));
  stage.addEventListener('drop', async (ev) => {
    ev.preventDefault();
    stage.classList.remove('k-ie-over');
    for (const f of [...(ev.dataTransfer?.files || [])]) await importFile(f);
  });
  document.addEventListener('paste', async (ev) => {
    const items = [...(ev.clipboardData?.files || [])].filter((f) => f.type.startsWith('image/'));
    for (const f of items) await importFile(f);
  });
  new ResizeObserver(() => { invalidate(); }).observe(stage);

  // ---------------- 对外接口 ----------------
  const api = {
    version: VERSION,
    root,
    get doc() { return { width: doc.width, height: doc.height }; },
    get layers() { return doc.layers.map((l) => ({ id: l.id, name: l.name, visible: l.visible, opacity: l.opacity, x: l.x, y: l.y, width: l.canvas.width, height: l.canvas.height })); },
    get active() { const l = activeLayer(); return l ? { id: l.id, name: l.name } : null; },
    get selection() { return doc.selection ? { ...doc.selection } : null; },
    get tool() { return tool; },
    get history() { return { undo: past.length, redo: future.length, limit: undoLimit, min: UNDO_MIN, labels: past.map((x) => x.label) }; },
    layerCanvas(id = doc.active) { return layerById(id)?.canvas || null; },
    on, edit, undo, redo, select, addLayer, removeLayer, duplicateLayer, moveLayer, setLayer,
    paint, applyFilter, crop, setSelection, importFile, open, exportCanvas, exportBlob, download, reset,
    addTool, setTool, addAction, addPanel, addTest, shortcut, notify, busy, fit, zoomBy,
    get tests() { return tests.slice(); },
    async selftest(options) {
      const mod = await import('./selftest.js');
      return mod.selftest(api, options);
    },
  };
  renderTools();
  setTool('move');
  changed();
  window.__editor = api;
  document.documentElement.dataset.editor = 'ready';
  return api;
}
