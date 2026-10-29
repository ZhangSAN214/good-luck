// 关于你（成员系统 P8）：8 位成员共用的一份用户画像。放在「成员设定与记忆」最前面的「关于你」。
// 只放事实：每条带来源（你写的 / 你说过 / 推测）、把握度和证据（你的原话）；推测会变淡，你说过的不变淡。
// 可以看证据、改、删、点「不对」（之后不再推回）、锁定、按领域关、清空、导出。只存在你电脑上。
// 每位成员对你的看法不在这里（归「关系」）。只用于私聊和生活群；工作模式不读取。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const when = (iso) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

export class AboutPanel {
  constructor({ api, now = () => Date.now(), goto = null } = {}) {
    this.api = api; // {get(), add(text, domain), edit(id, text), del(id), reject(id), lock(id, locked), domain(name, on), forget(i), clear(rejected), exportUrl(fmt),
    //  status(), setStatus(kind, on), player(p), addGame(exe, name), delGame(exe), addClass(w, start, end, name), addDdl(name, due), delSchedule(kind, id), clearSchedule()}
    this.st = null; // 你的状态（第二块）
    this.now = now;
    this.goto = goto; // (会话 id, 消息 id) → 去看原话
    this.v = null;
    this.msg = '';
    this.open = new Set(); // 展开了证据的条目
    this.editing = null;
    this.armed = {};
    this.el = null;
  }

