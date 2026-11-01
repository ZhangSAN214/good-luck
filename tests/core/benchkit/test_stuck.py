"""做满轮数没过线：列出没做到的、卡在哪、建议；你选再跑一轮 / 改要求再跑 / 就这样。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.benchkit.builder import stuck_report

from .test_builder import BASE, CheckSC, make, plan_with_requirements


def rnd(scores, met, findings=(), bugs=()):
    return {
        "scores": {"a": scores},
        "requirements": [
            {"id": "r1", "text": "能按婚礼定制", "met": met, "why": [] if met else ["只换了标签"]},
            {"id": "b1", "text": "主视觉占大半屏", "met": True, "why": []},
        ],
        "check": {"findings": list(findings)},
        "attack": {"bugs": list(bugs)},
    }


def test_stuck_report_names_what_is_missing_where_it_sticks_and_what_to_do():
    rep = stuck_report(
        [
            rnd(6, False),
            rnd(
                6.2,
                False,
                findings=[{"check": "横向溢出", "where": "手机", "severity": "block"}],
                bugs=[{"severity": "high", "problem": "保存会卡死"}],
            ),
        ]
    )
    assert [m["id"] for m in rep["missing"]] == ["r1"] and rep["missing"][0]["missed"] == 2
    assert rep["blocks"] == ["横向溢出（手机）"] and rep["high"] == ["保存会卡死"]
    text = "\n".join(rep["advice"])
    assert "「r1」连续 2 轮都没做到" in text and "改得更具体" in text
    assert "必须修" in text and "严重问题" in text
    assert "没怎么涨" in text  # 6 → 6.2
    rising = stuck_report([rnd(5, True), rnd(7, False)])
    assert "再跑一轮有希望" in "".join(rising["advice"])
    assert "连续" not in "".join(rising["advice"])  # 只有最后一轮没做到


@pytest.mark.asyncio
async def test_needs_you_then_more_runs_one_round_and_can_pass(tmp_path):
    # 前 2 轮 r1 没做到（每轮 3 位评审），你选再跑一轮后做到了
    sc = CheckSC(unmet={"r1"}, fix_after=6, plan=plan_with_requirements())
    bb = make(tmp_path, sc, baseline=BASE, max_rounds=2)
    b = bb.submit("旅行台")
    await bb.wait()
    assert b["status"] == "needs_you" and b["stuck"]["missing"][0]["id"] == "r1"
    with pytest.raises(ValueError):
        bb.decide(b["id"], "nope")
    bb.decide(b["id"], "more")
    await bb.wait()
    assert len(b["rounds"]) == 3 and b["status"] == "done" and b["stuck"] is None
    assert any("你选了再跑一轮" in x["text"] for x in b["log"])
    with pytest.raises(ValueError):  # 已经过线，不用你决定
        bb.decide(b["id"], "more")


@pytest.mark.asyncio
async def test_edit_requirements_then_run_and_accept_as_is(tmp_path):
    sc = CheckSC(unmet={"r1"}, plan=plan_with_requirements())
    bb = make(tmp_path, sc, baseline=BASE, max_rounds=1)
    b = bb.submit("旅行台")
    await bb.wait()
    with pytest.raises(ValueError):
        bb.decide(b["id"], "edit", [])
    bb.decide(
        b["id"],
        "edit",
        [{"id": "r1", "text": "选婚礼后标题出现「婚礼」"}, {"id": "b1", "text": "x"}],
    )
    await bb.wait()
    spec = json.loads((tmp_path / b["id"] / "spec.json").read_text(encoding="utf-8"))
    texts = [r["text"] for r in spec["requirements"]]
    assert texts[0] == "选婚礼后标题出现「婚礼」" and "主视觉占大半屏" in texts  # 底线还在
    review = [json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_review"][-1]
    assert "选婚礼后标题出现「婚礼」" in review["requirements"]
    assert b["status"] == "needs_you"  # 假评审还是判 r1 没做到
    bb.decide(b["id"], "accept")
    assert b["status"] == "done" and b["accepted_as_is"] and b["stuck"] is None
    saved = json.loads((tmp_path / b["id"] / "build.json").read_text(encoding="utf-8"))
    assert saved["accepted_as_is"] is True
    with pytest.raises(LookupError):
        bb.decide("nope", "more")
