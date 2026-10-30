// 工作台设计套件（docs/KIT.md）：图表、表格、会话窗、提示、空状态等，配合 kit.css 用。
// 原生 ES 模块，不依赖任何库；所有颜色走 CSS 变量，明暗和主题切换后自动跟着变。
// 图表按容器实际宽度画（字号不随缩放变小），容器变宽变窄会重画。

export { icon, ICONS } from './icons.js';
import { icon } from './icons.js';

export const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

export const PALETTE = ['var(--k-s1)', 'var(--k-s2)', 'var(--k-s3)', 'var(--k-s4)', 'var(--k-s5)', 'var(--k-s6)'];
const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

/** 数字格式：fmt(1234.5) → "1,234.5"；digits 固定小数位。 */
export function fmt(n, { digits = null, unit = '' } = {}) {
  if (n == null || Number.isNaN(+n)) return '—';
  const v = +n;
  const d = digits ?? (Math.abs(v) >= 100 || Number.isInteger(v) ? 0 : 1);
  return v.toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d }) + unit;
}

/** 好看的刻度：niceTicks(3.2, 97) → [0, 20, 40, 60, 80, 100]。 */
export function niceTicks(min, max, count = 4) {
  if (!(max > min)) { max = min + 1; }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const out = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(+v.toFixed(10));
  return out;
}

/** 数字从 0 滚到目标值。 */
export function countUp(el, to, { digits = null, ms = 700, unit = '' } = {}) {
  if (!el) return;
  if (reduced()) { el.textContent = fmt(to, { digits }) + unit; return; }
  const t0 = performance.now();
  const step = (t) => {
    const p = Math.min(1, (t - t0) / ms);
    const e = 1 - (1 - p) ** 3;
    el.textContent = fmt(to * e, { digits: digits ?? (Number.isInteger(to) ? 0 : 1) }) + unit;
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/** 本机存储（只放每个人自己的便利设置；读写失败当作没有）。 */
export function persist(ns) {
  return {
    get(k, d) { try { const v = localStorage.getItem(`${ns}:${k}`); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(`${ns}:${k}`, JSON.stringify(v)); } catch { /* 无痕窗口等 */ } },
  };
}

/**
 * 几个窗口一起读写的数据（行程里加了什么、记了哪些账）：存在本机，谁改了都通知大家。
 * 「已加入」「已付」这类状态都从这里算，不各存一份，两边才对得上。
 * sharedState(ns, bus) → { get(k, d), set(k, v), update(k, fn, d), on(k, fn) → 取消 }
 * on 会立刻用当前值调一次 fn，之后每次有窗口 set 都再调一次。
 */
export function sharedState(ns, bus) {
  const box = persist(ns);
  const name = (k) => `state:${k}`;
  const api = {
    get: (k, d) => box.get(k, d),
    set(k, v) { box.set(k, v); bus.dispatchEvent(new CustomEvent(name(k), { detail: v })); return v; },
    update(k, fn, d) { return api.set(k, fn(box.get(k, d))); },
    on(k, fn, d) {
      const h = (ev) => fn(ev.detail);
      bus.addEventListener(name(k), h);
      fn(box.get(k, d));
      return () => bus.removeEventListener(name(k), h);
    },
  };
  return api;
}

export function setTheme(name) { document.documentElement.dataset.kTheme = name; }
export function setMode(mode) {
  if (mode === 'auto') delete document.documentElement.dataset.kMode; else document.documentElement.dataset.kMode = mode;
}

// ---------- 浮层提示 ----------
let tipEl = null;
function tip(html, x, y) {
  if (!tipEl) { tipEl = document.createElement('div'); tipEl.className = 'k-tip'; tipEl.setAttribute('role', 'status'); document.body.appendChild(tipEl); }
  if (html == null) { tipEl.classList.remove('k-show'); return; }
  tipEl.innerHTML = html;
  const w = tipEl.offsetWidth / 2 + 8;
  tipEl.style.left = `${Math.max(w, Math.min(innerWidth - w, x))}px`;
  tipEl.style.top = `${Math.max(tipEl.offsetHeight + 16, y)}px`;
  tipEl.classList.add('k-show');
}
const swatch = (c) => `<i style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${c};margin-right:6px"></i>`;

// ---------- 图表的公共部分 ----------
const charts = new Map();
let ro = null;
function track(el, draw) {
  el.classList.add('k-chart');
  charts.set(el, draw);
  if (!ro && typeof ResizeObserver === 'function') {
    ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        const d = charts.get(e.target);
        if (d && Math.abs((e.target._kw || 0) - e.contentRect.width) > 2) d(false);
      }
    });
  }
  ro?.observe(el);
  draw(true);
}
const widthOf = (el) => { const w = Math.round(el.clientWidth || el.getBoundingClientRect().width || 320); el._kw = w; return Math.max(160, w); };
function legendHtml(items) {
  return `<div class="k-legend">${items.map((s) => `<span>${s.dashed ? `<i style="background:none;border-top:2px dashed ${s.color};height:0;border-radius:0"></i>` : `<i style="background:${s.color}"></i>`}${esc(s.name)}${s.extra ? ` <span class="k-v">${esc(s.extra)}</span>` : ''}</span>`).join('')}</div>`;
}

/**
 * 折线 / 面积图。
 * lineChart(el, { labels, series: [{ name, values, color?, area?, dashed?, axis?: 'right' }], height?, yFormat?, rightFormat?, xEvery? })
 */
