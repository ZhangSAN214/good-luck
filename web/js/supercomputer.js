// 「超级电脑」：几位编程助手平级、没有固定主管（docs/SUPERCOMPUTER.md）。
// 在「工作台」页的标签里，干活时分段栏「工作台」加一个绿点。面板：每位助手今天的次数（可改）、是否可用；交活；
// 当前这件活拆成了哪些小活、各自分给了谁、为什么（当时的分数）；结果和最近几件活。
// 管家：等你确认的定时活、排队、定时活（建 / 停 / 删 / 现在跑）、日报、审计链校验。
// 助手的名字、擅长的种类都来自接口，不写死。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const BUSY_MS = 3000;
const OPEN_MS = 10000;
const IDLE_MS = 30000;
const STATUS = {
  splitting: '正在拆分',
  working: '正在干',
  merging: '正在合并',
  done: '完成',
  failed: '失败',
  cancelled: '已叫停',
  waiting: '等着',
  running: '在干',
};
const RISK = { low: '低', medium: '中', high: '高' };
const REPEAT = { daily: [], workdays: [0, 1, 2, 3, 4], weekend: [5, 6] };
const ACT = {
  submit: '交活',
  finish: '活结束',
  queue: '排队',
  unqueue: '移出队',
  queue_wait: '队里等着',
  schedule_add: '建定时活',
  schedule_remove: '删定时活',
  schedule_toggle: '停用/启用定时活',
  schedule_run: '现在跑定时活',
  gate: '闸门判断',
  approve: '你批准',
  reject: '你没批',
  missed: '错过',
  limit: '改每天次数',
  wake: '叫醒',
  cancel: '叫停',
};
const KIND = { code: '写代码', frontend: '前端', test: '测试', docs: '文档', research: '查资料', review: '审查', split: '拆分', merge: '合并' };

/** 分数的一句话说明：总分 = 擅长 + 剩余额度 − 手上的活。 */
export function whyLine(score) {
  if (!score) return '';
  const pct = (x) => `${Math.round(Number(x) * 100)}%`;
  return `${Number(score.total).toFixed(2)} 分：擅长 ${pct(score.skill)} · 今天剩 ${pct(score.quota)} · 手上 ${score.busy} 件`;
}

export class SuperPanel {
  /**
   * @param {HTMLElement} dlg 画在哪里：弹窗（dialog）或「工作台」页里的一块
   * @param {HTMLButtonElement|null} button 干活时加绿点的按钮（分段栏的「工作台」）
   * @param {{api: Record<string, Function>, isOpen?: () => boolean}} opts isOpen：现在看得见吗（页面里用）
   */
  constructor(dlg, button, { api, isOpen = null }) {
    Object.assign(this, { dlg, button, api });
    this.isOpen = isOpen || (() => !!dlg.open);
    this.modal = dlg.tagName === 'DIALOG';
    this.st = null;
    this.viewing = null; // 正在看的旧任务（不是当前这件）
    this.err = '';
    this.draft = '';
    this.timer = null;
    this.form = { text: '', at: '09:00', repeat: 'daily', date: '' }; // 新定时活的草稿
    this.opened = new Set(); // 用户展开着的折叠块（重画时保持）
    this.reportDay = '';
    this.reportData = null;
    this.auditData = null;
    button?.addEventListener('click', () => this.open());
    dlg.addEventListener('click', (e) => this.onClick(e));
    dlg.addEventListener('change', (e) => this.onChange(e));
    dlg.addEventListener('input', (e) => {
      if (e.target.matches('[data-text]')) this.draft = e.target.value;
      const f = e.target.dataset && e.target.dataset.form;
      if (f) this.form[f] = e.target.value;
    });
    dlg.addEventListener(
      'toggle',
      (e) => {
        const k = e.target.dataset && e.target.dataset.k;
        if (!k) return;
        // 重画时带着 open 属性插进来的也会触发 toggle：只认真正的展开 / 收起
        if (e.target.open && !this.opened.has(k)) {
          this.opened.add(k);
          if (k === 'report') this.loadReport(this.reportDay);
          if (k === 'audit') this.loadAudit();
        } else if (!e.target.open) this.opened.delete(k);
      },
      true,
    );
    dlg.addEventListener('close', () => this.schedule());
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
    this.paintButton();
    if (this.isOpen() && !this.dlg.contains(document.activeElement?.closest('input,textarea,select') || null)) this.render();
  }