  async mount(el) {
    this.el = el;
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      this.onClick(e);
    });
    el.addEventListener('submit', (e) => {
      e.stopPropagation();
      this.onSubmit(e);
    });
    el.addEventListener('change', (e) => {
      const p = e.target.closest('[data-player]');
      if (p) this.act(() => this.api.player(p.value), '换好了');
    });
    this.render();
    await this.load();
  }

  async load() {
    try {
      this.v = await this.api.get();
      if (this.api.status) this.st = await this.api.status();
    } catch (e) {
      this.msg = e.message;
    }
    this.render();
  }

  async act(fn, done = '') {
    try {
      const v = await fn();
      if (v && v.facts) this.v = v;
      else if (v && v.items) this.st = v;
      else await this.load();
      this.msg = done;
    } catch (e) {
      this.msg = e.message || '出错了';
    }
    this.render();
  }

  factHtml(f) {
    const tags = [`<span class="ab-src ab-${f.source}">${esc(f.source_label)}</span>`, `<span title="把握度">把握 ${Math.round(f.confidence * 100)}%</span>`];
    if (f.locked) tags.push('<span>锁定</span>');
    if (f.faded) tags.push('<span title="很久没有新证据，聊天不再带">变淡了</span>');
    if (f.off) tags.push('<span>领域已关</span>');
    const n = f.evidence.length;
    const btns = [
      n ? `<button type="button" class="link" data-ev="${f.id}" aria-expanded="${this.open.has(f.id)}">证据 ${n}</button>` : '',
      `<button type="button" class="link" data-edit="${f.id}">改</button>`,
      `<button type="button" class="link" data-lock="${f.id}" data-on="${f.locked ? 0 : 1}">${f.locked ? '解锁' : '锁定'}</button>`,
      f.source !== 'user' ? `<button type="button" class="link" data-reject="${f.id}" title="删掉，以后像这样的不再记">不对</button>` : '',
      `<button type="button" class="link" data-del="${f.id}">删除</button>`,
    ].join('');
    const text =
      this.editing === f.id
        ? `<form class="pp-row ab-editf" data-id="${f.id}"><textarea name="text" rows="2" maxlength="120">${esc(f.text)}</textarea><button class="btn primary" type="submit">保存</button></form>`
        : `<p>${esc(f.text)}</p>`;
    const ev =
      this.open.has(f.id) && n
        ? `<ul class="ab-ev">${f.evidence
            .map(
              (e) => `<li><span class="ab-meta">${esc(when(e.at))} · ${esc(e.room_title || '聊天')}${e.by_name ? ` · ${esc(e.by_name)} 注意到` : ''}</span>
                <q>${esc(e.quote)}</q>${this.goto ? `<button type="button" class="link" data-go="${esc(e.room)}" data-msg="${e.msg}">去看原话</button>` : ''}</li>`,
            )
            .join('')}${f.noticed?.length ? `<li class="ab-meta">${esc(f.noticed.join('、'))} 也注意到了</li>` : ''}</ul>`
        : '';
    const hist = f.history?.length ? `<small class="ab-meta">改过 ${f.history.length} 次，原来是「${esc(f.history[f.history.length - 1].text)}」</small>` : '';
    return `<li class="${f.faded || f.off ? 'ab-dim' : ''}" data-fid="${f.id}"><div class="pp-mh">${tags.join('')}${btns}</div>${text}${hist}${ev}</li>`;
  }

  statusHtml() {
    const st = this.st;
    if (!st) return '';
    const PLAYERS = { qqmusic: 'QQ 音乐', cloudmusic: '网易云音乐', spotify: 'Spotify', any: '任意播放器' };
    const WD = '一二三四五六日';
    const item = (i) => {
      const extra = [];
      if (i.kind === 'music' && i.on)
        extra.push(`<label class="grp-row"><span>看哪个播放器</span><select data-player aria-label="播放器">${Object.entries(PLAYERS).map(([k, l]) => `<option value="${k}"${k === st.music_player ? ' selected' : ''}>${l}</option>`).join('')}</select></label>`);
      if (i.kind === 'game' && i.on) {
        const mine = Object.entries(st.games || {});
        extra.push(`<p class="ab-meta">认得的游戏：${esc(Object.values(st.config_games || {}).filter((x, k, a) => a.indexOf(x) === k).join('、'))}，以及 Steam / Epic 装的游戏。别的程序叫什么一律不说。</p>
          ${mine.length ? `<ul class="ab-mini">${mine.map(([exe, name]) => `<li>${esc(name)} <small>${esc(exe)}</small><button type="button" class="link" data-delgame="${esc(exe)}">删</button></li>`).join('')}</ul>` : ''}
          <form id="ab-game" class="grp-row"><input name="exe" maxlength="80" placeholder="程序文件名，如 Game.exe" aria-label="程序文件名"><input name="name" maxlength="30" placeholder="游戏名" aria-label="游戏名"><button class="btn" type="submit">加</button></form>`);
      }
      if (i.kind === 'timetable' && i.on) {
        const sc = st.schedule;
        const cls = sc.classes.map((c) => `<li>周${WD[c.weekday]} ${esc(c.start)}–${esc(c.end)} ${esc(c.name)}<button type="button" class="link" data-delsch="class" data-sid="${c.id}">删</button></li>`).join('');
        const ddl = sc.ddls.map((d) => `<li>${esc(d.due.replace('T', ' '))} ${esc(d.name)}<button type="button" class="link" data-delsch="ddl" data-sid="${d.id}">删</button></li>`).join('');
        extra.push(`<ul class="ab-mini">${cls || '<li class="hint">还没有课。</li>'}</ul>
          <form id="ab-class" class="grp-row"><select name="weekday" aria-label="星期">${[...WD].map((w, k) => `<option value="${k}">周${w}</option>`).join('')}</select><input name="start" type="time" required aria-label="上课"><input name="end" type="time" required aria-label="下课"><input name="name" maxlength="30" placeholder="课名" required aria-label="课名"><button class="btn" type="submit">加课</button></form>
          <ul class="ab-mini">${ddl || '<li class="hint">还没有 DDL。</li>'}</ul>
          <form id="ab-ddl" class="grp-row"><input name="name" maxlength="30" placeholder="什么 DDL" required aria-label="DDL"><input name="due" type="datetime-local" required aria-label="截止时间"><button class="btn" type="submit">加 DDL</button></form>
          ${cls || ddl ? '<button type="button" class="link danger" id="ab-sch-clear" data-schclear>清空课表和 DDL</button>' : ''}`);
      }
      const note = i.reason ? `<span class="ab-meta">${esc(i.reason)}</span>` : i.on ? `<span class="ab-meta">${i.preview ? `成员看到：${esc(i.preview)}` : '现在没什么可说的'}</span>` : '';
      return `<li data-kind="${i.kind}"><div class="ab-st"><button type="button" class="ab-tog${i.on ? ' on' : ''}" role="switch" aria-checked="${i.on}" data-st="${i.kind}"><i></i></button><b>${esc(i.label)}</b>${note}</div>${extra.join('')}</li>`;
    };
    return `<h4 class="grp-h">你的状态 <small>每项默认关 · 打开了成员聊天时才知道 · 读到的不存</small></h4>
      <ul class="pp-list grp-box ab-status">${st.items.map(item).join('')}</ul>
      ${st.line ? `<p class="grp-foot">成员现在会看到：${esc(st.line.split('\n').slice(1).join(' '))}</p>` : ''}`;
  }

  render() {
    if (!this.el) return;
    const v = this.v;
    if (!v) {
      this.el.innerHTML = `<h4 class="grp-h">关于你</h4><p class="hint">读取中…</p><p class="hint" role="status">${esc(this.msg)}</p>`;
      return;
    }
    const doms = v.domains
      .map((d) => `<button type="button" class="ab-dom${d.on ? ' on' : ''}" data-dom="${esc(d.name)}" aria-pressed="${d.on}" title="${d.on ? '点一下关掉：不再记、聊天也不带' : '点一下打开'}">${esc(d.name)}</button>`)
      .join('');
    const groups = v.domains
      .map((d) => {
        const rows = v.facts.filter((f) => f.domain === d.name);
        if (!rows.length) return '';
        return `<h5 class="ab-h">${esc(d.name)}${d.on ? '' : ' <small>已关</small>'}</h5><ul class="pp-list grp-box">${rows.map((f) => this.factHtml(f)).join('')}</ul>`;
      })
      .join('');
    const opts = v.domains.map((d) => `<option>${esc(d.name)}</option>`).join('');
    const rej = v.rejected.length
      ? `<details class="grp-box ab-rej"><summary>你说「不对」的 ${v.rejected.length} 条（像这样的不会再记）</summary><ul class="pp-list">${v.rejected
          .map((r, i) => `<li><div class="pp-mh"><span>${esc(r.domain)}</span><span>${esc(r.at)}</span><button type="button" class="link" data-forget="${i}">取消「不对」</button></div><p>${esc(r.text)}</p></li>`)
          .join('')}</ul></details>`
      : '';
    this.el.innerHTML = `
      <h4 class="grp-h">关于你 <small>8 位成员共用 · 只存在你电脑上${v.enabled ? '' : ' · 已在配置里关闭'}</small></h4>
      <p class="grp-foot">这里只放关于你的事实和证据。你亲口说过的不会变淡；成员推测的 30 天没有新证据就淡一半，淡了聊天就不再带。不推测${esc(v.blocked.join('、'))}。每位成员对你的看法不在这里。聊天时只带和话题有关的两三条。</p>
      <div class="grp-box ab-doms" aria-label="按领域开关">${doms}</div>
      <form id="ab-add" class="grp-box"><div class="grp-row"><input name="text" maxlength="120" placeholder="自己写一条，比如：我在读大三，学计算机" aria-label="写一条"><select name="domain" aria-label="领域">${opts}</select><button class="btn" type="submit">记下</button></div></form>
      ${groups || '<p class="hint grp-box">还没有。每晚成员整理时会从你聊天里亲口说的话里找线索（要对得上你的原话才记）；你也可以自己写。</p>'}
      ${rej}
      ${this.statusHtml()}
      <div class="grp-box grp-acts">
        <a class="grp-btn" href="${esc(this.api.exportUrl('md'))}" download>导出（Markdown）</a>
        <a class="grp-btn" href="${esc(this.api.exportUrl('json'))}" download>导出（JSON）</a>
        <button type="button" class="grp-btn danger" id="ab-clear" data-clear="0">清空画像</button>
        <button type="button" class="grp-btn danger" id="ab-clear-all" data-clear="1">连「不对」名单一起清</button>
      </div>
      <p class="hint" role="status">${esc(this.msg)}</p>`;
  }

  armedOk(id, label) {
    const b = this.el.querySelector(`#${id}`);
    if (this.now() > (this.armed[id] || 0)) {
      this.armed[id] = this.now() + 5000;
      if (b) {
        b.textContent = '再点一次确认';
        b.classList.add('gold');
      }
      setTimeout(() => {
        if (b && this.now() > (this.armed[id] || 0)) {
          b.textContent = label;
          b.classList.remove('gold');
        }
      }, 5100);
      return false;
    }
    this.armed[id] = 0;
    return true;
  }

  async onClick(e) {
    const t = e.target;
    const id = (k) => Number(t.closest(`[data-${k}]`).dataset[k]);
    if (t.closest('[data-ev]')) {
      const n = id('ev');
      if (this.open.has(n)) this.open.delete(n);
      else this.open.add(n);
      return this.render();
    }
    if (t.closest('[data-edit]')) {
      this.editing = id('edit');
      this.render();
      return this.el.querySelector('.ab-editf textarea')?.focus();
    }
    if (t.closest('[data-lock]')) {
      const b = t.closest('[data-lock]');
      return this.act(() => this.api.lock(id('lock'), b.dataset.on === '1'), b.dataset.on === '1' ? '锁住了：不会变淡，程序也不改它' : '解锁了');
    }
    if (t.closest('[data-reject]')) return this.act(() => this.api.reject(id('reject')), '删掉了，以后像这样的不再记');
    if (t.closest('[data-del]')) return this.act(() => this.api.del(id('del')), '删掉了');
    if (t.closest('[data-forget]')) return this.act(() => this.api.forget(id('forget')), '取消了');
    const dom = t.closest('[data-dom]');
    if (dom) {
      const on = dom.getAttribute('aria-pressed') !== 'true';
      return this.act(() => this.api.domain(dom.dataset.dom, on), on ? `「${dom.dataset.dom}」打开了` : `「${dom.dataset.dom}」关了：不再记，聊天也不带`);
    }
    const tog = t.closest('[data-st]');
    if (tog) {
      const on = tog.getAttribute('aria-checked') !== 'true';
      return this.act(() => this.api.setStatus(tog.dataset.st, on), on ? '打开了：成员聊天时会知道' : '关了');
    }
    const dg = t.closest('[data-delgame]');
    if (dg) return this.act(() => this.api.delGame(dg.dataset.delgame), '删掉了');
    const ds = t.closest('[data-delsch]');
    if (ds) return this.act(() => this.api.delSchedule(ds.dataset.delsch, Number(ds.dataset.sid)), '删掉了');
    if (t.closest('[data-schclear]')) {
      if (!this.armedOk('ab-sch-clear', '清空课表和 DDL')) return;
      return this.act(() => this.api.clearSchedule(), '课表和 DDL 清空了');
    }
    const go = t.closest('[data-go]');
    if (go && this.goto) return this.goto(go.dataset.go, Number(go.dataset.msg));
    const clear = t.closest('[data-clear]');
    if (clear) {
      const all = clear.dataset.clear === '1';
      const bid = all ? 'ab-clear-all' : 'ab-clear';
      if (!this.armedOk(bid, all ? '连「不对」名单一起清' : '清空画像')) return;
      return this.act(() => this.api.clear(all), '已清空');
    }
  }

  async onSubmit(e) {
    e.preventDefault();
    const f = e.target;
    const data = new FormData(f);
    const val = (k) => String(data.get(k) || '').trim();
    if (f.id === 'ab-game') return this.act(() => this.api.addGame(val('exe'), val('name')), '加好了');
    if (f.id === 'ab-class') return this.act(() => this.api.addClass(Number(val('weekday')), val('start'), val('end'), val('name')), '加好了');
    if (f.id === 'ab-ddl') return this.act(() => this.api.addDdl(val('name'), val('due')), '加好了');
    const text = val('text');
    if (!text) return;
    if (f.id === 'ab-add') return this.act(() => this.api.add(text, String(data.get('domain') || '')), '记下了');
    if (f.classList.contains('ab-editf')) {
      this.editing = null;
      return this.act(() => this.api.edit(Number(f.dataset.id), text), '改好了（改过的算你写的，不会变淡）');
    }
  }
}