export function lineChart(el, opt) {
  const draw = (animate) => {
    const W = widthOf(el), H = opt.height || 200;
    const series = opt.series.map((s, i) => ({ ...s, color: s.color || PALETTE[i % 6] }));
    const hasRight = series.some((s) => s.axis === 'right');
    const L = 40, R = hasRight ? 40 : 12, T = 10, B = 24;
    const scale = (ss) => {
      const vs = ss.flatMap((s) => s.values).filter((v) => v != null);
      if (!vs.length) return null;
      let lo = Math.min(...vs), hi = Math.max(...vs);
      if (opt.zero) lo = Math.min(0, lo);
      const pad = (hi - lo) * 0.08 || 1; const t = niceTicks(lo - (opt.zero ? 0 : pad), hi + pad, 4);
      return { t, lo: t[0], hi: t.at(-1) };
    };
    const left = scale(series.filter((s) => s.axis !== 'right'));
    const right = hasRight ? scale(series.filter((s) => s.axis === 'right')) : null;
    const n = opt.labels.length;
    const X = (i) => L + (n <= 1 ? 0 : (i * (W - L - R)) / (n - 1));
    const Y = (v, sc) => T + ((sc.hi - v) / (sc.hi - sc.lo)) * (H - T - B);
    const yf = opt.yFormat || ((v) => fmt(v)), rf = opt.rightFormat || yf;
    const every = opt.xEvery || Math.max(1, Math.ceil(n / Math.max(2, Math.floor((W - L - R) / 56))));
    let g = '';
    if (left) g += left.t.map((v) => `<line class="k-grid-l" x1="${L}" x2="${W - R}" y1="${Y(v, left)}" y2="${Y(v, left)}"/><text x="${L - 8}" y="${Y(v, left) + 4}" text-anchor="end">${esc(yf(v))}</text>`).join('');
    if (right) g += right.t.map((v) => `<text x="${W - R + 8}" y="${Y(v, right) + 4}">${esc(rf(v))}</text>`).join('');
    g += opt.labels.map((lb, i) => (i % every === 0 || i === n - 1) && !(i !== n - 1 && n - 1 - i < every / 2) ? `<text x="${X(i)}" y="${H - 6}" text-anchor="middle">${esc(lb)}</text>` : '').join('');
    const id = `kg${Math.random().toString(36).slice(2, 8)}`;
    series.forEach((s, si) => {
      const sc = s.axis === 'right' ? right : left; if (!sc) return;
      const pts = s.values.map((v, i) => (v == null ? null : [X(i), Y(v, sc)])).filter(Boolean);
      if (!pts.length) return;
      const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('');
      if (s.area) g += `<defs><linearGradient id="${id}${si}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${s.color}" stop-opacity=".28"/><stop offset="1" stop-color="${s.color}" stop-opacity="0"/></linearGradient></defs><path d="${d}L${pts.at(-1)[0]},${H - B}L${pts[0][0]},${H - B}Z" fill="url(#${id}${si})"/>`;
      const len = Math.ceil(pts.reduce((a, p, i) => a + (i ? Math.hypot(p[0] - pts[i - 1][0], p[1] - pts[i - 1][1]) : 0), 0)) + 4;
      g += `<path class="k-line${animate && !reduced() && !s.dashed ? ' k-draw' : ''}" style="--k-len:${len}" d="${d}" stroke="${s.color}"${s.dashed ? ' stroke-dasharray="5 4"' : ''}/>`;
      const last = pts.at(-1); g += `<circle cx="${last[0]}" cy="${last[1]}" r="3.5" fill="${s.color}" stroke="var(--k-surface)" stroke-width="2"/>`;
    });
    g += `<line class="k-cross" x1="0" x2="0" y1="${T}" y2="${H - B}"/><g class="k-dots"></g><rect class="k-hit" x="${L}" y="0" width="${W - L - R}" height="${H}" fill="transparent"/>`;
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(opt.label || series.map((s) => s.name).join('、') + ' 折线图')}">${g}</svg>${opt.legend === false ? '' : legendHtml(series)}`;
    const svg = el.querySelector('svg'), cross = svg.querySelector('.k-cross'), dots = svg.querySelector('.k-dots');
    const hover = (ev) => {
      const r = svg.getBoundingClientRect(); const x = ((ev.clientX - r.left) / r.width) * W;
      const i = Math.max(0, Math.min(n - 1, Math.round(((x - L) / (W - L - R)) * (n - 1))));
      cross.setAttribute('x1', X(i)); cross.setAttribute('x2', X(i)); svg.classList.add('k-hot');
      dots.innerHTML = series.map((s) => { const sc = s.axis === 'right' ? right : left; const v = s.values[i]; return v == null || !sc ? '' : `<circle cx="${X(i)}" cy="${Y(v, sc)}" r="4.5" fill="${s.color}" stroke="var(--k-surface)" stroke-width="2"/>`; }).join('');
      tip(`<b>${esc(opt.labels[i])}</b><br>${series.map((s) => s.values[i] == null ? '' : `${swatch(s.color)}${esc(s.name)} <b>${esc((s.axis === 'right' ? rf : yf)(s.values[i]))}</b>`).filter(Boolean).join('<br>')}`, r.left + (X(i) / W) * r.width, r.top + 8);
    };
    svg.addEventListener('pointermove', hover);
    svg.addEventListener('pointerleave', () => { svg.classList.remove('k-hot'); dots.innerHTML = ''; tip(null); });
  };
  track(el, draw);
}

/**
 * 柱状图（可堆叠）。
 * barChart(el, { labels, series: [{ name, values, color? }], stacked?, highlight?: 索引, height?, yFormat? })
 */