  paintButton() {
    if (!this.button) return;
    const st = this.st || {};
    this.button.classList.toggle('sc-on', !!st.busy);
    this.button.title = st.busy ? '超级电脑正在干活' : '工作台：超级电脑和 dsh';
  }

  async open() {
    this.err = '';
    if (this.modal && !this.dlg.open) this.dlg.showModal();
    try {
      this.st = await this.api.status();
    } catch (e) {
      this.err = e.message;
    }
    this.paintButton();
    this.render();
    this.schedule();
  }

  label(id) {
    const w = ((this.st && this.st.workers) || []).find((x) => x.id === id);
    return w ? w.label : id || '—';
  }

  render() {
    const st = this.st || {};
    const busy = !!st.busy;
    const head = `<div class="vh"><b>超级电脑</b>
      <span class="sc-state ${busy ? 'on' : ''}">${busy ? '干活中' : '空闲'}</span>
      <span class="sc-gap"></span>
      ${busy ? '<button type="button" class="btn" data-cancel>叫停</button>' : ''}
      ${this.modal ? '<button type="button" class="btn" data-close>关闭</button>' : ''}</div>`;
    const notes = [];
    if (st.unavailable) notes.push(`<p class="sc-warn">${esc(st.unavailable)}</p>`);
    if (this.err) notes.push(`<p class="sc-warn" role="alert">${esc(this.err)}</p>`);
    const workers = (st.workers || [])
      .map((w) => {
        const top = Object.entries(w.skills || {})
          .filter(([k]) => k !== 'split' && k !== 'merge')
          .sort((a, b) => b[1] - a[1])
          .slice(0, 3)
          .map(([k]) => KIND[k] || k)
          .join('、');
        const state = w.blocked
          ? `<span class="sc-warn">${esc(w.blocked)}</span>${w.cooling_until ? ` <button type="button" class="btn" data-wake="${esc(w.id)}">叫醒</button>` : ''}`
          : `<span class="sc-ok">可以接活</span>${w.running ? `，手上 ${w.running} 件` : ''}`;
        return `<div class="sc-worker" data-worker="${esc(w.id)}"><b>${esc(w.label)}</b>
          <div class="sc-row">今天 ${w.used} / <input type="number" min="0" max="1000" value="${w.limit}" data-limit="${esc(w.id)}" aria-label="${esc(w.label)} 每天最多几件"> 件</div>
          <div class="sc-row">${state}</div>
          <div class="sc-skills">最擅长：${esc(top || '—')}</div></div>`;
      })
      .join('');
    const ask = `<div class="sc-ask"><textarea data-text placeholder="要交的活，比如：给这个项目加一个导出 CSV 的命令，并补上测试">${esc(this.draft)}</textarea>
      <div class="sc-row"><button type="button" class="btn primary" data-submit ${st.unavailable ? 'disabled' : ''}>${busy ? '排进队' : '交给超级电脑'}</button>
      <span class="sc-hint">没有主管：拆分、每件小活、合并都按「擅长 + 今天剩余次数 + 手上的活」打分挑人，大家在同一个文件夹里同时干。只在你交活时干活。</span></div></div>`;
    const task = this.viewing || st.task;
    const recent = (st.recent || [])
      .filter((t) => !task || t.id !== task.id)
      .map((t) => `<li><button type="button" class="link" data-view="${esc(t.id)}">${esc((t.text || '').slice(0, 40))}</button> · ${esc(STATUS[t.status] || t.status)} · ${esc((t.created_at || '').slice(0, 16).replace('T', ' '))}</li>`)
      .join('');
    this.dlg.innerHTML = `${head}<div class="vb sc">${notes.join('')}
      <div class="sc-workers">${workers}</div>
      ${this.pendingHtml(st.butler)}
      ${ask}
      ${this.queueHtml(st.butler)}
      ${task ? this.taskHtml(task) : ''}
      ${this.butlerHtml(st.butler)}
      ${recent ? `<details><summary>最近几件活</summary><ul class="sc-recent">${recent}</ul></details>` : ''}
    </div>`;
  }

  fold(k, summary, body) {
    return `<details data-k="${k}" ${this.opened.has(k) ? 'open' : ''}><summary>${summary}</summary>${body}</details>`;
  }

