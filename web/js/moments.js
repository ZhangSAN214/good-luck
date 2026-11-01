// 朋友圈：成员根据当天真实发生的事发动态，互相点赞、评论；你也可以发、赞、评。
// 只存在你电脑上；成员只在你发动态或评论后回应你，不会主动来找你。
// 发动态可带图片（最多 9 张）、视频、语音和表情；评论可用 Emoji 和表情包。
// 版式照真实朋友圈：顶部封面 + 头像，下面一列动态；赞和评论收在每条右下角的「··」里，看谁的动态从右上角的小入口选。

import { homeDay, tzOpt } from './hometz.js';
import { MediaComposer, bindMedia, cardHTML, mediaHTML, richText, soloSticker, stickerSVG, visibleText } from './chatmedia.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const when = (iso) => {
  const d = new Date(iso);
  const day = homeDay(d) === homeDay() ? '今天' : d.toLocaleDateString('zh-CN', tzOpt({ month: 'numeric', day: 'numeric' }));
  return `${day} ${d.toLocaleTimeString('zh-CN', tzOpt({ hour: '2-digit', minute: '2-digit', hour12: false }))}`;
};

export class Moments {
  constructor(dialog, { api, onSeen = null, avatar = null } = {}) {
    this.dlg = dialog;
    this.api = api; // {list(author, before), post(text), like(id), comment(id, text, replyTo), del(id), delComment(cid)}
    this.onSeen = onSeen;
    this.avatarOf = avatar; // (成员, 名字) → 头像 HTML；没有就用名字首字
    this.author = null;
    this.menu = null; // 展开了「赞 / 评论」的那条动态
    this.composing = false;
    this.picking = false;
    this.rows = [];
    this.members = [];
    this.more = false;
    this.replying = null; // {moment, comment, name}
    this.msg = '';
    this.timer = null;
    this.dlg.addEventListener('click', (e) => this.onClick(e));
    this.dlg.addEventListener('submit', (e) => this.onSubmit(e));
    this.dlg.addEventListener('close', () => clearTimeout(this.timer));
    bindMedia(this.dlg);
    this.buildNew();
  }

  /** 发动态的表单只建一次：重画列表时搬回原位，写到一半的字和已选的图片都还在。 */
  buildNew() {
    const f = document.createElement('form');
    f.id = 'mo-new';
    f.className = 'mo-new';
    f.innerHTML = `<textarea name="text" rows="3" maxlength="500" placeholder="这一刻的想法…（成员会来评论）" aria-label="发动态"></textarea>
      <div class="mo-newbar"><p class="hint">成员写的是公寓楼里当天真实发生的事，没事就不发；点赞不花钱，评论计入每天的生活花费上限。成员会先看一遍你发的图片、视频和语音再评论（感官额度用完时只知道你发了）。</p><button class="btn primary" type="submit">发表</button></div>
      <div class="mo-tools"></div><div class="mo-tray" hidden></div>`;
    this.newForm = f;
    this.newMedia = new MediaComposer({
      input: f.querySelector('textarea'),
      bar: f.querySelector('.mo-tools'),
      tray: f.querySelector('.mo-tray'),
      down: true,
      onChange: () => {
        const busy = this.newMedia.busy();
        const b = f.querySelector('[type="submit"]');
        b.disabled = busy;
        b.textContent = busy ? '上传中…' : '发表';
      },
      onError: (msg) => this.say(msg),
    });
  }

  say(msg) {
    this.msg = msg;
    const el = this.dlg.querySelector('.pp-msg');
    if (el) el.textContent = msg;
  }

  async open(author = null) {
    if (!this.dlg.open) this.dlg.showModal();
    this.author = author;
    this.msg = '';
    this.replying = null;
    this.menu = null;
    this.picking = false;
    await this.load();
  }

  close() {
    this.dlg.close();
  }

