// 星图（docs/STARMAP.md）：圆桌星图 + 每位成员的人生星图，立体、可以转动。
// - 数据来自 /api/starmap 与 /api/starmap/<成员>（只读，不调模型）；这里只负责摆位置和画。
// - 拖动转动、滚轮 / 双指缩放；点一颗星：镜头飞过去，和它相连的星亮起、其余变暗，右侧卡片写详情和相关的星。
// - 空心虚线的星是「缺口」：还缺的东西，卡片里写怎么补。闪着光圈的是一周内的变化。
// - 底部时间轴：拖回过去，看圆桌 / 成员一路是怎么长出来的；「播放」从头放一遍。
// - 不用第三方库：canvas 2D + 自己的透视投影（几百颗星，普通电脑和平板都流畅）。

import { THEMES, Scene3D, loadLib, webglOK } from './starmap3d.js';
import { iconSvg } from './starmap_icons.js';

const THEME_KEY = 'roundtable.starmap.theme';
const ACCENT_KEY = 'roundtable.starmap.accent';
// 主题色：一排色块 + 自选颜色；点一下全图（线、光点、缺口、按钮）一起换，记在本机
export const ACCENTS = [
  ['#ff5a3c', '橙红'],
  ['#2b4bff', '克莱因蓝'],
  ['#1f8a64', '墨绿'],
  ['#8a4fe0', '紫'],
  ['#c9962e', '金'],
  ['#e0457b', '玫红'],
];
const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

// 星域的色相（HSL），圆桌和人生星图共用一张表；没列到的按名字散列
const HUE = {
  core: 44, hub: 198, work: 36, home: 152, chat: 328, bench: 262, members: 14, health: 96,
  identity: 210, personality: 330, values: 46, likes: 16, ability: 162, memory: 262,
  relation: 190, emotion: 352, style: 288, growth: 104,
};
const KIND_R = { core: 13, domain: 7.5, member: 6.2, star: 2.6, gap: 3.4 };
const DUST = 420; // 背景星尘颗数
const FOCAL = 760;

function hash(s) {
  let h = 2166136261;
  for (const c of String(s)) h = Math.imul(h ^ c.codePointAt(0), 16777619);
  return h >>> 0;
}
function rng(seed) {
  let a = seed || 1;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const hueOf = (g) => (g in HUE ? HUE[g] : hash(g) % 360);
const col = (g, l = 70, a = 1, s = 80) => `hsla(${hueOf(g)},${s}%,${l}%,${a})`;
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));

// 星域到中心的距离：星域越多轨道越大，免得挤在一起
export const orbitOf = (map) => 180 + 16 * map.nodes.filter((n) => n.kind === 'domain').length;

// 摆位置：中心在原点，星域在一条略微起伏的轨道上，每个星域的星绕着它排成一个小球壳
export function layout(map) {
  const pos = new Map();
  const kids = new Map();
  const parent = new Map();
  const center = map.center;
  pos.set(center, [0, 0, 0]);
  const domains = map.nodes.filter((n) => n.kind === 'domain');
  const orbit = orbitOf(map);
  const hubs = new Set([center, ...domains.map((d) => d.id)]);
  for (const l of map.links) {
    if (l.kind !== 'tree') continue;
    const [p, c] = hubs.has(l.a) ? [l.a, l.b] : [l.b, l.a];
    if (c === center || parent.has(c)) continue;
    parent.set(c, p);
    if (!kids.has(p)) kids.set(p, []);
    kids.get(p).push(c);
  }
  domains.forEach((d, i) => {
    const a = (i / domains.length) * Math.PI * 2 + 0.35;
    const y = (i % 2 ? 1 : -1) * 46 + Math.sin(a * 2) * 22;
    pos.set(d.id, [Math.cos(a) * orbit, y, Math.sin(a) * orbit]);
  });
  for (const d of domains) {
    const base = pos.get(d.id);
    const list = kids.get(d.id) || [];
    const r = 40 + 15 * Math.sqrt(list.length);
    const out = [base[0] * 0.22, 0, base[2] * 0.22]; // 往外推一点，别挤向中心
    const n = list.length;
    list.forEach((id, k) => {
      // 斐波那契球面，按 id 再抖一抖，看起来自然
      const t = rng(hash(id));
      const y = n === 1 ? 0 : 1 - (2 * (k + 0.5)) / n;
      const rr = Math.sqrt(1 - y * y);
      const phi = k * 2.399963 + t() * 0.4;
      const j = r * (0.82 + t() * 0.3);
      pos.set(id, [
        base[0] + out[0] + Math.cos(phi) * rr * j,
        base[1] + y * j * 0.8,
        base[2] + out[2] + Math.sin(phi) * rr * j,
      ]);
    });
  }
  for (const n of map.nodes) {
    if (pos.has(n.id)) continue;
    const t = rng(hash(n.id));
    pos.set(n.id, [(t() - 0.5) * 120, (t() - 0.5) * 80, (t() - 0.5) * 120]);
  }
  return pos;
}

