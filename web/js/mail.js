// 终端邮件栏：你本人的学校邮箱，只读（GET /api/mail 等，后端见 core/mail/）。
// 顶栏一颗「邮件」胶囊，数字是没读的重要邮件；点开是面板：重要 / 全部、正文、截止日期提醒、设置。
// 登录：点「登录学校邮箱」后，电脑上会弹出一个单独的浏览器窗口，由你本人在微软登录页输入账号密码、
// 完成验证；这个页面不收、不传任何密码。读取只读：不发、不删、不移动、不标已读。
// 邮件只存在你电脑的数据库里，不给成员看、不发给模型。打开首页时和之后每 15 分钟顺便刷新一次
// （后端限制最短间隔），后台任务进行中每 2 秒问一次进度。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const AUTO_MS = 15 * 60_000;
const POLL_MS = 1500;
const WEB_URL = 'https://outlook.office.com/mail/';
const WEEK = '日一二三四五六';
const pad = (n) => String(n).padStart(2, '0');

const ICON = {
  mail: '<rect x="3.5" y="5.5" width="17" height="13" rx="2.5"/><path d="M4 7l8 6 8-6"/>',
  back: '<path d="M14.5 6l-6 6 6 6"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6 6l1.6 1.6M16.4 16.4L18 18M6 18l1.6-1.6M16.4 7.6L18 6"/>',
};
const icon = (k) => `<svg class="ml-i" viewBox="0 0 24 24" aria-hidden="true">${ICON[k]}</svg>`;

/** 邮件时间：今天只显示时刻，今年显示月日，更早显示年月日。 */
export function when(iso, now = new Date()) {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return '';
  const hm = `${pad(t.getHours())}:${pad(t.getMinutes())}`;
  if (t.toDateString() === now.toDateString()) return hm;
  if (t.getFullYear() === now.getFullYear()) return `${t.getMonth() + 1}月${t.getDate()}日 ${hm}`;
  return `${t.getFullYear()}.${pad(t.getMonth() + 1)}.${pad(t.getDate())}`;
}

/** 提醒的截止时间（'YYYY-MM-DD' 或带 'THH:MM'，按你本机日历显示）。 */
export function dueText(due) {
  const [d, hm] = String(due || '').split('T');
  const [y, m, day] = d.split('-').map(Number);
  if (!y || !m || !day) return String(due || '');
  const wd = WEEK[new Date(y, m - 1, day).getDay()];
  return `${m}月${day}日 周${wd}${hm ? ` ${hm}` : ''}`;
}

const lines = (s) =>
  String(s || '')
    .split(/[\n,，]+/)
    .map((x) => x.trim())
    .filter(Boolean);

export class Mail {
  /**
   * @param {{load:Function, settings:Function, login:Function, refresh:Function, message:Function, reminder:Function, logout:Function, clear:Function}} api
   */
  constructor(api) {
    this.api = api;
    this.el = document.createElement('div');
    this.el.className = 'ml';
    this.data = null;
    this.error = '';
    this.open = false;
    this.view = 'list'; // list / detail / settings
    this.filter = 'important';
    this.detail = null;
    this.timer = null;
    this.el.addEventListener('click', (e) => this.onClick(e));
    this.el.addEventListener('submit', (e) => this.onSubmit(e));
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
    this.ready = this.reload().then(() => this.auto());
    this.autoTimer = setInterval(() => this.auto(), AUTO_MS);
  }

  /** 挂到终端首页顶栏的空位上（首页每次重画都会再挂一次，状态不丢）。 */
  mount(slot) {
    if (slot) slot.replaceWith(this.el);
  }

  async call(fn, ...args) {
    try {
      const data = await fn(...args);
      this.error = '';
      return data;
    } catch (err) {
      this.error = err?.message || String(err);
      this.paint();
      return null;
    }
  }

  async reload() {
    const data = await this.call(this.api.load);
    if (data) this.take(data);
  }

