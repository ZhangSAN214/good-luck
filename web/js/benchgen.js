// 「工作台」页的「生成器」标签（docs/KIT.md）：一句话生成一个工作台，这里是它的控制室。
// 看得到：现在走到哪一步、每个窗口谁在写、每轮质检和评审的分数与截图、找来的插件清单（搬了哪些、只借思路的有哪些）、
// 新收进零件库的零件；做好后在下面直接打开成品（沙箱里运行），成品里的对话窗经 postMessage 交给后端回答。
// 助手的名字来自超级电脑的接口（label），不写死。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const BUSY_MS = 3000;
const OPEN_MS = 10000;
const IDLE_MS = 30000;
export const STATUS = {
  planning: '正在拆需求',
  researching: '正在调研',
  scouting: '正在找插件',
  meeting: '方案会',
  building: '分头写窗口',
  checking: '质检和评审',
  reworking: '打回返工',
  done: '过线了',
  needs_you: '等你决定',
  failed: '失败',
  cancelled: '已叫停',
  interviewing: '正在准备访谈',
  looking: '正在看素材',
  waiting: '等你',
  delivered: '已交付',
  evolving: '正在出下一版提案',
};
// 选型台：某个版型下窗口的顺序和宽度（和后端 spec.py 的 layout_spans 一样算，签字后成品照这个排）
const rows = (ids, per) => {
  const out = [];
  for (let i = 0; i < ids.length; i += per) {
    const row = ids.slice(i, i + per);
    row.forEach((id) => out.push([id, 12 / row.length]));
  }
  return out;
};
export function layoutSpans(wins, hero, layout) {
  const ids = wins.map((w) => w.id);
  const h = ids.includes(hero) ? hero : ids[0];
  const rest = ids.filter((x) => x !== h);
  if (layout === 'hero-side') return rest.length ? [[h, 8], [rest[0], 4], ...rows(rest.slice(1), 3)] : [[h, 12]];
  if (layout === 'hero-wide') return [[h, 12], ...rows(rest, 3)];
  if (layout === 'dashboard') return rows([h, ...rest], 3);
  if (layout === 'workspace') return rest.length < 2 ? layoutSpans(wins, hero, 'hero-side') : [[rest[0], 3], [h, 6], [rest[1], 3], ...rows(rest.slice(2), 2)];
  if (layout === 'stream') return [h, ...rest].map((x) => [x, 12]);
  return wins.map((w) => [w.id, w.span]);
}
const SPANS = [3, 4, 6, 8, 12];
const SPAN_NAME = { 3: '窄', 4: '三分之一', 6: '一半', 8: '三分之二', 12: '通栏' };
const DESK_W = 1280;
/** 主色上的字用黑还是白（和后端 spec.py 的 _fg_for 一样算）。 */
export function fgFor(hex) {
  const lin = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const [r, g, b] = [1, 3, 5].map((i) => lin(parseInt(hex.slice(i, i + 2), 16) / 255));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.36 ? '#111418' : '#ffffff';
}
// 不改样式、只给写文案的助手看的项；其余每项都写成预览页 <html data-k-*>（和成品外壳一样）
const TEXT_ONLY = ['layout', 'theme', 'tone'];
const FRAMES = 6;
const matUrl = (bid, m) => `/bench/${encodeURIComponent(bid)}/materials/${encodeURIComponent(m.file)}`;

/** 视频在浏览器里截几帧（均匀取点），交给能看图的助手看；打不开的视频返回空。 */
export async function videoFrames(file, n = FRAMES, width = 960) {
  const url = URL.createObjectURL(file);
  const v = document.createElement('video');
  Object.assign(v, { muted: true, preload: 'auto', playsInline: true, src: url });
  const out = [];
  try {
    await new Promise((ok, bad) => {
      v.onloadeddata = ok;
      v.onerror = () => bad(new Error('打不开'));
      setTimeout(() => bad(new Error('太慢')), 15000);
    });
    const dur = Number.isFinite(v.duration) ? v.duration : 0;
    const scale = Math.min(1, width / (v.videoWidth || width));
    const c = document.createElement('canvas');
    c.width = Math.max(1, Math.round((v.videoWidth || width) * scale));
    c.height = Math.max(1, Math.round((v.videoHeight || 540) * scale));
    const base = file.name.replace(/\.[^.]+$/, '');
    for (let i = 0; i < (dur ? n : 1); i++) {
      const t = dur ? (dur * (i + 0.5)) / n : 0;
      if (t > 0) {
        await new Promise((ok) => {
          const done = setTimeout(ok, 3000);
          v.onseeked = () => (clearTimeout(done), ok());
          v.currentTime = t;
        });
      }
      c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
      const blob = await new Promise((ok) => c.toBlob(ok, 'image/jpeg', 0.82));
      if (blob) out.push(new File([blob], `${base}-第${i + 1}帧-${t.toFixed(1)}秒.jpg`, { type: 'image/jpeg' }));
    }
  } catch {
    return [];
  } finally {
    URL.revokeObjectURL(url);
  }
  return out;
}
const PHONE_W = 390;
// 一个界面里的 7 步（你是甲方，几位助手是乙方）：[键, 名字, 这一步在干什么]
export const FLOW = [
  ['idea', '说想法', '一句话，或者拖一张参考图、一件成品进来'],
  ['ask', '问取舍', '只问几个会改变做法的问题，可以全部跳过'],
  ['prep', '出样子', '调研、看素材、几位助手各提方案，出样子和计划'],
  ['sign', '你签字', '样子和计划都在这一屏，改好了签字开工'],
  ['build', '自动搭', '分段做，每段做完先给你试用'],
  ['check', '自检自修', '质检、评审、对抗手找毛病，自己先修'],
  ['deliver', '交付', '你试用验收；之后按你的意见出下一版'],
];
// 窄屏时三栏变成三屏，用这排按钮切
const INTENT_FIVE = [
  ['who', '给谁用'],
  ['where', '在哪用'],
  ['task', '要办成'],
  ['feel', '感觉'],
  ['avoid', '不要'],
];
const PANES = [
  ['client', '甲方'],
  ['stage', '成品'],
  ['plan', '计划'],
];
const PREP = ['planning', 'researching', 'scouting', 'meeting', 'looking'];

/** 项目走到 7 步里的第几步（从 0 数）。 */
export function flowIndex(b) {
  const kind = (b.gate || {}).kind || '';
  if (kind === 'interview' || b.status === 'interviewing') return 1;
  if (kind === 'sign') return 3;
  if (/^m\d+$/.test(kind)) return 4;
  if (kind === 'accept') return 6;
  if (['done', 'needs_you', 'delivered', 'evolving'].includes(b.status)) return 6;
  if (b.status === 'checking') return 5;
  if (['building', 'reworking'].includes(b.status)) return 4;
  if (PREP.includes(b.status)) return 2;
  // 失败或叫停：停在当时做到的那一步
  if ((b.rounds || []).length) return 5;
  if ((b.windows || []).length) return 4;
  return 2;
}

/** 项目墙缩略图：接口给的，或者从最近一轮的截图里挑电脑浅色那张（和后端 thumb_of 一样）。 */
export function thumbOf(x) {
  if (x.thumb) return x.thumb;
  for (const r of [...(x.rounds && Array.isArray(x.rounds) ? x.rounds : [])].reverse()) {
    const shots = ((r.check || {}).shots || []).map((p) => String(p).split(/[\\/]/).pop());
    const desk = shots.filter((n) => n.startsWith('电脑'));
    const list = desk.length ? desk : shots;
    if (list.length) return `report/round-${r.n}/${list.find((n) => n.includes('light')) || list[0]}`;
  }
  return null;
}

/** 从开工到现在（做完的到做完那刻）用了几分钟；时间不对时不显示。 */
export function elapsedMin(b, now = Date.now()) {
  const t0 = Date.parse(b.created_at || '');
  if (!Number.isFinite(t0)) return null;
  const t1 = b.finished_at ? Date.parse(b.finished_at) : now;
  const m = Math.round((t1 - t0) / 60000);
  return Number.isFinite(m) && m >= 0 ? m : null;
}
const WSTATUS = { waiting: '等着', running: '在写', done: '写好了', failed: '没写成' };
const VERDICT = { adopt: '直接用', idea: '只借思路', skip: '不用' };
const NEWS = { news: '资讯', video: '视频', launch: '发布会', blog: '官方博客' };
const CHECKED = { true: '', false: '（链接打不开，没核实）', null: '（没核实）' };
const SEVERITY = { high: '严重', mid: '一般', low: '小' };
const SHOT = { 电脑: '电脑', 平板: '平板', 手机: '手机', light: '浅色', dark: '深色' };

/** 截图文件名（如「手机_dark.png」）→「手机 · 深色」。 */
export function shotLabel(path) {
  const name = String(path).split(/[\\/]/).pop().replace(/\.png$/i, '');
  return name
    .split('_')
    .map((p) => SHOT[p] || p)
    .join(' · ');
}

/** 一轮的截图在成品文件夹里的地址：/bench/<编号>/report/round-<n>/<文件名>。 */
export function shotUrl(bid, n, path) {
  const name = String(path).split(/[\\/]/).pop();
  return `/bench/${encodeURIComponent(bid)}/report/round-${n}/${encodeURIComponent(name)}`;
}