export function barChart(el, opt) {
  const draw = (animate) => {
    const W = widthOf(el), H = opt.height || 200, L = 40, R = 8, T = 10, B = 24;
    const series = opt.series.map((s, i) => ({ ...s, color: s.color || PALETTE[i % 6] }));
    const n = opt.labels.length;
    const tot = (i) => (opt.stacked ? series.reduce((a, s) => a + (+s.values[i] || 0), 0) : Math.max(...series.map((s) => +s.values[i] || 0)));
    const max = Math.max(1e-9, ...opt.labels.map((_, i) => tot(i)));
    const t = niceTicks(0, max, 4), hi = t.at(-1);
    const Y = (v) => T + (1 - v / hi) * (H - T - B);
    const band = (W - L - R) / n, gap = Math.max(2, band * 0.28), bw = band - gap;
    const yf = opt.yFormat || ((v) => fmt(v));
    let g = t.map((v) => `<line class="k-grid-l" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${esc(yf(v))}</text>`).join('');
    const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor((W - L - R) / 34))));
    for (let i = 0; i < n; i++) {
      const x0 = L + i * band + gap / 2; let acc = 0;
      const act = opt.highlight === i ? ' k-active' : '';
      if (opt.stacked) {
        series.forEach((s, si) => {
          const v = +s.values[i] || 0; if (!v) return; const y1 = Y(acc + v), y0 = Y(acc); acc += v;
          const top = si === series.length - 1 || !series.slice(si + 1).some((z) => +z.values[i]);
          g += `<rect class="k-bar${act}" data-i="${i}" x="${x0}" y="${y1}" width="${bw}" height="${Math.max(0, y0 - y1)}" rx="${top ? Math.min(4, bw / 3) : 0}" fill="${s.color}" style="${animate ? `animation-delay:${i * 18}ms` : 'animation:none'}"/>`;
        });
      } else {
        const sw = bw / series.length;
        series.forEach((s, si) => {
          const v = +s.values[i] || 0;
          g += `<rect class="k-bar${act}" data-i="${i}" x="${x0 + si * sw}" y="${Y(v)}" width="${Math.max(1, sw - (series.length > 1 ? 2 : 0))}" height="${Math.max(0, Y(0) - Y(v))}" rx="${Math.min(4, sw / 3)}" fill="${s.color}" style="${animate ? `animation-delay:${i * 18}ms` : 'animation:none'}"/>`;
        });
      }
      if (i % every === 0 || opt.highlight === i) g += `<text x="${x0 + bw / 2}" y="${H - 6}" text-anchor="middle"${opt.highlight === i ? ' style="fill:var(--k-fg);font-weight:600"' : ''}>${esc(opt.labels[i])}</text>`;
    }
    g += `<g class="k-hits">${opt.labels.map((_, i) => `<rect data-i="${i}" x="${L + i * band}" y="0" width="${band}" height="${H - B}" fill="transparent"/>`).join('')}</g>`;
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(opt.label || '柱状图')}"><g class="k-plot">${g}</g></svg>${opt.legend === false || series.length < 2 ? '' : legendHtml(series)}`;
    const svg = el.querySelector('svg'), plot = svg.querySelector('.k-plot');
    svg.querySelector('.k-hits').addEventListener('pointermove', (ev) => {
      const i = +ev.target.dataset.i; if (Number.isNaN(i)) return;
      plot.classList.add('k-dim'); plot.querySelectorAll('.k-bar').forEach((b) => b.classList.toggle('k-active', +b.dataset.i === i));
      const r = svg.getBoundingClientRect();
      tip(`<b>${esc(opt.labels[i])}</b><br>${series.map((s) => `${swatch(s.color)}${esc(s.name)} <b>${esc(yf(+s.values[i] || 0))}</b>`).join('<br>')}`, r.left + ((L + (i + 0.5) * band) / W) * r.width, r.top + (Y(tot(i)) / H) * r.height);
    });
    svg.addEventListener('pointerleave', () => { plot.classList.remove('k-dim'); plot.querySelectorAll('.k-bar').forEach((b) => b.classList.toggle('k-active', opt.highlight === +b.dataset.i)); tip(null); });
  };
  track(el, draw);
}

/**
 * 环形图。
 * donut(el, { items: [{ label, value, color? }], center?: { value, label }, size?, format? })
 */
