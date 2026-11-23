// 「铁甲突围」的规则和电脑对手（原创，不复刻任何现成游戏）：纯逻辑，不碰页面，方便测试。
// 地图每局按种子自动生成（四角出生点、上下左右对称）；砖墙打两下碎、钢墙打不动、水挡车不挡炮弹、草丛能藏身。
// 每辆坦克几条命，被打中掉一条命、过一会儿在出生点重生（带一小会儿护盾）。
// 场上会刷六种补给：护盾、连发、修理（+1 命）、穿甲（能打碎钢墙）、加速、地雷（捡到两颗，按 E / K 放在身后）。
// 炮弹反弹（默认开）：打到钢墙和地图边会弹回来，最多弹 BOUNCES 次；地图上有「斜板」，炮弹碰到拐 90 度；
// 弹过的炮弹能打中自己（打不伤队友）。关掉反弹时钢墙吃掉炮弹、地图上没有斜板。
// 两种玩法：混战（各自为战）和组队（左边两个角一队、右边两个角一队，打不伤队友）。
// 最后只剩一方有命，或者时间到，按（还活着、击毁数、剩几条命）排名；组队按队伍算。

export const W = 23;
export const H = 15;
export const EMPTY = 0;
export const BRICK = 1;
export const STEEL = 2;
export const WATER = 3;
export const BUSH = 4;
export const SLASH = 5; // 斜板「/」：坦克过不去，炮弹碰到拐弯
export const BACKSLASH = 6; // 斜板「\」
const TANK = 0.78; // 坦克边长（格）
const SPEED = 3.2; // 格 / 秒
const SHOT_SPEED = 9;
const COOLDOWN = 0.5;
const RESPAWN = 1.6;
const SHIELD_AFTER_SPAWN = 2.2;
const POWER_EVERY = 11;
const BUFF = 8; // 连发、穿甲、加速持续几秒
const FAST = 1.45; // 加速时快多少
const MINE_ARM = 0.8; // 地雷放下后多久生效
const MAX_MINES = 3;
export const BOUNCES = 2; // 一发炮弹最多在钢墙 / 地图边上弹几次
const SHOT_LIFE = 2; // 炮弹最多飞几秒（约 18 格；免得满地图乱弹，斜板之间绕圈也会停）
const CATCH = 0.3; // 打出去这么久以内弹回来的，撞到自己算接住
export const POWERS = ['shield', 'rapid', 'repair', 'pierce', 'speed', 'mine'];
const POWER_WEIGHT = { shield: 3, rapid: 3, repair: 2, pierce: 2, speed: 2, mine: 2 };
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

const isMirror = (c) => c === SLASH || c === BACKSLASH;
const blocks = (c) => c === BRICK || c === STEEL || c === WATER || isMirror(c);
/** 斜板让炮弹拐弯：「/」右→上、「\」右→下（屏幕坐标 y 向下）。 */
const turn = (c, dx, dy) => (c === SLASH ? [-dy, -dx] : [dy, dx]);

/** 生成地图：四分之一随机铺，再上下、左右镜像；出生点周围清空；保证四个出生点彼此走得通（砖墙算能打通）。 */
export function makeMap(seed, { bounce = true, maze = true } = {}) {
  for (let tries = 0; tries < 30; tries += 1) {
    const r = rng(seed + tries * 7919);
    const g = maze ? mazeMap(r, bounce) : openMap(r, bounce);
    for (const [sx, sy] of SPAWNS) {
      for (let y = sy - 1; y <= sy + 1; y += 1) for (let x = sx - 1; x <= sx + 1; x += 1) if (y >= 0 && y < H && x >= 0 && x < W) g[y][x] = EMPTY;
    }
    if (connected(g)) return g;
  }
  return Array.from({ length: H }, () => new Array(W).fill(EMPTY));
}

const hw = Math.ceil(W / 2);
const hh = Math.ceil(H / 2);