export class BenchGenPanel {
  /**
   * @param {HTMLElement} el 画在哪里（「工作台」页的一块）
   * @param {{api: Record<string, Function>, isOpen: () => boolean, label?: (id: string) => string}} opts
   */
  constructor(el, { api, isOpen, label = (id) => id }) {
    Object.assign(this, { el, api, isOpen, label });
    this.st = null;
    this.viewing = null;
    this.composing = false; // 点了「新项目」：左边写想法，中间是项目墙
    this.device = 'desk'; // 中间成品按电脑还是手机宽度看
    this.drawer = ''; // 底部抽屉开着哪一格（空 = 收起）
    this.desk = null; // 资料台（GitHub 榜单 / 动态 / 能力地图 / 建议卡）
    this.deskTab = 'rank';
    this.deskCat = '';
    this.deskMode = 'total';
    this.deskKind = '';
    this.deskAsked = false; // 这次打开页面已经叫过一次刷新
    this.fromProduct = false; // 拖进来的是成品：要能做出类似东西的台
    this.form = {}; // 按表填的需求（字段清单来自后端 form_fields）
    this.pane = 'client'; // 窄屏时显示哪一栏
    this.err = '';
    this.draft = '';
    this.timer = null;
    this.net = null; // 联网自检的结果（null 没测过，'busy' 正在测）
    this.dirty = false; // 项目室的表单里填了还没交：定时刷新不重画，免得把你写的冲掉
    this.openGroups = new Set(['结构', '色彩']); // 选型台上展开着的分组（重画时保持）
    this.mats = []; // 开工前拖进来的素材（已上传、还没开工）：{id, name, kind, of, thumb}
    this.uploading = 0;
    this.pick = null; // 选型台上点的（版型、配色、材质……、参考、窗口宽窄和去掉的），签字时一起交
    this.icons = null; // 预览里画图标用（零件库的 icons.js，第一次打开选型台时加载）
    this.seq = 0; // 每次取状态加一：你点了按钮之后，之前发出去的定时刷新回来也不用（是旧的）
    el.addEventListener('click', (e) => this.onClick(e));
    // 抽屉开着时按 Esc 收起
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.drawer && this.isOpen()) {
        this.drawer = '';
        this.render(true);
      }
    });
    el.addEventListener('input', (e) => {
      if (e.target.matches('[data-text]')) this.draft = e.target.value;
      if (e.target.matches('[data-ff]')) this.form[e.target.dataset.ff] = e.target.value;
      if (e.target.closest('[data-form]')) this.dirty = true;
    });
    el.addEventListener('change', (e) => {
      const x = e.target;
      if (x.matches('[data-switch]')) return this.switchTo(x.value);
      if (x.matches('select[data-ff]')) this.form[x.dataset.ff] = x.value;
      if (!this.pick) return;
      if (x.matches('[data-dref]')) this.pick.refs[x.checked ? 'add' : 'delete'](x.dataset.dref);
      else if (x.matches('[data-dspan]')) {
        if (x.value) this.pick.spans[x.dataset.dspan] = Number(x.value);
        else delete this.pick.spans[x.dataset.dspan];
      } else if (x.matches('[data-ddrop]')) this.pick.drop[x.checked ? 'delete' : 'add'](x.dataset.ddrop);
      else if (x.matches('[data-drole]')) this.pick.assets[x.dataset.drole] = x.value;
      else return;
      this.redrawDesign();
    });
    // details 的 toggle 不冒泡，用捕获记下你展开 / 收起了哪组
    el.addEventListener(
      'toggle',
      (e) => {
        const g = e.target.dataset && e.target.dataset.dgroup;
        if (g) this.openGroups[e.target.open ? 'add' : 'delete'](g);
      },
      true,
    );
    el.addEventListener('dragover', (e) => {
      const zone = e.target.closest('[data-drop]');
      if (!zone) return;
      e.preventDefault();
      zone.classList.add('bg-over');
    });
    el.addEventListener('dragleave', (e) => e.target.closest('[data-drop]')?.classList.remove('bg-over'));
    el.addEventListener('drop', (e) => {
      const zone = e.target.closest('[data-drop]');
      if (!zone) return;
      e.preventDefault();
      zone.classList.remove('bg-over');
      this.addFiles([...(e.dataTransfer?.files || [])], zone.dataset.drop);
    });
    // 选文件的框放在面板外面：定时刷新会重画面板，挑文件挑得慢也不会丢
    this.fileInput = Object.assign(document.createElement('input'), { type: 'file', multiple: true, accept: 'image/*,video/*', hidden: true });
    this.fileInput.dataset.benchfile = 'submit';
    this.fileInput.addEventListener('change', () => {
      this.addFiles([...this.fileInput.files], this.fileInput.dataset.benchfile);
      this.fileInput.value = '';
    });
    document.body.append(this.fileInput);
    el.addEventListener('input', (e) => {
      if (e.target.matches('[data-dnote]') && this.pick) this.pick.note = e.target.value;
      if (e.target.matches('[data-daccent]') && this.pick) {
        this.pick.accent = e.target.value;
        this.drawPreview();
      }
    });
    this.ro = typeof ResizeObserver === 'function' ? new ResizeObserver(() => this.fitPreview()) : null;
    window.addEventListener('message', (e) => this.onMessage(e));
    this.refresh();
    this.schedule();
  }

  schedule() {
    clearTimeout(this.timer);
    const ms = this.st && this.st.busy ? BUSY_MS : this.isOpen() ? OPEN_MS : IDLE_MS;
    this.timer = setTimeout(() => this.refresh().finally(() => this.schedule()), ms);
  }

  async refresh() {
    const seq = ++this.seq;
    let st;
    try {
      st = await this.api.status();
    } catch {
      return;
    }
    if (seq !== this.seq) return;
    this.st = st;
    // 状态没变就不重画：重画会换掉按钮，正巧在你按下按钮时换掉，这一下就点空了
    const key = JSON.stringify(st);
    if (key === this.shown) return;
    // 各栏自己判断：正在打字、填了还没交的那一栏先不动；成品 iframe 内容没变就不换
    if (this.isOpen()) {
      this.shown = key;
      this.render();
    }
  }

  async open() {
    this.err = '';
    const seq = ++this.seq;
    let st = null;
    try {
      st = await this.api.status();
    } catch (e) {
      this.err = e.message;
    }
    // 取状态这段时间里你点了按钮：以那次的结果为准，这份旧的不用
    if (seq !== this.seq) return this.schedule();
    if (st) this.st = st;
    const key = JSON.stringify(this.st);
    if (key !== this.shown || this.err || !this.el.firstChild) {
      this.shown = key;
      this.render(true);
    }
    this.schedule();
  }

  /** 成品里的对话窗问问题：只认我们自己嵌进来的那个成品，编号以我们这边为准。 */
  async onMessage(e) {
    const frame = this.el.querySelector('iframe[data-preview]');
    if (!frame || e.source !== frame.contentWindow) return;
    const d = e.data || {};
    if (d.type !== 'bench-ask' || typeof d.id !== 'string') return;
    let text, data = null;
    try {
      ({ text, data = null } = await this.api.ask(frame.dataset.preview, String(d.q || ''), !!d.json));
    } catch (x) {
      text = `没问成：${x.message}`;
    }
    e.source.postMessage({ type: 'bench-answer', id: d.id, text, data }, '*');
  }

  /** 现在看的是哪个项目：切到别的项目时看那个，正在新开一个时不看任何项目。 */
  shownBuild() {
    if (this.composing) return null;
    return this.viewing || (this.st || {}).current || null;
  }

  /**
   * 一个界面画完全部（不跳页、不往下钻）：顶栏、左甲方、中成品、右乙方、底部抽屉。
   * 每块单独比较，内容没变就不动它（成品 iframe 不会被刷掉）；正在打字的那块不重画；
   * force：你刚点了按钮，全部按新状态画。
   */
  render(force = false) {
    const st = this.st || {};
    const b = this.shownBuild();
    if (!this.el.querySelector('.bg-app')) {
      this.el.innerHTML = `<div class="bg bg-app">
        <header class="bg-top" data-region="top"></header>
        <nav class="bg-panes" aria-label="切换栏目">${PANES.map(([k, t]) => `<button type="button" data-pane-go="${k}" aria-pressed="${this.pane === k}">${t}</button>`).join('')}</nav>
        <div class="bg-cols" data-showpane="${this.pane}">
          <section class="bg-col bg-client" data-region="client" aria-label="甲方（你）"></section>
          <section class="bg-col bg-stage" data-region="stage" aria-label="成品"></section>
          <section class="bg-col bg-plan" data-region="plan" aria-label="乙方（计划）"></section>
        </div>
        <div class="bg-drawer" data-region="drawer"></div>
      </div>`;
      force = true;
    }
    const app = this.el.querySelector('.bg-app');
    app.dataset.step = b ? FLOW[flowIndex(b)][0] : 'idea';
    this.el.querySelector('.bg-cols').dataset.showpane = this.pane;
    this.paint('top', this.topHtml(st, b), force);
    this.paint('client', this.clientHtml(st, b), force);
    this.paint('stage', this.stageHtml(st, b), force);
    this.paint('plan', this.planHtml(st, b), force);
    this.paint('drawer', this.drawerHtml(st, b), force);
    if (this.el.querySelector('[data-dpreview]')) this.drawPreview();
  }

  /** 只在内容变了时换掉这一块；你正在这块里打字、或填了还没交，就先不动（force 除外）。 */
  paint(name, html, force) {
    const box = this.el.querySelector(`[data-region="${name}"]`);
    if (!box || box.dataset.html === html) return;
    if (!force) {
      const typing = box.contains(document.activeElement?.closest('input,textarea,select') || null);
      if (typing || (this.dirty && box.querySelector('[data-form]'))) return;
    }
    const keep = box.scrollTop;
    box.innerHTML = html;
    box.dataset.html = html;
    box.scrollTop = keep;
  }

  /** 顶栏：项目切换、状态、走到第几步（进度条）、用时和次数、叫停 / 新项目。 */
  topHtml(st, b) {
    const busy = !!st.busy;
    const recent = st.recent || [];
    const cur = st.current;
    const opts = [];
    if (cur) opts.push(`<option value="${esc(cur.id)}" ${b && b.id === cur.id ? 'selected' : ''}>${esc(cur.title || (cur.request || '').slice(0, 18) || '当前项目')}（当前）</option>`);
    for (const x of recent) {
      if (cur && x.id === cur.id) continue;
      opts.push(`<option value="${esc(x.id)}" ${b && b.id === x.id ? 'selected' : ''}>${esc(x.title || (x.request || '').slice(0, 18))} · ${esc(STATUS[x.status] || x.status)}</option>`);
    }
    if (!b) opts.unshift('<option value="" selected>新项目</option>');
    const sw = opts.length > 1 || b ? `<select class="bg-switch" data-switch aria-label="切换项目">${opts.join('')}</select>` : '<span class="bg-switch-new">新项目</span>';
    const at = b ? flowIndex(b) : 0;
    const stopped = b && ['failed', 'cancelled'].includes(b.status);
    const pct = b ? (b.status === 'delivered' || b.status === 'done' ? 100 : Math.round((at / (FLOW.length - 1)) * 100)) : 0;
    const meta = [];
    if (b) {
      const used = ((b.team || {}).cards || []).length;
      const mins = elapsedMin(b);
      if (mins != null) meta.push(`用时 ${mins} 分`);
      if (used) meta.push(`已用 ${used} 次`);
      if ((b.rounds || []).length) meta.push(`第 ${b.rounds.length} 轮`);
      if (b.score != null) meta.push(`${esc(b.score)} 分`);
    }
    const state = b
      ? `<span class="bg-status bg-${esc(b.status)}">${esc(STATUS[b.status] || b.status)}</span>`
      : `<span class="bg-status">${busy ? '生成中' : '空闲'}</span>`;
    const actions = [
      `<button type="button" class="btn" data-netcheck ${this.net === 'busy' ? 'disabled' : ''}>${this.net === 'busy' ? '正在自检…' : '联网自检'}</button>`,
      busy ? '<button type="button" class="btn" data-cancel>叫停</button>' : '',
      b && !busy ? '<button type="button" class="btn" data-new>新项目</button>' : '',
    ].join('');
    return `<div class="bg-top-l"><b class="bg-title">${esc(b ? b.title || (b.request || '').slice(0, 16) || '还没起名' : '生成器')}</b>${state}${sw}</div>
      <div class="bg-top-m" ${b ? '' : 'hidden'}>
        <div class="bg-prog-l"><span>第 ${at + 1}/${FLOW.length} 步 · ${esc(FLOW[at][1])}${stopped ? '（停了）' : ''}</span><span class="bg-meta">${meta.map(esc).join(' · ')}</span></div>
        <div class="bg-prog" role="progressbar" aria-label="进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></div>
      </div>
      <div class="bg-top-r">${actions}</div>`;
  }

  /** 左栏「甲方」：说想法（新项目）；你说过的话和素材；现在等你做的事；变更单。 */
  clientHtml(st, b) {
    const busy = !!st.busy;
    const notes = [];
    if (st.unavailable) notes.push(`<p class="sc-warn">${esc(st.unavailable)}</p>`);
    if (this.err) notes.push(`<p class="sc-warn" role="alert">${esc(this.err)}</p>`);
    if (!b) return `<h3 class="bg-h">说想法</h3>${notes.join('')}${this.askHtml(st)}`;
    const req = `<div class="bg-said"><span class="bg-k">你说的</span><p>${esc((b.request || '').slice(0, 400))}</p></div>`;
    const mats = this.matsStrip(b);
    const changes = (b.trace || []).length ? '' : (b.changes || [])
      .map(
        (c) => `<li class="bg-ch-${esc(c.status)}"><b>${esc(c.id)}</b> ${esc(c.text)}${c.window ? `（${esc(this.winTitle(b, c.window))}）` : ''} · ${esc({ open: '处理中', done: '改完了，等你看', accepted: '改好了', failed: '没改成' }[c.status] || c.status)}${c.reply ? `<div class="sc-sub">${esc(c.reply.slice(-160))}</div>` : ''}</li>`,
      )
      .join('');
    const stuck = b.stuck && b.status === 'needs_you' ? this.stuckForm(b) : '';
    const evolve = b.status === 'delivered' ? this.evolveHtml(b) : '';
    const old = b.status === 'done' && !b.timeline ? '<p class="sc-hint">做好了。中间直接试用；有不满意的地方，新开一个项目把意见写进去。</p>' : '';
    const waiting = !b.gate && !stuck && !evolve && !old && busy ? '<p class="sc-hint bg-quiet">正在做，不用管。中间会实时刷新；需要你时这里会出现要做的事。</p>' : '';
    return `<h3 class="bg-h">甲方 · 你</h3>${notes.join('')}${req}${mats}
      ${this.gateHtml(b)}${stuck}${evolve}${old}${waiting}
      ${this.hasProduct(b) || (b.gate || {}).kind === 'sign' ? this.intentHtml(b) : ''}${this.traceHtml(b)}
      ${changes ? `<h4 class="bg-h4">变更单（${(b.changes || []).length} 张）</h4><ul class="bg-list bg-changes">${changes}</ul>` : ''}`;
  }

  /** 理想画像：一句话理想 + 五要素（每条标把握：确定 / 推断 / 假设），隐含期待和假设收起。 */
  intentHtml(b) {
    const it = b.intent;
    if (!it) return '';
    const lv = (x) => `<span class="bg-lv" data-lv="${esc(x.level)}">${esc(x.level)}</span>`;
    const rows = INTENT_FIVE.filter(([k]) => it[k])
      .map(([k, label]) => `<li><span class="bg-k">${esc(label)}</span><span>${esc(it[k].text)}</span>${lv(it[k])}</li>`)
      .join('');
    const more = [...(it.expect || []).map((x) => `<li>${esc(x.text)}<span class="sc-sub">来自${esc(x.from)}</span>${lv(x)}</li>`), ...(it.assume || []).map((x) => `<li>${esc(x.text)}${lv(x)}</li>`)].join('');
    const n = (it.expect || []).length + (it.assume || []).length;
    return `<section class="bg-intent" aria-label="理想画像"><h4 class="bg-h4">理想画像</h4>
      ${it.ideal ? `<p class="bg-ideal">${esc(it.ideal)}</p>` : ''}
      ${rows ? `<ul class="bg-five">${rows}</ul>` : ''}
      ${n ? `<details class="bg-fold"><summary>隐含期待和假设（${n} 条，假设会在第 1 段请你确认）</summary><ul class="bg-five bg-more">${more}</ul></details>` : ''}</section>`;
  }

  /** 意图追踪表：你说的每句话去了哪一段、对上哪几条验收、现在什么状态。 */
  traceHtml(b) {
    const rows = b.trace || [];
    if (!rows.length) return '';
    const changes = Object.fromEntries((b.changes || []).map((c) => [c.id, c]));
    const voices = Object.fromEntries((b.voices || []).map((v) => [v.id, v]));
    const open = rows.filter((r) => ['没做到', '没改成', '没对上', '待你看'].includes(r.state)).length;
    const kept = new Set(((this.st || {}).profile || []).map((x) => x.text));
    const items = rows
      .map((r) => {
        const v = voices[r.id] || {};
        const c = r.src === '变更单' ? changes[v.ref] : null;
        const where = [r.stage ? `第 ${r.stage} 段` : '', (r.reqs || []).join(' ')].filter(Boolean).join(' · ');
        return `<li data-state="${esc(r.state)}"><span class="bg-tr-st">${esc(r.state)}</span><span class="bg-tr-tx"><span class="bg-k">${esc(r.src)}</span>${esc(r.text.slice(0, 120))}${where ? `<span class="sc-sub">${esc(where)}</span>` : ''}${c && c.reply ? `<span class="sc-sub">${esc(c.reply.slice(-140))}</span>` : ''}</span>${kept.has(r.text.slice(0, 200).replace(/\s+/g, ' ').trim()) ? '<span class="bg-src">记住了</span>' : ['访谈', '签字', '变更单', '风格'].includes(r.src) ? `<button type="button" class="link bg-remember" data-remember="${esc(r.text.slice(0, 200))}" data-src="${esc(b.title || b.id)}" title="以后每个新项目都带上这句">记住</button>` : ''}</li>`;
      })
      .join('');
    return `<details class="bg-fold bg-trace" ${open ? 'open' : ''}><summary>你说的话去哪了（${rows.length} 句${open ? `，${open} 句要你看` : ''}）</summary><ul class="bg-trlist">${items}</ul></details>`;
  }

  askHtml(st) {
    const busy = !!st.busy;
    const mats = this.mats
      .filter((m) => !m.of)
      .map((m) => {
        const frames = this.mats.filter((x) => x.of === m.id).length;
        return `<li class="bg-mat">${m.thumb ? `<img src="${esc(m.thumb)}" alt="">` : ''}<span>${esc(m.name)}${m.kind === 'video' ? `（视频，截了 ${frames} 帧）` : ''}</span><button type="button" class="link" data-unmat="${esc(m.id)}" aria-label="去掉 ${esc(m.name)}">去掉</button></li>`;
      })
      .join('');
    const drop = `<div class="bg-drop" data-drop="submit"><span>参考图、视频拖到这里</span><button type="button" class="link" data-pick="submit">选文件</button>
      ${this.uploading ? `<span class="sc-sub">正在上传 ${this.uploading} 个…</span>` : ''}${mats ? `<ul class="bg-mats">${mats}</ul>` : ''}</div>
      ${mats ? `<label class="bg-fromprod"><input type="checkbox" data-fromprod ${this.fromProduct ? 'checked' : ''}><span><b>这是成品，照着它做一个台</b><span class="sc-sub">先拆出哪些固定（画风、尺寸、节奏）、哪些能换（角色、动作、文字），做成能批量出类似东西的台</span></span></label>` : ''}`;
    return `<div class="sc-ask bg-ask"><textarea data-text rows="5" aria-label="想要什么样的工作台" placeholder="一句话说你想要什么台。比如：管理我租的三套房子，看租金、到期、维修和水电" ${busy ? 'disabled' : ''}>${esc(this.draft)}</textarea>
      ${busy ? '<p class="sc-hint">上一个还在做，做完或叫停后才能开新的。</p>' : drop}
      <button type="button" class="btn primary bg-go" data-submit ${busy || st.unavailable ? 'disabled' : ''}>生成工作台</button>
      ${this.formHtml(st)}${this.profileHtml(st)}
      <p class="sc-hint">${st.project ? '先问你几个取舍，再出样子和计划给你签字；之后自动搭，每段做完给你试用。停下来等你时不花次数。' : `自动拆需求、分头写、质检和评审，不过线就返工，最多 ${esc(st.max_rounds || 3)} 轮。`}花的是订阅次数。</p></div>`;
  }

  /** 结构化需求表单（可选）：四组固定字段，清单来自后端，拼成文字也在后端（同一个构建器）。 */
  formHtml(st) {
    const fields = st.form_fields || [];
    if (!fields.length) return '';
    const filled = fields.filter((f) => (this.form[f.id] || '').trim() && !(f.options && this.form[f.id] === f.options[0])).length;
    let group = '';
    const rows = fields
      .map((f) => {
        const head = f.group !== group ? `<h5 class="bg-ffg">${esc((group = f.group))}</h5>` : '';
        const v = this.form[f.id] || '';
        const input = f.options
          ? `<select data-ff="${esc(f.id)}">${f.options.map((o) => `<option ${o === v ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`
          : f.id === 'must'
            ? `<textarea data-ff="${esc(f.id)}" rows="2" maxlength="400" placeholder="${esc(f.hint || '')}">${esc(v)}</textarea>`
            : `<input type="text" data-ff="${esc(f.id)}" maxlength="400" value="${esc(v)}" placeholder="${esc(f.hint || '')}">`;
        return `${head}<label class="bg-ff"><span>${esc(f.label)}</span>${input}</label>`;
      })
      .join('');
    return `<details class="bg-fold bg-form"${filled ? ' open' : ''}><summary>按表填（可选，填了更准${filled ? `，已填 ${filled} 格` : ''}）</summary><div class="bg-ffs">${rows}</div></details>`;
  }

  /** 一卡一会话：这张卡最近一次会话是不是假完成（说做好了，文件没动）。 */
  fakeNote(w) {
    const runs = w.runs || [];
    const fakes = runs.filter((r) => r.fake).length;
    if (!fakes) return '';
    const last = runs[runs.length - 1];
    return `<span class="bg-fake" title="助手说做好了，但文件一个字没动，不算数">${last.fake ? '最近一次是假完成' : `有过 ${fakes} 次假完成`}</span>`;
  }

  /** 甲方档案：你的长期偏好，每个新项目都带上（只存本机，可删）。 */
  profileHtml(st) {
    const items = st.profile || [];
    const rows = items.map((x) => `<li><span>${esc(x.text)}</span>${x.src ? `<span class="bg-src">${esc(x.src)}</span>` : ''}<button type="button" class="link" data-unprofile="${esc(x.id)}" aria-label="删掉：${esc(x.text)}">删掉</button></li>`).join('');
    return `<details class="bg-fold bg-profile"${items.length ? '' : ' open'}><summary>你的偏好（${items.length} 条，每个新项目都会带上）</summary>
      ${rows ? `<ul class="bg-list bg-prof">${rows}</ul>` : '<p class="sc-hint">还没有。比如「给小白用，别堆专业参数」「深色、不要表情符号」。项目里你说过的话也能点「记住」。</p>'}
      <div class="bg-profadd"><input type="text" data-profile-text maxlength="200" aria-label="写一条偏好" placeholder="写一条偏好"><button type="button" class="btn" data-profile-add>记下</button></div></details>`;
  }

  /** 素材缩略图一排（你拖进来的图和视频）。 */
  matsStrip(b) {
    const all = ((b.proposal || {}).design || {}).materials || b.materials || [];
    const items = all.filter((m) => m.kind !== 'frame').slice(0, 8);
    if (!items.length) return '';
    const thumbs = items
      .map((m) => {
        const frame = all.find((x) => x.of === m.id);
        const src = m.kind === 'video' ? (frame ? matUrl(b.id, frame) : '') : matUrl(b.id, m);
        return src ? `<img src="${esc(src)}" alt="${esc(m.name)}" title="${esc(m.name)}" loading="lazy">` : '';
      })
      .join('');
    return thumbs ? `<div class="bg-thumbs" aria-label="素材">${thumbs}</div>` : '';
  }

  /** 中栏「成品」：新项目时是项目墙；签字前是样子的实时预览；之后一直是真的成品（草稿也能看）。 */
  stageHtml(st, b) {
    if (!b) return this.wallHtml(st);
    const g = b.gate || {};
    if (g.kind === 'sign' && ((b.proposal || {}).design || {}).dims) {
      const pick = this.ensurePick(b);
      return `<div class="bg-stage-bar">${this.viewBar(pick)}</div>
        <div class="bg-design-view"><div class="bg-dframe" data-dframe><iframe data-dpreview title="工作台预览" srcdoc="${esc('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><link rel="stylesheet" href="/kit/kit.css"></head><body class="k-root"></body></html>')}"></iframe></div></div>
        <p class="sc-hint bg-cap">样子的实时预览：左边改一项，这里马上变，不花次数。窗口里的内容开工后才写。</p>`;
    }
    if (this.hasProduct(b)) {
      const finished = ['done', 'needs_you', 'delivered'].includes(b.status) || g.kind === 'accept';
      const ver = `${(b.rounds || []).length}-${(b.windows || []).filter((w) => w.status === 'done').length}-${(b.changes || []).filter((c) => c.status === 'done').length}`;
      const dev = this.device === 'phone' ? 'phone' : 'desk';
      const seg = [['desk', '电脑'], ['phone', '手机']].map(([v, t]) => `<button type="button" class="chip" data-device="${v}" aria-pressed="${dev === v}">${t}</button>`).join('');
      return `<div class="bg-stage-bar"><span class="bg-k">${finished ? '成品' : '草稿（还在改）'}</span><span class="sc-gap"></span>${seg}
          <a class="link" href="/bench/${encodeURIComponent(b.id)}/index.html" target="_blank" rel="noopener">在新窗口打开</a></div>
        <div class="bg-shell" data-device="${dev}"><iframe class="bg-frame" data-preview="${esc(b.id)}" title="${esc(b.title || '成品')}" sandbox="allow-scripts allow-forms allow-popups allow-downloads" src="/bench/${encodeURIComponent(b.id)}/index.html?v=${esc(ver)}"></iframe></div>`;
    }
    // 还没有能看的：有理想画像就把它放大摆在中间（看得见的复述），否则说清楚现在在干什么
    const log = (b.log || []).slice(-6).reverse().map((l) => `<li><span class="bg-src">${esc((l.at || '').slice(11, 16))}</span> ${esc(l.text)}</li>`).join('');
    if (b.intent) return this.idealPage(b, log);
    return `<div class="bg-empty"><p class="bg-empty-h">${esc(STATUS[b.status] || b.status)}</p>
      <p class="sc-hint">${esc(FLOW[flowIndex(b)][2])}</p>${log ? `<ol class="bg-live" aria-label="最近在做的">${log}</ol>` : ''}</div>`;
  }

  /** 成品或草稿已经能看了吗（中间放 iframe）。 */
  hasProduct(b) {
    const g = b.gate || {};
    if (g.kind === 'sign' && ((b.proposal || {}).design || {}).dims) return false;
    const finished = ['done', 'needs_you', 'delivered'].includes(b.status) || g.kind === 'accept';
    return finished || (b.windows || []).some((w) => w.status === 'done');
  }

  /** 中间的理想画像页：一句话理想、五要素（标把握）、隐含期待和假设；底下滚动最近在做的。 */
  idealPage(b, log) {
    const it = b.intent;
    const lv = (x) => `<span class="bg-lv" data-lv="${esc(x.level)}">${esc(x.level)}</span>`;
    const tiles = INTENT_FIVE.map(([k, label]) => `<div class="bg-tile${it[k] ? '' : ' none'}"><span class="bg-k">${esc(label)}</span><p>${esc(it[k] ? it[k].text : '还不知道')}</p>${it[k] ? lv(it[k]) : ''}</div>`).join('');
    const ex = (it.expect || []).map((x) => `<li>${esc(x.text)}<span class="sc-sub">来自${esc(x.from)}</span></li>`).join('');
    const as = (it.assume || []).map((x) => `<li>${esc(x.text)}</li>`).join('');
    return `<div class="bg-idealpage" aria-label="理想画像">
      <span class="bg-k">理想画像 · 我们理解的你想要的</span>
      <p class="bg-idealbig">${esc(it.ideal || b.request || '')}</p>
      <div class="bg-tiles">${tiles}</div>
      <div class="bg-idealcols">
        ${ex ? `<section><h4 class="bg-h4">理所当然要有的</h4><ul class="bg-list">${ex}</ul></section>` : ''}
        ${as ? `<section><h4 class="bg-h4">先替你定的（第 1 段演示时请你确认）</h4><ul class="bg-list">${as}</ul></section>` : ''}
      </div>
      ${log ? `<ol class="bg-live" aria-label="最近在做的">${log}</ol>` : ''}</div>`;
  }

  /** 项目墙：最近的项目，每个一张缩略图 + 状态。 */
  wallHtml(st) {
    const items = [st.current, ...(st.recent || [])].filter(Boolean);
    const seen = new Set();
    const cards = items
      .filter((x) => !seen.has(x.id) && seen.add(x.id))
      .slice(0, 9)
      .map(
        (x) => `<button type="button" class="bg-wall-i" data-view="${esc(x.id)}">
          ${thumbOf(x) ? `<img src="/bench/${encodeURIComponent(x.id)}/${String(thumbOf(x)).split('/').map(encodeURIComponent).join('/')}" alt="" loading="lazy">` : '<span class="bg-wall-ph" aria-hidden="true"></span>'}
          <span class="bg-wall-t">${esc(x.title || (x.request || '').slice(0, 24))}</span>
          <span class="bg-wall-s"><i class="bg-dot bg-dot-${esc(x.status)}"></i>${esc(STATUS[x.status] || x.status)}${x.score != null ? ` · ${esc(x.score)} 分` : ''}</span></button>`,
      )
      .join('');
    return cards
      ? `<div class="bg-stage-bar"><span class="bg-k">最近的项目</span></div><div class="bg-wall">${cards}</div>`
      : '<div class="bg-empty"><p class="bg-empty-h">还没有项目</p><p class="sc-hint">左边写一句话，或者拖一张你喜欢的成品截图进来。</p></div>';
  }

  /** 右栏「乙方」：7 步走到哪；签字前是方案；开工后是分段、谁在写什么、最近一轮的结果。 */
  planHtml(st, b) {
    const at = b ? flowIndex(b) : -1;
    const stopped = b && ['failed', 'cancelled', 'needs_you'].includes(b.status);
    const flow = FLOW.map(([key, name, hint], i) => {
      const cls = !b ? '' : i < at || (i === at && ['delivered', 'done'].includes(b.status)) ? 'past' : i === at ? (stopped ? 'stop' : 'now') : '';
      return `<li class="${cls}" data-flow="${key}"><b>${esc(name)}</b>${!b || i === at ? `<span class="sc-sub">${esc(hint)}</span>` : ''}</li>`;
    }).join('');
    if (!b) return `<h3 class="bg-h">乙方 · 怎么做</h3><ol class="bg-flow" aria-label="步骤">${flow}</ol>`;
    const g = b.gate || {};
    const body = [];
    if (g.kind === 'sign') body.push(this.proposalHtml(b));
    else {
      body.push(this.milestonesHtml(b));
      const wins = (b.windows || [])
        .map(
          (w) => `<tr><td>${esc(w.title)}</td><td>${esc(w.author ? this.label(w.author) : '—')}</td>
            <td class="sc-st-${esc(w.status)}">${esc(WSTATUS[w.status] || w.status)}${(w.rework || []).length ? `（返工 ${w.rework.length} 次）` : ''}${this.fakeNote(w)}</td></tr>`,
        )
        .join('');
      if (wins) body.push(`<h4 class="bg-h4">分工</h4><table class="sc-subs bg-wintable"><thead><tr><th>窗口</th><th>谁写</th><th>状态</th></tr></thead><tbody>${wins}</tbody></table>`);
      body.push(this.lastRoundHtml(b));
      if (b.stuck) body.push(this.stuckInfo(b));
    }
    return `<h3 class="bg-h">乙方 · 计划</h3><ol class="bg-flow bg-steps" aria-label="进度">${flow}</ol>${body.join('')}`;
  }

  /** 签字前的方案：窗口、联动、共用信息、分段、方案会、借鉴的项目（每块默认收起，只露一句）。 */
  proposalHtml(b) {
    const p = b.proposal || {};
    const fold = (title, inner, open = false) => (inner ? `<details class="bg-fold" ${open ? 'open' : ''}><summary>${title}</summary>${inner}</details>` : '');
    const wins = (p.windows || [])
      .map(
        (w) => `<li><details><summary><b>${esc(w.title)}</b>${w.id === p.hero ? ' （主视觉）' : ''}：${esc(w.purpose)}</summary>
          ${w.data ? `<p><b>数据</b>：${esc(w.data)}</p>` : ''}
          ${(w.actions || []).length ? `<p><b>能做的事</b>：${esc(w.actions.join('、'))}</p>` : ''}
          ${(w.parts || []).length ? `<p><b>用到的零件</b>：${esc(w.parts.join('、'))}</p>` : ''}</details></li>`,
      )
      .join('');
    const list = (xs) => ((xs || []).length ? `<ul class="bg-list">${xs.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '');
    const ms = (p.milestones || []).map((m) => this.stageItem(m, true)).join('');
    const plugins = (p.plugins || []).length ? `<p class="sc-hint">建议给 dsh 装的插件：${esc(p.plugins.join('、'))}（底部「调研」里点「申请装上」）</p>` : '';
    return `<h4 class="bg-h4">方案</h4>
      ${p.kind_label && p.kind !== 'other' ? `<p class="sc-hint bg-kind">按「${esc(p.kind_label.split('（')[0])}」的做法做</p>` : ''}
      ${ms ? `<ol class="bg-flow bg-ms bg-stages">${ms}</ol>` : ''}
      ${fold(`建议池（你没要，这次不做，${(p.pool || []).length} 条）`, list(p.pool))}
      ${fold(`窗口（${(p.windows || []).length} 个）`, wins ? `<ul class="bg-list bg-wins">${wins}</ul>` : '', true)}
      ${fold('窗口之间怎么联动', list(p.links))}
      ${fold('共用信息', list(p.shared))}
      ${fold('方案会：谁提了什么、为什么采纳', list(p.meeting))}
      ${fold('借鉴的项目', list(p.tech))}
      ${plugins}
      <a class="link" href="/bench/${encodeURIComponent(b.id)}/${encodeURIComponent('方案书.md')}" target="_blank" rel="noopener">方案书全文</a>`;
  }

  /** 分段（里程碑）：每段做到哪；现在这段的演示截图（改之前 / 改之后）。 */
  milestonesHtml(b) {
    const plan = (b.proposal || {}).milestones || [];
    if (!plan.length) return '';
    const g = b.gate || {};
    const done = new Set((b.milestones || []).filter((m) => m.passed).map((m) => m.n));
    const items = plan
      .map((m, i) => {
        const n = m.n || i + 1;
        const now = g.kind === `m${n}`;
        const cls = done.has(n) ? 'past' : now ? 'now' : '';
        return this.stageItem({ ...m, n }, now || (!done.has(n) && n === (b.stage || 1) && !g.kind), cls);
      })
      .join('');
    const m = (b.milestones || []).find((x) => `m${x.n}` === g.kind);
    const demos = m ? m.demos || [] : [];
    const shots = demos
      .slice(-2)
      .map((list, i, arr) => `<figure class="bg-demo"><figcaption>${arr.length > 1 && i === 0 ? '改之前' : arr.length > 1 ? '改之后' : '演示截图'}</figcaption>${(list || []).map((p) => `<a href="/bench/${encodeURIComponent(b.id)}/${p.split(/[\\/]/).map(encodeURIComponent).join('/')}" target="_blank" rel="noopener"><img src="/bench/${encodeURIComponent(b.id)}/${p.split(/[\\/]/).map(encodeURIComponent).join('/')}" alt="这一段的截图" loading="lazy"></a>`).join('')}</figure>`)
      .join('');
    const checks = m ? m.checks || [] : [];
    const okN = checks.filter((x) => x.ok).length;
    const auto = checks.length ? `<p class="sc-sub bg-autochk">这一段自动检查：${okN}/${checks.length} 条做到${(b.baseline || []).length ? ` · 已锁定 ${b.baseline.length} 条，后面不许改坏` : ''}</p>` : (b.baseline || []).length ? `<p class="sc-sub bg-autochk">已锁定 ${b.baseline.length} 条验收，后面不许改坏</p>` : '';
    return `<h4 class="bg-h4">分段</h4><ol class="bg-flow bg-ms bg-stages">${items}</ol>${auto}${shots ? `<div class="bg-demos">${shots}</div>` : ''}`;
  }

  /** 一段：名字和目标；展开时加「你能试」、窗口、验收几条、这段不做什么、风险。 */
  stageItem(m, open, cls = '') {
    const bits = open
      ? [
          (m.try || []).length ? `<span class="sc-sub">你能试：${esc(m.try.join(' → '))}</span>` : '',
          `<span class="sc-sub">${esc((m.titles || []).join('、'))}${this.acceptLabel(m)}</span>`,
          m.skip ? `<span class="sc-sub">不做：${esc(m.skip)}</span>` : '',
          m.risk ? `<span class="sc-sub">风险：${esc(m.risk)}</span>` : '',
        ].join('')
      : '';
    return `<li class="${cls}"><b>第 ${m.n} 段 · ${esc(m.title)}</b>${m.goal && m.goal !== m.title ? `<span class="sc-sub">${esc(m.goal)}</span>` : ''}${bits}</li>`;
  }

  acceptLabel(m) {
    const acc = m.accept || [];
    const own = acc.filter((a) => a.startsWith('r')).length;
    const base = acc.length - own;
    return [own ? ` · 你的要求 ${own} 条` : '', base ? ' · 底线全验' : ''].join('');
  }

  /** 最近一轮：质检分、验收几条做到、评审各项分（全部轮次在底部「质检」里）。 */
  lastRoundHtml(b) {
    const r = (b.rounds || []).slice(-1)[0];
    if (!r) return '';
    const c = r.check || {};
    const reqs = r.requirements || [];
    const met = reqs.filter((x) => x.met).length;
    const scores = Object.entries(r.scores || {})
      .map(([k, v]) => `<li><span>${esc(k)}</span><span class="bg-bar" role="img" aria-label="${esc(k)} ${esc(v)} 分"><i style="width:${Math.max(0, Math.min(100, Number(v) * 10))}%"></i></span><b>${esc(Number(v).toFixed(1))}</b></li>`)
      .join('');
    const miss = reqs.filter((x) => !x.met).slice(0, 4).map((x) => `<li class="bg-miss"><span class="bg-mark" aria-hidden="true">✗</span><b>${esc(x.id)}</b> ${esc(x.text)}</li>`).join('');
    return `<h4 class="bg-h4">第 ${r.n} 轮 · ${r.passed ? '过线' : '没过线'}</h4>
      <p class="sc-sub">质检${c.passed ? '通过' : '不通过'} ${esc(c.score ?? '—')} 分${reqs.length ? ` · 验收 ${met}/${reqs.length}` : ''}</p>
      ${miss ? `<ul class="bg-reqs">${miss}</ul>` : ''}${scores ? `<ul class="bg-scores bg-scores-s">${scores}</ul>` : ''}`;
  }

  /** 底部抽屉：调研、插件、质检、团队、过程、联网、零件库。拉起来盖在三栏上，不换页。 */
  drawerHtml(st, b) {
    const r = (b && b.research) || null;
    const tabs = [
      ['research', '调研', r ? (r.counts || {}).github || 0 : 0],
      ['sources', '插件', b ? (b.sources || []).length : 0],
      ['rounds', '质检', b ? (b.rounds || []).length : 0],
      ['team', '团队', b ? ((b.team || {}).cards || []).length : 0],
      ['log', '过程', b ? (b.log || []).length : 0],
      ['net', '联网', this.net && this.net !== 'busy' ? this.net.ok : 0],
      ['lib', '零件库', ((st.library || {}).parts || []).length],
      ['desk', '资料', this.desk ? (this.desk.suggest || []).length : 0],
    ];
    const open = this.drawer;
    const bar = tabs
      .map(([k, t, n]) => `<button type="button" role="tab" data-drawer="${k}" aria-selected="${open === k}">${t}${n ? `<span class="bg-n">${esc(n)}</span>` : ''}</button>`)
      .join('');
    let body = '';
    if (open === 'research') body = (b && this.researchHtml(b)) || '<p class="sc-hint">还没调研。开工后先查 GitHub、论文和资讯，结果都在这里。</p>';
    else if (open === 'sources') body = (b && this.sourcesHtml(b)) || '<p class="sc-hint">这次没找插件。</p>';
    else if (open === 'rounds') body = b && (b.rounds || []).length ? b.rounds.map((x) => this.roundHtml(b, x)).join('') : '<p class="sc-hint">还没开始质检。</p>';
    else if (open === 'team') body = (b && this.teamHtml(b)) || '<p class="sc-hint">还没开始协作。</p>';
    else if (open === 'log') body = b ? this.logHtml(b) : '<p class="sc-hint">还没有项目。</p>';
    else if (open === 'net') body = `${this.netHtml() || '<p class="sc-hint">点右上「联网自检」，看成品要用的公开数据和调研来源通不通。</p>'}`;
    else if (open === 'desk') body = this.deskHtml();
    else if (open === 'lib') {
      const parts = ((st.library || {}).parts || []).map((p) => `<li><b>${esc(p.name)}</b>：${esc(p.use)}</li>`).join('');
      body = parts ? `<p class="sc-hint">做台的过程中自己长出来的零件，下次直接用。</p><ul class="bg-list">${parts}</ul>` : '<p class="sc-hint">零件库还没有新长出来的零件。</p>';
    }
    return `<div class="bg-dbar" role="tablist" aria-label="资料">${bar}<span class="sc-gap"></span>${open ? '<button type="button" class="link" data-drawer="">收起</button>' : ''}</div>
      ${open ? `<div class="bg-dbody" role="tabpanel">${body}</div>` : ''}`;
  }

  /** 资料台：榜单（每类前 10，总榜 / 新星）、动态（论文和订阅）、能力地图（按工作台类型筛）、建议卡。 */
  deskHtml() {
    const d = this.desk;
    if (!d) return '<p class="sc-hint">正在读资料台…</p>';
    if (!d.enabled) return '<p class="sc-hint">资料台在配置里关着（bench.desk.enabled）。</p>';
    const tabs = [
      ['rank', '榜单'],
      ['feed', '动态', (d.feed || []).length],
      ['caps', '能力地图', (d.caps || []).length],
      ['suggest', '建议', (d.suggest || []).length],
    ]
      .map(([k, t, n]) => `<button type="button" class="chip" data-desk-tab="${k}" aria-pressed="${this.deskTab === k}">${t}${n ? ` ${esc(n)}` : ''}</button>`)
      .join('');
    const when = d.updated_at ? `${d.updated_at.slice(5, 16).replace('T', ' ')} 更新` : '还没刷新过';
    const head = `<div class="bg-deskbar"><div class="bg-chips" role="group" aria-label="资料分类">${tabs}</div><span class="sc-gap"></span>
      <span class="bg-src" data-desk-when>${d.busy ? '正在刷新（GitHub 每分钟限 10 次，约 3～5 分钟）…' : esc(when)}</span>
      <button type="button" class="link" data-desk-refresh ${d.busy ? 'disabled' : ''}>刷新</button></div>`;
    let body = '';
    if (this.deskTab === 'rank') body = this.deskRankHtml(d);
    else if (this.deskTab === 'feed') body = this.deskFeedHtml(d);
    else if (this.deskTab === 'caps') body = this.deskCapsHtml(d);
    else body = this.deskSuggestHtml(d);
    const notes = (d.notes || []).length ? `<details class="bg-fold"><summary>这次有 ${d.notes.length} 处没取到</summary><ul class="bg-list">${d.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul></details>` : '';
    return head + body + notes;
  }

  deskRankHtml(d) {
    const cats = d.categories || [];
    if (!cats.length) return '<p class="sc-hint">配置里还没写榜单分类（bench.desk.categories）。</p>';
    const cat = cats.find((c) => c.id === this.deskCat) || cats[0];
    const nav = cats.map((c) => `<button type="button" role="tab" data-desk-cat="${esc(c.id)}" aria-selected="${c.id === cat.id}">${esc(c.label)}</button>`).join('');
    const mode = ['total', 'rising'].map((m) => `<button type="button" class="chip" data-desk-mode="${m}" aria-pressed="${this.deskMode === m}">${m === 'total' ? '总榜' : `新星（${d.rising_days || 30} 天内新建）`}</button>`).join('');
    const rows = (((d.rank || {})[cat.id] || {})[this.deskMode] || [])
      .map(
        (r, i) => `<li><span class="bg-rk">${i + 1}</span><div><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer"><b>${esc(r.repo)}</b></a>${r.new ? ' <span class="bg-tag">新</span>' : ''}
          <span class="bg-src">★ ${esc(r.stars.toLocaleString())} · ${esc(r.license || '许可证未写')} · ${esc(r.pushed || '')} 更新</span><p>${esc(r.desc || '（没写简介）')}</p></div></li>`,
      )
      .join('');
    return `<div class="bg-desk2"><nav class="bg-desknav" role="tablist" aria-label="榜单分类">${nav}</nav>
      <div><div class="bg-chips" role="group" aria-label="榜单">${mode}</div>
      ${rows ? `<ol class="bg-rank">${rows}</ol>` : '<p class="sc-hint">这一类还没取到，点右上「刷新」。</p>'}</div></div>`;
  }

  deskFeedHtml(d) {
    const kinds = { paper: '论文', news: '资讯', video: '视频', model: '模型' };
    const rows = (d.feed || [])
      .map(
        (x) => `<li><span class="bg-tag" data-kind="${esc(x.kind)}">${esc(kinds[x.kind] || x.kind)}</span><div><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer"><b>${esc(x.title)}</b></a>
          <span class="bg-src">${esc(x.source)}${x.date ? ` · ${esc(x.date)}` : ''}${x.score ? ` · 👍 ${esc(x.score)}` : ''}</span>${x.summary ? `<p>${esc(x.summary)}</p>` : ''}</div></li>`,
      )
      .join('');
    const topics = (d.topics || []).length ? `<p class="sc-hint">关注主题：${esc(d.topics.join('、'))}（改 bench.desk.topics）</p>` : '';
    return topics + (rows ? `<ul class="bg-feed">${rows}</ul>` : '<p class="sc-hint">还没有动态，点右上「刷新」。</p>');
  }

  deskCapsHtml(d) {
    const names = { editor: '编辑器', motion3d: '3D 与动画', game: '游戏', sim: '模拟经营', dashboard: '看板', productivity: '效率工具', content: '内容创作', education: '学习', audio: '音频', chat: '聊天角色' };
    const used = [...new Set((d.caps || []).flatMap((c) => c.kinds || []))];
    const kinds = { '': '全部', ...Object.fromEntries(used.map((k) => [k, names[k] || k])) };
    const pick = Object.entries(kinds).map(([k, t]) => `<button type="button" class="chip" data-desk-kind="${k}" aria-pressed="${this.deskKind === k}">${t}</button>`).join('');
    const caps = (d.caps || []).filter((c) => !this.deskKind || (c.kinds || []).includes(this.deskKind));
    const rows = caps
      .map(
        (c) => `<tr><th scope="row">${c.repo ? `<a href="https://github.com/${esc(c.repo)}" target="_blank" rel="noopener noreferrer">${esc(c.name)}</a>` : esc(c.name)}${c.added ? ' <span class="bg-tag">你加的</span>' : ''}<span class="bg-src">${esc(c.direction)}</span></th>
          <td>${esc(c.use)}${c.note ? `<span class="bg-src">${esc(c.note)}</span>` : ''}</td><td>${esc(c.license_seen || c.license || '未写')}</td>
          <td>${c.stars ? `★ ${esc(c.stars.toLocaleString())}${c.unprobed && c.verified ? `<span class="bg-src">${esc(c.verified.slice(0, 10))} 核实的</span>` : ''}` : ''}${c.unprobed ? `<span class="bg-unprobed" title="${esc(c.unprobed)}">不可探测：${esc(c.unprobed)}</span>` : c.stars ? '' : '<span class="bg-src">还没核实</span>'}</td><td>${esc(c.cost || '$0')}</td><td data-lv="${esc(c.difficulty)}">${esc(c.difficulty)}</td><td>${esc(c.status)}</td></tr>`,
      )
      .join('');
    return `<div class="bg-chips" role="group" aria-label="工作台类型">${pick}</div>
      <p class="sc-hint">写窗口时会按工作台类型挑几样附给助手。星数和许可证每次刷新从 GitHub 核实。</p>
      <div class="bg-tablewrap"><table class="bg-caps"><thead><tr><th>工具</th><th>用途</th><th>许可证</th><th>星数</th><th>花费</th><th>接入难度</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  deskSuggestHtml(d) {
    const rows = (d.suggest || [])
      .map(
        (s) => `<li><div><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer"><b>${esc(s.repo)}</b></a> <span class="bg-src">★ ${esc(s.stars.toLocaleString())} · ${esc(s.license)} · 主题「${esc(s.direction)}」</span><p>${esc(s.desc || '（没写简介）')}</p></div>
          <div class="bg-acts"><button type="button" class="primary" data-desk-sug="${esc(s.id)}" data-accept="1">加进地图</button><button type="button" data-desk-sug="${esc(s.id)}" data-accept="0">不要</button></div></li>`,
      )
      .join('');
    return rows
      ? `<p class="sc-hint">按关注主题每周找一次：星数够、许可证宽松、地图里还没有的。你点了才加；「不要」的以后不再推。</p><ul class="bg-sugg">${rows}</ul>`
      : '<p class="sc-hint">这周没有新的建议。</p>';
  }

  /** 打开「资料」时读一次；过期了叫后台刷新，刷新中每 5 秒看一眼。 */
  async loadDesk(refresh = false) {
    try {
      this.desk = await this.api.desk(refresh);
      if (!refresh && this.desk.due && !this.deskAsked && this.desk.enabled) {
        this.deskAsked = true;
        this.desk = await this.api.desk(true);
      }
    } catch (x) {
      this.err = `资料台没读到：${x.message}`;
    }
    this.paint('drawer', this.drawerHtml(this.st || {}, this.shownBuild()), true);
    clearTimeout(this.deskTimer);
    if (this.desk && this.desk.busy && this.drawer === 'desk') this.deskTimer = setTimeout(() => this.loadDesk(), 5000);
  }

  logHtml(b) {
    const timeline = (b.timeline || [])
      .map((t) => `<li><span class="bg-src">${esc(t.at.slice(5, 16).replace('T', ' '))} · ${esc(t.stage)}</span> <b>${esc(t.title)}</b>${t.detail ? ` · ${esc(t.detail)}` : ''}</li>`)
      .join('');
    const log = (b.log || []).map((l) => `<li>${esc(l.at.slice(11, 19))} ${esc(l.text)}</li>`).join('');
    const parts = (b.new_parts || []).length ? `<p class="sc-hint">这次新收进零件库：${esc(b.new_parts.join('、'))}</p>` : '';
    return `${timeline ? `<h4 class="bg-h4">项目时间线</h4><ol class="bg-list bg-timeline">${timeline}</ol>` : ''}${parts}
      ${log ? `<h4 class="bg-h4">过程</h4><ol class="sc-log">${log}</ol>` : ''}`;
  }

  /** 联网自检的结果：每项通不通、多快；不通的写原因。 */
  netHtml() {
    const r = this.net;
    if (!r || r === 'busy') return '';
    const rows = (r.items || [])
      .map(
        (x) => `<li class="${x.ok ? 'bg-met' : 'bg-miss'}"><span class="bg-mark" role="img" aria-label="${x.ok ? '通' : '不通'}">${x.ok ? '✓' : '✗'}</span><b>${esc(x.source)}</b> <span class="bg-src">${esc(x.group)}</span> ${esc(x.use)} · ${esc(x.ok ? `${x.ms} 毫秒` : x.detail)}</li>`,
      )
      .join('');
    return `<details class="bg-net" open><summary>联网自检：${esc(r.ok)}/${esc(r.total)} 项能连上</summary><ul class="bg-reqs">${rows}</ul>
      <p class="sc-hint">不通的项目：成品里那部分会退回参考数据并标明日期；调研那部分会少一些资料。多半是网络或代理拦下了，换个网络再试。</p></details>`;
  }

  /** 调研报告：GitHub（至少 10 个）、学习卡、dsh 插件（建议安装）、论文、资讯和视频。 */
  researchHtml(b) {
    const r = b.research;
    if (!r) return '';
    const c = r.counts || {};
    const link = (x) => `<a class="link" href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>`;
    const gh = (r.github || [])
      .map(
        (g) => `<tr><td>${link(g)}</td><td>${esc(g.stars)}</td><td>${esc(g.license || '没写')}</td><td>${esc(g.date || '—')}</td><td class="bg-v-${esc(g.verdict)}">${esc(VERDICT[g.verdict] || g.verdict)}</td><td class="sc-why">${esc(g.summary)}</td></tr>`,
      )
      .join('');
    const cards = (r.cards || [])
      .map(
        (k) => `<li><b>${esc(k.repo)}</b>${k.plugin ? ' <span class="bg-src">dsh 插件</span>' : ''}${k.worker ? `（${esc(this.label(k.worker))} 写的）` : ''}：${esc(k.what)}
          ${(k.borrow || []).length ? `<ul>${k.borrow.map((x) => `<li>借：${esc(x)}</li>`).join('')}${(k.avoid || []).map((x) => `<li>别学：${esc(x)}</li>`).join('')}</ul>` : ''}</li>`,
      )
      .join('');
    const asked = new Set((b.plugin_requests || []).map((x) => x.name));
    const plugins = (r.plugins || [])
      .map((p) => {
        const state = p.whitelisted
          ? `<span class="bg-ok">已审过，在电脑上运行 setup_dsh.py 就能装（${esc(p.whitelisted)}）</span>`
          : asked.has(p.title)
            ? '<span class="bg-src">已申请：审过源码、钉好版本后加进白名单</span>'
            : `<button type="button" class="btn" data-plugin="${esc(p.title)}">申请装上</button>`;
        return `<li>${link(p)} · ${esc(p.stars)} 星 · ${esc(p.summary)} ${state}</li>`;
      })
      .join('');
    const papers = (r.papers || []).map((p) => `<li>${link(p)} · ${esc(p.source)} ${esc(p.date)} · ${esc(p.summary)}</li>`).join('');
    const news = (r.news || [])
      .map((n) => `<li>${esc(NEWS[n.kind] || '资讯')}：${link(n)} · ${esc(n.source)} ${esc(n.date)}${esc(CHECKED[String(n.verified)] ?? '')} · ${esc(n.summary)}</li>`)
      .join('');
    const notes = (r.notes || []).map((x) => `<p class="sc-sub">${esc(x)}</p>`).join('');
    return `<details class="bg-research" open><summary>调研报告：GitHub ${esc(c.github || 0)} 个 · 学习卡 ${esc((r.cards || []).length)} 张 · 论文 ${esc(c.papers || 0)} 篇 · 资讯和视频 ${esc(c.news || 0)} 条${c.plugins ? ` · dsh 插件 ${esc(c.plugins)} 个` : ''}</summary>
      ${notes}
      ${gh ? `<table class="sc-subs bg-gh"><thead><tr><th>项目</th><th>星</th><th>许可证</th><th>最近更新</th><th>结论</th><th>是什么</th></tr></thead><tbody>${gh}</tbody></table>` : ''}
      ${cards ? `<h4>学习卡</h4><ul class="bg-cards">${cards}</ul>` : ''}
      ${plugins ? `<h4>dsh 插件（建议安装，你点同意才装）</h4><ul class="bg-list bg-plugins">${plugins}</ul>` : ''}
      ${papers ? `<h4>论文</h4><ul class="bg-list bg-papers">${papers}</ul>` : ''}
      ${news ? `<h4>资讯和视频</h4><ul class="bg-list bg-news">${news}</ul>` : ''}
      <a class="link" href="/bench/${encodeURIComponent(b.id)}/${encodeURIComponent('调研报告.md')}" target="_blank" rel="noopener">打开完整调研报告</a>
    </details>`;
  }

  /** 团队：方案会（各自的方案、谁投了谁、主案）+ 协作看板（按人分栏：当过什么角色、干了哪些事）。 */
  teamHtml(b) {
    const m = b.meeting;
    const cards = ((b.team || {}).cards || []);
    if (!m && !cards.length) return '';
    let meeting = '';
    if (m && (m.pitches || []).length) {
      const pitches = [...m.pitches]
        .sort((x, y) => x.label.localeCompare(y.label))
        .map((p) => {
          const lead = p.worker === m.lead;
          return `<li class="bg-pitch ${lead ? 'bg-lead' : ''}"><p><b>方案 ${esc(p.label)}</b> · ${esc(this.label(p.worker))} · ${esc(p.votes)} 票${lead ? ' <span class="bg-badge">主案</span>' : ''}</p>
            <p>${esc(p.idea)}</p>${p.hero ? `<p class="sc-sub">主视觉：${esc(p.hero)}</p>` : ''}
            <ul>${(p.plan || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
            ${(p.risks || []).length ? `<p class="sc-sub">风险：${esc(p.risks.join('；'))}</p>` : ''}</li>`;
        })
        .join('');
      const votes = (m.votes || [])
        .map((v) => `<li>${esc(this.label(v.voter))} 投给方案 ${esc(v.pick)}：${esc(v.why)}${(v.borrow || []).length ? `<span class="sc-sub"> · 建议补：${esc(v.borrow.join('；'))}</span>` : ''}</li>`)
        .join('');
      meeting = `<h4>方案会</h4><ul class="bg-pitches">${pitches}</ul>${votes ? `<ul class="bg-list bg-votes">${votes}</ul>` : ''}`;
    } else if (m) {
      meeting = '<p class="sc-hint">方案会没收到能用的方案，按规格直接做。</p>';
    }
    const by = new Map();
    for (const c of cards) {
      if (!by.has(c.who)) by.set(c.who, []);
      by.get(c.who).push(c);
    }
    const cols = [...by.entries()]
      .map(([who, list]) => {
        const roles = [...new Set(list.map((c) => c.role))];
        const recent = list
          .slice(-6)
          .reverse()
          .map((c) => `<li class="${c.ok ? '' : 'sc-warn'}"><span class="bg-src">${esc(c.at.slice(11, 16))} ${esc(c.role)}</span> ${esc(c.task)}${c.ok ? '' : '（没成）'}</li>`)
          .join('');
        return `<div class="bg-col"><p><b>${esc(this.label(who))}</b>${who === (m || {}).lead ? ' <span class="bg-badge">主案</span>' : ''} · ${list.length} 件</p>
          <p class="bg-roles">${roles.map((r) => `<span class="chip">${esc(r)}</span>`).join('')}</p><ul class="bg-list">${recent}</ul></div>`;
      })
      .join('');
    return `<details class="bg-team" open><summary>团队：${by.size} 位助手协作${m && m.lead ? `，主案 ${esc(this.label(m.lead))}` : ''}</summary>
      ${meeting}
      ${cols ? `<h4>协作看板</h4><div class="bg-board">${cols}</div>` : ''}
    </details>`;
  }

  /** 做满轮数没过线（右栏）：哪几条没做到、卡在哪、建议。 */
  stuckInfo(b) {
    const k = b.stuck;
    if (!k) return '';
    const missing = (k.missing || [])
      .map((m) => `<li class="bg-miss"><b>${esc(m.id)}</b> ${esc(m.text)}<span class="sc-sub">${m.missed > 1 ? `连续 ${esc(m.missed)} 轮没做到` : '这轮没做到'}${(m.why || []).length ? `：${esc(m.why.join('；'))}` : ''}</span></li>`)
      .join('');
    const advice = (k.advice || []).map((a) => `<li>${esc(a)}</li>`).join('');
    return `<div class="bg-stuck">
      <p><b>${b.status === 'needs_you' ? '做满了还没过线' : '交付前没过线的地方'}</b>${(k.trend || []).length ? ` · 评审分 ${esc(k.trend.join(' → '))}` : ''}</p>
      ${missing ? `<ul class="bg-reqs">${missing}</ul>` : ''}
      ${(k.blocks || []).length ? `<p class="sc-sub">质检必须修：${esc(k.blocks.join('、'))}</p>` : ''}
      ${(k.high || []).length ? `<p class="sc-sub">对抗手的严重问题：${esc(k.high.join('；'))}</p>` : ''}
      ${advice ? `<h4 class="bg-h4">建议</h4><ul class="bg-list">${advice}</ul>` : ''}
    </div>`;
  }

  /** 做满轮数没过线（左栏）：你来定——再跑一轮 / 改要求再跑 / 就这样收下。 */
  stuckForm(b) {
    const last = (b.rounds || []).slice(-1)[0] || {};
    const reqs = (last.requirements || []).map((r) => this.reqRow(r)).join('');
    return `<div class="bg-gate bg-stuck-form" data-form><p class="bg-gate-title"><b>等你：</b>做满了还没过线，你来定（右边写了卡在哪）</p>
      <div class="sc-row"><button type="button" class="btn primary" data-decide="more">再跑一轮</button><button type="button" class="btn" data-decide="accept">就这样，收下</button></div>
      <details class="bg-edit"><summary>改要求再跑</summary><ul class="bg-reqs bg-sign-reqs">${reqs}</ul>
        <button type="button" class="link" data-addreq>加一条要求</button>
        <div class="sc-row"><button type="button" class="btn" data-decide="edit">按改好的要求再跑一轮</button></div></details>
    </div>`;
  }

  /** 验收清单的一行：底线不能改，你的要求可以改、删。 */
  reqRow(r) {
    return String(r.id).startsWith('b')
      ? `<li class="bg-req-base"><b>${esc(r.id)}</b> <span class="bg-src">底线</span> ${esc(r.text)}</li>`
      : `<li data-req="${esc(r.id)}"><b>${esc(r.id)}</b> <input type="text" value="${esc(r.text)}" aria-label="要求 ${esc(r.id)}" maxlength="300"><button type="button" class="link" data-delreq aria-label="删掉 ${esc(r.id)}">删掉</button></li>`;
  }

  /** 选型台的状态：第一次打开某个台的方案书时，按拆需求助手的推荐填好。 */
  ensurePick(b) {
    const d = (b.proposal || {}).design || {};
    if (this.pick && this.pick.bid === b.id) return this.pick;
    const picks = d.picks || {};
    this.pick = {
      bid: b.id,
      ...picks,
      refs: new Set((d.refs || []).map((r) => r.name)),
      assets: {},
      accent: picks.accent || '',
      note: d.note || '',
      spans: {},
      drop: new Set(),
      view: { mode: 'light', device: 'desk' },
    };
    return this.pick;
  }

  /** 签字时交的选型：只交改过宽窄的窗口。 */
  designChoice(b) {
    const d = (b.proposal || {}).design;
    if (!d || !this.pick || this.pick.bid !== b.id) return undefined;
    const out = { refs: [...this.pick.refs], spans: { ...this.pick.spans }, drop: [...this.pick.drop] };
    for (const dim of d.dims || []) out[dim.key] = this.pick[dim.key] || '';
    out.note = this.pick.note || '';
    out.assets = { ...this.pick.assets };
    return out;
  }

  /** 现在的选型下窗口怎么排：先按版型，再去掉不要的、套上你调的宽窄。 */
  arranged(b) {
    const p = b.proposal || {};
    const pick = this.pick;
    const wins = (p.windows || []).filter((w) => !pick.drop.has(w.id));
    const byId = Object.fromEntries(wins.map((w) => [w.id, w]));
    return layoutSpans(wins, p.hero, pick.layout || 'plan').map(([id, n]) => ({ ...byId[id], span: pick.spans[id] || n }));
  }

  viewBar(pick) {
    const seg = (key, opts) => opts.map(([v, t]) => `<button type="button" class="chip" data-dz="${key}" data-v="${v}" aria-pressed="${pick.view[key] === v}">${t}</button>`).join('');
    return `<div class="bg-dview" data-dviewbar>${seg('device', [['desk', '电脑'], ['phone', '手机']])}<span class="sc-gap"></span>${seg('mode', [['light', '浅色'], ['dark', '深色']])}</div>`;
  }

  designOpts(b, d, pick) {
    const one = (dim) => {
      const rec = (d.recommend || {})[dim.key];
      const why = (d.why || {})[dim.key];
      if (dim.kind === 'color') {
        const palette = [...new Set([...(((d.look || {}).palette) || []), ...(rec ? [rec] : [])])];
        const sw = palette
          .map((c) => `<button type="button" class="bg-swatch" data-swatch="${esc(c)}" style="background:${esc(c)}" aria-pressed="${pick.accent === c}" title="${esc(c)}${c === rec ? '（推荐）' : ''}" aria-label="主色 ${esc(c)}"></button>`)
          .join('');
        return `<div class="bg-dim"><b>${esc(dim.label)}</b><span class="bg-opts"><input type="color" data-daccent value="${esc(pick.accent || '#2f5bea')}" aria-label="自己挑主色">
          ${sw}<button type="button" class="chip" data-dz="accent" data-v="" aria-pressed="${!pick.accent}">用配色自带</button></span>
          <span class="sc-sub">${palette.length ? '色块是从你的素材里取的。' : ''}${pick.accent ? `现在：${esc(pick.accent)}` : '跟着配色走'}${why && pick.accent === rec ? `。推荐理由：${esc(why)}` : ''}</span></div>`;
      }
      const cur = dim.options.find((o) => o.id === pick[dim.key]) || dim.options[0];
      const chips = dim.options
        .map((o) => `<button type="button" class="chip" data-dz="${esc(dim.key)}" data-v="${esc(o.id)}" aria-pressed="${o.id === pick[dim.key]}" title="${esc(o.hint)}">${esc(o.label)}${o.id === rec ? '<span class="bg-rec">推荐</span>' : ''}</button>`)
        .join('');
      return `<div class="bg-dim"><b>${esc(dim.label)}</b><span class="bg-opts">${chips}</span>
        <span class="sc-sub">${esc(cur.hint)}${why && cur.id === rec ? `。推荐理由：${esc(why)}` : ''}</span></div>`;
    };
    const groups = [];
    for (const dim of d.dims || []) {
      const g = dim.group || '其他';
      if (!groups.length || groups[groups.length - 1][0] !== g) groups.push([g, []]);
      groups[groups.length - 1][1].push(dim);
    }
    const dims = groups.map(([g, list]) => `<details class="bg-dgroup" data-dgroup="${esc(g)}" ${this.openGroups.has(g) ? 'open' : ''}><summary>${esc(g)}<span class="sc-sub">${list.map((x) => esc(x.label)).join('、')}</span></summary>${list.map(one).join('')}</details>`).join('');
    const refs = (d.refs || [])
      .map((r) => `<li><label><input type="checkbox" data-dref="${esc(r.name)}" ${pick.refs.has(r.name) ? 'checked' : ''}> <b>${esc(r.name)}</b> <span class="bg-src">${esc(r.from || '')}</span> ${esc(r.borrow || '')}</label></li>`)
      .join('');
    const p = b.proposal || {};
    const auto = Object.fromEntries(layoutSpans((p.windows || []).filter((w) => !pick.drop.has(w.id)), p.hero, pick.layout || 'plan'));
    const wins = (p.windows || [])
      .map((w) => {
        const off = pick.drop.has(w.id);
        const opts = [`<option value="">跟版型（${esc(SPAN_NAME[auto[w.id]] || '—')}）</option>`, ...SPANS.map((n) => `<option value="${n}" ${pick.spans[w.id] === n ? 'selected' : ''}>${SPAN_NAME[n]}</option>`)].join('');
        return `<li class="${off ? 'bg-off' : ''}"><label><input type="checkbox" data-ddrop="${esc(w.id)}" ${off ? '' : 'checked'} ${w.id === p.hero ? 'disabled' : ''} aria-label="要「${esc(w.title)}」"> ${esc(w.title)}${w.id === p.hero ? '（主视觉）' : ''}</label>
          <select data-dspan="${esc(w.id)}" aria-label="「${esc(w.title)}」的宽度" ${off ? 'disabled' : ''}>${opts}</select></li>`;
      })
      .join('');
    return `${this.materialsHtml(b, d, pick)}${dims}
      <label class="bg-dim"><b>还想要的风格（自己写，助手照着做）</b><textarea data-dnote rows="2" maxlength="200" placeholder="比如：像老式胶片相机的质感、按钮要大、别用紫色">${esc(pick.note || '')}</textarea></label>
      ${refs ? `<div class="bg-dim"><b>参考作品（勾上的写进规格，助手照着借鉴）</b><ul class="bg-drefs">${refs}</ul></div>` : ''}
      <div class="bg-dim"><b>窗口（去掉勾就不做，宽度可调）</b><ul class="bg-dwins">${wins}</ul></div>
      <button type="button" class="link" data-dreset>恢复推荐</button>`;
  }

  /** 素材：拖进来的图和视频，标用处（只当参考 / Logo / 主视觉图 / 背景 / 图库）；看过的显示分析。 */
  materialsHtml(b, d, pick) {
    const all = d.materials || [];
    const roles = d.roles || {};
    const rows = all
      .filter((m) => m.kind !== 'frame')
      .map((m) => {
        const frames = all.filter((x) => x.of === m.id);
        const thumb = m.kind === 'video' ? (frames[0] ? matUrl(b.id, frames[0]) : '') : matUrl(b.id, m);
        const opts = Object.entries(roles)
          .filter(([k]) => !(m.kind === 'video' && (k === 'logo' || k === 'bg')))
          .map(([k, t]) => `<option value="${esc(k)}" ${(pick.assets[m.id] || 'ref') === k ? 'selected' : ''}>${esc(t)}</option>`)
          .join('');
        const seen = m.kind === 'video' ? frames.some((x) => x.seen) : m.seen;
        return `<li class="bg-mat">${thumb ? `<img src="${esc(thumb)}" alt="">` : ''}<span>${esc(m.name)}${m.kind === 'video' ? `（视频，${frames.length} 帧）` : ''}${seen ? ' <span class="bg-src">看过</span>' : ''}</span>
          <select data-drole="${esc(m.id)}" aria-label="「${esc(m.name)}」用来做什么">${opts}</select></li>`;
      })
      .join('');
    const look = d.look;
    const items = ((look || {}).items || []).filter((x) => x.what).slice(0, 6).map((x) => `<li>${esc(x.what)}${x.borrow ? `：${esc(x.borrow)}` : ''}</li>`).join('');
    const unseen = all.some((m) => (m.kind === 'image' || m.kind === 'frame') && !m.seen);
    return `<div class="bg-dim bg-dmats" data-drop="board"><b>素材（拖图片或视频进来，标好用处，预览马上用上）</b>
      ${rows ? `<ul class="bg-mats">${rows}</ul>` : '<span class="sc-sub">还没有素材。</span>'}
      <span class="bg-opts"><button type="button" class="link" data-pick="board">选文件</button>
      ${unseen ? '<button type="button" class="chip" data-dlook>请助手看一遍新素材（占 1 次）</button>' : ''}${this.uploading ? `<span class="sc-sub">正在上传 ${this.uploading} 个…</span>` : ''}</span>
      ${look && look.summary ? `<p class="sc-sub">看素材的结论：${esc(look.summary)}</p>` : ''}${items ? `<ul class="bg-list">${items}</ul>` : ''}</div>`;
  }

  /** 拖进来的文件：先上传收下；视频再在浏览器里截几帧一起传。开工前的留在框里，签字前的直接加进工作台。 */
  async addFiles(files, where) {
    const ok = files.filter((f) => /^(image|video)\//.test(f.type) || /\.(png|jpe?g|webp|gif|mp4|webm|mov)$/i.test(f.name));
    if (!ok.length) {
      this.err = '只收图片和视频';
      return this.render(true);
    }
    this.uploading += ok.length;
    where === 'board' ? this.redrawDesign() : this.render(true);
    const got = [];
    for (const f of ok) {
      try {
        const m = await this.api.material(f);
        const video = m.kind === 'video';
        got.push({ ...m, thumb: video ? '' : URL.createObjectURL(f) });
        if (video) {
          const frames = await videoFrames(f);
          for (const fr of frames) {
            const x = await this.api.material(fr, m.id);
            got.push({ ...x, thumb: '' });
          }
          const first = got.find((x) => x.of === m.id);
          if (first) got.find((x) => x.id === m.id).thumb = URL.createObjectURL(frames[0]);
          if (!frames.length) this.err = `「${f.name}」浏览器截不了帧，只能当文件参考`;
        }
      } catch (x) {
        this.err = `「${f.name}」没传上：${x.message}`;
      }
      this.uploading -= 1;
    }
    const b = this.viewing || (this.st || {}).current;
    if (where === 'board' && b && got.length) {
      try {
        this.st = await this.api.attach(b.id, got.map((m) => m.id));
        const fresh = (this.st || {}).current;
        if (fresh && fresh.id === b.id && fresh.proposal) b.proposal.design.materials = fresh.proposal.design.materials;
      } catch (x) {
        this.err = x.message;
      }
      return this.redrawDesign();
    }
    this.mats.push(...got);
    this.render(true);
  }

  /** 选型变了：只重画左边的选项和右边的预览，不碰你在验收清单里改的字。 */
  redrawDesign() {
    this.dirty = true;
    const b = this.viewing || (this.st || {}).current;
    const box = this.el.querySelector('[data-dopts]');
    if (!b || !box) return;
    box.innerHTML = this.designOpts(b, b.proposal.design, this.pick);
    this.el.querySelector('[data-dviewbar]').outerHTML = this.viewBar(this.pick);
    this.drawPreview();
  }

  /** 实时预览：在 iframe 里用零件库的样式画出顶栏和每个窗口（宽窄、顺序、主视觉、材质……都和成品一样）。 */
  async drawPreview() {
    const frame = this.el.querySelector('[data-dpreview]');
    const b = this.viewing || (this.st || {}).current;
    if (!frame || !b || !this.pick) return;
    const doc = frame.contentDocument;
    // srcdoc 还没载入时 contentDocument 是一张空白页（about:blank），等它载完再画
    if (!doc || doc.URL !== 'about:srcdoc' || doc.readyState !== 'complete' || !doc.body) {
      frame.addEventListener('load', () => this.drawPreview(), { once: true });
      return;
    }
    if (!this.icons) {
      try {
        this.icons = (await import('/kit/icons.js')).icon;
      } catch {
        this.icons = () => '';
      }
    }
    const p = b.proposal || {};
    const d = p.design || {};
    const pick = this.pick;
    const root = doc.documentElement;
    root.dataset.kTheme = pick.theme || 'ink';
    root.dataset.kMode = pick.view.mode;
    const roles = {};
    for (const m of d.materials || []) if (pick.assets[m.id] && pick.assets[m.id] !== 'ref') roles[pick.assets[m.id]] = m;
    for (const dim of d.dims || []) {
      if (dim.kind === 'color' || TEXT_ONLY.includes(dim.key)) continue;
      let v = pick[dim.key] || '';
      if (dim.key === 'bg' && v === 'image' && !roles.bg) v = 'soft'; // 没标背景图就退回柔光（和成品一样）
      root.dataset[`k${dim.key[0].toUpperCase()}${dim.key.slice(1)}`] = v;
    }
    root.style.cssText = '';
    if (/^#[0-9a-f]{6}$/i.test(pick.accent || '')) {
      root.style.setProperty('--k-accent', pick.accent);
      root.style.setProperty('--k-accent-fg', fgFor(pick.accent));
    }
    if (roles.bg) root.style.setProperty('--k-bg-image', `url("${matUrl(b.id, roles.bg)}")`);
    const ic = (name, size = 17) => this.icons(name, { size });
    const cards = this.arranged(b)
      .map((w) => {
        const hero = w.id === p.hero;
        const acts = (w.actions || []).slice(0, 3).map((a, i) => `<button type="button" class="k-btn${i === 0 ? ' k-primary' : ''}">${esc(a)}</button>`).join('');
        const parts = (w.parts || []).slice(0, 5).map((x) => `<span class="k-badge">${esc(x)}</span>`).join('');
        const media = hero && roles.hero
          ? roles.hero.kind === 'video'
            ? `<video src="${esc(matUrl(b.id, roles.hero))}" muted autoplay loop playsinline style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"></video>`
            : `<img src="${esc(matUrl(b.id, roles.hero))}" alt="" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover">`
          : '';
        const body = hero
          ? `<div class="k-stack" style="position:relative;padding:22px 22px 18px;flex:1;justify-content:flex-end;background:linear-gradient(160deg,var(--k-accent-soft),transparent 70%)">${media}<div class="k-stack" style="position:relative;${media ? 'background:color-mix(in srgb,var(--k-surface) 82%,transparent);padding:14px;border-radius:var(--k-r-sm)' : ''}"><span class="k-kicker">${esc(w.title)}</span><p class="k-display">${esc(d.tagline || p.subtitle || w.title)}</p><p class="k-muted">${esc(w.purpose)}</p><div class="k-row">${parts}</div><div class="k-row">${acts}</div></div></div>`
          : `<div class="k-stack"><p class="k-muted">${esc(w.purpose)}</p><div class="k-row">${parts}</div><div class="k-row">${acts}</div></div>`;
        return `<section class="k-card k-span-${w.span}${hero ? ' k-hero' : ''}" data-pw="${esc(w.id)}" data-span="${w.span}"><div class="k-card-h"><span class="k-ico-wrap">${ic(w.icon || 'layout-grid')}</span><div><h2>${esc(w.title)}</h2>${w.sub ? `<div class="k-sub">${esc(w.sub)}</div>` : ''}</div></div><div class="k-card-b">${body}</div></section>`;
      })
      .join('');
    doc.body.innerHTML = `<div class="k-page"><header class="k-topbar"><div class="k-brand">${roles.logo ? `<span class="k-logo k-logo-img"><img src="${esc(matUrl(b.id, roles.logo))}" alt="标志"></span>` : `<span class="k-logo">${ic(p.icon || 'layers', 20)}</span>`}<div><h1>${esc(p.title || '')}</h1><div class="k-sub">${esc(p.subtitle || '')}</div></div></div></header><main class="k-grid">${cards}</main></div>`;
    this.fitPreview();
  }

  /** 电脑按 1280 宽画再缩到框里；手机按 390 宽原样画。 */
  fitPreview() {
    const box = this.el.querySelector('[data-dframe]');
    const frame = box && box.querySelector('iframe');
    if (!frame || !this.pick) return;
    if (this.ro && !box.dataset.watched) {
      this.ro.observe(box);
      box.dataset.watched = '1';
    }
    const phone = this.pick.view.device === 'phone';
    const w = phone ? PHONE_W : DESK_W;
    const scale = Math.min(1, (box.clientWidth || w) / w);
    const doc = frame.contentDocument;
    const h = Math.max(480, Math.min(2400, doc && doc.body ? doc.documentElement.scrollHeight : 800));
    Object.assign(frame.style, { width: `${w}px`, height: `${h}px`, transform: `scale(${scale})` });
    box.style.height = `${Math.ceil(h * scale)}px`;
    box.dataset.device = phone ? 'phone' : 'desk';
  }

  winTitle(b, wid) {
    return ((b.windows || []).find((w) => w.id === wid) || {}).title || wid;
  }

  /** 停在关口时你要做的事。 */
  gateHtml(b) {
    const g = b.gate;
    if (!g) return '';
    const head = `<p class="bg-gate-title"><b>等你：</b>${esc(g.title)}${g.auto_at ? `<span class="sc-sub">（${esc(g.auto_at.slice(5, 16).replace('T', ' '))} 前不回就算通过）</span>` : ''}</p>`;
    const end = '<button type="button" class="link" data-endproj>结束这个项目</button>';
    if (g.kind === 'interview') {
      const qs = ((b.interview || {}).questions || [])
        .map(
          (q) => `<li><label><b>${esc(q.q)}</b>${q.why ? `<span class="sc-sub">${esc(q.why)}</span>` : ''}
            ${(q.options || []).length ? `<span class="bg-opts">${q.options.map((o) => `<button type="button" class="chip${o === q.recommend ? ' rec' : ''}" data-opt="${esc(q.id)}" data-val="${esc(o)}">${esc(o)}${o === q.recommend ? '<small>推荐</small>' : ''}</button>`).join('')}</span>` : ''}
            ${q.effect ? `<span class="sc-sub">${esc(q.effect)}</span>` : ''}
            <textarea data-q="${esc(q.id)}" rows="2" aria-label="${esc(q.q)}" placeholder="可以不填"></textarea></label></li>`,
        )
        .join('');
      return `<div class="bg-gate" data-form>${head}<ol class="bg-qs">${qs}</ol>
        <div class="sc-row"><button type="button" class="btn primary" data-answer>交回答，开始做方案</button><button type="button" class="btn" data-skip>全部按推荐</button>${end}</div></div>`;
    }
    if (g.kind === 'sign') {
      const p = b.proposal || {};
      const d = p.design;
      const reqs = (p.requirements || []).map((r) => this.reqRow(r)).join('');
      const opts = d && (d.dims || []).length ? `<section class="bg-design" aria-label="设计选型"><h4 class="bg-h4">样子（改一项，中间马上变，不花次数）</h4><div class="bg-design-opts" data-dopts>${this.designOpts(b, d, this.ensurePick(b))}</div></section>` : '';
      return `<div class="bg-gate" data-form>${head}
        <div class="sc-row bg-signrow"><button type="button" class="btn primary" data-sign>签字开工</button>${end}</div>
        ${opts}
        <h4 class="bg-h4">验收清单（你的要求可以改、删、加；底线不能删）</h4><ul class="bg-reqs bg-sign-reqs">${reqs}</ul>
        <button type="button" class="link" data-addreq>加一条要求</button>
        <label class="bg-note">补充一句（可不填）<textarea data-note rows="2"></textarea></label></div>`;
    }
    // 分段演示 / 交付验收：中间直接试用，这里通过或提意见（演示截图在右栏）
    const accept = g.kind === 'accept';
    const scope = accept ? (b.windows || []).map((w) => w.id) : (((b.proposal || {}).milestones || [])[Number(g.kind.slice(1)) - 1] || {}).windows || [];
    const opts = scope.map((id) => `<option value="${esc(id)}">${esc(this.winTitle(b, id))}</option>`).join('');
    return `<div class="bg-gate" data-form>${head}
      <p class="sc-hint">${accept ? '中间就是成品，逐条试一遍验收清单。' : '中间是这一段做好的部分，可以直接点着试。'}</p>
      <div class="sc-row"><button type="button" class="btn primary" data-pass>${accept ? '验收通过' : '通过，做下一段'}</button></div>
      <label class="bg-note">哪里不对（一条一行）<textarea data-comments rows="3" placeholder="例如：地图太小，看不清路线"></textarea></label>
      <label class="bg-note">想改成什么样（可不写，写了更不容易改偏）<input type="text" data-cwant maxlength="300" placeholder="例如：地图占右边一半，路线加粗"></label>
      <label class="bg-note">针对哪个窗口 <select data-cwin><option value="">这一段的全部窗口</option>${opts}</select></label>
      <div class="sc-row"><button type="button" class="btn" data-change>提意见，让他们改</button>${end}</div></div>`;
  }

  evolveHtml(b) {
    const e = b.evolve;
    if (!e) return `<div class="sc-row"><button type="button" class="btn" data-evolve ${this.st && this.st.busy ? 'disabled' : ''}>出下一版提案</button><span class="sc-hint">汇总你的意见，再查一遍新资料，提出下一版改什么。</span></div>`;
    const items = (e.items || []).map((x) => `<li><b>${esc(x.title)}</b>：${esc(x.why)}${x.basis ? `<span class="sc-sub">依据：${esc(x.basis)}</span>` : ''}</li>`).join('');
    return `<div class="bg-evolve"><h4>下一版提案</h4>${items ? `<ol class="bg-list">${items}</ol>
      <button type="button" class="btn" data-nextver>按提案做下一版</button>` : '<p class="sc-hint">这次没出成提案，可以再试一次。</p>'}</div>`;
  }

  sourcesHtml(b) {
    const items = b.sources || [];
    if (!items.length) return '';
    const rows = items
      .map(
        (s) => `<tr><td>${esc(s.need)}</td><td><a class="link" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.repo)}</a></td>
          <td>${esc(s.license || '没写')}</td><td>${esc(s.stars)}</td><td class="bg-v-${esc(s.verdict)}">${esc(VERDICT[s.verdict] || s.verdict)}</td><td class="sc-why">${esc(s.why)}</td></tr>`,
      )
      .join('');
    const used = items.filter((s) => s.verdict === 'adopt').length;
    return `<details class="bg-sources" open><summary>插件清单：直接用 ${used} 个，只借思路 ${items.length - used} 个</summary>
      <table class="sc-subs"><thead><tr><th>为了</th><th>仓库</th><th>许可证</th><th>星</th><th>结论</th><th>为什么</th></tr></thead><tbody>${rows}</tbody></table></details>`;
  }

  roundHtml(b, r) {
    const c = r.check || {};
    const scores = Object.entries(r.scores || {})
      .map(
        ([k, v]) => `<li><span>${esc(k)}</span><span class="bg-bar" role="img" aria-label="${esc(k)} ${esc(v)} 分"><i style="width:${Math.max(0, Math.min(100, Number(v) * 10))}%"></i></span><b>${esc(Number(v).toFixed(1))}</b></li>`,
      )
      .join('');
    const findings = (c.findings || [])
      .slice(0, 8)
      .map((f) => `<li class="${f.severity === 'block' ? 'sc-warn' : ''}">${esc(f.check)}（${esc(f.where)}）${esc(f.detail)}</li>`)
      .join('');
    const shots = (c.shots || [])
      .map((p) => {
        const url = shotUrl(b.id, r.n, p);
        return `<a href="${url}" target="_blank" rel="noopener" class="bg-shot"><img src="${url}" alt="第 ${r.n} 轮截图：${esc(shotLabel(p))}" loading="lazy"><span>${esc(shotLabel(p))}</span></a>`;
      })
      .join('');
    const reviews = (r.reviews || [])
      .map((v) => {
        const probs = Object.entries(v.problems || {})
          .flatMap(([, x]) => x.problems || [])
          .slice(0, 6)
          .map((p) => `<li>${esc(p)}</li>`)
          .join('');
        return `<li>${esc(v.worker ? this.label(v.worker) : '—')} 评 ${esc(this.label(v.author))} 写的窗口：${esc(v.error || v.summary || '')}${probs ? `<ul>${probs}</ul>` : ''}</li>`;
      })
      .join('');
    // 验收清单：你的要求和底线逐条做到没有（有一条没做到就不过线）
    const reqs = r.requirements || [];
    const metN = reqs.filter((x) => x.met).length;
    const reqList = reqs
      .map(
        (x) => `<li class="${x.met ? 'bg-met' : 'bg-miss'}"><span class="bg-mark" role="img" aria-label="${x.met ? '做到了' : '没做到'}">${x.met ? '✓' : '✗'}</span><b>${esc(x.id)}</b> <span class="bg-src">${esc(x.source)}</span> ${esc(x.text)}${!x.met && x.why?.length ? `<div class="sc-sub">${esc(x.why.join('；'))}</div>` : ''}</li>`,
      )
      .join('');
    const atk = r.attack || null;
    const bugs = atk
      ? (atk.bugs || [])
          .map((x) => `<li class="bg-bug-${esc(x.severity)}"><b>${esc(SEVERITY[x.severity] || x.severity)}</b>${x.window ? ` · ${esc(this.winTitle(b, x.window))}` : ''}：${esc(x.problem)}${x.fix ? `<span class="sc-sub">建议：${esc(x.fix)}</span>` : ''}</li>`)
          .join('')
      : '';
    const attack = atk
      ? `<div class="bg-attack"><p><b>对抗手</b>${atk.worker ? `（${esc(this.label(atk.worker))}）` : ''}：${esc(atk.error || atk.summary || '')}${!atk.error && !bugs ? ' 没找到问题' : ''}</p>${bugs ? `<ul class="bg-list">${bugs}</ul>` : ''}</div>`
      : '';
    const back = Object.entries(r.reverted || {})
      .map(([id, [a, z]]) => `${esc(id)}（${esc(a)} → ${esc(z)}）`)
      .join('、');
    return `<details class="bg-round" ${r === b.rounds[b.rounds.length - 1] ? 'open' : ''}>
      <summary>第 ${r.n} 轮：质检${c.passed ? '通过' : '不通过'} ${esc(c.score ?? '—')} 分${reqs.length ? ` · 验收 ${metN}/${reqs.length}` : ''} · ${r.passed ? '过线' : '没过线'}</summary>
      ${reqList ? `<ul class="bg-reqs" aria-label="验收清单">${reqList}</ul>` : ''}
      ${back ? `<p class="sc-sub bg-back">分数降了，退回上一版：${back}</p>` : ''}
      ${scores ? `<ul class="bg-scores">${scores}</ul>` : ''}
      ${findings ? `<ul class="bg-findings">${findings}</ul>` : ''}
      ${shots ? `<div class="bg-shots">${shots}</div>` : ''}
      ${reviews ? `<ul class="bg-reviews">${reviews}</ul>` : ''}
      ${attack}
    </details>`;
  }

  async onClick(e) {
    const t = e.target;
    // 抽屉开着时点别处：先收起抽屉（这一下照常生效）
    if (this.drawer && !t.closest('.bg-drawer')) {
      this.drawer = '';
      this.paint('drawer', this.drawerHtml(this.st || {}, this.shownBuild()), true);
    }
    if (t.closest('[data-fromprod]')) {
      this.fromProduct = t.closest('[data-fromprod]').checked;
      return;
    }
    const rem = t.closest('[data-remember]');
    if (rem) {
      return this.act(() => this.api.profileAdd(rem.dataset.remember, rem.dataset.src || ''));
    }
    if (t.closest('[data-profile-add]')) {
      const box = this.el.querySelector('[data-profile-text]');
      const text = (box?.value || '').trim();
      if (!text) return box?.focus();
      return this.act(() => this.api.profileAdd(text));
    }
    const unp = t.closest('[data-unprofile]');
    if (unp) {
      return this.act(() => this.api.profileRemove(unp.dataset.unprofile));
    }
    if (t.closest('[data-submit]')) {
      const ids = this.mats.map((m) => m.id);
      const fromProduct = this.fromProduct && ids.length > 0;
      const done = await this.act(() => this.api.submit(this.draft, ids, fromProduct, this.form), true);
      if (!this.err) {
        this.mats = [];
        this.fromProduct = false;
        this.form = {};
      }
      return done;
    }
    const pickBtn = t.closest('[data-pick]');
    if (pickBtn) {
      this.fileInput.dataset.benchfile = pickBtn.dataset.pick;
      return this.fileInput.click();
    }
    const un = t.closest('[data-unmat]');
    if (un) {
      this.mats = this.mats.filter((m) => m.id !== un.dataset.unmat && m.of !== un.dataset.unmat);
      return this.render(true);
    }
    if (t.closest('[data-cancel]') || t.closest('[data-endproj]')) return this.act(() => this.api.cancel());
    const b = this.viewing || (this.st || {}).current;
    const form = t.closest('[data-form]');
    const opt = t.closest('[data-opt]');
    if (opt && form) {
      const ta = form.querySelector(`textarea[data-q="${opt.dataset.opt}"]`);
      const val = opt.dataset.val || opt.textContent;
      ta.value = ta.value ? `${ta.value}、${val}` : val;
      this.dirty = true;
      return;
    }
    if (t.closest('[data-answer]') || t.closest('[data-skip]')) {
      const answers = {};
      if (t.closest('[data-answer]')) form.querySelectorAll('textarea[data-q]').forEach((x) => (answers[x.dataset.q] = x.value));
      return this.act(() => this.api.answer(b.id, answers));
    }
    const dz = t.closest('[data-dz]');
    if (dz && this.pick) {
      const [key, value] = [dz.dataset.dz, dz.dataset.v];
      if (key === 'mode' || key === 'device') this.pick.view[key] = value;
      else this.pick[key] = value;
      return this.redrawDesign();
    }
    const sw = t.closest('[data-swatch]');
    if (sw && this.pick) {
      this.pick.accent = sw.dataset.swatch;
      return this.redrawDesign();
    }
    if (t.closest('[data-dlook]') && b) {
      this.dirty = false;
      return this.act(() => this.api.look(b.id));
    }
    if (t.closest('[data-dreset]') && this.pick) {
      this.pick = null;
      this.ensurePick(b);
      return this.redrawDesign();
    }
    if (t.closest('[data-addreq]')) {
      const li = document.createElement('li');
      li.dataset.req = '';
      li.innerHTML = '<b>新</b> <input type="text" aria-label="新要求" maxlength="300"><button type="button" class="link" data-delreq>删掉</button>';
      form.querySelector('.bg-sign-reqs').append(li);
      li.querySelector('input').focus();
      this.dirty = true;
      return;
    }
    if (t.closest('[data-delreq]')) {
      t.closest('li').remove();
      this.dirty = true;
      return;
    }
    if (t.closest('[data-sign]')) {
      const reqs = [...form.querySelectorAll('li[data-req]')].map((li) => ({ id: li.dataset.req, text: li.querySelector('input').value }));
      const note = form.querySelector('[data-note]').value;
      return this.act(() => this.api.sign(b.id, reqs, note, this.designChoice(b)));
    }
    if (t.closest('[data-pass]')) return this.act(() => this.api.review(b.id, 'pass'));
    if (t.closest('[data-change]')) {
      const win = form.querySelector('[data-cwin]').value;
      const want = (form.querySelector('[data-cwant]')?.value || '').trim();
      const comments = form
        .querySelector('[data-comments]')
        .value.split('\n')
        .map((x) => x.trim())
        .filter(Boolean)
        .map((text) => ({ text: want ? `问题：${text}；想要：${want}` : text, window: win }));
      if (!comments.length) {
        this.err = '写一下哪里要改';
        return this.render(true);
      }
      return this.act(() => this.api.review(b.id, 'change', comments));
    }
    if (t.closest('[data-evolve]')) return this.act(() => this.api.evolve(b.id));
    const dec = t.closest('[data-decide]');
    if (dec) {
      const action = dec.dataset.decide;
      const reqs =
        action === 'edit'
          ? [...form.querySelectorAll('li[data-req]')].map((li) => ({ id: li.dataset.req, text: li.querySelector('input').value }))
          : undefined;
      return this.act(() => this.api.decide(b.id, action, reqs));
    }
    const plug = t.closest('[data-plugin]');
    if (plug) return this.act(() => this.api.plugin(b.id, plug.dataset.plugin));
    if (t.closest('[data-nextver]')) {
      const items = ((b.evolve || {}).items || []).map((x) => `- ${x.title}：${x.why}`).join('\n');
      this.draft = `${b.request}\n\n这是下一版，要做的改进：\n${items}`;
      this.viewing = null;
      this.composing = true;
      this.pane = 'client';
      this.render(true);
      this.el.querySelector('[data-text]')?.focus();
      return;
    }
    if (t.closest('[data-netcheck]')) {
      this.net = 'busy';
      this.render(true);
      try {
        this.net = await this.api.netCheck();
        this.drawer = 'net';
      } catch (x) {
        this.net = null;
        this.err = `自检没做成：${x.message}`;
      }
      return this.render(true);
    }
    const view = t.closest('[data-view]');
    if (view) return this.switchTo(view.dataset.view);
    if (t.closest('[data-new]')) {
      this.composing = true;
      this.viewing = null;
      this.pane = 'client';
      this.render(true);
      this.el.querySelector('[data-text]')?.focus();
      return;
    }
    const dev = t.closest('button[data-device]');
    if (dev) {
      this.device = dev.dataset.device;
      return this.render(true);
    }
    const dr = t.closest('[data-drawer]');
    if (dr) {
      this.drawer = this.drawer === dr.dataset.drawer ? '' : dr.dataset.drawer;
      if (this.drawer === 'desk') this.loadDesk();
      return this.render(true);
    }
    const dsk = t.closest('[data-desk-tab],[data-desk-cat],[data-desk-mode],[data-desk-kind]');
    if (dsk) {
      if (dsk.dataset.deskTab) this.deskTab = dsk.dataset.deskTab;
      if (dsk.dataset.deskCat) this.deskCat = dsk.dataset.deskCat;
      if (dsk.dataset.deskMode) this.deskMode = dsk.dataset.deskMode;
      if (dsk.dataset.deskKind !== undefined) this.deskKind = dsk.dataset.deskKind;
      return this.paint('drawer', this.drawerHtml(this.st || {}, this.shownBuild()), true);
    }
    if (t.closest('[data-desk-refresh]')) return this.loadDesk(true);
    const sug = t.closest('[data-desk-sug]');
    if (sug) {
      try {
        this.desk = await this.api.deskSuggest(sug.dataset.deskSug, sug.dataset.accept === '1');
      } catch (x) {
        this.err = `没处理成：${x.message}`;
      }
      return this.paint('drawer', this.drawerHtml(this.st || {}, this.shownBuild()), true);
    }
    const go = t.closest('[data-pane-go]');
    if (go) {
      this.pane = go.dataset.paneGo;
      this.el.querySelectorAll('[data-pane-go]').forEach((x) => x.setAttribute('aria-pressed', String(x === go)));
      this.el.querySelector('.bg-cols').dataset.showpane = this.pane;
    }
  }

  /** 顶栏切换项目 / 点项目墙：看那个项目（当前在做的就回到当前）。 */
  async switchTo(id) {
    this.err = '';
    const cur = (this.st || {}).current;
    this.composing = false;
    if (!id) {
      this.composing = true;
      this.viewing = null;
    } else if (cur && cur.id === id) this.viewing = null;
    else {
      try {
        this.viewing = await this.api.get(id);
      } catch (x) {
        this.err = x.message;
      }
    }
    this.pane = 'stage';
    this.render(true);
  }

  async act(fn, submitted = false) {
    this.err = '';
    this.dirty = false;
    ++this.seq;
    try {
      this.st = await fn();
      ++this.seq;
      if (submitted) {
        this.draft = '';
        this.viewing = null;
        this.composing = false;
      }
    } catch (x) {
      this.err = x.message;
    }
    if (this.viewing && this.st && this.st.current && this.st.current.id === this.viewing.id) this.viewing = null;
    this.shown = JSON.stringify(this.st);
    this.render(true);
    this.schedule();
  }
}
