// 成员设定与记忆：聊天里每位成员的自我档案（人物卡，profile.js）、简单人设（性格、说话风格、口头禅、兴趣）和 TA 的记忆。
// 人设和记忆只用于私聊、群聊、朋友圈，工作模式与办公室不读取；记忆只存在你电脑上，可以改、删、清空。

import { ProfilePanel } from './profile.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const LAYER = { note: '记下的', day: '日记', week: '一周', month: '一个月', profile: '长期档案' };
const SOURCE = { local: '你改过的设定', config: '配置文件里的设定', empty: '没有设定（用模型本身的风格）' };
const BACK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>';
const list = (s) =>
  String(s || '')
    .split(/[,，、\n]/)
    .map((x) => x.trim())
    .filter(Boolean);

export class People {
  constructor(dialog, { api, now = () => Date.now(), avatar = null } = {}) {
    this.dlg = dialog;
    this.api = api; // {personas(), setPersona(id, p), memories(id), addMemory(id, text), editMemory(mid, text), delMemory(mid), clearMemories(id?), tidy(), profile…（见 profile.js）}
    this.now = now;
    this.avatarOf = avatar; // (成员 id, 名字) → 头像 HTML；没有就用名字首字
    this.members = [];
    this.member = null;
    this.memories = [];
    this.mind = null; // 记忆件、挂着的事、习惯（P2）
    this.itemQuery = '';
    this.diary = null; // 翻日记的结果
    this.armed = {}; // 两步确认：按钮 id → 截止时间
    this.msg = '';
    this.profile = api?.profile
      ? new ProfilePanel({
          now,
          api: {
            profile: api.profile,
            change: api.change,
            lock: api.lock,
            revert: api.revert,
            clear: api.clearProfile,
            importCards: api.importCards,
          },
        })
      : null;
    this.dlg.addEventListener('click', (e) => this.onClick(e));
    this.dlg.addEventListener('submit', (e) => this.onSubmit(e));
  }

  async open(member = null) {
    if (!this.dlg.open) this.dlg.showModal();
    this.msg = '';
    await this.loadMembers();
    this.member = member || this.member || this.members[0]?.id || null;
    await this.loadMemories();
    this.render();
  }

  close() {
    this.dlg.close();
  }

  async loadMembers() {
    try {
      this.members = (await this.api.personas()).members || [];
    } catch (e) {
      this.msg = e.message;
    }
  }

  async loadMemories() {
    if (!this.member) return;
    try {
      this.memories = (await this.api.memories(this.member)).memories || [];
      this.mind = this.api.items ? await this.api.items(this.member, this.itemQuery) : null;
    } catch (e) {
      this.msg = e.message;
    }
  }

  mindHtml() {
    const d = this.mind;
    if (!d) return '';
    const hang = d.hanging.length
      ? d.hanging.map((h) => `<li data-hid="${h.id}"><div class="pp-mh"><b>挂着</b><span>${esc(h.since)}</span><button type="button" class="link" data-drop="${h.id}">放下</button></div><p>${esc(h.text)}</p></li>`).join('')
      : '<li class="hint">现在没有挂心上的事。</li>';
    const items = d.items.length
      ? d.items
          .map(
            (r) => `<li data-iid="${r.id}"><div class="pp-mh"><b>${esc(r.domain)}</b><span>${esc(r.day)}</span>${r.kind === 'fact' ? '<span>长期</span>' : ''}${r.hits > 1 ? `<span>${r.hits} 次</span>` : ''}${'★'.repeat(Math.max(0, r.importance - 1))}${r.superseded_by ? '<span>已更新</span>' : ''}
              <button type="button" class="link" data-iedit="${r.id}">改</button><button type="button" class="link" data-idel="${r.id}">删除</button></div>
              <p>${esc(r.text)}${r.tone ? `<br><small>现在想起来：${esc(r.tone)}</small>` : ''}</p></li>`,
          )
          .join('')
      : '<li class="hint">还没有记忆件。每晚整理时会从当天的事里挑几件记下。</li>';
    const found = this.diary
      ? `<ul class="pp-list grp-box">${this.diary.length ? this.diary.map((t) => `<li><p>${esc(t)}</p></li>`).join('') : '<li class="hint">没翻到。</li>'}</ul>`
      : '';
    return `<h4 class="grp-h">记忆件与联想 <small>共 ${d.count} 件${d.links != null ? ` · 联想 ${d.links} 条` : ''} · TA 今天还能翻 ${d.diary_reads_left} 次日记</small></h4>
          ${d.habits ? `<p class="grp-foot">${esc(d.habits)}</p>` : ''}
          <ul class="pp-list grp-box">${hang}</ul>
          <form id="pp-isearch" class="grp-box"><div class="grp-row"><input name="q" value="${esc(this.itemQuery)}" placeholder="找记忆件（关键词）" aria-label="找记忆件"><button class="btn" type="submit">找</button></div></form>
          <ul class="pp-list grp-box">${items}</ul>
          <form id="pp-diary" class="grp-box"><div class="grp-row"><input name="q" placeholder="翻日记：日期（10-21）或关键词" aria-label="翻日记"><button class="btn" type="submit">翻</button></div></form>
          ${found}`;
  }

