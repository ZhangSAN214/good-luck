// 公寓楼：员工下班后住的地方（像素风剖面图，类似基建）。住户按作息在各房间活动，正在办事的在办公室 / 会议室。
// 数据来自 /api/apartment（按浏览器本地时间）；小人用 /api/members 的 chibi，没有素材时用昵称首字。
// 匿名未揭晓的会话：服务端只给数量，这里只在会议室门上挂牌，不显示是谁。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const REFRESH_MS = 30_000;

// 活动 → 头顶的小气泡
const BUBBLES = { sleep: 'z', eat: '🍙', idle: '♪', work: '⌨', wait: '?', exercise: '💪', chat: '…', read: '📖', stargaze: '✧', game: '🎮' };
const ROOM_VERB = { office: '在办公室', meeting: '在会议室', canteen: '在食堂', lounge: '在休息室', gym: '在健身房', rooftop: '在天台' };
const ROLE = { manager: '经理', staff: '员工' };

// 像素家具：每个房间一张小 SVG（viewBox 64×24，crispEdges），颜色用 CSS 变量，深浅主题都能看清
const R = (x, y, w, h, c) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${c}"/>`;
const FURNITURE = {
  dorm: (lit) =>
    R(4, 14, 22, 6, 'var(--px-wood)') + R(4, 12, 6, 4, 'var(--px-cloth)') + R(10, 13, 16, 3, 'var(--px-accent)') + R(4, 20, 2, 4, 'var(--px-wood-2)') + R(24, 20, 2, 4, 'var(--px-wood-2)') +
    R(36, 4, 12, 9, 'var(--px-frame)') + R(37, 5, 10, 7, lit ? 'var(--px-glow)' : 'var(--px-glass)') + R(41, 5, 1, 7, 'var(--px-frame)') +
    R(52, 14, 8, 2, 'var(--px-wood)') + R(53, 16, 2, 8, 'var(--px-wood-2)') + R(57, 16, 2, 8, 'var(--px-wood-2)') + R(55, 10, 3, 4, 'var(--px-glow)'),
  office: (lit) =>
    [4, 24, 44].map((x) => R(x, 14, 16, 2, 'var(--px-wood)') + R(x + 1, 16, 2, 8, 'var(--px-wood-2)') + R(x + 13, 16, 2, 8, 'var(--px-wood-2)') + R(x + 4, 6, 8, 6, 'var(--px-frame)') + R(x + 5, 7, 6, 4, lit ? 'var(--px-screen)' : 'var(--px-glass)') + R(x + 7, 12, 2, 2, 'var(--px-frame)')).join(''),
  meeting: () =>
    R(8, 2, 22, 10, 'var(--px-board)') + R(10, 4, 10, 1, 'var(--px-accent)') + R(10, 7, 14, 1, 'var(--px-frame)') +
    R(34, 14, 26, 3, 'var(--px-wood)') + R(36, 17, 2, 7, 'var(--px-wood-2)') + R(56, 17, 2, 7, 'var(--px-wood-2)'),
  canteen: () =>
    R(2, 12, 20, 12, 'var(--px-frame)') + R(2, 10, 20, 2, 'var(--px-wood)') + R(6, 6, 6, 4, 'var(--px-glass)') + R(7, 3, 1, 2, 'var(--px-cloth)') + R(10, 2, 1, 3, 'var(--px-cloth)') +
    R(30, 15, 14, 2, 'var(--px-wood)') + R(36, 17, 2, 7, 'var(--px-wood-2)') + R(48, 15, 14, 2, 'var(--px-wood)') + R(54, 17, 2, 7, 'var(--px-wood-2)'),
  lounge: (lit) =>
    R(4, 14, 24, 6, 'var(--px-accent)') + R(4, 10, 24, 4, 'var(--px-accent-2)') + R(2, 12, 3, 8, 'var(--px-accent-2)') + R(27, 12, 3, 8, 'var(--px-accent-2)') +
    R(38, 4, 18, 11, 'var(--px-frame)') + R(39, 5, 16, 9, lit ? 'var(--px-screen)' : 'var(--px-glass)') + R(45, 15, 4, 2, 'var(--px-frame)') +
    R(59, 16, 4, 8, 'var(--px-wood-2)') + R(58, 9, 6, 7, 'var(--px-leaf)'),
  gym: () =>
    R(4, 18, 22, 3, 'var(--px-frame)') + R(22, 8, 2, 10, 'var(--px-frame)') + R(20, 7, 6, 2, 'var(--px-accent)') +
    R(36, 20, 14, 1, 'var(--px-frame)') + R(34, 18, 3, 5, 'var(--px-accent-2)') + R(49, 18, 3, 5, 'var(--px-accent-2)') + R(55, 4, 6, 18, 'var(--px-glass)'),
  rooftop: () =>
    R(4, 6, 12, 14, 'var(--px-frame)') + R(4, 4, 12, 2, 'var(--px-wood-2)') + R(6, 20, 2, 4, 'var(--px-wood-2)') + R(12, 20, 2, 4, 'var(--px-wood-2)') +
    R(40, 2, 1, 16, 'var(--px-frame)') + R(37, 4, 7, 1, 'var(--px-frame)') + R(40, 1, 1, 1, 'var(--px-accent)') +
    R(52, 18, 10, 6, 'var(--px-wood)') + R(53, 13, 8, 5, 'var(--px-leaf)'),
};