/** 往四分之一里写一格，同时镜像到另外三个角（斜板跟着翻：左右翻、上下翻各换一次方向）。 */
function mirrorPut(g) {
  const flip = (v) => (v === SLASH ? BACKSLASH : v === BACKSLASH ? SLASH : v);
  return (x, y, v) => {
    for (const [xx, yy, f] of [
      [x, y, 0],
      [W - 1 - x, y, 1],
      [x, H - 1 - y, 1],
      [W - 1 - x, H - 1 - y, 0],
    ]) {
      g[yy][xx] = f ? flip(v) : v;
    }
  };
}

/**
 * 迷宫地图：四分之一里先挖一个迷宫（偶数格是通道、奇数格是墙），再随机拆掉一小部分墙留出环路，
 * 中线上开几个口子连通四块，最后镜像。墙多数是砖（打得穿，越打越开阔），夹着钢墙（反弹用）、
 * 少量水和草丛，柱子上偶尔放斜板。
 */
function mazeMap(r, bounce) {
  const g = Array.from({ length: H }, () => new Array(W).fill(EMPTY));
  const put = mirrorPut(g);
  const q = Array.from({ length: hh }, () => new Array(hw).fill(1)); // 1 = 墙
  const cells = [];
  for (let y = 0; y < hh; y += 2) for (let x = 0; x < hw - 1; x += 2) cells.push([x, y]);
  for (const [x, y] of cells) q[y][x] = 0;
  // 随机深度优先挖通
  const seen = new Set(['0,0']);
  const stack = [[0, 0]];
  while (stack.length) {
    const [x, y] = stack[stack.length - 1];
    const next = [
      [2, 0],
      [-2, 0],
      [0, 2],
      [0, -2],
    ]
      .map(([dx, dy]) => [x + dx, y + dy])
      .filter(([nx, ny]) => nx >= 0 && ny >= 0 && nx < hw - 1 && ny < hh && q[ny][nx] === 0 && !seen.has(`${nx},${ny}`));
    if (!next.length) {
      stack.pop();
      continue;
    }
    const [nx, ny] = next[Math.floor(r() * next.length)];
    q[(y + ny) / 2][(x + nx) / 2] = 0;
    seen.add(`${nx},${ny}`);
    stack.push([nx, ny]);
  }
  // 拆墙留环路（只拆墙段，不拆柱子）；中线（最后一列 / 最后一行）上开口子
  for (let y = 0; y < hh; y += 1) {
    for (let x = 0; x < hw; x += 1) {
      if (q[y][x] !== 1 || (x % 2 === 1 && y % 2 === 1)) continue;
      const axis = x === hw - 1 || y === hh - 1;
      if (r() < (axis ? 0.45 : 0.14)) q[y][x] = 0;
    }
  }
  q[hh - 1][hw - 3] = 0;
  q[hh - 3][hw - 1] = 0;
  for (let y = 0; y < hh; y += 1) {
    for (let x = 0; x < hw; x += 1) {
      if (!q[y][x]) {
        if (r() < 0.06) put(x, y, BUSH);
        continue;
      }
      const pillar = x % 2 === 1 && y % 2 === 1;
      const roll = r();
      let v = pillar ? (roll < 0.55 ? STEEL : BRICK) : roll < 0.62 ? BRICK : roll < 0.86 ? STEEL : roll < 0.93 ? WATER : BUSH;
      put(x, y, v);
    }
  }
  // 每个角放 1～2 块斜板（放在柱子上；不放中线上，中线镜像回自己，斜板翻过来就对不上了）
  if (bounce) {
    const spots = [];
    for (let y = 1; y < hh - 1; y += 2) for (let x = 3; x < hw - 1; x += 2) spots.push([x, y]);
    for (let k = 1 + (r() < 0.5 ? 1 : 0); k > 0 && spots.length; k -= 1) {
      const [x, y] = spots.splice(Math.floor(r() * spots.length), 1)[0];
      put(x, y, r() < 0.5 ? SLASH : BACKSLASH);
    }
  }
  return g;
}