  async load(keep = false) {
    // 切换作者时，之前发出的请求可能后回来：只认最后一次，免得标题是新作者、列表还是旧的
    const seq = (this.seq = (this.seq || 0) + 1);
    try {
      const d = await this.api.list(this.author, null);
      if (seq !== this.seq) return;
      this.rows = d.moments || [];
      this.members = d.members || [];
      this.more = this.rows.length >= 20;
      if (!this.author) this.onSeen?.();
    } catch (e) {
      this.msg = e.message;
    }
    // 正在写字或挑表情时不重画（不然面板和写到一半的评论会消失）
    if (!keep || !this.dlg.querySelector('textarea:focus, input:focus, .mo-cf .cm-panel:not([hidden])')) this.render();
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

  /** 动态正文；只发了一个表情包时画成大表情。 */
  textHTML(m) {
    const shown = visibleText(m.text, m.meta);
    if (!shown) return '';
    const solo = !m.meta?.media?.length && soloSticker(shown);
    if (solo) return `<p class="mo-text mo-stk">${stickerSVG(solo.pack, solo.name, { big: true })}</p>`;
    return `<p class="mo-text">${richText(shown)}</p>`;
  }

  face(id, name, cls = 'mo-av') {
    const member = this.members.find((m) => m.id === id);
    const art = id !== 'user' && this.avatarOf ? this.avatarOf(member, name) : '';
    return art ? `<span class="${cls}">${art}</span>` : `<span class="${cls} ini">${esc((name || '?').slice(0, 1))}</span>`;
  }

  item(m) {
    const likes = m.likes.length ? `<p class="mo-likes"><span class="mo-heart" aria-hidden="true">♡</span> ${m.likes.map((l) => `<b>${esc(l.name)}</b>`).join('，')}</p>` : '';
    const comments = m.comments
      .map(
        (c) => `<li><b>${esc(c.name)}</b>${c.reply_name ? `回复<b>${esc(c.reply_name)}</b>` : ''}：${richText(c.text)}${c.offline ? ' <span class="gc-tag away">你不在时</span>' : ''}
          ${c.author !== 'user' ? `<button type="button" class="link" data-reply="${m.id}:${c.id}" data-name="${esc(c.name)}">回复</button>` : ''}<button type="button" class="link" data-delc="${c.id}">删除</button></li>`,
      )
      .join('');
    const reply = this.replying?.moment === m.id;
    const open = this.menu === m.id;
    return `<article class="mo" data-mid="${m.id}">
      ${this.face(m.author, m.name)}
      <div class="mo-body">
        <header><b class="mo-name">${esc(m.name)}</b></header>
        ${this.textHTML(m)}
        ${m.meta?.media?.length ? `<div class="mo-media">${mediaHTML(m.meta.media, { group: `mo${m.id}`, layout: 'moment', mine: m.author === 'user' })}</div>` : ''}
        ${cardHTML(m.meta?.card, { layout: 'moment' })}
        <div class="mo-foot"><time datetime="${esc(m.created_at)}">${esc(when(m.created_at))}</time>${m.offline ? '<span class="gc-tag away">你不在时</span>' : ''}<button type="button" class="link mo-del" data-del="${m.id}">删除</button>
          <span class="mo-menu"><span class="mo-act"${open ? '' : ' hidden'}><button type="button" data-like="${m.id}" aria-pressed="${m.liked}">♡ ${m.liked ? '取消' : '赞'}</button><button type="button" data-reply="${m.id}:">评论</button></span>
          <button type="button" class="mo-more" data-menu="${m.id}" aria-expanded="${open}" aria-label="赞或评论">··</button></span></div>
        ${likes || comments ? `<div class="mo-fb">${likes}${comments ? `<ul class="mo-comments">${comments}</ul>` : ''}</div>` : ''}
        ${reply ? `<form class="mo-cf" data-mid="${m.id}"><input name="text" maxlength="500" placeholder="${this.replying.comment ? `回复 ${esc(this.replying.name)}` : '评论'}" aria-label="评论" required><button class="btn primary" type="submit">发送</button><span class="mo-cf-tools"></span></form>` : ''}
      </div>
    </article>`;
  }

  render() {
    const who = this.author && this.author !== 'user' ? this.nameOf(this.author) : '我';
    const chips = [`<button type="button" class="pp-who${!this.author ? ' cur' : ''}" data-author="">全部</button>`]
      .concat(this.members.map((m) => `<button type="button" class="pp-who${this.author === m.id ? ' cur' : ''}" data-author="${esc(m.id)}">${esc(m.name)}</button>`))
      .concat([`<button type="button" class="pp-who${this.author === 'user' ? ' cur' : ''}" data-author="user">我</button>`])
      .join('');
    this.dlg.innerHTML = `
      <div class="vh mo-bar"><button type="button" class="mo-icon" data-close aria-label="关闭"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg></button>
        <b>朋友圈${this.author ? ` · ${esc(who)}` : ''}</b>
        <button type="button" class="mo-icon" data-pick aria-expanded="${this.picking}" aria-label="看谁的朋友圈"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.8-3.2 3-5 5.5-5s4.7 1.8 5.5 5"/><path d="M15.5 5.2a3 3 0 0 1 0 5.6M17.5 14.5c1.6.7 2.6 2.3 3 4.5"/></svg></button>
        <button type="button" class="mo-icon" data-compose aria-expanded="${this.composing}" aria-label="发动态"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h3l1.5-2h7L17 8h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg></button>
        <nav class="mo-pick" aria-label="看谁的朋友圈"${this.picking ? '' : ' hidden'}>${chips}</nav></div>
      <div class="vb mo-wrap">
        <div class="mo-cover"><span class="mo-me">${esc(who)}</span>${this.face(this.author || 'user', who, 'mo-av big')}</div>
        <div id="mo-new-slot"></div>
        <div class="mo-feed">${this.rows.length ? this.rows.map((m) => this.item(m)).join('') : '<p class="hint mo-empty">还没有动态。</p>'}</div>
        ${this.more ? '<button type="button" class="btn mo-older" data-more>更早的</button>' : ''}
        <p class="hint pp-msg" role="status">${esc(this.msg)}</p>
      </div>`;
    this.newForm.hidden = !this.composing;
    this.dlg.querySelector('#mo-new-slot')?.replaceWith(this.newForm);
    this.cfMedia?.destroy();
    this.cfMedia = null;
    const cf = this.dlg.querySelector('.mo-cf');
    if (cf) this.cfMedia = new MediaComposer({ input: cf.querySelector('input'), bar: cf.querySelector('.mo-cf-tools'), kinds: [], compact: true });
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
    if (t.closest('[data-pick]')) {
      this.picking = !this.picking;
      return this.render();
    }
    if (t.closest('[data-compose]')) {
      this.composing = !this.composing;
      this.render();
      if (this.composing) this.dlg.querySelector('#mo-new textarea')?.focus();
      return;
    }
    const menu = t.closest('[data-menu]');
    if (menu) {
      const id = Number(menu.dataset.menu);
      this.menu = this.menu === id ? null : id;
      return this.render();
    }
    if (this.menu !== null && !t.closest('.mo-act')) {
      this.menu = null;
      this.render();
    }
    const tab = t.closest('[data-author]');
    if (tab) return this.open(tab.dataset.author || null);
    const like = t.closest('[data-like]');
    if (like) {
      this.menu = null;
      return this.act(() => this.api.like(Number(like.dataset.like)));
    }
    const reply = t.closest('[data-reply]');
    if (reply) {
      const [mid, cid] = reply.dataset.reply.split(':');
      this.replying = { moment: Number(mid), comment: cid ? Number(cid) : null, name: reply.dataset.name || '' };
      this.menu = null;
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
    if (f.id === 'mo-new') {
      const media = this.newMedia.ready();
      if (!text && !media.length) return;
      if (this.newMedia.busy()) return this.say('附件还在上传，稍等一下');
      this.composing = false;
      let ok = false;
      await this.act(async () => {
        await this.api.post(text, media);
        ok = true;
      }, '发好了');
      if (ok) {
        f.querySelector('textarea').value = '';
        this.newMedia.clear();
      }
      return this.later();
    }
    if (!text) return;
    if (f.classList.contains('mo-cf')) {
      const r = this.replying;
      this.replying = null;
      await this.act(() => this.api.comment(Number(f.dataset.mid), text, r?.comment ?? null));
      return this.later();
    }
  }
}