  current() {
    return this.members.find((m) => m.id === this.member) || null;
  }

  face(m) {
    const art = this.avatarOf ? this.avatarOf(m.id, m.name) : '';
    return `<span class="pp-face${art ? '' : ' ini'}">${art || esc((m.name || '?').slice(0, 1))}</span>`;
  }

  render() {
    const m = this.current();
    const p = m?.persona || {};
    const tabs = this.members
      .map((x) => `<button type="button" class="pp-who${x.id === this.member ? ' cur' : ''}" data-member="${esc(x.id)}"${x.id === this.member ? ' aria-current="true"' : ''}>${this.face(x)}<span class="pp-nm">${esc(x.name)}</span></button>`)
      .join('');
    const mems = this.memories.length
      ? this.memories
          .map(
            (r) => `<li data-mid="${r.id}"><div class="pp-mh"><b>${esc(LAYER[r.layer] || r.layer)}</b>${r.period ? `<span>${esc(r.period)}</span>` : ''}${r.source === 'user' ? '<span>你写的</span>' : ''}${r.offline ? '<span>你不在时</span>' : ''}
              <button type="button" class="link" data-edit="${r.id}">改</button><button type="button" class="link" data-del="${r.id}">删除</button></div>
              <p>${esc(r.text).replace(/\n/g, '<br>')}</p></li>`,
          )
          .join('')
      : '<li class="hint">还没有记忆。每天聊过天之后会自动整理一段；你也可以替 TA 记一条。</li>';
    const row = (name, label, value, ph = '') =>
      `<label class="grp-row"><span>${label}</span><input name="${name}" value="${esc(value)}"${ph ? ` placeholder="${esc(ph)}"` : ''}${name === 'personality' || name === 'speaking_style' ? ' maxlength="300"' : ''}></label>`;
    this.dlg.innerHTML = `
      <div class="vh sheet-bar"><button type="button" class="sheet-icon" data-close aria-label="关闭">${BACK}</button><b>成员设定与记忆</b><span></span></div>
      <div class="vb pp">
        <nav class="pp-members pp-strip" aria-label="成员">${tabs}</nav>
        ${
          m
            ? `${this.profile ? '<section class="pp-prof grp" id="pp-prof" aria-label="自我档案"></section>' : ''}
        <form id="pp-persona" class="pp-form grp">
          <h4 class="grp-h">${esc(m.name)} 的人设 <small>${esc(SOURCE[m.source] || '')}</small></h4>
          <div class="grp-box">
            ${row('personality', '性格', p.personality, '比如：安静、爱较真')}
            ${row('speaking_style', '说话风格', p.speaking_style, '比如：句子短，爱用比喻')}
            ${row('catchphrases', '口头禅', (p.catchphrases || []).join('，'), '逗号分隔，最多 5 个')}
            ${row('interests', '兴趣', (p.interests || []).join('，'), '逗号分隔，最多 8 个；上网时按兴趣看新闻')}
          </div>
          <p class="grp-foot">只用于私聊、群聊和朋友圈；工作和办公室不受影响。空着就用模型本身的说话风格。</p>
          <div class="pp-row grp-end"><button type="button" class="btn" data-reset>恢复默认</button><button class="btn primary" type="submit">保存人设</button></div>
        </form>
        <section class="pp-mem grp" aria-label="记忆">
          <h4 class="grp-h">${esc(m.name)} 的记忆 <small>只存在你电脑上</small></h4>
          <form id="pp-add" class="grp-box"><div class="grp-row"><input name="text" maxlength="300" placeholder="替 TA 记一条，比如：我下周三考试" aria-label="记一条"><button class="btn" type="submit">记下</button></div></form>
          <ul class="pp-list grp-box">${mems}</ul>
          ${this.mindHtml()}
          <div class="grp-box grp-acts"><button type="button" class="grp-btn" data-tidy>现在整理</button><button type="button" class="grp-btn danger" id="pp-clear-one" data-clear="one">清空 TA 的记忆</button><button type="button" class="grp-btn danger" id="pp-clear-all" data-clear="all">清空全部成员的记忆</button></div>
        </section>`
            : '<p class="hint">没有成员。</p>'
        }
        <p class="hint pp-msg" role="status">${esc(this.msg)}</p>
      </div>`;
    const prof = this.dlg.querySelector('#pp-prof');
    if (prof && m) this.profile.mount(prof, m.id);
  }

