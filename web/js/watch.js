// 「一起看」：投屏陪伴（docs/WATCH.md）。顶栏一个按钮，投屏时变成红点「投屏中」，哪个页面都看得到。
// 面板：选画面来源（整个屏幕 / 某个窗口 / 安卓手机平板）、选 1～3 位陪看、勾选同意后开始；
// 开始后左边是预览（只在电脑上），右边是和她们的聊天；可以「安静看」、随时停止。
// 截图只在电脑内存里，网页预览每几秒取一张；她们读到的是画面的文字描述（面板里可以展开看）。
// 安卓设备在面板里用无线调试配对 / 连接（扫二维码，或填配对码）；iPhone 只能用 AirPlay 镜像到电脑上的窗口再选那个窗口。
// 看视频时不用切回来：「悬浮窗」在电脑上开一个总在最前面的小窗（可拖动、调透明度、收成小圆点）；
// 浏览器支持时还有「画中画」小窗（同样总在最前面）。两种小窗里都能直接打字。

import { OverlayView } from './overlay.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const STATUS_MS = 3000;
const MESSAGES_MS = 1500;
const IDLE_MS = 15000; // 面板关着时多久看一次是否在投屏（顶栏红点）
const QR_POLL_MS = 2000; // 等手机扫配对二维码时多久看一次

