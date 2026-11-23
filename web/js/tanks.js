// 「铁甲突围」的画面和操作：画布画地图和坦克（全部现场画，不用图片素材），键盘 / 手机触屏操作，
// 每秒 60 帧推进 tanks_core.js 的规则。成员的坦克由本地电脑开（不调模型、不花钱）。
// 手感：开炮有火光、坦克被击毁震屏加碎片、砖块碎屑、履带印、挡下炮弹闪一下；成员头顶冒小气泡说话（台词来自配置）。

import { BACKSLASH, BRICK, BUSH, H, SLASH, STEEL, W, WATER, createGame, placeOf, step } from './tanks_core.js';
import { sfx } from './arcade_sfx.js';
import { esc } from './util.js';

const COLORS = ['#e8c278', '#5fc4b8', '#e98a7a', '#a99be8'];
// 组队时按队伍上色：左边一队暖色，右边一队冷色（按坐的角分深浅）
const TEAM_COLORS = { 0: '#e8c278', 2: '#e98a5a', 1: '#5fc4b8', 3: '#7fa6e8' };
const MINE_KEYS = new Set(['KeyE', 'KeyK']);
// 成员没配台词时用的通用台词
const LINES = {
  start: ['来吧！', '这局我要赢', '准备好了'],
  kill: ['打中了！', '漂亮', '就这？'],
  die: ['啊！', '下次再算账', '大意了'],
  power: ['捡到好东西', '嘿嘿', '运气不错'],
  win: ['赢啦！', '谢谢大家'],
  ally: ['掩护你', '一起上！'],
};
const KEYS = { ArrowUp: 'up', KeyW: 'up', ArrowDown: 'down', KeyS: 'down', ArrowLeft: 'left', KeyA: 'left', ArrowRight: 'right', KeyD: 'right' };
const FIRE = new Set(['Space', 'KeyJ', 'Enter']);
const POWER_ZH = { shield: '盾', rapid: '连', repair: '修', pierce: '穿', speed: '速', mine: '雷' };
const POWER_TIP = { shield: '护盾', rapid: '连发', repair: '修理 +1 命', pierce: '穿甲（能打碎钢墙）', speed: '加速', mine: '地雷 ×2' };
const POWER_BG = { shield: '#3c7fb8', rapid: '#c08a1e', repair: '#3f8f5a', pierce: '#8a4fb8', speed: '#2f9aa0', mine: '#a8433a' };
const ANGLE = { up: 0, right: Math.PI / 2, down: Math.PI, left: -Math.PI / 2 };

export class TankGame {
  /**
   * @param {{players: {id: string, name: string, human?: boolean, team?: number}[], seed: number, lives: number,
   *   minutes: number, level: string, mode?: 'ffa'|'team', lines?: Record<string, Record<string, string[]>>,
   *   onEnd: (r: {place: number|null, kills: number, rank: object[]}) => void}} opts
   */
  constructor(opts) {
    this.opts = opts;
    this.g = createGame(opts.players, { seed: opts.seed, lives: opts.lives, seconds: Math.round(opts.minutes * 60), level: opts.level, mode: opts.mode || 'ffa', bounce: opts.bounce !== false, maze: opts.maze !== false });
    this.me = opts.players.find((p) => p.human)?.id || null;
    this.lines = opts.lines || {};
    this.held = [];
    this.fire = false;
    this.mine = false;
    this.fx = { shake: 0, flashes: [], bits: [], tracks: [], bubbles: [], blink: {}, stop: 0, trackAt: 0 };
    this.saidAt = {};
    this.lineRand = mulberry(opts.seed ^ 0x51ed);
    this.paused = false;
    this.ended = false;
    this.last = 0;
    this.acc = 0;
    this.raf = 0;
    this.el = document.createElement('div');
    this.el.className = 'ac-tanks';
    this.el.innerHTML = `<div class="ac-thud" data-thud></div>
      <div class="ac-tstage"><canvas class="ac-tcanvas" aria-label="铁甲突围战场" role="img"></canvas><div class="ac-tover" data-tover hidden></div></div>
      ${this.me ? `<div class="ac-tpad" aria-label="触屏操作">
        <div class="ac-tdpad">${['up', 'left', 'right', 'down'].map((d) => `<button type="button" class="ac-tkey k-${d}" data-tdir="${d}" aria-label="${{ up: '上', down: '下', left: '左', right: '右' }[d]}"></button>`).join('')}</div>
        <div class="ac-tbtns"><button type="button" class="ac-tmine" data-tmine aria-label="放地雷">地雷</button><button type="button" class="ac-tfire" data-tfire aria-label="开炮">开炮</button></div>
      </div>` : ''}
      <p class="ac-sub ac-ttip">${this.me ? '方向键 / WASD 开动，空格 / J 开炮，E / K 放地雷，P 暂停。手机上用下面的方向键、「开炮」「地雷」。' : '你在旁观：成员们自己开（本地电脑操控）。'}${this.g.mode === 'team' ? '2 对 2：左边两个角一队、右边两个角一队，炮弹打不伤队友。' : ''}${this.g.bounce ? '炮弹碰到钢墙和边框会弹回来（最多两次），碰到斜板拐弯；小心被自己弹回来的炮弹打中。' : ''}草丛能藏身，砖墙打两下就碎，钢墙只有穿甲弹打得碎，水挡车不挡炮弹。补给：盾 护盾、连 连发、修 多一条命、穿 穿甲、速 加速、雷 两颗地雷。</p>`;
    this.canvas = this.el.querySelector('canvas');
    this.ctx = this.canvas.getContext('2d');
    this.hud = this.el.querySelector('[data-thud]');
    this.over = this.el.querySelector('[data-tover]');
    this.onKey = (e) => this.key(e);
    this.onResize = () => this.resize();
    this.bindPad();
  }

