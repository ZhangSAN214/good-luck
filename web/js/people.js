// 成员设定与记忆：聊天里每位成员的人设（性格、说话风格、口头禅、兴趣）和 TA 的记忆。
// 人设和记忆只用于私聊、群聊、朋友圈，工作模式与办公室不读取；记忆只存在你电脑上，可以改、删、清空。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const LAYER = { note: '记下的', day: '一天', week: '一周', month: '一个月', profile: '长期档案' };
const SOURCE = { local: '你改过的设定', config: '配置文件里的设定', empty: '没有设定（用模型本身的风格）' };
const list = (s) =>
  String(s || '')
    .split(/[,，、\n]/)
    .map((x) => x.trim())
    .filter(Boolean);

export class People {
  constructor(dialog, { api, now = () => Date.now() } = {}) {
    this.dlg = dialog;
    this.api = api; // {personas(), setPersona(id, p), memories(id), addMemory(id, text), editMemory(mid, text), delMemory(mid), clearMemories(id?), tidy()}
    this.now = now;
    this.members = [];
    this.member = null;
    this.memories = [];
    this.armed = {}; // 两步确认：按钮 id → 截止时间
    this.msg = '';
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
    } catch (e) {
      this.msg = e.message;
    }
  }

  current() {
    return this.members.find((m) => m.id === this.member) || null;
  }

  render() {
    const m = this.current();
    const p = m?.persona || {};
    const tabs = this.members
      .map((x) => `<button type="button" class="pp-who${x.id === this.member ? ' cur' : ''}" data-member="${esc(x.id)}"${x.id === this.member ? ' aria-current="true"' : ''}>${esc(x.name)}</button>`)
      .join('');
    const mems = this.memories.length
      ? this.memories
          .map(
            (r) => `<li data-mid="${r.id}"><div class="pp-mh"><b>${esc(LAYER[r.layer] || r.layer)}</b>${r.period ? `<span>${esc(r.period)}</span>` : ''}${r.source === 'user' ? '<span>你写的</span>' : ''}${r.offline ? '<span>你不在时</span>' : ''}
              <button type="button" class="link" data-edit="${r.id}">改</button><button type="button" class="link" data-del="${r.id}">删</button></div>
              <p>${esc(r.text).replace(/\n/g, '<br>')}</p></li>`,
          )
          .join('')
      : '<li class="hint">还没有记忆。每天聊过天之后会自动整理一段；你也可以替 TA 记一条。</li>';
    this.dlg.innerHTML = `
      <div class="vh"><b>成员设定与记忆</b><button type="button" class="btn" data-close>关闭</button></div>
      <div class="vb pp">
        <nav class="pp-members" aria-label="成员">${tabs}</nav>
        ${
          m
            ? `<form id="pp-persona" class="pp-form">
          <h4>${esc(m.name)} 的人设 <small>${esc(SOURCE[m.source] || '')}</small></h4>
          <p class="hint">只用于私聊、群聊和朋友圈；工作和办公室不受影响。空着就用模型本身的说话风格。</p>
          <label>性格<input name="personality" maxlength="300" value="${esc(p.personality)}" placeholder="比如：安静、爱较真"></label>
          <label>说话风格<input name="speaking_style" maxlength="300" value="${esc(p.speaking_style)}" placeholder="比如：句子短，爱用比喻"></label>
          <label>口头禅（逗号分隔，最多 5 个）<input name="catchphrases" value="${esc((p.catchphrases || []).join('，'))}"></label>
          <label>兴趣（逗号分隔，最多 8 个；上网时按兴趣看新闻）<input name="interests" value="${esc((p.interests || []).join('，'))}"></label>
          <div class="pp-row"><button class="btn primary" type="submit">保存人设</button><button type="button" class="btn" data-reset>恢复默认</button></div>
        </form>
        <section class="pp-mem" aria-label="记忆">
          <h4>${esc(m.name)} 的记忆 <small>只存在你电脑上</small></h4>
          <form id="pp-add" class="pp-row"><input name="text" maxlength="300" placeholder="替 TA 记一条，比如：我下周三考试" aria-label="记一条"><button class="btn" type="submit">记下</button></form>
          <ul class="pp-list">${mems}</ul>
          <div class="pp-row"><button type="button" class="btn" data-tidy>现在整理</button><button type="button" class="btn" id="pp-clear-one" data-clear="one">清空 TA 的记忆</button><button type="button" class="btn" id="pp-clear-all" data-clear="all">清空全部成员的记忆</button></div>
        </section>`
            : '<p class="hint">没有成员。</p>'
        }
        <p class="hint pp-msg" role="status">${esc(this.msg)}</p>
      </div>`;
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
    if (t.closest('[data-close]')) return this.close();
    const who = t.closest('[data-member]');
    if (who) {
      this.member = who.dataset.member;
      this.msg = '';
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
    e.preventDefault();
    const f = e.target;
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
    if (f.classList.contains('pp-editf')) {
      return this.act(() => this.api.editMemory(Number(f.dataset.mid), String(data.get('text') || '')), '改好了');
    }
  }
}