  take(data) {
    this.data = data;
    if (this.view === 'detail' && this.detail && !data.enabled) this.view = 'list';
    this.paint();
    clearTimeout(this.timer);
    if (data.busy) this.timer = setTimeout(() => this.poll(), POLL_MS);
  }

  async poll() {
    await this.reload();
    if (this.view === 'detail' && this.detail?.loading && !this.data?.busy) this.showDetail(this.detail.id);
  }

  /** 打开首页时和每 15 分钟：已登录就让后端刷新一次（离上次太近后端会跳过）。 */
  async auto() {
    const d = this.data;
    if (!d?.enabled || d.status !== 'connected' || d.busy) return;
    const data = await this.call(this.api.refresh, false);
    if (data) this.take(data);
  }

  count() {
    return this.data?.enabled ? this.data.counts?.important || 0 : 0;
  }

  paintButton() {
    const btn = this.el.querySelector('.ml-btn');
    if (!btn) return;
    const n = this.count();
    const unread = this.data?.counts?.unread || 0;
    btn.setAttribute('aria-label', this.data?.enabled ? `邮件：${unread} 封未读，其中重要 ${n} 封` : '邮件栏（未打开）');
    const dot = btn.querySelector('.ml-dot');
    dot.hidden = !n;
    dot.textContent = n > 9 ? '9+' : String(n);
  }

  statusLine() {
    const d = this.data;
    if (!d) return this.error ? '' : '<p class="hb-hint">正在读取…</p>';
    if (d.busy) return `<p class="ml-status ml-busy" role="status"><span class="ml-spin" aria-hidden="true"></span>${esc(d.busy_text)}…</p>`;
    const map = {
      connected: `已连接 · ${d.last_ok ? `${when(d.last_ok)} 更新` : ''}`,
      expired: '登录过期了，需要重新登录',
      never: '还没登录',
      error: '上次没读成功',
    };
    const text = map[d.status] || '';
    const msg = d.message && d.status !== 'connected' ? `<span class="ml-msg">${esc(d.message)}</span>` : '';
    return `<p class="ml-status ml-${esc(d.status)}">${esc(text)}${msg}</p>`;
  }

  intro() {
    return `<div class="ml-intro">
      <p>把你的<b>学校邮箱</b>接进终端：只读，重要的老师 / 学院 / 考试邮件置顶，截止日期提醒你确认。</p>
      <ul>
        <li>登录时弹出一个单独的浏览器窗口，<b>由你本人</b>在微软登录页输入账号密码、完成验证；圆桌不代填、不保存密码。</li>
        <li>只读：不发信、不删信、不移动、不标已读；那个窗口里的写入操作也会被拦下。</li>
        <li>邮件只存在这台电脑上，不给成员看、不发给模型；「重要」按本机规则判断，不花钱。</li>
      </ul>
      <button type="button" class="ml-main" data-enable>打开邮件栏</button>
    </div>`;
  }

  loginBox() {
    const d = this.data;
    const again = d.status === 'expired';
    return `<div class="ml-intro">
      <p>${again ? '学校的登录状态过期了（多久过期由学校决定），重新登录一次就好。' : '点下面的按钮，电脑上会弹出一个单独的浏览器窗口，打开微软登录页。'}</p>
      <p class="hb-hint">请在那个窗口里<b>自己</b>输入学校账号密码并完成验证（手机验证器 / 验证码）；看到「保持登录状态？」可以选「是」，以后少登录几次。登录好以后窗口会自动关掉。不要把密码发给任何人，包括这里的聊天。</p>
      <button type="button" class="ml-main" data-login${d.busy ? ' disabled' : ''}>登录学校邮箱</button>
    </div>`;
  }

  row(m) {
    const chips = (m.reasons || []).map((r) => `<span class="ml-why">${esc(r)}</span>`).join('');
    return `<li><button type="button" class="ml-row${m.is_read ? '' : ' unread'}${m.important ? ' imp' : ''}" data-open="${esc(m.id)}">
      <span class="ml-from">${esc(m.sender_name || m.sender_addr || '（没有发件人）')}</span>
      <span class="ml-time">${esc(when(m.received_at))}</span>
      <span class="ml-subj">${esc(m.subject || '（无标题）')}</span>
      <span class="ml-prev">${esc(m.preview)}</span>
      ${chips ? `<span class="ml-whys">${chips}</span>` : ''}
    </button></li>`;
  }

