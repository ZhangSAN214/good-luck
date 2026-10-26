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
const DECIDED = { do: '要做', skip: '不做' };
const JOB = {
  running: '开发侧在做…',
  done: 'PR 开好了',
  failed: '开发侧没做成',
  timeout: '开发侧超时了',
  interrupted: '服务重启过，没做完',
};

// 开发侧的进度：PR 链接、「合并」按钮（只有你点才合并）
function job(m, i, off) {
  const j = i.job;
  if (!j) return '';
  const pr = j.pr && /^https:\/\/github\.com\//.test(j.pr) ? `<a href="${esc(j.pr)}" target="_blank" rel="noopener">看 PR</a>` : '';
  const merge =
    j.status === 'done' && j.pr && !j.merged
      ? `<button type="button" class="link" data-merge data-sid="${esc(m.session_id)}" data-idea="${esc(i.id)}"${off}>合并</button>`
      : '';
  const state = j.merged ? '已合并' : JOB[j.status] || j.status;
  return `<small class="ev-job">${esc(state)}${j.error ? `（${esc(j.error)}）` : ''}</small>${pr}${merge}`;
}

// 开完的例会：得票前几名，每条可以点「做这条 / 不做」（已定的显示结果，还能改）
function ideas(m, busy) {
  if (!m.ideas || !m.ideas.length) return '';
  const off = busy ? ' disabled' : '';
  const rows = m.ideas
    .map((i, n) => {
      const btn = (kind, label) =>
        `<button type="button" class="link" data-decide="${kind}" data-sid="${esc(m.session_id)}" data-idea="${esc(i.id)}"${off}>${label}</button>`;
      const state = i.decision ? `<b class="ev-decided">${DECIDED[i.decision]}</b>` : '';
      return `<li><span>${n + 1}. ${esc(i.text)}</span><small>${i.score} 分 · ${i.votes} 票</small>
        <span class="ev-act">${state}${i.decision === 'do' ? '' : btn('do', '做这条')}${i.decision === 'skip' ? '' : btn('skip', '不做')}${job(m, i, off)}</span></li>`;
    })
    .join('');
  return `<ol class="ev-ideas">${rows}</ol>`;
}
const usd = (n) => `$${Number(n || 0).toFixed(n >= 1 ? 2 : 4)}`;

// 插件清单：每个插件写做什么、有什么用、优缺点、为什么做、参考；可以按场景筛
const FIELDS = [
  ['what', '做什么'],
  ['use', '有什么用'],
  ['pros', '优点'],
  ['cons', '缺点'],
  ['why', '为什么做'],
  ['source', '参考'],
];
function plugins(m, cfg, scene, busy) {
  if (m.status !== 'completed' || !cfg.enabled) return '';
  const off = busy ? ' disabled' : '';
  const p = m.plugins;
  const write = (label) =>
    `<button type="button" class="link" data-plugins="${esc(m.session_id)}"${off}>${label}</button>`;
  if (!p || !p.items || !p.items.length) {
    const err = p && p.error ? `<small class="hint">上次没写成：${esc(p.error)}</small>` : '';
    return `<div class="ev-plugins"><span class="hint">还没有插件清单（每场至少 ${cfg.count} 个，预计约 ${usd(cfg.estimate_usd)}）</span>${write(p && p.error ? '再写一次' : '写插件清单')}${err}</div>`;
  }
  const chips = [['', `全部 ${p.items.length}`], ...Object.entries(p.scenes || {}).map(([s, n]) => [s, `${s} ${n}`])]
    .map(
      ([s, label]) =>
        `<button type="button" class="ev-chip${s === (scene || '') ? ' on' : ''}" data-scene="${esc(s)}" data-sid="${esc(m.session_id)}">${esc(label)}</button>`,
    )
    .join('');
  const items = p.items
    .map((it, n) => [it, n])
    .filter(([it]) => !scene || it.scene === scene)
    .map(
      ([it, n]) => `<li class="ev-plugin"><div class="ev-ph"><b>${n + 1}. ${esc(it.name)}</b><span class="ev-tag">${esc(it.scene)}</span></div>
        <dl>${FIELDS.map(([k, label]) => `<dt>${label}</dt><dd>${esc(it[k])}</dd>`).join('')}</dl></li>`,
    )
    .join('');
  return `<details class="ev-plugins" open><summary>插件清单 · ${p.items.length} 个 · 写清单花 ${usd(p.cost_usd)}${p.file ? ` · 存为 插件清单/${esc(p.file)}` : ''}</summary>
    <div class="ev-chips">${chips}<span class="ev-act">${write('重写')}</span></div>
    <ol class="ev-plist">${items}</ol></details>`;
}

