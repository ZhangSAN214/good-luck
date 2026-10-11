// 网页小人：悬浮在页面上的一位成员（/api/members 的 chibi），能拖动、贴边、隐藏。
// 动作和表情由 brain.js 的状态机决定；有 pet.json 逐帧素材的用逐帧播放，其余用 CSS 程序动画。
// 匿名且未揭晓的会话里由 app.js 调 setAllowed(false)：小人整个从页面移除，不留任何素材地址。
import { ACTIONS, Brain, EXPRESSIONS, clamp, home, loadJSON, saveJSON, snap } from './brain.js';

const KEY = 'roundtable.pet'; // {x, y, edge, hidden, member}
const TICK_MS = 100;
const WALK_PX_PER_S = 36;

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

// 选一位有 chibi 的成员：本地记住的优先，否则排序最靠前的
export function pickMember(data, preferred) {
  if (!data || !data.available) return null;
  const ok = (data.members || []).filter((m) => m && m.images && m.images.chibi);
  return ok.find((m) => m.id === preferred) || ok[0] || null;
}

export class Pet {
  constructor({ root = document.body, storage = () => window.localStorage, now = () => Date.now() } = {}) {
    this.root = root;
    this.storage = storage;
    this.now = now;
    let calm = false;
    try {
      calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch {
      calm = false;
    }
    this.calm = calm;
    this.brain = new Brain({ now, calm });
    this.saved = loadJSON(storage, KEY) || {};
    this.member = null;
    this.allowed = true;
    this.failed = false;
    this.el = null;
    this.call = null;
    this.timer = null;
    this.pos = null;
    this.shown = '';
    this.frameKey = '';
    this.frameStart = 0;
    this.onResize = () => this.place(this.pos, false);
    this.onActivity = () => this.brain.poke();
  }

  // 成员素材（/api/members 的结果，失败时为 null）
  setMembers(data) {
    this.member = pickMember(data, this.saved.member);
    this.failed = false;
    this.sync();
  }

  // false：当前会话匿名且未揭晓，小人不得出现
  setAllowed(ok) {
    if (this.allowed === !!ok) return;
    this.allowed = !!ok;
    this.sync();
  }

  // 给下一步（和讨论事件联动）用
  act(name, opts) {
    const ok = this.brain.act(name, opts);
    if (ok) this.render();
    return ok;
  }

  feel(name, opts) {
    const ok = this.brain.feel(name, opts);
    if (ok) this.render();
    return ok;
  }

  get visible() {
    return !!this.el;
  }

  sync() {
    const want = this.allowed && !!this.member && !this.failed;
    if (!want) return this.unmount();
    if (this.saved.hidden) {
      this.unmountPet();
      this.mountCall();
    } else {
      this.unmountCall();
      this.mountPet();
    }
  }

  // --- 挂载 / 移除 -------------------------------------------------------------------

  mountPet() {
    if (this.el && this.el.dataset.member === this.member.id) return;
    this.unmountPet();
    const m = this.member;
    const name = m.display || m.name || '';
    const el = document.createElement('div');
    el.className = `pet${this.calm ? ' calm' : ''}`;
    el.dataset.member = m.id;
    el.setAttribute('role', 'group');
    el.setAttribute('aria-label', `小人：${name}（可拖动；双击看全部动作）`);
    if (m.theme_color) el.style.setProperty('--pet-color', m.theme_color);
    el.innerHTML = `
      <button class="pet-hide" type="button" title="隐藏小人" aria-label="隐藏小人">×</button>
      <div class="pet-shadow"></div>
      <div class="pet-body"><div class="pet-pose"><div class="pet-face">
        <img class="pet-img" alt="${esc(name)}" draggable="false" src="${esc(m.images.chibi)}">
        <span class="pet-blush l"></span><span class="pet-blush r"></span>
        <span class="pet-sweat"></span>
      </div></div></div>
      <span class="pet-emote" aria-hidden="true"></span>
      <span class="pet-prop" aria-hidden="true"></span>
      <span class="pet-bubble" aria-hidden="true"><i></i><i></i><i></i></span>
      <span class="pet-confetti" aria-hidden="true">${'<i></i>'.repeat(10)}</span>`;
    this.el = el;
    this.img = el.querySelector('.pet-img');
    this.chibi = m.images.chibi;
    this.img.addEventListener('error', () => {
      // 图片坏了：不显示小人（逐帧图坏了就退回 chibi）
      if (this.img && this.img.getAttribute('src') !== this.chibi) {
        this.frameKey = 'broken';
        this.img.src = this.chibi;
        return;
      }
      this.failed = true;
      this.sync();
    });
    el.querySelector('.pet-hide').addEventListener('click', (e) => {
      e.stopPropagation();
      this.setHidden(true);
    });
    el.addEventListener('pointerdown', (e) => this.down(e));
    el.addEventListener('dblclick', () => this.brain.startDemo());
    this.preload();
    this.root.appendChild(el);
    this.place(this.saved.x !== undefined ? this.saved : null, false);
    if (this.saved.edge) el.dataset.edge = this.saved.edge;
    window.addEventListener('resize', this.onResize);
    for (const ev of ['pointermove', 'keydown', 'wheel']) document.addEventListener(ev, this.onActivity, { passive: true });
    this.shown = '';
    this.render();
    this.timer = setInterval(() => this.step(), TICK_MS);
  }

  unmountPet() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (this.el) this.el.remove();
    this.el = null;
    this.img = null;
    this.drag = null;
    window.removeEventListener('resize', this.onResize);
    for (const ev of ['pointermove', 'keydown', 'wheel']) document.removeEventListener(ev, this.onActivity);
  }

