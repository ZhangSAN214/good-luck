"""Live2D 行为驱动器（web/live2d/driver.js）：视线先动头再跟、情绪状态机、眨眼、口型、参数映射。
用 node 跑，没有就跳过。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

DRIVER = Path(__file__).resolve().parents[2] / "web" / "live2d" / "driver.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="需要 node")


def run(body: str):
    """在 node 里执行 body（D 为 driver.js 模块，make(seed, ranges) 造驱动器，tick(d, 秒) 推进），
    返回 body 的返回值。"""
    script = (
        f"const D = await import({json.dumps(DRIVER.as_uri())});"
        "const make = (seed = 1, ranges = {}) =>"
        "  new D.BehaviorDriver({ random: D.mulberry32(seed), ranges });"
        "const tick = (d, sec, dt = 1 / 60) => { let o; const n = Math.round(sec / dt);"
        "  for (let i = 0; i < n; i++) o = d.update(dt); return o; };"
        f"const out = await (async () => {{ {body} }})();"
        "process.stdout.write(JSON.stringify(out));"
    )
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(res.stdout)


def test_six_emotions():
    names = run("return Object.keys(D.EMOTIONS);")
    assert set(names) == {"calm", "happy", "shy", "thinking", "surprised", "smirk"}


def test_gaze_leads_head():
    eye, head_early, head_late, eye_late = run(
        """const d = make(3, { ParamAngleX: [-45, 45] }); tick(d, 1.0); d.lookAt(1, 0);
        const a = tick(d, 0.15); const b = tick(d, 1.6);
        return [a.ParamEyeBallX, a.ParamAngleX, b.ParamAngleX, b.ParamEyeBallX];"""
    )
    assert eye > 0.6  # 眼睛 0.15 秒内就转过去了
    assert head_early < 12  # 头还没怎么动
    assert head_late > 28  # 头随后转得很大（±45 的模型）
    assert eye_late < eye  # 头转过去以后眼睛回正一些


def test_head_turn_uses_model_range():
    small, big = run(
        """const a = make(5, { ParamAngleX: [-30, 30] }); a.lookAt(1, 0);
        const b = make(5, { ParamAngleX: [-45, 45] }); b.lookAt(1, 0);
        return [tick(a, 3).ParamAngleX, tick(b, 3).ParamAngleX];"""
    )
    assert small <= 30 and big > small + 5


def test_blinks_are_irregular():
    gaps = run(
        """const d = make(7); const times = []; let closed = false;
        for (let i = 0; i < 60 * 60; i++) { const o = d.update(1 / 60);
          if (o.ParamEyeLOpen < 0.2 && !closed) { times.push(i / 60); closed = true; }
          if (o.ParamEyeLOpen > 0.8) closed = false; }
        return times.slice(1).map((t, i) => t - times[i]);"""
    )
    assert 8 <= len(gaps) <= 40  # 一分钟眨 9～40 次
    mean = sum(gaps) / len(gaps)
    spread = (sum((g - mean) ** 2 for g in gaps) / len(gaps)) ** 0.5
    assert spread > 0.4  # 间隔不是固定的


def test_idle_motion_is_not_a_loop():
    same, different = run(
        """const run = (seed) => { const d = make(seed); const xs = [];
          for (let i = 0; i < 600; i++) xs.push(d.update(1 / 30).ParamAngleX); return xs; };
        const a = run(11), b = run(11), c = run(12);
        return [JSON.stringify(a) === JSON.stringify(b),
          a.some((v, i) => Math.abs(v - c[i]) > 0.5)];"""
    )
    assert same and different  # 同一种子可复现，换种子就不一样


def test_surprise_returns_to_previous_emotion():
    states = run(
        """const d = make(2); d.setEmotion('happy'); tick(d, 1); d.setEmotion('surprised');
        const s1 = d.emotion; tick(d, 3); return [s1, d.emotion];"""
    )
    assert states == ["surprised", "happy"]


def test_emotion_transition_is_eased():
    max_step, smile = run(
        """const d = make(4); d.setEmotion('happy'); tick(d, 2); d.setEmotion('smirk');
        let prev = d.update(1 / 60), m = 0;
        for (let i = 0; i < 90; i++) { const o = d.update(1 / 60);
          m = Math.max(m, Math.abs(o.ParamEyeRSmile - prev.ParamEyeRSmile)); prev = o; }
        return [m, prev.ParamEyeRSmile];"""
    )
    assert max_step < 0.08  # 一帧之内不跳变
    assert smile < 0.3  # 坏笑是不对称的：右眼笑得少


def test_smirk_is_asymmetric_and_shy_blushes():
    smirk, shy = run(
        """const a = make(1); a.setEmotion('smirk'); const o = tick(a, 2);
        const b = make(1); b.setEmotion('shy'); const p = tick(b, 3);
        return [[o.ParamEyeLSmile, o.ParamEyeRSmile, o.ParamBrowLY, o.ParamBrowRY],
          [p.ParamCheek, p.ParamAngleY]];"""
    )
    assert smirk[0] > smirk[1] + 0.3 and smirk[2] > smirk[3]
    assert shy[0] > 0.9 and shy[1] < -3  # 害羞：脸红、低头


def test_audio_lip_sync_opens_fast_closes_slower():
    opened, closed_quick, closed_late = run(
        """const d = make(1); d.setSpeaking(true); d.setAudioLevel(0.3);
        const a = tick(d, 0.12); d.setAudioLevel(0);
        const b = tick(d, 0.05); const c = tick(d, 0.5);
        return [a.ParamMouthOpenY, b.ParamMouthOpenY, c.ParamMouthOpenY];"""
    )
    assert opened > 0.8
    assert closed_quick > 0.3  # 合得比开得慢
    assert closed_late < 0.05


def test_speaking_without_audio_makes_syllables():
    lo, hi = run(
        """const d = make(9); d.setSpeaking(true); let lo = 1, hi = 0;
        for (let i = 0; i < 180; i++) { const v = d.update(1 / 60).ParamMouthOpenY;
          lo = Math.min(lo, v); hi = Math.max(hi, v); }
        return [lo, hi];"""
    )
    assert lo < 0.15 and hi > 0.5


def test_mood_interface():
    got = run(
        """const d = make(1); const out = [];
        const moods = ['开心', 'teasing', '思考', 'unknown-word', { valence: -0.6 },
          { valence: 0.2, arousal: 0.9 }];
        for (const m of moods) {
          d.setMood(m); out.push(d.emotion); }
        return out;"""
    )
    assert got == ["happy", "smirk", "thinking", "calm", "shy", "surprised"]


def test_outputs_stay_in_range():
    bad = run(
        """const d = make(6, { ParamAngleX: [-45, 45] }); const bad = [];
        const r = { ...D.DEFAULT_RANGES, ParamAngleX: [-45, 45] };
        for (const e of Object.keys(D.EMOTIONS)) {
          d.setEmotion(e); d.lookAt(1, 1); d.setSpeaking(true);
          for (let i = 0; i < 120; i++) { const o = d.update(1 / 60);
            for (const [k, v] of Object.entries(o)) {
              if (r[k] && (v < r[k][0] - 1e-9 || v > r[k][1] + 1e-9)) bad.push([e, k, v]); } } }
        return bad;"""
    )
    assert bad == []


def test_param_map_matches_aliases_and_clamps():
    mapped, missing, applied = run(
        """const m = new D.ParamMap([
            { id: 'ParamAngleX', min: -45, max: 45 }, { id: 'PARAM_EYE_L_OPEN', min: 0, max: 1 },
            { id: 'ParamMouthOpenY', min: 0, max: 1 } ]);
        const got = {};
        m.apply({ ParamAngleX: 60, ParamEyeLOpen: 0.5, ParamMouthOpenY: -1, ParamCheek: 1 },
          (id, v) => { got[id] = v; });
        return [m.map, m.missing.includes('ParamCheek'), got];"""
    )
    assert mapped == {
        "ParamAngleX": "ParamAngleX",
        "ParamEyeLOpen": "PARAM_EYE_L_OPEN",
        "ParamMouthOpenY": "ParamMouthOpenY",
    }
    assert missing
    assert applied == {"ParamAngleX": 45, "PARAM_EYE_L_OPEN": 0.5, "ParamMouthOpenY": 0}


def test_random_expression_skips_sweat_and_tears():
    picks = run(
        """const names = ['smile', 'sweat', 'shy', 'tears', 'gloom', '流汗'];
        const r = D.mulberry32(3); const seen = new Set();
        for (let i = 0; i < 200; i++) seen.add(D.pickIdleExpression(names, r));
        return [...seen].sort();"""
    )
    assert picks == ["shy", "smile"]
