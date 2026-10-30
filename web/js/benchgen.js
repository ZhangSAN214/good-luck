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
  scouting: '正在找插件',
  building: '分头写窗口',
  checking: '质检和评审',
  reworking: '打回返工',
  done: '过线了',
  needs_you: '等你决定',
  failed: '失败',
  cancelled: '已叫停',
};
const STEPS = [
  ['planning', '拆需求'],
  ['scouting', '找插件'],
  ['building', '分头写'],
  ['checking', '质检评审'],
  ['reworking', '返工'],
  ['done', '过线'],
];
const WSTATUS = { waiting: '等着', running: '在写', done: '写好了', failed: '没写成' };
const VERDICT = { adopt: '直接用', idea: '只借思路', skip: '不用' };
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
  if (status === 'needs_you') return 4;
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
    el.addEventListener('click', (e) => this.onClick(e));
    el.addEventListener('input', (e) => {
      if (e.target.matches('[data-text]')) this.draft = e.target.value;
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
    try {
      this.st = await this.api.status();
    } catch {
      return;
    }
    // 正在打字、或在预览成品时不重画（不然输入框和成品会被刷掉）
    const typing = this.el.contains(document.activeElement?.closest('input,textarea') || null);
    if (this.isOpen() && !typing && !this.preview) this.render();
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
      ${busy ? '<button type="button" class="btn" data-cancel>叫停</button>' : ''}</div>`;
    const notes = [];
    if (st.unavailable) notes.push(`<p class="sc-warn">${esc(st.unavailable)}</p>`);
    if (this.err) notes.push(`<p class="sc-warn" role="alert">${esc(this.err)}</p>`);
    const ask = `<div class="sc-ask"><textarea data-text aria-label="想要什么样的工作台" placeholder="一句话说你想要什么台，比如：做一个管理我租的三套房子的台，看租金、到期、维修和水电" ${busy ? 'disabled' : ''}>${esc(this.draft)}</textarea>
      <div class="sc-row"><button type="button" class="btn primary" data-submit ${busy || st.unavailable ? 'disabled' : ''}>生成工作台</button>
      <span class="sc-hint">拆需求、找插件、几位助手分头写、质检加截图评审（不评自己写的），不过线就打回重做，最多 ${esc(st.max_rounds || 3)} 轮。花的是订阅次数。</span></div></div>`;
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
      ${ask}
      ${b ? this.buildHtml(b) : ''}
      ${library}
      ${recent ? `<details><summary>最近生成的</summary><ul class="sc-recent">${recent}</ul></details>` : ''}
    </div>`;
  }

  buildHtml(b) {
    const at = stepIndex(b.status);
    const stopped = ['needs_you', 'failed', 'cancelled'].includes(b.status);
    const reworked = (b.rounds || []).length > 1 || b.status === 'reworking';
    const steps = STEPS.map(([key, name], i) => {
      // 一轮就过线、或没找插件：那一格标「跳过」，不假装走过
      const skipped =
        (key === 'reworking' && !reworked && at > i) || (key === 'scouting' && !(b.sources || []).length && at > i);
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
    const finished = ['done', 'needs_you'].includes(b.status);
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
      <ol class="bg-steps" aria-label="进度">${steps}</ol>
      ${b.status === 'needs_you' ? '<p class="sc-warn">做满了还没过线：下面有每轮的分数、截图和评审意见，成品也能先打开看看。要不要换个说法再生成一次，由你决定。</p>' : ''}
      ${finished || draft ? `<div class="sc-row"><button type="button" class="btn ${finished ? 'primary' : ''}" data-preview="${esc(b.id)}">${openLabel}</button>
        <a class="link" href="/bench/${encodeURIComponent(b.id)}/index.html" target="_blank" rel="noopener">在新窗口打开</a></div>` : ''}
      ${wins ? `<table class="sc-subs"><thead><tr><th>窗口</th><th>谁写的</th><th>状态</th></tr></thead><tbody>${wins}</tbody></table>` : ''}
      ${this.sourcesHtml(b)}
      ${newParts}
      ${rounds}
      ${log ? `<details><summary>过程</summary><ol class="sc-log">${log}</ol></details>` : ''}
      ${this.preview === b.id ? `<iframe class="bg-frame" data-preview="${esc(b.id)}" title="${esc(b.title || '成品')}" sandbox="allow-scripts allow-forms allow-popups allow-downloads" src="/bench/${encodeURIComponent(b.id)}/index.html"></iframe>` : ''}
      ${this.viewing ? '<button type="button" class="link" data-back>回到当前</button>' : ''}
    </div>`;
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
    if (t.closest('[data-cancel]')) return this.act(() => this.api.cancel());
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
    try {
      this.st = await fn();
      if (submitted) {
        this.draft = '';
        this.viewing = null;
        this.preview = null;
      }
    } catch (x) {
      this.err = x.message;
    }
    this.render();
    this.schedule();
  }
}