/** 开阔地图（第一版的样子）：四分之一随机撒几堆砖、钢、水、草，中间一个钢十字。 */
function openMap(r, bounce) {
  const g = Array.from({ length: H }, () => new Array(W).fill(EMPTY));
  const put = mirrorPut(g);
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
  // 反弹开着时撒几块斜板（单格，离出生点远一点）：打拐弯炮用
  if (bounce) {
    const n = 2 + Math.floor(r() * 2);
    for (let k = 0; k < n; k += 1) {
      const x = 3 + Math.floor(r() * (hw - 4));
      const y = 2 + Math.floor(r() * (hh - 3));
      put(x, y, r() < 0.5 ? SLASH : BACKSLASH);
    }
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
  return g;
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
      if (c === STEEL || c === WATER || isMirror(c) || seen.has(`${nx},${ny}`)) continue;
      seen.add(`${nx},${ny}`);
      q.push([nx, ny]);
    }
  }
  return SPAWNS.every(([x, y]) => seen.has(`${x},${y}`));
}

/** 组队时每队坐哪两个角：左边（左下、左上）一队，右边（右上、右下）一队。 */
const TEAM_SLOTS = [
  [0, 2],
  [1, 3],
];

/**
 * 开一局。players: [{id, name, human, team?}]（最多 4 位）；
 * opts: {seed, lives, seconds, level: 'easy' | 'normal' | 'hard', mode: 'ffa' | 'team', bounce: 炮弹反弹, maze: 迷宫地图}。
 * 混战按顺序坐四个角；组队时 team 为 0 / 1（没写就前两位一队、后两位一队）。
 */
export function createGame(players, { seed = 1, lives = 3, seconds = 180, level = 'normal', mode = 'ffa', bounce = true, maze = true } = {}) {
  const map = makeMap(seed, { bounce, maze });
  const hp = map.map((row) => row.map((c) => (c === BRICK ? 2 : 0)));
  const team = mode === 'team';
  const used = [0, 0];
  const tanks = players.slice(0, 4).map((p, i) => {
    let slot = i;
    let side = i;
    if (team) {
      side = p.team === 0 || p.team === 1 ? p.team : i < 2 ? 0 : 1;
      if (used[side] >= 2) side = 1 - side;
      slot = TEAM_SLOTS[side][used[side]];
      used[side] += 1;
    }
    return {
      id: p.id,
      name: p.name,
      human: !!p.human,
      slot,
      team: side,
      x: SPAWNS[slot][0] + 0.5,
      y: SPAWNS[slot][1] + 0.5,
      dir: SPAWNS[slot][1] < H / 2 ? 'down' : 'up', // 开局炮口朝中间
      lives,
      kills: 0,
      deaths: 0,
      selfHits: 0,
      alive: true,
      respawn: 0,
      shield: SHIELD_AFTER_SPAWN,
      rapid: 0,
      pierce: 0,
      fast: 0,
      mines: 0,
      mineCool: 0,
      cool: 0,
      moving: false,
      brain: { next: 0, dir: null, fire: false, path: null },
    };
  });
  return {
    map,
    hp,
    tanks,
    mode: team ? 'team' : 'ffa',
    bounce: !!bounce,
    maze: !!maze,
    shots: [],
    powers: [],
    mines: [],
    booms: [],
    events: [],
    time: 0,
    limit: seconds,
    powerAt: POWER_EVERY,
    over: false,
    rank: null,
    winTeam: null,
    rand: rng(seed ^ 0x9e3779b9),
    level,
  };
}

/** 是不是对手（组队时同队不算）。 */
export const foes = (a, b) => a !== b && a.team !== b.team;

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
    t.pierce = Math.max(0, t.pierce - dt);
    t.fast = Math.max(0, t.fast - dt);
    t.mineCool = Math.max(0, t.mineCool - dt);
    const inp = t.human ? inputs[t.id] || {} : think(g, t, dt);
    move(g, t, inp.dir, dt);
    if (inp.fire) fire(g, t);
    if (inp.mine) layMine(g, t);
  }
  moveShots(g, dt);
  tickMines(g, dt);
  pickPowers(g);
  g.powerAt -= dt;
  if (g.powerAt <= 0) {
    g.powerAt = POWER_EVERY;
    dropPower(g);
  }
  g.booms = g.booms.filter((b) => (b.t += dt) < 0.6);
  const sides = new Set(g.tanks.filter((t) => t.lives > 0 || t.alive).map((t) => t.team));
  if (sides.size <= 1 || g.time >= g.limit) finish(g);
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
  const speed = SPEED * (t.fast > 0 ? FAST : 1);
  const nx = t.x + dx * speed * dt;
  const ny = t.y + dy * speed * dt;
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
  const x = t.x + dx * 0.5;
  const y = t.y + dy * 0.5;
  g.shots.push({ owner: t.id, team: t.team, x, y, dx, dy, pierce: t.pierce > 0, left: g.bounce ? BOUNCES : 0, bounced: 0, life: SHOT_LIFE, turned: -1 });
  t.cool = t.rapid > 0 ? COOLDOWN / 2.5 : COOLDOWN;
  g.events.push({ kind: 'shoot', id: t.id, x, y, dir: t.dir });
}

