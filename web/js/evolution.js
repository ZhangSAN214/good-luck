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

// 给新手看的大白话：每个按钮做什么、点了会怎样、花不花钱（鼠标停在按钮上也能看到）
const TIP = {
  scout: '让成员自己上网找几篇新做法，整理成「资料卡」放进收件箱。要花一点钱（通常几美分），算在本月上限里。',
  meet: '马上开一场例会：成员读资料卡、提建议、互相打分投票。要花钱（每场有上限，超了就不开），开完自动写插件清单。',
  open: '看这场例会的完整过程：谁提了什么、怎么投的票。只是看，不花钱。',
  export: '把这场的结果存成一份文件（采纳单），放在你电脑的数据目录里。不花钱。',
  do: '你拍板「要做这条」：记进待办；开了自动实现的话，开发侧会动手改代码，做完在这里出现「看 PR」和「合并」。不花圆桌的钱。',
  skip: '你拍板「不做」：记下来，以后不再提醒。不花钱，随时可以改主意。',
  merge: '把开发侧做好的改动正式并进程序。会再问你一次，确认后才合并。不花钱。',
  pr: '在网页上看开发侧改了哪些代码。只是看。',
  plugins: '让一位成员根据这场的资料和建议，写一份至少 20 个插件的清单给你看。要花一点钱（约几美分）。',
  rewrite: '不满意这份清单，就让成员重新写一份（旧的会被替换）。要花一点钱（约几美分）。',
  scene: '只看用在这个场景里的插件；点「全部」看回所有。不花钱。',
  minor: '小版本：主题不限，成员自由发挥，最后给你 3 个选项。每周自动开的就是这种。',
  major: '大版本：你先定一个主题，成员都围绕它想，最后给你 10 个选项。适合每月一次的大改。',
  theme: '点一下现成的主题，或者在框里用自己的话写（比如「让成员聊天更自然」）。',
};
const tip = (k) => ` title="${esc(TIP[k])}"`;

// 页面顶部的「新手说明」：不用术语讲清楚整个流程
const GUIDE = `
  <p><b>这是什么？</b>革新例会就是让圆桌里的 AI 成员每周开一次「怎么把圆桌变得更好」的会。它们只出主意、投票，不会自己乱改程序；改不改，最后都由你说了算。</p>
  <ol>
    <li><b>准备资料</b>：先要有几张「资料卡」（一页纸的新做法介绍）。可以点「找资料」让成员自己上网找，也可以由开发侧放进来。</li>
    <li><b>选会的种类</b>：<b>小版本</b>不限主题、让成员随意发挥，最后给你 3 个选项，每周自动开的就是它；<b>大版本</b>要你先定一个主题（比如「群聊更自然」），成员都围绕它想，最后给你 10 个选项，适合每月一次的大改。</li>
    <li><b>开会</b>：点「现在开一场」，或者到了每周固定时间自动开（小版本）。成员读同样的资料卡，各自提建议。</li>
    <li><b>投票</b>：成员匿名互相打分（不能投自己），按得分排出选项：小版本 3 个，大版本 10 个。</li>
    <li><b>插件清单</b>：开完会，一位成员把这场的想法整理成至少 20 个「插件」，每个都写清楚做什么、有什么用、优缺点、为什么做、用在哪个场景（办公室、公寓、群聊、朋友圈……）。只是给你看的参考。</li>
    <li><b>你拍板</b>：觉得哪条好就点「做这条」，不想要就点「不做」。</li>
    <li><b>做出来、再合并</b>：开发侧照着做，做好后出现「看 PR」（PR 就是一份「改了哪些代码」的申请单）。你看过满意了点「合并」，改动才会真正进入程序。不点就什么都不会变。</li>
  </ol>
  <p><b>花钱吗？</b>找资料、开会、写插件清单要花一点钱（调用 AI 的费用），每场和每月都有上限，快超了就自动不开。看过程、导出、做这条 / 不做、合并都不花圆桌的钱。</p>
  <p><b>小提示</b>：鼠标停在任何按钮上，都会显示它是做什么的。</p>`;

