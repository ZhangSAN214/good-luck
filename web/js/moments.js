// 朋友圈：成员根据当天真实发生的事发动态，互相点赞、评论；你也可以发、赞、评。
// 只存在你电脑上；成员只在你发动态或评论后回应你，不会主动来找你。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const when = (iso) => {
  const d = new Date(iso);
  const day = d.toDateString() === new Date().toDateString() ? '今天' : d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
  return `${day} ${d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })}`;
};

export class Moments {
  constructor(dialog, { api, onSeen = null } = {}) {
    this.dlg = dialog;
    this.api = api; // {list(author, before), post(text), like(id), comment(id, text, replyTo), del(id), delComment(cid)}
    this.onSeen = onSeen;
    this.author = null;
    this.rows = [];
    this.members = [];
    this.more = false;
    this.replying = null; // {moment, comment, name}
    this.msg = '';
    this.timer = null;
    this.dlg.addEventListener('click', (e) => this.onClick(e));
    this.dlg.addEventListener('submit', (e) => this.onSubmit(e));
    this.dlg.addEventListener('close', () => clearTimeout(this.timer));
  }

  async open(author = null) {
    if (!this.dlg.open) this.dlg.showModal();
    this.author = author;
    this.msg = '';
    this.replying = null;
    await this.load();
  }

  close() {
    this.dlg.close();
  }

  async load(keep = false) {
    try {
      const d = await this.api.list(this.author, null);
      this.rows = d.moments || [];
      this.members = d.members || [];
      this.more = this.rows.length >= 20;
      if (!this.author) this.onSeen?.();
    } catch (e) {
      this.msg = e.message;
    }
    if (!keep || !this.dlg.querySelector('textarea:focus, input:focus')) this.render();
  }

  /** 发完动态 / 评论后，成员在后台回应：过几秒再拉一次。 */
  later() {
    clearTimeout(this.timer);
    let n = 0;
    const tick = async () => {
      if (!this.dlg.open || n++ > 5) return;
      await this.load(true);
      this.timer = setTimeout(tick, 2500);
    };
    this.timer = setTimeout(tick, 1500);
  }

  nameOf(id) {
    return this.members.find((m) => m.id === id)?.name || id;
  }

  item(m) {
    const likes = m.likes.length ? `<p class="mo-likes">♥ ${m.likes.map((l) => esc(l.name)).join('、')}</p>` : '';
    const comments = m.comments
      .map(
        (c) => `<li><b>${esc(c.name)}</b>${c.reply_name ? ` 回复 <b>${esc(c.reply_name)}</b>` : ''}：${esc(c.text)}${c.offline ? ' <span class="gc-tag away">你不在时</span>' : ''}
          ${c.author !== 'user' ? `<button type="button" class="link" data-reply="${m.id}:${c.id}" data-name="${esc(c.name)}">回复</button>` : ''}<button type="button" class="link" data-delc="${c.id}">删</button></li>`,
      )
      .join('');
    const reply = this.replying?.moment === m.id;
    return `<article class="mo" data-mid="${m.id}">
      <header><b>${esc(m.name)}</b><time datetime="${esc(m.created_at)}">${esc(when(m.created_at))}</time>${m.offline ? '<span class="gc-tag away">你不在时</span>' : ''}</header>
      <p class="mo-text">${esc(m.text).replace(/\n/g, '<br>')}</p>
      <div class="mo-act"><button type="button" class="link" data-like="${m.id}" aria-pressed="${m.liked}">${m.liked ? '取消赞' : '赞'}</button>
        <button type="button" class="link" data-reply="${m.id}:">评论</button><button type="button" class="link" data-del="${m.id}">删</button></div>
      ${likes}${comments ? `<ul class="mo-comments">${comments}</ul>` : ''}
      ${reply ? `<form class="mo-cf" data-mid="${m.id}"><input name="text" maxlength="500" placeholder="${this.replying.comment ? `回复 ${esc(this.replying.name)}` : '写评论'}" aria-label="评论" required><button class="btn primary" type="submit">发送</button></form>` : ''}
    </article>`;
  }

  render() {
    const tabs = [`<button type="button" class="pp-who${!this.author ? ' cur' : ''}" data-author="">全部</button>`]
      .concat(this.members.map((m) => `<button type="button" class="pp-who${this.author === m.id ? ' cur' : ''}" data-author="${esc(m.id)}">${esc(m.name)}</button>`))
      .concat([`<button type="button" class="pp-who${this.author === 'user' ? ' cur' : ''}" data-author="user">我</button>`])
      .join('');
    this.dlg.innerHTML = `
      <div class="vh"><b>朋友圈${this.author ? ` · ${esc(this.author === 'user' ? '我' : this.nameOf(this.author))}` : ''}</b><button type="button" class="btn" data-close>关闭</button></div>
      <div class="vb mo-wrap">
        <nav class="pp-members" aria-label="看谁的朋友圈">${tabs}</nav>
        <form id="mo-new" class="pp-row"><textarea name="text" rows="2" maxlength="500" placeholder="发一条动态（成员会来评论）" aria-label="发动态"></textarea><button class="btn primary" type="submit">发表</button></form>
        <p class="hint">成员每天最多发一条，写的是公寓楼里当天真实发生的事；点赞不花钱，评论计入每天的生活花费上限。</p>
        ${this.rows.length ? this.rows.map((m) => this.item(m)).join('') : '<p class="hint">还没有动态。</p>'}
        ${this.more ? '<button type="button" class="btn" data-more>更早的</button>' : ''}
        <p class="hint pp-msg" role="status">${esc(this.msg)}</p>
      </div>`;
  }

  async act(fn, done = '') {
    try {
      await fn();
      this.msg = done;
    } catch (e) {
      this.msg = e.message || '出错了';
    }
    await this.load();
  }

  async onClick(e) {
    const t = e.target;
    if (t.closest('[data-close]')) return this.close();
    const tab = t.closest('[data-author]');
    if (tab) return this.open(tab.dataset.author || null);
    const like = t.closest('[data-like]');
    if (like) return this.act(() => this.api.like(Number(like.dataset.like)));
    const reply = t.closest('[data-reply]');
    if (reply) {
      const [mid, cid] = reply.dataset.reply.split(':');
      this.replying = { moment: Number(mid), comment: cid ? Number(cid) : null, name: reply.dataset.name || '' };
      this.render();
      this.dlg.querySelector(`.mo-cf[data-mid="${mid}"] input`)?.focus();
      return;
    }
    const del = t.closest('[data-del]');
    if (del) return this.act(() => this.api.del(Number(del.dataset.del)), '删掉了');
    const delc = t.closest('[data-delc]');
    if (delc) return this.act(() => this.api.delComment(Number(delc.dataset.delc)), '删掉了');
    if (t.closest('[data-more]')) {
      try {
        const d = await this.api.list(this.author, this.rows[this.rows.length - 1].id);
        this.rows = this.rows.concat(d.moments || []);
        this.more = (d.moments || []).length >= 20;
      } catch (err) {
        this.msg = err.message;
      }
      this.render();
    }
  }

  async onSubmit(e) {
    e.preventDefault();
    const f = e.target;
    const text = String(new FormData(f).get('text') || '').trim();
    if (!text) return;
    if (f.id === 'mo-new') {
      await this.act(() => this.api.post(text), '发好了');
      return this.later();
    }
    if (f.classList.contains('mo-cf')) {
      const r = this.replying;
      this.replying = null;
      await this.act(() => this.api.comment(Number(f.dataset.mid), text, r?.comment ?? null));
      return this.later();
    }
  }
}