const svg = (kind, lit) => `<svg class="furni" viewBox="0 0 64 24" shape-rendering="crispEdges" aria-hidden="true" preserveAspectRatio="xMidYMax meet">${(FURNITURE[kind] || (() => ''))(lit)}</svg>`;

/** 白天 / 黄昏 / 夜里（决定天空和窗户灯） */
export function phase(hour) {
  if (hour >= 7 && hour < 17) return 'day';
  if (hour >= 17 && hour < 19) return 'dusk';
  if (hour >= 5 && hour < 7) return 'dawn';
  return 'night';
}

/** 住户现在在哪儿、干什么的一句话 */
export function whereText(r, dormName) {
  const at = r.room === r.dorm ? `在${dormName || '宿舍'}` : ROOM_VERB[r.room] || '';
  return `${at}${r.activity_label}`;
}

export class Apartment {
  constructor(root, { api, now = () => new Date(), openSession = null, chatWith = null } = {}) {
    this.root = root;
    this.api = api;
    this.now = now;
    this.openSession = openSession;
    this.chatWith = chatWith;
    this.art = null;
    this.data = null;
    this.selected = null;
    this.timer = null;
    this.error = '';
    root.addEventListener('click', (e) => this.onClick(e));
    root.addEventListener('keydown', (e) => this.onKey(e));
  }

  setArt(art) {
    this.art = art && art.available ? art : null;
    if (this.data) this.render();
  }

  async show() {
    this.root.hidden = false;
    await this.load();
    clearInterval(this.timer);
    this.timer = setInterval(() => this.load(), REFRESH_MS);
  }

  hide() {
    this.root.hidden = true;
    clearInterval(this.timer);
    this.timer = null;
  }

  async load() {
    const t = this.now();
    // 后端按周一 = 0；JS 的 getDay() 周日 = 0
    const weekday = (t.getDay() + 6) % 7;
    try {
      this.data = await this.api(t.getHours(), weekday);
      this.error = '';
    } catch (e) {
      this.error = e.message || '加载失败';
    }
    this.hour = t.getHours();
    this.clock = t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    this.render();
  }

  look(r) {
    const memberId = this.art?.models?.[r.model_ids[0]];
    return (this.art?.members || []).find((m) => m.id === memberId) || null;
  }

  // i：在这个房间里的第几位；n：房间里一共几位。均匀排开，各自的走动节奏按住户序号错开
  tenant(r, i, n) {
    const m = this.look(r);
    const color = m?.theme_color || 'var(--brass)';
    const name = m?.display || r.name;
    const img = m?.images?.chibi
      ? `<img src="${esc(m.images.chibi)}" alt="" loading="lazy" draggable="false">`
      : `<span class="ini" style="background:${esc(color)}">${esc(name.slice(0, 1))}</span>`;
    const dorms = this.dormNames();
    const label = `${name}（${ROLE[r.role] || r.role}）：${whereText(r, dorms[r.dorm])}`;
    const x = n <= 1 ? 50 : 10 + (80 * i) / (n - 1);
    const k = (this.data?.residents || []).indexOf(r);
    const pressed = this.selected === r.id;
    return `<button type="button" class="tenant act-${esc(r.activity)}${pressed ? ' on' : ''}" data-r="${esc(r.id)}" aria-pressed="${pressed}" aria-label="${esc(label)}" title="${esc(label)}" style="--x:${x.toFixed(1)}%;--d:${6 + (k % 4) * 1.7}s;--c:${esc(color)}">${img}<i class="bub">${BUBBLES[r.activity] || ''}</i><b class="tag">${esc(name)}</b></button>`;
  }

  dormNames() {
    const map = {};
    for (const floor of this.data?.floors || []) for (const room of floor) if (room.kind === 'dorm') map[room.id] = room.name;
    return map;
  }