  /** 等你确认的定时活：放在最上面，批准就排进队。 */
  pendingHtml(b) {
    const items = (b && b.pending) || [];
    if (!items.length) return '';
    const rows = items
      .map(
        (p) => `<li class="sc-pend" data-pend="${esc(p.id)}"><span class="sc-risk sc-risk-${esc(p.risk)}">风险${esc(RISK[p.risk] || p.risk)}</span>
        <span class="sc-ptext">${esc((p.text || '').slice(0, 120))}</span>
        <span class="sc-hint">${esc(p.why || '')}</span>
        <span class="sc-gap"></span>
        <button type="button" class="btn primary" data-approve="${esc(p.id)}">批准</button>
        <button type="button" class="btn" data-reject="${esc(p.id)}">不批</button></li>`,
      )
      .join('');
    return `<div class="sc-box sc-pending"><b>等你确认（${items.length}）</b><ul>${rows}</ul></div>`;
  }

  queueHtml(b) {
    const items = (b && b.queue) || [];
    if (!items.length) return '';
    const rows = items
      .map(
        (q, i) => `<li data-queue="${esc(q.id)}">${i + 1}. ${esc((q.text || '').slice(0, 80))}
        <span class="sc-hint">${esc(q.why || '')}${q.error ? ` · 还没开始：${esc(q.error)}` : ''}</span>
        <button type="button" class="link" data-unqueue="${esc(q.id)}">移出</button></li>`,
      )
      .join('');
    return `<div class="sc-box"><b>排队（${items.length}）</b><span class="sc-hint">前一件干完就接着干</span><ol class="sc-queue">${rows}</ol></div>`;
  }

  /** 管家：闸门说明、定时活、日报、审计。 */
  butlerHtml(b) {
    if (!b) return '';
    const gate = `<p class="sc-hint">自治闸门：定时活到点时「${esc(b.autonomy_text)}」，高风险永远等你点头；今天自己跑了 ${b.auto_used} / ${b.auto_max} 件。你亲手交的、点「现在跑」的都直接干。</p>`;
    const rows = (b.schedules || [])
      .map((s) => {
        const next = s.next ? `下次 ${esc(s.next.slice(5, 16).replace('T', ' '))}` : s.enabled ? '不会再跑' : '已停用';
        return `<li class="sc-sched ${s.enabled ? '' : 'off'}" data-sched="${esc(s.id)}">
          <b>${esc(s.when)}</b> <span class="sc-risk sc-risk-${esc(s.risk)}">风险${esc(RISK[s.risk] || s.risk)}</span>
          <span class="sc-ptext">${esc((s.text || '').slice(0, 80))}</span>
          <span class="sc-hint">${next}${s.last_result ? ` · 上次：${esc(s.last_result)}` : ''}</span>
          <span class="sc-gap"></span>
          <button type="button" class="link" data-run="${esc(s.id)}">现在跑</button>
          <button type="button" class="link" data-toggle="${esc(s.id)}" data-on="${s.enabled ? '0' : '1'}">${s.enabled ? '停用' : '启用'}</button>
          <button type="button" class="link" data-remove="${esc(s.id)}">删除</button></li>`;
      })
      .join('');
    const f = this.form;
    const opt = (v, t) => `<option value="${v}" ${f.repeat === v ? 'selected' : ''}>${t}</option>`;
    const form = `<div class="sc-row sc-form">
      <input type="text" data-form="text" placeholder="定时干什么，比如：每天检查依赖有没有安全更新，列个清单" value="${esc(f.text)}" aria-label="定时活的内容">
      <select data-form="repeat" aria-label="重复">${opt('daily', '每天')}${opt('workdays', '工作日')}${opt('weekend', '周末')}${opt('once', '只一次')}</select>
      ${f.repeat === 'once' ? `<input type="date" data-form="date" value="${esc(f.date)}" aria-label="哪一天">` : ''}
      <input type="time" data-form="at" value="${esc(f.at)}" aria-label="几点">
      <button type="button" class="btn" data-add-sched>加定时活</button></div>`;
    const sched = this.fold(
      'sched',
      `定时活（${(b.schedules || []).length}）`,
      `${gate}${rows ? `<ul class="sc-scheds">${rows}</ul>` : '<p class="sc-hint">还没有定时活。电脑关着错过了点，开机后 12 小时内补跑一次。</p>'}${form}`,
    );
    const r = this.reportData;
    const days = r ? [''].concat(r.days || []) : [''];
    const dayOpts = days
      .map((d) => `<option value="${esc(d)}" ${d === this.reportDay ? 'selected' : ''}>${d ? esc(d) : '今天（现算）'}</option>`)
      .join('');
    const report = this.fold(
      'report',
      '日报',
      `<div class="sc-row"><select data-report-day aria-label="哪天的日报">${dayOpts}</select>
       <span class="sc-hint">程序按记录汇总，不调模型、不花钱；每天的存在 data/super/reports/。</span></div>
       ${r ? `<div class="sc-report">${reportHtml(r.markdown)}</div>` : '<p class="sc-hint">加载中…</p>'}`,
    );
    const a = this.auditData;
    const chk = a ? a.check : null;
    const head = chk
      ? chk.ok
        ? `<span class="sc-ok">完好</span>，共 ${chk.count} 条`
        : `<span class="sc-warn">第 ${chk.broken_at} 条对不上：${esc(chk.why)}</span>`
      : `共 ${b.audit.count} 条`;
    const lines = a
      ? a.recent
          .map((e) => `<li>${esc(String(e.at).slice(5, 16).replace('T', ' '))} ${esc(ACT[e.act] || e.act)} <span class="sc-hint">${esc(auditLine(e.data))}</span></li>`)
          .join('')
      : '';
    const audit = this.fold(
      'audit',
      `审计链（${head}）`,
      `<p class="sc-hint">交活、批准、改次数、闸门判断、每件活的结局都记一条，前后用 hash 串起来；改了、删了、调换了都能查出来。日报还记下每天的锚点，整条重写也能发现。</p>
       ${lines ? `<ol class="sc-audit">${lines}</ol>` : ''}`,
    );
    return `<div class="sc-butler"><b>管家</b>${sched}${report}${audit}</div>`;
  }

