// 「备份」：整库备份（docs/BACKUP.md）。成员的记忆、关系、日记、成长和聊天记录都在一个数据库文件里，
// 每天自动备份一份，另有存档点（更新前、恢复前自动存，也能在这里手动存）。
// 恢复会用备份覆盖现在的全部数据：先在面板里再确认一次，服务端会先存「恢复前」存档点；恢复后要重启圆桌。
// 只能在电脑本机上用（服务端也会拒绝平板）。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

// update_and_start.bat 存的标签是英文（批处理里不写中文），界面上显示成中文
const LABEL = { update: '更新前' };
const mb = (n) => `${(Number(n || 0) / 1048576).toFixed(1)} MB`;
const when = (iso) => String(iso || '').replace('T', ' ').slice(0, 16);

export class BackupPanel {
  constructor(dlg, button, { api }) {
    this.dlg = dlg;
    this.api = api;
    this.info = null;
    this.msg = '';
    this.confirm = '';
    this.done = null;
    button.addEventListener('click', () => this.open());
    dlg.addEventListener('click', (e) => this.onClick(e));
    dlg.addEventListener('submit', (e) => this.onSubmit(e));
  }

  async open() {
    this.msg = '';
    this.confirm = '';
    this.done = null;
    await this.load();
    if (!this.dlg.open) this.dlg.showModal();
  }

  async load() {
    try {
      this.info = await this.api.status();
    } catch (e) {
      this.msg = e.message;
    }
    this.render();
  }

  row(f) {
    const kind = f.kind === 'daily' ? '每天' : `存档点 · ${esc(LABEL[f.label] || f.label)}`;
    const ask = this.confirm === f.name;
    const act = ask
      ? `<span class="bk-ask">用这份覆盖现在的全部数据？</span>
         <button type="button" class="btn danger" data-restore="${esc(f.name)}">确定恢复</button>
         <button type="button" class="btn" data-cancel>取消</button>`
      : `<button type="button" class="btn" data-ask="${esc(f.name)}">恢复这份</button>`;
    return `<li><span class="lan-tag">${kind}</span><span class="bk-when">${esc(when(f.at))}</span><span class="bk-size">${mb(f.size)}</span>${act}</li>`;
  }

  render() {
    const i = this.info || {};
    const files = i.files || [];
    const state = !i.folder
      ? '<p class="lan-state warn">现在用的是临时数据库，不备份。</p>'
      : i.enabled
        ? `<p class="lan-state on">每天第一次开着时自动备份一份，留 ${esc(i.keep_daily)} 份；存档点留 ${esc(i.keep_checkpoints)} 份。${i.latest ? `最近一份：${esc(when(i.latest.at))}。` : '还没有备份。'}</p>`
        : '<p class="lan-state warn">每天自动备份已关（config/roundtable.yaml 的 backup.enabled），仍可手动存档点和恢复。</p>';
    const done = this.done
      ? `<p class="lan-state warn">已恢复 ${esc(when(this.done.restored.at))} 那份。<b>请关掉圆桌再打开</b>，让成员重新读取。反悔的话恢复「存档点 · 恢复前」那份即可。</p>`
      : '';
    this.dlg.innerHTML = `
      <div class="vh"><b>备份：成员的记忆、关系、成长和聊天记录</b><button type="button" class="btn" data-close>关闭</button></div>
      <div class="vb lan">
        ${state}
        ${done}
        <h4>存一个存档点</h4>
        <form class="lan-pw" data-form="point">
          <input name="label" maxlength="24" placeholder="标签（可不填），如：试跑前">
          <button type="submit" class="btn" ${i.folder ? '' : 'disabled'}>现在存</button>
        </form>
        <p class="lan-hint">更新圆桌（update_and_start.bat）和恢复之前会自动存。存档点只在这台电脑上，不上传。</p>
        <h4>备份列表</h4>
        ${files.length ? `<ul class="lan-urls bk-list">${files.map((f) => this.row(f)).join('')}</ul>` : '<p class="lan-hint">还没有备份。</p>'}
        <p class="lan-hint">放在 <code>${esc(i.folder || '')}</code>。只含数据库；上传的图片、语音等文件不在里面，见 docs/BACKUP.md。</p>
        <p class="lan-msg" role="status">${esc(this.msg)}</p>
      </div>`;
  }

  async onSubmit(e) {
    if (e.target.dataset.form !== 'point') return;
    e.preventDefault();
    try {
      const got = await this.api.checkpoint(e.target.elements.label.value || '手动');
      this.info = got;
      this.msg = `已存：${got.saved.name}`;
    } catch (err) {
      this.msg = err.message;
    }
    this.render();
  }

  async onClick(e) {
    const t = e.target.closest('button');
    if (!t) return;
    if (t.hasAttribute('data-close')) return this.dlg.close();
    if (t.dataset.ask) {
      this.confirm = t.dataset.ask;
      return this.render();
    }
    if (t.hasAttribute('data-cancel')) {
      this.confirm = '';
      return this.render();
    }
    if (!t.dataset.restore) return;
    t.disabled = true;
    try {
      const got = await this.api.restore(t.dataset.restore);
      this.info = got;
      this.done = got;
      this.msg = '';
    } catch (err) {
      this.msg = err.message;
    }
    this.confirm = '';
    this.render();
  }
}
