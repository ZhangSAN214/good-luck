// 关系（成员系统 P4）：和你到了哪一步（新邻居 → 熟悉 → 老朋友）、和每位同事有多熟、TA 对同事的看法。
// 全由程序按真实的共同经历累积（你说话 TA 接了、生活里碰到一起、私下聊天、一起干活），不调模型、不花钱。
// 只影响生活里谁和谁更常凑一起聊；工作派活不看关系。
// 另有 TA 对你的见解：每周按 TA 自己的性格读「关于你」和最近的聊天，在上周的看法上接着想（可以和别人不同、可以不同意你）；
// 你可以在一句看法下随手回一句，没有打分按钮。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const pct = (x) => `${Math.round((x || 0) * 100)}%`;
const day = (s) => (s ? String(s).slice(0, 10) : '');
// 「2026-W43」→「10月19日那周」（ISO 周的周一）
const week = (w) => {
  const m = /^(\d{4})-W(\d{2})$/.exec(String(w || ''));
  if (!m) return esc(w);
  const jan4 = new Date(Date.UTC(Number(m[1]), 0, 4));
  const mon = new Date(jan4.getTime() + ((Number(m[2]) - 1) * 7 - ((jan4.getUTCDay() + 6) % 7)) * 86400000);
  return `${mon.getUTCMonth() + 1}月${mon.getUTCDate()}日那周`;
};

export class RelationPanel {
  constructor({ api, now = () => Date.now() } = {}) {
    this.api = api; // {get(id), clear(id), think(id), dropView(id, index), replyView(id, index, text)}
    this.now = now;
    this.member = null;
    this.r = null;
    this.msg = '';
    this.armed = 0;
    this.busy = false;
    this.el = null;
  }