  start() {
    window.addEventListener('keydown', this.onKey);
    window.addEventListener('keyup', this.onKey);
    window.addEventListener('resize', this.onResize);
    this.resize();
    this.last = performance.now();
    const loop = (now) => {
      this.raf = requestAnimationFrame(loop);
      this.frame(now);
    };
    this.raf = requestAnimationFrame(loop);
    this.drawHud();
  }

  stop() {
    cancelAnimationFrame(this.raf);
    window.removeEventListener('keydown', this.onKey);
    window.removeEventListener('keyup', this.onKey);
    window.removeEventListener('resize', this.onResize);
  }

  // --- 操作 ----------------------------------------------------------------------

  key(e) {
    if (!this.me || this.ended || !this.el.isConnected) return;
    const tag = e.target?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    const down = e.type === 'keydown';
    const dir = KEYS[e.code];
    if (dir) {
      e.preventDefault();
      this.held = this.held.filter((d) => d !== dir);
      if (down) this.held.push(dir);
    } else if (FIRE.has(e.code)) {
      e.preventDefault();
      this.fire = down;
    } else if (MINE_KEYS.has(e.code)) {
      e.preventDefault();
      this.mine = down;
    } else if (e.code === 'KeyP' && down) {
      this.togglePause();
    }
  }

  bindPad() {
    const pad = this.el.querySelector('.ac-tpad');
    if (!pad) return;
    const press = (e, on) => {
      const b = e.target.closest('[data-tdir],[data-tfire],[data-tmine]');
      if (!b) return;
      e.preventDefault();
      if (b.dataset.tfire !== undefined) this.fire = on;
      else if (b.dataset.tmine !== undefined) this.mine = on;
      else {
        const d = b.dataset.tdir;
        this.held = this.held.filter((x) => x !== d);
        if (on) this.held.push(d);
      }
      b.classList.toggle('on', on);
    };
    pad.addEventListener('pointerdown', (e) => {
      e.target.setPointerCapture?.(e.pointerId);
      press(e, true);
    });
    for (const ev of ['pointerup', 'pointercancel', 'lostpointercapture']) pad.addEventListener(ev, (e) => press(e, false));
  }

  togglePause() {
    if (this.ended) return;
    this.paused = !this.paused;
    this.drawHud();
  }

  // --- 推进 ----------------------------------------------------------------------

  frame(now) {
    const dt = Math.min(0.1, (now - this.last) / 1000);
    this.last = now;
    if (!this.paused && !this.ended) {
      // 击毁的一瞬间顿一下（更有打击感）
      if (this.fx.stop > 0) this.fx.stop -= dt;
      else this.acc += dt;
      const inputs = this.me ? { [this.me]: { dir: this.held[this.held.length - 1] || null, fire: this.fire, mine: this.mine } } : {};
      let n = 0;
      while (this.acc >= 1 / 60 && n < 6) {
        if (!this.started) this.greet();
        step(this.g, 1 / 60, inputs);
        this.sounds();
        this.effects();
        this.acc -= 1 / 60;
        n += 1;
        if (this.g.over) break;
      }
      if (n) this.drawHud();
      if (this.g.over) this.end();
    }
    this.tickFx(this.paused ? 0 : dt);
    this.draw(now / 1000);
  }

  // --- 手感：火光、碎片、履带印、震屏、气泡 -------------------------------------------

  /** 开局几位成员各说一句（错开一点）。 */
  greet() {
    this.started = true;
    const bots = this.g.tanks.filter((t) => !t.human);
    bots.forEach((t, k) => setTimeout(() => this.say(t.id, 'start', 1), 300 + k * 700));
    if (this.g.mode === 'team') {
      const ally = bots.find((t) => this.me && t.team === this.g.tanks.find((x) => x.id === this.me)?.team);
      if (ally) setTimeout(() => this.say(ally.id, 'ally', 1), 300 + bots.length * 700);
    }
  }