export class Evolution {
  constructor(dialog, { api, openSession = null } = {}) {
    this.dlg = dialog;
    this.api = api; // {get(), meet(), exportList(sid), scout(), plugins(sid), decide(sid, idea, decision), merge(sid, idea)}
    this.openSession = openSession;
    this.data = null;
    this.msg = '';
    this.busy = false;
    this.scene = {}; // 会话 id → 插件清单正在看的场景
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
              }</span>${ideas(m, this.busy)}${plugins(m, d.plugins || {}, this.scene[m.session_id], this.busy)}</li>`,
          )
          .join('')
      : '<li class="hint">还没开过。</li>';
    const scout = d.scout || {};
    const scoutHint = !scout.enabled
      ? '自己找资料没有开启（evolution.scout.enabled）。'
      : scout.can_search
        ? `只搜 ${esc((scout.domains || []).join('、'))}；一位成员写卡、另一位复核，通过的才放进收件箱。`
        : '没有能用的联网搜索 / 读网页服务，暂时找不了资料。';
    this.dlg.innerHTML = `
      <div class="vh"><b>革新例会</b><button type="button" class="btn" data-close>关闭</button></div>
      <div class="vb ev">
        <p class="hint">成员一起读本周的资料卡，各自提建议、碰撞、匿名投票（不能投自己），得票前几名写成采纳单。采纳单只是建议：开发侧从中挑一条不碰规则的去实现、开 PR，合不合并由你决定。</p>
        <p class="hint"><b>${state}</b></p>
        <p class="hint">本月已花 ${usd(d.month_spent_usd)} / 上限 ${usd(d.monthly_usd)} · 每场预计超过 ${usd(d.meeting_usd)} 就不开</p>
        <h3>资料卡</h3>
        <p class="hint ev-path">${d.inbox ? `放在 ${esc(d.inbox)}` : '没有数据目录'}</p>
        <ul class="ev-list">${cards}${notes}</ul>
        <p class="hint">${scoutHint}</p>
        <h3>最近的例会</h3>
        <p class="hint">每场列得票前 ${d.adopt_top || 10} 名，可以点「做这条」：开发侧照着实现、开 PR，合并仍由你点；「不做」就记下不做。下面的插件清单是一位成员根据本场资料卡和点子整理给你看的（做什么、有什么用、优缺点、为什么做、用在哪个场景），开完会自动写，花费算进例会上限。</p>
        <ul class="ev-list">${meetings}</ul>
        <div class="pp-row">
          <button type="button" class="btn" data-scout${d.enabled && scout.enabled && scout.can_search && !this.busy ? '' : ' disabled'}>找资料</button>
          <button type="button" class="btn primary" data-meet${d.enabled && d.cards.length && !this.busy ? '' : ' disabled'}>现在开一场</button>
        </div>
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
    if (t.closest('[data-scout]')) {
      return this.act(this.api.scout, (r) =>
        r.written.length
          ? `写好 ${r.written.length} 张资料卡，花费 ${usd(r.cost_usd)}`
          : `这次没有写出资料卡（${r.rejected.length} 个网页没通过），花费 ${usd(r.cost_usd)}`,
      );
    }
    const sc = t.closest('[data-scene]');
    if (sc) {
      this.scene[sc.dataset.sid] = sc.dataset.scene;
      return this.render();
    }
    const pl = t.closest('[data-plugins]');
    if (pl) {
      return this.act(
        () => this.api.plugins(pl.dataset.plugins),
        (r) => `插件清单写好了：${r.plugins.length} 个，花费 ${usd(r.cost_usd)}`,
      );
    }
    const dec = t.closest('[data-decide]');
    if (dec) {
      const { sid, idea, decide } = dec.dataset;
      return this.act(
        () => this.api.decide(sid, idea, decide),
        (r) =>
          decide !== 'do'
            ? `记下了：${idea} 不做`
            : r.dev_started
              ? `记下了：${idea} 要做，开发侧已经开始实现，做完会在这里出现 PR 链接和「合并」`
              : `记下了：${idea} 要做，开发侧会照着实现、开 PR`,
      );
    }
    const mg = t.closest('[data-merge]');
    if (mg) {
      const { sid, idea } = mg.dataset;
      if (!window.confirm(`确定合并 ${idea} 的 PR 吗？合并后会进入开发分支。`)) return;
      return this.act(() => this.api.merge(sid, idea), (r) => `已合并：${r.pr}`);
    }
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
