"""可执行的验收（C6）：拆需求时写的 probe 规范化；
质检时程序真的去做，没做到就不算做到、交回负责的窗口。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.benchkit.spec import normalize, probe_of

from .test_builder import PLAN, CheckSC, make


def test_probe_of_keeps_only_runnable_steps_and_checkable_results():
    assert probe_of(None) is None and probe_of("点一下") is None
    got = probe_of(
        {
            "steps": [
                {"do": "fill", "target": "目的地", "value": "京都"},
                {"do": "fill", "target": "人数"},  # 没写填什么：丢掉
                {"do": "hover", "target": "地图"},  # 不认得的动作：丢掉
                {"do": "click", "target": "  生成   行程 ", "value": "多余"},
                *[{"do": "click", "target": f"按钮{i}"} for i in range(5)],
            ],
            "expect": ["京都", "", "x" * 60],
            "changes": "yes",  # 不是 true 就当没写
        }
    )
    assert got["steps"][:2] == [
        {"do": "fill", "target": "目的地", "value": "京都"},
        {"do": "click", "target": "生成 行程"},
    ]
    assert len(got["steps"]) == 4 and got["expect"] == ["京都", "x" * 40]
    assert got["changes"] is False
    # 没有要看的结果：等于没写
    assert probe_of({"steps": [{"do": "click", "target": "生成"}], "expect": []}) is None
    assert probe_of({"steps": [{"do": "click", "target": "生成"}], "changes": True})["changes"]


def test_spec_keeps_probe_on_requirements_and_baseline():
    raw = dict(PLAN)
    raw["requirements"] = [
        {
            "text": "换目的地后行程跟着变",
            "windows": ["plan"],
            "probe": {
                "steps": [{"do": "fill", "target": "目的地", "value": "京都"}],
                "expect": ["京都"],
            },
        },
        {"text": "好看"},
        {"id": "b1", "probe": {"steps": [{"do": "click", "target": "地图"}], "changes": True}},
    ]
    spec = normalize(raw, max_windows=8, baseline=["主视觉占大半屏"])
    by = {r["id"]: r for r in spec.requirements}
    assert by["r1"]["probe"]["expect"] == ["京都"] and by["r2"]["probe"] is None
    assert by["b1"]["probe"]["changes"] is True


class Prober:
    def __init__(self, results):
        self.results, self.calls = results, []

    def __call__(self, target, probes, allow=()):
        self.calls.append([p["id"] for p in probes])
        return [{"id": p["id"], **self.results[p["id"]]} for p in probes]


def probe_plan():
    raw = dict(PLAN)
    raw["requirements"] = [
        {
            "text": "换目的地后行程跟着变",
            "windows": ["plan"],
            "probe": {
                "steps": [{"do": "fill", "target": "目的地", "value": "京都"}],
                "expect": ["京都"],
            },
        },
        {
            "text": "按我的条件生成推荐",
            "windows": ["budget"],
            "probe": {"steps": [{"do": "click", "target": "生成"}], "expect": ["推荐"]},
        },
        {"text": "看起来专业"},
    ]
    return raw


@pytest.mark.asyncio
async def test_failed_probe_overrules_reviewers_and_goes_to_its_window(tmp_path):
    # 评审都说做到了；程序去填「京都」没看到，r1 不算做到；r2 要现场生成，查不了，不冤枉
    sc = CheckSC(plan=probe_plan())
    prober = Prober(
        {
            "r1": {"ok": False, "why": "做完以后页面上没有「京都」"},
            "r2": {"ok": None, "why": "要请助手现场生成"},
        }
    )
    bb = make(tmp_path, sc, max_rounds=1)
    bb.probe = prober
    b = bb.submit("旅行台")
    await bb.wait()
    rnd = b["rounds"][0]
    assert prober.calls == [["r1", "r2"]]
    reqs = {r["id"]: r for r in rnd["requirements"]}
    assert not reqs["r1"]["met"] and reqs["r1"]["why"][0] == "自动检查：做完以后页面上没有「京都」"
    assert reqs["r2"]["met"] and reqs["r3"]["met"]
    finding = next(f for f in rnd["check"]["findings"] if f["check"] == "验收自动检查没过")
    assert finding["windows"] == ["plan"] and finding["severity"] == "block"
    assert not rnd["passed"] and b["status"] == "needs_you"
    assert any("验收自动检查：0/2 条做到，1 条要现场生成" in x["text"] for x in b["log"])
    # 评审拿到的质检结果里也有这一条
    review = next(json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_review")
    assert "验收自动检查没过" in review["check"]


@pytest.mark.asyncio
async def test_passing_probe_and_no_probe_need_no_browser(tmp_path):
    sc = CheckSC(plan=probe_plan())
    bb = make(tmp_path, sc)
    bb.probe = Prober({"r1": {"ok": True, "why": ""}, "r2": {"ok": True, "why": ""}})
    b = bb.submit("旅行台")
    await bb.wait()
    assert b["status"] == "done", b["log"]
    sc2 = CheckSC()
    bb2 = make(tmp_path / "x", sc2)
    bb2.probe = None  # 没有 probe 的规格根本不会去开浏览器
    b2 = bb2.submit("旅行台")
    await bb2.wait()
    assert b2["status"] == "done" and b2["rounds"][0]["probes"] == []
