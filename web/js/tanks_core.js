// 「铁甲突围」的规则和电脑对手（原创，不复刻任何现成游戏）：纯逻辑，不碰页面，方便测试。
// 地图每局按种子自动生成（四角出生点、上下左右对称）；砖墙打两下碎、钢墙打不动、水挡车不挡炮弹、草丛能藏身。
// 每辆坦克几条命，被打中掉一条命、过一会儿在出生点重生（带一小会儿护盾）。场上会刷护盾、连发、修理三种补给。
// 最后只剩一辆有命的坦克，或者时间到，按（还活着、击毁数、剩几条命）排名。

export const W = 23;
export const H = 15;
export const EMPTY = 0;
export const BRICK = 1;
export const STEEL = 2;
export const WATER = 3;
export const BUSH = 4;
const TANK = 0.78; // 坦克边长（格）
const SPEED = 3.2; // 格 / 秒
const SHOT_SPEED = 9;
const COOLDOWN = 0.5;
const RESPAWN = 1.6;
const SHIELD_AFTER_SPAWN = 2.2;
const POWER_EVERY = 11;
export const DIRS = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] };
const DIR_NAMES = ['up', 'down', 'left', 'right'];

/** 可复现的随机数（种子相同，地图和电脑的随机都一样）。 */
export function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const SPAWNS = [
  [1, H - 2],
  [W - 2, 1],
  [1, 1],
  [W - 2, H - 2],
];

const blocks = (c) => c === BRICK || c === STEEL || c === WATER;

/** 生成地图：四分之一随机铺，再上下、左右镜像；出生点周围清空；保证四个出生点彼此走得通（砖墙算能打通）。 */
export function makeMap(seed) {
  for (let tries = 0; tries < 30; tries += 1) {
    const r = rng(seed + tries * 7919);
    const g = Array.from({ length: H }, () => new Array(W).fill(EMPTY));
    const hw = Math.ceil(W / 2);
    const hh = Math.ceil(H / 2);
    const put = (x, y, v) => {
      for (const [xx, yy] of [
        [x, y],
        [W - 1 - x, y],
        [x, H - 1 - y],
        [W - 1 - x, H - 1 - y],
      ]) {
        g[yy][xx] = v;
      }
    };
    const clusters = 9 + Math.floor(r() * 4);
    for (let k = 0; k < clusters; k += 1) {
      const roll = r();
      const v = roll < 0.55 ? BRICK : roll < 0.68 ? STEEL : roll < 0.8 ? WATER : BUSH;
      const cw = 1 + Math.floor(r() * 3);
      const ch = 1 + Math.floor(r() * 3);
      const x0 = Math.floor(r() * hw);
      const y0 = Math.floor(r() * hh);
      for (let y = y0; y < Math.min(hh, y0 + ch); y += 1) for (let x = x0; x < Math.min(hw, x0 + cw); x += 1) put(x, y, v);
    }
    // 中间放一圈砖当掩体
    const cx = Math.floor(W / 2);
    const cy = Math.floor(H / 2);
    for (const [dx, dy] of [
      [-1, -1],
      [1, -1],
      [-1, 1],
      [1, 1],
    ]) {
      g[cy + dy][cx + dx] = BRICK;
    }
    g[cy][cx] = STEEL;
    for (const [sx, sy] of SPAWNS) {
      for (let y = sy - 1; y <= sy + 1; y += 1) for (let x = sx - 1; x <= sx + 1; x += 1) if (y >= 0 && y < H && x >= 0 && x < W) g[y][x] = EMPTY;
    }
    if (connected(g)) return g;
  }
  return Array.from({ length: H }, () => new Array(W).fill(EMPTY));
}

function connected(g) {
  const seen = new Set();
  const q = [SPAWNS[0]];
  seen.add(`${SPAWNS[0][0]},${SPAWNS[0][1]}`);
  while (q.length) {
    const [x, y] = q.shift();
    for (const [dx, dy] of Object.values(DIRS)) {
      const nx = x + dx;
      const ny = y + dy;
      if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
      const c = g[ny][nx];
      if (c === STEEL || c === WATER || seen.has(`${nx},${ny}`)) continue;
      seen.add(`${nx},${ny}`);
      q.push([nx, ny]);
    }
  }
  return SPAWNS.every(([x, y]) => seen.has(`${x},${y}`));
}

