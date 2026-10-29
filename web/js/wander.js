// 「上网」：成员自己上网（docs/WANDER.md）。顶栏一个按钮，有人在逛时变成「● 某某在逛」。
// 面板：正在逛的那位（小图 + 正在看的页面 + 叫停）、让某位去逛某个网站、每位成员的浏览记录
// （每页一句感想、结束时一段小结）。第一期只看不互动。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const OPEN_MS = 3000;
const IDLE_MS = 20000;

const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : '');

export class Wander {
  /**
   * @param {HTMLDialogElement} dlg
   * @param {HTMLButtonElement} button 顶栏按钮
   * @param {{api: Record<string, Function>}} opts
   */
  constructor(dlg, button, { api }) {
    Object.assign(this, { dlg, button, api });
    this.st = null;
    this.member = null; // 正在看谁的记录
    this.site = '';
    this.log = [];
    this.lastId = 0;
    this.err = '';
    this.busy = false;
    this.timer = null;
    button.addEventListener('click', () => this.open());
    dlg.addEventListener('click', (e) => this.onClick(e));
    dlg.addEventListener('change', (e) => this.onChange(e));
    dlg.addEventListener('close', () => this.schedule());
    this.refresh();
    this.schedule();
  }

  schedule() {
    clearInterval(this.timer);
    this.timer = setInterval(() => this.refresh(), this.dlg.open ? OPEN_MS : IDLE_MS);
  }

  async refresh() {
    try {
      this.st = await this.api.status();
    } catch {
      return;
    }
    this.paintButton();
    if (!this.dlg.open) return;
    await this.pull();
    this.renderLive();
    this.renderLog();
  }

  paintButton() {
    const r = this.st?.running;
    this.button.classList.toggle('wd-on', Boolean(r));
    this.button.textContent = '上网'; // 有人在逛时只加一个不占位置的绿点（.wd-on），顶栏不变宽
    this.button.title = r ? `${r.name}正在逛${r.site}：点开看看` : '成员自己上网：看她们逛了什么';
  }

  async open() {
    this.err = '';
    if (!this.dlg.open) this.dlg.showModal();
    this.schedule();
    try {
      this.st = await this.api.status();
    } catch (e) {
      this.err = e.message;
    }
    const members = this.st?.members || [];
    if (!this.member || !members.some((m) => m.id === this.member)) {
      this.member = this.st?.running?.member || members.find((m) => m.available)?.id || members[0]?.id || null;
    }
    this.resetLog();
    await this.pull();
    this.render();
  }

  resetLog() {
    this.gen = (this.gen || 0) + 1;
    this.log = [];
    this.lastId = 0;
  }

  async pull() {
    if (!this.member) return;
    const gen = this.gen;
    try {
      const data = await this.api.log(this.member, this.lastId);
      if (gen !== this.gen) return;
      const fresh = data.messages.filter((m) => m.id > this.lastId);
      if (fresh.length) {
        this.log.push(...fresh);
        this.log = this.log.slice(-300);
        this.lastId = fresh[fresh.length - 1].id;
      }
    } catch {
      /* 下次再拉 */
    }
  }