  mountCall() {
    if (this.call) return;
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'pet-call';
    b.textContent = '小人';
    b.title = '叫小人回来';
    b.addEventListener('click', () => this.setHidden(false));
    this.call = b;
    this.root.appendChild(b);
  }

  unmountCall() {
    if (this.call) this.call.remove();
    this.call = null;
  }

  unmount() {
    this.unmountPet();
    this.unmountCall();
  }

  setHidden(hidden) {
    this.save({ hidden });
    if (!hidden) this.brain.act('wave');
    this.sync();
  }

  save(patch) {
    this.saved = { ...this.saved, ...patch };
    saveJSON(this.storage, KEY, this.saved);
  }

  preload() {
    const pet = this.member.pet;
    if (!pet) return;
    for (const group of [pet.actions, pet.expressions]) {
      for (const clip of Object.values(group || {})) {
        for (const src of clip.frames) {
          const i = new Image();
          i.src = src;
        }
      }
    }
  }

  // --- 位置与拖动 ---------------------------------------------------------------------

  size() {
    return { w: this.el.offsetWidth || 120, h: this.el.offsetHeight || 150 };
  }

  view() {
    return { w: document.documentElement.clientWidth || window.innerWidth, h: window.innerHeight };
  }

  place(pos, persist = true) {
    if (!this.el) return;
    const size = this.size();
    const view = this.view();
    this.pos = pos ? clamp(pos, size, view) : home(size, view);
    this.el.style.left = `${this.pos.x}px`;
    this.el.style.top = `${this.pos.y}px`;
    if (persist) this.save({ x: this.pos.x, y: this.pos.y });
  }