  room(room, people, lit) {
    const d = this.data;
    const owner = room.kind === 'dorm' ? (d.residents || []).find((r) => r.dorm === room.id) : null;
    const title = owner ? `${room.name} · ${this.look(owner)?.display || owner.name}` : room.name;
    const busy = people.some((p) => p.activity === 'work' || p.activity === 'wait');
    let sign = '';
    if (room.kind === 'meeting' && d.hidden_meetings) sign = `<span class="sign" title="匿名会议不显示是谁">匿名会议进行中 ×${d.hidden_meetings}</span>`;
    const tenants = people.map((p, i) => this.tenant(p, i, people.length)).join('');
    return `<div class="room k-${esc(room.kind)}${busy ? ' busy' : ''}${people.length ? '' : ' empty'}" data-room="${esc(room.id)}"><span class="rname">${esc(title)}</span>${sign}${svg(room.kind, lit || busy)}<div class="people">${tenants}</div></div>`;
  }

  side() {
    const d = this.data;
    const dorms = this.dormNames();
    const sel = (d.residents || []).find((r) => r.id === this.selected);
    let h = `<div class="clock"><span class="big">${esc(this.clock)}</span><span class="hint">按你电脑的时间作息；30 秒刷新一次</span></div>`;
    if (sel) {
      const m = this.look(sel);
      h += `<div class="card" id="resident-card"><div class="who">${m?.images?.avatar_128 ? `<img src="${esc(m.images.avatar_128)}" alt="">` : ''}<div><b>${esc(m?.display || sel.name)}</b><div class="hint">${esc(ROLE[sel.role] || sel.role)} · 住 ${esc(dorms[sel.dorm] || '')}</div></div></div>`;
      h += `<p>${esc(whereText(sel, dorms[sel.dorm]))}</p>`;
      h += `<p class="hint">本月工资 $${Number(sel.month_paid_usd || 0).toFixed(4)} · ${sel.model_ids.length} 种工作模式</p>`;
      if (sel.session_id) h += `<button type="button" class="btn" data-open="${esc(sel.session_id)}">去看 TA 在办的事</button> `;
      if (this.chatWith) h += `<button type="button" class="btn" data-chat="${esc(sel.name)}">在群聊里 @ TA</button>`;
      h += '</div>';
    }
    h += '<ul class="roster">';
    for (const r of d.residents || []) {
      const m = this.look(r);
      h += `<li><button type="button" class="link${this.selected === r.id ? ' on' : ''}" data-r="${esc(r.id)}"><span><b>${esc(m?.display || r.name)}</b>${r.role === 'manager' ? ' <span class="pill">经理</span>' : ''}</span><span class="hint">${esc(whereText(r, dorms[r.dorm]))}</span></button></li>`;
    }
    h += '</ul><p class="hint">住户就是办公室的员工；同一位的全力 / 节电两种模式住一间。这里只是看看，不调用模型、不花钱。</p>';
    return h;
  }

  render() {
    if (!this.data) {
      this.root.innerHTML = `<p class="empty">${esc(this.error || '加载中…')}</p>`;
      return;
    }
    const d = this.data;
    const p = phase(this.hour ?? 12);
    const lit = p === 'night' || p === 'dusk';
    const where = new Map();
    for (const r of d.residents || []) {
      const list = where.get(r.room) || [];
      list.push(r);
      where.set(r.room, list);
    }
    let floors = '';
    for (const floor of d.floors || []) {
      floors += `<div class="floor${floor[0]?.kind === 'rooftop' ? ' roof' : ''}">${floor.map((room) => this.room(room, where.get(room.id) || [], lit)).join('')}</div>`;
    }
    const err = this.error ? `<p class="hint badp" role="alert">${esc(this.error)}</p>` : '';
    this.root.innerHTML = `<div class="sky ph-${p}"><div class="bldg" aria-label="公寓楼">${floors}<div class="ground"></div></div></div><aside class="home-side" aria-label="住户">${err}${this.side()}</aside>`;
  }

  onClick(e) {
    const open = e.target.closest('[data-open]');
    if (open && this.openSession) return this.openSession(open.dataset.open);
    const talk = e.target.closest('[data-chat]');
    if (talk && this.chatWith) return this.chatWith(talk.dataset.chat);
    const r = e.target.closest('[data-r]');
    if (!r) return;
    this.selected = this.selected === r.dataset.r ? null : r.dataset.r;
    this.render();
    this.root.querySelector(`.tenant[data-r="${CSS.escape(r.dataset.r)}"]`)?.focus({ preventScroll: true });
  }

  onKey(e) {
    if (e.key === 'Escape' && this.selected) {
      this.selected = null;
      this.render();
    }
  }
}