/**
 * 开一局。players: [{id, name, human}]（最多 4 位，按顺序坐四个角）；
 * opts: {seed, lives, seconds, level: 'easy' | 'normal' | 'hard'}。
 */
export function createGame(players, { seed = 1, lives = 3, seconds = 180, level = 'normal' } = {}) {
  const map = makeMap(seed);
  const hp = map.map((row) => row.map((c) => (c === BRICK ? 2 : 0)));
  const tanks = players.slice(0, 4).map((p, i) => ({
    id: p.id,
    name: p.name,
    human: !!p.human,
    slot: i,
    x: SPAWNS[i][0] + 0.5,
    y: SPAWNS[i][1] + 0.5,
    dir: i % 2 === 0 ? 'up' : 'down',
    lives,
    kills: 0,
    deaths: 0,
    alive: true,
    respawn: 0,
    shield: SHIELD_AFTER_SPAWN,
    rapid: 0,
    cool: 0,
    moving: false,
    brain: { next: 0, dir: null, fire: false, path: null },
  }));
  return {
    map,
    hp,
    tanks,
    shots: [],
    powers: [],
    booms: [],
    events: [],
    time: 0,
    limit: seconds,
    powerAt: POWER_EVERY,
    over: false,
    rank: null,
    rand: rng(seed ^ 0x9e3779b9),
    level,
  };
}

const inside = (x, y) => x >= 0 && y >= 0 && x < W && y < H;
const cellAt = (g, x, y) => (inside(x, y) ? g.map[y][x] : STEEL);

function tankBlocked(g, t, x, y) {
  const h = TANK / 2;
  for (const [px, py] of [
    [x - h, y - h],
    [x + h - 0.001, y - h],
    [x - h, y + h - 0.001],
    [x + h - 0.001, y + h - 0.001],
  ]) {
    if (blocks(cellAt(g, Math.floor(px), Math.floor(py)))) return true;
  }
  for (const o of g.tanks) {
    if (o === t || !o.alive) continue;
    if (Math.abs(o.x - x) < TANK && Math.abs(o.y - y) < TANK) return true;
  }
  return false;
}

/** 推进一帧。inputs: {坦克 id: {dir: 'up'|..|null, fire: bool}}（电脑坦克的不用给，自己想）。 */
export function step(g, dt, inputs = {}) {
  if (g.over) return;
  g.events = [];
  g.time += dt;
  for (const t of g.tanks) {
    if (!t.alive) {
      if (t.lives > 0) {
        t.respawn -= dt;
        if (t.respawn <= 0) spawn(g, t);
      }
      continue;
    }
    t.cool = Math.max(0, t.cool - dt);
    t.shield = Math.max(0, t.shield - dt);
    t.rapid = Math.max(0, t.rapid - dt);
    const inp = t.human ? inputs[t.id] || {} : think(g, t, dt);
    move(g, t, inp.dir, dt);
    if (inp.fire) fire(g, t);
  }
  moveShots(g, dt);
  pickPowers(g);
  g.powerAt -= dt;
  if (g.powerAt <= 0) {
    g.powerAt = POWER_EVERY;
    dropPower(g);
  }
  g.booms = g.booms.filter((b) => (b.t += dt) < 0.6);
  const left = g.tanks.filter((t) => t.lives > 0 || t.alive);
  if (left.length <= 1 || g.time >= g.limit) finish(g);
}

function spawn(g, t) {
  const [sx, sy] = SPAWNS[t.slot];
  t.x = sx + 0.5;
  t.y = sy + 0.5;
  if (tankBlocked(g, t, t.x, t.y)) {
    t.respawn = 0.3; // 出生点被挡住了，等一下
    return;
  }
  t.alive = true;
  t.shield = SHIELD_AFTER_SPAWN;
  t.brain.path = null;
  g.events.push({ kind: 'spawn', id: t.id });
}