  reminderRow(r, withSubject = true) {
    const acts =
      r.status === 'pending'
        ? `<button type="button" class="hb-chip" data-rem="${r.id}" data-do="accept">加入提醒</button><button type="button" class="hb-chip" data-rem="${r.id}" data-do="dismiss">不要</button>`
        : r.status === 'accepted'
          ? `<span class="ml-ok">已加入</span><button type="button" class="hb-chip" data-rem="${r.id}" data-do="undo">撤销</button>`
          : `<span class="ml-no">不要了</span><button type="button" class="hb-chip" data-rem="${r.id}" data-do="undo">撤销</button>`;
    return `<li class="ml-rem ${esc(r.status)}"><b class="ml-due">${esc(dueText(r.due))}</b>
      <span class="ml-snip">${withSubject && r.subject ? `${esc(r.subject)}：` : ''}${esc(r.snippet)}</span>
      <span class="ml-acts">${acts}</span></li>`;
  }

  listView() {
    const d = this.data;
    const all = d.messages || [];
    const shown = this.filter === 'important' ? all.filter((m) => m.important) : all;
    const pending = (d.reminders || []).filter((r) => r.status === 'pending');
    const accepted = (d.reminders || []).filter((r) => r.status === 'accepted');
    const needLogin = d.status === 'never' || d.status === 'expired';
    return `
      ${needLogin && !d.busy ? this.loginBox() : ''}
      ${pending.length ? `<section class="ml-sec"><p class="nt-k">在邮件里找到的日期（确认后才算）</p><ul class="ml-rems">${pending.map((r) => this.reminderRow(r)).join('')}</ul></section>` : ''}
      ${accepted.length ? `<section class="ml-sec"><p class="nt-k">即将到来</p><ul class="ml-rems">${accepted.map((r) => this.reminderRow(r)).join('')}</ul></section>` : ''}
      ${all.length || !needLogin ? `<div class="ml-tabs" role="group" aria-label="筛选">
        <button type="button" class="hb-chip" data-filter="important" aria-pressed="${this.filter === 'important'}">重要 ${all.filter((m) => m.important).length}</button>
        <button type="button" class="hb-chip" data-filter="all" aria-pressed="${this.filter === 'all'}">全部 ${all.length}</button>
        <span class="ml-days">最近 ${d.days} 天</span></div>
      <ul class="ml-list">${shown.map((m) => this.row(m)).join('') || `<li class="hb-hint ml-empty">${this.filter === 'important' ? '没有重要邮件' : '没有邮件'}</li>`}</ul>` : ''}`;
  }

  detailView() {
    const m = this.detail;
    if (!m) return '<p class="hb-hint">正在打开…</p>';
    const body = m.body ?? null;
    return `<article class="ml-detail">
      <h3 class="ml-dsubj">${esc(m.subject || '（无标题）')}</h3>
      <p class="ml-dfrom">${esc(m.sender_name)}${m.sender_addr ? ` &lt;${esc(m.sender_addr)}&gt;` : ''} · ${esc(when(m.received_at))}${m.attachments ? ' · 有附件' : ''}</p>
      ${(m.reasons || []).length ? `<p class="ml-whys">${m.reasons.map((r) => `<span class="ml-why">${esc(r)}</span>`).join('')}</p>` : ''}
      ${(m.reminders || []).length ? `<ul class="ml-rems">${m.reminders.map((r) => this.reminderRow(r, false)).join('')}</ul>` : ''}
      <div class="ml-body">${body !== null ? esc(body) : `${esc(m.preview)}${m.loading ? '<p class="hb-hint ml-busy"><span class="ml-spin" aria-hidden="true"></span>正在读取正文…</p>' : '<p class="hb-hint">（正文还没读到，下次刷新时会再试）</p>'}`}</div>
      <p class="hb-hint">这里只显示文字，不加载图片、不打开链接。要回信或看附件，请到 <a href="${WEB_URL}" target="_blank" rel="noopener noreferrer">网页版邮箱</a> 里操作。</p>
    </article>`;
  }