  armedOk(id, label) {
    const b = this.dlg.querySelector(`#${id}`);
    if (this.now() > (this.armed[id] || 0)) {
      this.armed[id] = this.now() + 5000;
      if (b) {
        b.textContent = '再点一次确认';
        b.classList.add('gold');
      }
      setTimeout(() => {
        if (b && this.now() > (this.armed[id] || 0)) {
          b.textContent = label;
          b.classList.remove('gold');
        }
      }, 5100);
      return false;
    }
    this.armed[id] = 0;
    return true;
  }

  async act(fn, done) {
    try {
      await fn();
      if (done !== null) this.msg = done || '';
    } catch (e) {
      this.msg = e.message || '出错了';
    }
    await this.loadMembers();
    await this.loadMemories();
    this.render();
  }

  async onClick(e) {
    const t = e.target;
    if (t.closest('#pp-prof')) return; // 自我档案自己处理（profile.js）
    if (t.closest('[data-close]')) return this.close();
    const who = t.closest('[data-member]');
    if (who) {
      this.member = who.dataset.member;
      this.msg = '';
      this.itemQuery = '';
      this.diary = null;
      await this.loadMemories();
      return this.render();
    }
    if (t.closest('[data-reset]')) return this.act(() => this.api.setPersona(this.member, null), '已恢复默认人设');
    if (t.closest('[data-tidy]')) {
      this.msg = '正在整理…';
      this.render();
      return this.act(async () => {
        const r = await this.api.tidy();
        this.msg = `整理好了，新写了 ${r.written} 条`;
      }, null);
    }
    const drop = t.closest('[data-drop]');
    if (drop) return this.act(() => this.api.dropHanging(Number(drop.dataset.drop)), '放下了');
    const idel = t.closest('[data-idel]');
    if (idel) return this.act(() => this.api.delItem(Number(idel.dataset.idel)), '删掉了');
    const iedit = t.closest('[data-iedit]');
    if (iedit) {
      const li = iedit.closest('li');
      const old = this.mind.items.find((r) => r.id === Number(iedit.dataset.iedit));
      li.querySelector('p').outerHTML = `<form class="pp-row pp-ieditf" data-iid="${old.id}"><textarea name="text" rows="2" maxlength="300">${esc(old.text)}</textarea><button class="btn primary" type="submit">保存</button></form>`;
      li.querySelector('textarea').focus();
      return;
    }
    const del = t.closest('[data-del]');
    if (del) return this.act(() => this.api.delMemory(Number(del.dataset.del)), '删掉了');
    const edit = t.closest('[data-edit]');
    if (edit) {
      const li = edit.closest('li');
      const old = this.memories.find((r) => r.id === Number(edit.dataset.edit));
      li.querySelector('p').outerHTML = `<form class="pp-row pp-editf" data-mid="${old.id}"><textarea name="text" rows="3" maxlength="300">${esc(old.text)}</textarea><button class="btn primary" type="submit">保存</button></form>`;
      li.querySelector('textarea').focus();
      return;
    }
    const clear = t.closest('[data-clear]');
    if (clear) {
      const all = clear.dataset.clear === 'all';
      const id = all ? 'pp-clear-all' : 'pp-clear-one';
      if (!this.armedOk(id, all ? '清空全部成员的记忆' : '清空 TA 的记忆')) return;
      return this.act(() => this.api.clearMemories(all ? null : this.member), '已清空');
    }
  }

  async onSubmit(e) {
    const f = e.target;
    if (f.closest('#pp-prof')) return; // 自我档案自己处理（profile.js）
    e.preventDefault();
    const data = new FormData(f);
    if (f.id === 'pp-persona') {
      const persona = {
        personality: String(data.get('personality') || '').trim(),
        speaking_style: String(data.get('speaking_style') || '').trim(),
        catchphrases: list(data.get('catchphrases')).slice(0, 5),
        interests: list(data.get('interests')).slice(0, 8),
      };
      return this.act(() => this.api.setPersona(this.member, persona), '人设已保存');
    }
    if (f.id === 'pp-add') {
      const text = String(data.get('text') || '').trim();
      if (!text) return;
      return this.act(() => this.api.addMemory(this.member, text), '记下了');
    }
    if (f.id === 'pp-isearch') {
      this.itemQuery = String(data.get('q') || '').trim();
      return this.act(async () => {}, null);
    }
    if (f.id === 'pp-diary') {
      const q = String(data.get('q') || '').trim();
      if (!q) return;
      return this.act(async () => {
        this.diary = (await this.api.readDiary(this.member, q)).found || [];
      }, null);
    }
    if (f.classList.contains('pp-ieditf')) {
      return this.act(() => this.api.editItem(Number(f.dataset.iid), String(data.get('text') || '')), '改好了（旧的留着）');
    }
    if (f.classList.contains('pp-editf')) {
      return this.act(() => this.api.editMemory(Number(f.dataset.mid), String(data.get('text') || '')), '改好了');
    }
  }
}
