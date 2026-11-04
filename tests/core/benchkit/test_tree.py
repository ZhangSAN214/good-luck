"""生成器第二版 · 第 1 块：三层分工树（大阶段 → 小项目 → 小线程）。"""

from __future__ import annotations

import asyncio

import pytest

from roundtable.core.benchkit.tree import PHASES, node_of, tree_view
from tests.core.benchkit.test_builder import FakeSC, make


async def test_every_step_becomes_a_thread_under_its_project(tmp_path):
    sc = FakeSC()
    bench = make(tmp_path, sc)
    b = bench.submit("帮我做个东京三天旅行的工作台")
    await bench.wait()
    assert b["status"] == "done"
    threads = b["threads"]
    assert len(threads) == len(sc.calls)  # 每派一件活记一条
    assert all(t["status"] == "done" and t["worker"] in sc.workers for t in threads)
    assert all(t["attempts"] >= 1 and t["started_at"] and t["finished_at"] for t in threads)
    writes = [t for t in threads if t["window"]]
    assert {t["window"] for t in writes} == {w["id"] for w in b["windows"]}
    tree = bench.tree(b["id"])
    assert [p["name"] for p in tree["phases"]] == list(PHASES)
    names = {p["name"]: [x["name"] for x in p["projects"]] for p in tree["phases"]}
    assert "方案书" in names["立项"]  # 拆需求挂在立项 / 方案书
    assert "质检与评审" in names["交付"]
    deliver = next(p for p in tree["phases"] if p["name"] == "交付")
    qa = next(x for x in deliver["projects"] if x["name"] == "质检与评审")
    assert qa["status"] == "done" and qa["attempts"] >= 1 and qa["workers"]


def test_node_of_follows_progress():
    assert node_of({}, "访谈") == ("立项", "访谈")
    assert node_of({}, "对抗") == ("交付", "对抗挑错")
    b = {"stage": 2, "proposal": {"milestones": [{"title": "最小可用"}, {"title": "主要功能"}]}}
    assert node_of(b, "实施") == ("执行", "第 2 段：主要功能")
    assert node_of({"stage_task": None, "status": "reworking"}, "实施") == ("交付", "质检与评审")
    assert node_of({}, "实施") == ("执行", "写窗口")


def test_old_projects_show_from_team_cards():
    b = {
        "team": {
            "cards": [
                {
                    "at": "2026-10-30T01:00:00+00:00",
                    "who": "a",
                    "label": "A",
                    "role": "拆需求",
                    "task": "把一句话拆成规格",
                    "ok": True,
                },
                {
                    "at": "2026-10-30T01:05:00+00:00",
                    "who": "b",
                    "label": "B",
                    "role": "实施",
                    "task": "写窗口「地图」",
                    "ok": False,
                },
            ]
        },
        "proposal": {"milestones": [{"title": "最小可用"}]},
    }
    tree = tree_view(b)
    assert tree["legacy"]
    start = next(p for p in tree["phases"] if p["name"] == "立项")
    assert start["projects"][0]["name"] == "方案书" and start["projects"][0]["status"] == "done"
    run = next(p for p in tree["phases"] if p["name"] == "执行")
    assert {x["name"] for x in run["projects"]} >= {"写窗口", "第 1 段：最小可用"}
    planned = next(x for x in run["projects"] if x["name"] == "第 1 段：最小可用")
    assert planned["status"] == "waiting" and planned["threads"] == []


async def test_pause_holds_new_threads_until_resume(tmp_path):
    sc = FakeSC()
    bench = make(tmp_path, sc)
    b = bench.submit("帮我做个东京三天旅行的工作台")
    bench.pause(b["id"])  # 整个项目暂停：第一件活就排队
    await asyncio.sleep(0.05)
    assert sc.calls == [] and b["threads"][0]["status"] == "paused"
    tree = bench.resume(b["id"])
    assert tree["paused"] == []
    await bench.wait()
    assert b["status"] == "done" and sc.calls
    with pytest.raises(ValueError, match="大阶段"):
        bench.pause(b["id"], "随便/什么")


async def test_rerun_window_thread_with_reason_and_new_worker(tmp_path):
    sc = FakeSC()
    bench = make(tmp_path, sc)
    b = bench.submit("帮我做个东京三天旅行的工作台")
    await bench.wait()
    write = next(t for t in b["threads"] if t["window"] == "plan")
    with pytest.raises(ValueError, match="哪里不对"):
        bench.rerun_thread(b["id"], write["id"], "  ")
    with pytest.raises(ValueError, match="没有这位助手"):
        bench.rerun_thread(b["id"], write["id"], "标题太小", worker="zz")
    plan = next(t for t in b["threads"] if not t["window"])
    with pytest.raises(ValueError, match="写窗口"):
        bench.rerun_thread(b["id"], plan["id"], "重来")
    n = len(sc.calls)
    bench.rerun_thread(b["id"], write["id"], "标题太小，看不清", worker="c")
    await bench.wait()
    last = sc.calls[n]
    assert last["role"] == "bench_rework" and last["preset"] == "c"
    assert "标题太小，看不清" in sc.prompts[n]  # 带上了上次哪里不对，不原样再发
    assert b["threads"][-1]["window"] == "plan" and b["threads"][-1]["worker"] == "c"
