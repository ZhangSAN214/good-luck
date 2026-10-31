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
const PHONE_W = 390;
// 项目室四个阶段（你是甲方，几位助手是乙方）
const STAGES = ['立项', '执行', '交付', '革新'];
const GATE_STAGE = { interview: 0, sign: 0, accept: 2 };

/** 项目走到哪个阶段（0 立项、1 执行、2 交付、3 革新）。 */
export function stageIndex(b) {
  const kind = (b.gate || {}).kind || '';
  if (kind in GATE_STAGE) return GATE_STAGE[kind];
  if (/^m\d+$/.test(kind)) return 1;
  if (b.evolve || b.status === 'evolving') return 3;
  if (b.status === 'delivered') return 2;
  if (['planning', 'researching', 'scouting', 'meeting', 'interviewing'].includes(b.status)) return 0;
  return (b.timeline || []).length ? 1 : 0;
}
const STEPS = [
  ['planning', '拆需求'],
  ['researching', '调研'],
  ['scouting', '找插件'],
  ['meeting', '方案会'],
  ['building', '分头写'],
  ['checking', '质检评审'],
  ['reworking', '返工'],
  ['done', '过线'],
];
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

/** 走到第几步（返工之后又回到质检时，返工那一格也算走过）。 */
export function stepIndex(status) {
  if (status === 'needs_you') return STEPS.findIndex(([k]) => k === 'reworking');
  const i = STEPS.findIndex(([k]) => k === status);
  return i < 0 ? -1 : i;
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
    this.preview = null; // 正在预览的成品编号
    this.err = '';
    this.draft = '';
    this.timer = null;
    this.net = null; // 联网自检的结果（null 没测过，'busy' 正在测）
    this.dirty = false; // 项目室的表单里填了还没交：定时刷新不重画，免得把你写的冲掉
    this.pick = null; // 选型台上点的（版型、配色、材质……、参考、窗口宽窄和去掉的），签字时一起交
    this.icons = null; // 预览里画图标用（零件库的 icons.js，第一次打开选型台时加载）
    this.seq = 0; // 每次取状态加一：你点了按钮之后，之前发出去的定时刷新回来也不用（是旧的）
    el.addEventListener('click', (e) => this.onClick(e));
    el.addEventListener('input', (e) => {
      if (e.target.matches('[data-text]')) this.draft = e.target.value;
      if (e.target.closest('[data-form]')) this.dirty = true;
    });
    el.addEventListener('change', (e) => {
      const x = e.target;
      if (!this.pick) return;
      if (x.matches('[data-dref]')) this.pick.refs[x.checked ? 'add' : 'delete'](x.dataset.dref);
      else if (x.matches('[data-dspan]')) {
        if (x.value) this.pick.spans[x.dataset.dspan] = Number(x.value);
        else delete this.pick.spans[x.dataset.dspan];
      } else if (x.matches('[data-ddrop]')) this.pick.drop[x.checked ? 'delete' : 'add'](x.dataset.ddrop);
      else return;
      this.redrawDesign();
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
    // 正在打字、或在预览成品时不重画（不然输入框和成品会被刷掉）
    const typing = this.el.contains(document.activeElement?.closest('input,textarea') || null);
    if (this.isOpen() && !typing && !this.preview && !this.dirty) {
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
      this.render();
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

  render() {
    const st = this.st || {};
    const busy = !!st.busy;
    const head = `<div class="vh"><b>生成器</b>
      <span class="sc-state ${busy ? 'on' : ''}">${busy ? '生成中' : '空闲'}</span>
      <span class="sc-gap"></span>
      <button type="button" class="btn" data-netcheck ${this.net === 'busy' ? 'disabled' : ''}>${this.net === 'busy' ? '正在自检…' : '联网自检'}</button>
      ${busy ? '<button type="button" class="btn" data-cancel>叫停</button>' : ''}</div>`;
    const notes = [];
    if (st.unavailable) notes.push(`<p class="sc-warn">${esc(st.unavailable)}</p>`);
    if (this.err) notes.push(`<p class="sc-warn" role="alert">${esc(this.err)}</p>`);
    const ask = `<div class="sc-ask"><textarea data-text aria-label="想要什么样的工作台" placeholder="一句话说你想要什么台，比如：做一个管理我租的三套房子的台，看租金、到期、维修和水电" ${busy ? 'disabled' : ''}>${esc(this.draft)}</textarea>
      <div class="sc-row"><button type="button" class="btn primary" data-submit ${busy || st.unavailable ? 'disabled' : ''}>生成工作台</button>
      <span class="sc-hint">${st.project ? '你是甲方：先回答几个问题 → 调研（至少 10 个 GitHub 项目、论文、资讯）→ 方案书你签字 → 按里程碑演示，你通过或提意见 → 交付你验收 → 下一版提案。停下来等你时不花次数。' : `拆需求、找插件、几位助手分头写、质检加截图评审（不评自己写的），不过线就打回重做，最多 ${esc(st.max_rounds || 3)} 轮。`}花的是订阅次数。</span></div></div>`;
    const b = this.viewing || st.current;
    const recent = (st.recent || [])
      .filter((x) => !b || x.id !== b.id)
      .map(
        (x) =>
          `<li><button type="button" class="link" data-view="${esc(x.id)}">${esc(x.title || (x.request || '').slice(0, 30))}</button> · ${esc(STATUS[x.status] || x.status)}${x.score != null ? ` · ${esc(x.score)} 分` : ''} · ${esc((x.created_at || '').slice(0, 16).replace('T', ' '))}</li>`,
      )
      .join('');
    const lib = st.library || {};
    const parts = (lib.parts || []).map((p) => `<li><b>${esc(p.name)}</b>：${esc(p.use)}</li>`).join('');
    const library = parts
      ? `<details><summary>零件库里自己长出来的零件（${(lib.parts || []).length} 个）</summary><ul class="bg-list">${parts}</ul></details>`
      : '';
    this.el.innerHTML = `${head}<div class="vb sc bg">${notes.join('')}
      ${this.netHtml()}
      ${ask}
      ${b ? this.buildHtml(b) : ''}
      ${library}
      ${recent ? `<details><summary>最近生成的</summary><ul class="sc-recent">${recent}</ul></details>` : ''}
    </div>`;
    if (this.el.querySelector('[data-dpreview]')) this.drawPreview();
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

  buildHtml(b) {
    const at = stepIndex(b.status);
    const stopped = ['needs_you', 'failed', 'cancelled'].includes(b.status);
    const reworked = (b.rounds || []).length > 1 || b.status === 'reworking';
    const steps = STEPS.map(([key, name], i) => {
      // 一轮就过线、或没找插件：那一格标「跳过」，不假装走过
      const skipped =
        (key === 'reworking' && !reworked && at > i) ||
        (key === 'scouting' && !(b.sources || []).length && at > i) ||
        (key === 'researching' && !b.research && at > i) ||
        (key === 'meeting' && !b.meeting && at > i);
      const cls = skipped ? 'skip' : i < at || b.status === 'done' ? 'past' : i === at && !stopped ? 'now' : '';
      return `<li class="${cls}">${esc(name)}${skipped ? '（跳过）' : ''}</li>`;
    }).join('');
    const wins = (b.windows || [])
      .map(
        (w) => `<tr><td>${esc(w.title)}</td><td>${esc(w.author ? this.label(w.author) : '—')}</td>
          <td class="sc-st-${esc(w.status)}">${esc(WSTATUS[w.status] || w.status)}${(w.rework || []).length ? `（返工 ${w.rework.length} 次）` : ''}</td></tr>`,
      )
      .join('');
    const rounds = (b.rounds || []).map((r) => this.roundHtml(b, r)).join('');
    const finished = ['done', 'needs_you', 'delivered'].includes(b.status) || (b.gate || {}).kind === 'accept';
    // 没跑完也能先看草稿：只要有窗口写好了（跑到一半看到的是当时的样子，还会再改）
    const draft = !finished && (b.windows || []).some((w) => w.status === 'done');
    const openLabel = this.preview === b.id ? (finished ? '收起成品' : '收起草稿') : finished ? '打开成品' : '先看草稿';
    const log = (b.log || []).map((l) => `<li>${esc(l.at.slice(11, 19))} ${esc(l.text)}</li>`).join('');
    const newParts = (b.new_parts || []).length
      ? `<p class="sc-hint">这次新收进零件库：${esc(b.new_parts.join('、'))}</p>`
      : '';
    return `<div class="sc-task bg-build">
      <p><b class="bg-title">${esc(b.title || '还没起名')}</b> · <span class="bg-status bg-${esc(b.status)}">${esc(STATUS[b.status] || b.status)}</span>${b.score != null ? ` · 评审平均 ${esc(b.score)} 分` : ''}</p>
      <p class="sc-hint">你说的：${esc((b.request || '').slice(0, 200))}</p>
      ${b.timeline ? this.projectHtml(b) : ''}
      <ol class="bg-steps" aria-label="进度">${steps}</ol>
      ${this.teamHtml(b)}
      ${this.stuckHtml(b)}
      ${finished || draft ? `<div class="sc-row"><button type="button" class="btn ${finished ? 'primary' : ''}" data-preview="${esc(b.id)}">${openLabel}</button>
        <a class="link" href="/bench/${encodeURIComponent(b.id)}/index.html" target="_blank" rel="noopener">在新窗口打开</a></div>` : ''}
      ${wins ? `<table class="sc-subs"><thead><tr><th>窗口</th><th>谁写的</th><th>状态</th></tr></thead><tbody>${wins}</tbody></table>` : ''}
      ${this.researchHtml(b)}
      ${this.sourcesHtml(b)}
      ${newParts}
      ${rounds}
      ${log ? `<details><summary>过程</summary><ol class="sc-log">${log}</ol></details>` : ''}
      ${this.preview === b.id ? `<iframe class="bg-frame" data-preview="${esc(b.id)}" title="${esc(b.title || '成品')}" sandbox="allow-scripts allow-forms allow-popups allow-downloads" src="/bench/${encodeURIComponent(b.id)}/index.html"></iframe>` : ''}
      ${this.viewing ? '<button type="button" class="link" data-back>回到当前</button>' : ''}
    </div>`;
  }

  /** 项目室：阶段条、现在等你做的事（关口）、变更单、时间线。 */
  projectHtml(b) {
    if (!(b.timeline || []).length && !b.gate) return '';
    const at = stageIndex(b);
    const stages = STAGES.map(
      (n, i) => `<li class="${i < at || b.status === 'delivered' && i <= at ? 'past' : i === at ? 'now' : ''}">${esc(n)}</li>`,
    ).join('');
    const changes = (b.changes || [])
      .map(
        (c) => `<li class="bg-ch-${esc(c.status)}"><b>${esc(c.id)}</b> ${esc(c.text)}${c.window ? `（${esc(this.winTitle(b, c.window))}）` : ''} · ${esc({ open: '处理中', done: '改好了', failed: '没改成' }[c.status] || c.status)}${c.reply ? `<div class="sc-sub">${esc(c.reply.slice(-160))}</div>` : ''}</li>`,
      )
      .join('');
    const timeline = (b.timeline || [])
      .map((t) => `<li><span class="bg-src">${esc(t.at.slice(5, 16).replace('T', ' '))} · ${esc(t.stage)}</span> <b>${esc(t.title)}</b>${t.detail ? ` · ${esc(t.detail)}` : ''}</li>`)
      .join('');
    return `<section class="bg-project" aria-label="项目室">
      <ol class="bg-stages" aria-label="项目阶段">${stages}</ol>
      ${this.gateHtml(b)}
      ${b.status === 'delivered' ? this.evolveHtml(b) : ''}
      ${changes ? `<details open><summary>变更单（${(b.changes || []).length} 张）</summary><ul class="bg-list bg-changes">${changes}</ul></details>` : ''}
      ${timeline ? `<details><summary>项目时间线</summary><ol class="bg-list bg-timeline">${timeline}</ol></details>` : ''}
    </section>`;
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

  /** 做满轮数没过线：哪几条没做到、卡在哪、建议；不在项目室时你选再跑一轮 / 改要求 / 就这样。 */
  stuckHtml(b) {
    const k = b.stuck;
    if (!k) return '';
    const missing = (k.missing || [])
      .map((m) => `<li class="bg-miss"><b>${esc(m.id)}</b> ${esc(m.text)}<span class="sc-sub">${m.missed > 1 ? `连续 ${esc(m.missed)} 轮没做到` : '这轮没做到'}${(m.why || []).length ? `：${esc(m.why.join('；'))}` : ''}</span></li>`)
      .join('');
    const advice = (k.advice || []).map((a) => `<li>${esc(a)}</li>`).join('');
    const choose = b.status === 'needs_you';
    let form = '';
    if (choose) {
      const last = (b.rounds || []).slice(-1)[0] || {};
      const reqs = (last.requirements || [])
        .map((r) =>
          String(r.id).startsWith('b')
            ? `<li class="bg-req-base"><b>${esc(r.id)}</b> <span class="bg-src">底线</span> ${esc(r.text)}</li>`
            : `<li data-req="${esc(r.id)}"><b>${esc(r.id)}</b> <input type="text" value="${esc(r.text)}" aria-label="要求 ${esc(r.id)}" maxlength="300"><button type="button" class="link" data-delreq aria-label="删掉 ${esc(r.id)}">删掉</button></li>`,
        )
        .join('');
      form = `<details class="bg-edit"><summary>改要求</summary><ul class="bg-reqs bg-sign-reqs">${reqs}</ul>
        <button type="button" class="link" data-addreq>加一条要求</button>
        <div class="sc-row"><button type="button" class="btn" data-decide="edit">按改好的要求再跑一轮</button></div></details>
        <div class="sc-row"><button type="button" class="btn primary" data-decide="more">再跑一轮</button><button type="button" class="btn" data-decide="accept">就这样，收下</button></div>`;
    }
    return `<div class="bg-stuck" ${choose ? 'data-form' : ''}>
      <p><b>${choose ? '做满了还没过线，你来定' : '交付前没过线的地方'}</b>${(k.trend || []).length ? ` · 评审分 ${esc(k.trend.join(' → '))}` : ''}</p>
      ${missing ? `<ul class="bg-reqs">${missing}</ul>` : ''}
      ${(k.blocks || []).length ? `<p class="sc-sub">质检必须修：${esc(k.blocks.join('、'))}</p>` : ''}
      ${(k.high || []).length ? `<p class="sc-sub">对抗手的严重问题：${esc(k.high.join('；'))}</p>` : ''}
      ${advice ? `<h4>建议</h4><ul class="bg-list">${advice}</ul>` : ''}
      ${form}
    </div>`;
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

  /** 选型台：左边每项的选项（带「推荐」和理由）、参考作品、窗口宽窄；右边实时预览。 */
  designHtml(b) {
    const d = (b.proposal || {}).design;
    if (!d || !(d.dims || []).length) return '';
    const pick = this.ensurePick(b);
    return `<section class="bg-design" aria-label="设计选型">
      <h4>设计选型（点一下右边马上变，不花次数）</h4>
      <div class="bg-design-cols"><div class="bg-design-opts" data-dopts>${this.designOpts(b, d, pick)}</div>
      <div class="bg-design-view">${this.viewBar(pick)}
        <div class="bg-dframe" data-dframe><iframe data-dpreview title="工作台预览" srcdoc="${esc('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><link rel="stylesheet" href="/kit/kit.css"></head><body class="k-root"></body></html>')}"></iframe></div>
        <p class="sc-hint">预览用的是成品同一套样式；窗口里先放用途、零件和按钮，内容开工后才写。</p></div></div>
    </section>`;
  }

  viewBar(pick) {
    const seg = (key, opts) => opts.map(([v, t]) => `<button type="button" class="chip" data-dz="${key}" data-v="${v}" aria-pressed="${pick.view[key] === v}">${t}</button>`).join('');
    return `<div class="bg-dview" data-dviewbar>${seg('device', [['desk', '电脑'], ['phone', '手机']])}<span class="sc-gap"></span>${seg('mode', [['light', '浅色'], ['dark', '深色']])}</div>`;
  }

  designOpts(b, d, pick) {
    const dims = (d.dims || [])
      .map((dim) => {
        const rec = (d.recommend || {})[dim.key];
        const cur = dim.options.find((o) => o.id === pick[dim.key]) || dim.options[0];
        const chips = dim.options
          .map((o) => `<button type="button" class="chip" data-dz="${esc(dim.key)}" data-v="${esc(o.id)}" aria-pressed="${o.id === pick[dim.key]}" title="${esc(o.hint)}">${esc(o.label)}${o.id === rec ? '<span class="bg-rec">推荐</span>' : ''}</button>`)
          .join('');
        const why = (d.why || {})[dim.key];
        return `<div class="bg-dim"><b>${esc(dim.label)}</b><span class="bg-opts">${chips}</span>
          <span class="sc-sub">${esc(cur.hint)}${why && cur.id === rec ? `。推荐理由：${esc(why)}` : ''}</span></div>`;
      })
      .join('');
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
    return `${dims}
      ${refs ? `<div class="bg-dim"><b>参考作品（勾上的写进规格，助手照着借鉴）</b><ul class="bg-drefs">${refs}</ul></div>` : ''}
      <div class="bg-dim"><b>窗口（去掉勾就不做，宽度可调）</b><ul class="bg-dwins">${wins}</ul></div>
      <button type="button" class="link" data-dreset>恢复推荐</button>`;
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
    for (const k of ['material', 'density', 'corner', 'type']) root.dataset[`k${k[0].toUpperCase()}${k.slice(1)}`] = pick[k] || '';
    const ic = (name, size = 17) => this.icons(name, { size });
    const cards = this.arranged(b)
      .map((w) => {
        const hero = w.id === p.hero;
        const acts = (w.actions || []).slice(0, 3).map((a, i) => `<button type="button" class="k-btn${i === 0 ? ' k-primary' : ''}">${esc(a)}</button>`).join('');
        const parts = (w.parts || []).slice(0, 5).map((x) => `<span class="k-badge">${esc(x)}</span>`).join('');
        const body = hero
          ? `<div class="k-stack" style="padding:22px 22px 18px;flex:1;justify-content:flex-end;background:linear-gradient(160deg,var(--k-accent-soft),transparent 70%)"><span class="k-kicker">${esc(w.title)}</span><p class="k-display">${esc(d.tagline || p.subtitle || w.title)}</p><p class="k-muted">${esc(w.purpose)}</p><div class="k-row">${parts}</div><div class="k-row">${acts}</div></div>`
          : `<div class="k-stack"><p class="k-muted">${esc(w.purpose)}</p><div class="k-row">${parts}</div><div class="k-row">${acts}</div></div>`;
        return `<section class="k-card k-span-${w.span}${hero ? ' k-hero' : ''}" data-pw="${esc(w.id)}" data-span="${w.span}"><div class="k-card-h"><span class="k-ico-wrap">${ic(w.icon || 'layout-grid')}</span><div><h2>${esc(w.title)}</h2>${w.sub ? `<div class="k-sub">${esc(w.sub)}</div>` : ''}</div></div><div class="k-card-b">${body}</div></section>`;
      })
      .join('');
    doc.body.innerHTML = `<div class="k-page"><header class="k-topbar"><div class="k-brand"><span class="k-logo">${ic(p.icon || 'layers', 20)}</span><div><h1>${esc(p.title || '')}</h1><div class="k-sub">${esc(p.subtitle || '')}</div></div></div></header><main class="k-grid">${cards}</main></div>`;
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
            ${(q.options || []).length ? `<span class="bg-opts">${q.options.map((o) => `<button type="button" class="chip" data-opt="${esc(q.id)}">${esc(o)}</button>`).join('')}</span>` : ''}
            <textarea data-q="${esc(q.id)}" rows="2" aria-label="${esc(q.q)}" placeholder="可以不填"></textarea></label></li>`,
        )
        .join('');
      return `<div class="bg-gate" data-form>${head}<ol class="bg-qs">${qs}</ol>
        <div class="sc-row"><button type="button" class="btn primary" data-answer>交回答，开始做方案</button><button type="button" class="btn" data-skip>全部跳过</button>${end}</div></div>`;
    }
    if (g.kind === 'sign') {
      const p = b.proposal || {};
      const reqs = (p.requirements || [])
        .map((r) =>
          String(r.id).startsWith('b')
            ? `<li class="bg-req-base"><b>${esc(r.id)}</b> <span class="bg-src">底线</span> ${esc(r.text)}</li>`
            : `<li data-req="${esc(r.id)}"><b>${esc(r.id)}</b> <input type="text" value="${esc(r.text)}" aria-label="要求 ${esc(r.id)}" maxlength="300"><button type="button" class="link" data-delreq aria-label="删掉 ${esc(r.id)}">删掉</button></li>`,
        )
        .join('');
      const wins = (p.windows || [])
        .map(
          (w) => `<li><details><summary><b>${esc(w.title)}</b>${w.id === p.hero ? ' （主视觉）' : ''}：${esc(w.purpose)}</summary>
            ${w.data ? `<p><b>数据</b>：${esc(w.data)}</p>` : ''}
            ${(w.actions || []).length ? `<p><b>能做的事</b>：${esc(w.actions.join('、'))}</p>` : ''}
            ${(w.parts || []).length ? `<p><b>用到的零件</b>：${esc(w.parts.join('、'))}</p>` : ''}</details></li>`,
        )
        .join('');
      const links = (p.links || []).map((x) => `<li>${esc(x)}</li>`).join('');
      const shared = (p.shared || []).map((x) => `<li>${esc(x)}</li>`).join('');
      const ms = (p.milestones || []).map((m) => `<li>${esc(m.title)}：${esc((m.titles || []).join('、'))}</li>`).join('');
      const tech = (p.tech || []).map((t) => `<li>${esc(t)}</li>`).join('');
      const plugins = (p.plugins || []).length ? `<p class="sc-hint">建议给 dsh 装的插件：${esc(p.plugins.join('、'))}（在下面调研报告里点「申请装上」）</p>` : '';
      return `<div class="bg-gate" data-form>${head}
        ${this.designHtml(b)}
        <h4>验收清单（你的要求可以改、删、加；底线不能删）</h4><ul class="bg-reqs bg-sign-reqs">${reqs}</ul>
        <button type="button" class="link" data-addreq>加一条要求</button>
        <h4>窗口（点开看数据、操作和零件）</h4><ul class="bg-list bg-wins">${wins}</ul>
        ${links ? `<h4>窗口之间怎么联动</h4><ul class="bg-list">${links}</ul>` : ''}
        ${shared ? `<h4>共用信息（只放一份，各窗口都从这里读）</h4><ul class="bg-list">${shared}</ul>` : ''}
        <h4>里程碑（每个都先演示给你看）</h4><ol class="bg-list">${ms}</ol>
        ${(p.meeting || []).length ? `<h4>方案会：谁提了什么、为什么采纳</h4><ul class="bg-list">${p.meeting.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
        ${tech ? `<h4>借鉴的项目</h4><ul class="bg-list">${tech}</ul>` : ''}
        ${plugins}
        <label class="bg-note">补充一句（可不填）<textarea data-note rows="2"></textarea></label>
        <div class="sc-row"><button type="button" class="btn primary" data-sign>签字开工</button>
        <a class="link" href="/bench/${encodeURIComponent(b.id)}/${encodeURIComponent('方案书.md')}" target="_blank" rel="noopener">方案书全文</a>${end}</div></div>`;
    }
    // 里程碑演示 / 交付验收：先看（草稿或成品），再通过或提意见
    const accept = g.kind === 'accept';
    const m = accept ? null : (b.milestones || []).find((x) => `m${x.n}` === g.kind);
    const demos = m ? m.demos || [] : [];
    const shots = demos.length
      ? demos
          .slice(-2)
          .map((list, i, arr) => `<figure class="bg-demo"><figcaption>${arr.length > 1 && i === 0 ? '改之前' : arr.length > 1 ? '改之后' : '演示截图'}</figcaption>${(list || []).map((p) => `<a href="/bench/${encodeURIComponent(b.id)}/${p.split(/[\\/]/).map(encodeURIComponent).join('/')}" target="_blank" rel="noopener"><img src="/bench/${encodeURIComponent(b.id)}/${p.split(/[\\/]/).map(encodeURIComponent).join('/')}" alt="里程碑截图" loading="lazy"></a>`).join('')}</figure>`)
          .join('')
      : '';
    const scope = accept ? (b.windows || []).map((w) => w.id) : (((b.proposal || {}).milestones || [])[Number(g.kind.slice(1)) - 1] || {}).windows || [];
    const opts = scope.map((id) => `<option value="${esc(id)}">${esc(this.winTitle(b, id))}</option>`).join('');
    return `<div class="bg-gate" data-form>${head}
      ${shots ? `<div class="bg-demos">${shots}</div>` : ''}
      <p class="sc-hint">${accept ? '点下面「打开成品」逐条试用验收清单。' : '点下面「先看草稿」试用这一步做好的部分。'}</p>
      <label class="bg-note">要改的地方（一条一行）<textarea data-comments rows="3" placeholder="例如：地图再大一点"></textarea></label>
      <label class="bg-note">针对哪个窗口 <select data-cwin><option value="">这一步的全部窗口</option>${opts}</select></label>
      <div class="sc-row"><button type="button" class="btn primary" data-pass>${accept ? '验收通过' : '通过，做下一步'}</button><button type="button" class="btn" data-change>提意见，让他们改</button>${end}</div></div>`;
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
    if (t.closest('[data-submit]')) return this.act(() => this.api.submit(this.draft), true);
    if (t.closest('[data-cancel]') || t.closest('[data-endproj]')) return this.act(() => this.api.cancel());
    const b = this.viewing || (this.st || {}).current;
    const form = t.closest('[data-form]');
    const opt = t.closest('[data-opt]');
    if (opt && form) {
      const ta = form.querySelector(`textarea[data-q="${opt.dataset.opt}"]`);
      ta.value = ta.value ? `${ta.value}、${opt.textContent}` : opt.textContent;
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
      const comments = form
        .querySelector('[data-comments]')
        .value.split('\n')
        .map((x) => x.trim())
        .filter(Boolean)
        .map((text) => ({ text, window: win }));
      if (!comments.length) {
        this.err = '写一下哪里要改';
        return this.render();
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
      this.render();
      this.el.querySelector('[data-text]')?.focus();
      return;
    }
    if (t.closest('[data-netcheck]')) {
      this.net = 'busy';
      this.render();
      try {
        this.net = await this.api.netCheck();
      } catch (x) {
        this.net = null;
        this.err = `自检没做成：${x.message}`;
      }
      return this.render();
    }
    const pv = t.closest('button[data-preview]');
    if (pv) {
      this.preview = this.preview === pv.dataset.preview ? null : pv.dataset.preview;
      this.render();
      this.el.querySelector('iframe[data-preview]')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    const view = t.closest('[data-view]');
    if (view) {
      try {
        this.viewing = await this.api.get(view.dataset.view);
      } catch (x) {
        this.err = x.message;
      }
      return this.render();
    }
    if (t.closest('[data-back]')) {
      this.viewing = null;
      return this.render();
    }
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
        this.preview = null;
      }
    } catch (x) {
      this.err = x.message;
    }
    if (this.viewing && this.st && this.st.current && this.st.current.id === this.viewing.id) this.viewing = null;
    this.shown = JSON.stringify(this.st);
    this.render();
    this.schedule();
  }
}
