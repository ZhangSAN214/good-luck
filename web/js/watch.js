// 「一起看」：投屏陪伴（docs/WATCH.md）。顶栏一个按钮，投屏时变成红点「投屏中」，哪个页面都看得到。
// 面板：选画面来源（整个屏幕 / 某个窗口 / 安卓手机平板）、选 1～3 位陪看、勾选同意后开始；
// 开始后左边是预览（只在电脑上），右边是和她们的聊天；可以「安静看」、随时停止。
// 截图只在电脑内存里，网页预览每几秒取一张；她们读到的是画面的文字描述（面板里可以展开看）。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const STATUS_MS = 3000;
const MESSAGES_MS = 1500;
const IDLE_MS = 15000; // 面板关着时多久看一次是否在投屏（顶栏红点）

export class Watch {
  /**
   * @param {HTMLDialogElement} dlg
   * @param {HTMLButtonElement} button 顶栏按钮
   * @param {{api: Record<string, Function>}} opts
   */
  constructor(dlg, button, { api }) {
    Object.assign(this, { dlg, button, api });
    this.st = null;
    this.devices = null;
    this.members = [];
    this.msgs = [];
    this.lastId = 0;
    this.picked = new Set();
    this.source = 'pc';
    this.consent = false;
    this.err = '';
    this.remote = false; // 平板上打开的：不能开始、不能预览
    this.busy = false;
    this.timers = [];
    button.addEventListener('click', () => this.open());
    dlg.addEventListener('click', (e) => this.onClick(e));
    dlg.addEventListener('change', (e) => this.onChange(e));
    dlg.addEventListener('submit', (e) => this.onSubmit(e));
    dlg.addEventListener('close', () => this.schedule());
    this.refresh();
    this.schedule();
  }

  schedule() {
    this.timers.forEach(clearInterval);
    this.timers = [];
    if (this.dlg.open) {
      this.timers.push(setInterval(() => this.refresh(), STATUS_MS));
      this.timers.push(setInterval(() => this.pull(), MESSAGES_MS));
    } else {
      this.timers.push(setInterval(() => this.refresh(), IDLE_MS));
    }
  }

  async refresh() {
    const was = Boolean(this.st?.running);
    try {
      this.st = await this.api.status();
    } catch {
      return;
    }
    this.paintButton();
    if (!this.dlg.open) return;
    if (this.st.running && was) this.renderLive(); // 只刷新状态区和预览，不打断正在输入的话
    else if (this.st.running !== was) {
      if (!this.st.running) await this.loadSetup();
      else this.render();
    }
  }

  paintButton() {
    const on = Boolean(this.st?.running);
    this.button.classList.toggle('wt-live', on);
    this.button.textContent = on ? '● 投屏中' : '一起看';
    this.button.title = on ? '正在投屏给成员看：点开聊天或停止' : '投屏陪伴：把屏幕给成员看，她们陪你一起看';
  }

  async open() {
    this.err = '';
    if (!this.dlg.open) this.dlg.showModal();
    this.schedule();
    await this.refresh();
    await this.loadSetup();
    this.resetChat();
    await this.pull();
    this.render();
  }

  async loadSetup() {
    try {
      const data = await this.api.status(true);
      this.devices = data.devices;
      this.scrcpy = Boolean(data.scrcpy);
    } catch (e) {
      if (e.status === 403) this.remote = true;
      else this.err = e.message;
    }
    try {
      this.members = (await this.api.members()).members || [];
    } catch {
      this.members = [];
    }
    this.render();
  }

  /** 从头重新拉聊天（打开面板、开始新的一场时）；还在路上的旧请求作废。 */
  resetChat() {
    this.gen = (this.gen || 0) + 1;
    this.lastId = 0;
    this.msgs = [];
  }

  async pull() {
    const gen = this.gen;
    try {
      const data = await this.api.messages(this.lastId);
      if (gen !== this.gen) return;
      // 定时拉取和发送后的拉取可能同时回来：按 id 去重
      const fresh = data.messages.filter((m) => m.id > this.lastId);
      if (fresh.length) {
        this.msgs.push(...fresh);
        this.msgs = this.msgs.slice(-200);
        this.lastId = fresh[fresh.length - 1].id;
      }
      this.typing = data.typing || [];
    } catch {
      return;
    }
    if (this.dlg.open) this.renderChat();
  }