/** 在身后放一颗地雷（要先捡到「雷」）。 */
function layMine(g, t) {
  if (t.mines <= 0 || t.mineCool > 0) return;
  const [dx, dy] = DIRS[t.dir];
  const x = t.x - dx * 0.75;
  const y = t.y - dy * 0.75;
  if (blocks(cellAt(g, Math.floor(x), Math.floor(y)))) return;
  t.mines -= 1;
  t.mineCool = 0.6;
  g.mines.push({ owner: t.id, team: t.team, x, y, arm: MINE_ARM });
  g.events.push({ kind: 'mine', id: t.id, x, y });
}

function tickMines(g, dt) {
  for (const m of g.mines) {
    m.arm = Math.max(0, m.arm - dt);
    if (m.arm > 0) continue;
    const victim = g.tanks.find((t) => t.alive && t.team !== m.team && Math.abs(t.x - m.x) < 0.6 && Math.abs(t.y - m.y) < 0.6);
    if (!victim) continue;
    m.dead = true;
    g.booms.push({ x: m.x, y: m.y, t: 0, small: false });
    g.events.push({ kind: 'mineboom', x: m.x, y: m.y });
    hit(g, victim, m.owner);
  }
  g.mines = g.mines.filter((m) => !m.dead);
}

function moveShots(g, dt) {
  const sub = 3;
  for (const s of g.shots) {
    s.life -= dt;
    if (s.life <= 0) s.dead = true;
  }
  for (let k = 0; k < sub; k += 1) {
    for (const s of g.shots) {
      if (s.dead) continue;
      const px = s.x;
      const py = s.y;
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
        g.events.push({ kind: 'brick', broke: g.map[cy][cx] === EMPTY, x: cx + 0.5, y: cy + 0.5, sx: s.x, sy: s.y });
        continue;
      }
      if (c === STEEL) {
        // 穿甲弹能打碎钢墙（地图边缘外面的不算）
        const broke = s.pierce && inside(cx, cy);
        if (!broke && s.left > 0) {
          // 弹回来：退回墙外，掉头
          s.x = px;
          s.y = py;
          s.dx = -s.dx;
          s.dy = -s.dy;
          s.left -= 1;
          s.bounced += 1;
          s.turned = -1;
          g.events.push({ kind: 'bounce', x: px, y: py });
          continue;
        }
        s.dead = true;
        g.booms.push({ x: s.x, y: s.y, t: 0, small: true });
        if (broke) g.map[cy][cx] = EMPTY;
        g.events.push({ kind: 'steel', broke, x: cx + 0.5, y: cy + 0.5, sx: s.x, sy: s.y });
        continue;
      }
      if (isMirror(c)) {
        // 过了斜板中线就拐弯（同一块斜板只拐一次）
        const key = cy * W + cx;
        const past = (s.x - cx - 0.5) * s.dx + (s.y - cy - 0.5) * s.dy;
        if (s.turned !== key && past >= 0) {
          [s.dx, s.dy] = turn(c, s.dx, s.dy);
          s.x = cx + 0.5 + s.dx * past;
          s.y = cy + 0.5 + s.dy * past;
          s.turned = key;
          s.bounced += 1;
          g.events.push({ kind: 'deflect', x: cx + 0.5, y: cy + 0.5 });
        }
        continue;
      }
      for (const t of g.tanks) {
        if (!t.alive) continue;
        if (t.id === s.owner && !s.bounced) continue; // 刚打出去的炮弹不会打到自己
        if (Math.abs(t.x - s.x) < TANK / 2 + 0.06 && Math.abs(t.y - s.y) < TANK / 2 + 0.06) {
          s.dead = true;
          // 被自己弹回来的炮弹打中；贴着墙打、马上弹回来的算接住了（不然贴墙开炮就是送命）
          if (t.id === s.owner && SHOT_LIFE - s.life < CATCH) g.events.push({ kind: 'catch', id: t.id, x: s.x, y: s.y });
          else if (t.id === s.owner) hit(g, t, s.owner);
          else if (t.team === s.team) g.events.push({ kind: 'friendly', id: t.id, x: s.x, y: s.y }); // 打不伤队友
          else hit(g, t, s.owner);
          break;
        }
      }
    }
    // 两发炮弹撞上就一起没（同队的互相穿过）
    for (const a of g.shots) {
      if (a.dead) continue;
      for (const b of g.shots) {
        if (b === a || b.dead || b.team === a.team) continue;
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
    g.events.push({ kind: 'block', id: t.id, x: t.x, y: t.y });
    return;
  }
  t.lives -= 1;
  t.deaths += 1;
  t.alive = false;
  t.respawn = RESPAWN;
  t.rapid = 0;
  t.pierce = 0;
  t.fast = 0;
  const shooter = g.tanks.find((o) => o.id === by);
  if (shooter && foes(shooter, t)) shooter.kills += 1;
  const self = by === t.id;
  if (self) t.selfHits += 1;
  g.booms.push({ x: t.x, y: t.y, t: 0, small: false });
  g.events.push({ kind: 'kill', id: t.id, by, x: t.x, y: t.y, out: t.lives <= 0, self });
}

function dropPower(g) {
  if (g.powers.length >= 2) return;
  for (let k = 0; k < 40; k += 1) {
    const x = Math.floor(g.rand() * W);
    const y = Math.floor(g.rand() * H);
    if (g.map[y][x] !== EMPTY) continue;
    if (g.tanks.some((t) => t.alive && Math.abs(t.x - x - 0.5) < 2 && Math.abs(t.y - y - 0.5) < 2)) continue;
    const total = POWERS.reduce((a, k) => a + POWER_WEIGHT[k], 0);
    let roll = g.rand() * total;
    const kind = POWERS.find((k) => (roll -= POWER_WEIGHT[k]) < 0) || 'shield';
    g.powers.push({ x, y, kind });
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
    else if (p.kind === 'rapid') t.rapid = BUFF;
    else if (p.kind === 'pierce') t.pierce = BUFF;
    else if (p.kind === 'speed') t.fast = BUFF;
    else if (p.kind === 'mine') t.mines = Math.min(MAX_MINES, t.mines + 2);
    else t.lives += 1;
    g.events.push({ kind: 'power', id: t.id, power: p.kind });
  }
}

function finish(g) {
  g.over = true;
  const score = (list) => [Number(list.some((t) => t.lives > 0)), list.reduce((a, t) => a + t.kills, 0), list.reduce((a, t) => a + Math.max(0, t.lives), 0), -list.reduce((a, t) => a + t.deaths, 0)];
  const better = (a, b) => {
    for (let k = 0; k < a.length; k += 1) if (a[k] !== b[k]) return b[k] - a[k];
    return 0;
  };
  if (g.mode === 'team') {
    // 组队：按队伍排（还有命的队 → 击毁多 → 剩命多 → 被击毁少），同队名次相同
    const teams = [0, 1].map((k) => g.tanks.filter((t) => t.team === k)).filter((l) => l.length);
    teams.sort((a, b) => better(score(a), score(b)));
    g.winTeam = teams[0]?.[0]?.team ?? null;
    g.rank = teams.flatMap((list, k) =>
      [...list].sort((a, b) => better(score([a]), score([b]))).map((t) => ({ id: t.id, name: t.name, place: k + 1, team: t.team, kills: t.kills, lives: Math.max(0, t.lives), human: t.human })),
    );
  } else {
    const order = [...g.tanks].sort((a, b) => better(score([a]), score([b])));
    g.rank = order.map((t, k) => ({ id: t.id, name: t.name, place: k + 1, team: t.team, kills: t.kills, lives: Math.max(0, t.lives), human: t.human }));
  }
  g.events.push({ kind: 'over' });
}

/** 某辆坦克这一局排第几（1 起）。 */
export function placeOf(g, id) {
  return g.rank?.find((r) => r.id === id)?.place ?? null;
}

// --- 电脑对手 ------------------------------------------------------------------------

const LEVEL = {
  easy: { react: 0.55, aim: 0.55, dodge: 0.2, bank: 0 },
  normal: { react: 0.32, aim: 0.8, dodge: 0.55, bank: 1 },
  hard: { react: 0.18, aim: 0.95, dodge: 0.85, bank: 2 },
}; // bank：最多算几次弹墙 / 拐弯的炮

/**
 * 假装朝 dir 开一炮，沿路推演（钢墙和地图边弹回、斜板拐弯、砖墙挡住），看最先碰到谁。
 * 返回 {foe, turns}（打中对手，中途拐了几次）、{self: true}（会打回自己）或 null（打不到人）。
 * 坦克当作不动，只是个估计。ghost=true 时当别的坦克都不在（打空了会不会弹回自己）。
 */
export function plan(g, t, dir, maxCells = 16, ghost = false) {
  let [dx, dy] = DIRS[dir];
  let x = t.x + dx * 0.5;
  let y = t.y + dy * 0.5;
  let left = g.bounce ? BOUNCES : 0;
  let turns = 0;
  let turned = -1;
  const stepLen = 0.25;
  for (let k = 0, n = maxCells / stepLen; k < n; k += 1) {
    const px = x;
    const py = y;
    x += dx * stepLen;
    y += dy * stepLen;
    const cx = Math.floor(x);
    const cy = Math.floor(y);
    const c = cellAt(g, cx, cy);
    if (c === BRICK) return null;
    if (c === STEEL) {
      if (t.pierce > 0 && inside(cx, cy)) return null;
      if (left <= 0) return null;
      left -= 1;
      turns += 1;
      x = px;
      y = py;
      dx = -dx;
      dy = -dy;
      turned = -1;
      continue;
    }
    if (isMirror(c)) {
      const key = cy * W + cx;
      const past = (x - cx - 0.5) * dx + (y - cy - 0.5) * dy;
      if (turned !== key && past >= 0) {
        [dx, dy] = turn(c, dx, dy);
        x = cx + 0.5 + dx * past;
        y = cy + 0.5 + dy * past;
        turned = key;
        turns += 1;
      }
      continue;
    }
    for (const o of g.tanks) {
      if (!o.alive || (ghost && o !== t)) continue;
      if (o === t && (!turns || (k * stepLen) / SHOT_SPEED < CATCH)) continue;
      if (Math.abs(o.x - x) < TANK / 2 + 0.06 && Math.abs(o.y - y) < TANK / 2 + 0.06) {
        if (o === t) return { self: true };
        if (o.team === t.team) return null; // 队友挡着
        return { foe: o, turns };
      }
    }
  }
  return null;
}

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
      if (c === STEEL || c === WATER || isMirror(c)) continue;
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

/** 对手在自己正后方 5 格以内（放地雷的好时机）。 */
function behind(t, o) {
  const [dx, dy] = DIRS[t.dir];
  const back = (t.x - o.x) * dx + (t.y - o.y) * dy;
  const side = dx ? Math.abs(t.y - o.y) : Math.abs(t.x - o.x);
  return back > 0.5 && back < 5 && side < 0.6;
}

/** 开炮方向上、目标之前有没有队友（有就先别打）。 */
function allyInLine(g, t, lof) {
  const [dx, dy] = DIRS[lof.dir];
  return g.tanks.some((o) => {
    if (o === t || !o.alive || o.team !== t.team) return false;
    const ahead = (o.x - t.x) * dx + (o.y - t.y) * dy;
    const side = dx ? Math.abs(o.y - t.y) : Math.abs(o.x - t.x);
    return ahead > 0 && ahead <= lof.dist + 0.5 && side < 0.6;
  });
}

function danger(g, t) {
  for (const s of g.shots) {
    // 对手的炮弹，或者自己弹回来的炮弹（队友的打不伤）
    if (s.team === t.team && !(s.owner === t.id && s.bounced)) continue;
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
  // 自己打出去、弹回来的炮弹心里有数，一定躲；别人的按难度看反应
  if (shot && (shot.owner === t.id || g.rand() < lv.dodge)) {
    const side = shot.dx ? ['up', 'down'] : ['left', 'right'];
    if (g.rand() < 0.5) side.reverse(); // 往哪边躲随机，不偏哪个角
    const pick = side.find((d) => {
      const [dx, dy] = DIRS[d];
      return !tankBlocked(g, t, t.x + dx * 0.6, t.y + dy * 0.6);
    });
    if (pick) {
      b.dir = pick;
      return { dir: pick, fire: false };
    }
  }
  const enemies = g.tanks.filter((o) => foes(t, o) && o.alive);
  if (!enemies.length) {
    b.dir = null;
    return { dir: null, fire: false };
  }
  // 有地雷：有对手跟在身后时放一颗
  if (t.mines > 0 && (enemies.some((o) => behind(t, o)) || (t.moving && g.rand() < 0.08))) return { dir: b.dir, fire: false, mine: true };
  // 草丛里的看不清：离得远就当没看见
  const visible = enemies.filter((o) => cellAt(g, Math.floor(o.x), Math.floor(o.y)) !== BUSH || Math.hypot(o.x - t.x, o.y - t.y) < 3);
  const pool = visible.length ? visible : enemies;
  const target = pool.reduce((a, o) => (Math.hypot(o.x - t.x, o.y - t.y) < Math.hypot(a.x - t.x, a.y - t.y) ? o : a));
  // 先看直线能不能打到，再看（按难度）弹墙、拐弯的炮能不能打到对手；会打回自己的不打
  const lof = lineOfFire(g, t, target);
  let aimDir = lof && lof.dist <= 12 && !allyInLine(g, t, lof) ? lof.dir : null;
  if (aimDir && plan(g, t, aimDir)?.self) aimDir = null;
  if (!aimDir && lv.bank > 0 && g.bounce) {
    for (const d of [t.dir, ...DIR_NAMES.filter((d) => d !== t.dir)]) {
      const p = plan(g, t, d);
      if (p?.foe && p.turns <= lv.bank) {
        aimDir = d;
        break;
      }
    }
  }
  if (aimDir && g.rand() < lv.aim) {
    t.dir = aimDir; // 原地转炮口
    b.dir = null;
    b.fire = true;
    // 打空了会弹回来的：开完炮早点回神，好挪开
    if (plan(g, t, aimDir, 16, true)?.self) b.next = Math.min(b.next, 0.12);
    return { dir: null, fire: true };
  }
  const tx = Math.floor(t.x);
  const ty = Math.floor(t.y);
  // 补给比对手近（6 格以内）就先去捡
  const far = Math.hypot(target.x - t.x, target.y - t.y);
  const near = g.powers.filter((p) => Math.hypot(p.x + 0.5 - t.x, p.y + 0.5 - t.y) < Math.min(6, far));
  const goal = near.length ? [near[0].x, near[0].y] : [Math.floor(target.x), Math.floor(target.y)];
  if (b.goal !== `${goal}`) b.path = null;
  b.goal = `${goal}`;
  if (!b.path || !b.path.length || g.rand() < 0.25) b.path = findPath(g, tx, ty, goal[0], goal[1]);
  while (b.path && b.path.length && b.path[0][0] === tx && b.path[0][1] === ty) b.path.shift();
  if (!b.path || !b.path.length) {
    b.dir = DIR_NAMES[Math.floor(g.rand() * 4)];
    return { dir: b.dir, fire: g.rand() < 0.3 && !plan(g, t, t.dir)?.self };
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
