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
  if (['planning', 'researching', 'scouting', 'interviewing'].includes(b.status)) return 0;
  return (b.timeline || []).length ? 1 : 0;
}
const STEPS = [
  ['planning', '拆需求'],
  ['researching', '调研'],
  ['scouting', '找插件'],
  ['building', '分头写'],
  ['checking', '质检评审'],
  ['reworking', '返工'],
  ['done', '过线'],
];
const WSTATUS = { waiting: '等着', running: '在写', done: '写好了', failed: '没写成' };
const VERDICT = { adopt: '直接用', idea: '只借思路', skip: '不用' };
const NEWS = { news: '资讯', video: '视频', launch: '发布会', blog: '官方博客' };
const CHECKED = { true: '', false: '（链接打不开，没核实）', null: '（没核实）' };
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
    this.seq = 0; // 每次取状态加一：你点了按钮之后，之前发出去的定时刷新回来也不用（是旧的）
    el.addEventListener('click', (e) => this.onClick(e));
    el.addEventListener('input', (e) => {
      if (e.target.matches('[data-text]')) this.draft = e.target.value;
      if (e.target.closest('[data-form]')) this.dirty = true;
    });
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
    // 正在打字、或在预览成品时不重画（不然输入框和成品会被刷掉）
    const typing = this.el.contains(document.activeElement?.closest('input,textarea') || null);
    if (this.isOpen() && !typing && !this.preview && !this.dirty) this.render();
  }

  async open() {
    this.err = '';
    try {
      this.st = await this.api.status();
    } catch (e) {
      this.err = e.message;
    }
    this.render();
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
        (k) => `<li><b>${esc(k.repo)}</b>${k.worker ? `（${esc(this.label(k.worker))} 写的）` : ''}：${esc(k.what)}
          ${(k.borrow || []).length ? `<ul>${k.borrow.map((x) => `<li>借：${esc(x)}</li>`).join('')}${(k.avoid || []).map((x) => `<li>别学：${esc(x)}</li>`).join('')}</ul>` : ''}</li>`,
      )
      .join('');
    const plugins = (r.plugins || []).map((p) => `<li>${link(p)} · ${esc(p.stars)} 星 · ${esc(p.summary)}</li>`).join('');
    const papers = (r.papers || []).map((p) => `<li>${link(p)} · ${esc(p.source)} ${esc(p.date)} · ${esc(p.summary)}</li>`).join('');
    const news = (r.news || [])
      .map((n) => `<li>${esc(NEWS[n.kind] || '资讯')}：${link(n)} · ${esc(n.source)} ${esc(n.date)}${esc(CHECKED[String(n.verified)] ?? '')} · ${esc(n.summary)}</li>`)
      .join('');
    const notes = (r.notes || []).map((x) => `<p class="sc-sub">${esc(x)}</p>`).join('');
    return `<details class="bg-research" open><summary>调研报告：GitHub ${esc(c.github || 0)} 个 · 学习卡 ${esc((r.cards || []).length)} 张 · 论文 ${esc(c.papers || 0)} 篇 · 资讯和视频 ${esc(c.news || 0)} 条${c.plugins ? ` · dsh 插件 ${esc(c.plugins)} 个` : ''}</summary>
      ${notes}
      ${gh ? `<table class="sc-subs bg-gh"><thead><tr><th>项目</th><th>星</th><th>许可证</th><th>最近更新</th><th>结论</th><th>是什么</th></tr></thead><tbody>${gh}</tbody></table>` : ''}
      ${cards ? `<h4>学习卡</h4><ul class="bg-cards">${cards}</ul>` : ''}
      ${plugins ? `<h4>dsh 插件（建议安装，你点同意才装）</h4><ul class="bg-list">${plugins}</ul>` : ''}
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
        (key === 'researching' && !b.research && at > i);
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
      ${b.status === 'needs_you' ? '<p class="sc-warn">做满了还没过线：下面有每轮的分数、截图和评审意见，成品也能先打开看看。要不要换个说法再生成一次，由你决定。</p>' : ''}
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
      const wins = (p.windows || []).map((w) => `<li>${esc(w.title)}${w.id === p.hero ? ' <b>（主视觉）</b>' : ''}：${esc(w.purpose)}</li>`).join('');
      const ms = (p.milestones || []).map((m) => `<li>${esc(m.title)}：${esc((m.titles || []).join('、'))}</li>`).join('');
      const tech = (p.tech || []).map((t) => `<li>${esc(t)}</li>`).join('');
      const plugins = (p.plugins || []).length ? `<p class="sc-hint">建议给 dsh 装的插件：${esc(p.plugins.join('、'))}（下一步做安装）</p>` : '';
      return `<div class="bg-gate" data-form>${head}
        <h4>验收清单（你的要求可以改、删、加；底线不能删）</h4><ul class="bg-reqs bg-sign-reqs">${reqs}</ul>
        <button type="button" class="link" data-addreq>加一条要求</button>
        <h4>窗口</h4><ul class="bg-list">${wins}</ul>
        <h4>里程碑（每个都先演示给你看）</h4><ol class="bg-list">${ms}</ol>
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
      return this.act(() => this.api.sign(b.id, reqs, note));
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
    this.render();
    this.schedule();
  }
}
