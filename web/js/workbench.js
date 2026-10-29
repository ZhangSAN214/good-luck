// 「工作台」：圆桌在电脑上管着的 dsh 边车（docs/DSH.md）。顶栏一个按钮，运行中加一个绿点。
// 面板：启动 / 停止、本月花费与上限、白名单插件；运行中把 dsh 网页嵌在下面（只在电脑本机能看）。
// dsh 是工具，不是成员：这里不显示任何成员名、头像或人设；模型、渠道、key 名都来自接口，不写死。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const OPEN_MS = 4000;
const IDLE_MS = 30000;
const KIND = { panel: '网页面板', tool: '工具', skill: '技能', subagent: '子智能体', other: '其他' };
const MODE = { 'read-only': '只读', 'workspace-write': '只能写工作区', 'danger-full-access': '完全访问' };

/** 只接受 dsh 本机地址（http://127.0.0.1:端口/...），别的一律不嵌。 */
export function safeDshUrl(u) {
  return /^http:\/\/127\.0\.0\.1:\d{2,5}\//.test(u || '') ? u : '';
}

/** 本月花费的一句话。 */
export function budgetLine(b) {
  if (!b) return '';
  const spent = Number(b.spent_usd) || 0;
  const limit = Number(b.limit_usd) || 0;
  if (!b.tracking) return `本月上限 $${limit.toFixed(2)}；还没法统计花费（没有配置 key 或余额接口）。`;
  const bal = b.balance != null ? `，账户余额 ${Number(b.balance).toFixed(2)} ${esc(b.currency || '')}` : '';
  const err = b.error ? `（上次查余额失败：${esc(b.error)}）` : '';
  return `本月约 $${spent.toFixed(2)} / 上限 $${limit.toFixed(2)}${bal}${err}`;
}

export class Workbench {
  /**
   * @param {HTMLDialogElement} dlg
   * @param {HTMLButtonElement} button 顶栏按钮
   * @param {{api: Record<string, Function>}} opts
   */
  constructor(dlg, button, { api }) {
    Object.assign(this, { dlg, button, api });
    this.st = null;
    this.err = '';
    this.busy = false;
    this.timer = null;
    this.frameUrl = '';
    button.addEventListener('click', () => this.open());
    dlg.addEventListener('click', (e) => this.onClick(e));
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
    if (this.dlg.open && !this.busy) this.render();
  }

  paintButton() {
    const st = this.st || {};
    this.button.hidden = st.enabled === false;
    const on = st.state === 'running';
    this.button.classList.toggle('wb-on', on);
    this.button.title = on ? '工作台正在运行：点开' : '工作台：圆桌在电脑上管着的 dsh，装插件、写代码';
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
    this.render();
  }

  render() {
    const st = this.st || {};
    const running = st.state === 'running';
    const starting = st.state === 'starting' || st.state === 'stopping';
    const head = `<div class="vh"><b>工作台</b>
      <span class="wb-state ${running ? 'on' : ''}">${running ? '运行中' : starting ? '处理中…' : '已停止'}</span>
      <span class="wb-gap"></span>
      ${running ? `<button type="button" class="btn" data-check title="立刻查一次余额">刷新花费</button><button type="button" class="btn" data-stop>停止</button>` : `<button type="button" class="btn primary" data-start ${st.unavailable || this.busy || starting ? 'disabled' : ''}>${this.busy ? '启动中…' : '启动'}</button>`}
      <button type="button" class="btn" data-close>关闭</button></div>`;
    const notes = [];
    if (st.unavailable) notes.push(`<p class="wb-warn">${esc(st.unavailable)}</p>`);
    if (st.stopped_for_budget) notes.push(`<p class="wb-warn" role="alert">${esc(st.stopped_for_budget)}</p>`);
    else if (st.last_exit && !running) notes.push(`<p class="wb-hint">上次停止：${esc(st.last_exit)}</p>`);
    if (this.err) notes.push(`<p class="wb-warn" role="alert">${esc(this.err)}</p>`);
    if (st.installed && !st.key_present && st.key_env)
      notes.push(`<p class="wb-hint">没有找到 ${esc(st.key_env)}：工作台能打开，但调不了模型。把它写进项目根目录的 .env 后重启圆桌。</p>`);
    if (running && st.local_only)
      notes.push('<p class="wb-hint">工作台只能在电脑本机打开（dsh 只监听本机）；在电脑上的圆桌里点「工作台」即可。</p>');
    const plugins = (st.plugins || [])
      .map((p) => `<li><b>${esc(p.name)}</b> ${esc(p.version)} · ${esc(KIND[p.kind] || p.kind)} · ${esc(p.license)}${p.enabled ? '' : ' · 未启用'}<span>${esc(p.note)}</span></li>`)
      .join('');
    const info = `<div class="wb-info">
        <p>${budgetLine(st.budget)}</p>
        <p class="wb-hint">dsh ${esc(st.runtime || '')} · 文件权限：${esc(MODE[st.permission_mode] || st.permission_mode || '')}。dsh 是工具，不是成员；它的数据只在电脑的 data/dsh/ 里，会话日志上传和遥测已强制关闭。</p>
        ${plugins ? `<details><summary>白名单插件（${(st.plugins || []).length}）</summary><ul class="wb-plugins">${plugins}</ul></details>` : ''}
      </div>`;
    const url = running ? safeDshUrl(st.url) : '';
    this.dlg.innerHTML = `${head}<div class="vb wb">${notes.join('')}${info}<div class="wb-frame-box"></div>${
      st.log && st.log.length && !running ? `<details class="wb-log"><summary>dsh 输出（最后几行）</summary><pre>${esc(st.log.slice(-15).join('\n'))}</pre></details>` : ''
    }</div>`;
    this.paintFrame(url);
  }

  paintFrame(url) {
    const box = this.dlg.querySelector('.wb-frame-box');
    if (!box) return;
    if (!url) {
      this.frameUrl = '';
      box.hidden = true;
      return;
    }
    box.hidden = false;
    if (url !== this.frameUrl || !box.querySelector('iframe')) {
      this.frameUrl = url;
      box.innerHTML = `<div class="wb-bar"><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">在新窗口打开</a></div><iframe class="wb-frame" title="工作台" src="${esc(url)}" referrerpolicy="no-referrer"></iframe>`;
    }
  }

  async onClick(e) {
    const t = e.target;
    if (t.closest('[data-close]')) return this.dlg.close();
    if (t.closest('[data-start]')) return this.act('start');
    if (t.closest('[data-stop]')) return this.act('stop');
    if (t.closest('[data-check]')) return this.act('check');
  }

  async act(what) {
    this.err = '';
    this.busy = what === 'start';
    this.render();
    try {
      this.st = await this.api[what]();
    } catch (x) {
      this.err = x.message;
    } finally {
      this.busy = false;
    }
    this.paintButton();
    this.render();
  }
}
