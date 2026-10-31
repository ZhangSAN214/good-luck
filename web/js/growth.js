// 成长（成员系统 P6）：每周一成员读一遍上一周的经历，最多改 3 条自我档案，每条都引用真实经历；
// 程序把关、另一位成员评审后才写进档案。这里是成长时间线：改了什么、为什么、被拦下的原因，可以一条条撤回。
// 另有从全部经历累积的习惯、按领域的熟练度和每月一份「这个月的我」（程序算，不花钱）。
// 只用于私聊、群聊、朋友圈和生活；工作和办公室不读取。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const SECTION = { personality: '性格', values: '三观', style: '说话', likes: '喜好' };
const KIND = { change: '改了', rejected: '没改', skip: '这周没改', revert: '撤回' };

// 「personality.五项.好奇」→「性格分·好奇」；「likes.最爱」→「喜好·最爱」
const where = (path) => {
  const [sec, key, trait] = String(path || '').split('.');
  if (key === '五项') return `性格分·${trait || ''}`;
  return `${SECTION[sec] || sec}·${key || ''}`;
};

export const describe = (e) => {
  if (e.kind === 'skip') return e.reason || '';
  if (e.kind === 'revert') return `撤回了「${where(e.path)}」的那次改动`;
  const what =
    e.op === 'score'
      ? `${where(e.path)} ${e.old ?? '?'} → ${e.new ?? (e.old ?? 0) + e.delta}`
      : `${where(e.path)} ${e.op === 'add' ? '加上' : '去掉'}「${e.value}」`;
  return what;
};

export class GrowthPanel {
  constructor({ api, now = () => Date.now() } = {}) {
    this.api = api; // {get(id), run(id), revert(id, entryId), undo(id, token), clear(id)}
    this.now = now;
    this.member = null;
    this.g = null;
    this.msg = '';
    this.busy = false;
    this.armed = 0;
    this.el = null;
  }

