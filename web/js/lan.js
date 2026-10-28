// 「平板」：让同一 Wi-Fi 下的平板 / 手机打开电脑上的圆桌（docs/LAN.md）。
// 电脑上：设访问口令、看平板该打开的地址、生成一次性二维码、让所有平板退出。
// 平板上：这个按钮变成「退出」。设置只能在电脑本机上做（服务端也会拒绝别的设备）。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

export class LanPanel {
  constructor(dlg, button, { api }) {
    this.dlg = dlg;
    this.button = button;
    this.api = api;
    this.info = null;
    this.qr = null;
    this.msg = '';
    this.remote = false;
    button.addEventListener('click', () => (this.remote ? this.logout() : this.open()));
    dlg.addEventListener('click', (e) => this.onClick(e));
    dlg.addEventListener('submit', (e) => this.onSubmit(e));
    this.detect();
  }

  // 这个页面是不是在平板上打开的：/api/lan 只回答电脑本机
  async detect() {
    try {
      this.info = await this.api.status();
    } catch (e) {
      if (e.status === 403) {
        this.remote = true;
        this.button.textContent = '退出';
        this.button.title = '这台平板退出圆桌（下次要重新输入口令或扫码）';
      }
    }
  }

  async logout() {
    try {
      await this.api.logout();
    } finally {
      location.assign('/login.html');
    }
  }

  async open() {
    this.msg = '';
    this.qr = null;
    try {
      this.info = await this.api.status();
    } catch (e) {
      this.msg = e.message;
    }
    this.render();
    if (!this.dlg.open) this.dlg.showModal();
  }

  render() {
    const i = this.info || {};
    const urls = i.urls || [];
    const state = !i.enabled
      ? '<p class="lan-state off">现在<b>只有这台电脑</b>能打开圆桌。要让平板用，请关掉当前的黑色服务窗口，改为双击项目里的 <code>start_lan.bat</code> 启动。</p>'
      : i.has_password
        ? '<p class="lan-state on">已对家里的局域网开放：平板在同一个 Wi-Fi 里打开下面的地址，输入口令或扫二维码即可。</p>'
        : '<p class="lan-state warn">已对局域网开放，但<b>还没设访问口令</b>：设好之前平板进不来。</p>';
    const addr = urls.length
      ? `<ul class="lan-urls">${urls
          .map(
            (u) =>
              `<li><code>${esc(u)}</code> <button type="button" class="btn" data-pair="${esc(u)}" ${i.has_password ? '' : 'disabled'}>生成二维码</button></li>`,
          )
          .join('')}</ul>`
      : '<p class="lan-hint">没找到这台电脑的局域网地址：确认电脑连着 Wi-Fi 或网线。</p>';
    const qr = this.qr
      ? `<div class="lan-qr"><div class="code">${this.qr.qr}</div><p>用平板相机扫一下，${esc(this.qr.minutes)} 分钟内有效、只能用一次。<br><code>${esc(this.qr.url)}</code></p></div>`
      : '';
    this.dlg.innerHTML = `
      <div class="vh"><b>平板：在家里的平板 / 手机上用圆桌</b><button type="button" class="btn" data-close>关闭</button></div>
      <div class="vb lan">
        ${state}
        <h4>1. 访问口令</h4>
        <form class="lan-pw" data-form="pw">
          <input type="password" name="pw" autocomplete="new-password" minlength="6" maxlength="128" required placeholder="${i.has_password ? '输入新口令可修改（至少 6 位）' : '至少 6 位'}">
          <button type="submit" class="btn">${i.has_password ? '修改口令' : '设置口令'}</button>
        </form>
        <p class="lan-hint">口令只在电脑上存加盐哈希；改口令会让所有平板退出。连续输错 5 次会暂时锁定那台设备。</p>
        <h4>2. 平板打开的地址</h4>
        ${addr}
        ${qr}
        <h4>3. 平板上「添加到主屏幕」</h4>
        <p class="lan-hint">iPad：Safari 打开后点「分享」→「添加到主屏幕」。安卓：Chrome 菜单 →「添加到主屏幕」。之后像 App 一样从桌面打开。</p>
        <h4>4. 平板打不开时：Windows 防火墙</h4>
        <p class="lan-hint">第一次用 <code>start_lan.bat</code> 启动时，Windows 会弹窗问是否允许 Python 访问网络：勾「专用网络」并允许。如果之前点了拒绝，在<b>管理员 PowerShell</b> 里运行下面这一条（只放行专用网络的这个端口）：</p>
        <pre class="lan-cmd">${esc(i.firewall || '')}</pre>
        <p class="lan-hint">另外家里的 Wi-Fi 在 Windows 里要是「专用网络」（设置 → 网络和 Internet → Wi-Fi → 网络配置文件类型）。学校 eduroam 等公共网络一般会隔离设备，平板连不到电脑。</p>
        <p class="lan-warn">家里局域网内是明文 HTTP：只在自己家等可信网络里用，不要在公共 Wi-Fi 下开放。</p>
        <p><button type="button" class="btn" data-signout ${i.has_password ? '' : 'disabled'}>让所有平板退出登录</button></p>
        <p class="lan-msg" role="status">${esc(this.msg)}</p>
      </div>`;
  }

  async onSubmit(e) {
    if (e.target.dataset.form !== 'pw') return;
    e.preventDefault();
    try {
      await this.api.setPassword(e.target.elements.pw.value);
      this.info = await this.api.status();
      this.msg = '口令已保存。';
    } catch (err) {
      this.msg = err.message;
    }
    this.render();
  }

  async onClick(e) {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.hasAttribute('data-close')) return this.dlg.close();
    try {
      if (t.dataset.pair) {
        this.qr = await this.api.pair(t.dataset.pair);
        this.msg = '';
      } else if (t.hasAttribute('data-signout')) {
        await this.api.signOut();
        this.msg = '所有平板都已退出，需要重新输入口令或扫码。';
      } else return;
    } catch (err) {
      this.msg = err.message;
    }
    this.render();
  }
}
