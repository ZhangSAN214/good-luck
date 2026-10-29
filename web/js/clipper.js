// 资料浏览器（办公室）：搜索、打开网页看正文、把全文或选中的段落存进资料夹，提问时附上。
// 网页由后端只读抓取并提取正文（不花钱）；搜索走联网搜索（有花费）。资料只存在这台电脑上。
import { tzOpt } from './hometz.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const BACK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>';
// 像网址：带协议，或「xxx.yy/…」且没有空格
const URLISH = /^(https?:\/\/\S+|[\w-]+(\.[\w-]+)+(:\d+)?(\/\S*)?)$/i;
const when = (iso) => (iso ? new Date(iso).toLocaleString('zh-CN', tzOpt({ hour12: false, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })) : '');

export class Clipper {
  constructor(dialog, { api, attach } = {}) {
    this.dlg = dialog;
    this.api = api; // {list, open(url), search(q), save(clip), text(id), del(id), attach(id)}
    this.attach = attach; // (附件信息) => void：放进提问区的附件里
    this.data = { clips: [] };
    this.tab = 'web'; // web | saved
    this.page = null; // 当前打开的网页
    this.hits = null; // 搜索结果
    this.history = []; // 返回用：之前的 {page, hits}
    this.viewing = null; // 资料夹里正在看的那份
    this.input = '';
    this.sel = ''; // 最近一次在正文里选中的文字
    this.msg = '';
    this.busy = false;
    this.dlg.addEventListener('click', (e) => this.onClick(e));
    this.dlg.addEventListener('submit', (e) => this.onSubmit(e));
    // 按「存选中」时别让按下鼠标 / 手指把选区清掉
    this.dlg.addEventListener('pointerdown', (e) => {
      if (e.target.closest('[data-save-sel]')) e.preventDefault();
      else if (e.target.closest('#cl-read')) this.sel = ''; // 在正文里点一下 = 重新选
    });
    document.addEventListener('selectionchange', () => this.onSelection());
  }

  async open() {
    if (!this.dlg.open) this.dlg.showModal();
    this.msg = '';
    try {
      this.data = await this.api.list();
    } catch (e) {
      this.msg = e.message;
    }
    this.render();
    if (this.tab === 'web' && !this.page) this.dlg.querySelector('#cl-q')?.focus();
  }

  close() {
    this.dlg.close();
  }

  say(msg) {
    this.msg = msg;
    const el = this.dlg.querySelector('.cl-msg');
    if (el) el.textContent = msg;
  }

  // --- 渲染 ---------------------------------------------------------------------

  render() {
    const d = this.data || { clips: [] };
    const n = d.clips?.length || 0;
    const disabled = d.enabled === false;
    this.dlg.innerHTML = `
      <div class="vh sheet-bar"><button type="button" class="sheet-icon" data-close aria-label="关闭">${BACK}</button><b>资料</b><span></span></div>
      <div class="vb cl">
        <div class="seg cl-tabs">
          <button type="button" data-tab="web" aria-pressed="${this.tab === 'web'}">浏览</button>
          <button type="button" data-tab="saved" aria-pressed="${this.tab === 'saved'}">资料夹${n ? ` · ${n}` : ''}</button>
        </div>
        ${disabled ? '<p class="hint">资料浏览器已在配置里关闭。</p>' : this.tab === 'web' ? this.webHTML() : this.savedHTML()}
        <p class="hint cl-msg" role="status">${esc(this.msg)}</p>
      </div>`;
    this.onSelection();
  }

  webHTML() {
    const d = this.data;
    const searchNote = d.search ? `搜索每次约 $${Number(d.search_estimate_usd || 0).toFixed(2)}` : '联网搜索现在不可用，只能输入网址';
    const bar = `
      <form class="cl-bar" id="cl-go" autocomplete="off">
        ${this.history.length ? '<button type="button" class="btn" data-back aria-label="返回上一页">返回</button>' : ''}
        <input id="cl-q" type="search" maxlength="2000" value="${esc(this.input)}" placeholder="输入网址打开，或输入关键词搜索" aria-label="网址或搜索词" enterkeyhint="go">
        <button class="btn primary" type="submit"${this.busy ? ' disabled' : ''}>${this.busy ? '…' : '前往'}</button>
      </form>
      <p class="hint cl-sub">打开网页不花钱，只取正文；${esc(searchNote)}。要登录才能看的页面读不到。</p>`;
    if (this.page) return bar + this.pageHTML(this.page);
    if (this.hits) return bar + this.hitsHTML(this.hits);
    return bar + '<p class="hint cl-empty">看到有用的段落，选中后点「存选中」；整篇有用就点「存全文」。存下的资料在「资料夹」里，提问时点「附上」交给成员。</p>';
  }