  async mount(el, member) {
    this.el = el;
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      this.onClick(e);
    });
    if (member !== this.member) {
      this.member = member;
      this.g = null;
      this.msg = '';
    }
    this.render();
    await this.load();
  }

  async load() {
    if (!this.member) return;
    try {
      this.g = await this.api.get(this.member);
    } catch (e) {
      this.msg = e.message;
    }
    this.render();
  }

  entry(e) {
    const tags = [
      `<span>${esc(e.week || '')}</span>`,
      e.score != null ? `<span>评审 ${e.score} 分</span>` : '',
      e.user ? '<span>受你影响</span>' : '',
      e.reverted ? '<span>已撤回</span>' : '',
    ].join('');
    const undo = e.kind === 'change' && !e.reverted ? `<button type="button" class="link" data-grevert="${e.id}">撤回</button>` : '';
    const why = e.kind === 'change' && e.why ? `<br>${esc(e.why)}` : '';
    const reason = e.kind === 'rejected' ? `<br><small>没改的原因：${esc(e.reason)}${e.clash_name ? `（${esc(e.clash_name)}）` : ''}</small>` : '';
    const quote = e.quote && e.kind !== 'skip' ? `<br><small>引用的经历：「${esc(e.quote)}」</small>` : '';
    return `<li class="gr-${esc(e.kind)}"><div class="pp-mh"><b>${esc(KIND[e.kind] || e.kind)}</b>${tags}${undo}</div><p>${esc(describe(e))}${why}${reason}${quote}</p></li>`;
  }

  // 成长总账：设定、口癖、和你的阶段、对你的看法、收藏排成一条（新的在前），能撤回的给「撤回」
  book(g) {
    const rows = g.book || [];
    if (!rows.length) return '';
    const names = g.book_sources || {};
    const day = (at) => String(at || '').slice(0, 10);
    const items = rows
      .slice(0, 60)
      .map(
        (c) =>
          `<li class="gr-book${c.undone ? ' gr-undone' : ''}"><div class="pp-mh"><b>${esc(names[c.source] || c.source)}</b><span>${esc(day(c.at))}</span>${c.undone ? '<span>已撤回</span>' : ''}${c.undo ? `<button type="button" class="link" data-gundo="${esc(c.undo)}">撤回</button>` : ''}</div><p>${esc(c.text)}${c.why ? `<br><small>${esc(c.why)}</small>` : ''}</p></li>`,
      )
      .join('');
    return `<details class="pf-sec gr-bookbox" open><summary><b>全部变化</b><span>设定、口癖、和你、看法、收藏 · 共 ${rows.length} 条</span></summary><ul class="pp-list grp-box">${items}</ul></details>`;
  }

  // 习惯（全部经历累积）、熟练度、最近一份「这个月的我」
  extras(g) {
    const habits = (g.habits || []).length
      ? `<p class="grp-foot">习惯：${g.habits.map((h) => `${esc(h.label)}（${h.n} 次，${h.weeks} 周）`).join('、')}</p>`
      : '<p class="grp-foot">习惯：还没有。同一件事做够 5 次、跨 3 周才算习惯。</p>';
    const skills = (g.skills || []).map((s) => `<span class="gr-skill" title="经验 ${s.xp}${s.next ? `，${s.next} 升下一级` : ''}">${esc(s.domain)} ${s.level} 级·${esc(s.name)}</span>`).join(' ');
    const r = (g.reports || [])[0];
    const report = r
      ? `<details class="pf-sec gr-report"><summary><b>这个月的我</b><span>${esc(r.month)} · 共 ${g.reports.length} 份</span></summary><ul class="pp-list grp-box">${r.lines.map((x) => `<li><p>${esc(x)}</p></li>`).join('')}</ul></details>`
      : '';
    return `${habits}<p class="grp-foot">熟练度：${skills}</p>${report}`;
  }

  render() {
    if (!this.el) return;
    const g = this.g;
    if (!g) {
      this.el.innerHTML = `<h4 class="grp-h">成长</h4><p class="hint">${esc(this.msg || '正在读…')}</p>`;
      return;
    }
    const rows = g.timeline.length ? g.timeline.map((e) => this.entry(e)).join('') : '<li class="hint">还没有成长记录。每周一会读一遍上一周的经历，经历太少的那周跳过。</li>';
    const r = g.rules || {};
    const canRun = g.enabled && g.due && g.week !== g.due;
    this.el.innerHTML = `<h4 class="grp-h">成长 <small>每周一反思上一周${g.week ? ` · 上次：${esc(g.week)}` : ''}</small></h4>
      <p class="grp-foot">每周最多改 ${r.max_changes ?? 3} 条，每条都要引用这一周真实发生的事；性格分每月最多变 ${r.core_monthly_steps ?? 1} 格，受你影响的改动不超过 ${Math.round((r.user_share ?? 0.3) * 100)}%，你锁住的项不动。另一位成员评审打分，低于 3 分的不改。</p>
      ${this.extras(g)}
      ${this.book(g)}
      <ul class="pp-list grp-box">${rows}</ul>
      <div class="grp-box grp-acts">${canRun ? `<button type="button" class="grp-btn" data-grun${this.busy ? ' disabled' : ''}>${this.busy ? '正在反思…' : `现在反思 ${esc(g.due)}`}</button>` : ''}<button type="button" class="grp-btn danger" id="gr-clear" data-gclear>清空成长记录</button></div>
      <p class="hint" role="status">${esc(this.msg)}</p>`;
  }

  async act(fn, done = '') {
    try {
      this.g = await fn();
      this.msg = done;
    } catch (e) {
      this.msg = e.message || '出错了';
    }
    this.render();
  }

  async onClick(e) {
    const t = e.target;
    const ud = t.closest('[data-gundo]');
    if (ud) return this.act(() => this.api.undo(this.member, ud.dataset.gundo), '撤回了');
    const rv = t.closest('[data-grevert]');
    if (rv) return this.act(() => this.api.revert(this.member, Number(rv.dataset.grevert)), '撤回了，档案存成了一个新版本');
    if (t.closest('[data-grun]')) {
      if (this.busy) return;
      this.busy = true;
      this.render();
      await this.act(() => this.api.run(this.member), '反思完了');
      this.busy = false;
      return this.render();
    }
    if (t.closest('[data-gclear]')) {
      if (this.now() > this.armed) {
        this.armed = this.now() + 5000;
        const b = this.el.querySelector('#gr-clear');
        if (b) b.textContent = '再点一次确认（档案本身不动）';
        return;
      }
      this.armed = 0;
      await this.api.clear(this.member);
      return this.load();
    }
  }
}