  settingsView() {
    const d = this.data;
    const r = d.rules || {};
    const area = (name, label, hint, value) => `<label class="ml-field"><span>${label}</span>
      <textarea name="${name}" rows="3" spellcheck="false">${esc((value || []).join('\n'))}</textarea><small class="hb-hint">${hint}</small></label>`;
    return `<form class="ml-form" data-settings>
      <label class="ml-check"><input type="checkbox" name="enabled"${d.enabled ? ' checked' : ''}> 打开邮件栏</label>
      <p class="nt-k">怎么算「重要」（命中任一条就算，只在本机判断）</p>
      ${area('domains', '学校发件人的域名', '一行一个，子域名也算', r.domains)}
      ${area('ignore_domains', '这些域名不算', '默认排除同学之间互发的', r.ignore_domains)}
      ${area('senders', '我关心的发件人', '一行一个邮箱地址', r.senders)}
      ${area('keywords', '关键词', '标题或摘要里出现就算；英文按整词匹配', r.keywords)}
      <label class="ml-check"><input type="checkbox" name="use_flags"${r.use_flags !== false ? ' checked' : ''}> 我在邮箱里插了旗标、或发件人标了「重要」的也算</label>
      <p class="nt-k">读取</p>
      <label class="ml-field ml-inline"><span>保留最近</span><input type="number" name="days" min="1" max="60" value="${esc(d.days)}"><span>天的邮件</span></label>
      <label class="ml-check"><input type="checkbox" name="show_window"${d.show_window ? ' checked' : ''}> 后台刷新时也显示浏览器窗口（学校总要求重新登录时可以试试）</label>
      ${area('extra_hosts', '学校登录额外用到的网站', '一般不用填；登录页打不开时，把下面「最近拦下的」里出现的学校登录网站加进来', d.extra_hosts)}
      <div class="ml-btns"><button type="submit" class="ml-main">保存</button></div>
      ${(d.blocked || []).length ? `<details class="ml-blocked"><summary class="hb-hint">上次读取时拦下的请求（${d.blocked.length}）</summary><ul>${d.blocked.map((b) => `<li>${esc(b)}</li>`).join('')}</ul></details>` : ''}
      <p class="nt-k">清除</p>
      <div class="ml-btns">
        <button type="button" class="hb-chip" data-clear>清空邮件缓存</button>
        <button type="button" class="hb-chip ml-danger" data-logout>退出登录并清除全部</button>
      </div>
      <p class="hb-hint">「退出登录」会删掉这台电脑上专用窗口的登录状态和全部缓存的邮件、提醒。你也可以随时在学校账号的安全设置里让所有设备退出。</p>
    </form>`;
  }

  paint() {
    const d = this.data;
    let body = '';
    if (d && !d.available) body = '<p class="hb-hint">这里用不了邮件栏（没有本机数据目录）。</p>';
    else if (d && !d.enabled && this.view !== 'settings') body = this.intro();
    else if (d) body = this.view === 'detail' ? this.detailView() : this.view === 'settings' ? this.settingsView() : this.listView();
    const canRefresh = d?.enabled && (d.status === 'connected' || d.status === 'error') && !d.busy;
    const inner = this.view !== 'list';
    this.el.innerHTML = `
      <button type="button" class="ml-btn" aria-expanded="${this.open}">${icon('mail')}<span>邮件</span><span class="ml-dot" hidden></span></button>
      <div class="ml-panel" role="dialog" aria-label="邮件栏：学校邮箱（只读）"${this.open ? '' : ' hidden'}>
        <div class="ml-head">
          ${inner ? `<button type="button" class="hb-chip ml-icon" data-back aria-label="返回">${icon('back')}</button>` : ''}
          <b class="nt-title">${this.view === 'settings' ? '邮件栏设置' : '邮件'}</b><span class="nt-sub">学校邮箱 · 只读</span>
          <span class="ml-tools">
            ${canRefresh && this.view === 'list' ? '<button type="button" class="hb-chip" data-refresh>刷新</button>' : ''}
            ${d?.available && this.view !== 'settings' ? `<button type="button" class="hb-chip ml-icon" data-settings-open aria-label="设置">${icon('gear')}</button>` : ''}
          </span>
        </div>
        ${d?.enabled ? this.statusLine() : ''}
        ${this.error ? `<p class="hb-hint hb-err" role="alert">${esc(this.error)}</p>` : ''}
        ${!d && !this.error ? '<p class="hb-hint">正在读取…</p>' : ''}
        ${body}
      </div>`;
    this.paintButton();
  }