  hitsHTML(r) {
    if (!r.hits.length) return '<p class="hint">没有搜到结果，换个说法试试。</p>';
    return `<ul class="cl-hits">${r.hits
      .map(
        (h, i) => `<li><button type="button" class="cl-hit" data-hit="${i}"><b>${esc(h.title)}</b><small>${esc(h.url)}</small><span>${esc(h.snippet)}</span></button></li>`,
      )
      .join('')}</ul>`;
  }

  pageHTML(p) {
    const body = p.blocks.length
      ? p.blocks
          .map((b) => (b.startsWith('## ') ? `<h4>${esc(b.slice(3))}</h4>` : `<p>${esc(b).replace(/\n/g, '<br>')}</p>`))
          .join('')
      : `<p class="hint">${esc(p.hint || '没提取到正文')}</p>`;
    const links = p.links?.length
      ? `<details class="cl-links"><summary>正文里的链接（${p.links.length}）</summary><ul>${p.links
          .map((l, i) => `<li><button type="button" class="link" data-link="${i}">${esc(l.text)}</button></li>`)
          .join('')}</ul></details>`
      : '';
    return `
      <div class="cl-page">
        <div class="cl-head"><b class="cl-title">${esc(p.title)}</b><small><a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">${esc(p.site)} ↗</a> · 约 ${p.chars} 字</small></div>
        <div class="cl-acts">
          <button type="button" class="btn primary" data-save-all${p.blocks.length ? '' : ' disabled'}>存全文</button>
          <button type="button" class="btn" data-save-sel disabled title="先在下面选中一段文字">存选中</button>
        </div>
        <article class="cl-read" id="cl-read">${body}</article>
        ${links}
      </div>`;
  }

  savedHTML() {
    const clips = this.data.clips || [];
    if (this.viewing) {
      const c = this.viewing;
      return `
        <div class="cl-page">
          <div class="cl-head"><b class="cl-title">${esc(c.title)}</b><small><a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.site)} ↗</a> · ${c.chars} 字 · ${esc(when(c.saved_at))}</small></div>
          <div class="cl-acts"><button type="button" class="btn" data-unview>返回资料夹</button><button type="button" class="btn primary" data-attach="${esc(c.id)}">附上到题目</button></div>
          <pre class="cl-read cl-raw">${esc(c.text)}</pre>
        </div>`;
    }
    if (!clips.length) return '<p class="hint cl-empty">资料夹是空的。在「浏览」里打开网页，存全文或选中的段落。</p>';
    return `<ul class="cl-list grp-box">${clips
      .map(
        (c) => `<li class="grp-row" data-id="${esc(c.id)}"><span class="fd-name"><b>${esc(c.title)}</b><small>${esc(c.site)} · ${c.chars} 字${c.partial ? ' · 选段' : ''} · ${esc(when(c.saved_at))}</small><small class="cl-ex">${esc(c.excerpt)}</small></span>
          <span class="cl-row-acts"><button type="button" class="btn primary" data-attach="${esc(c.id)}">附上</button><button type="button" class="link" data-view="${esc(c.id)}">查看</button><button type="button" class="link" data-del="${esc(c.id)}">删除</button></span></li>`,
      )
      .join('')}</ul>
      <p class="grp-foot">资料只存在这台电脑上。「附上」= 把这份资料作为附件放进办公室的提问区，讨论、头脑风暴、协同都能用。</p>`;
  }

  // --- 选中文字 -------------------------------------------------------------------

  selected() {
    const read = this.dlg.querySelector('#cl-read');
    const sel = document.getSelection();
    if (!read || !sel || sel.isCollapsed || !sel.rangeCount) return '';
    const range = sel.getRangeAt(0);
    if (!read.contains(range.commonAncestorContainer)) return '';
    return sel.toString().trim();
  }