// 开发侧的进度：PR 链接、「合并」按钮（只有你点才合并）
function job(m, i, off) {
  const j = i.job;
  if (!j) return '';
  const pr = j.pr && /^https:\/\/github\.com\//.test(j.pr) ? `<a href="${esc(j.pr)}" target="_blank" rel="noopener"${tip('pr')}>看 PR</a>` : '';
  const merge =
    j.status === 'done' && j.pr && !j.merged
      ? `<button type="button" class="link" data-merge data-sid="${esc(m.session_id)}" data-idea="${esc(i.id)}"${tip('merge')}${off}>合并</button>`
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
        `<button type="button" class="link" data-decide="${kind}" data-sid="${esc(m.session_id)}" data-idea="${esc(i.id)}"${tip(kind)}${off}>${label}</button>`;
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
    `<button type="button" class="link" data-plugins="${esc(m.session_id)}"${tip(label === '重写' ? 'rewrite' : 'plugins')}${off}>${label}</button>`;
  if (!p || !p.items || !p.items.length) {
    const err = p && p.error ? `<small class="hint">上次没写成：${esc(p.error)}</small>` : '';
    return `<div class="ev-plugins"><span class="hint">还没有插件清单（每场至少 ${cfg.count} 个，预计约 ${usd(cfg.estimate_usd)}）</span>${write(p && p.error ? '再写一次' : '写插件清单')}${err}</div>`;
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
  return `<details class="ev-plugins" open><summary>插件清单 · ${p.items.length} 个 · 写清单花 ${usd(p.cost_usd)}${p.file ? ` · 存为 插件清单/${esc(p.file)}` : ''}</summary>
    <p class="hint ev-tip">每张卡片是一个插件：做什么、有什么用、优缺点、为什么做。点上面的场景按钮只看那个场景（不花钱）；「重写」会让成员重新写一份（约花几美分）。</p>
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
    this.kind = 'minor'; // 下一场开哪种：minor 小版本 / major 大版本
    this.theme = ''; // 大版本的主题
    this.dlg.addEventListener('click', (e) => this.onClick(e));
    this.dlg.addEventListener('change', (e) => {
      if (e.target.name === 'evkind') {
        this.kind = e.target.value;
        this.render();
      }
    });
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

  render() {
    const d = this.data || { cards: [], meetings: [], notes: [] };
    const sch = d.schedule || {};
    const state = d.enabled
      ? `已开启 · ${esc(d.plan_label)}全员上桌（用便宜的那一档，所有成员都参加）· ${d.anonymous ? '匿名（成员只用代号，看不出是谁）' : '不匿名'}${d.auto ? ` · 每周${DAYS[(sch.weekday || 1) - 1]} ${sch.hour ?? 9} 点之后自动开（要圆桌开着）` : ''}`
      : '还没有开启：在 config/roundtable.yaml 里把 evolution.enabled 改成 true，再重启圆桌（或让开发侧帮你开）';
    const cards = d.cards.length
      ? d.cards.map((c, i) => `<li><b>${i + 1}. ${esc(c.title)}</b><small>${esc(c.file)} · ${c.chars} 字${c.truncated ? ' · 超长已截断' : ''}</small></li>`).join('')
      : '<li class="hint">收件箱里还没有资料卡（.md / .txt）。</li>';
    const notes = (d.notes || []).map((n) => `<li class="hint">${esc(n)}</li>`).join('');
    const meetings = d.meetings.length
      ? d.meetings
          .map(
            (m) => `<li><span class="ev-week">${esc(m.week)}</span><span class="ev-tag">${esc(m.kind_label || '小版本')}</span>${m.theme ? `<b>「${esc(m.theme)}」</b>` : ''}<span>${esc(STATUS[m.status] || m.status)} · ${usd(m.cost_usd)}${m.auto ? ' · 自动' : ''}</span>
              <span class="ev-act"><button type="button" class="link" data-open="${esc(m.session_id)}"${tip('open')}>看过程</button>${
                m.adopted
                  ? `<small>采纳单 ${esc(m.adopted)}</small>`
                  : m.status === 'completed'
                    ? `<button type="button" class="link" data-export="${esc(m.session_id)}"${tip('export')}>导出采纳单</button>`
                    : ''
              }</span>${ideas(m, this.busy)}${plugins(m, d.plugins || {}, this.scene[m.session_id], this.busy)}</li>`,
          )
          .join('')
      : '<li class="hint">还没开过。</li>';
    const scout = d.scout || {};
    const kinds = d.kinds || {};
    const scoutHint = !scout.enabled
      ? '自己找资料没有开启（evolution.scout.enabled）。'
      : scout.can_search
        ? `只搜 ${esc((scout.domains || []).join('、'))}；一位成员写卡、另一位复核，通过的才放进收件箱。`
        : '没有能用的联网搜索 / 读网页服务，暂时找不了资料。';
    this.dlg.innerHTML = `
      <div class="vh"><b>革新例会</b><button type="button" class="btn" data-close>关闭</button></div>
      <div class="vb ev">
        <details class="ev-guide"${d.meetings.length ? '' : ' open'}><summary>新手说明：这一页是干什么的？（点开 / 收起）</summary>${GUIDE}</details>
        <p class="hint">成员一起读本周的资料卡，各自提建议、匿名投票（不能投自己），得票前几名写成采纳单。采纳单只是建议：做不做、合不合并都由你决定。</p>
        <p class="hint"><b>${state}</b></p>
        <p class="hint">本月已花 ${usd(d.month_spent_usd)} / 上限 ${usd(d.monthly_usd)} · 每场预计超过 ${usd(d.meeting_usd)} 就不开（花费 = 调用 AI 的钱，到上限会自动停）</p>
        <h3>资料卡</h3>
        <p class="hint ev-tip">资料卡就是一页纸的「新做法介绍」，成员开会时读它。开过会的卡会自动收起来，下周不会重复读。</p>
        <p class="hint ev-path">${d.inbox ? `放在 ${esc(d.inbox)}` : '没有数据目录'}</p>
        <ul class="ev-list">${cards}${notes}</ul>
        <p class="hint">${scoutHint}</p>
        <h3>最近的例会</h3>
        <p class="hint">每场按得票列出选项（小版本 ${kinds.minor ? kinds.minor.top : 3} 个，大版本 ${kinds.major ? kinds.major.top : 10} 个），后面的按钮是这样用的：</p>
        <ul class="ev-keys">
          <li><b>看过程</b>：看这场会的完整经过，只是看，不花钱。</li>
          <li><b>导出采纳单</b>：把结果存成文件放到你电脑上，不花钱。</li>
          <li><b>做这条</b>：你同意做这条建议，开发侧会照着改，做好后出现「看 PR」。</li>
          <li><b>不做</b>：记下你不想要这条，随时可以改主意。</li>
          <li><b>看 PR / 合并</b>：先看改了什么，满意再点「合并」（会再确认一次），不点就不会改动程序。</li>
          <li><b>插件清单</b>：开完会自动写的至少 20 个插件，给你参考，可以按场景筛选；没有的话点「写插件清单」（约花几美分）。</li>
        </ul>
        <ul class="ev-list">${meetings}</ul>
        <h3>下一场开哪种？</h3>
        <div class="ev-kind">
          <label${tip('minor')}><input type="radio" name="evkind" value="minor"${this.kind === 'minor' ? ' checked' : ''}> <b>小版本</b></label>
          <small class="hint">主题不限，成员随意发挥，最后给你 ${kinds.minor ? kinds.minor.top : 3} 个选项。每周自动开的就是这种。</small>
          <label${tip('major')}><input type="radio" name="evkind" value="major"${this.kind === 'major' ? ' checked' : ''}> <b>大版本</b></label>
          <small class="hint">你先定一个主题，成员都围绕它想，最后给你 ${kinds.major ? kinds.major.top : 10} 个选项。适合每月一次的大改。</small>
        </div>
        ${
          this.kind === 'major'
            ? `<div class="ev-theme">
          <p class="hint ev-tip">大版本的主题：点一个现成的，或者在框里用自己的话写（最多 ${d.theme_chars || 200} 字）。</p>
          <div class="ev-chips">${(d.themes || []).map((t) => `<button type="button" class="ev-chip${t === this.theme ? ' on' : ''}" data-theme="${esc(t)}"${tip('theme')}>${esc(t)}</button>`).join('')}</div>
          <input type="text" class="ev-theme-input" data-theme-input maxlength="${d.theme_chars || 200}" placeholder="例如：让成员在群聊里说话更自然" value="${esc(this.theme)}">
        </div>`
            : ''
        }
        <p class="hint ev-tip">「找资料」：让成员上网找新做法写成资料卡（约花几美分）。「现在开一场」：立刻开会，要先有资料卡（每场有花费上限）。按钮是灰的说明现在不能点：还没开启、没有资料卡，或者正在忙。</p>
        <div class="pp-row">
          <button type="button" class="btn" data-scout${tip('scout')}${d.enabled && scout.enabled && scout.can_search && !this.busy ? '' : ' disabled'}>找资料</button>
          <button type="button" class="btn primary" data-meet${tip('meet')}${d.enabled && d.cards.length && !this.busy ? '' : ' disabled'}>现在开一场</button>
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
    const th = t.closest('[data-theme]');
    if (th) {
      this.theme = th.dataset.theme;
      return this.render();
    }
    if (t.closest('[data-meet]')) {
      const theme = this.theme.trim();
      if (this.kind === 'major' && !theme) {
        this.msg = '大版本要先选或填一个主题';
        return this.render();
      }
      const label = this.kind === 'major' ? `大版本「${theme}」` : '小版本';
      return this.act(
        () => this.api.meet(this.kind, this.kind === 'major' ? theme : null),
        (r) => `${label}开始了：预计 ${usd(r.estimate_usd)}，开完后在这里看结果和插件清单`,
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