  async mount(el, member) {
    this.el = el;
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      this.onClick(e);
    });
    el.addEventListener('submit', (e) => this.onSubmit(e));
    if (member !== this.member) {
      this.member = member;
      this.r = null;
      this.msg = '';
    }
    this.render();
    await this.load();
  }

  async load() {
    if (!this.member) return;
    try {
      this.r = await this.api.get(this.member);
    } catch (e) {
      this.msg = e.message;
    }
    this.render();
  }

  // 离下一阶段还差什么
  next(u) {
    const n = u.next;
    if (!n) return '已经是最后一个阶段了。';
    const need = [];
    if (u.days < n.days) need.push(`再聊 ${n.days - u.days} 天`);
    if (u.n < n.exchanges) need.push(`再来回 ${n.exchanges - u.n} 次`);
    if (n.weeks) need.push(`认识满 ${n.weeks} 周`);
    return `到「${esc(n.stage)}」：${need.length ? need.join('、') : '快了'}。`;
  }

  // 一句看法：怎么来的、以前怎么想、你回过什么
  view(v) {
    const tag = v.change && v.change !== '保留' ? `<span>${esc(v.change === '新的' ? '新想到' : v.change)}</span>` : '';
    const why = [
      ...(v.quotes || []).map((t) => `你说过「${esc(t)}」`),
      ...(v.facts_text || []).map((t) => `「${esc(t)}」`),
    ];
    const was = (v.history || []).length
      ? `<details class="rl-was"><summary>以前怎么想（${v.history.length}）</summary><ol>${v.history
          .map((h) => `<li>${week(h.week)}：${esc(h.text)}${h.reply ? `<br><small>你回：「${esc(h.reply)}」</small>` : ''}</li>`)
          .join('')}</ol></details>`
      : '';
    const reply = v.reply?.text
      ? `<p class="rl-reply"><small>你回：「${esc(v.reply.text)}」</small> <button type="button" class="link" data-runreply="${v.index}">撤回</button></p>`
      : `<form class="rl-reply" data-rreply="${v.index}"><input name="t" maxlength="80" placeholder="想说点什么就回一句（TA 自己体会你的意思）" aria-label="回一句"><button type="submit" class="link">回</button></form>`;
    return `<li class="${v.live ? '' : 'rl-gone'}"><div class="pp-mh"><b>${esc(v.text)}</b>${tag}${v.agree ? '' : '<span>不太同意你</span>'}${v.live ? '' : '<span>依据被删了，聊天不带</span>'}<button type="button" class="link" data-rdrop="${v.index}">删掉</button></div>${v.was ? `<p><small>${/^以前/.test(v.was) ? '' : '以前以为：'}${esc(v.was)}</small></p>` : ''}${why.length ? `<p><small>${v.since ? `从${week(v.since)}起这么想 · ` : ''}因为${why.join('、')}</small></p>` : ''}${was}${reply}</li>`;
  }

  // TA 对你的见解（每周想一次，在上周的看法上接着想）
  views(r) {
    const rows = (r.views || []).map((v) => this.view(v)).join('');
    const log = (r.views_log || [])[0];
    const empty = `<li class="hint">${esc(log?.note || '还没想过。每周一 TA 会按自己的性格，回想「关于你」和最近和你的聊天，写几句「我觉得」。')}</li>`;
    const changes = (log?.changes || []).length
      ? `<p class="grp-foot">${week(log.week)}：${log.changes.map(esc).join('；')}</p>`
      : '';
    const can = r.insight_enabled && r.insight_due && r.views_week !== r.insight_due;
    const depth = r.depth ? `现在是「${esc(r.user?.stage || '')}」，最多 ${r.depth.views} 句；越熟想得越多越深。` : '';
    return `<h4 class="grp-h">TA 对你的看法 <small>${r.views_week ? `上次想的是${week(r.views_week)}` : '每周一想一次'}</small></h4>
      <p class="grp-foot">只是 TA 自己从相处里慢慢形成的理解，8 位可以想得不一样，也可以不同意你。每周在上周的基础上接着想：保留、加深、改观或放下。聊天时 TA 可能顺口说出来，你怎么接、在这里回什么，TA 都会自己琢磨。${depth}</p>
      ${changes}
      <ul class="pp-list grp-box">${rows || empty}</ul>
      ${can ? `<div class="grp-box grp-acts"><button type="button" class="grp-btn" data-rthink${this.busy ? ' disabled' : ''}>${this.busy ? '正在想…' : `现在想一想（${week(r.insight_due)}）`}</button></div>` : ''}`;
  }

  render() {
    if (!this.el) return;
    const r = this.r;
    if (!r) {
      this.el.innerHTML = `<h4 class="grp-h">关系</h4><p class="hint">${esc(this.msg || '正在读…')}</p>`;
      return;
    }
    const u = r.user;
    const you = u.n
      ? `<p><b class="rl-stage">${esc(u.stage)}</b> · 认识第 ${u.known} 天 · 一起聊过 ${u.days} 天、${u.n} 次来回 · 亲近度 ${pct(u.closeness)}${u.quiet ? ' · 有阵子没聊了' : ''}</p>
         <p class="grp-foot">${this.next(u)}阶段只升不降；亲近度不来往会慢慢变淡。${(u.history || []).length ? `经历：${u.history.map((h) => `${day(h.at)} 成了「${esc(h.stage)}」`).join('；')}` : ''}</p>`
      : '<p class="hint">还是新邻居：你和 TA 还没说过话（你说话、TA 接了才算一来一回；工作群不算）。</p>';
    const peers = r.peers.length
      ? r.peers
          .map(
            (p) => `<li><div class="pp-mh"><b>${esc(p.name)}</b><span>亲近度 ${pct(p.closeness)}</span></div><p>${esc(p.view)}</p></li>`,
          )
          .join('')
      : '<li class="hint">还没和同事来往过。生活里碰到一起、私下聊天、群里搭话、一起干活都会记下来。</li>';
    this.el.innerHTML = `<h4 class="grp-h">关系 <small>程序按一起经历的事算，不花钱</small></h4>
      <div class="grp-box rl-you">${you}</div>
      <p class="grp-foot">和同事：越熟的越常凑一起聊（生人也会碰到）。工作派活不看关系。</p>
      <ul class="pp-list grp-box">${peers}</ul>
      ${this.views(r)}
      <div class="grp-box grp-acts"><button type="button" class="grp-btn danger" id="rl-clear" data-rclear>清空 TA 的关系</button></div>
      <p class="hint" role="status">${esc(this.msg)}</p>`;
  }

  async act(fn, done = '') {
    try {
      this.r = await fn();
      this.msg = done;
    } catch (err) {
      this.msg = err.message || '出错了';
    }
    this.render();
  }

  async onSubmit(e) {
    const form = e.target.closest('[data-rreply]');
    if (!form) return;
    e.preventDefault();
    const text = form.querySelector('input').value.trim();
    if (!text) return;
    await this.act(() => this.api.replyView(this.member, Number(form.dataset.rreply), text), '回好了，TA 下次想的时候会看到');
  }

  async onClick(e) {
    const un = e.target.closest('[data-runreply]');
    if (un) return this.act(() => this.api.replyView(this.member, Number(un.dataset.runreply), ''), '撤回了');
    const drop = e.target.closest('[data-rdrop]');
    if (drop) return this.act(() => this.api.dropView(this.member, Number(drop.dataset.rdrop)), '删掉了');
    if (e.target.closest('[data-rthink]')) {
      if (this.busy) return;
      this.busy = true;
      this.render();
      await this.act(() => this.api.think(this.member), '想完了');
      this.busy = false;
      return this.render();
    }
    if (!e.target.closest('[data-rclear]')) return;
    if (this.now() > this.armed) {
      this.armed = this.now() + 5000;
      const b = this.el.querySelector('#rl-clear');
      if (b) b.textContent = '再点一次确认（从头再按经历累积）';
      return;
    }
    this.armed = 0;
    try {
      await this.api.clear(this.member);
      this.msg = '清空了';
    } catch (err) {
      this.msg = err.message || '出错了';
    }
    await this.load();
  }
}