  toggle(open, refocus = true) {
    this.open = open;
    if (!open) this.view = 'list';
    this.paint();
    if (open) {
      this.el.querySelector('.ml-panel button, .ml-panel a')?.focus();
      this.reload();
    } else if (refocus) this.el.querySelector('.ml-btn')?.focus();
  }

  async showDetail(id) {
    this.view = 'detail';
    if (this.detail?.id !== id) this.detail = null;
    this.paint();
    const m = await this.call(this.api.message, id);
    if (!m) return;
    this.detail = m;
    this.paint();
    if (m.loading) {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.poll(), POLL_MS);
    }
  }

  async act(fn, ...args) {
    const data = await this.call(fn, ...args);
    if (data) this.take(data);
    return data;
  }

  async onClick(e) {
    const t = e.target;
    if (t.closest('.ml-btn')) return this.toggle(!this.open);
    if (t.closest('[data-back]')) {
      this.view = 'list';
      this.detail = null;
      return this.paint();
    }
    if (t.closest('[data-settings-open]')) {
      this.view = 'settings';
      return this.paint();
    }
    const f = t.closest('[data-filter]');
    if (f) {
      this.filter = f.dataset.filter;
      return this.paint();
    }
    const open = t.closest('[data-open]');
    if (open) return this.showDetail(open.dataset.open);
    if (t.closest('[data-enable]')) return this.act(this.api.settings, { enabled: true });
    if (t.closest('[data-login]')) return this.act(this.api.login);
    if (t.closest('[data-refresh]')) return this.act(this.api.refresh, true);
    const rem = t.closest('[data-rem]');
    if (rem) {
      const data = await this.act(this.api.reminder, Number(rem.dataset.rem), rem.dataset.do);
      if (data && this.view === 'detail' && this.detail) this.showDetail(this.detail.id);
      return;
    }
    if (t.closest('[data-clear]')) {
      if (!window.confirm('清空这台电脑上缓存的全部邮件和提醒？（登录状态保留）')) return;
      return this.act(this.api.clear);
    }
    if (t.closest('[data-logout]')) {
      if (!window.confirm('退出学校邮箱登录，并清除这台电脑上的登录状态、缓存的邮件和提醒？')) return;
      if (await this.act(this.api.logout)) {
        this.view = 'list';
        this.paint();
      }
      return;
    }
  }

  async onSubmit(e) {
    if (!e.target.matches('[data-settings]')) return;
    e.preventDefault();
    const f = new FormData(e.target);
    const body = {
      enabled: f.get('enabled') === 'on',
      days: Number(f.get('days')) || 14,
      show_window: f.get('show_window') === 'on',
      extra_hosts: lines(f.get('extra_hosts')),
      rules: {
        domains: lines(f.get('domains')),
        ignore_domains: lines(f.get('ignore_domains')),
        senders: lines(f.get('senders')),
        keywords: lines(f.get('keywords')),
        use_flags: f.get('use_flags') === 'on',
      },
    };
    const data = await this.act(this.api.settings, body);
    if (data) {
      this.view = 'list';
      this.paint();
    }
  }
}