  async loadReport(day) {
    try {
      this.reportData = await this.api.report(day);
      this.reportDay = day;
    } catch (x) {
      this.err = x.message;
    }
    this.render();
  }

  async loadAudit() {
    try {
      this.auditData = await this.api.audit();
    } catch (x) {
      this.err = x.message;
    }
    this.render();
  }

  addSchedule() {
    const f = this.form;
    const body = { text: f.text.trim(), at: f.at };
    if (f.repeat === 'once') body.once = f.date;
    else body.days = REPEAT[f.repeat] || [];
    return this.act(async () => {
      const st = await this.api.addSchedule(body);
      this.form.text = '';
      return st;
    });
  }

  taskHtml(t) {
    const rows = (t.subtasks || [])
      .map((s) => {
        const top = (s.ranking || [])[0];
        return `<tr><td>${s.n}. ${esc(s.title)}</td><td>${esc(KIND[s.kind] || s.kind)}</td><td>${esc(this.label(s.worker))}</td>
          <td class="sc-st-${esc(s.status)}">${esc(STATUS[s.status] || s.status)}</td><td class="sc-why">${esc(whyLine(top))}</td></tr>
          ${s.summary ? `<tr><td colspan="5"><details class="sc-sum"><summary>说明</summary><pre>${esc(s.summary)}</pre></details></td></tr>` : ''}`;
      })
      .join('');
    const step = (name, rec) =>
      rec && rec.worker ? `<p class="sc-hint">${name}：${esc(this.label(rec.worker))}（${esc(whyLine((rec.ranking || [])[0]))}）</p>` : '';
    const log = (t.log || []).map((l) => `<li>${esc(l.at.slice(11, 19))} ${esc(l.text)}</li>`).join('');
    return `<div class="sc-task">
      <p><b>${esc(STATUS[t.status] || t.status)}</b> · ${esc(t.text.slice(0, 120))}</p>
      <p class="sc-hint">工作文件夹：${esc(t.folder)}</p>
      ${step('拆分', t.split)}
      ${rows ? `<table class="sc-subs"><thead><tr><th>小活</th><th>种类</th><th>谁在干</th><th>状态</th><th>为什么是他</th></tr></thead><tbody>${rows}</tbody></table>` : ''}
      ${step('合并', t.merge)}
      ${t.result ? `<div class="sc-result"><b>结果</b><pre>${esc(t.result)}</pre></div>` : ''}
      ${log ? `<details><summary>过程</summary><ol class="sc-log">${log}</ol></details>` : ''}
      ${this.viewing ? '<button type="button" class="link" data-back>回到当前</button>' : ''}
    </div>`;
  }