  /** 让一位成员冒个气泡（chance 是说话的概率；每位 2.5 秒内最多一句）。 */
  say(id, kind, chance = 0.6) {
    const t = this.g.tanks.find((x) => x.id === id);
    if (!t || t.human || this.lineRand() > chance) return;
    const now = this.g.time;
    if (now - (this.saidAt[id] ?? -9) < 2.5 && kind !== 'win') return;
    const own = this.lines[id]?.[kind];
    const pool = own?.length ? own : LINES[kind] || [];
    if (!pool.length) return;
    this.saidAt[id] = now;
    this.fx.bubbles = this.fx.bubbles.filter((b) => b.id !== id);
    this.fx.bubbles.push({ id, text: pool[Math.floor(this.lineRand() * pool.length)], t: 2.4 });
  }

  effects() {
    const fx = this.fx;
    for (const e of this.g.events) {
      if (e.kind === 'shoot') {
        fx.flashes.push({ x: e.x, y: e.y, dir: e.dir, t: 0.09 });
        if (e.id === this.me) fx.shake = Math.max(fx.shake, 0.06);
      } else if (e.kind === 'brick' || e.kind === 'steel') {
        const col = e.kind === 'brick' ? ['#b0633f', '#7d4430', '#d08a5a'] : ['#c5ced8', '#8994a0'];
        this.burst(e.sx, e.sy, e.broke ? 10 : 4, col, e.broke ? 3.2 : 2);
        if (e.broke) fx.shake = Math.max(fx.shake, 0.1);
      } else if (e.kind === 'kill') {
        this.burst(e.x, e.y, 22, ['#ffd27a', '#ff9a4a', '#5a5f66', '#2a2f36'], 5);
        fx.shake = Math.max(fx.shake, e.id === this.me || e.by === this.me ? 0.5 : 0.3);
        if (e.by === this.me || e.id === this.me) fx.stop = 0.07;
        this.say(e.by, 'kill', 0.75);
        this.say(e.id, 'die', 0.55);
      } else if (e.kind === 'block' || e.kind === 'friendly') {
        fx.blink[e.id] = 0.15;
        this.burst(e.x, e.y, 5, ['#bfe3ff', '#ffffff'], 2.2);
      } else if (e.kind === 'bounce' || e.kind === 'deflect') {
        this.burst(e.x, e.y, 3, ['#fff3c4', '#ffd27a'], 1.8);
      } else if (e.kind === 'catch') {
        this.burst(e.x, e.y, 4, ['#fff3c4', '#ffffff'], 1.6);
      } else if (e.kind === 'mineboom') {
        fx.shake = Math.max(fx.shake, 0.35);
      } else if (e.kind === 'power') {
        this.say(e.id, 'power', 0.45);
      }
    }
    // 履带印：开动的坦克每隔一小段留一道
    fx.trackAt -= 1 / 60;
    if (fx.trackAt <= 0) {
      fx.trackAt = 0.09;
      for (const t of this.g.tanks) if (t.alive && t.moving) fx.tracks.push({ x: t.x, y: t.y, dir: t.dir, age: 0 });
      if (fx.tracks.length > 400) fx.tracks.splice(0, fx.tracks.length - 400);
    }
  }

