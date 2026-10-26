// 革新例会：成员读同一叠资料卡（开发侧整理，成员看不到原文），走头脑风暴提建议、匿名互评、投票，导出采纳单。
// 默认关闭（config/roundtable.yaml 的 evolution.enabled）；每场、每月都有花费上限。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const STATUS = {
  created: '准备中',
  running: '开会中',
  completed: '开完了',
  awaiting_confirmation: '等你确认',
  paused: '暂停了',
  stopped: '停止了',
  failed: '失败了',
};
const DAYS = '一二三四五六日';
const usd = (n) => `$${Number(n || 0).toFixed(n >= 1 ? 2 : 4)}`;

export class Evolution {
  constructor(dialog, { api, openSession = null } = {}) {
    this.dlg = dialog;
    this.api = api; // {get(), meet(), exportList(sid)}
    this.openSession = openSession;
    this.data = null;
    this.msg = '';
    this.busy = false;
    this.dlg.addEventListener('click', (e) => this.onClick(e));
  }

  async open() {
    if (!this.dlg.open) this.dlg.showModal();
    this.msg = '';
    await this.load();
  }

  close() {
    this.dlg.close();
  }

  async load() {
    try {
      this.data = await this.api.get();
    } catch (e) {
      this.msg = e.message;
    }
    this.render();
  }

  render() {
    const d = this.data || { cards: [], meetings: [], notes: [] };
    const sch = d.schedule || {};
    const state = d.enabled
      ? `已开启 · ${esc(d.plan_label)}全员上桌 · ${d.anonymous ? '匿名' : '不匿名'}${d.auto ? ` · 每周${DAYS[(sch.weekday || 1) - 1]} ${sch.hour ?? 9} 点之后自动开（服务开着时）` : ''}`
      : '没有开启：在 config/roundtable.yaml 里把 evolution.enabled 改成 true 后重启服务';
    const cards = d.cards.length
      ? d.cards.map((c, i) => `<li><b>${i + 1}. ${esc(c.title)}</b><small>${esc(c.file)} · ${c.chars} 字${c.truncated ? ' · 超长已截断' : ''}</small></li>`).join('')
      : '<li class="hint">收件箱里还没有资料卡（.md / .txt）。</li>';
    const notes = (d.notes || []).map((n) => `<li class="hint">${esc(n)}</li>`).join('');
    const meetings = d.meetings.length
      ? d.meetings
          .map(
            (m) => `<li><span class="ev-week">${esc(m.week)}</span><span>${esc(STATUS[m.status] || m.status)} · ${usd(m.cost_usd)}${m.auto ? ' · 自动' : ''}</span>
              <span class="ev-act"><button type="button" class="link" data-open="${esc(m.session_id)}">看过程</button>${
                m.adopted
                  ? `<small>采纳单 ${esc(m.adopted)}</small>`
                  : m.status === 'completed'
                    ? `<button type="button" class="link" data-export="${esc(m.session_id)}">导出采纳单</button>`
                    : ''
              }</span></li>`,
          )
          .join('')
      : '<li class="hint">还没开过。</li>';
    this.dlg.innerHTML = `
      <div class="vh"><b>革新例会</b><button type="button" class="btn" data-close>关闭</button></div>
      <div class="vb ev">
        <p class="hint">成员一起读本周的资料卡，各自提建议、碰撞、匿名投票（不能投自己），得票前几名写成采纳单。采纳单只是建议：开发侧从中挑一条不碰规则的去实现、开 PR，合不合并由你决定。</p>
        <p class="hint"><b>${state}</b></p>
        <p class="hint">本月已花 ${usd(d.month_spent_usd)} / 上限 ${usd(d.monthly_usd)} · 每场预计超过 ${usd(d.meeting_usd)} 就不开</p>
        <h3>资料卡</h3>
        <p class="hint ev-path">${d.inbox ? `放在 ${esc(d.inbox)}` : '没有数据目录'}</p>
        <ul class="ev-list">${cards}${notes}</ul>
        <h3>最近的例会</h3>
        <ul class="ev-list">${meetings}</ul>
        <div class="pp-row"><button type="button" class="btn primary" data-meet${d.enabled && d.cards.length && !this.busy ? '' : ' disabled'}>现在开一场</button></div>
        <p class="hint pp-msg" role="status">${esc(this.msg)}</p>
      </div>`;
  }

  async act(fn, done) {
    this.busy = true;
    this.render();
    try {
      const r = await fn();
      this.msg = done(r);
    } catch (e) {
      this.msg = e.message || '出错了';
    }
    this.busy = false;
    await this.load();
  }

  async onClick(e) {
    const t = e.target;
    if (t.closest('[data-close]')) return this.close();
    if (t.closest('[data-meet]')) {
      return this.act(this.api.meet, (r) => `开始了：预计 ${usd(r.estimate_usd)}，开完后在这里导出采纳单`);
    }
    const ex = t.closest('[data-export]');
    if (ex) return this.act(() => this.api.exportList(ex.dataset.export), (r) => `已保存到 ${r.path}`);
    const op = t.closest('[data-open]');
    if (op && this.openSession) {
      this.close();
      await this.openSession(op.dataset.open);
    }
  }
}