function move(g, t, dir, dt) {
  t.moving = false;
  if (!dir || !DIRS[dir]) return;
  const [dx, dy] = DIRS[dir];
  if (dir !== t.dir) {
    // 拐弯时把另一个方向对齐到格子中间（好钻进一格宽的路）
    if (dx) t.y = snap(g, t, t.x, t.y, 'y');
    else t.x = snap(g, t, t.x, t.y, 'x');
    t.dir = dir;
  }
  const nx = t.x + dx * SPEED * dt;
  const ny = t.y + dy * SPEED * dt;
  if (!tankBlocked(g, t, nx, ny)) {
    t.x = nx;
    t.y = ny;
    t.moving = true;
  }
  // 顺手把另一个方向往格子中间靠一点（不容易卡在拐角）
  const axis = dx ? 'y' : 'x';
  const v = t[axis];
  const c = Math.floor(v) + 0.5;
  const nudge = Math.sign(c - v) * Math.min(Math.abs(c - v), SPEED * dt * 0.6);
  if (nudge) {
    const px = axis === 'x' ? v + nudge : t.x;
    const py = axis === 'y' ? v + nudge : t.y;
    if (!tankBlocked(g, t, px, py)) t[axis] = v + nudge;
  }
}

function snap(g, t, x, y, axis) {
  const v = axis === 'x' ? x : y;
  const c = Math.floor(v) + 0.5;
  if (Math.abs(c - v) > 0.42) return v;
  const ok = axis === 'x' ? !tankBlocked(g, t, c, y) : !tankBlocked(g, t, x, c);
  return ok ? c : v;
}

function fire(g, t) {
  const mine = g.shots.filter((s) => s.owner === t.id).length;
  if (t.cool > 0 || mine >= (t.rapid > 0 ? 2 : 1)) return;
  const [dx, dy] = DIRS[t.dir];
  g.shots.push({ owner: t.id, x: t.x + dx * 0.5, y: t.y + dy * 0.5, dx, dy });
  t.cool = t.rapid > 0 ? COOLDOWN / 2.5 : COOLDOWN;
  g.events.push({ kind: 'shoot', id: t.id });
}

function moveShots(g, dt) {
  const sub = 3;
  for (let k = 0; k < sub; k += 1) {
    for (const s of g.shots) {
      if (s.dead) continue;
      s.x += (s.dx * SHOT_SPEED * dt) / sub;
      s.y += (s.dy * SHOT_SPEED * dt) / sub;
      const cx = Math.floor(s.x);
      const cy = Math.floor(s.y);
      const c = cellAt(g, cx, cy);
      if (c === BRICK) {
        g.hp[cy][cx] -= 1;
        if (g.hp[cy][cx] <= 0) g.map[cy][cx] = EMPTY;
        s.dead = true;
        g.booms.push({ x: s.x, y: s.y, t: 0, small: true });
        g.events.push({ kind: 'brick', broke: g.map[cy][cx] === EMPTY });
        continue;
      }
      if (c === STEEL) {
        s.dead = true;
        g.booms.push({ x: s.x, y: s.y, t: 0, small: true });
        g.events.push({ kind: 'steel' });
        continue;
      }
      for (const t of g.tanks) {
        if (!t.alive || t.id === s.owner) continue;
        if (Math.abs(t.x - s.x) < TANK / 2 + 0.06 && Math.abs(t.y - s.y) < TANK / 2 + 0.06) {
          s.dead = true;
          hit(g, t, s.owner);
          break;
        }
      }
    }
    // 两发炮弹撞上就一起没
    for (const a of g.shots) {
      if (a.dead) continue;
      for (const b of g.shots) {
        if (b === a || b.dead || b.owner === a.owner) continue;
        if (Math.abs(a.x - b.x) < 0.25 && Math.abs(a.y - b.y) < 0.25) {
          a.dead = b.dead = true;
          g.booms.push({ x: a.x, y: a.y, t: 0, small: true });
        }
      }
    }
  }
  g.shots = g.shots.filter((s) => !s.dead);
}

function hit(g, t, by) {
  if (t.shield > 0) {
    g.events.push({ kind: 'block', id: t.id });
    return;
  }
  t.lives -= 1;
  t.deaths += 1;
  t.alive = false;
  t.respawn = RESPAWN;
  t.rapid = 0;
  const shooter = g.tanks.find((o) => o.id === by);
  if (shooter) shooter.kills += 1;
  g.booms.push({ x: t.x, y: t.y, t: 0, small: false });
  g.events.push({ kind: 'kill', id: t.id, by });
}