  async onClick(e) {
    const t = e.target;
    if (t.closest('[data-close]')) return this.dlg.close();
    if (t.closest('[data-submit]')) return this.act(() => this.api.submit(this.draft, !!(this.st && this.st.busy)), true);
    const pick = (attr) => t.closest(`[${attr}]`)?.getAttribute(attr);
    let id;
    if ((id = pick('data-approve'))) return this.act(() => this.api.approve(id));
    if ((id = pick('data-reject'))) return this.act(() => this.api.reject(id));
    if ((id = pick('data-unqueue'))) return this.act(() => this.api.unqueue(id));
    if ((id = pick('data-run'))) return this.act(() => this.api.runSchedule(id));
    if ((id = pick('data-remove'))) return this.act(() => this.api.removeSchedule(id));
    if ((id = pick('data-toggle'))) {
      const on = t.closest('[data-toggle]').dataset.on === '1';
      return this.act(() => this.api.toggleSchedule(id, on));
    }
    if (t.closest('[data-add-sched]')) return this.addSchedule();
    if (t.closest('[data-cancel]')) return this.act(() => this.api.cancel());
    const wake = t.closest('[data-wake]');
    if (wake) return this.act(() => this.api.wake(wake.dataset.wake));
    const view = t.closest('[data-view]');
    if (view) {
      try {
        this.viewing = await this.api.task(view.dataset.view);
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

  onChange(e) {
    if (e.target.matches('[data-report-day]')) return this.loadReport(e.target.value);
    const f = e.target.dataset && e.target.dataset.form;
    if (f) {
      this.form[f] = e.target.value;
      if (f === 'repeat') this.render();
      return;
    }
    const input = e.target.closest('[data-limit]');
    if (!input) return;
    const n = Math.max(0, Math.min(1000, Math.round(Number(input.value) || 0)));
    this.act(() => this.api.limit(input.dataset.limit, n));
  }

  async act(fn, submitted = false) {
    this.err = '';
    try {
      this.st = await fn();
      if (submitted) {
        this.draft = '';
        this.viewing = null;
      }
    } catch (x) {
      this.err = x.message;
    }
    if (this.opened.has('audit')) this.auditData = null;
    if (this.opened.has('report')) this.reportData = null;
    this.paintButton();
    this.render();
    this.schedule();
    if (this.opened.has('audit')) this.loadAudit();
    if (this.opened.has('report')) this.loadReport(this.reportDay);
  }
}

/** 日报的 Markdown 画成网页：只认标题、表格、列表和段落（日报是程序写的，格式固定）。 */
export function reportHtml(md) {
  const out = [];
  let table = null;
  const flush = () => {
    if (!table) return;
    const [head, ...body] = table.filter((r) => !/^\|[-|\s]+\|$/.test(r));
    const cells = (r, tag) =>
      r.slice(1, -1).split('|').map((c) => `<${tag}>${esc(c.trim())}</${tag}>`).join('');
    out.push(`<table class="sc-subs"><thead><tr>${cells(head, 'th')}</tr></thead><tbody>${body.map((r) => `<tr>${cells(r, 'td')}</tr>`).join('')}</tbody></table>`);
    table = null;
  };
  for (const line of String(md || '').split('\n')) {
    if (line.startsWith('|')) {
      (table = table || []).push(line.trim());
      continue;
    }
    flush();
    if (line.startsWith('# ')) out.push(`<h4>${esc(line.slice(2))}</h4>`);
    else if (line.startsWith('## ')) out.push(`<h5>${esc(line.slice(3))}</h5>`);
    else if (line.startsWith('- ')) out.push(`<p class="sc-li">· ${esc(line.slice(2))}</p>`);
    else if (line.trim()) out.push(`<p class="sc-report-sum">${esc(line)}</p>`);
  }
  flush();
  return out.join('');
}

/** 审计记录的一句话：只挑能看懂的几项。 */
function auditLine(d) {
  if (!d) return '';
  const parts = [];
  if (d.text) parts.push(`「${String(d.text).slice(0, 40)}」`);
  if (d.status) parts.push(STATUS[d.status] || d.status);
  if (d.why) parts.push(d.why);
  if (d.when) parts.push(d.when);
  if (d.worker) parts.push(d.worker);
  if (d.limit !== undefined) parts.push(`每天 ${d.limit} 件`);
  if (d.task) parts.push(d.task);
  return parts.join(' · ');
}