  render() {
    const st = this.st || {};
    const members = st.members || [];
    const online = members.filter((m) => m.available);
    const sites = st.sites || [];
    const head = '<div class="vh"><b>她们在上网</b><button type="button" class="btn" data-close>关闭</button></div>';
    const start = st.unavailable
      ? `<p class="wd-warn">现在不能上网：${esc(st.unavailable)}</p>`
      : `<div class="wd-start">
          <label>让 <select data-who aria-label="成员">${online.map((m) => `<option value="${esc(m.id)}" ${m.id === this.member ? 'selected' : ''}>${esc(m.name)}</option>`).join('')}</select></label>
          <label>去逛 <select data-site aria-label="网站"><option value="">随她挑</option>${sites.map((s) => `<option value="${esc(s.id)}" ${s.id === this.site ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select></label>
          <button type="button" class="btn primary" data-go ${this.busy || st.running || !online.length ? 'disabled' : ''}>去逛逛</button>
        </div>
        <p class="wd-hint">只看不互动（不点赞、不评论、不买东西）；在电脑上的独立浏览器里逛，和你的浏览器分开。${st.auto ? `空闲时会自己去逛，每人每天最多 ${esc(st.per_day)} 次。` : ''}花费算在生活每天的上限里。</p>`;
    const tabs = members
      .map((m) => `<button type="button" class="wd-tab ${m.id === this.member ? 'on' : ''}" data-member="${esc(m.id)}">${esc(m.name)}${m.today ? `<span>${m.today}</span>` : ''}</button>`)
      .join('');
    this.dlg.innerHTML = `${head}
      <div class="vb wd">
        <div class="wd-live"></div>
        ${start}
        ${this.err ? `<p class="wd-warn" role="alert">${esc(this.err)}</p>` : ''}
        <h4>浏览记录</h4>
        <div class="wd-tabs" role="tablist">${tabs}</div>
        <div class="wd-log" aria-live="polite"></div>
      </div>`;
    this.renderLive();
    this.renderLog();
  }

  renderLive() {
    const box = this.dlg.querySelector('.wd-live');
    if (!box) return;
    const r = this.st?.running;
    if (!r) {
      box.innerHTML = '';
      box.hidden = true;
      return;
    }
    box.hidden = false;
    let img = box.querySelector('.wd-frame');
    if (!img) {
      box.innerHTML = `<img class="wd-frame" alt="她正在看的页面" hidden><div class="wd-now"></div>`;
      img = box.querySelector('.wd-frame');
      img.addEventListener('load', () => (img.hidden = false));
      img.addEventListener('error', () => (img.hidden = true));
    }
    if (r.frame) img.src = this.api.frameUrl();
    const url = safeUrl(r.url);
    box.querySelector('.wd-now').innerHTML = `
      <div class="wd-bar"><span class="wd-dot" aria-hidden="true"></span><b>${esc(r.name)}</b> 正在逛${esc(r.site)} · 第 ${esc(r.steps)} / ${esc(r.max_steps)} 页</div>
      ${r.title ? `<p class="wd-title">${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(r.title)}</a>` : esc(r.title)}</p>` : ''}
      <button type="button" class="btn wd-stop" data-stop>叫停</button>`;
  }

  renderLog() {
    const box = this.dlg.querySelector('.wd-log');
    if (!box) return;
    const name = (this.st?.members || []).find((m) => m.id === this.member)?.name || '';
    if (!this.log.length) {
      box.innerHTML = `<p class="wd-hint">${esc(name)}还没自己上过网。</p>`;
      return;
    }
    const items = [...this.log].reverse().map((m) => {
      const w = m.meta?.wander || {};
      const when = (m.created_at || '').replace('T', ' ').slice(5, 16);
      if (w.kind === 'start') return `<p class="wd-trip"><span>${esc(when)}</span>${esc(m.text)}</p>`;
      if (w.kind === 'summary') return `<p class="wd-sum"><b>小结</b>${esc(m.text)}</p>`;
      if (w.kind === 'page') {
        const url = safeUrl(w.url);
        const title = w.title || w.url || '';
        const link = url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>` : esc(title);
        return `<p class="wd-page"><span class="wd-where">${link}</span>${esc(m.text)}</p>`;
      }
      return `<p class="wd-sys">${esc(m.text)}</p>`;
    });
    box.innerHTML = items.join('');
  }

  async onClick(e) {
    const t = e.target;
    if (t.closest('[data-close]')) return this.dlg.close();
    const tab = t.closest('[data-member]');
    if (tab) {
      this.member = tab.dataset.member;
      this.resetLog();
      await this.pull();
      return this.render();
    }
    if (t.closest('[data-stop]')) {
      try {
        this.st = await this.api.stop();
      } catch (x) {
        this.err = x.message;
      }
      return this.render();
    }
    if (t.closest('[data-go]')) return this.go();
  }

  onChange(e) {
    const t = e.target;
    if (t.matches('[data-site]')) this.site = t.value;
    else if (t.matches('[data-who]')) {
      this.member = t.value;
      this.resetLog();
      this.pull().then(() => this.render());
    }
  }

  async go() {
    const who = this.dlg.querySelector('[data-who]')?.value;
    if (!who) return;
    this.err = '';
    this.busy = true;
    this.render();
    try {
      this.st = await this.api.start(who, this.site || null);
      if (this.member !== who) {
        this.member = who;
        this.resetLog();
      }
      await this.pull();
    } catch (x) {
      this.err = x.message;
    } finally {
      this.busy = false;
    }
    this.paintButton();
    this.render();
  }
}
