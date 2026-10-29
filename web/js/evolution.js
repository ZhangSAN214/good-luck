// 革新例会：成员读同一叠资料卡，走头脑风暴提建议、匿名互评、投票；你只管点「做 / 不做」。
// 开小版本还是大版本由后端按时间自动判断（每月第一场大版本，其余小版本），页面只显示结论和原因；
// 默认关闭（config/roundtable.yaml 的 evolution.enabled）；每场、每月都有花费上限。
// 版式照「成员设定」那套 iPhone 设置式分组卡片：最重要的（下一场、本场结论）在上，其余收进「更多」。

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
const KIND = { minor: '小版本', major: '大版本' };
const JOB = {
  running: '开发侧在做…',
  done: 'PR 开好了',
  failed: '开发侧没做成',
  timeout: '开发侧超时了',
  interrupted: '服务重启过，没做完',
};
const BACK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>';
const NEXT = '<svg class="ev-chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg>';

// 鼠标停在按钮上看到的一句话说明（页面上不再铺满说明文字）
const TIP = {
  meet: '马上开一场：成员读资料卡、提建议、匿名投票。要花一点钱，每场有上限。',
  scout: '让成员上网找新做法写成资料卡，通常几美分，算在本月上限里。',
  open: '看这场会的完整经过，只是看，不花钱。',
  export: '把结果存成文件放到你电脑的数据目录里，不花钱。',
  do: '同意做这条：记进待办，开发侧照着改，做好后出现「看 PR」。',
  skip: '不做这条：记下来，随时可以改主意。',
  merge: '把开发侧做好的改动并进程序，会再问你一次。',
  pr: '看开发侧改了哪些代码。',
  plugins: '让一位成员根据这场写一份插件清单，约几美分。',
  scene: '只看这个场景的插件。',
  theme: '点一个现成主题，或用自己的话写。',
};
const tip = (k) => ` title="${esc(TIP[k])}"`;
const usd = (n) => `$${Number(n || 0).toFixed(n >= 1 ? 2 : 4)}`;
const money = (n) => `$${Number(n || 0).toFixed(2)}`;

// 「怎么运作？」折叠里的四句话
const HOW = `
  <ol class="ev-how">
    <li>成员读几张「资料卡」（新做法简介），各自出主意，再匿名互相打分。</li>
    <li>开哪种会自动定：每月第一场是<b>大版本</b>（围绕一个主题，给 10 个选项），其余是<b>小版本</b>（不限主题，给 3 个）。</li>
    <li>你只管点「做」或「不做」。点了「做」，开发侧照着改，做好后你看过再点「合并」。</li>
    <li>开会和写清单要花一点钱，每场 ≤ $0.40、每月 ≤ $1.50，快超了就自动不开。</li>
  </ol>`;

// 开发侧的进度：PR 链接、「合并」（只有你点才合并）
function job(m, i, off) {
  const j = i.job;
  if (!j) return '';
  const pr = j.pr && /^https:\/\/github\.com\//.test(j.pr) ? `<a href="${esc(j.pr)}" target="_blank" rel="noopener"${tip('pr')}>看 PR</a>` : '';
  const merge =
    j.status === 'done' && j.pr && !j.merged
      ? `<button type="button" class="ev-pill" data-merge data-sid="${esc(m.session_id)}" data-idea="${esc(i.id)}"${tip('merge')}${off}>合并</button>`
      : '';
  const state = j.merged ? '已合并' : JOB[j.status] || j.status;
  return `<div class="ev-job"><small>${esc(state)}${j.error ? `（${esc(j.error)}）` : ''}</small>${pr}${merge}</div>`;
}

// 本场结论：得票前几名，每条右边「做 / 不做」；定过的显示结果，点另一个可以改
function ideas(m, busy) {
  if (!m.ideas || !m.ideas.length) {
    const why = m.status === 'completed' ? '这场没有得票的建议。' : `${STATUS[m.status] || m.status}，开完后结论会出现在这里。`;
    return `<p class="ev-empty">${esc(why)}</p>`;
  }
  const off = busy ? ' disabled' : '';
  const btn = (i, kind, label) =>
    `<button type="button" class="ev-pill${i.decision === kind ? ' on' : ''}${kind === 'do' ? ' do' : ''}" data-decide="${kind}" data-sid="${esc(m.session_id)}" data-idea="${esc(i.id)}" aria-pressed="${i.decision === kind}"${tip(kind)}${off}>${label}</button>`;
  return `<ol class="ev-ideas">${m.ideas
    .map(
      (i, n) => `<li class="ev-idea${i.decision ? ` is-${i.decision}` : ''}">
        <span class="ev-no">${n + 1}</span>
        <div class="ev-body"><p class="ev-text" data-clamp>${esc(i.text)}</p>
          <small class="ev-meta">${i.score} 分 · ${i.votes} 票${i.decision === 'do' ? ' · 要做' : i.decision === 'skip' ? ' · 不做' : ''}</small>${job(m, i, off)}</div>
        <div class="ev-acts">${btn(i, 'do', '做')}${btn(i, 'skip', '不做')}</div></li>`,
    )
    .join('')}</ol>`;
}

