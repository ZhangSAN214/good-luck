# ruff: noqa: E501  （下面内嵌的是 JS 代码，按 JS 的习惯排版）
"""铁甲突围的纯逻辑（web/js/tanks_core.js）：炮弹反弹、斜板拐弯、被自己弹回的炮弹打中、迷宫地图。

用 node 跑，没有就跳过。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2] / "web" / "js" / "tanks_core.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="需要 node")

# 一块空场地：两辆坦克（都是人操控，不会自己乱动），可以按需摆墙、摆位置
SETUP = """
const T = await import(URL);
function arena(opts = {}) {
  const g = T.createGame(
    [{ id: 'a', name: 'a', human: true }, { id: 'b', name: 'b', human: true }],
    { seed: 3, mode: opts.mode || 'ffa', bounce: opts.bounce !== false },
  );
  g.map = g.map.map((row) => row.map(() => T.EMPTY));
  g.hp = g.map.map((row) => row.map(() => 0));
  for (const t of g.tanks) t.shield = 0;
  return g;
}
function place(t, x, y, dir) { t.x = x + 0.5; t.y = y + 0.5; t.dir = dir; }
function run(g, secs, inputs = {}) {
  const ev = [];
  for (let k = 0; k < secs * 60 && !g.over; k += 1) { T.step(g, 1 / 60, inputs); ev.push(...g.events); inputs = {}; }
  return ev;
}
"""


def run(body: str):
    script = (
        SETUP.replace("URL", json.dumps(CORE.as_uri()))
        + f"const out = await (async () => {{ {body} }})();"
        + "process.stdout.write(JSON.stringify(out));"
    )
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(res.stdout)


def test_shot_bounces_off_steel_and_can_hit_its_own_tank():
    out = run(
        """
        const g = arena();
        const [a, b] = g.tanks;
        place(a, 5, 7, 'right'); place(b, 20, 1, 'down');
        g.map[7][10] = T.STEEL;
        const ev = run(g, 2, { a: { fire: true } });
        return { bounce: ev.filter((e) => e.kind === 'bounce').length,
                 self: ev.some((e) => e.kind === 'kill' && e.id === 'a' && e.self),
                 lives: a.lives, kills: a.kills };
        """
    )
    assert out == {"bounce": 1, "self": True, "lives": 2, "kills": 0}


def test_point_blank_bounce_is_caught_and_bounce_off_means_steel_eats_it():
    out = run(
        """
        const g = arena();
        const [a, b] = g.tanks;
        place(a, 5, 7, 'right'); place(b, 20, 1, 'down');
        g.map[7][6] = T.STEEL;  // 贴着墙开炮
        const ev = run(g, 1, { a: { fire: true } });
        const g2 = arena({ bounce: false });
        place(g2.tanks[0], 5, 7, 'right'); place(g2.tanks[1], 20, 1, 'down');
        g2.map[7][10] = T.STEEL;
        const ev2 = run(g2, 2, { a: { fire: true } });
        return { caught: ev.some((e) => e.kind === 'catch'), lives: a.lives,
                 bounce2: ev2.filter((e) => e.kind === 'bounce').length, lives2: g2.tanks[0].lives,
                 mirrors: T.makeMap(5, { bounce: false }).flat().filter((c) => c === T.SLASH || c === T.BACKSLASH).length };
        """
    )
    assert out == {"caught": True, "lives": 3, "bounce2": 0, "lives2": 3, "mirrors": 0}


def test_mirror_turns_shot_around_the_corner_onto_an_enemy():
    out = run(
        """
        const g = arena();
        const [a, b] = g.tanks;
        place(a, 3, 7, 'right'); place(b, 10, 2, 'down');
        g.map[7][10] = T.SLASH;  // 往右飞的炮弹在这里拐向上，打到上面的 b
        const p = T.plan(g, a, 'right');
        const ev = run(g, 2, { a: { fire: true } });
        return { plan: p && p.foe && p.foe.id, turns: p && p.turns,
                 hit: ev.some((e) => e.kind === 'kill' && e.id === 'b' && e.by === 'a'), kills: a.kills };
        """
    )
    assert out == {"plan": "b", "turns": 1, "hit": True, "kills": 1}


def test_bounced_shot_never_hurts_a_teammate_and_shots_expire():
    out = run(
        """
        const g = T.createGame(
          [{ id: 'a', name: 'a', human: true, team: 0 }, { id: 'c', name: 'c', human: true, team: 0 },
           { id: 'b', name: 'b', human: true, team: 1 }, { id: 'd', name: 'd', human: true, team: 1 }],
          { seed: 3, mode: 'team' });
        g.map = g.map.map((row) => row.map(() => T.EMPTY));
        for (const t of g.tanks) t.shield = 0;
        const [a, c, b, d] = g.tanks;
        place(a, 2, 7, 'up'); place(c, 8, 7, 'up'); place(b, 20, 1, 'down'); place(d, 20, 13, 'up');
        g.map[7][12] = T.STEEL;
        a.dir = 'right';
        const ev = run(g, 3, { a: { fire: true } });
        return { friendly: ev.some((e) => e.kind === 'friendly'), lives: c.lives, shots: g.shots.length };
        """
    )
    assert out == {"friendly": True, "lives": 3, "shots": 0}


def test_maze_maps_are_mirrored_connected_and_mazy():
    out = run(
        """
        const res = [];
        for (let seed = 1; seed <= 30; seed += 1) {
          const m = T.makeMap(seed);
          const flip = (v) => (v === T.SLASH ? T.BACKSLASH : v === T.BACKSLASH ? T.SLASH : v);
          let sym = true;
          for (let y = 0; y < T.H; y += 1) for (let x = 0; x < T.W; x += 1) {
            if (m[y][T.W - 1 - x] !== flip(m[y][x]) || m[T.H - 1 - y][T.W - 1 - x] !== m[y][x]) sym = false;
          }
          const walls = m.flat().filter((c) => c !== T.EMPTY && c !== T.BUSH).length;
          const open = T.makeMap(seed, { maze: false }).flat().filter((c) => c !== T.EMPTY && c !== T.BUSH).length;
          res.push({ sym, walls, open, spawns: T.SPAWNS.every(([x, y]) => m[y][x] === T.EMPTY) });
        }
        return res;
        """
    )
    assert all(r["sym"] and r["spawns"] for r in out)
    # 迷宫比开阔地图墙多
    assert sum(r["walls"] for r in out) > 1.2 * sum(r["open"] for r in out)


def test_bots_play_full_games_with_bounce_and_rarely_shoot_themselves():
    out = run(
        """
        let deaths = 0, self = 0, banked = 0;
        for (let seed = 1; seed <= 12; seed += 1) {
          const g = T.createGame(['a', 'b', 'c', 'd'].map((id) => ({ id, name: id })), { seed, level: 'hard' });
          for (let k = 0; k < 180 * 60 && !g.over; k += 1) {
            T.step(g, 1 / 60);
            for (const e of g.events) { if (e.kind === 'kill') { deaths += 1; if (e.self) self += 1; } if (e.kind === 'deflect') banked += 1; }
          }
          if (!g.over) return { stuck: seed };
        }
        return { deaths, self, banked };
        """
    )
    assert "stuck" not in out
    assert out["self"] <= out["deaths"] * 0.15