export function donut(el, opt) {
  const draw = () => {
    const W = widthOf(el), items = opt.items.map((s, i) => ({ ...s, color: s.color || PALETTE[i % 6] })).filter((s) => s.value > 0);
    const size = Math.min(opt.size || 168, W), r = size / 2 - 12, sw = Math.max(14, size * 0.13), C = 2 * Math.PI * r;
    const tot = items.reduce((a, s) => a + s.value, 0) || 1, f = opt.format || ((v) => fmt(v));
    let acc = 0;
    const arcs = items.map((s, i) => { const len = (s.value / tot) * C, off = acc; acc += len; return `<circle class="k-seg-arc" data-i="${i}" r="${r}" fill="none" stroke="${s.color}" stroke-width="${sw}" stroke-dasharray="${Math.max(0, len - (items.length > 1 ? 2 : 0))} ${C}" stroke-dashoffset="${-off}"/>`; }).join('');
    const c = opt.center || { value: f(tot), label: '合计' };
    const side = W >= size + 170;
    el.innerHTML = `<div style="display:flex;align-items:center;gap:18px;flex-wrap:${side ? 'nowrap' : 'wrap'};justify-content:${side ? 'flex-start' : 'center'}">
      <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" style="width:${size}px;flex:none" role="img" aria-label="${esc(opt.label || '环形图')}"><circle r="${r}" cx="${size / 2}" cy="${size / 2}" fill="none" stroke="var(--k-surface-3)" stroke-width="${sw}"/><g class="k-plot" transform="translate(${size / 2} ${size / 2}) rotate(-90)">${arcs}</g>
      <text class="k-center-v" x="${size / 2}" y="${size / 2 + 6}" text-anchor="middle">${esc(c.value)}</text><text x="${size / 2}" y="${size / 2 + 24}" text-anchor="middle">${esc(c.label)}</text></svg>
      <div class="k-legend" style="flex-direction:column;gap:6px;margin:0;min-width:0">${items.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.label)} <span class="k-v">${esc(f(s.value))} · ${Math.round((s.value / tot) * 100)}%</span></span>`).join('')}</div></div>`;
    const plot = el.querySelector('.k-plot');
    plot.addEventListener('pointermove', (ev) => {
      const i = +ev.target.dataset.i; if (Number.isNaN(i)) return; const s = items[i];
      plot.classList.add('k-dim'); plot.querySelectorAll('.k-seg-arc').forEach((a) => a.classList.toggle('k-active', +a.dataset.i === i));
      tip(`${swatch(s.color)}${esc(s.label)} <b>${esc(f(s.value))}</b> · ${Math.round((s.value / tot) * 100)}%`, ev.clientX, ev.clientY - 6);
    });
    plot.addEventListener('pointerleave', () => { plot.classList.remove('k-dim'); tip(null); });
  };
  track(el, draw);
}

/** 迷你趋势线（KPI 卡里用）。 */
export function spark(el, values, { color = 'var(--k-accent)', area = true } = {}) {
  const draw = () => {
    const W = widthOf(el), H = el.clientHeight || 34, vs = values.filter((v) => v != null);
    if (vs.length < 2) { el.innerHTML = ''; return; }
    const lo = Math.min(...vs), hi = Math.max(...vs), X = (i) => (i * (W - 4)) / (values.length - 1) + 2, Y = (v) => 3 + ((hi - v) / (hi - lo || 1)) * (H - 6);
    const d = values.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join('');
    const id = `ks${Math.random().toString(36).slice(2, 8)}`;
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true" style="display:block">${area ? `<defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".25"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs><path d="${d}L${X(values.length - 1)},${H}L${X(0)},${H}Z" fill="url(#${id})"/>` : ''}<path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
  };
  track(el, draw);
}

/** 进度环。 */
export function ring(el, value, { max = 100, size = 120, label = '', color = 'var(--k-accent)', text = null } = {}) {
  const r = size / 2 - 9, C = 2 * Math.PI * r, p = Math.max(0, Math.min(1, value / max));
  // 小圆环：数字不小于 14px，说明字放不下就挪到圆环下面，不叠在一起
  const big = size >= 96, num = Math.max(14, Math.round(size / 5));
  const caption = label && big ? `<text x="50%" y="${size / 2 + size / 6}" text-anchor="middle" style="font:500 12px var(--k-font);fill:var(--k-fg-3)">${esc(label)}</text>` : '';
  const under = label && !big ? `<div class="k-sub" style="text-align:center">${esc(label)}</div>` : '';
  el.innerHTML = `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img" aria-label="${esc(label)} ${Math.round(p * 100)}%"><circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--k-surface-3)" stroke-width="10"/><circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="10" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - p)}" transform="rotate(-90 ${size / 2} ${size / 2})" style="transition:stroke-dashoffset var(--k-slow) var(--k-ease)"/><text x="50%" y="${big ? size / 2 + 2 : size / 2 + num / 3}" text-anchor="middle" style="font:700 ${num}px var(--k-font);fill:var(--k-fg)">${esc(text ?? Math.round(p * 100) + '%')}</text>${caption}</svg>${under}`;
}

/**
 * 日历热力图（习惯、打卡次数等）：values 是从最早到今天的每日数值。
 * heatmap(el, { values, end?: Date, label? })
 */
export function heatmap(el, { values, end = new Date(), label = '每日' }) {
  const draw = () => {
    const W = widthOf(el), days = values.length, weeks = Math.ceil((days + 6) / 7) + 1;
    const cell = Math.max(9, Math.min(16, Math.floor((W - 22) / weeks) - 3)), gap = 3, H = 7 * (cell + gap) + 18;
    const max = Math.max(1, ...values);
    const start = new Date(end); start.setDate(start.getDate() - days + 1);
    const off = (start.getDay() + 6) % 7; // 每列一周，周一在最上面
    let g = ['一', '三', '五'].map((d, i) => `<text x="0" y="${i * 2 * (cell + gap) + cell - 2}">${d}</text>`).join('');
    values.forEach((v, i) => {
      const dt = new Date(start); dt.setDate(start.getDate() + i);
      const col = Math.floor((i + off) / 7), row = (i + off) % 7;
      const k = v ? 0.2 + 0.8 * (v / max) : 0;
      g += `<rect x="${20 + col * (cell + gap)}" y="${row * (cell + gap)}" width="${cell}" height="${cell}" rx="3" fill="${v ? `color-mix(in srgb, var(--k-accent) ${Math.round(k * 100)}%, var(--k-surface-3))` : 'var(--k-surface-3)'}"><title>${dt.getMonth() + 1}月${dt.getDate()}日 ${label} ${v}</title></rect>`;
    });
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(label)}热力图">${g}</svg>`;
  };
  track(el, draw);
}

/**
 * 可排序表格。
 * table(el, { columns: [{ key, label, align?: 'right', format?: (v, row) => html, sort?: true }], rows, empty? })
 */
