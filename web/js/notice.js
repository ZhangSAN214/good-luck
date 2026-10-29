// 终端公告栏：版本更新说明（GET /api/changelog，数据在 config/changelog.yaml），新的在最上面。
// 没看过的版本带「新」标记；展开一条就算看过，也可以「全部标为已读」。已读记录只存在这台电脑的浏览器里。
// 上面一栏是日历（GET /api/calendar）：今天和未来几天是什么日子（节假日、农历节日、节气、成员生日……），
// 还能加 / 删你自己的纪念日（只存本机）。只读数据，不调用模型、不花钱。

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

const KIND_LABEL = {
  holiday: '假日',
  birthday: '生日',
  joined: '入职',
  user: '纪念日',
  lunar: '农历',
  observance: '节日',
  term: '节气',
  workday: '补班',
};

/** 一条日子的说明：哪里的节日 / 哪里放假。 */
export function itemNote(it) {
  const off = (it.off || []).filter((p) => !it.name.includes(p)); // 「日本文化之日」不再写「日本放假」
  if (it.rest) return '成员放假';
  if (off.length) return `${off.join('、')}放假`;
  if (it.kind === 'workday') return `${(it.places || []).join('、')}调休上班`;
  if (it.kind === 'observance' && it.places?.length && it.places[0] !== '国际') return it.places.join('、');
  return '';
}

/** 「今天」「明天」或「11月5日 周四」。 */
export function dayLabel(day, index) {
  if (index === 0) return '今天';
  if (index === 1) return '明天';
  const [, m, d] = day.date.split('-').map(Number);
  return `${m}月${d}日 ${day.weekday}`;
}

export class Notice {
  /** @param {{load: () => Promise<{latest:string, releases:Array}>, calendar?: {load: Function, add: Function, remove: Function}}} opts */
  constructor({ load, calendar = null }) {
    this.loadFn = load;
    this.cal = calendar;
    this.calData = null;
    this.calError = '';
    this.calForm = false;
    this.calMsg = '';
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
    this.el.addEventListener('submit', (e) => {
      if (!e.target.matches?.('[data-add-date]')) return;
      e.preventDefault();
      this.addDate(e.target);
    });
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
    const before = JSON.stringify([this.data, this.error, this.calData, this.calError]);
    try {
      this.data = await this.loadFn();
      this.error = '';
    } catch (err) {
      this.error = `公告读不出来：${err?.message || err}`;
    }
    if (this.cal) {
      try {
        this.calData = await this.cal.load();
        this.calError = '';
      } catch (err) {
        this.calError = `日历读不出来：${err?.message || err}`;
      }
    }
    // 没变就不重画，展开的条目不收起
    if (JSON.stringify([this.data, this.error, this.calData, this.calError]) !== before) this.paint();
  }

  /** 今天的第一个日子（显示在顶栏按钮上）。 */
  todayName() {
    return this.calData?.days?.[0]?.items?.[0]?.name || '';
  }

  calendar() {
    if (!this.cal) return '';
    if (this.calError) return `<p class="hb-hint hb-err" role="alert">${esc(this.calError)}</p>`;
    const data = this.calData;
    if (!data) return '<p class="hb-hint">正在读取日历…</p>';
    if (!data.enabled) return '';
    const days = (data.days || [])
      .map((d, i) => ({ d, i }))
      .filter(({ d, i }) => i === 0 || d.items.length);
    const rows = days
      .map(({ d, i }) => {
        const items = d.items.length
          ? d.items
              .map((it) => {
                const note = itemNote(it);
                return `<li class="nt-cal-it k-${esc(it.kind)}"><span class="nt-cal-k">${esc(KIND_LABEL[it.kind] || '')}</span>${esc(it.name)}${
                  note ? `<span class="nt-cal-note">${esc(note)}</span>` : ''
                }</li>`;
              })
              .join('')
          : '<li class="nt-cal-it nt-cal-none">平常的一天</li>';
        return `<div class="nt-cal-day${i === 0 ? ' is-today' : ''}${d.rest ? ' is-rest' : ''}" data-date="${esc(d.date)}">
          <div class="nt-cal-when"><b>${esc(dayLabel(d, i))}</b><span>${esc(d.lunar)}${d.rest ? ' · 休息日' : ''}</span></div>
          <ul>${items}</ul></div>`;
      })
      .join('');
    const mine = (data.user_dates || [])
      .map(
        (u) => `<li><span>${esc(u.name)}</span><span class="nt-cal-note">${u.lunar ? '农历 ' : ''}${esc(u.date)}</span>
          <button type="button" class="nt-cal-del" data-del-date="${esc(u.id)}" aria-label="删除纪念日：${esc(u.name)}">删除</button></li>`,
      )
      .join('');
    return `<section class="nt-cal" aria-label="日历：近期的日子">
      <div class="nt-head"><b class="nt-title">日历</b><span class="nt-sub">近 ${(data.days || []).length} 天</span>
        <button type="button" class="hb-chip" data-cal-form aria-expanded="${this.calForm}">我的纪念日</button></div>
      ${rows}
      ${
        this.calForm
          ? `<div class="nt-cal-mine">
        ${mine ? `<ul>${mine}</ul>` : '<p class="hb-hint">还没有纪念日。加上之后成员也会知道（只存在这台电脑上）。</p>'}
        <form class="nt-cal-add" data-add-date>
          <input name="name" maxlength="30" placeholder="名字，如 来曼城的日子" aria-label="纪念日名字" required>
          <input name="date" placeholder="2025-09-20 或 09-20" aria-label="日期" required>
          <label><input type="checkbox" name="lunar"> 农历</label>
          <button type="submit" class="hb-chip">加上</button>
        </form>
        ${this.calMsg ? `<p class="hb-hint hb-err" role="alert">${esc(this.calMsg)}</p>` : ''}
      </div>`
          : ''
      }
    </section>`;
  }

  async addDate(form) {
    const f = new FormData(form);
    try {
      await this.cal.add({ name: String(f.get('name') || ''), date: String(f.get('date') || '').trim(), lunar: !!f.get('lunar') });
      this.calMsg = '';
    } catch (err) {
      this.calMsg = `没加上：${err?.message || err}`;
    }
    await this.reloadCalendar();
  }

  async removeDate(id) {
    try {
      await this.cal.remove(id);
      this.calMsg = '';
    } catch (err) {
      this.calMsg = `没删掉：${err?.message || err}`;
    }
    await this.reloadCalendar();
  }

  async reloadCalendar() {
    try {
      this.calData = await this.cal.load();
    } catch (err) {
      this.calError = `日历读不出来：${err?.message || err}`;
    }
    this.paint();
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
    const today = btn.querySelector('.nt-today');
    if (today) {
      const name = this.todayName();
      today.hidden = !name;
      today.textContent = name ? `· ${name}` : '';
    }
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
      <button type="button" class="nt-btn" aria-expanded="${this.open}">${icon('horn')}<span>公告</span><span class="nt-today" hidden></span><span class="nt-dot" hidden></span></button>
      <div class="nt-panel" role="dialog" aria-label="公告栏：日历和版本更新说明"${this.open ? '' : ' hidden'}>
        ${this.calendar()}
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
    if (e.target.closest('[data-cal-form]')) {
      this.calForm = !this.calForm;
      return this.paint();
    }
    const del = e.target.closest('[data-del-date]');
    if (del) return this.removeDate(del.dataset.delDate);
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
