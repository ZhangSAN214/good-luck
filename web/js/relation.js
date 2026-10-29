// 关系（成员系统 P4）：和你到了哪一步（新邻居 → 熟悉 → 老朋友）、和每位同事有多熟、TA 对同事的看法。
// 全由程序按真实的共同经历累积（你说话 TA 接了、生活里碰到一起、私下聊天、一起干活），不调模型、不花钱。
// 只影响生活里谁和谁更常凑一起聊；工作派活不看关系。
// 另有 TA 对你的见解：每周按 TA 自己的性格读一遍「关于你」画像，写几句「我觉得」（可以和别人不同、可以不同意你）。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const pct = (x) => `${Math.round((x || 0) * 100)}%`;
const day = (s) => (s ? String(s).slice(0, 10) : '');

export class RelationPanel {
  constructor({ api, now = () => Date.now() } = {}) {
    this.api = api; // {get(id), clear(id), think(id), dropView(id, index)}
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

  // TA 对你的见解（每周想一次）
  views(r) {
    const rows = (r.views || [])
      .map(
        (v) => `<li class="${v.live ? '' : 'rl-gone'}"><div class="pp-mh"><b>${esc(v.text)}</b>${v.agree ? '' : '<span>不太同意你</span>'}${v.live ? '' : '<span>依据被删了，聊天不带</span>'}<button type="button" class="link" data-rdrop="${v.index}">删掉</button></div>${(v.facts_text || []).length ? `<p><small>从这几条来：${v.facts_text.map((t) => `「${esc(t)}」`).join('')}</small></p>` : ''}</li>`,
      )
      .join('');
    const log = (r.views_log || [])[0];
    const empty = `<li class="hint">${esc(log?.note || '还没想过。每周一会按 TA 自己的性格读一遍「关于你」，写几句「我觉得」。')}</li>`;
    const can = r.insight_enabled && r.insight_due && r.views_week !== r.insight_due;
    return `<h4 class="grp-h">TA 对你的看法 <small>${r.views_week ? `上次：${esc(r.views_week)}` : '每周一想一次'}</small></h4>
      <p class="grp-foot">只是 TA 自己的理解，8 位可以想得不一样，也可以不同意你；聊天时 TA 会说「我觉得」。每句都要从「关于你」里的条目来，那几条被你删了就不再带。</p>
      <ul class="pp-list grp-box">${rows || empty}</ul>
      ${can ? `<div class="grp-box grp-acts"><button type="button" class="grp-btn" data-rthink${this.busy ? ' disabled' : ''}>${this.busy ? '正在想…' : `现在想一想 ${esc(r.insight_due)}`}</button></div>` : ''}`;
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

  async onClick(e) {
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