  burst(x, y, n, colors, speed) {
    for (let k = 0; k < n; k += 1) {
      const a = Math.random() * Math.PI * 2;
      const v = speed * (0.3 + Math.random() * 0.7);
      this.fx.bits.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, t: 0.45 + Math.random() * 0.4, col: colors[k % colors.length], r: 0.05 + Math.random() * 0.07 });
    }
    if (this.fx.bits.length > 300) this.fx.bits.splice(0, this.fx.bits.length - 300);
  }

  tickFx(dt) {
    const fx = this.fx;
    fx.shake = Math.max(0, fx.shake - dt * 1.6);
    for (const f of fx.flashes) f.t -= dt;
    fx.flashes = fx.flashes.filter((f) => f.t > 0);
    for (const b of fx.bits) {
      b.x += b.vx * dt;
      b.y += b.vy * dt;
      b.vx *= 0.9;
      b.vy *= 0.9;
      b.t -= dt;
    }
    fx.bits = fx.bits.filter((b) => b.t > 0);
    for (const tr of fx.tracks) tr.age += dt;
    fx.tracks = fx.tracks.filter((tr) => tr.age < 5);
    for (const b of fx.bubbles) b.t -= dt;
    fx.bubbles = fx.bubbles.filter((b) => b.t > 0);
    for (const k of Object.keys(fx.blink)) if ((fx.blink[k] -= dt) <= 0) delete fx.blink[k];
  }

  sounds() {
    let shoot = false;
    let ting = false;
    for (const e of this.g.events) {
      if (e.kind === 'shoot' && (e.id === this.me || !shoot)) {
        if (e.id === this.me) sfx('shoot');
        shoot = true;
      } else if (e.kind === 'kill') sfx('hit');
      else if (e.kind === 'brick' && e.broke) sfx('pass');
      else if (e.kind === 'power') sfx('power');
      else if (e.kind === 'block') sfx('chip');
      else if (e.kind === 'steel' && e.broke) sfx('bomb');
      else if (e.kind === 'mine' && e.id === this.me) sfx('pick');
      else if (e.kind === 'mineboom') sfx('bomb');
      else if ((e.kind === 'bounce' || e.kind === 'deflect') && !ting) {
        sfx('ting'); // 弹墙：一帧只响一下
        ting = true;
      }
    }
  }

  end() {
    if (this.ended) return;
    this.ended = true;
    const place = this.me ? placeOf(this.g, this.me) : null;
    const kills = this.me ? this.g.tanks.find((t) => t.id === this.me).kills : 0;
    if (this.me) sfx(place === 1 ? 'win' : 'lose');
    for (const r of this.g.rank || []) if (r.place === 1) this.say(r.id, 'win', 1);
    this.drawHud();
    this.showOver('');
    this.opts.onEnd?.({ place, kills, rank: this.g.rank });
  }

  /** 结束面板（金币结算回来后再填一行）。 */
  showOver(coinLine) {
    const g = this.g;
    const rows = (g.rank || [])
      .map((r) => `<li class="${r.place === 1 ? 'win' : ''}"><span class="ac-tdot" style="background:${this.color(g.tanks.find((t) => t.id === r.id))}"></span>${g.mode === 'team' ? (r.place === 1 ? '胜方' : '败方') : `第 ${r.place} 名`} <b>${esc(r.id === this.me ? '你' : r.name)}</b> 击毁 ${r.kills} 次 · 剩 ${r.lives} 条命</li>`)
      .join('');
    const place = this.me ? placeOf(g, this.me) : null;
    const winners = (g.rank || []).filter((r) => r.place === 1).map((r) => (r.id === this.me ? '你' : r.name));
    const head =
      g.mode === 'team'
        ? this.me
          ? place === 1
            ? '你们队赢了！'
            : '你们队输了'
          : `${esc(winners.join('、'))} 这队赢了`
        : this.me
          ? place === 1
            ? '你赢了！'
            : `你拿了第 ${place} 名`
          : `${esc(g.rank?.[0]?.name || '')} 赢了`;
    this.over.hidden = false;
    this.over.innerHTML = `<div class="ac-tpanel hb-glass">
      <h3>${head}</h3>
      <ol class="ac-trank">${rows}</ol>
      ${coinLine ? `<p class="ac-betres win">${esc(coinLine)}</p>` : ''}
      <div class="ac-row"><button type="button" class="btn" data-act="tanks-back">回大厅</button><button type="button" class="btn primary" data-act="tanks-again">再来一局</button></div>
    </div>`;
  }

  // --- 画 ------------------------------------------------------------------------

  resize() {
    const w = Math.max(240, this.el.clientWidth || this.el.parentElement?.clientWidth || 0);
    const cell = Math.max(10, Math.floor(Math.min(w / W, (window.innerHeight * 0.66) / H)));
    this.cell = cell;
    const dpr = window.devicePixelRatio || 1;
    this.canvas.style.width = `${cell * W}px`;
    this.canvas.style.height = `${cell * H}px`;
    this.canvas.width = Math.round(cell * W * dpr);
    this.canvas.height = Math.round(cell * H * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  drawHud() {
    const g = this.g;
    const left = Math.max(0, g.limit - g.time);
    const mm = Math.floor(left / 60);
    const ss = String(Math.floor(left % 60)).padStart(2, '0');
    const rows = g.tanks
      .map((t) => {
        const hearts = t.lives > 0 ? '♥'.repeat(Math.min(t.lives, 6)) : '出局';
        const buffs = [t.pierce > 0 ? '穿' : '', t.fast > 0 ? '速' : '', t.rapid > 0 ? '连' : '', t.mines > 0 ? `雷×${t.mines}` : ''].filter(Boolean).join(' ');
        const side = g.mode === 'team' ? `<span class="ac-tteam">${t.team === 0 ? '左队' : '右队'}</span>` : '';
        return `<span class="ac-tplayer${t.lives <= 0 && !t.alive ? ' out' : ''}"><span class="ac-tdot" style="background:${this.color(t)}"></span>${side}<b>${esc(t.id === this.me ? '你' : t.name)}</b><span class="ac-thearts">${hearts}</span><span class="ac-tkills">击毁 ${t.kills}</span>${buffs ? `<span class="ac-tbuff">${buffs}</span>` : ''}</span>`;
      })
      .join('');
    const html = `${rows}<span class="ac-grow"></span><span class="ac-ttime">${mm}:${ss}</span>${this.me && !this.ended ? `<button type="button" class="ac-seg ac-sm" data-act="tanks-pause">${this.paused ? '继续' : '暂停'}</button>` : ''}<button type="button" class="btn" data-act="tanks-quit">${this.ended ? '回大厅' : '退出'}</button>`;
    if (html !== this.hudHtml) {
      this.hud.innerHTML = html;
      this.hudHtml = html;
    }
  }

  color(tk) {
    return this.g.mode === 'team' ? TEAM_COLORS[tk.slot] : COLORS[tk.slot];
  }

  draw(t) {
    const { ctx, g } = this;
    const c = this.cell || 20;
    ctx.save();
    if (this.fx.shake > 0 && !reduceMotion()) {
      const a = this.fx.shake * c * 0.5;
      ctx.translate((Math.random() - 0.5) * a, (Math.random() - 0.5) * a);
    }
    ctx.fillStyle = '#23282a';
    ctx.fillRect(0, 0, W * c, H * c);
    ctx.strokeStyle = 'rgba(255,255,255,.035)';
    ctx.lineWidth = 1;
    for (let x = 1; x < W; x += 1) {
      ctx.beginPath();
      ctx.moveTo(x * c + 0.5, 0);
      ctx.lineTo(x * c + 0.5, H * c);
      ctx.stroke();
    }
    for (let y = 1; y < H; y += 1) {
      ctx.beginPath();
      ctx.moveTo(0, y * c + 0.5);
      ctx.lineTo(W * c, y * c + 0.5);
      ctx.stroke();
    }
    for (let y = 0; y < H; y += 1) {
      for (let x = 0; x < W; x += 1) {
        const v = g.map[y][x];
        if (v === BRICK) this.brick(x, y, g.hp[y][x]);
        else if (v === STEEL) this.steel(x, y);
        else if (v === WATER) this.water(x, y, t);
        else if (v === SLASH || v === BACKSLASH) this.mirror(x, y, v === SLASH);
      }
    }
    this.drawTracks();
    for (const m of g.mines) this.mineAt(m, t);
    for (const p of g.powers) this.power(p, t);
    for (const tk of g.tanks) if (tk.alive) this.tank(tk, t);
    for (const s of g.shots) this.shot(s);
    for (const f of this.fx.flashes) this.flash(f);
    for (let y = 0; y < H; y += 1) for (let x = 0; x < W; x += 1) if (g.map[y][x] === BUSH) this.bush(x, y);
    for (const b of g.booms) this.boom(b);
    for (const b of this.fx.bits) this.bit(b);
    for (const tk of g.tanks) if (tk.alive) this.label(tk);
    for (const b of this.fx.bubbles) this.bubble(b);
    ctx.restore();
    if (this.paused) {
      ctx.fillStyle = 'rgba(0,0,0,.45)';
      ctx.fillRect(0, 0, W * c, H * c);
      ctx.fillStyle = '#eceff3';
      ctx.font = `${Math.round(c * 1.2)}px "Noto Serif SC", serif`;
      ctx.textAlign = 'center';
      ctx.fillText('暂停中（按 P 继续）', (W * c) / 2, (H * c) / 2);
    }
  }

  brick(x, y, hp) {
    const { ctx } = this;
    const c = this.cell;
    const px = x * c;
    const py = y * c;
    ctx.fillStyle = hp >= 2 ? '#a3573a' : '#7d4430';
    ctx.fillRect(px, py, c, c);
    ctx.strokeStyle = 'rgba(30,14,8,.55)';
    ctx.lineWidth = Math.max(1, c / 16);
    const h = c / 4;
    for (let k = 0; k < 4; k += 1) {
      ctx.beginPath();
      ctx.moveTo(px, py + k * h);
      ctx.lineTo(px + c, py + k * h);
      ctx.stroke();
      const off = k % 2 ? c / 4 : (c * 3) / 4;
      ctx.beginPath();
      ctx.moveTo(px + off, py + k * h);
      ctx.lineTo(px + off, py + (k + 1) * h);
      ctx.stroke();
    }
    ctx.fillStyle = 'rgba(255,220,190,.12)';
    ctx.fillRect(px, py, c, c / 10);
    if (hp < 2) {
      ctx.strokeStyle = 'rgba(20,10,6,.8)';
      ctx.beginPath();
      ctx.moveTo(px + c * 0.2, py + c * 0.15);
      ctx.lineTo(px + c * 0.45, py + c * 0.5);
      ctx.lineTo(px + c * 0.35, py + c * 0.85);
      ctx.stroke();
    }
  }

  /** 斜板：深色底座上一块斜着的亮钢板（炮弹碰到拐 90 度）。 */
  mirror(x, y, slash) {
    const { ctx } = this;
    const c = this.cell;
    const px = x * c;
    const py = y * c;
    ctx.fillStyle = '#2b333c';
    ctx.fillRect(px + c * 0.06, py + c * 0.06, c * 0.88, c * 0.88);
    ctx.save();
    ctx.lineCap = 'round';
    ctx.strokeStyle = '#7d8a97';
    ctx.lineWidth = c * 0.26;
    ctx.beginPath();
    if (slash) {
      ctx.moveTo(px + c * 0.18, py + c * 0.82);
      ctx.lineTo(px + c * 0.82, py + c * 0.18);
    } else {
      ctx.moveTo(px + c * 0.18, py + c * 0.18);
      ctx.lineTo(px + c * 0.82, py + c * 0.82);
    }
    ctx.stroke();
    ctx.strokeStyle = '#e8eef4';
    ctx.lineWidth = c * 0.08;
    ctx.stroke();
    ctx.restore();
  }

  steel(x, y) {
    const { ctx } = this;
    const c = this.cell;
    const px = x * c;
    const py = y * c;
    const gr = ctx.createLinearGradient(px, py, px + c, py + c);
    gr.addColorStop(0, '#b8c2cc');
    gr.addColorStop(1, '#6d7884');
    ctx.fillStyle = gr;
    ctx.fillRect(px, py, c, c);
    ctx.fillStyle = '#8994a0';
    ctx.fillRect(px + c * 0.18, py + c * 0.18, c * 0.64, c * 0.64);
    ctx.fillStyle = 'rgba(40,46,54,.8)';
    for (const [a, b] of [
      [0.1, 0.1],
      [0.9, 0.1],
      [0.1, 0.9],
      [0.9, 0.9],
    ]) {
      ctx.beginPath();
      ctx.arc(px + a * c, py + b * c, Math.max(1, c * 0.05), 0, Math.PI * 2);
      ctx.fill();
    }
  }

  water(x, y, t) {
    const { ctx } = this;
    const c = this.cell;
    const px = x * c;
    const py = y * c;
    ctx.fillStyle = '#1d5a83';
    ctx.fillRect(px, py, c, c);
    ctx.strokeStyle = 'rgba(170,215,240,.45)';
    ctx.lineWidth = Math.max(1, c / 18);
    for (let k = 0; k < 2; k += 1) {
      const yy = py + c * (0.35 + k * 0.35);
      ctx.beginPath();
      for (let i = 0; i <= 8; i += 1) {
        const xx = px + (c * i) / 8;
        const wave = Math.sin(t * 2.4 + i * 0.9 + x * 1.3 + k) * c * 0.06;
        if (i) ctx.lineTo(xx, yy + wave);
        else ctx.moveTo(xx, yy + wave);
      }
      ctx.stroke();
    }
  }

  bush(x, y) {
    const { ctx } = this;
    const c = this.cell;
    ctx.fillStyle = 'rgba(62,122,58,.93)';
    for (const [a, b, r] of [
      [0.28, 0.3, 0.3],
      [0.72, 0.3, 0.3],
      [0.3, 0.72, 0.3],
      [0.72, 0.72, 0.3],
      [0.5, 0.5, 0.32],
    ]) {
      ctx.beginPath();
      ctx.arc(x * c + a * c, y * c + b * c, r * c, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = 'rgba(130,190,110,.35)';
    ctx.beginPath();
    ctx.arc(x * c + 0.4 * c, y * c + 0.35 * c, 0.12 * c, 0, Math.PI * 2);
    ctx.fill();
  }

  tank(tk, t) {
    const { ctx } = this;
    const c = this.cell;
    const s = c * 0.78;
    ctx.save();
    ctx.translate(tk.x * c, tk.y * c);
    ctx.rotate(ANGLE[tk.dir]);
    // 履带（开动时纹路往后走）
    ctx.fillStyle = '#1b1e22';
    ctx.fillRect(-s / 2, -s / 2, s * 0.24, s);
    ctx.fillRect(s / 2 - s * 0.24, -s / 2, s * 0.24, s);
    ctx.strokeStyle = 'rgba(255,255,255,.18)';
    ctx.lineWidth = Math.max(1, c / 20);
    const roll = tk.moving ? (t * 14) % 1 : 0;
    for (let k = 0; k < 5; k += 1) {
      const yy = -s / 2 + ((k + roll) / 5) * s;
      ctx.beginPath();
      ctx.moveTo(-s / 2, yy);
      ctx.lineTo(-s / 2 + s * 0.24, yy);
      ctx.moveTo(s / 2 - s * 0.24, yy);
      ctx.lineTo(s / 2, yy);
      ctx.stroke();
    }
    // 车身（挡下炮弹那一下闪白）
    const col = this.fx.blink[tk.id] ? '#ffffff' : this.color(tk);
    ctx.fillStyle = col;
    roundRect(ctx, -s * 0.3, -s * 0.38, s * 0.6, s * 0.76, s * 0.1);
    ctx.fill();
    ctx.fillStyle = 'rgba(0,0,0,.18)';
    roundRect(ctx, -s * 0.3, s * 0.12, s * 0.6, s * 0.26, s * 0.08);
    ctx.fill();
    // 炮管和炮塔
    ctx.fillStyle = '#2a2f36';
    ctx.fillRect(-s * 0.07, -s * 0.66, s * 0.14, s * 0.5);
    ctx.fillStyle = shade(col, -0.22);
    ctx.beginPath();
    ctx.arc(0, 0, s * 0.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = 'rgba(255,255,255,.35)';
    ctx.beginPath();
    ctx.arc(-s * 0.06, -s * 0.06, s * 0.06, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    if (tk.shield > 0) {
      ctx.strokeStyle = `rgba(150,210,255,${0.45 + 0.3 * Math.sin(t * 10)})`;
      ctx.lineWidth = Math.max(2, c / 10);
      ctx.beginPath();
      ctx.arc(tk.x * c, tk.y * c, s * 0.72, 0, Math.PI * 2);
      ctx.stroke();
    }
    if (tk.rapid > 0) {
      ctx.fillStyle = '#ffd36b';
      ctx.beginPath();
      ctx.arc(tk.x * c + s * 0.42, tk.y * c - s * 0.42, Math.max(2, c * 0.08), 0, Math.PI * 2);
      ctx.fill();
    }
    if (tk.pierce > 0) {
      ctx.fillStyle = '#c98cf0';
      ctx.beginPath();
      ctx.arc(tk.x * c - s * 0.42, tk.y * c - s * 0.42, Math.max(2, c * 0.08), 0, Math.PI * 2);
      ctx.fill();
    }
    if (tk.fast > 0 && tk.moving) {
      // 加速：车尾拖两道风线
      const [dx, dy] = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] }[tk.dir];
      ctx.strokeStyle = 'rgba(160,240,240,.55)';
      ctx.lineWidth = Math.max(1, c / 16);
      for (const off of [-0.25, 0.25]) {
        const ox = dy ? off : 0;
        const oy = dx ? off : 0;
        ctx.beginPath();
        ctx.moveTo((tk.x - dx * 0.45 + ox) * c, (tk.y - dy * 0.45 + oy) * c);
        ctx.lineTo((tk.x - dx * 0.95 + ox) * c, (tk.y - dy * 0.95 + oy) * c);
        ctx.stroke();
      }
    }
  }

  drawTracks() {
    const { ctx } = this;
    const c = this.cell;
    for (const tr of this.fx.tracks) {
      const a = 0.16 * (1 - tr.age / 5);
      ctx.fillStyle = `rgba(0,0,0,${a})`;
      const along = tr.dir === 'up' || tr.dir === 'down';
      const w = c * 0.14;
      const l = c * 0.22;
      for (const side of [-0.29, 0.29]) {
        const x = (tr.x + (along ? side : 0)) * c;
        const y = (tr.y + (along ? 0 : side)) * c;
        if (along) ctx.fillRect(x - w / 2, y - l / 2, w, l);
        else ctx.fillRect(x - l / 2, y - w / 2, l, w);
      }
    }
  }

  mineAt(m, t) {
    const { ctx } = this;
    const c = this.cell;
    const mine = this.me && this.g.tanks.find((x) => x.id === this.me)?.team === m.team;
    // 自己（和队友）的雷看得清；别人的雷只隐约看得见
    ctx.globalAlpha = mine || !this.me ? 1 : 0.35;
    ctx.fillStyle = '#2b2f35';
    ctx.beginPath();
    ctx.arc(m.x * c, m.y * c, c * 0.22, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,.25)';
    ctx.lineWidth = Math.max(1, c / 20);
    ctx.stroke();
    const on = m.arm > 0 ? Math.sin(t * 20) > 0 : Math.sin(t * 6) > 0;
    ctx.fillStyle = on ? '#ff5a4a' : '#6b2a24';
    ctx.beginPath();
    ctx.arc(m.x * c, m.y * c, c * 0.07, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  flash(f) {
    const { ctx } = this;
    const c = this.cell;
    const [dx, dy] = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] }[f.dir];
    const k = f.t / 0.09;
    const g = ctx.createRadialGradient(f.x * c, f.y * c, 0, f.x * c, f.y * c, c * 0.5);
    g.addColorStop(0, `rgba(255,250,220,${0.95 * k})`);
    g.addColorStop(1, 'rgba(255,170,60,0)');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc((f.x + dx * 0.12) * c, (f.y + dy * 0.12) * c, c * 0.5, 0, Math.PI * 2);
    ctx.fill();
  }

  bit(b) {
    const { ctx } = this;
    const c = this.cell;
    ctx.globalAlpha = Math.min(1, b.t * 2.5);
    ctx.fillStyle = b.col;
    const r = b.r * c;
    ctx.fillRect(b.x * c - r, b.y * c - r, r * 2, r * 2);
    ctx.globalAlpha = 1;
  }

  bubble(b) {
    const tk = this.g.tanks.find((x) => x.id === b.id);
    if (!tk || !tk.alive) return;
    const { ctx } = this;
    const c = this.cell;
    const size = Math.max(11, Math.round(c * 0.55));
    ctx.font = `${size}px "Noto Sans SC", system-ui, sans-serif`;
    const text = b.text.length > 16 ? `${b.text.slice(0, 15)}…` : b.text;
    const w = ctx.measureText(text).width + size;
    const h = size * 1.6;
    let x = tk.x * c - w / 2;
    x = Math.max(2, Math.min(W * c - w - 2, x));
    let y = tk.y * c - c * 1.1 - h;
    if (y < 2) y = tk.y * c + c * 0.7;
    ctx.globalAlpha = Math.min(1, b.t * 3);
    ctx.fillStyle = 'rgba(250,248,242,.95)';
    roundRect(ctx, x, y, w, h, h * 0.4);
    ctx.fill();
    ctx.strokeStyle = this.color(tk);
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = '#2a2a2e';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x + size / 2, y + h / 2 + 1);
    ctx.textBaseline = 'alphabetic';
    ctx.globalAlpha = 1;
  }

  label(tk) {
    const { ctx } = this;
    const c = this.cell;
    ctx.font = `${Math.max(10, Math.round(c * 0.5))}px "Noto Sans SC", system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.fillStyle = 'rgba(0,0,0,.55)';
    const text = tk.id === this.me ? '你' : tk.name;
    ctx.fillText(text, tk.x * c + 1, tk.y * c - c * 0.62 + 1);
    ctx.fillStyle = this.color(tk);
    ctx.fillText(text, tk.x * c, tk.y * c - c * 0.62);
  }

  shot(s) {
    const { ctx } = this;
    const c = this.cell;
    ctx.strokeStyle = 'rgba(255,200,120,.45)';
    ctx.lineWidth = Math.max(2, c * 0.12);
    ctx.beginPath();
    ctx.moveTo(s.x * c, s.y * c);
    ctx.lineTo((s.x - s.dx * 0.5) * c, (s.y - s.dy * 0.5) * c);
    ctx.stroke();
    ctx.fillStyle = '#fff3d6';
    ctx.beginPath();
    ctx.arc(s.x * c, s.y * c, Math.max(2, c * 0.11), 0, Math.PI * 2);
    ctx.fill();
  }

  power(p, t) {
    const { ctx } = this;
    const c = this.cell;
    const pulse = 1 + Math.sin(t * 5) * 0.06;
    const s = c * 0.7 * pulse;
    ctx.save();
    ctx.translate((p.x + 0.5) * c, (p.y + 0.5) * c);
    ctx.fillStyle = POWER_BG[p.kind];
    roundRect(ctx, -s / 2, -s / 2, s, s, s * 0.2);
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,.7)';
    ctx.lineWidth = Math.max(1, c / 14);
    ctx.stroke();
    ctx.fillStyle = '#fff';
    ctx.font = `bold ${Math.round(s * 0.62)}px "Noto Serif SC", serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(POWER_ZH[p.kind], 0, s * 0.04);
    ctx.restore();
    ctx.textBaseline = 'alphabetic';
  }

  boom(b) {
    const { ctx } = this;
    const c = this.cell;
    const k = b.t / 0.6;
    const r = (b.small ? 0.35 : 1.1) * c * (0.4 + k);
    ctx.fillStyle = `rgba(255,${Math.round(190 - 120 * k)},80,${0.75 * (1 - k)})`;
    ctx.beginPath();
    ctx.arc(b.x * c, b.y * c, r, 0, Math.PI * 2);
    ctx.fill();
    if (!b.small) {
      ctx.fillStyle = `rgba(255,240,200,${0.8 * (1 - k)})`;
      ctx.beginPath();
      ctx.arc(b.x * c, b.y * c, r * 0.45, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

function reduceMotion() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

/** 台词用的随机数（和规则的随机分开，不影响对局复现）。 */
function mulberry(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function shade(hex, k) {
  const n = parseInt(hex.slice(1), 16);
  const f = (v) => Math.max(0, Math.min(255, Math.round(v + v * k)));
  return `rgb(${f(n >> 16)},${f((n >> 8) & 255)},${f(n & 255)})`;
}

export { POWER_TIP };
