"""小人对圆桌事件的反应（web/js/agent/reactions.js）：用 node 直接跑，没有 node 时跳过。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MOD = Path(__file__).resolve().parents[2] / "web" / "js" / "agent" / "reactions.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="需要 node")


def run(body: str):
    script = (
        f"const R = await import({json.dumps(MOD.as_uri())});"
        "const clock = { t: 1000 }; const now = () => clock.t;"
        "const rep = () => new R.Reporter({ labels: { debate: '讨论', merge: '合并' }, now });"
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


def test_speaking_steps_talk_and_coordination_steps_think():
    out = run(
        """
        const r = rep();
        const a = r.react({ type: 'step_started', step: 'debate', table_no: 1 });
        clock.t += 5000;
        const b = r.react({ type: 'step_started', step: 'merge', table_no: 1 });
        return [a, b, r.status];
        """
    )
    assert out[0]["act"] == "talk" and out[0]["say"] == "讨论中（第 1 桌）"
    assert out[1]["act"] == "think" and out[1]["say"] == "合并中（第 1 桌）"
    assert out[2] == "合并中（第 1 桌）"


def test_progress_bubbles_are_rate_limited_but_actions_continue():
    out = run(
        """
        const r = rep();
        const a = r.react({ type: 'step_started', step: 'debate', table_no: 1 });
        clock.t += 500;
        const b = r.react({ type: 'step_started', step: 'merge', table_no: 1 });
        return [a, b];
        """
    )
    assert out[0]["say"] and "say" not in out[1] and out[1]["act"] == "think"


def test_checkpoint_waves_with_question_and_asks_for_confirmation():
    out = run("const r = rep(); return [r.react({ type: 'checkpoint' }), r.status];")
    r = out[0]
    assert r["act"] == "wave" and r["feel"] == "confused" and r["urgent"] is True
    assert "确认" in r["say"] and out[1] == "等你确认"


def test_celebrates_only_a_run_that_was_going_on():
    out = run(
        """
        const r = rep();
        // 打开一个早就结束的会话：快照是已完成，之后的 state 不庆祝
        r.react({ type: 'snapshot', status: 'completed' });
        const old = r.react({ type: 'state', status: 'completed' });
        // 新跑一场：开始 → 完成
        r.react({ type: 'step_started', step: 'debate', table_no: 1 });
        const live = r.react({ type: 'state', status: 'completed' });
        const again = r.react({ type: 'state', status: 'completed' });
        return [old, live, again, r.status];
        """
    )
    assert out[0] is None
    assert out[1]["act"] == "celebrate" and out[1]["urgent"] is True
    assert out[2] is None and out[3] == "已完成"


def test_failure_is_sad_and_budget_warning_is_hurried():
    out = run(
        """
        const r = rep();
        r.react({ type: 'step_started', step: 'debate', table_no: 1 });
        const msg = '本场花费已超过预算的 80%';
        const w = r.react({ type: 'budget_warning', data: { message: msg } });
        const f = r.react({ type: 'state', status: 'failed' });
        return [w, f];
        """
    )
    assert out[0]["feel"] == "confused" and out[0]["act"] == "jump" and "预算" in out[0]["say"]
    assert out[1]["feel"] == "sad"


def test_unknown_events_do_nothing_and_long_text_is_cut():
    out = run(
        """
        const r = rep();
        const x = r.react({ type: 'something_new' });
        const w = r.react({ type: 'budget_warning', data: { message: 'x'.repeat(300) } });
        return [x, w.say.length];
        """
    )
    assert out[0] is None and out[1] <= 40


def test_quiet_mode_and_poke_counter():
    out = run(
        """
        const r = rep();
        r.quiet();
        const m1 = r.muted;
        clock.t += R.QUIET_MS + 1;
        const m2 = r.muted;
        r.quiet(); r.wake();
        const p = [r.poke(), r.poke(), r.poke()];
        clock.t += 20000;
        const q = [r.poke(), r.poke()];
        return [m1, m2, r.muted, p, q];
        """
    )
    assert out[:3] == [True, False, False]
    assert out[3] == [False, False, True]  # 连戳三下生气
    assert out[4] == [False, False]  # 隔久了重新数


def test_look_direction_and_hit_part():
    out = run(
        """
        const rect = { x: 100, y: 100, w: 120, h: 150 };
        return [
          R.lookDir(rect, { x: 380, y: 170 }),   // 右边近
          R.lookDir(rect, { x: 10, y: 170 }),    // 左边近
          R.lookDir(rect, { x: 900, y: 170 }),   // 太远
          R.lookDir(rect, { x: 160, y: 170 }),   // 正对着
          R.hitPart(10, 150), R.hitPart(100, 150),
        ];
        """
    )
    assert out == [1, -1, 0, 0, "head", "body"]
