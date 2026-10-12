// 网页小人：悬浮在页面上的一位成员（/api/members 的 chibi），能拖动、贴边、隐藏。
// 动作和表情由 brain.js 的状态机决定；有 pet.json 逐帧素材的用逐帧播放，其余用 CSS 程序动画。
// 匿名且未揭晓的会话里由 app.js 调 setAllowed(false)：小人整个从页面移除，不留任何素材地址。
import { ACTIONS, Brain, EXPRESSIONS, LONG_PRESS_MS, canDrag, clamp, home, loadJSON, saveJSON, snap } from './brain.js';
import { Reporter, hitPart, lookDir } from './reactions.js';

const KEY = 'roundtable.pet'; // {x, y, edge, hidden, member}
const EGG_KEY = 'roundtable.pet.egg'; // 彩蛋的冷却和每日次数：{动作: {lastAt, day, count}}，只存本机
const PHONE_MAX_W = 600; // 视口不宽于此按手机处理：小人缩小、收在角落
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
  // labels：步骤名表；goto：点气泡 / 菜单「跳到讨论」时调用
  constructor({ root = document.body, storage = () => window.localStorage, now = () => Date.now(), labels = {}, goto = null } = {}) {
    this.root = root;
    this.goto = goto;
    this.reporter = new Reporter({ labels, now });
    this.members = [];
    this.sayTimer = null;
    this.menu = null;
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
    this.onResize = () => {
      this.el?.classList.toggle('phone', this.phone);
      this.place(this.pos, false);
    };
    this.pendingEgg = false; // 任务刚完成、等庆祝结束后掷 task_done 彩蛋
    // 任何用户活动：特殊待机立刻停笔；长时间没动之后回来则掷 user_return 彩蛋
    this.onActivity = () => this.activity();
    this.onMouse = (ev) => {
      this.activity();
      if (ev.pointerType !== 'touch') this.lookAt(ev.clientX, ev.clientY);
    };
    this.onVisible = () => {
      if (document.hidden) return this.pause();
      this.resume();
    };
    this.onOutside = (ev) => {
      if (this.menu && !ev.target.closest?.('.pet-menu, .pet-more')) this.closeMenu();
    };
    this.onKey = (ev) => {
      this.activity();
      if (ev.key === 'Escape') this.closeMenu();
    };
  }

  // 成员素材（/api/members 的结果，失败时为 null）
  setMembers(data) {
    this.members = data && data.available ? (data.members || []).filter((m) => m && m.images && m.images.chibi) : [];
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

  // 圆桌事件（SSE）：小人做动作、换表情、冒气泡
  report(e) {
    const r = this.reporter.react(e);
    if (!r || !this.el) return r;
    if (r.act === 'celebrate') this.pendingEgg = true;
    if (r.act) this.act(r.act, r.actMs ? { ms: r.actMs } : r.ms && !r.feel ? { ms: r.ms } : undefined);
    if (r.feel) this.feel(r.feel, r.ms ? { ms: r.ms } : undefined);
    if (r.say && !this.reporter.muted) this.say(r.say, { urgent: r.urgent });
    return r;
  }

  // 新题目 / 换会话：清掉气泡
  resetReport() {
    this.reporter.reset();
    this.hush();
  }

  // 头顶冒一个气泡；点气泡跳到讨论
  say(text, { ms = 7000, urgent = false } = {}) {
    if (!this.el || !text) return;
    const b = this.el.querySelector('.pet-say');
    b.textContent = text;
    b.hidden = false;
    b.classList.toggle('urgent', !!urgent);
    clearTimeout(this.sayTimer);
    this.sayTimer = setTimeout(() => this.hush(), ms);
  }

  hush() {
    clearTimeout(this.sayTimer);
    const b = this.el && this.el.querySelector('.pet-say');
    if (b) {
      b.hidden = true;
      b.textContent = '';
    }
  }

  activity() {
    const r = this.brain.poke();
    if (r.long) this.rollEgg('user_return');
    if (r.wasSpecial && this.el) this.render();
    return r;
  }

  // 掷彩蛋；命中就按帧数和末帧停留算出播放时长，并把冷却状态存进本机
  rollEgg(trigger) {
    if (!this.el || !this.allowed) return null;
    const busy = this.reporter.running || this.reporter.confirming || !!this.menu || !!this.drag;
    const r = this.brain.rollEgg(trigger, { busy });
    if (r.fired) {
      saveJSON(this.storage, EGG_KEY, this.brain.eggState);
      const egg = this.brain.eggs.find((e) => e.action === r.action);
      if (egg && egg.say) this.say(egg.say, { ms: 4500 });
      this.render();
    }
    return r;
  }

  // 页面切到后台：停掉所有定时器；回来算一次活动
  pause() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.hiddenAt = this.now();
  }

  resume() {
    if (!this.el || this.timer) return;
    const away = this.hiddenAt ? this.now() - this.hiddenAt : 0;
    this.hiddenAt = 0;
    this.timer = setInterval(() => this.step(), TICK_MS);
    // 后台的这段时间也算没操作：时钟补回，再按「回来」处理
    const r = this.brain.poke();
    if (r.long || away >= this.brain.awayMs()) this.rollEgg('user_return');
    this.render();
  }

  // 手机视口：小人缩小、收在角落（CSS 的 .phone 管大小）
  get phone() {
    return this.view().w <= PHONE_MAX_W;
  }

  // 鼠标靠近时看向鼠标（只在待机时，不抢动作）
  lookAt(x, y) {
    if (!this.el || this.drag || this.brain.action !== 'idle' || !this.pos) return;
    const d = lookDir({ ...this.pos, ...this.size() }, { x, y });
    if (d && d !== this.brain.dir) {
      this.brain.dir = d;
      this.render();
    }
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
      <button class="pet-more" type="button" title="小人菜单" aria-label="小人菜单" aria-haspopup="menu">⋯</button>
      <span class="pet-say" role="status" hidden></span>
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
    el.querySelector('.pet-more').addEventListener('click', (e) => {
      e.stopPropagation();
      this.menu ? this.closeMenu() : this.openMenu();
    });
    el.querySelector('.pet-say').addEventListener('click', (e) => {
      e.stopPropagation();
      this.hush();
      this.goto?.();
    });
    el.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      this.openMenu();
    });
    el.addEventListener('pointerdown', (e) => this.down(e));
    el.addEventListener('dblclick', () => this.brain.startDemo());
    this.preload();
    this.configureBehavior();
    this.root.appendChild(el);
    this.place(this.saved.x !== undefined ? this.saved : null, false);
    if (this.saved.edge) el.dataset.edge = this.saved.edge;
    window.addEventListener('resize', this.onResize);
    document.addEventListener('pointermove', this.onMouse, { passive: true });
    document.addEventListener('keydown', this.onKey, { passive: true });
    document.addEventListener('wheel', this.onActivity, { passive: true });
    document.addEventListener('pointerdown', this.onOutside, true);
    document.addEventListener('pointerdown', this.onActivity, { passive: true, capture: true });
    document.addEventListener('touchstart', this.onActivity, { passive: true });
    document.addEventListener('scroll', this.onActivity, { passive: true, capture: true });
    window.addEventListener('focus', this.onActivity);
    document.addEventListener('visibilitychange', this.onVisible);
    this.el.classList.toggle('phone', this.phone);
    this.shown = '';
    this.render();
    this.timer = setInterval(() => this.step(), TICK_MS);
  }

  unmountPet() {
    clearTimeout(this.sayTimer);
    this.closeMenu();
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (this.el) this.el.remove();
    this.el = null;
    this.img = null;
    this.drag = null;
    window.removeEventListener('resize', this.onResize);
    document.removeEventListener('pointermove', this.onMouse);
    document.removeEventListener('keydown', this.onKey);
    document.removeEventListener('wheel', this.onActivity);
    document.removeEventListener('pointerdown', this.onOutside, true);
    document.removeEventListener('pointerdown', this.onActivity, true);
    document.removeEventListener('touchstart', this.onActivity);
    document.removeEventListener('scroll', this.onActivity, true);
    window.removeEventListener('focus', this.onActivity);
    document.removeEventListener('visibilitychange', this.onVisible);
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

  // 这位成员的特殊待机和彩蛋（pet.json 声明、服务端校验过）；每个彩蛋的播放时长按它的帧数算
  configureBehavior() {
    const pet = this.member.pet || {};
    const clipMs = (name) => {
      const c = pet.actions && pet.actions[name];
      if (!c) return undefined;
      return Math.round((c.frames.length / Math.max(1, c.fps)) * 1000 + (c.hold_last_s || 0) * 1000);
    };
    const have = (n) => !!(pet.actions && pet.actions[n]);
    this.brain.configure({
      special: pet.special_idle && have(pet.special_idle.action) ? pet.special_idle : null,
      eggs: (pet.easter_eggs || []).filter((e) => have(e.action)).map((e) => ({ ...e, ms: clipMs(e.action) })),
      eggState: loadJSON(this.storage, EGG_KEY) || {},
    });
    this.brain.busy = () => this.reporter.running || this.reporter.confirming || !!this.menu || !!this.drag;
    this.curtsy = have('curtsy');
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
    if (e.button !== 0 || e.target.closest('.pet-hide, .pet-more, .pet-say, .pet-menu')) return;
    e.preventDefault();
    try {
      this.el.setPointerCapture?.(e.pointerId);
    } catch {
      // 合成事件没有活动指针，忽略
    }
    this.drag = {
      dx: e.clientX - this.pos.x,
      dy: e.clientY - this.pos.y,
      x0: e.clientX,
      y0: e.clientY,
      moved: false,
      type: e.pointerType,
      t0: this.now(),
    };
    const move = (ev) => {
      const d = this.drag;
      if (!d) return;
      if (!d.moved && Math.hypot(ev.clientX - d.x0, ev.clientY - d.y0) < 4) return;
      // 触摸屏：没按够时间就挪动，算轻划，不拖（页面照常滚动）
      if (!d.moved && !canDrag(d.type, this.now() - d.t0)) return void (d.swiped = true);
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
      } else if (!d.swiped) {
        this.tap(d.y0 - this.pos.y);
      }
      this.render();
    };
    this.el.addEventListener('pointermove', move);
    this.el.addEventListener('pointerup', up);
    this.el.addEventListener('pointercancel', up);
  }

  // 轻点：点头顶是摸头，点身体是戳；连戳几下会生气
  tap(relY) {
    this.reporter.wake();
    if (this.brain.oneShot()) return; // 行礼 / 彩蛋 / 庆祝播放中再点：忽略，播完再响应
    if (hitPart(relY, this.size().h) === 'head') {
      if (this.curtsy) {
        this.brain.act('curtsy', { ms: this.clipMs('curtsy') });
        return;
      }
      this.brain.act('idle');
      this.brain.feel('shy', { ms: 2200 });
      this.say('嘿嘿～', { ms: 1800 });
      return;
    }
    if (this.reporter.poke()) {
      this.brain.feel('angry', { ms: 2600 });
      this.say('别戳啦！', { ms: 2000 });
      return;
    }
    this.brain.act('jump');
    this.brain.feel('surprised', { ms: 1200 });
  }

  clipMs(name) {
    const c = this.member.pet && this.member.pet.actions[name];
    return c ? Math.round((c.frames.length / Math.max(1, c.fps)) * 1000) : undefined;
  }

  // --- 点击菜单 -------------------------------------------------------------------------

  openMenu() {
    if (!this.el) return;
    this.closeMenu();
    const name = this.member.display || this.member.name || '小人';
    const items = [
      ['who', '她是谁'],
      ['status', '当前状态'],
      ['goto', '跳到讨论'],
      [this.reporter.muted ? 'unquiet' : 'quiet', this.reporter.muted ? '不用安静了' : '安静一会儿'],
    ];
    if (this.members.length > 1) items.push(['switch', '换个小人']);
    const menu = document.createElement('div');
    menu.className = 'pet-menu';
    menu.setAttribute('role', 'menu');
    const draw = (rows) => {
      menu.textContent = '';
      for (const [id, label] of rows) {
        const b = document.createElement('button');
        b.type = 'button';
        b.setAttribute('role', 'menuitem');
        b.dataset.item = id;
        b.textContent = label;
        b.addEventListener('click', (ev) => {
          ev.stopPropagation();
          this.choose(id, name, draw);
        });
        menu.appendChild(b);
      }
    };
    draw(items);
    this.el.appendChild(menu);
    this.menu = menu;
  }

  closeMenu() {
    if (this.menu) this.menu.remove();
    this.menu = null;
  }

  choose(id, name, draw) {
    if (id === 'switch') {
      draw(this.members.map((m) => [`m:${m.id}`, `${m.display || m.name || m.id}${m.id === this.member.id ? ' ✓' : ''}`]));
      return;
    }
    if (id.startsWith('m:')) {
      this.closeMenu();
      this.setMember(id.slice(2));
      return;
    }
    this.closeMenu();
    if (id === 'who') {
      this.say(`我是${name}，圆桌里的一位 AI 成员。点我的头摸一摸，点身体戳一戳。`, { ms: 8000 });
      this.act('wave');
    } else if (id === 'status') {
      this.say(this.reporter.status, { ms: 6000 });
    } else if (id === 'goto') {
      this.goto?.();
    } else if (id === 'quiet') {
      this.reporter.quiet();
      this.hush();
      this.say('好，我安静一会儿，不冒泡了。', { ms: 2500 });
    } else if (id === 'unquiet') {
      this.reporter.wake();
      this.say('我回来啦。', { ms: 2000 });
    }
  }

  // 换成另一位成员（记在本地，下次打开也用她）
  setMember(id) {
    const m = this.members.find((x) => x.id === id);
    if (!m || (this.member && m.id === this.member.id)) return;
    this.save({ member: id });
    this.member = m;
    this.unmountPet();
    this.sync();
  }

  // --- 每一拍 -------------------------------------------------------------------------

  step() {
    if (!this.el) return;
    this.brain.tick();
    if (this.pendingEgg && this.brain.action === 'idle') {
      this.pendingEgg = false;
      this.rollEgg('task_done');
    }
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
