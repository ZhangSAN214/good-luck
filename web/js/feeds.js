// 上网：成员读哪些公开订阅源（RSS / Atom）。只读、不登录；抓取不花钱，分享和搜索计入生活每日上限。
// 网络梗：梗库（经典梗、新梗、谁懂）、手动加删、马上更新一次。
import { tzOpt } from './hometz.js';

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
  constructor(dialog, { api, memes = null } = {}) {
    this.dlg = dialog;
    this.api = api; // {get(), set(feeds|null), refresh()}
    this.memesApi = memes; // 网络梗：{get(), add(meme), remove(phrase), refresh()}
    this.memes = null;
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
      if (this.memesApi) this.memes = await this.memesApi.get();
    } catch (e) {
      this.msg = e.message;
    }
    this.render();
  }

  memesHtml() {
    const m = this.memes;
    if (!m) return '';
    const st = m.status || {};
    const when = st.at ? new Date(st.at).toLocaleString('zh-CN', tzOpt({ hour12: false })) : '还没更新过';
    const rows = m.memes.length
      ? m.memes
          .map(
            (x) => `<li class="grp-row mm-row"><span class="fd-name"><b>${esc(x.phrase)}</b>${x.classic ? '<small class="mm-tag">经典</small>' : ''}<small>${esc(x.meaning || '意思还没查到，下次联网时补上')}</small>${x.cue || x.reply ? `<small>${x.cue ? `上句「${esc(x.cue)}」` : ''}${x.reply ? ` → 接「${esc(x.reply)}」` : ''}</small>` : ''}<small class="fd-url">懂的人：${esc(x.known_by.join('、') || '暂时没有')}</small></span>
              <button type="button" class="link" data-meme-del="${esc(x.phrase)}">删除</button></li>`,
          )
          .join('')
      : '<li class="hint grp-row">还没有梗。每天会从热榜和搜索里自动收新梗，也可以在下面自己加。</li>';
    return `
        <section class="grp">
          <h4 class="grp-h">网络梗 <small>${m.memes.length} 个</small></h4>
          <ul class="fd-list grp-box">${rows}</ul>
          <p class="grp-foot">上次更新：${esc(when)}${st.titles != null ? ` · 热榜 ${st.titles} 条${st.failed ? `（${st.failed} 个没抓到）` : ''} · 新收 ${st.added ?? 0} 个` : ''} · 今天搜索 ${m.search_used_today ?? 0} / ${m.search_per_day ?? 0} 次${m.enabled === false ? ' · <b>现在已在配置里关闭</b>' : ''}</p>
        </section>
        <form id="mm-add" class="fd-add grp">
          <h4 class="grp-h">加一个梗</h4>
          <div class="grp-box">
            <label class="grp-row"><span>梗</span><input name="phrase" maxlength="20" placeholder="比如：虾爹" aria-label="梗" required></label>
            <label class="grp-row"><span>意思</span><input name="meaning" maxlength="80" placeholder="一句话说清" aria-label="意思"></label>
            <label class="grp-row"><span>上句</span><input name="cue" maxlength="40" placeholder="别人说什么时用，如：康神开播了" aria-label="上句"></label>
            <label class="grp-row"><span>接法</span><input name="reply" maxlength="40" placeholder="怎么接，如：真的假的？！" aria-label="接法"></label>
            <label class="grp-row"><span>经典梗</span><input type="checkbox" class="switch" name="classic" checked aria-label="经典梗（永远不下架）"></label>
          </div>
          <div class="pp-row grp-end"><button class="btn primary" type="submit">加上</button></div>
        </form>
        <section class="grp">
          <div class="grp-box grp-acts"><button type="button" class="grp-btn" data-meme-refresh>现在更新一次梗</button></div>
          <p class="grp-foot">每天从公开热榜（${esc((m.sources || []).join('、'))}）抓标题、再搜一次，交给最便宜的一位挑出新梗（约 $0.01～0.03，算生活每日上限）。新梗 ${m.fresh_days} 天没再上榜就下架；经典梗不下架；老梗名单里的（${m.stale.length} 个，如 ${esc(m.stale.slice(0, 4).join('、'))}）不收，成员说出口前也会删掉。谁懂哪个梗只看各自的性格。</p>
        </section>`;
  }

  feeds() {
    return (this.data?.feeds || []).map((f) => ({ ...f }));
  }

  render() {
    const d = this.data || { feeds: [] };
    const when = d.last_refresh ? new Date(d.last_refresh).toLocaleString('zh-CN', tzOpt({ hour12: false })) : '还没抓过';
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
            <label class="grp-row"><span>类型</span><select name="kind" aria-label="类型"><option value="news">新闻</option><option value="forum">论坛</option><option value="blog">博客</option><option value="video">视频</option></select></label>
          </div>
          <div class="pp-row grp-end"><button class="btn primary" type="submit">添加</button></div>
        </form>
        <section class="grp">
          <div class="grp-box grp-acts"><button type="button" class="grp-btn" data-refresh>现在抓一次</button><button type="button" class="grp-btn" data-reset>恢复默认清单</button></div>
          <p class="grp-foot">成员闲下来时从这些公开订阅源里按兴趣（在「成员设定」里改）挑一条，分享到生活群，附来源和发布时间；论坛、博客和搜索来的标「未核实」。只读：不登录、不发帖、不往外发送你的任何信息。</p>
        </section>
        ${this.memesHtml()}
        <p class="hint pp-msg" role="status">${esc(this.msg)}</p>
      </div>`;
  }

  async memeCall(fn, done) {
    try {
      this.memes = await fn();
      this.msg = done;
    } catch (e) {
      this.msg = e.message || '出错了';
    }
    this.render();
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
    const mdel = t.closest('[data-meme-del]');
    if (mdel) return this.memeCall(() => this.memesApi.remove(mdel.dataset.memeDel), '删掉了，以后也不再收');
    if (t.closest('[data-meme-refresh]')) {
      this.msg = '正在更新梗…';
      this.render();
      return this.memeCall(() => this.memesApi.refresh(), '更新好了');
    }
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
    if (e.target.id === 'mm-add') {
      const meme = {
        phrase: String(data.get('phrase') || '').trim(),
        meaning: String(data.get('meaning') || '').trim(),
        cue: String(data.get('cue') || '').trim(),
        reply: String(data.get('reply') || '').trim(),
        classic: data.get('classic') === 'on',
      };
      if (!meme.phrase) return;
      return this.memeCall(() => this.memesApi.add(meme), '加好了');
    }
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
