// 「上网」面板下半部分：旅行与 B 站（docs/LIFE_STORY.md「旅行」「旅行打卡」「B 站」）。
// 谁在外面旅行、打卡记录（判定卡过没过、旅行日记）、看过的 B 站视频（几帧 + 看到了什么）、
// 看完写的私信和动态（只存在这台电脑上给你看，不会发到 B 站）、一键总开关、让她看一个视频。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : '');
const day = (s) => String(s || '').slice(5, 10);
const when = (s) => String(s || '').replace('T', ' ').slice(5, 16);
const media = (id) => `/api/chat/media/${encodeURIComponent(id)}`;

export class Trips {
  /** @param {{trips: Function, switches: Function, watch: Function}} api */
  constructor(api) {
    this.api = api;
    this.data = null;
    this.err = '';
    this.busy = false;
  }

  async load() {
    try {
      this.data = await this.api.trips();
      this.err = '';
    } catch (e) {
      this.err = e.message;
    }
  }

  html() {
    const d = this.data;
    if (!d) return this.err ? `<p class="wd-warn">${esc(this.err)}</p>` : '';
    const names = d.names || {};
    const nm = (id) => esc(names[id] || id);
    const b = d.bili || {};
    const sw = (key, on, label) =>
      `<button type="button" class="btn tp-sw ${on ? 'on' : ''}" data-tsw="${key}" aria-pressed="${on}">${label}：${on ? '开' : '关'}</button>`;
    const away = Object.entries(d.travel || {})
      .filter(([, v]) => v.now)
      .map(([id, v]) => `<li><b>${nm(id)}</b> ${v.now.away ? '在' : '计划去'}${esc(v.now.dest)}（${day(v.now.go)} 出发，${day(v.now.back)} 回来）</li>`);
    const cards = (d.checkins || []).map((r) => {
      const j = r.journal;
      const mark = r.ok ? '<span class="tp-ok">打卡</span>' : `<span class="tp-no">没打卡</span>`;
      const why = r.ok ? esc(r.card?.why || '') : esc(r.reason || '');
      const photo = r.photo?.id ? `<img class="tp-photo" src="${media(r.photo.id)}" alt="打卡照片" loading="lazy">` : '';
      const body = j ? `<details><summary>${esc(j.title || '旅行日记')}</summary><div class="tp-journal">${esc(j.body)}</div></details>` : '';
      return `<li><span class="tp-when">${day(r.date)}</span><b>${nm(r.member)}</b> ${r.source === 'bili' ? 'B 站' : '旅行'}《${esc(r.title)}》 ${mark}<span class="tp-why">${why}</span>${photo}${body}</li>`;
    });
    const outbox = (b.outbox || []).map((o) => {
      const url = safeUrl(o.url);
      const link = url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">《${esc(o.title)}》</a>` : `《${esc(o.title)}》`;
      return `<li><span class="tp-when">${when(o.at)}</span><b>${nm(o.member)}</b><span class="tp-kind">${o.kind === 'dm' ? '私信你' : '动态'}</span>${esc(o.text)} ${link}</li>`;
    });
    const watched = (b.watched || []).map((w) => {
      const url = safeUrl(w.url);
      const frames = (w.frames || []).map((f) => `<figure><img src="${media(f.id)}" alt="${esc(f.t)} 的画面" loading="lazy"><figcaption>${esc(f.t)}</figcaption></figure>`).join('');
      const seen = w.seen ? `<details><summary>她看到了什么</summary><div class="tp-journal">${esc(w.seen)}</div></details>` : '';
      return `<li><span class="tp-when">${when(w.at)}</span><b>${nm(w.member)}</b> 看了${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">《${esc(w.title)}》</a>` : `《${esc(w.title)}》`}<div class="tp-frames">${frames}</div>${seen}</li>`;
    });
    const list = (items, empty) => (items.length ? `<ul class="tp-list">${items.join('')}</ul>` : `<p class="wd-hint">${empty}</p>`);
    const who = Object.entries(names)
      .map(([id, n]) => `<option value="${esc(id)}">${esc(n)}</option>`)
      .join('');
    const q = b.quota || {};
    return `<section class="tp" aria-label="旅行与 B 站">
      <h4>旅行与 B 站</h4>
      <div class="tp-sws">${sw('auto', b.auto, 'B 站全部自动')}${sw('photo', b.photo, '打卡配图')}</div>
      <p class="wd-hint">B 站自动：她们逛到的视频每天最多看 ${esc(q.watch)} 个，看完每人每天最多写 1 条私信、1 条动态（全体私信 ${esc(q.dm)} 条、动态 ${esc(q.post)} 条）。<b>私信和动态只存在这台电脑上，在下面给你看，不会发到 B 站。</b>打卡配图默认关（要真画图，会花出图的钱）。花费都算在生活每天的上限里。</p>
      ${this.err ? `<p class="wd-warn" role="alert">${esc(this.err)}</p>` : ''}
      <h5>在外面</h5>${list(away, '现在没人出门旅行。')}
      <h5>私信和动态</h5>${list(outbox, '还没有。')}
      <h5>打卡</h5>${list(cards, '还没打过卡：旅行途中、看完 B 站视频时，值得站进去的地方才打。')}
      <h5>看过的 B 站视频</h5>${list(watched, '还没看过。')}
      <div class="tp-watch"><label>让 <select data-twho aria-label="成员">${who}</select></label><input type="url" data-turl placeholder="B 站视频链接（带 BV 号）" aria-label="B 站视频链接"><button type="button" class="btn" data-twatch ${this.busy ? 'disabled' : ''}>${this.busy ? '在看…' : '让她看'}</button></div>
    </section>`;
  }

  /** 处理点击；有改动返回 true（外面重画）。 */
  async onClick(t, root) {
    const s = t.closest('[data-tsw]');
    if (s) {
      const key = s.dataset.tsw;
      const on = s.getAttribute('aria-pressed') !== 'true';
      try {
        this.data = await this.api.switches({ [key]: on });
      } catch (e) {
        this.err = e.message;
      }
      return true;
    }
    if (t.closest('[data-twatch]')) {
      const member = root.querySelector('[data-twho]')?.value;
      const url = root.querySelector('[data-turl]')?.value.trim();
      if (!member || !url) return false;
      this.busy = true;
      this.err = '';
      try {
        await this.api.watch(member, url);
      } catch (e) {
        this.err = e.message;
      }
      this.busy = false;
      await this.load();
      return true;
    }
    return false;
  }
}