  sources() {
    const out = [];
    for (const [kind, d] of Object.entries(this.devices || {})) {
      if (!d.available) out.push({ kind, reason: d.reason });
      for (const s of d.sources) out.push({ kind, ...s });
    }
    return out;
  }

  render() {
    const st = this.st || {};
    const b = st.budget || { spent_usd: 0, limit_usd: 0 };
    const money = `本月 $${Number(b.spent_usd).toFixed(2)} / $${Number(b.limit_usd).toFixed(2)}`;
    const head = `<div class="vh"><b>一起看</b><span class="wt-money">${money}</span><button type="button" class="btn" data-close>关闭</button></div>`;
    if (st.running) {
      const scrcpy = st.source?.id?.startsWith('android:') && this.scrcpy;
      this.dlg.innerHTML = `${head}
        <div class="vb wt wt-on">
          <div class="wt-left">
            ${this.remote ? '<p class="wt-hint">预览只在电脑上显示。</p>' : '<img class="wt-frame" alt="当前画面预览" hidden>'}
            <div class="wt-live"></div>
            <div class="wt-ctrls">
              <label class="hint switch"><input type="checkbox" data-quiet ${st.quiet ? 'checked' : ''}> 安静看（不主动说话）</label>
              ${scrcpy ? '<button type="button" class="btn" data-scrcpy>流畅窗口</button>' : ''}
              <button type="button" class="btn wt-stop" data-stop>停止投屏</button>
            </div>
          </div>
          <div class="wt-right">
            <div class="wt-chat" aria-live="polite"></div>
            <form class="wt-say" autocomplete="off">
              <input type="text" maxlength="1000" placeholder="和她们说点什么（@名字 指定谁回）" aria-label="说话">
              <button class="btn primary" type="submit">发送</button>
            </form>
          </div>
        </div>`;
      const img = this.dlg.querySelector('.wt-frame');
      if (img) {
        img.addEventListener('load', () => (img.hidden = false));
        img.addEventListener('error', () => (img.hidden = true));
      }
      this.renderLive();
      this.renderChat();
      return;
    }
    const srcs = this.sources();
    const list = srcs.length
      ? srcs
          .map((s) =>
            s.id
              ? `<label class="wt-opt"><input type="radio" name="wt-src" value="${esc(s.id)}" ${this.source === s.id ? 'checked' : ''}> <span class="wt-k">${s.kind === 'android' ? '安卓' : s.kind === 'window' ? '窗口' : '电脑'}</span>${esc(s.title)}</label>`
              : `<p class="wt-hint">${s.kind === 'android' ? '安卓' : '电脑'}：${esc(s.reason)}</p>`,
          )
          .join('')
      : '<p class="wt-hint">正在找可以投屏的画面…</p>';
    const online = this.members.filter((m) => m.available);
    const max = st.max_watchers || 3;
    const people = online
      .map(
        (m) =>
          `<label class="wt-opt"><input type="checkbox" data-member="${esc(m.id)}" ${this.picked.has(m.id) ? 'checked' : ''} ${!this.picked.has(m.id) && this.picked.size >= max ? 'disabled' : ''}> ${esc(m.name)}</label>`,
      )
      .join('');
    const last = this.msgs.length ? '<h4>上一次</h4><div class="wt-chat wt-past"></div>' : '';
    this.dlg.innerHTML = `${head}
      <div class="vb wt">
        ${this.remote ? '<p class="wt-warn">投屏只能在电脑上开始。开始以后，平板上可以聊天和停止。</p>' : `
        <h4>投什么 <button type="button" class="link" data-reload>刷新</button></h4>
        <div class="wt-list">${list}</div>
        <h4>谁陪你看（最多 ${max} 位）</h4>
        <div class="wt-list wt-people">${people || '<p class="wt-hint">现在没有在线的成员。</p>'}</div>
        <label class="wt-consent"><input type="checkbox" data-consent ${this.consent ? 'checked' : ''}> 我同意：画面明显变化时，把截图交给云端的识图模型写成文字给她们看（截图不存盘；支付、银行、密码窗口自动暂停；每月最多 $${Number(b.limit_usd).toFixed(2)}）</label>
        <div class="wt-ctrls"><button type="button" class="btn primary" data-start ${this.busy ? 'disabled' : ''}>开始一起看</button></div>`}
        ${this.err ? `<p class="wt-warn" role="alert">${esc(this.err)}</p>` : ''}
        ${last}
      </div>`;
    this.renderChat();
  }