  down(e) {
    if (e.button !== 0 || e.target.closest('.pet-hide')) return;
    e.preventDefault();
    this.el.setPointerCapture?.(e.pointerId);
    this.drag = { dx: e.clientX - this.pos.x, dy: e.clientY - this.pos.y, x0: e.clientX, y0: e.clientY, moved: false };
    const move = (ev) => {
      const d = this.drag;
      if (!d) return;
      if (!d.moved && Math.hypot(ev.clientX - d.x0, ev.clientY - d.y0) < 4) return;
      if (!d.moved) {
        d.moved = true;
        this.brain.grab();
        delete this.el.dataset.edge;
        this.el.classList.add('held');
      }
      this.place({ x: ev.clientX - d.dx, y: ev.clientY - d.dy }, false);
      this.render();
    };
    const up = () => {
      this.el?.removeEventListener('pointermove', move);
      this.el?.removeEventListener('pointerup', up);
      this.el?.removeEventListener('pointercancel', up);
      const d = this.drag;
      this.drag = null;
      if (!this.el || !d) return;
      this.el.classList.remove('held');
      if (d.moved) {
        const s = snap(this.pos, this.size(), this.view());
        this.place(s, false);
        if (s.edge) this.el.dataset.edge = s.edge;
        this.save({ x: this.pos.x, y: this.pos.y, edge: s.edge });
        this.brain.drop();
      } else {
        // 轻点一下：开心地跳一下（更多互动在下一步）
        this.brain.act('jump');
        this.brain.feel('happy', { ms: 1200 });
      }
      this.render();
    };
    this.el.addEventListener('pointermove', move);
    this.el.addEventListener('pointerup', up);
    this.el.addEventListener('pointercancel', up);
  }

  // --- 每一拍 -------------------------------------------------------------------------

  step() {
    if (!this.el) return;
    this.brain.tick();
    if (this.brain.action === 'walk' && !this.drag) this.walk();
    this.render();
  }

  walk() {
    const size = this.size();
    const view = this.view();
    const dx = (this.brain.dir * WALK_PX_PER_S * TICK_MS) / 1000;
    const x = this.pos.x + dx;
    if (x <= 0 || x >= view.w - size.w) this.brain.dir = -this.brain.dir; // 走到边就回头
    delete this.el.dataset.edge;
    this.place({ x, y: this.pos.y }, false);
    if (Math.abs(this.pos.x - (this.saved.x ?? -1)) >= 8) this.save({ x: this.pos.x, y: this.pos.y, edge: null });
  }

  render() {
    const el = this.el;
    if (!el) return;
    const st = this.brain.state();
    const key = `${st.action}|${st.face || ''}|${st.blink ? 1 : 0}|${st.dir}`;
    if (key !== this.shown) {
      this.shown = key;
      el.dataset.action = st.action;
      el.dataset.dir = st.dir < 0 ? 'left' : 'right';
      if (st.face) el.dataset.face = st.face;
      else delete el.dataset.face;
      el.classList.toggle('blink', st.blink);
      el.querySelector('.pet-emote').textContent = st.face ? EXPRESSIONS[st.face].emote : '';
      el.title = `${this.member.display || this.member.name || ''} · ${ACTIONS[st.action].label}${
        st.face ? ` · ${EXPRESSIONS[st.face].label}` : ''
      }`;
    }
    this.frames(st);
  }

  // 逐帧：眨眼 > 当前动作 > 当前表情；都没有时用待机帧并叠加程序动画；没有 pet.json 就用 chibi
  frames(st) {
    if (this.frameKey === 'broken') return;
    const pet = this.member.pet;
    const own =
      pet &&
      ((st.blink && pet.actions.blink) || pet.actions[st.action] || (st.face && pet.expressions[st.face]) || null);
    const clip = own || (pet && pet.actions.idle) || null;
    const key = clip ? `${own ? `${st.blink ? 'blink' : st.action}|${st.face || ''}` : 'idle'}` : '';
    if (key !== this.frameKey) {
      this.frameKey = key;
      this.frameStart = this.now();
    }
    this.el.classList.toggle('framed', !!own); // 有专门的帧时不再叠加程序姿势
    const src = clip ? clip.frames[this.frameIndex(clip)] : this.chibi;
    if (this.img.getAttribute('src') !== src) this.img.setAttribute('src', src);
  }

  frameIndex(clip) {
    const n = clip.frames.length;
    const i = Math.floor(((this.now() - this.frameStart) * clip.fps) / 1000);
    return clip.loop ? i % n : Math.min(i, n - 1);
  }
}