/** 本机生成二维码（不联网），画成 SVG。 */
async function qrSvg(text) {
  if (typeof window.qrcode !== 'function') {
    await new Promise((resolve, reject) => {
      const el = document.createElement('script');
      el.src = 'boot/assets/qrcode-gen.js';
      el.onload = resolve;
      el.onerror = () => reject(new Error('二维码组件加载失败'));
      document.head.appendChild(el);
    });
  }
  const qr = window.qrcode(0, 'M');
  qr.addData(text, 'Byte');
  qr.make();
  const n = qr.getModuleCount();
  let d = '';
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (qr.isDark(y, x)) d += `M${x + 4} ${y + 4}h1v1h-1z`;
  return `<svg class="wt-qr" viewBox="0 0 ${n + 8} ${n + 8}" role="img" aria-label="安卓配对二维码" shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/><path d="${d}" fill="#000"/></svg>`;
}

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
    dlg.addEventListener('close', () => {
      this.stopQr();
      this.schedule();
    });
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
    if (!this.st.running && this.pip) this.closePip();
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
              ${this.remote ? '' : '<button type="button" class="btn" data-overlay title="总在最前面的小窗：看视频时不用切回来">悬浮窗</button>'}
              ${'documentPictureInPicture' in window ? '<button type="button" class="btn" data-pip title="浏览器的画中画小窗，总在最前面">画中画</button>' : ''}
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
        ${this.phoneHelp()}
        <h4>谁陪你看（最多 ${max} 位）</h4>
        <div class="wt-list wt-people">${people || '<p class="wt-hint">现在没有在线的成员。</p>'}</div>
        <label class="wt-consent"><input type="checkbox" data-consent ${this.consent ? 'checked' : ''}> 我同意：画面明显变化时，把截图交给云端的识图模型写成文字给她们看（截图不存盘；支付、银行、密码窗口自动暂停；每月最多 $${Number(b.limit_usd).toFixed(2)}）</label>
        <div class="wt-ctrls"><button type="button" class="btn primary" data-start ${this.busy ? 'disabled' : ''}>开始一起看</button></div>`}
        ${this.err ? `<p class="wt-warn" role="alert">${esc(this.err)}</p>` : ''}
        ${last}
      </div>`;
    this.renderChat();
  }

  /** 连手机 / 平板：安卓无线调试扫码或填配对码；iPhone 用 AirPlay 镜像到电脑窗口。 */
  phoneHelp() {
    const a = this.adb || {};
    const msg = a.msg ? `<p class="wt-hint" role="status">${esc(a.msg)}</p>` : '';
    return `
      <details class="wt-phone"${a.open ? ' open' : ''} data-phone>
        <summary>连接安卓手机 / 平板（无线，不用插线）</summary>
        <ol>
          <li>手机、平板和这台电脑连同一个 Wi-Fi。</li>
          <li>打开开发者选项：设置 → 关于手机（平板）→ 版本信息 → 连续点「版本号」7 次。</li>
          <li>设置 → 系统与更新 → 开发者选项 → 打开「无线调试」，点进去。</li>
        </ol>
        <h5>方式一：扫二维码（推荐）</h5>
        <p class="wt-hint">点下面的按钮，在手机上点「使用二维码配对设备」扫这张图，配好后自动连上。</p>
        <div class="wt-adb">
          <button type="button" class="btn" data-qr ${a.busy || a.qr ? 'disabled' : ''}>${a.qr ? '等手机扫码…' : '生成配对二维码'}</button>
          ${a.qr ? '<button type="button" class="btn" data-qr-cancel>取消</button>' : ''}
        </div>
        ${a.qr || ''}
        ${a.via === 'qr' ? msg : ''}
        <h5>方式二：配对码</h5>
        <p class="wt-hint">点「使用配对码配对设备」，把弹窗里的 IP 地址、端口和 6 位配对码填到下面，点「配对」（弹窗别关）。配对一次以后，下次只要打开无线调试再点「连接」（端口可以留空，圆桌自己找）。</p>
        <div class="wt-adb">
          <input data-adb-host placeholder="IP 地址，如 192.168.1.5" value="${esc(a.host || '')}" aria-label="IP 地址" inputmode="decimal">
          <input data-adb-port placeholder="端口" value="${esc(a.port || '')}" aria-label="端口" inputmode="numeric" maxlength="5">
          <input data-adb-code placeholder="6 位配对码" aria-label="配对码" inputmode="numeric" maxlength="6">
          <button type="button" class="btn" data-pair ${a.busy ? 'disabled' : ''}>配对</button>
          <button type="button" class="btn" data-connect ${a.busy ? 'disabled' : ''}>连接</button>
        </div>
        ${a.via === 'qr' ? '' : msg}
      </details>
      <details class="wt-phone">
        <summary>iPhone 怎么投</summary>
        <p class="wt-hint">苹果不让别的程序读 iPhone 屏幕。可以在这台电脑上装一个免费的 AirPlay 接收软件（例如 LetsView），iPhone 打开控制中心 →「屏幕镜像」→ 选这台电脑，再点上面的「刷新」，在列表里选镜像出来的那个窗口。iPhone 这一路不会自动认出支付页面，看到隐私内容时请点「安静看」或停止。</p>
      </details>`;
  }

  async adbDo(kind) {
    const q = (sel) => this.dlg.querySelector(sel)?.value.trim() || '';
    const host = q('[data-adb-host]');
    const port = q('[data-adb-port]');
    const code = q('[data-adb-code]');
    this.stopQr();
    this.adb = { open: true, host, port, busy: true, msg: kind === 'pair' ? '正在配对…' : '正在连接…' };
    this.render();
    try {
      if (kind === 'pair') {
        if (!host || !port || !/^\d{6}$/.test(code)) throw new Error('请填 IP 地址、端口和 6 位配对码。');
        const r = await this.api.adbPair(host, Number(port), code);
        this.adb.msg = r.connected.length ? `配对成功，已连上 ${r.connected.join('、')}。` : '配对成功。回到无线调试主页面，再点「连接」。';
      } else {
        // 连接时填的是无线调试主页面「IP 地址和端口」那一行；端口填错（如填成配对端口）或不填时自动找
        const r = await this.api.adbConnect(host, port ? Number(port) : 0);
        const moved = port && !r.connected.some((x) => x.endsWith(`:${port}`));
        this.adb.msg = `已连上 ${r.connected.join('、')}。${moved ? '（你填的端口连不上，已自动改用手机的连接端口）' : ''}`;
        if (moved) this.adb.port = r.connected[0].split(':').pop();
      }
    } catch (x) {
      this.adb.msg = x.message;
    }
    this.adb.busy = false;
    await this.loadSetup();
  }

  /** 方式一：生成配对二维码，等手机扫（每 2 秒问一次后端），扫了自动配对并连上。 */
  async qrStart() {
    this.stopQr();
    this.adb = { ...(this.adb || {}), open: true, via: 'qr', busy: true, msg: '正在准备 adb…' };
    this.render();
    try {
      const r = await this.api.adbQr();
      this.adb.qr = await qrSvg(r.payload);
      this.adb.msg = `手机上：无线调试 →「使用二维码配对设备」→ 扫上面这张图（${Math.round(r.expires_s / 60)} 分钟内有效）。`;
      this.qrTimer = setInterval(() => this.qrPoll(), QR_POLL_MS);
    } catch (x) {
      this.adb.msg = x.message;
    }
    this.adb.busy = false;
    this.render();
  }

  async qrPoll() {
    if (this.qrPolling) return;
    this.qrPolling = true;
    try {
      const r = await this.api.adbQrPoll();
      if (r.state === 'waiting') return;
      this.stopQr();
      this.adb.msg = {
        connected: `扫码配对成功，已连上 ${(r.connected || []).join('、')}。`,
        paired: '扫码配对成功。回到无线调试主页面，再点「连接」。',
        expired: '二维码过期了，再点一次「生成配对二维码」。',
      }[r.state] || '二维码已失效，再生成一张。';
      await this.loadSetup();
    } catch (x) {
      this.stopQr();
      this.adb.msg = x.message;
      this.render();
    } finally {
      this.qrPolling = false;
    }
  }

  stopQr() {
    clearInterval(this.qrTimer);
    this.qrTimer = null;
    if (this.adb) this.adb.qr = '';
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
    if (t.closest('[data-overlay]')) {
      try {
        const r = await this.api.overlay();
        this.note(r.already ? '悬浮窗已经开着了（在屏幕右上角附近）。' : '悬浮窗打开了：按住顶上那条拖动，「－」收起。');
      } catch (x) {
        this.note(x.message);
      }
      return;
    }
    if (t.closest('[data-pip]')) return this.openPip();
    if (t.closest('[data-start]')) return this.start();
    if (t.closest('[data-qr]')) return this.qrStart();
    if (t.closest('[data-qr-cancel]')) {
      this.stopQr();
      this.adb.msg = '';
      return this.render();
    }
    if (t.closest('[data-pair]')) return this.adbDo('pair');
    if (t.closest('[data-connect]')) return this.adbDo('connect');
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

  /** 投屏中的一句提示（不重画整个面板，免得打断输入）。 */
  note(text) {
    const live = this.dlg.querySelector('.wt-live');
    if (!live) return;
    let p = this.dlg.querySelector('.wt-note');
    if (!p) {
      p = document.createElement('p');
      p.className = 'wt-hint wt-note';
      p.setAttribute('role', 'status');
      live.after(p);
    }
    p.textContent = text;
  }

  /** 浏览器画中画小窗（Document Picture-in-Picture，Edge / Chrome 116+）：总在最前面；和悬浮窗同一个界面。 */
  async openPip() {
    if (this.pip) return this.pip.focus();
    let pip;
    try {
      pip = await window.documentPictureInPicture.requestWindow({ width: 360, height: 360 });
    } catch (x) {
      return this.note(`画中画没打开：${x.message}`);
    }
    this.pip = pip;
    pip.document.title = '一起看';
    const css = pip.document.createElement('link');
    css.rel = 'stylesheet';
    css.href = new URL('css/overlay.css', location.href).href;
    pip.document.head.append(css);
    pip.document.body.className = 'ov-page';
    const mount = pip.document.createElement('div');
    mount.id = 'ov';
    pip.document.body.append(mount);
    const host = {
      opacity: false, // 画中画窗口本身不能透明：滑块只调背景深浅
      setSmall: (small) => {
        try {
          pip.resizeTo(360, small ? 60 : 360);
        } catch {
          /* 有的浏览器不让改大小 */
        }
      },
      close: () => pip.close(),
    };
    this.pipView = await new OverlayView(mount, { host, doc: pip.document }).start();
    pip.addEventListener('pagehide', () => {
      this.pipView?.stop();
      this.pip = this.pipView = null;
    });
  }

  closePip() {
    const pip = this.pip;
    this.pipView?.stop();
    this.pip = this.pipView = null;
    pip?.close();
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