export class StarMap {
  constructor(dlg, button, { api }) {
    this.dlg = dlg;
    this.api = api; // {roundtable(), member(id)}
    this.view = 'roundtable'; // 'roundtable' 或成员 id
    this.map = null;
    this.members = []; // 圆桌星图里的成员：{id, name}
    this.sel = null;
    this.hover = null;
    this.cut = null; // 时间轴：只显示 t <= cut 的星（null = 现在）
    this.cam = { yaw: 0.6, pitch: 0.38, dist: 820, tx: 0, ty: 0, tz: 0, goal: [0, 0, 0], goalDist: 820, goalYaw: null };
    this.idle = 0;
    this.shift = 0;
    this.raf = 0;
    this.reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.theme = 'mono';
    this.accent = ACCENTS[0][0];
    try {
      const t = localStorage.getItem(THEME_KEY);
      if (t && (THEMES[t] || t === 'flat')) this.theme = t;
      const a = localStorage.getItem(ACCENT_KEY);
      if (a && /^#[0-9a-f]{6}$/i.test(a)) this.accent = a;
    } catch {
      /* 本机存不了就用默认 */
    }
    const t = rng(7);
    this.dust = Array.from({ length: DUST }, () => {
      const u = t() * 2 - 1;
      const a = t() * Math.PI * 2;
      const r = 700 + t() * 900;
      return [Math.sqrt(1 - u * u) * Math.cos(a) * r, u * r * 0.6, Math.sqrt(1 - u * u) * Math.sin(a) * r, t()];
    });
    button.addEventListener('click', () => this.open());
    dlg.addEventListener('close', () => this.stop());
    dlg.addEventListener('click', (e) => this.onClick(e));
    dlg.addEventListener('input', (e) => this.onInput(e));
  }

  async open(view = 'roundtable') {
    if (!this.dlg.open) this.dlg.showModal();
    this.shell();
    await this.load(view);
  }

  shell() {
    if (this.dlg.querySelector('.sm')) return;
    this.dlg.innerHTML = `<div class="sm">
      <header class="sm-top">
        <div class="sm-title"><h2 data-title>星图</h2><span class="sm-sub" data-sub></span></div>
        <nav class="sm-tabs" data-tabs aria-label="切换星图"></nav>
        <div class="sm-acc" role="group" aria-label="主题色">${ACCENTS.map(
          ([c, name]) => `<button type="button" data-acc="${c}" style="--sw:${c}" title="${name}" aria-label="主题色：${name}"></button>`,
        ).join('')}<label class="sm-acc-pick" title="自己调颜色"><input type="color" data-accpick aria-label="自己调主题色"></label></div>
        <select class="sm-theme" data-skin aria-label="画风">${Object.entries(THEMES)
          .map(([k, t]) => `<option value="${k}">${t.label}</option>`)
          .join('')}<option value="flat">平面（省电）</option></select>
        <button type="button" class="sm-fold" data-fold aria-expanded="false">星域</button>
        <button type="button" class="sm-x" data-close aria-label="关闭">×</button>
      </header>
      <div class="sm-stage" data-stage>
        <div class="sm-gl" data-gl></div>
        <canvas class="sm-canvas" aria-label="立体星图：拖动转动，滚轮缩放，点星看详情"></canvas>
        <div class="sm-tip" data-tip hidden></div>
        <div class="sm-hint">拖动转动 · 滚轮缩放 · 点星看相关</div>
      </div>
      <aside class="sm-legend" data-legend></aside>
      <aside class="sm-card" data-card hidden></aside>
      <footer class="sm-time" data-time></footer>
    </div>`;
    this.canvas = this.dlg.querySelector('canvas');
    this.ctx = this.canvas.getContext('2d');
    this.bindPointer();
    this.dlg.querySelector('[data-skin]').addEventListener('change', (e) => this.setTheme(e.target.value));
    this.dlg.querySelector('[data-accpick]').addEventListener('input', (e) => this.setAccent(e.target.value));
    this.setAccent(this.accent, false);
  }

  setAccent(hex, save = true) {
    this.accent = hex;
    const box = this.dlg.querySelector('.sm');
    box.style.setProperty('--acc', hex);
    for (const b of this.dlg.querySelectorAll('[data-acc]')) b.setAttribute('aria-pressed', String(b.dataset.acc === hex));
    const pick = this.dlg.querySelector('[data-accpick]');
    if (pick.value !== hex) pick.value = hex;
    if (this.gl) this.gl.setAccent(hex);
    if (save) {
      try {
        localStorage.setItem(ACCENT_KEY, hex);
      } catch {
        /* 存不了也照样换 */
      }
    }
  }

  async setTheme(t) {
    this.theme = t;
    try {
      localStorage.setItem(THEME_KEY, t);
    } catch {
      /* 存不了也照样切 */
    }
    this.dropGl();
    if (this.map) {
      await this.draw();
      this.select(this.sel);
    }
  }

  dropGl() {
    if (this.gl) this.gl.dispose();
    this.gl = null;
  }

  // 画：能用 WebGL 就用 three.js 的立体画法，否则（或选了「平面」）用 canvas 2D
  async draw() {
    const box = this.dlg.querySelector('.sm');
    box.dataset.theme = this.theme;
    this.dlg.querySelector('[data-skin]').value = this.theme;
    if (this.theme !== 'flat' && webglOK()) {
      try {
        await loadLib();
        if (!this.gl) {
          this.gl = new Scene3D(this.dlg.querySelector('[data-gl]'), {
            theme: this.theme,
            accent: this.accent,
            onPick: (id, e, dbl) => this.picked(id, dbl),
          });
        }
        this.gl.setMap(this.map, this.pos, this.orbit);
        box.classList.add('gl');
        this.stop2d();
        return;
      } catch (err) {
        console.warn('星图：WebGL 画不了，改用平面', err);
        this.dropGl();
      }
    }
    box.classList.remove('gl');
    this.start();
  }

  picked(id, dbl) {
    const n = id && this.byId.get(id);
    if (n && n.kind === 'member' && (dbl || id === this.sel)) this.load(n.member);
    else this.select(id);
  }

  async load(view) {
    this.view = view;
    this.sel = null;
    this.cut = null;
    this.dlg.querySelector('[data-card]').hidden = true;
    this.dlg.querySelector('[data-sub]').textContent = '读取中…';
    try {
      if (view === 'roundtable') {
        this.map = await this.api.roundtable();
        this.members = this.map.nodes.filter((n) => n.kind === 'member').map((n) => ({ id: n.member, name: n.label }));
      } else {
        this.map = await this.api.member(view);
      }
    } catch (e) {
      this.dlg.querySelector('[data-sub]').textContent = `读不到星图：${e.message}`;
      return;
    }
    this.index();
    this.cam.goal = [0, 0, 0];
    this.cam.goalDist = this.home;
    this.renderPanels();
    await this.draw();
  }

  index() {
    const m = this.map;
    this.pos = layout(m);
    this.orbit = orbitOf(m);
    this.home = Math.round(this.orbit * 3.1);
    this.byId = new Map(m.nodes.map((n) => [n.id, n]));
    this.adj = new Map(m.nodes.map((n) => [n.id, []]));
    for (const l of m.links) {
      if (this.adj.has(l.a)) this.adj.get(l.a).push({ id: l.b, kind: l.kind });
      if (this.adj.has(l.b)) this.adj.get(l.b).push({ id: l.a, kind: l.kind });
    }
    const ts = m.nodes.map((n) => n.t).filter(Boolean).sort();
    this.times = [...new Set(ts)];
  }

  visible(n) {
    return !this.cut || !n.t || n.t <= this.cut;
  }

  // ------------------------------------------------------------------ 面板

  renderPanels() {
    const m = this.map;
    const d = this.dlg;
    d.querySelector('[data-title]').textContent = m.title;
    d.querySelector('[data-fold]').textContent = m.kind === 'life' ? '星域' : '星区';
    d.querySelector('[data-sub]').textContent = m.subtitle;
    const tab = (id, label, extra = '') =>
      `<button type="button" data-view="${esc(id)}" aria-pressed="${this.view === id}" ${extra}>${esc(label)}</button>`;
    d.querySelector('[data-tabs]').innerHTML =
      tab('roundtable', '圆桌') + this.members.map((x) => tab(x.id, x.name)).join('');

    const groups = m.groups
      .map((g) => {
        const pct = g.total ? Math.round((g.filled / Math.max(g.total, 1)) * 100) : null;
        const empty = g.total === null && !g.count;
        return `<li class="sm-g${empty ? ' empty' : ''}" data-node="${esc(this.domainId(g.key))}" style="--c:${col(g.key, 66)}">
          <i>${iconSvg(g.key)}</i><span>${esc(g.label)}</span><b>${empty ? '缺' : pct !== null ? `${g.filled}/${g.total}` : g.count}</b>
          ${pct !== null ? `<em style="--p:${pct}%"></em>` : ''}</li>`;
      })
      .join('');
    const score = m.score !== null && m.score !== undefined
      ? `<div class="sm-score"><svg viewBox="0 0 36 36"><circle cx="18" cy="18" r="15.9" /><circle cx="18" cy="18" r="15.9" style="stroke-dasharray:${Math.round(m.score * 100)} 100" /></svg><b>${Math.round(m.score * 100)}<small>%</small></b><span>完整度</span></div>`
      : '';
    const gaps = m.gaps.length
      ? `<h3>缺口 <small>${m.gaps.length}</small></h3><ul class="sm-gaps">${m.gaps
          .map((g) => `<li data-node="${esc(g.node)}" title="${esc(g.fix || '')}">${esc(g.text)}</li>`)
          .join('')}</ul>`
      : '<h3>缺口</h3><p class="sm-none">没有缺口，星图是完整的。</p>';
    d.querySelector('[data-legend]').innerHTML = `${score}<h3>${m.kind === 'life' ? '星域' : '星区'}</h3><ul class="sm-groups">${groups}</ul>${gaps}`;

    const times = this.times;
    const changes = m.changes
      .slice(0, 8)
      .map((c) => `<li data-node="${esc(c.node)}"><time>${esc((c.t || '').slice(5))}</time>${esc(c.text)}</li>`)
      .join('');
    d.querySelector('[data-time]').innerHTML = `
      <div class="sm-tl">
        <button type="button" class="sm-play" data-play ${times.length > 1 ? '' : 'disabled'} aria-label="从头播放变化">▶</button>
        <input type="range" data-cut min="0" max="${times.length}" value="${times.length}" step="1" ${times.length ? '' : 'disabled'} aria-label="时间轴">
        <output data-cutlabel>现在</output>
      </div>
      <ul class="sm-changes" aria-label="最近的变化">${changes || '<li class="sm-none">还没有变化记录</li>'}</ul>`;
  }

  domainId(key) {
    const n = this.map.nodes.find((x) => x.kind === 'domain' && x.group === key);
    return n ? n.id : '';
  }

  renderCard() {
    const card = this.dlg.querySelector('[data-card]');
    const n = this.sel && this.byId.get(this.sel);
    if (!n) {
      card.hidden = true;
      return;
    }
    const rel = (this.adj.get(n.id) || [])
      .map((x) => ({ ...x, n: this.byId.get(x.id) }))
      .filter((x) => x.n && this.visible(x.n))
      .sort((a, b) => (a.kind === 'tree') - (b.kind === 'tree') || b.n.value - a.n.value);
    const kindName = { core: '中心', domain: '星域', member: '成员', star: '星', gap: '缺口' }[n.kind] || '';
    const linkName = { relate: '相关', peer: '亲近', tree: '' };
    const groupLabel = (this.map.groups.find((g) => g.key === n.group) || {}).label || '';
    card.hidden = false;
    card.style.setProperty('--c', col(n.group, 68));
    card.innerHTML = `
      <div class="sm-ch"><span class="sm-dot ${n.kind}">${n.kind === 'gap' ? '' : iconSvg(n.kind === 'core' ? 'core' : n.group)}</span><div><b>${esc(n.label)}</b><small>${esc([groupLabel, kindName].filter(Boolean).join(' · '))}${n.fresh ? ' · <i class="sm-new">新</i>' : ''}</small></div>
        <button type="button" class="sm-x" data-unsel aria-label="取消选择">×</button></div>
      ${n.detail ? `<p class="sm-detail">${esc(n.detail)}</p>` : ''}
      ${n.kind === 'member' ? `<button type="button" class="sm-enter" data-view="${esc(n.member)}">进入${esc(n.label)}的人生星图 →</button>` : ''}
      ${n.peer && this.view !== 'roundtable' ? `<button type="button" class="sm-enter" data-view="${esc(n.peer)}">去看${esc(n.label.split(' · ')[0])}的星图 →</button>` : ''}
      ${rel.length ? `<h4>相关的星 <small>${rel.length}</small></h4><ul class="sm-rel">${rel
        .slice(0, 24)
        .map((x) => `<li data-node="${esc(x.id)}" style="--c:${col(x.n.group, 66)}"><i class="${x.n.kind}"></i>${esc(x.n.label)}${linkName[x.kind] ? `<em>${linkName[x.kind]}</em>` : ''}</li>`)
        .join('')}</ul>` : ''}`;
  }

  select(id) {
    if (!id || !this.byId.has(id)) {
      this.sel = null;
      this.cam.goal = [0, 0, 0];
      this.cam.goalDist = this.home;
    } else {
      this.sel = id;
      const p = this.pos.get(id);
      const n = this.byId.get(id);
      this.cam.goal = n.kind === 'core' ? [0, 0, 0] : p.slice();
      // 转到「从外往里看」：选中的星在前，中心在它身后，同一星域的星散开在周围
      if (n.kind !== 'core' && Math.hypot(p[0], p[2]) > 20) this.cam.goalYaw = -Math.PI / 2 - Math.atan2(p[2], p[0]);
      this.cam.goalDist = n.kind === 'core' ? this.home : n.kind === 'domain' || n.kind === 'member' ? 600 : 500;
    }
    this.idle = 0;
    if (this.gl) this.gl.select(this.sel, new Set(this.sel ? [this.sel, ...(this.adj.get(this.sel) || []).map((x) => x.id)] : []));
    this.renderCard();
    for (const li of this.dlg.querySelectorAll('[data-node]')) li.classList.toggle('on', li.dataset.node === this.sel);
  }

  onClick(e) {
    const t = e.target.closest('[data-close],[data-view],[data-node],[data-unsel],[data-play],[data-fold],[data-acc]');
    if (!t) return;
    if (t.matches('[data-acc]')) this.setAccent(t.dataset.acc);
    else if (t.matches('[data-fold]')) {
      const box = this.dlg.querySelector('.sm');
      const open = !box.classList.contains('legend-open');
      box.classList.toggle('legend-open', open);
      t.setAttribute('aria-expanded', String(open));
    } else if (t.matches('[data-close]')) this.dlg.close();
    else if (t.matches('[data-view]')) this.load(t.dataset.view);
    else if (t.matches('[data-unsel]')) this.select(null);
    else if (t.matches('[data-play]')) this.play();
    else if (t.matches('[data-node]')) {
      this.dlg.querySelector('.sm').classList.remove('legend-open');
      this.cut = null;
      this.syncCut();
      this.select(t.dataset.node);
    }
  }

  onInput(e) {
    if (!e.target.matches('[data-cut]')) return;
    const i = Number(e.target.value);
    this.cut = i >= this.times.length ? null : this.times[i] || null;
    this.syncCut(false);
  }

  syncCut(setRange = true) {
    const r = this.dlg.querySelector('[data-cut]');
    const label = this.dlg.querySelector('[data-cutlabel]');
    if (!r) return;
    if (setRange) r.value = this.cut ? this.times.indexOf(this.cut) : this.times.length;
    label.textContent = this.cut ? this.cut : '现在';
    if (this.gl) this.gl.setVisible((n) => this.visible(n));
    if (this.sel && !this.visible(this.byId.get(this.sel))) this.select(null);
    else this.renderCard();
  }

  play() {
    if (!this.times.length) return;
    clearInterval(this.playing);
    let i = 0;
    this.playing = setInterval(() => {
      if (!this.dlg.open || i > this.times.length) {
        clearInterval(this.playing);
        return;
      }
      this.cut = i >= this.times.length ? null : this.times[i];
      this.syncCut();
      i += 1;
    }, Math.max(120, Math.min(600, 6000 / this.times.length)));
  }

  // ------------------------------------------------------------------ 交互

  bindPointer() {
    const c = this.canvas;
    const pts = new Map();
    let moved = 0;
    let pinch = 0;
    c.addEventListener('pointerdown', (e) => {
      c.setPointerCapture(e.pointerId);
      pts.set(e.pointerId, [e.clientX, e.clientY]);
      moved = 0;
      this.idle = 0;
    });
    c.addEventListener('pointermove', (e) => {
      const r = c.getBoundingClientRect();
      if (!pts.has(e.pointerId)) {
        this.hover = this.pick(e.clientX - r.left, e.clientY - r.top);
        c.style.cursor = this.hover ? 'pointer' : 'grab';
        this.tip(this.hover, e.clientX - r.left, e.clientY - r.top);
        return;
      }
      const [x0, y0] = pts.get(e.pointerId);
      pts.set(e.pointerId, [e.clientX, e.clientY]);
      if (pts.size === 2) {
        const [a, b] = [...pts.values()];
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (pinch) this.zoom(pinch / d);
        pinch = d;
        moved += 10;
        return;
      }
      const dx = e.clientX - x0;
      const dy = e.clientY - y0;
      moved += Math.abs(dx) + Math.abs(dy);
      this.cam.yaw -= dx * 0.006;
      this.cam.goalYaw = null;
      this.cam.pitch = clamp(this.cam.pitch + dy * 0.005, -1.25, 1.25);
      this.idle = 0;
      c.style.cursor = 'grabbing';
    });
    const up = (e) => {
      if (!pts.has(e.pointerId)) return;
      pts.delete(e.pointerId);
      if (pts.size < 2) pinch = 0;
      if (moved < 6 && pts.size === 0) {
        const r = c.getBoundingClientRect();
        const id = this.pick(e.clientX - r.left, e.clientY - r.top);
        if (id && id === this.sel && this.byId.get(id).kind === 'member') this.load(this.byId.get(id).member);
        else this.select(id);
      }
    };
    c.addEventListener('pointerup', up);
    c.addEventListener('pointercancel', up);
    c.addEventListener('pointerleave', () => {
      this.hover = null;
      this.tip(null);
    });
    c.addEventListener(
      'wheel',
      (e) => {
        e.preventDefault();
        this.zoom(Math.exp(e.deltaY * 0.0012));
      },
      { passive: false },
    );
    c.addEventListener('dblclick', (e) => {
      const r = c.getBoundingClientRect();
      const id = this.pick(e.clientX - r.left, e.clientY - r.top);
      const n = id && this.byId.get(id);
      if (n && n.kind === 'member') this.load(n.member);
    });
  }

  zoom(f) {
    this.cam.goalDist = clamp(this.cam.goalDist * f, 220, 2200);
    this.idle = 0;
  }

  tip(id, x = 0, y = 0) {
    const el = this.dlg.querySelector('[data-tip]');
    const n = id && this.byId.get(id);
    if (!n) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    el.textContent = n.label;
    el.style.transform = `translate(${Math.round(x + 14)}px, ${Math.round(y - 10)}px)`;
  }

  pick(x, y) {
    let best = null;
    let bd = Infinity;
    for (const p of this.proj || []) {
      const d = Math.hypot(p.x - x, p.y - y);
      if (d < Math.max(10, p.r + 6) && d < bd) {
        best = p.id;
        bd = d;
      }
    }
    return best;
  }

  // ------------------------------------------------------------------ 绘制

  start() {
    if (this.raf) return;
    const tick = (now) => {
      this.raf = this.dlg.open ? requestAnimationFrame(tick) : 0;
      this.frame(now || 0);
    };
    this.raf = requestAnimationFrame(tick);
  }

  stop() {
    this.stop2d();
    clearInterval(this.playing);
    this.dropGl();
  }

  stop2d() {
    cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  project(p, W, H) {
    const c = this.cam;
    const x = p[0] - c.tx;
    const y = p[1] - c.ty;
    const z = p[2] - c.tz;
    const cy = Math.cos(c.yaw);
    const sy = Math.sin(c.yaw);
    const x1 = x * cy - z * sy;
    const z1 = x * sy + z * cy;
    const cp = Math.cos(c.pitch);
    const sp = Math.sin(c.pitch);
    const y2 = y * cp - z1 * sp;
    const z2 = y * sp + z1 * cp + c.dist;
    const k = FOCAL / Math.max(z2, 40);
    return { x: W / 2 + x1 * k, y: H / 2 - this.shift + y2 * k, z: z2, k };
  }

  frame(now) {
    const cv = this.canvas;
    if (!cv || !this.map) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const W = cv.clientWidth;
    const H = cv.clientHeight;
    if (!W || !H) return;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
      cv.width = Math.round(W * dpr);
      cv.height = Math.round(H * dpr);
    }
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const cam = this.cam;
    this.idle += 1;
    if (this.idle > 90 && !this.hover) cam.yaw += (this.reduce ? 0.0004 : 0.0011) * (this.sel ? 0.4 : 1);
    const e = 0.08;
    if (cam.goalYaw !== null && cam.goalYaw !== undefined) {
      const d = Math.atan2(Math.sin(cam.goalYaw - cam.yaw), Math.cos(cam.goalYaw - cam.yaw));
      cam.yaw += d * e;
      if (Math.abs(d) < 0.002) cam.goalYaw = null;
    }
    cam.tx += (cam.goal[0] - cam.tx) * e;
    cam.ty += (cam.goal[1] - cam.ty) * e;
    cam.tz += (cam.goal[2] - cam.tz) * e;
    cam.dist += (cam.goalDist - cam.dist) * e;
    // 手机上卡片盖住下半屏：选中时把画面往上挪
    const lift = W < 760 && this.sel ? H * 0.2 : 0;
    this.shift += (lift - this.shift) * e;
    const pulse = (Math.sin(now / 520) + 1) / 2;

    // 背景：深空渐变 + 星尘（视差）
    const bg = ctx.createRadialGradient(W * 0.5, H * 0.45, 0, W * 0.5, H * 0.5, Math.max(W, H) * 0.75);
    bg.addColorStop(0, '#141b33');
    bg.addColorStop(0.55, '#0a0f1f');
    bg.addColorStop(1, '#05070f');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);
    for (const d of this.dust) {
      const p = this.project(d, W, H);
      if (p.z < 60) continue;
      const tw = 0.35 + 0.65 * ((Math.sin(now / 900 + d[3] * 40) + 1) / 2);
      ctx.fillStyle = `rgba(200,215,255,${(0.12 + d[3] * 0.35) * tw})`;
      const s = Math.max(0.4, p.k * 1.1);
      ctx.fillRect(p.x, p.y, s, s);
    }

    // 轨道环
    ctx.lineWidth = 1;
    for (const [rad, a] of [[this.orbit, 0.13], [this.orbit * 0.5, 0.07], [this.orbit * 1.55, 0.05]]) {
      ctx.beginPath();
      for (let i = 0; i <= 120; i += 1) {
        const t = (i / 120) * Math.PI * 2;
        const p = this.project([Math.cos(t) * rad, 0, Math.sin(t) * rad], W, H);
        if (i) ctx.lineTo(p.x, p.y);
        else ctx.moveTo(p.x, p.y);
      }
      ctx.strokeStyle = `rgba(160,180,255,${a})`;
      ctx.stroke();
    }

    const sel = this.sel;
    const near = new Set(sel ? [sel, ...(this.adj.get(sel) || []).map((x) => x.id)] : []);
    const hov = this.hover;
    const proj = [];
    const P = new Map();
    for (const n of this.map.nodes) {
      if (!this.visible(n)) continue;
      const p = this.project(this.pos.get(n.id), W, H);
      if (p.z < 30) continue;
      p.id = n.id;
      p.n = n;
      p.r = (KIND_R[n.kind] || 3) * (n.kind === 'star' ? 0.7 + n.value * 0.9 : 1) * p.k;
      P.set(n.id, p);
      proj.push(p);
    }
    this.proj = proj;

    // 连线
    for (const l of this.map.links) {
      const a = P.get(l.a);
      const b = P.get(l.b);
      if (!a || !b) continue;
      const lit = sel && (l.a === sel || l.b === sel);
      const dim = sel && !lit;
      const depth = clamp(1.4 - (a.z + b.z) / 2 / 1400, 0.25, 1);
      let alpha = l.kind === 'tree' ? 0.16 : l.kind === 'peer' ? 0.1 + l.w * 0.3 : 0.32;
      if (lit) alpha = 0.85;
      if (dim) alpha *= 0.25;
      const g = ctx.createLinearGradient(a.x, a.y, b.x, b.y);
      g.addColorStop(0, col(a.n.group, 72, alpha * depth));
      g.addColorStop(1, col(b.n.group, 72, alpha * depth));
      ctx.strokeStyle = g;
      ctx.lineWidth = (lit ? 1.6 : l.kind === 'peer' ? 0.6 + l.w * 1.6 : 0.8) * Math.min(1.4, (a.k + b.k) / 2);
      ctx.setLineDash(l.kind === 'relate' ? [3, 4] : []);
      ctx.beginPath();
      if (l.kind === 'peer' || l.kind === 'relate') {
        // 弧线：往中心外侧弯一点，避免和从属线重叠
        const mx = (a.x + b.x) / 2;
        const my = (a.y + b.y) / 2;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        ctx.moveTo(a.x, a.y);
        ctx.quadraticCurveTo(mx - dy * 0.18, my + dx * 0.18, b.x, b.y);
      } else {
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
      }
      ctx.stroke();
    }
    ctx.setLineDash([]);

    // 星：远的先画；名字最后一起画，按重要程度排，挤在一起时次要的不画
    proj.sort((a, b) => b.z - a.z);
    const labels = [];
    for (const p of proj) {
      const n = p.n;
      const dim = sel && !near.has(n.id);
      const depth = clamp(1.5 - p.z / 1300, 0.3, 1);
      const a = (dim ? 0.18 : 1) * depth;
      const r = p.r;
      if (n.kind === 'gap') {
        ctx.setLineDash([2.5, 2.5]);
        ctx.lineWidth = 1.2;
        ctx.strokeStyle = `rgba(255,138,128,${(0.55 + 0.4 * pulse) * a})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r + 1.5, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
      } else {
        const glow = r * (n.kind === 'core' ? 5 : n.kind === 'star' ? 4.2 : 3.6);
        const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, glow);
        const light = n.kind === 'core' ? 88 : 72;
        g.addColorStop(0, col(n.group, light, 0.55 * a * (0.5 + n.value / 2)));
        g.addColorStop(1, col(n.group, light, 0));
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(p.x, p.y, glow, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = col(n.group, n.kind === 'core' ? 92 : 84, a, 70);
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fill();
        if (n.kind === 'domain' || n.kind === 'member' || n.kind === 'core') {
          ctx.strokeStyle = col(n.group, 80, 0.45 * a);
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.arc(p.x, p.y, r + 3.5 * p.k, 0, Math.PI * 2);
          ctx.stroke();
        }
        if (n.missing) {
          ctx.setLineDash([2, 3]);
          ctx.strokeStyle = `rgba(255,138,128,${0.7 * a})`;
          ctx.beginPath();
          ctx.arc(p.x, p.y, r + 6 * p.k, 0, Math.PI * 2);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }
      if (n.fresh && !dim) {
        const ring = r + 3 + ((now / 30) % 18) * p.k;
        ctx.strokeStyle = col(n.group, 80, 0.5 * (1 - ((now / 30) % 18) / 18));
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(p.x, p.y, ring, 0, Math.PI * 2);
        ctx.stroke();
      }
      if (n.id === sel) {
        ctx.strokeStyle = 'rgba(255,255,255,.9)';
        ctx.lineWidth = 1.2;
        const s = r + 8 + pulse * 3;
        ctx.beginPath();
        for (let i = 0; i < 4; i += 1) {
          const t0 = i * (Math.PI / 2) + now / 1600;
          ctx.moveTo(p.x + Math.cos(t0) * s, p.y + Math.sin(t0) * s);
          ctx.arc(p.x, p.y, s, t0, t0 + 0.9);
        }
        ctx.stroke();
      }
      // 名字：中心、星域、成员总显示；其它星在选中 / 相关 / 悬停 / 镜头拉近时显示
      const big = n.kind === 'core' || n.kind === 'domain' || n.kind === 'member';
      const show = big || n.id === hov || (sel && near.has(n.id)) || p.k > 1.9;
      if (show && !(dim && !big)) {
        const rank = n.id === hov || n.id === sel ? 0 : n.kind === 'core' ? 1 : big ? 2 : near.has(n.id) ? 3 : 4;
        labels.push({ p, n, a, big, rank });
      }
    }
    this.labels(labels);
  }

  labels(list) {
    const ctx = this.ctx;
    const boxes = [];
    list.sort((x, y) => x.rank - y.rank || x.p.z - y.p.z);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (const { p, n, a, big, rank } of list) {
      const size = n.kind === 'core' ? 15 : big ? 12.5 : 11;
      ctx.font = `${n.kind === 'core' || n.kind === 'domain' ? 600 : 400} ${size}px "Noto Sans SC", system-ui, sans-serif`;
      const w = ctx.measureText(n.label).width;
      const ty = p.y + p.r + (big ? 7 : 4);
      const box = [p.x - w / 2 - 3, ty - 2, p.x + w / 2 + 3, ty + size + 2];
      const hit = boxes.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1]);
      if (hit && rank > 2) continue;
      boxes.push(box);
      ctx.fillStyle = `rgba(4,7,16,${0.6 * a})`;
      ctx.fillText(n.label, p.x + 0.8, ty + 0.8);
      ctx.fillStyle = n.kind === 'gap' ? `rgba(255,170,160,${a})` : big ? `rgba(240,244,255,${a})` : `rgba(205,214,240,${a})`;
      ctx.fillText(n.label, p.x, ty);
    }
  }
}
