// 「超级电脑」：几位编程助手平级、没有固定主管（docs/SUPERCOMPUTER.md）。
// 在「工作台」页的标签里，干活时分段栏「工作台」加一个绿点。面板：每位助手今天的次数（可改）、是否可用；交活；
// 当前这件活拆成了哪些小活、各自分给了谁、为什么（当时的分数）；结果和最近几件活。
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
    button?.addEventListener('click', () => this.open());
    dlg.addEventListener('click', (e) => this.onClick(e));
    dlg.addEventListener('change', (e) => this.onChange(e));
    dlg.addEventListener('input', (e) => {
      if (e.target.matches('[data-text]')) this.draft = e.target.value;
    });
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
    if (this.isOpen() && !this.dlg.contains(document.activeElement?.closest('input,textarea') || null)) this.render();
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
    const ask = `<div class="sc-ask"><textarea data-text placeholder="要交的活，比如：给这个项目加一个导出 CSV 的命令，并补上测试" ${busy ? 'disabled' : ''}>${esc(this.draft)}</textarea>
      <div class="sc-row"><button type="button" class="btn primary" data-submit ${busy || st.unavailable ? 'disabled' : ''}>交给超级电脑</button>
      <span class="sc-hint">没有主管：拆分、每件小活、合并都按「擅长 + 今天剩余次数 + 手上的活」打分挑人，大家在同一个文件夹里同时干。只在你交活时干活。</span></div></div>`;
    const task = this.viewing || st.task;
    const recent = (st.recent || [])
      .filter((t) => !task || t.id !== task.id)
      .map((t) => `<li><button type="button" class="link" data-view="${esc(t.id)}">${esc((t.text || '').slice(0, 40))}</button> · ${esc(STATUS[t.status] || t.status)} · ${esc((t.created_at || '').slice(0, 16).replace('T', ' '))}</li>`)
      .join('');
    this.dlg.innerHTML = `${head}<div class="vb sc">${notes.join('')}
      <div class="sc-workers">${workers}</div>
      ${ask}
      ${task ? this.taskHtml(task) : ''}
      ${recent ? `<details><summary>最近几件活</summary><ul class="sc-recent">${recent}</ul></details>` : ''}
    </div>`;
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
    if (t.closest('[data-submit]')) return this.act(() => this.api.submit(this.draft), true);
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
    this.paintButton();
    this.render();
    this.schedule();
  }
}
