// 终端公告栏：版本更新说明（GET /api/changelog，数据在 config/changelog.yaml），新的在最上面。
// 没看过的版本带「新」标记；展开一条就算看过，也可以「全部标为已读」。已读记录只存在这台电脑的浏览器里。
// 只读，不调用模型、不花钱。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const KEY = 'roundtable.notice.read'; // 看过的版本号
const SHOWN = 5; // 默认列出最近几条，更早的折叠起来

/** 还没看过的版本（保持新的在前）。 */
export function unreadVersions(releases, read) {
  const seen = new Set(read || []);
  return (releases || []).map((r) => r.version).filter((v) => !seen.has(v));
}

const ICON = {
  horn: '<path d="M4 10v4h3l6 4V6L7 10zM16.5 9a4 4 0 0 1 0 6M19 7a7 7 0 0 1 0 10"/>',
};
const icon = (k) => `<svg class="nt-i" viewBox="0 0 24 24" aria-hidden="true">${ICON[k]}</svg>`;

export class Notice {
  /** @param {{load: () => Promise<{latest:string, releases:Array}>}} opts */
  constructor({ load }) {
    this.loadFn = load;
    this.el = document.createElement('div');
    this.el.className = 'nt';
    this.data = null;
    this.error = '';
    this.open = false;
    this.more = false;
    this.read = this.loadRead();
    this.fresh = new Set(); // 这次打开面板前还没看过的：面板开着时「新」标记先留着
    this.expanded = new Set(); // 展开着的版本：重画时保持
    this.el.addEventListener('click', (e) => this.onClick(e));
    this.el.addEventListener(
      'toggle',
      (e) => {
        if (!this.open || !e.target.matches?.('details[data-v]')) return;
        const v = e.target.dataset.v;
        if (e.target.open) {
          this.expanded.add(v);
          this.markRead([v]);
        } else this.expanded.delete(v);
      },
      true,
    );
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.open) {
        e.stopPropagation();
        this.toggle(false);
      }
    });
    document.addEventListener('pointerdown', (e) => {
      if (this.open && !this.el.contains(e.target)) this.toggle(false, false);
    });
    this.paint();
    this.ready = this.refresh();
  }

  loadRead() {
    try {
      const v = JSON.parse(localStorage.getItem(KEY) || '[]');
      return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : [];
    } catch {
      return [];
    }
  }

  markRead(versions) {
    const add = versions.filter((v) => !this.read.includes(v));
    if (!add.length) return;
    this.read = [...this.read, ...add];
    try {
      localStorage.setItem(KEY, JSON.stringify(this.read));
    } catch {
      // 存不了（隐私模式）就只在这次生效
    }
    this.paintButton();
  }

  async refresh() {
    const before = JSON.stringify([this.data, this.error]);
    try {
      this.data = await this.loadFn();
      this.error = '';
    } catch (err) {
      this.error = `公告读不出来：${err?.message || err}`;
    }
    if (JSON.stringify([this.data, this.error]) !== before) this.paint(); // 没变就不重画，展开的条目不收起
  }

  /** 挂到终端首页顶栏的空位上（首页每次重画都会再挂一次，状态不丢）。 */
  mount(slot) {
    if (slot) slot.replaceWith(this.el);
  }

  unread() {
    return unreadVersions(this.data?.releases, this.read);
  }

  paintButton() {
    const n = this.unread().length;
    const btn = this.el.querySelector('.nt-btn');
    if (!btn) return;
    btn.classList.toggle('has-new', n > 0);
    btn.setAttribute('aria-label', n ? `公告：${n} 条新的版本更新` : '公告：版本更新说明');
    const dot = btn.querySelector('.nt-dot');
    if (dot) {
      dot.hidden = !n;
      dot.textContent = n > 9 ? '9+' : String(n);
    }
  }

  release(r) {
    const isNew = this.fresh.has(r.version);
    const list = (label, items) =>
      items?.length ? `<p class="nt-k">${label}</p><ul>${items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
    return `<details class="nt-rel" data-v="${esc(r.version)}"${this.expanded.has(r.version) ? ' open' : ''}>
      <summary><span class="nt-ver">v${esc(r.version)}</span><span class="nt-ttl">${esc(r.title)}</span>${
        isNew ? '<span class="nt-new">新</span>' : ''
      }<span class="nt-date">${esc(r.date)}</span></summary>
      <div class="nt-body">${list('新增', r.added)}${list('修复', r.fixed)}${
        r.howto ? `<p class="nt-k">怎么用</p><p class="nt-how">${esc(r.howto)}</p>` : ''
      }<p class="nt-pr">${esc(r.date)}${r.pr?.length ? ` · 对应 ${r.pr.map((n) => `#${n}`).join('、')}` : ''}</p></div>
    </details>`;
  }

  paint() {
    const rels = this.data?.releases || [];
    const shown = this.more ? rels : rels.slice(0, SHOWN);
    const hidden = rels.length - shown.length;
    this.el.innerHTML = `
      <button type="button" class="nt-btn" aria-expanded="${this.open}">${icon('horn')}<span>公告</span><span class="nt-dot" hidden></span></button>
      <div class="nt-panel" role="dialog" aria-label="公告栏：版本更新说明"${this.open ? '' : ' hidden'}>
        <div class="nt-head"><b class="nt-title">公告栏</b><span class="nt-sub">版本更新说明</span>
          <button type="button" class="hb-chip" data-allread${this.unread().length ? '' : ' disabled'}>全部标为已读</button></div>
        ${this.error ? `<p class="hb-hint hb-err" role="alert">${esc(this.error)}</p>` : ''}
        ${!this.data && !this.error ? '<p class="hb-hint">正在读取…</p>' : ''}
        <div class="nt-list">${shown.map((r) => this.release(r)).join('')}</div>
        ${hidden > 0 ? `<button type="button" class="hb-chip nt-more" data-more>更早的更新（${hidden} 条）</button>` : ''}
        ${this.more && rels.length > SHOWN ? '<button type="button" class="hb-chip nt-more" data-less>收起更早的</button>' : ''}
      </div>`;
    this.paintButton();
    // 默认展开的最新一条在面板打开时算看过
    if (this.open && rels[0] && this.expanded.has(rels[0].version)) this.markRead([rels[0].version]);
  }

  toggle(open, refocus = true) {
    if (open) {
      this.fresh = new Set(this.unread());
      const latest = this.data?.releases?.[0]?.version;
      this.expanded = new Set(latest ? [latest] : []);
    }
    this.open = open;
    if (!open) this.more = false;
    this.paint();
    if (open) {
      this.el.querySelector('.nt-panel')?.focus?.();
      this.el.querySelector('.nt-panel summary')?.focus();
      this.refresh(); // 公告文件可能刚更新，打开时再读一次
    } else if (refocus) this.el.querySelector('.nt-btn')?.focus();
  }

  onClick(e) {
    if (e.target.closest('.nt-btn')) return this.toggle(!this.open);
    if (e.target.closest('[data-allread]')) {
      this.markRead((this.data?.releases || []).map((r) => r.version));
      this.fresh.clear();
      return this.paint();
    }
    if (e.target.closest('[data-more]')) {
      this.more = true;
      return this.paint();
    }
    if (e.target.closest('[data-less]')) {
      this.more = false;
      return this.paint();
    }
  }
}