// 插件清单：收在「更多」里；每个插件一张小卡，可以按场景筛
const FIELDS = [
  ['what', '做什么'],
  ['use', '有什么用'],
  ['pros', '优点'],
  ['cons', '缺点'],
  ['why', '为什么做'],
  ['source', '参考'],
];
function plugins(m, cfg, scene, busy) {
  if (!m || m.status !== 'completed' || !cfg.enabled) return '';
  const off = busy ? ' disabled' : '';
  const p = m.plugins;
  if (!p || !p.items || !p.items.length) {
    const label = p && p.error ? '插件清单没写成，再写一次' : '写插件清单';
    return `<button type="button" class="grp-btn ev-row" data-plugins="${esc(m.session_id)}"${tip('plugins')}${off}><span>${label}</span><small>约 ${usd(cfg.estimate_usd)}</small>${NEXT}</button>`;
  }
  const chips = [['', `全部 ${p.items.length}`], ...Object.entries(p.scenes || {}).map(([s, n]) => [s, `${s} ${n}`])]
    .map(
      ([s, label]) =>
        `<button type="button" class="ev-chip${s === (scene || '') ? ' on' : ''}" data-scene="${esc(s)}" data-sid="${esc(m.session_id)}"${tip('scene')}>${esc(label)}</button>`,
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
  return `<details class="ev-fold ev-plugins"${scene !== undefined ? ' open' : ''}><summary class="ev-row"><span>插件清单</span><small>${p.items.length} 个</small>${NEXT}</summary>
    <div class="ev-fold-in"><div class="ev-chips">${chips}</div><ol class="ev-plist">${items}</ol>
    <button type="button" class="ev-link" data-plugins="${esc(m.session_id)}"${tip('plugins')}${off}>不满意，重写一份（约 ${usd(cfg.estimate_usd)}）</button></div></details>`;
}

export class Evolution {
  constructor(dialog, { api, openSession = null } = {}) {
    this.dlg = dialog;
    this.api = api; // {get(), meet(kind, theme), exportList(sid), scout(), plugins(sid), decide(sid, idea, decision), merge(sid, idea), setTheme(theme)}
    this.openSession = openSession;
    this.data = null;
    this.msg = '';
    this.busy = false;
    this.scene = {}; // 会话 id → 插件清单正在看的场景
    this.override = null; // 你手动改的这次开哪种（null = 按自动判断）
    this.editTheme = false;
    this.theme = '';
    this.dlg.addEventListener('click', (e) => this.onClick(e));
    this.dlg.addEventListener('input', (e) => {
      if (e.target.matches('[data-theme-input]')) this.theme = e.target.value; // 不重画，免得打字时丢焦点
    });
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

  // 下一场：自动判断的结论 + 原因；大版本显示主题；一个主按钮
  hero(d) {
    const sch = d.schedule || {};
    const nxt = d.next || { kind: 'minor', reason: '', top: 3 };
    const kinds = d.kinds || {};
    const kind = this.override || nxt.kind;
    const top = kinds[kind] ? kinds[kind].top : nxt.top;
    const manual = this.override && this.override !== nxt.kind;
    const other = kind === 'major' ? 'minor' : 'major';
    const theme = kind === 'major' ? (nxt.kind === 'major' ? nxt.theme : nxt.major_theme) : null;
    const themeSrc = d.next_theme ? '你定的' : '自动轮换';
    const spent = Number(d.month_spent_usd || 0);
    const cap = Number(d.monthly_usd || 1.5);
    const pct = Math.min(100, Math.round((spent / cap) * 100));
    const cards = d.cards || [];
    const canMeet = d.enabled && cards.length && !this.busy;
    const why = !d.enabled ? '' : !cards.length ? '还没有资料卡，先让成员找一点资料' : '';
    const themeRow =
      kind === 'major'
        ? `<div class="ev-theme"><span>主题</span><b>${theme ? `「${esc(theme)}」` : '还没定'}</b><small>${theme ? themeSrc : ''}</small>
            <button type="button" class="ev-link" data-edit-theme>${this.editTheme ? '收起' : '改主题'}</button></div>${this.editTheme ? this.themeEditor(d) : ''}`
        : '';
    if (!d.enabled) {
      return `<section class="ev-hero">
        <p class="ev-eyebrow">革新例会还没开启</p>
        <p class="ev-reason">在 config/roundtable.local.yaml 里写 evolution: {enabled: true}，再重启圆桌（也可以让开发侧帮你开）。</p>
        <div class="ev-go"><button type="button" class="btn primary" data-meet${tip('meet')} disabled>现在开一场</button></div></section>`;
    }
    return `<section class="ev-hero ev-${kind}">
      <p class="ev-eyebrow">下一场${d.auto ? ` · 每周${DAYS[(sch.weekday || 1) - 1]} ${sch.hour ?? 9}:00 自动开` : ''}</p>
      <h2 class="ev-kind">${KIND[kind]}<small>${top} 个选项</small></h2>
      <p class="ev-reason">${manual ? `这次手动改成了${KIND[kind]}` : `自动判断：${esc(nxt.reason)}`}</p>
      ${themeRow}
      <div class="ev-go">
        <button type="button" class="btn primary" data-meet${tip('meet')}${canMeet ? '' : ' disabled'}>现在开一场</button>
        <button type="button" class="ev-link" data-override="${manual ? '' : other}">${manual ? '恢复自动判断' : `改成${KIND[other]}`}</button>
      </div>
      ${why ? `<p class="ev-why">${why}</p>` : ''}
      <div class="ev-budget" title="本月革新例会已花 / 每月上限；每场预计超过 ${money(d.meeting_usd)} 就不开">
        <span>本月 ${money(spent)} / ${money(cap)}</span><i style="--p:${pct}%"></i><span>资料卡 ${cards.length} 张</span>
      </div>
    </section>`;
  }

  themeEditor(d) {
    const cur = this.theme || d.next_theme || '';
    const on = cur || (d.next && d.next.major_theme) || '';
    return `<div class="ev-theme-edit">
      <div class="ev-chips">${(d.themes || []).map((t) => `<button type="button" class="ev-chip${t === on ? ' on' : ''}" data-theme="${esc(t)}"${tip('theme')}>${esc(t)}</button>`).join('')}</div>
      <input type="text" data-theme-input maxlength="${d.theme_chars || 200}" placeholder="或者用自己的话写，比如：让群聊更自然" value="${esc(cur)}">
      <div class="ev-go"><button type="button" class="btn" data-save-theme>用这个主题</button><button type="button" class="ev-link" data-auto-theme>交给自动轮换</button></div>
    </div>`;
  }

  render() {
    const d = this.data || { cards: [], meetings: [], notes: [] };
    const meetings = d.meetings || [];
    const [latest, ...older] = meetings;
    const off = this.busy ? ' disabled' : '';
    const scout = d.scout || {};
    const canScout = d.enabled && scout.enabled && scout.can_search && !this.busy;
    const result = latest
      ? `<section class="grp ev-result">
          <h4 class="grp-h">本场结论 <small>${esc(latest.week)} · ${esc(latest.kind_label || KIND[latest.kind] || '小版本')}${latest.theme ? `「${esc(latest.theme)}」` : ''} · ${esc(STATUS[latest.status] || latest.status)} · ${usd(latest.cost_usd)}</small></h4>
          <div class="grp-box">${ideas(latest, this.busy)}</div>
        </section>`
      : `<section class="grp ev-result"><h4 class="grp-h">本场结论</h4><div class="grp-box"><p class="ev-empty">还没开过。开完的第一场会出现在这里。</p></div></section>`;
    const history = older.length
      ? `<details class="ev-fold"><summary class="ev-row"><span>以前的例会</span><small>${older.length} 场</small>${NEXT}</summary>
          <ul class="ev-history">${older
            .map(
              (m) => `<li><span class="ev-week">${esc(m.week)}</span><span class="ev-tag">${esc(m.kind_label || KIND[m.kind] || '')}</span>${m.theme ? `<span>「${esc(m.theme)}」</span>` : ''}<small>${esc(STATUS[m.status] || m.status)} · ${usd(m.cost_usd)}</small><button type="button" class="ev-link" data-open="${esc(m.session_id)}"${tip('open')}>看过程</button></li>`,
            )
            .join('')}</ul></details>`
      : '';
    const more = `<section class="grp ev-more">
        <h4 class="grp-h">更多</h4>
        <div class="grp-box grp-acts">
          ${latest ? plugins(latest, d.plugins || {}, this.scene[latest.session_id], this.busy) : ''}
          ${latest ? `<button type="button" class="grp-btn ev-row" data-open="${esc(latest.session_id)}"${tip('open')}><span>看完整过程</span><small>不花钱</small>${NEXT}</button>` : ''}
          ${
            latest && latest.adopted
              ? `<div class="grp-row ev-row"><span>采纳单</span><small>已存 ${esc(latest.adopted)}</small></div>`
              : latest && latest.status === 'completed'
                ? `<button type="button" class="grp-btn ev-row" data-export="${esc(latest.session_id)}"${tip('export')}${off}><span>存采纳单到电脑</span><small>不花钱</small>${NEXT}</button>`
                : ''
          }
          <button type="button" class="grp-btn ev-row" data-scout${tip('scout')}${canScout ? '' : ' disabled'}><span>让成员找资料</span><small>${scout.can_search === false ? '没有联网服务' : '约几美分'}</small>${NEXT}</button>
          ${history}
        </div>
      </section>`;
    const notes = (d.notes || []).length ? `<p class="grp-foot">${(d.notes || []).map(esc).join('；')}</p>` : '';
    this.dlg.innerHTML = `
      <div class="vh sheet-bar"><button type="button" class="sheet-icon" data-close aria-label="关闭">${BACK}</button><b>革新例会</b><span></span></div>
      <div class="vb ev">
        <div class="ev-lede"><p>成员每周开会出主意、匿名投票，你只管点「做」或「不做」。</p><details class="ev-help"><summary>怎么运作？</summary>${HOW}</details></div>
        ${this.hero(d)}
        ${notes}
        ${result}
        ${more}
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
    const clamp = t.closest('[data-clamp]');
    if (clamp) return clamp.classList.toggle('open');
    if (t.closest('[data-scout]')) {
      return this.act(this.api.scout, (r) =>
        r.written.length
          ? `写好 ${r.written.length} 张资料卡，花费 ${usd(r.cost_usd)}`
          : `这次没有写出资料卡（${r.rejected.length} 个网页没通过），花费 ${usd(r.cost_usd)}`,
      );
    }
    const ov = t.closest('[data-override]');
    if (ov) {
      this.override = ov.dataset.override || null;
      return this.render();
    }
    if (t.closest('[data-edit-theme]')) {
      this.editTheme = !this.editTheme;
      this.theme = '';
      return this.render();
    }
    const th = t.closest('[data-theme]');
    if (th) {
      this.theme = th.dataset.theme;
      return this.render();
    }
    if (t.closest('[data-save-theme]') || t.closest('[data-auto-theme]')) {
      const theme = t.closest('[data-auto-theme]') ? '' : this.theme.trim() || (this.data && this.data.next_theme) || '';
      this.editTheme = false;
      this.theme = '';
      return this.act(
        () => this.api.setTheme(theme),
        (r) => (theme ? `下次大版本的主题定为「${theme}」` : `主题交给自动轮换${r.major_theme ? `，下次是「${r.major_theme}」` : ''}`),
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
            ? '记下了：这条不做'
            : r.dev_started
              ? '记下了：要做，开发侧已经开始，做完这里会出现「看 PR」和「合并」'
              : '记下了：要做，开发侧会照着实现、开 PR',
      );
    }
    const mg = t.closest('[data-merge]');
    if (mg) {
      const { sid, idea } = mg.dataset;
      if (!window.confirm(`确定合并 ${idea} 的 PR 吗？合并后会进入开发分支。`)) return;
      return this.act(() => this.api.merge(sid, idea), (r) => `已合并：${r.pr}`);
    }
    if (t.closest('[data-meet]')) {
      const kind = this.override;
      return this.act(
        () => this.api.meet(kind, null),
        (r) => {
          this.override = null;
          return `${KIND[r.kind] || '例会'}${r.theme ? `「${r.theme}」` : ''}开始了：预计 ${usd(r.estimate_usd)}，开完后结论会出现在这里`;
        },
      );
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