function dropPower(g) {
  if (g.powers.length >= 2) return;
  for (let k = 0; k < 40; k += 1) {
    const x = Math.floor(g.rand() * W);
    const y = Math.floor(g.rand() * H);
    if (g.map[y][x] !== EMPTY) continue;
    if (g.tanks.some((t) => t.alive && Math.abs(t.x - x - 0.5) < 2 && Math.abs(t.y - y - 0.5) < 2)) continue;
    const kinds = ['shield', 'rapid', 'repair'];
    g.powers.push({ x, y, kind: kinds[Math.floor(g.rand() * kinds.length)] });
    g.events.push({ kind: 'drop' });
    return;
  }
}

function pickPowers(g) {
  for (const t of g.tanks) {
    if (!t.alive) continue;
    const p = g.powers.find((q) => Math.abs(q.x + 0.5 - t.x) < 0.7 && Math.abs(q.y + 0.5 - t.y) < 0.7);
    if (!p) continue;
    g.powers = g.powers.filter((q) => q !== p);
    if (p.kind === 'shield') t.shield = Math.max(t.shield, 6);
    else if (p.kind === 'rapid') t.rapid = 8;
    else t.lives += 1;
    g.events.push({ kind: 'power', id: t.id, power: p.kind });
  }
}

function finish(g) {
  g.over = true;
  const order = [...g.tanks].sort((a, b) => Number(b.lives > 0) - Number(a.lives > 0) || b.kills - a.kills || b.lives - a.lives || a.deaths - b.deaths);
  g.rank = order.map((t, k) => ({ id: t.id, name: t.name, place: k + 1, kills: t.kills, lives: Math.max(0, t.lives), human: t.human }));
  g.events.push({ kind: 'over' });
}

/** 某辆坦克这一局排第几（1 起）。 */
export function placeOf(g, id) {
  return g.rank?.find((r) => r.id === id)?.place ?? null;
}

// --- 电脑对手 ------------------------------------------------------------------------

const LEVEL = {
  easy: { react: 0.55, aim: 0.55, dodge: 0.2 },
  normal: { react: 0.32, aim: 0.8, dodge: 0.55 },
  hard: { react: 0.18, aim: 0.95, dodge: 0.85 },
};

/** 两点在同一行 / 列且中间没有钢墙：返回朝向（中间有砖也算，炮弹能打开）。 */
function lineOfFire(g, t, o) {
  const tx = Math.floor(t.x);
  const ty = Math.floor(t.y);
  const ox = Math.floor(o.x);
  const oy = Math.floor(o.y);
  if (tx === ox && ty === oy) {
    const dx = o.x - t.x;
    const dy = o.y - t.y;
    return { dir: Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 'right' : 'left') : dy > 0 ? 'down' : 'up', dist: 0 };
  }
  if (Math.abs(t.x - o.x) < 0.45 && oy !== ty) {
    const d = oy > ty ? 1 : -1;
    for (let y = ty + d; y !== oy; y += d) if (g.map[y][tx] === STEEL) return null;
    return { dir: d > 0 ? 'down' : 'up', dist: Math.abs(oy - ty) };
  }
  if (Math.abs(t.y - o.y) < 0.45 && ox !== tx) {
    const d = ox > tx ? 1 : -1;
    for (let x = tx + d; x !== ox; x += d) if (g.map[ty][x] === STEEL) return null;
    return { dir: d > 0 ? 'right' : 'left', dist: Math.abs(ox - tx) };
  }
  return null;
}

