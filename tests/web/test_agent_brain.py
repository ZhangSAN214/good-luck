"""网页小人的大脑（web/js/agent/brain.js）：状态机、位置、存储容错。用 node 跑，没有就跳过。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from roundtable.core.members_art import PET_ACTIONS, PET_EXPRESSIONS

BRAIN = Path(__file__).resolve().parents[2] / "web" / "js" / "agent" / "brain.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="需要 node")


def run(body: str):
    """在 node 里执行 body（B 为 brain.js 模块，clock 为可控时钟），返回 body 的返回值。"""
    script = (
        f"const B = await import({json.dumps(BRAIN.as_uri())});"
        "const clock = { t: 1000 }; const now = () => clock.t;"
        "let seq = []; const random = () => (seq.length ? seq.shift() : 0.99);"
        "const brain = (o = {}) => new B.Brain({ now, random, ...o });"
        f"const out = await (async () => {{ {body} }})();"
        "process.stdout.write(JSON.stringify(out));"
    )
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(res.stdout)


def test_names_match_backend():
    names = run("return [Object.keys(B.ACTIONS), Object.keys(B.EXPRESSIONS)];")
    assert set(names[0]) | {"blink"} == set(PET_ACTIONS)
    assert set(names[1]) == set(PET_EXPRESSIONS)


def test_actions_expire_back_to_idle_and_bring_their_face():
    out = run(
        """
        const b = brain(); const r = [];
        b.act('wave'); r.push(b.tick());
        clock.t += 1900; r.push(b.tick());
        b.act('think'); r.push(b.tick());
        clock.t += 3300; r.push(b.tick());
        return r;
        """
    )
    assert out[0]["action"] == "wave" and out[0]["face"] == "happy"
    assert out[1]["action"] == "idle" and out[1]["face"] is None
    assert out[2]["action"] == "think" and out[2]["face"] == "thinking"
    assert out[3]["action"] == "idle" and out[3]["face"] is None


def test_priority_drag_beats_everything_until_dropped():
    out = run(
        """
        const b = brain(); const r = [];
        b.act('celebrate');
        r.push(b.act('wave'));            // 庆祝时不被挥手打断
        r.push(b.act('celebrate'));
        b.grab(); r.push(b.tick());
        r.push(b.act('celebrate'));       // 被拖着时不能庆祝
        r.push(b.feel('happy'));          // 也不换表情
        clock.t += 60000; r.push(b.tick().action);
        b.drop(); r.push(b.tick());
        clock.t += 600; r.push(b.tick());
        return r;
        """
    )
    assert out[0] is False and out[1] is True
    assert out[2]["action"] == "drag" and out[2]["face"] == "surprised"
    assert out[3] is False and out[4] is False
    assert out[5] == "drag"  # 一直挣扎，直到松手
    assert out[6]["action"] == "land" and out[6]["face"] is None
    assert out[7]["action"] == "idle"


def test_expressions_have_their_own_timer():
    out = run(
        """
        const b = brain(); const r = [];
        for (const f of Object.keys(B.EXPRESSIONS)) { b.feel(f); r.push(b.tick().face); }
        r.push(b.feel('nope'));
        clock.t += B.FACE_MS + 1; r.push(b.tick().face);
        return r;
        """
    )
    assert out[:-2] == list(PET_EXPRESSIONS) or set(out[:-2]) == set(PET_EXPRESSIONS)
    assert out[-2] is False and out[-1] is None


def test_gets_sleepy_then_sleeps_and_wakes_on_activity():
    out = run(
        """
        const b = brain(); const r = [];
        clock.t += B.SLEEPY_AFTER; r.push(b.tick());
        clock.t += B.SLEEP_AFTER - B.SLEEPY_AFTER; r.push(b.tick());
        clock.t += 60000; r.push(b.tick().action);
        b.poke(); r.push(b.tick());
        return r;
        """
    )
    assert out[0]["action"] == "sleepy" and out[0]["face"] == "sleepy"
    assert out[1]["action"] == "sleep" and out[1]["face"] is None
    assert out[2] == "sleep"
    assert out[3]["action"] == "idle" and out[3]["face"] == "surprised"


def test_idle_does_small_things_by_itself():
    out = run(
        """
        const r = [];
        // random 依次决定：间隔、挑选的小动作
        seq = [0, 0, 0.0]; const a = brain(); clock.t += 1900; r.push(a.tick());  // 眨眼
        seq = [0, 0, 0.6]; const b = brain(); clock.t += 1900; r.push(b.tick().action);  // 走
        seq = [0, 0, 0.6]; const c = brain({ calm: true }); clock.t += 1900;
        r.push(c.tick().action);
        return r;
        """
    )
    assert out[0]["blink"] is True and out[0]["action"] == "idle"
    assert out[1] == "walk"
    assert out[2] == "idle"  # 减少动态效果时不自己走


def test_demo_shows_every_action_and_expression():
    out = run(
        """
        const b = brain(); const acts = new Set(), faces = new Set();
        b.startDemo();
        for (let i = 0; i < 600; i++) {
          const s = b.tick(); acts.add(s.action); if (s.face) faces.add(s.face); clock.t += 100;
        }
        return [[...acts], [...faces]];
        """
    )
    acts, faces = out
    assert set(acts) >= {
        "wave",
        "jump",
        "walk",
        "think",
        "talk",
        "type",
        "celebrate",
        "sleepy",
        "sleep",
    }
    assert set(faces) == set(PET_EXPRESSIONS)


def test_clamp_snap_and_home():
    out = run(
        """
        const size = { w: 100, h: 150 }, view = { w: 1000, h: 800 };
        return [
          B.clamp({ x: -50, y: 9999 }, size, view),
          B.clamp({ x: NaN, y: 'x' }, size, view),
          B.clamp({ x: 10, y: 10 }, size, { w: 50, h: 50 }),
          B.snap({ x: 30, y: 300 }, size, view),
          B.snap({ x: 880, y: 640 }, size, view),
          B.snap({ x: 400, y: 300 }, size, view),
          B.home(size, view),
        ];
        """
    )
    assert out[0] == {"x": 0, "y": 650}
    assert out[1] == {"x": 900, "y": 650}
    assert out[2] == {"x": 0, "y": 0}  # 窗口比小人还小
    assert out[3] == {"x": 0, "y": 300, "edge": "left"}
    assert out[4] == {"x": 900, "y": 650, "edge": "right"}
    assert out[5] == {"x": 400, "y": 300, "edge": None}
    assert out[6] == {"x": 876, "y": 626}


def test_storage_errors_are_swallowed():
    out = run(
        """
        const boom = () => { throw new Error('denied'); };
        const throwing = () => ({ getItem: boom, setItem: boom });
        const mem = {};
        const ok = () => ({ getItem: (k) => mem[k] ?? null, setItem: (k, v) => { mem[k] = v; } });
        return [
          B.loadJSON(boom, 'k'), B.saveJSON(boom, 'k', { a: 1 }),
          B.loadJSON(throwing, 'k'), B.saveJSON(throwing, 'k', { a: 1 }),
          B.saveJSON(ok, 'k', { x: 3 }), B.loadJSON(ok, 'k'),
          (mem.bad = '{oops', B.loadJSON(ok, 'bad')), (mem.n = '5', B.loadJSON(ok, 'n')),
        ];
        """
    )
    assert out == [None, False, None, False, True, {"x": 3}, None, None]
