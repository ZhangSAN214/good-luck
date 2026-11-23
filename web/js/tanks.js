// 「铁甲突围」的画面和操作：画布画地图和坦克（全部现场画，不用图片素材），键盘 / 手机触屏操作，
// 每秒 60 帧推进 tanks_core.js 的规则。成员的坦克由本地电脑开（不调模型、不花钱）。

import { BRICK, BUSH, H, STEEL, W, WATER, createGame, placeOf, step } from './tanks_core.js';
import { sfx } from './arcade_sfx.js';
import { esc } from './util.js';

const COLORS = ['#e8c278', '#5fc4b8', '#e98a7a', '#a99be8'];
const KEYS = { ArrowUp: 'up', KeyW: 'up', ArrowDown: 'down', KeyS: 'down', ArrowLeft: 'left', KeyA: 'left', ArrowRight: 'right', KeyD: 'right' };
const FIRE = new Set(['Space', 'KeyJ', 'Enter']);
const POWER_ZH = { shield: '盾', rapid: '连', repair: '修' };
const POWER_TIP = { shield: '护盾', rapid: '连发', repair: '修理 +1 命' };
const ANGLE = { up: 0, right: Math.PI / 2, down: Math.PI, left: -Math.PI / 2 };

export class TankGame {
  /**
   * @param {{players: {id: string, name: string, human?: boolean}[], seed: number, lives: number,
   *   minutes: number, level: string, onEnd: (r: {place: number|null, kills: number, rank: object[]}) => void}} opts
   */
  constructor(opts) {
    this.opts = opts;
    this.g = createGame(opts.players, { seed: opts.seed, lives: opts.lives, seconds: Math.round(opts.minutes * 60), level: opts.level });
    this.me = opts.players.find((p) => p.human)?.id || null;
    this.held = [];
    this.fire = false;
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
        <button type="button" class="ac-tfire" data-tfire aria-label="开炮">开炮</button>
      </div>` : ''}
      <p class="ac-sub ac-ttip">${this.me ? '方向键 / WASD 开动，空格 / J 开炮，P 暂停。手机上用下面的方向键和「开炮」。' : '你在旁观：成员们自己开（本地电脑操控）。'}草丛能藏身，砖墙打两下就碎，钢墙打不动，水挡车不挡炮弹。补给：盾 = 护盾、连 = 连发、修 = 多一条命。</p>`;
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
    } else if (e.code === 'KeyP' && down) {
      this.togglePause();
    }
  }

  bindPad() {
    const pad = this.el.querySelector('.ac-tpad');
    if (!pad) return;
    const press = (e, on) => {
      const b = e.target.closest('[data-tdir],[data-tfire]');
      if (!b) return;
      e.preventDefault();
      if (b.dataset.tfire !== undefined) this.fire = on;
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
      this.acc += dt;
      const inputs = this.me ? { [this.me]: { dir: this.held[this.held.length - 1] || null, fire: this.fire } } : {};
      let n = 0;
      while (this.acc >= 1 / 60 && n < 6) {
        step(this.g, 1 / 60, inputs);
        this.sounds();
        this.acc -= 1 / 60;
        n += 1;
        if (this.g.over) break;
      }
      if (n) this.drawHud();
      if (this.g.over) this.end();
    }
    this.draw(now / 1000);
  }

  sounds() {
    let shoot = false;
    for (const e of this.g.events) {
      if (e.kind === 'shoot' && (e.id === this.me || !shoot)) {
        if (e.id === this.me) sfx('shoot');
        shoot = true;
      } else if (e.kind === 'kill') sfx('hit');
      else if (e.kind === 'brick' && e.broke) sfx('pass');
      else if (e.kind === 'power') sfx('power');
      else if (e.kind === 'block') sfx('chip');
    }
  }

  end() {
    if (this.ended) return;
    this.ended = true;
    const place = this.me ? placeOf(this.g, this.me) : null;
    const kills = this.me ? this.g.tanks.find((t) => t.id === this.me).kills : 0;
    if (this.me) sfx(place === 1 ? 'win' : 'lose');
    this.drawHud();
    this.showOver('');
    this.opts.onEnd?.({ place, kills, rank: this.g.rank });
  }

  /** 结束面板（金币结算回来后再填一行）。 */
  showOver(coinLine) {
    const g = this.g;
    const rows = (g.rank || [])
      .map((r) => `<li class="${r.place === 1 ? 'win' : ''}"><span class="ac-tdot" style="background:${COLORS[g.tanks.find((t) => t.id === r.id).slot]}"></span>第 ${r.place} 名 <b>${esc(r.id === this.me ? '你' : r.name)}</b> 击毁 ${r.kills} 次 · 剩 ${r.lives} 条命</li>`)
      .join('');
    const place = this.me ? placeOf(g, this.me) : null;
    const head = this.me ? (place === 1 ? '你赢了！' : `你拿了第 ${place} 名`) : `${esc(g.rank?.[0]?.name || '')} 赢了`;
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
        return `<span class="ac-tplayer${t.lives <= 0 && !t.alive ? ' out' : ''}"><span class="ac-tdot" style="background:${COLORS[t.slot]}"></span><b>${esc(t.id === this.me ? '你' : t.name)}</b><span class="ac-thearts">${hearts}</span><span class="ac-tkills">击毁 ${t.kills}</span></span>`;
      })
      .join('');
    const html = `${rows}<span class="ac-grow"></span><span class="ac-ttime">${mm}:${ss}</span>${this.me && !this.ended ? `<button type="button" class="ac-seg ac-sm" data-act="tanks-pause">${this.paused ? '继续' : '暂停'}</button>` : ''}<button type="button" class="btn" data-act="tanks-quit">${this.ended ? '回大厅' : '退出'}</button>`;
    if (html !== this.hudHtml) {
      this.hud.innerHTML = html;
      this.hudHtml = html;
    }
  }

  draw(t) {
    const { ctx, g } = this;
    const c = this.cell || 20;
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
      }
    }
    for (const p of g.powers) this.power(p, t);
    for (const tk of g.tanks) if (tk.alive) this.tank(tk, t);
    for (const s of g.shots) this.shot(s);
    for (let y = 0; y < H; y += 1) for (let x = 0; x < W; x += 1) if (g.map[y][x] === BUSH) this.bush(x, y);
    for (const b of g.booms) this.boom(b);
    for (const tk of g.tanks) if (tk.alive) this.label(tk);
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
    // 车身
    const col = COLORS[tk.slot];
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
  }

  label(tk) {
    const { ctx } = this;
    const c = this.cell;
    ctx.font = `${Math.max(10, Math.round(c * 0.5))}px "Noto Sans SC", system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.fillStyle = 'rgba(0,0,0,.55)';
    const text = tk.id === this.me ? '你' : tk.name;
    ctx.fillText(text, tk.x * c + 1, tk.y * c - c * 0.62 + 1);
    ctx.fillStyle = COLORS[tk.slot];
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
    ctx.fillStyle = { shield: '#3c7fb8', rapid: '#c08a1e', repair: '#3f8f5a' }[p.kind];
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