  /** 投屏中：只更新状态区、预览和钱，不动聊天输入框。 */
  renderLive() {
    const st = this.st || {};
    const live = this.dlg.querySelector('.wt-live');
    if (!live) return this.render();
    const seen = live.querySelector('details');
    const open = seen ? seen.open : false;
    live.innerHTML = `
      <div class="wt-bar"><span class="wt-dot" aria-hidden="true"></span>正在投屏「${esc(st.source?.title)}」· ${esc(st.minutes)} 分钟 · 陪看：${(st.watchers || []).map((w) => esc(w.name)).join('、')}</div>
      ${st.paused ? `<p class="wt-warn" role="status">${esc(st.paused)}</p>` : ''}
      ${st.error ? `<p class="wt-warn" role="status">${esc(st.error)}</p>` : ''}
      <details class="wt-seen"${open ? ' open' : ''}><summary>她们看到的（画面的文字描述）</summary>
        ${(st.screens || []).map((s) => `<p><span>${s.ago_s < 60 ? '刚才' : `${Math.round(s.ago_s / 60)} 分钟前`}</span>${esc(s.text)}</p>`).join('') || '<p>还没看到画面。</p>'}
      </details>`;
    const img = this.dlg.querySelector('.wt-frame');
    if (img && !st.paused) img.src = this.api.frameUrl();
    else if (img) img.hidden = true;
    const b = st.budget || { spent_usd: 0, limit_usd: 0 };
    const money = this.dlg.querySelector('.wt-money');
    if (money) money.textContent = `本月 $${Number(b.spent_usd).toFixed(2)} / $${Number(b.limit_usd).toFixed(2)}`;
  }

  renderChat() {
    const box = this.dlg.querySelector('.wt-chat');
    if (!box) return;
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.innerHTML =
      this.msgs
        .map((m) =>
          m.author === 'system'
            ? `<p class="wt-sys">${esc(m.text)}</p>`
            : `<p class="wt-msg ${m.author === 'user' ? 'me' : ''}"><b>${esc(m.name)}</b>${esc(m.text)}</p>`,
        )
        .join('') + ((this.typing || []).length ? `<p class="wt-sys">${this.typing.map(esc).join('、')} 正在输入…</p>` : '');
    if (atBottom) box.scrollTop = box.scrollHeight;
  }

  async onClick(e) {
    const t = e.target;
    if (t.closest('[data-close]')) return this.dlg.close();
    if (t.closest('[data-reload]')) return this.loadSetup();
    if (t.closest('[data-stop]')) {
      await this.api.stop().catch((x) => (this.err = x.message));
      await this.refresh();
      return this.loadSetup();
    }
    if (t.closest('[data-scrcpy]')) {
      try {
        await this.api.scrcpy(this.st.source.id);
      } catch (x) {
        this.err = x.message;
        this.render();
      }
      return;
    }
    if (t.closest('[data-start]')) return this.start();
  }

  onChange(e) {
    const t = e.target;
    if (t.name === 'wt-src') this.source = t.value;
    else if (t.matches('[data-member]')) {
      if (t.checked) this.picked.add(t.dataset.member);
      else this.picked.delete(t.dataset.member);
      this.render();
    } else if (t.matches('[data-consent]')) this.consent = t.checked;
    else if (t.matches('[data-quiet]')) this.api.quiet(t.checked).then(() => this.refresh()).catch(() => {});
  }

  async start() {
    this.err = '';
    if (!this.consent) this.err = '请先勾选同意。';
    else if (!this.picked.size) this.err = '至少选一位陪你看。';
    if (this.err) return this.render();
    this.busy = true;
    this.render();
    try {
      this.st = await this.api.start(this.source, [...this.picked], true);
      this.resetChat();
      await this.pull();
    } catch (x) {
      this.err = x.message;
    } finally {
      this.busy = false;
    }
    this.paintButton();
    this.render();
  }

  async onSubmit(e) {
    e.preventDefault();
    const input = e.target.querySelector('input');
    const text = input?.value.trim();
    if (!text) return;
    input.value = '';
    try {
      await this.api.say(text);
      await this.pull();
    } catch (x) {
      this.err = x.message;
      this.render();
    }
  }
}
