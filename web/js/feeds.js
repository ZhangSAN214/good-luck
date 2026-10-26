// 上网：成员读哪些公开订阅源（RSS / Atom）。只读、不登录；抓取不花钱，分享和搜索计入生活每日上限。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const KIND = { news: '新闻', forum: '论坛', blog: '博客' };
const BACK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>';
const list = (s) =>
  String(s || '')
    .split(/[,，、\n]/)
    .map((x) => x.trim())
    .filter(Boolean);

export class Feeds {
  constructor(dialog, { api } = {}) {
    this.dlg = dialog;
    this.api = api; // {get(), set(feeds|null), refresh()}
    this.data = null;
    this.msg = '';
    this.dlg.addEventListener('click', (e) => this.onClick(e));
    this.dlg.addEventListener('submit', (e) => this.onSubmit(e));
    this.dlg.addEventListener('change', (e) => this.onChange(e));
  }

  async open() {
    if (!this.dlg.open) this.dlg.showModal();
    this.msg = '';
    await this.load();
  }

  close() {
    this.dlg.close();
  }

  async load() {
    try {
      this.data = await this.api.get();
    } catch (e) {
      this.msg = e.message;
    }
    this.render();
  }

  feeds() {
    return (this.data?.feeds || []).map((f) => ({ ...f }));
  }

  render() {
    const d = this.data || { feeds: [] };
    const when = d.last_refresh ? new Date(d.last_refresh).toLocaleString('zh-CN', { hour12: false }) : '还没抓过';
    const rows = d.feeds.length
      ? d.feeds
          .map(
            (f, i) => `<li data-i="${i}" class="grp-row"><span class="fd-name"><b>${esc(f.name)}</b><small>${esc(KIND[f.kind] || f.kind)}${f.topics?.length ? ` · ${esc(f.topics.join('、'))}` : ''}</small><small class="fd-url">${esc(f.url)}</small></span>
              <button type="button" class="link" data-del="${i}">删除</button><label class="fd-on"><input type="checkbox" class="switch" data-toggle="${i}"${f.enabled ? ' checked' : ''} aria-label="启用 ${esc(f.name)}"></label></li>`,
          )
          .join('')
      : '<li class="hint grp-row">没有订阅源。成员就不上网了（订阅源里没有合兴趣的内容时也可能联网搜索，每天有次数上限）。</li>';
    this.dlg.innerHTML = `
      <div class="vh sheet-bar"><button type="button" class="sheet-icon" data-close aria-label="关闭">${BACK}</button><b>上网</b><span></span></div>
      <div class="vb fd">
        <section class="grp">
          <h4 class="grp-h">成员看的订阅源 <small>${d.source === 'local' ? '你改过的清单' : '默认清单'}</small></h4>
          <ul class="fd-list grp-box">${rows}</ul>
          <p class="grp-foot">上次抓取：${esc(when)} · 存着 ${d.items ?? 0} 条 · 今天搜索 ${d.search_used_today ?? 0} / ${d.search_per_day ?? 0} 次${d.enabled === false ? ' · <b>现在已在配置里关闭</b>' : ''}</p>
        </section>
        <form id="fd-add" class="fd-add grp">
          <h4 class="grp-h">添加订阅源</h4>
          <div class="grp-box">
            <label class="grp-row"><span>名称</span><input name="name" maxlength="40" placeholder="比如：科技日报" aria-label="名称" required></label>
            <label class="grp-row"><span>网址</span><input name="url" type="url" maxlength="500" placeholder="RSS / Atom 订阅地址" aria-label="订阅源网址" required></label>
            <label class="grp-row"><span>话题</span><input name="topics" placeholder="逗号分隔，如 科技，游戏" aria-label="话题"></label>
            <label class="grp-row"><span>类型</span><select name="kind" aria-label="类型"><option value="news">新闻</option><option value="forum">论坛</option><option value="blog">博客</option></select></label>
          </div>
          <div class="pp-row grp-end"><button class="btn primary" type="submit">添加</button></div>
        </form>
        <section class="grp">
          <div class="grp-box grp-acts"><button type="button" class="grp-btn" data-refresh>现在抓一次</button><button type="button" class="grp-btn" data-reset>恢复默认清单</button></div>
          <p class="grp-foot">成员闲下来时从这些公开订阅源里按兴趣（在「成员设定」里改）挑一条，分享到生活群，附来源和发布时间；论坛、博客和搜索来的标「未核实」。只读：不登录、不发帖、不往外发送你的任何信息。</p>
        </section>
        <p class="hint pp-msg" role="status">${esc(this.msg)}</p>
      </div>`;
  }

  async save(feeds, done) {
    try {
      this.data = await this.api.set(feeds);
      this.msg = done;
    } catch (e) {
      this.msg = e.message || '出错了';
    }
    this.render();
  }

  async onClick(e) {
    const t = e.target;
    if (t.closest('[data-close]')) return this.close();
    const del = t.closest('[data-del]');
    if (del) {
      const feeds = this.feeds();
      feeds.splice(Number(del.dataset.del), 1);
      return this.save(feeds, '删掉了');
    }
    if (t.closest('[data-reset]')) return this.save(null, '已恢复默认清单');
    if (t.closest('[data-refresh]')) {
      this.msg = '正在抓取…';
      this.render();
      try {
        const r = await this.api.refresh();
        this.data = r;
        this.msg = `抓到 ${r.added} 条新内容${r.failed ? `，${r.failed} 个订阅源没抓到` : ''}`;
      } catch (err) {
        this.msg = err.message || '出错了';
      }
      this.render();
    }
  }

  onChange(e) {
    const box = e.target.closest('[data-toggle]');
    if (!box) return;
    const feeds = this.feeds();
    feeds[Number(box.dataset.toggle)].enabled = box.checked;
    this.save(feeds, box.checked ? '已启用' : '已停用');
  }

  onSubmit(e) {
    e.preventDefault();
    const data = new FormData(e.target);
    const feed = {
      name: String(data.get('name') || '').trim(),
      url: String(data.get('url') || '').trim(),
      topics: list(data.get('topics')).slice(0, 8),
      kind: String(data.get('kind') || 'news'),
      enabled: true,
    };
    if (!feed.name || !feed.url) return;
    this.save([...this.feeds(), feed], '加好了');
  }
}
