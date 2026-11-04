// 「游戏模式」（docs/GAMEMODE.md）：你玩游戏或别的程序在用显卡时，圆桌让出显卡。
// 自动识别（在跑游戏、前台全屏、显卡占用 / 显存）+ 这里的手动开关；显卡连续空闲几分钟后补跑排队的活。
// 按钮上的小点：亮着 = 正在让路。每 30 秒看一次状态（只读一个小接口，不调模型）。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const mins = (s) => Math.max(1, Math.round(Number(s || 0) / 60));
const when = (t) => new Date(Number(t || 0) * 1000).toLocaleString('zh-CN', { hour12: false }).slice(5, 16);

export class GameModePanel {
  constructor(dlg, button, { api }) {
    this.dlg = dlg;
    this.button = button;
    this.api = api;
    this.info = null;
    this.msg = '';
    button.addEventListener('click', () => this.open());
    dlg.addEventListener('click', (e) => this.onClick(e));
    this.refresh();
    setInterval(() => this.refresh(), 30000);
  }

  async refresh() {
    try {
      this.info = await this.api.status();
      this.msg = '';
    } catch (e) {
      this.msg = e.message;
    }
    this.mark();
    if (this.dlg.open) this.render();
  }

  mark() {
    const i = this.info || {};
    const on = Boolean(i.busy || (i.free_in_s > 0));
    this.button.classList.toggle('gm-on', on);
    this.button.title = i.busy
      ? `游戏模式：${(i.reasons || [])[0] || ''}（圆桌暂不用显卡）`
      : i.free_in_s > 0
        ? `显卡刚空下来，约 ${mins(i.free_in_s)} 分钟后圆桌再用`
        : '游戏模式：你玩游戏时圆桌自动让出显卡';
  }

  async open() {
    await this.refresh();
    this.render();
    if (!this.dlg.open) this.dlg.showModal();
  }

  render() {
    const i = this.info || {};
    const gpu = i.gpu || {};
    const state = i.busy
      ? `<p class="lan-state warn">正在让路：${esc((i.reasons || []).join('；'))}。用显卡的活（本地出图、本地配音）先停，云端聊天照常。</p>`
      : i.free_in_s > 0
        ? `<p class="lan-state warn">显卡刚空下来，再等约 ${mins(i.free_in_s)} 分钟（要连续空闲 ${esc(i.idle_minutes)} 分钟）圆桌才用。</p>`
        : '<p class="lan-state on">显卡空闲，圆桌可以用。</p>';
    const reading = gpu.unavailable
      ? `读不到显卡：${esc(gpu.unavailable)}（只看游戏和全屏）`
      : `显卡占用 ${gpu.util_pct == null ? '—' : `${esc(gpu.util_pct)}%`}${gpu.others_mb == null ? '' : `，别的程序占显存 ${esc(gpu.others_mb)} MB`}`;
    const queue = i.queue || [];
    const cpu = { low: '降到最低优先级', pause: '降到最低优先级，并停掉后台的生活 / 例会 / 管家', normal: '不管' }[i.cpu_when_busy] || '';
    this.dlg.innerHTML = `
      <div class="vh"><b>游戏模式：玩游戏时让出显卡</b><button type="button" class="btn" data-close>关闭</button></div>
      <div class="vb lan">
        ${state}
        <h4>手动开关</h4>
        <p><button type="button" class="btn ${i.manual ? 'primary' : ''}" data-manual="${i.manual ? '0' : '1'}">${i.manual ? '关掉手动游戏模式' : '现在就开游戏模式'}</button></p>
        <p class="lan-hint">${i.auto ? '自动识别开着：在跑游戏（关于你的游戏表、Steam / Epic 装的）、前台全屏、显卡被别的程序占用时自动让路。' : '自动识别关着（config/roundtable.yaml 的 gamemode.auto），只认手动开关。'}</p>
        <p class="lan-hint">${reading}。忙时圆桌自己的 CPU：${esc(cpu)}。</p>
        <h4>排队等显卡的活（${queue.length}）</h4>
        ${queue.length ? `<ul class="lan-urls">${queue.map((j) => `<li><span class="bk-when">${esc(when(j.added))}</span>${esc(j.label)}</li>`).join('')}</ul>` : '<p class="lan-hint">没有。</p>'}
        <p class="lan-msg" role="status">${esc(this.msg)}</p>
      </div>`;
  }

  async onClick(e) {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.hasAttribute('data-close')) return this.dlg.close();
    if (!t.dataset.manual) return;
    t.disabled = true;
    try {
      this.info = await this.api.set(t.dataset.manual === '1');
      this.msg = '';
    } catch (err) {
      this.msg = err.message;
    }
    this.mark();
    this.render();
  }
}