  onSelection() {
    const btn = this.dlg.querySelector('[data-save-sel]');
    if (!btn) return;
    const now = this.selected();
    if (now) this.sel = now;
    else if (!document.getSelection()?.isCollapsed) this.sel = ''; // 选到了正文外面
    const text = this.sel;
    btn.disabled = !text;
    btn.textContent = text ? `存选中（${text.length} 字）` : '存选中';
  }

  // --- 动作 ---------------------------------------------------------------------

  async go(raw) {
    const q = String(raw || '').trim();
    if (!q || this.busy) return;
    this.input = q;
    if (URLISH.test(q)) return this.openUrl(q);
    this.busy = true;
    this.say('正在搜索…');
    this.render();
    try {
      const r = await this.api.search(q);
      this.pushHistory();
      this.hits = r;
      this.page = null;
      this.msg = r.cost_usd ? `搜索花费 $${Number(r.cost_usd).toFixed(3)}` : '';
    } catch (e) {
      this.msg = e.message || '搜索失败';
    }
    this.busy = false;
    this.render();
  }

  async openUrl(url) {
    this.busy = true;
    this.say('正在打开…');
    this.render();
    try {
      const page = await this.api.open(url);
      this.pushHistory();
      this.page = page;
      this.sel = '';
      this.input = page.url;
      this.msg = '';
    } catch (e) {
      this.msg = e.message || '打不开这个网页';
    }
    this.busy = false;
    this.render();
    this.dlg.querySelector('.vb')?.scrollTo?.({ top: 0 });
  }

  pushHistory() {
    if (this.page || this.hits) this.history.push({ page: this.page, hits: this.hits, input: this.input });
    if (this.history.length > 20) this.history.shift();
  }

  back() {
    const prev = this.history.pop();
    if (!prev) return;
    Object.assign(this, { page: prev.page, hits: prev.hits, input: prev.input, msg: '' });
    this.render();
  }

  async save(partial) {
    const p = this.page;
    if (!p) return;
    const text = partial ? this.selected() || this.sel : p.blocks.join('\n\n');
    if (!text) return;
    try {
      const clip = await this.api.save({ url: p.url, title: p.title, text, partial });
      this.data.clips = [clip, ...(this.data.clips || [])];
      this.msg = `存好了（${clip.chars} 字${clip.truncated ? '，太长截断了' : ''}），在「资料夹」里`;
      this.sel = '';
      document.getSelection()?.removeAllRanges();
    } catch (e) {
      this.msg = e.message || '没存上';
    }
    this.render();
  }

  async attachClip(id) {
    try {
      const att = await this.api.attach(id);
      this.attach?.(att);
      this.say(`已附上「${att.name}」到办公室的提问区`);
    } catch (e) {
      this.say(e.message || '附上失败');
    }
  }

  async onClick(e) {
    const t = e.target;
    if (t.closest('[data-close]')) return this.close();
    const tab = t.closest('[data-tab]');
    if (tab) {
      this.tab = tab.dataset.tab;
      this.viewing = null;
      this.msg = '';
      return this.render();
    }
    if (t.closest('[data-back]')) return this.back();
    const hit = t.closest('[data-hit]');
    if (hit) return this.openUrl(this.hits.hits[Number(hit.dataset.hit)].url);
    const link = t.closest('[data-link]');
    if (link) return this.openUrl(this.page.links[Number(link.dataset.link)].url);
    if (t.closest('[data-save-all]')) return this.save(false);
    if (t.closest('[data-save-sel]')) return this.save(true);
    const att = t.closest('[data-attach]');
    if (att) return this.attachClip(att.dataset.attach);
    const view = t.closest('[data-view]');
    if (view) {
      try {
        this.viewing = await this.api.text(view.dataset.view);
      } catch (err) {
        this.msg = err.message;
      }
      return this.render();
    }
    if (t.closest('[data-unview]')) {
      this.viewing = null;
      return this.render();
    }
    const del = t.closest('[data-del]');
    if (del) {
      try {
        this.data = await this.api.del(del.dataset.del);
        this.msg = '删掉了';
      } catch (err) {
        this.msg = err.message;
      }
      this.render();
    }
  }

  onSubmit(e) {
    if (e.target.id !== 'cl-go') return;
    e.preventDefault();
    this.go(this.dlg.querySelector('#cl-q')?.value);
  }
}
