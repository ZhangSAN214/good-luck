// 「出问题点这里」：只读自检（docs/FIRSTAID.md）。四类现场一个入口：起不来、卡住 / 没反应、
// 想撤销刚才的更新、数据不见了。每项红黄绿 + 一句话怎么办；「体检报告」是一段纯文本，
// 不含聊天内容和 key，可以整段复制发给我们。这里不改任何东西，真要恢复去「备份」里做。
// 只能在电脑本机上用（服务端也会拒绝平板）。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const MARK = { ok: '正常', info: '提示', warn: '留意', bad: '问题' };

export class FirstAidPanel {
  constructor(dlg, button, { api }) {
    this.dlg = dlg;
    this.api = api;
    this.info = null;
    this.scene = '';
    this.report = '';
    this.msg = '';
    button.addEventListener('click', () => this.open());
    dlg.addEventListener('click', (e) => this.onClick(e));
  }

  async open() {
    this.scene = '';
    this.report = '';
    this.msg = '正在检查…';
    this.info = null;
    this.render();
    if (!this.dlg.open) this.dlg.showModal();
    await this.load();
  }

  async load() {
    try {
      this.info = await this.api.check();
      this.msg = '';
    } catch (e) {
      this.msg = e.message;
    }
    this.render();
  }

  item(f) {
    const fix = f.fix && (f.level === 'warn' || f.level === 'bad') ? `<p class="fa-fix">怎么办：${esc(f.fix)}</p>` : '';
    return `<li class="fa-item ${esc(f.level)}"><span class="fa-dot" aria-label="${esc(MARK[f.level])}"></span>
      <div><b>${esc(f.title)}</b>${f.detail ? `<span class="fa-detail">${esc(f.detail)}</span>` : ''}${fix}</div></li>`;
  }

  render() {
    const i = this.info;
    const scenes = (i?.scenes || [])
      .map(
        (s) =>
          `<button type="button" class="fa-scene ${esc(s.level)}${this.scene === s.id ? ' on' : ''}" data-scene="${esc(s.id)}" aria-pressed="${this.scene === s.id}">${esc(s.name)}</button>`,
      )
      .join('');
    const items = (i?.findings || []).filter((f) => !this.scene || f.scene === this.scene);
    const report = this.report
      ? `<textarea class="fa-report" readonly rows="12" aria-label="体检报告">${esc(this.report)}</textarea>`
      : '';
    this.dlg.innerHTML = `
      <div class="vh"><b>出问题点这里：自检</b><button type="button" class="btn" data-close>关闭</button></div>
      <div class="vb lan fa">
        ${i ? `<p class="lan-state fa-head ${esc(i.level)}">${esc(i.headline)}</p>` : ''}
        ${i ? `<div class="fa-scenes" role="group" aria-label="按现场看">${scenes}</div>` : ''}
        ${i ? `<ul class="fa-list">${items.map((f) => this.item(f)).join('') || '<li class="lan-hint">这一类没有发现问题。</li>'}</ul>` : ''}
        <div class="fa-acts">
          <button type="button" class="btn" data-recheck>重新检查</button>
          <button type="button" class="btn" data-report>生成体检报告</button>
          ${this.report ? '<button type="button" class="btn" data-copy>复制报告</button><button type="button" class="btn" data-save>存成文件</button>' : ''}
        </div>
        ${report}
        <p class="lan-hint">只读检查，不改任何东西。报告只有检查结果，不含聊天内容、记忆和 key，可以整段发给我们。要恢复数据请到「备份」。圆桌打不开时，在项目目录运行 <code>roundtable doctor</code>。</p>
        <p class="lan-msg" role="status">${esc(this.msg)}</p>
      </div>`;
  }

  async onClick(e) {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.hasAttribute('data-close')) return this.dlg.close();
    if (t.dataset.scene !== undefined) {
      this.scene = this.scene === t.dataset.scene ? '' : t.dataset.scene;
      return this.render();
    }
    if (t.hasAttribute('data-recheck')) {
      this.msg = '正在检查…';
      this.render();
      return this.load();
    }
    if (t.hasAttribute('data-report')) {
      try {
        this.report = (await this.api.report()).text;
        this.msg = '';
      } catch (err) {
        this.msg = err.message;
      }
      return this.render();
    }
    if (t.hasAttribute('data-copy')) {
      try {
        await navigator.clipboard.writeText(this.report);
        this.msg = '已复制，可以直接粘贴发给我们。';
      } catch {
        const area = this.dlg.querySelector('.fa-report');
        area?.select();
        this.msg = '浏览器不让自动复制：报告已选中，按 Ctrl+C 复制。';
      }
      const box = this.dlg.querySelector('[role=status]');
      if (box) box.textContent = this.msg;
      return;
    }
    if (t.hasAttribute('data-save')) {
      const url = URL.createObjectURL(new Blob([this.report], { type: 'text/plain;charset=utf-8' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `圆桌体检报告-${new Date().toISOString().slice(0, 10)}.txt`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  }
}
