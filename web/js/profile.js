// 自我档案（成员引擎 M1）：人物卡读进来的档案、随身卡、修改记录。放在「成员设定与记忆」里。
// 档案只存在你电脑上；每次修改都是一个新版本，可以撤回；你锁住的项成长机制改不了。
// 只用于私聊、群聊、朋友圈和生活；工作和办公室不读取。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const SECTION = { identity: '身份', personality: '性格', values: '三观', style: '说话', likes: '喜好', state: '状态' };
const WHO = { user: '你', growth: '成长', program: '程序' };
const CARD_KEY = '随身卡'; // identity.随身卡：你自己写的随身卡
const LONG = 60; // 超过这么多字的值折叠显示
// 人物卡原文是 Markdown：显示时去掉 # 和 ** 这类记号
const plain = (s) => String(s).replace(/^#+\s*/gm, '').replace(/\*\*|__/g, '');

// 值 ↔ 编辑框文字：列表用「、」分隔；对象（五项性格分）写成「外向 3、较真 4」
const show = (v) => {
  if (Array.isArray(v)) return v.join('、');
  if (v && typeof v === 'object') return Object.entries(v).map(([k, x]) => `${k} ${x}`).join('、');
  return String(v ?? '');
};
const parse = (text, old) => {
  const s = String(text || '').trim();
  if (Array.isArray(old)) return s.split(/[、，,\n]/).map((x) => x.trim()).filter(Boolean);
  if (old && typeof old === 'object') {
    const out = {};
    for (const part of s.split(/[、，,\n]/)) {
      const m = part.trim().match(/^(.+?)\s*[:：]?\s*(-?\d+(?:\.\d+)?)$/);
      if (m) out[m[1].trim()] = Number(m[2]);
    }
    return out;
  }
  return s;
};

export class ProfilePanel {
  constructor({ api, now = () => Date.now() } = {}) {
    this.api = api; // {profiles(), profile(id), change(id, patch, reason), lock(id, path, locked), revert(id, v), clear(id), importCards(text, mapping, dryRun)}
    this.now = now;
    this.member = null;
    this.p = null; // 当前档案（接口返回）
    this.msg = '';
    this.editing = null; // 正在改的项：'分区.键' 或 'card'
    this.adding = null; // 正在加项的分区
    this.pending = null; // 导入预览：{text, cards, members, source}
    this.armed = 0;
    this.el = null;
  }

  // People 每次重绘后把容器交过来
  async mount(el, member) {
    this.el = el;
    // 重绘会把点到的元素换掉：不让事件再冒泡到外层的「成员设定与记忆」
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      this.onClick(e);
    });
    el.addEventListener('submit', (e) => {
      e.stopPropagation();
      this.onSubmit(e);
    });
    el.addEventListener('change', (e) => this.onChange(e));
    if (member !== this.member) {
      this.member = member;
      this.editing = this.adding = null;
      this.msg = '';
      this.p = null;
    }
    this.render();
    await this.load();
  }

  async load() {
    if (!this.member) return;
    try {
      this.p = await this.api.profile(this.member);
    } catch (e) {
      this.msg = e.message;
    }
    this.render();
  }

  item(sec, key, value) {
    const path = `${sec}.${key}`;
    const locked = (this.p.locked || []).includes(path);
    const text = show(value);
    const body =
      this.editing === path
        ? `<form class="pf-edit" data-path="${esc(path)}"><textarea name="v" rows="${Math.min(12, Math.max(2, Math.ceil(text.length / 28)))}">${esc(text)}</textarea><div class="pp-row grp-end"><button type="button" class="btn" data-cancel>取消</button><button class="btn primary" type="submit">保存</button></div></form>`
        : text.length > LONG
          ? `<details class="pf-long"><summary>${esc(plain(text).replace(/\s+/g, ' ').slice(0, LONG))}…</summary><p>${esc(plain(text)).replace(/\n/g, '<br>')}</p></details>`
          : `<p>${esc(text)}</p>`;
    return `<li class="pf-item${locked ? ' locked' : ''}" data-key="${esc(path)}">
      <div class="pp-mh"><b>${esc(key)}</b>${locked ? '<span>已锁定</span>' : ''}
        <button type="button" class="link" data-edit="${esc(path)}">改</button>
        <button type="button" class="link" data-lock="${esc(path)}" data-locked="${locked ? 1 : 0}">${locked ? '解锁' : '锁定'}</button>
        <button type="button" class="link" data-del="${esc(path)}">删除</button></div>${body}</li>`;
  }

  section(sec, who) {
    // 短的项（名字、口头禅…）在前，整段原文在后
    const items = Object.entries((this.p.data || {})[sec] || {})
      .filter(([k]) => !(sec === 'identity' && k === CARD_KEY))
      .sort((a, b) => (show(a[1]).length > LONG) - (show(b[1]).length > LONG));
    const add =
      this.adding === sec
        ? `<li><form class="pf-add" data-sec="${esc(sec)}"><div class="grp-row"><input name="k" maxlength="30" placeholder="名称，比如：最近在读" aria-label="名称"></div><textarea name="v" rows="2" maxlength="2000" placeholder="内容" aria-label="内容"></textarea><div class="pp-row grp-end"><button type="button" class="btn" data-cancel>取消</button><button class="btn primary" type="submit">加上</button></div></form></li>`
        : `<li><button type="button" class="link" data-add="${esc(sec)}">＋ 加一项</button></li>`;
    return `<details class="pf-sec" data-sec="${esc(sec)}"${this.adding === sec || (this.editing || '').startsWith(`${sec}.`) ? ' open' : ''}>
      <summary><b>${esc(SECTION[sec] || sec)}</b><span>${items.length} 项 · 能改的：${who.map((w) => esc(WHO[w] || w)).join('、')}</span></summary>
      <ul class="pp-list grp-box">${items.map(([k, v]) => this.item(sec, k, v)).join('')}${add}</ul></details>`;
  }

  history() {
    const rows = (this.p.history || [])
      .map((h) => {
        const changes = (h.changes || []).map((c) => c.path).slice(0, 6).join('、');
        const cur = h.version === this.p.version;
        return `<li><div class="pp-mh"><b>第 ${h.version} 版</b><span>${esc(WHO[h.author] || h.author)}</span><span>${esc(String(h.created_at || '').slice(0, 16).replace('T', ' '))}</span>
          ${cur ? '<span>当前</span>' : `<button type="button" class="link" data-revert="${h.version}">撤回到这版</button>`}</div>
          <p>${esc(h.reason || '')}${changes ? `<br><small>${esc(changes)}${(h.changes || []).length > 6 ? ' …' : ''}</small>` : ''}</p></li>`;
      })
      .join('');
    return `<details class="pf-sec"><summary><b>修改记录</b><span>${(this.p.history || []).length} 版 · 旧版本都留着</span></summary><ul class="pp-list grp-box">${rows || '<li class="hint">还没有修改。</li>'}</ul></details>`;
  }

  preview() {
    const v = this.pending;
    const opts = (sel) =>
      `<option value="">不导入</option>${v.members.map((m) => `<option value="${esc(m.id)}"${m.id === sel ? ' selected' : ''}>${esc(m.name)}</option>`).join('')}`;
    const rows = v.cards
      .map((c) => `<label class="grp-row"><span>${esc(c.name)}</span><select name="${esc(c.name)}" aria-label="${esc(c.name)} 对应的成员">${opts(c.member)}</select></label>`)
      .join('');
    return `<form class="pf-import grp">
      <h4 class="grp-h">导入人物卡 <small>${esc(v.source)} · ${v.cards.length} 张</small></h4>
      <div class="grp-box">${rows}</div>
      <p class="grp-foot">按卡上的名字自动对上成员，可以改。导入后每位成员存成一个新版本（能撤回）；你锁住的项不动，你加的项保留。</p>
      <div class="pp-row grp-end"><button type="button" class="btn" data-cancel-import>取消</button><button class="btn primary" type="submit">导入</button></div></form>`;
  }

  render() {
    if (!this.el) return;
    const file = '<input type="file" accept=".md,.markdown,.txt,text/markdown,text/plain" hidden data-file>';
    const tools = `<div class="grp-box grp-acts"><button type="button" class="grp-btn" data-import>导入人物卡（.md）</button>${
      this.p && this.p.version ? '<button type="button" class="grp-btn danger" id="pf-clear" data-clear>清空 TA 的档案和修改记录</button>' : ''
    }</div>${file}`;
    if (this.pending) {
      this.el.innerHTML = `${this.preview()}<p class="hint pp-msg" role="status">${esc(this.msg)}</p>`;
      return;
    }
    if (!this.p) {
      this.el.innerHTML = `<h4 class="grp-h">自我档案</h4><p class="hint">读取中…</p><p class="hint pp-msg" role="status">${esc(this.msg)}</p>`;
      return;
    }
    const card = this.p.card || {};
    const head = `<h4 class="grp-h">自我档案 <small>${this.p.version ? `第 ${this.p.version} 版 · ` : ''}只存在你电脑上</small></h4>`;
    if (!this.p.version) {
      this.el.innerHTML = `${head}<p class="grp-foot pf-empty">还没有档案。导入人物卡后，私聊、群聊、朋友圈和生活里 TA 会带着档案的「随身卡」说话；下面的简单人设就不再进提示词了。</p>${tools}<p class="hint pp-msg" role="status">${esc(this.msg)}</p>`;
      return;
    }
    const cardBody =
      this.editing === 'card'
        ? `<form class="pf-edit" data-path="card"><textarea name="v" rows="10" maxlength="${card.budget || 400}">${esc(card.text)}</textarea><div class="pp-row grp-end"><button type="button" class="btn" data-cancel>取消</button><button class="btn primary" type="submit">保存随身卡</button></div></form>`
        : `<pre class="pf-card">${esc(card.text || '（空）')}</pre>`;
    const sections = Object.entries(this.p.sections || {})
      .map(([sec, who]) => this.section(sec, who))
      .join('');
    this.el.innerHTML = `${head}
      <div class="grp-box pf-cardbox">
        <div class="pp-mh"><b>随身卡</b><span>${card.chars || 0}/${card.budget || 400} 字</span><span>${card.custom ? '你写的' : '从档案自动生成'}</span>
          <button type="button" class="link" data-edit="card">改</button>${card.custom ? '<button type="button" class="link" data-card-auto>改回自动生成</button>' : ''}</div>
        ${cardBody}
      </div>
      <p class="grp-foot">每次聊天、发朋友圈、过生活时 TA 都带着这张卡（总共只带约 ${esc(card.budget || 400)} 字，省钱）。完整档案在下面，以后成长、记忆等模块会用到。</p>
      <div class="pf-secs">${sections}${this.history()}</div>
      ${tools}
      <p class="hint pp-msg" role="status">${esc(this.msg)}</p>`;
  }

  async act(fn, done) {
    try {
      const r = await fn();
      if (r && r.member && r.data) this.p = r;
      else await this.load();
      this.msg = done || '';
      this.editing = this.adding = null;
    } catch (e) {
      this.msg = e.message || '出错了';
    }
    this.render();
  }

  valueOf(path) {
    const [sec, ...rest] = path.split('.');
    return ((this.p.data || {})[sec] || {})[rest.join('.')];
  }

  patch(path, value) {
    const [sec, ...rest] = path.split('.');
    return { [sec]: { [rest.join('.')]: value } };
  }

  async onClick(e) {
    const t = e.target;
    if (t.closest('[data-import]')) return this.el.querySelector('[data-file]')?.click();
    if (t.closest('[data-cancel-import]')) {
      this.pending = null;
      this.msg = '';
      return this.render();
    }
    if (t.closest('[data-cancel]')) {
      this.editing = this.adding = null;
      return this.render();
    }
    const edit = t.closest('[data-edit]');
    if (edit) {
      this.editing = edit.dataset.edit;
      this.render();
      this.el.querySelector('.pf-edit textarea')?.focus();
      return;
    }
    const add = t.closest('[data-add]');
    if (add) {
      this.adding = add.dataset.add;
      this.render();
      this.el.querySelector('.pf-add input')?.focus();
      return;
    }
    if (t.closest('[data-card-auto]')) return this.act(() => this.api.change(this.member, { identity: { [CARD_KEY]: null } }, '随身卡改回自动生成'), '随身卡改回自动生成了');
    const lock = t.closest('[data-lock]');
    if (lock) {
      const locked = lock.dataset.locked !== '1';
      return this.act(() => this.api.lock(this.member, lock.dataset.lock, locked), locked ? '锁定了：成长机制改不了这一项' : '解锁了');
    }
    const del = t.closest('[data-del]');
    if (del) return this.act(() => this.api.change(this.member, this.patch(del.dataset.del, null), `删掉 ${del.dataset.del}`), '删掉了（修改记录里还能撤回）');
    const rev = t.closest('[data-revert]');
    if (rev) return this.act(() => this.api.revert(this.member, Number(rev.dataset.revert)), `撤回到第 ${rev.dataset.revert} 版（存成了新的一版）`);
    if (t.closest('[data-clear]')) {
      const b = this.el.querySelector('#pf-clear');
      if (this.now() > this.armed) {
        this.armed = this.now() + 5000;
        if (b) {
          b.textContent = '再点一次确认';
          b.classList.add('gold');
        }
        return;
      }
      this.armed = 0;
      return this.act(() => this.api.clear(this.member), '档案清空了');
    }
  }

  async onChange(e) {
    const input = e.target.closest('[data-file]');
    if (!input || !input.files?.length) return;
    const f = input.files[0];
    try {
      const text = await f.text();
      const r = await this.api.importCards(text, null, true);
      this.pending = { text, cards: r.cards, members: r.members, source: r.source };
      this.msg = '';
    } catch (err) {
      this.msg = err.message || '读不了这个文件';
    }
    input.value = '';
    this.render();
  }

  async onSubmit(e) {
    e.preventDefault();
    const f = e.target;
    const data = new FormData(f);
    if (f.classList.contains('pf-import')) {
      const mapping = {};
      for (const c of this.pending.cards) mapping[c.name] = String(data.get(c.name) || '');
      const text = this.pending.text;
      this.pending = null;
      this.msg = '正在导入…';
      this.render();
      try {
        const r = await this.api.importCards(text, mapping, false);
        this.msg = `导入了 ${r.imported} 位成员的人物卡`;
      } catch (err) {
        this.msg = err.message || '导入失败';
      }
      return this.load();
    }
    if (f.classList.contains('pf-edit')) {
      const path = f.dataset.path;
      const raw = String(data.get('v') || '');
      if (path === 'card') {
        return this.act(() => this.api.change(this.member, { identity: { [CARD_KEY]: raw.trim() || null } }, '你改了随身卡'), '随身卡已保存');
      }
      const value = parse(raw, this.valueOf(path));
      const empty = Array.isArray(value) ? !value.length : typeof value === 'object' ? !Object.keys(value).length : !value;
      return this.act(() => this.api.change(this.member, this.patch(path, empty ? null : value), `你改了 ${path}`), '改好了');
    }
    if (f.classList.contains('pf-add')) {
      const key = String(data.get('k') || '').trim();
      const value = String(data.get('v') || '').trim();
      if (!key || !value) return;
      return this.act(() => this.api.change(this.member, this.patch(`${f.dataset.sec}.${key}`, value), `你加了 ${f.dataset.sec}.${key}`), '加上了');
    }
  }
}