export function table(el, { columns, rows, empty: emptyText = '还没有数据' }) {
  let sortKey = null, dir = 1;
  const render = () => {
    const rs = sortKey ? [...rows].sort((a, b) => (a[sortKey] > b[sortKey] ? dir : a[sortKey] < b[sortKey] ? -dir : 0)) : rows;
    el.innerHTML = rows.length ? `<div class="k-table-wrap"><table class="k-table"><thead><tr>${columns.map((c) => `<th${c.sort ? ` data-sort="${esc(c.key)}"` : ''}${sortKey === c.key ? ` aria-sort="${dir > 0 ? 'ascending' : 'descending'}"` : ''} class="${c.align === 'right' ? 'k-r' : ''}" scope="col">${esc(c.label)}</th>`).join('')}</tr></thead>
      <tbody>${rs.map((r) => `<tr>${columns.map((c) => `<td class="${c.align === 'right' ? 'k-r k-num' : ''}">${c.format ? c.format(r[c.key], r) : esc(r[c.key])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
      : empty({ icon: 'inbox', title: emptyText });
  };
  el.addEventListener('click', (ev) => {
    const th = ev.target.closest('[data-sort]'); if (!th) return;
    dir = sortKey === th.dataset.sort ? -dir : 1; sortKey = th.dataset.sort; render();
  });
  render();
  return { update(next) { rows = next; render(); } };
}

/** 空状态（返回 HTML）。 */
export function empty({ icon: ic = 'inbox', title = '', hint = '', action = '' } = {}) {
  return `<div class="k-empty"><div class="k-ico-wrap">${icon(ic, { size: 22 })}</div>${title ? `<strong>${esc(title)}</strong>` : ''}${hint ? `<p>${esc(hint)}</p>` : ''}${action}</div>`;
}

/** 底部提示条。 */
export function toast(text, { icon: ic = 'circle-check', ms = 2600 } = {}) {
  let box = document.querySelector('.k-toasts');
  if (!box) { box = document.createElement('div'); box.className = 'k-toasts'; box.setAttribute('aria-live', 'polite'); document.body.appendChild(box); }
  const t = document.createElement('div'); t.className = 'k-toast'; t.innerHTML = `${icon(ic)}<span>${esc(text)}</span>`;
  box.appendChild(t);
  setTimeout(() => { t.classList.add('k-out'); setTimeout(() => t.remove(), 200); }, ms);
}

/** 分段按钮：seg(el, [{value, label}], 当前值, 变化回调)。 */
export function seg(el, options, value, onChange) {
  el.classList.add('k-seg'); el.setAttribute('role', 'group');
  const render = () => { el.innerHTML = options.map((o) => `<button type="button" data-v="${esc(o.value)}" aria-pressed="${o.value === value}">${esc(o.label)}</button>`).join(''); };
  el.addEventListener('click', (ev) => { const b = ev.target.closest('[data-v]'); if (!b) return; value = options.find((o) => String(o.value) === b.dataset.v).value; render(); onChange?.(value); });
  render();
  return { get value() { return value; }, set(v) { value = v; render(); } };
}

/**
 * 会话窗：在 el 里画消息列表、快捷问题和输入框。
 * chat(el, { greeting?, quick?: [], placeholder?, avatar?: 图标名, onAsk: async (text, history) => 回答文字, store?: persist(...) })
 * 回答可以慢慢来：等待期间显示「正在输入」。
 */
export function chat(el, opt) {
  const st = opt.store, key = 'chat';
  let log = (st && st.get(key, null)) || (opt.greeting ? [['ai', opt.greeting]] : []);
  el.classList.add('k-chat');
  el.innerHTML = `<div class="k-chat-log" aria-live="polite"></div><div class="k-quick"></div><form class="k-composer"><input class="k-input" placeholder="${esc(opt.placeholder || '输入你的问题')}" aria-label="${esc(opt.placeholder || '输入你的问题')}" autocomplete="off"><button class="k-btn k-primary k-icon" type="submit" aria-label="发送">${icon('send', { size: 16 })}</button></form>`;
  const box = el.querySelector('.k-chat-log'), input = el.querySelector('input'), quick = el.querySelector('.k-quick');
  const bubble = ([who, text]) => `<div class="k-msg k-${who}">${who === 'ai' ? `<span class="k-who">${icon(opt.avatar || 'bot', { size: 15 })}</span>` : ''}<div class="k-bubble">${esc(text)}</div></div>`;
  const render = () => { box.innerHTML = log.map(bubble).join(''); box.scrollTop = box.scrollHeight; };
  const save = () => st && st.set(key, log.slice(-40));
  quick.innerHTML = (opt.quick || []).map((q) => `<button type="button" class="k-chip" data-q="${esc(q)}">${esc(q)}</button>`).join('');
  let busy = false;
  async function ask(text) {
    if (busy || !text) return; busy = true;
    log.push(['me', text]); render(); save();
    box.insertAdjacentHTML('beforeend', `<div class="k-msg k-ai k-wait"><span class="k-who">${icon(opt.avatar || 'bot', { size: 15 })}</span><div class="k-bubble k-typing" aria-label="正在输入"><i></i><i></i><i></i></div></div>`);
    box.scrollTop = box.scrollHeight;
    let reply;
    try { reply = await opt.onAsk(text, log); } catch (e) { reply = `出错了：${e?.message || e}`; }
    log.push(['ai', String(reply ?? '')]); busy = false; render(); save();
  }
  quick.addEventListener('click', (ev) => { const b = ev.target.closest('[data-q]'); if (b) ask(b.dataset.q); });
  el.querySelector('form').addEventListener('submit', (ev) => { ev.preventDefault(); const t = input.value.trim(); input.value = ''; ask(t); });
  render();
  return { ask, say(text) { log.push(['ai', text]); render(); save(); }, clear() { log = opt.greeting ? [['ai', opt.greeting]] : []; render(); save(); } };
}

// ---------- 时间线 ----------
/**
 * 竖向时间线（行程、日程、进度记录）。
 * timeline(el, { items: [{ time, title, meta?, icon?, tone?: 'accent'|'good'|'warn'|'bad', done? }] })
 */
export function timeline(el, { items, empty: emptyText = '还没有安排' }) {
  el.classList.add('k-timeline');
  el.innerHTML = items.length ? `<ol class="k-tl">${items.map((it) => `<li class="k-tl-i${it.done ? ' k-done' : ''}" data-tone="${esc(it.tone || 'accent')}">
    <span class="k-tl-dot">${icon(it.done ? 'check' : it.icon || 'clock', { size: 13 })}</span>
    <div class="k-tl-b"><span class="k-tl-t k-num">${esc(it.time)}</span><strong>${esc(it.title)}</strong>${it.meta ? `<span class="k-tl-m">${esc(it.meta)}</span>` : ''}</div></li>`).join('')}</ol>` : empty({ icon: 'calendar', title: emptyText });
}

// ---------- 看板 ----------
/**
 * 看板：拖动卡片换列；键盘用户用卡片上的 ← → 按钮。
 * kanban(el, { columns: [{ id, title }], cards: [{ id, col, title, meta?, tag?, tone? }], onMove?: (card, toCol) => void })
 */
export function kanban(el, { columns, cards, onMove }) {
  el.classList.add('k-kanban');
  const render = () => {
    el.innerHTML = columns.map((c, ci) => {
      const cs = cards.filter((k) => k.col === c.id);
      return `<section class="k-kb-col" data-col="${esc(c.id)}" aria-label="${esc(c.title)}"><header><span>${esc(c.title)}</span><span class="k-badge k-num">${cs.length}</span></header>
        <div class="k-kb-list">${cs.map((k) => `<article class="k-kb-card" draggable="true" data-id="${esc(k.id)}"><strong>${esc(k.title)}</strong>${k.meta ? `<span class="k-meta">${esc(k.meta)}</span>` : ''}
          <div class="k-split">${k.tag ? `<span class="k-badge k-${esc(k.tone || 'accent')}">${esc(k.tag)}</span>` : '<span></span>'}<span class="k-row" style="gap:2px">${ci > 0 ? `<button type="button" class="k-btn k-ghost k-icon k-sm" data-mv="-1" aria-label="移到${esc(columns[ci - 1].title)}">${icon('chevron-left', { size: 15 })}</button>` : ''}${ci < columns.length - 1 ? `<button type="button" class="k-btn k-ghost k-icon k-sm" data-mv="1" aria-label="移到${esc(columns[ci + 1].title)}">${icon('chevron-right', { size: 15 })}</button>` : ''}</span></div></article>`).join('') || '<p class="k-kb-empty">拖到这里</p>'}</div></section>`;
    }).join('');
  };
  const move = (id, col) => { const k = cards.find((x) => String(x.id) === String(id)); if (!k || k.col === col) return; k.col = col; render(); onMove?.(k, col); };
  el.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-mv]'); if (!b) return;
    const id = b.closest('[data-id]').dataset.id, k = cards.find((x) => String(x.id) === id);
    const ci = columns.findIndex((c) => c.id === k.col) + +b.dataset.mv; if (columns[ci]) move(id, columns[ci].id);
  });
  el.addEventListener('dragstart', (ev) => { const c = ev.target.closest('[data-id]'); if (c) { ev.dataTransfer.setData('text/plain', c.dataset.id); c.classList.add('k-dragging'); } });
  el.addEventListener('dragend', () => el.querySelectorAll('.k-dragging,.k-over').forEach((x) => x.classList.remove('k-dragging', 'k-over')));
  el.addEventListener('dragover', (ev) => { const col = ev.target.closest('[data-col]'); if (col) { ev.preventDefault(); el.querySelectorAll('.k-over').forEach((x) => x !== col && x.classList.remove('k-over')); col.classList.add('k-over'); } });
  el.addEventListener('drop', (ev) => { const col = ev.target.closest('[data-col]'); if (col) { ev.preventDefault(); move(ev.dataTransfer.getData('text/plain'), col.dataset.col); } });
  render();
  return { update(next) { cards = next; render(); } };
}

// ---------- 月历 ----------
/**
 * 月历：标出有安排的日子，点一天回调。
 * calendar(el, { month?: Date, events: [{ date: 'YYYY-MM-DD', title, tone? }], onPick?: (iso, events) => void })
 */
export function calendar(el, { month = new Date(), events = [], onPick } = {}) {
  el.classList.add('k-cal');
  let cur = new Date(month.getFullYear(), month.getMonth(), 1), picked = null;
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const today = iso(new Date());
  const render = () => {
    const y = cur.getFullYear(), m = cur.getMonth(), first = (new Date(y, m, 1).getDay() + 6) % 7, n = new Date(y, m + 1, 0).getDate();
    let cells = '';
    for (let i = 0; i < first; i++) cells += '<span class="k-cal-d k-out" aria-hidden="true"></span>';
    for (let d = 1; d <= n; d++) {
      const k = iso(new Date(y, m, d)), ev = events.filter((e) => e.date === k);
      cells += `<button type="button" class="k-cal-d${k === today ? ' k-today' : ''}${k === picked ? ' k-picked' : ''}" data-d="${k}" aria-label="${m + 1}月${d}日${ev.length ? `，${ev.length} 项安排` : ''}"><span class="k-num">${d}</span>${ev.length ? `<span class="k-cal-dots">${ev.slice(0, 3).map((e) => `<i style="background:var(--k-${esc(e.tone || 'accent')})"></i>`).join('')}</span>` : ''}</button>`;
    }
    el.innerHTML = `<div class="k-split" style="margin-bottom:8px"><strong class="k-num">${y} 年 ${m + 1} 月</strong><span class="k-row" style="gap:2px"><button type="button" class="k-btn k-ghost k-icon k-sm" data-nav="-1" aria-label="上个月">${icon('chevron-left', { size: 16 })}</button><button type="button" class="k-btn k-ghost k-icon k-sm" data-nav="1" aria-label="下个月">${icon('chevron-right', { size: 16 })}</button></span></div>
      <div class="k-cal-g">${['一', '二', '三', '四', '五', '六', '日'].map((w) => `<span class="k-cal-w">${w}</span>`).join('')}${cells}</div>`;
  };
  el.addEventListener('click', (ev) => {
    const nav = ev.target.closest('[data-nav]'); if (nav) { cur = new Date(cur.getFullYear(), cur.getMonth() + +nav.dataset.nav, 1); render(); return; }
    const d = ev.target.closest('[data-d]'); if (d) { picked = d.dataset.d; render(); onPick?.(picked, events.filter((e) => e.date === picked)); }
  });
  render();
  return { update(next) { events = next; render(); } };
}

// ---------- 地图框 ----------
/**
 * 示意地图：按经纬度把地点画在一张自适应的图上，按顺序连线、编号；不联网、不需要地图服务。
 * 需要真地图时，生成器在这一格里换成地图服务（要用户点头）。
 * mapFrame(el, { points: [{ name, lat, lng, meta?, group? }], route?: true, height? })
 */
export function mapFrame(el, { points, route = true, height = 300, label = '地点示意图' }) {
  const draw = () => {
    const W = widthOf(el), H = height, pad = 34;
    if (!points.length) { el.innerHTML = empty({ icon: 'globe', title: '还没有地点' }); return; }
    const lats = points.map((p) => p.lat), lngs = points.map((p) => p.lng);
    let [a, b, c, d] = [Math.min(...lats), Math.max(...lats), Math.min(...lngs), Math.max(...lngs)];
    if (b - a < 0.01) { a -= 0.01; b += 0.01; } if (d - c < 0.01) { c -= 0.01; d += 0.01; }
    const k = Math.cos(((a + b) / 2) * Math.PI / 180);
    const sx = (W - pad * 2) / ((d - c) * k), sy = (H - pad * 2) / (b - a), s = Math.min(sx, sy);
    const ox = (W - (d - c) * k * s) / 2, oy = (H - (b - a) * s) / 2;
    const X = (lng) => ox + (lng - c) * k * s, Y = (lat) => H - oy - (lat - a) * s;
    const groups = [...new Set(points.map((p) => p.group ?? ''))];
    const col = (p) => PALETTE[groups.indexOf(p.group ?? '') % 6];
    let g = `<rect width="${W}" height="${H}" rx="12" fill="var(--k-surface-2)"/>`;
    for (let x = 24; x < W; x += 48) g += `<line x1="${x}" x2="${x}" y1="0" y2="${H}" stroke="var(--k-line)" stroke-width="1" opacity=".6"/>`;
    for (let y = 24; y < H; y += 48) g += `<line y1="${y}" y2="${y}" x1="0" x2="${W}" stroke="var(--k-line)" stroke-width="1" opacity=".6"/>`;
    if (route && points.length > 1) {
      const pts = points.map((p) => [X(p.lng), Y(p.lat)]);
      const dd = pts.map((p, i) => (i ? `Q${(pts[i - 1][0] + p[0]) / 2 + (p[1] - pts[i - 1][1]) * 0.15},${(pts[i - 1][1] + p[1]) / 2 - (p[0] - pts[i - 1][0]) * 0.15} ${p[0]},${p[1]}` : `M${p[0]},${p[1]}`)).join(' ');
      g += `<path d="${dd}" fill="none" stroke="var(--k-accent)" stroke-width="2.5" stroke-dasharray="6 5" opacity=".75"/>`;
    }
    // 地点名字找一个不和别的圆点、名字重叠的位置：下、上、右、左
    const boxes = points.map((p) => ({ x: X(p.lng) - 15, y: Y(p.lat) - 15, w: 30, h: 30 }));
    const hit = (r) => boxes.some((o) => r.x < o.x + o.w && r.x + r.w > o.x && r.y < o.y + o.h && r.y + r.h > o.y);
    points.forEach((p, i) => {
      const x = X(p.lng), y = Y(p.lat), tw = [...p.name].length * 13 + 6;
      const cand = [[x, y + 30, 'middle', x - tw / 2, y + 18], [x, y - 20, 'middle', x - tw / 2, y - 33], [x + 19, y + 5, 'start', x + 17, y - 8], [x - 19, y + 5, 'end', x - 17 - tw, y - 8]];
      const pick = cand.find((c) => { const r = { x: c[3], y: c[4], w: tw, h: 17 }; return r.x >= 0 && r.x + r.w <= W && !hit(r); }) || cand[0];
      boxes.push({ x: pick[3], y: pick[4], w: tw, h: 17 });
      g += `<g class="k-pin" data-i="${i}" tabindex="0" role="button" aria-label="${i + 1}. ${esc(p.name)}"><circle cx="${x}" cy="${y}" r="13" fill="${col(p)}" stroke="var(--k-surface)" stroke-width="3"/><text x="${x}" y="${y + 4}" text-anchor="middle" style="fill:#fff;font-weight:700;font-size:12px">${i + 1}</text>
        <text x="${pick[0]}" y="${pick[1]}" text-anchor="${pick[2]}" style="fill:var(--k-fg);font-weight:600;font-size:12.5px;paint-order:stroke;stroke:var(--k-surface-2);stroke-width:4px">${esc(p.name)}</text></g>`;
    });
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(label)}">${g}</svg>`;
    el.querySelectorAll('.k-pin').forEach((pin) => {
      const show = () => { const p = points[+pin.dataset.i], r = pin.getBoundingClientRect(); tip(`<b>${+pin.dataset.i + 1}. ${esc(p.name)}</b>${p.meta ? `<br>${esc(p.meta)}` : ''}`, r.left + r.width / 2, r.top); };
      pin.addEventListener('pointerenter', show); pin.addEventListener('focus', show);
      pin.addEventListener('pointerleave', () => tip(null)); pin.addEventListener('blur', () => tip(null));
    });
  };
  track(el, draw);
}

// ---------- 图片 / 视频查看器 ----------
/**
 * 缩略图网格 + 点开大图（← → 切换，Esc 关闭）。
 * viewer(el, { items: [{ src, alt, caption?, type?: 'image'|'video' }] })
 */
export function viewer(el, { items }) {
  el.classList.add('k-viewer');
  el.innerHTML = items.length ? `<div class="k-vw-grid">${items.map((it, i) => `<button type="button" class="k-vw-th" data-i="${i}" aria-label="查看：${esc(it.alt)}">${it.type === 'video' ? `<video src="${esc(it.src)}" muted preload="metadata" aria-hidden="true"></video><span class="k-vw-play">${icon('play', { size: 18 })}</span>` : `<img src="${esc(it.src)}" alt="${esc(it.alt)}" loading="lazy">`}</button>`).join('')}</div>` : empty({ icon: 'image', title: '还没有图片' });
  let dlg = null, cur = 0;
  const show = () => {
    const it = items[cur];
    dlg.querySelector('.k-vw-stage').innerHTML = it.type === 'video' ? `<video src="${esc(it.src)}" controls autoplay></video>` : `<img src="${esc(it.src)}" alt="${esc(it.alt)}">`;
    dlg.querySelector('.k-vw-cap').textContent = `${cur + 1} / ${items.length}${it.caption ? ` · ${it.caption}` : ''}`;
  };
  el.addEventListener('click', (ev) => {
    const th = ev.target.closest('[data-i]'); if (!th) return; cur = +th.dataset.i;
    if (!dlg) {
      dlg = document.createElement('dialog'); dlg.className = 'k-dialog k-vw-dlg';
      dlg.innerHTML = `<div class="k-vw-stage"></div><div class="k-split" style="padding:10px 14px"><span class="k-vw-cap k-muted"></span><span class="k-row" style="gap:4px"><button type="button" class="k-btn k-icon k-sm" data-go="-1" aria-label="上一张">${icon('chevron-left', { size: 16 })}</button><button type="button" class="k-btn k-icon k-sm" data-go="1" aria-label="下一张">${icon('chevron-right', { size: 16 })}</button><button type="button" class="k-btn k-icon k-sm" data-close aria-label="关闭">${icon('x', { size: 16 })}</button></span></div>`;
      dlg.addEventListener('click', (e) => {
        if (e.target === dlg || e.target.closest('[data-close]')) dlg.close();
        const g = e.target.closest('[data-go]'); if (g) { cur = (cur + +g.dataset.go + items.length) % items.length; show(); }
      });
      dlg.addEventListener('keydown', (e) => { if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { cur = (cur + (e.key === 'ArrowRight' ? 1 : -1) + items.length) % items.length; show(); } });
      document.body.appendChild(dlg);
    }
    show(); dlg.showModal();
  });
}

// ---------- 编辑器框 ----------
/**
 * 带行号、字数和保存状态的文本编辑框（写作、代码、提示词、清单）。
 * editor(el, { value, placeholder?, mono?: true, onChange?: (text) => void, label? })
 */
export function editor(el, { value = '', placeholder = '', mono = true, onChange, label = '编辑区' } = {}) {
  el.classList.add('k-editor');
  el.innerHTML = `<div class="k-ed-body"><pre class="k-ed-gutter k-num" aria-hidden="true"></pre><textarea class="k-ed-ta${mono ? ' k-mono' : ''}" spellcheck="false" aria-label="${esc(label)}" placeholder="${esc(placeholder)}"></textarea></div><div class="k-ed-foot"><span class="k-ed-count k-num"></span><span class="k-ed-state">已保存</span></div>`;
  const ta = el.querySelector('textarea'), gut = el.querySelector('.k-ed-gutter'), cnt = el.querySelector('.k-ed-count'), state = el.querySelector('.k-ed-state');
  let timer = 0;
  const sync = () => {
    const lines = ta.value.split('\n').length;
    gut.textContent = Array.from({ length: lines }, (_, i) => i + 1).join('\n');
    const chars = ta.value.replace(/\s/g, '').length;
    cnt.textContent = `${lines} 行 · ${chars} 字`;
  };
  ta.value = value; sync();
  ta.addEventListener('scroll', () => { gut.scrollTop = ta.scrollTop; });
  ta.addEventListener('input', () => { sync(); state.textContent = '正在保存…'; clearTimeout(timer); timer = setTimeout(() => { onChange?.(ta.value); state.textContent = '已保存'; }, 500); });
  ta.addEventListener('keydown', (e) => { if (e.key === 'Tab' && !e.shiftKey && mono) { e.preventDefault(); ta.setRangeText('  ', ta.selectionStart, ta.selectionEnd, 'end'); ta.dispatchEvent(new Event('input')); } });
  return { get value() { return ta.value; }, set(v) { ta.value = v; sync(); } };
}

// ---------- 清单 ----------
/**
 * 可勾选清单（打包清单、待办、步骤），带完成进度。
 * checklist(el, { items: [{ id, text, done?, meta? }], onChange?: (items) => void, addable?: true })
 */
export function checklist(el, { items, onChange, addable = true, placeholder = '加一项' }) {
  el.classList.add('k-checklist');
  const render = () => {
    const done = items.filter((x) => x.done).length;
    el.innerHTML = `<div class="k-split" style="margin-bottom:6px"><span class="k-muted k-num" style="font-size:12.5px">完成 ${done} / ${items.length}</span><span class="k-progress" style="flex:1;max-width:160px"><i style="width:${items.length ? (done / items.length) * 100 : 0}%"></i></span></div>
      <ul class="k-list">${items.map((x) => `<li class="k-item k-ck${x.done ? ' k-done' : ''}"><input type="checkbox" class="k-check" data-id="${esc(x.id)}"${x.done ? ' checked' : ''} id="ck-${esc(x.id)}"><label for="ck-${esc(x.id)}" class="k-main"><span class="k-title">${esc(x.text)}</span>${x.meta ? `<span class="k-meta">${esc(x.meta)}</span>` : ''}</label><button type="button" class="k-btn k-ghost k-icon k-sm" data-del="${esc(x.id)}" aria-label="删掉：${esc(x.text)}">${icon('x', { size: 15 })}</button></li>`).join('')}</ul>
      ${addable ? `<form class="k-row" style="margin-top:8px"><input class="k-input" style="flex:1" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}"><button class="k-btn k-icon" type="submit" aria-label="添加">${icon('plus', { size: 16 })}</button></form>` : ''}`;
  };
  el.addEventListener('change', (ev) => { const c = ev.target.closest('[data-id]'); if (!c) return; items.find((x) => String(x.id) === c.dataset.id).done = c.checked; render(); onChange?.(items); });
  el.addEventListener('click', (ev) => { const d = ev.target.closest('[data-del]'); if (!d) return; items = items.filter((x) => String(x.id) !== d.dataset.del); render(); onChange?.(items); });
  el.addEventListener('submit', (ev) => { ev.preventDefault(); const inp = el.querySelector('form input'); const t = inp.value.trim(); if (!t) return; items.push({ id: Date.now().toString(36), text: t, done: false }); render(); onChange?.(items); el.querySelector('form input').focus(); });
  render();
  return { get items() { return items; } };
}