/** 从 (sx, sy) 到 (gx, gy) 的路（格子序列）；砖墙算能走（多花点）。 */
function findPath(g, sx, sy, gx, gy) {
  const key = (x, y) => y * W + x;
  const dist = new Map([[key(sx, sy), 0]]);
  const prev = new Map();
  const open = [[0, sx, sy]];
  while (open.length) {
    open.sort((a, b) => a[0] - b[0]);
    const [d, x, y] = open.shift();
    if (x === gx && y === gy) break;
    if (d > (dist.get(key(x, y)) ?? Infinity)) continue;
    for (const [dx, dy] of Object.values(DIRS)) {
      const nx = x + dx;
      const ny = y + dy;
      if (!inside(nx, ny)) continue;
      const c = g.map[ny][nx];
      if (c === STEEL || c === WATER) continue;
      const nd = d + (c === BRICK ? 3 : 1);
      if (nd < (dist.get(key(nx, ny)) ?? Infinity)) {
        dist.set(key(nx, ny), nd);
        prev.set(key(nx, ny), [x, y]);
        open.push([nd, nx, ny]);
      }
    }
    if (dist.size > W * H * 2) break;
  }
  if (!prev.has(key(gx, gy))) return null;
  const path = [];
  let cur = [gx, gy];
  while (cur && !(cur[0] === sx && cur[1] === sy)) {
    path.unshift(cur);
    cur = prev.get(key(cur[0], cur[1]));
  }
  return path;
}

function danger(g, t) {
  for (const s of g.shots) {
    if (s.owner === t.id) continue;
    const ahead = (t.x - s.x) * s.dx + (t.y - s.y) * s.dy;
    if (ahead <= 0 || ahead > 4) continue;
    const side = s.dx ? Math.abs(t.y - s.y) : Math.abs(t.x - s.x);
    if (side < 0.55) return s;
  }
  return null;
}

/** 电脑坦克这一帧想怎么动：躲炮弹 → 对准了就开炮 → 沿路去找最近的对手，路上的砖墙打开。 */
export function think(g, t, dt) {
  const lv = LEVEL[g.level] || LEVEL.normal;
  const b = t.brain;
  b.next -= dt;
  if (b.next > 0) return { dir: b.dir, fire: b.fire && t.cool <= 0 };
  b.next = lv.react * (0.7 + g.rand() * 0.6);
  b.fire = false;
  const shot = danger(g, t);
  if (shot && g.rand() < lv.dodge) {
    const side = shot.dx ? ['up', 'down'] : ['left', 'right'];
    const pick = side.find((d) => {
      const [dx, dy] = DIRS[d];
      return !tankBlocked(g, t, t.x + dx * 0.6, t.y + dy * 0.6);
    });
    if (pick) {
      b.dir = pick;
      return { dir: pick, fire: false };
    }
  }
  const foes = g.tanks.filter((o) => o !== t && o.alive);
  if (!foes.length) {
    b.dir = null;
    return { dir: null, fire: false };
  }
  // 草丛里的看不清：离得远就当没看见
  const visible = foes.filter((o) => cellAt(g, Math.floor(o.x), Math.floor(o.y)) !== BUSH || Math.hypot(o.x - t.x, o.y - t.y) < 3);
  const pool = visible.length ? visible : foes;
  const target = pool.reduce((a, o) => (Math.hypot(o.x - t.x, o.y - t.y) < Math.hypot(a.x - t.x, a.y - t.y) ? o : a));
  const lof = lineOfFire(g, t, target);
  if (lof && lof.dist <= 12 && g.rand() < lv.aim) {
    if (t.dir !== lof.dir) {
      b.dir = lof.dir;
      t.dir = lof.dir; // 原地转炮口
    }
    b.dir = null;
    b.fire = true;
    return { dir: null, fire: true };
  }
  const tx = Math.floor(t.x);
  const ty = Math.floor(t.y);
  if (!b.path || !b.path.length || g.rand() < 0.25) b.path = findPath(g, tx, ty, Math.floor(target.x), Math.floor(target.y));
  while (b.path && b.path.length && b.path[0][0] === tx && b.path[0][1] === ty) b.path.shift();
  if (!b.path || !b.path.length) {
    b.dir = DIR_NAMES[Math.floor(g.rand() * 4)];
    return { dir: b.dir, fire: g.rand() < 0.3 };
  }
  const [nx, ny] = b.path[0];
  const dir = nx > tx ? 'right' : nx < tx ? 'left' : ny > ty ? 'down' : 'up';
  b.dir = dir;
  // 前面是砖就开炮
  if (g.map[ny][nx] === BRICK) {
    t.dir = dir;
    b.fire = true;
  }
  return { dir, fire: b.fire };
}
