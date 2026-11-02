"""成长总账：设定、口癖、和你的阶段、看法、收藏排成一条（新的在前）；撤回交回原模块；
月报按来源数；「这阵子的你」只拼最近的、限字数、撤回的不算；工作模式不给。"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from roundtable.core.member.drift import Drift
from roundtable.core.member.growth.book import (
    LINGO,
    PROFILE,
    STASH,
    BookError,
    for_rt,
    recent_text,
)
from roundtable.core.member.growth.report import lines
from roundtable.core.member.mode import WorkModeError
from roundtable.core.service import RoundtableService
from roundtable.core.storage.repository import iso

from ...orchestrator.conftest import Env
from .common import NOW, fake_growth, setup


def _seed(env: Env) -> dict[str, str]:
    now = env.rt.budget.now()
    t = {k: iso(now - timedelta(days=d)) for k, d in (("old", 40), ("mid", 5), ("new", 1))}
    Drift(env.rt)._save(
        "V1",
        {
            "swaps": [
                {
                    "kind": "kaomoji",
                    "old": "(^_^)",
                    "new": "(￣▽￣)",
                    "at": t["new"],
                    "why": "用腻了",
                },
                {"kind": "rest", "old": "笑死", "new": "", "at": t["old"], "why": "用腻了"},
            ],
            "rested": {"笑死": iso(now + timedelta(days=20))},
        },
    )
    env.rt.repo.save_relation_data(
        "V1",
        {
            "stages": [{"at": t["mid"], "stage": "熟悉"}],
            "views_log": [
                {"week": "W", "at": t["mid"], "views": [], "changes": ["加深：你很较真"]}
            ],
        },
    )
    env.rt.repo.set_setting(
        "sticker_stash",
        json.dumps({"V1": [{"n": 1, "id": 9, "label": "猫猫叹气", "at": t["mid"], "uses": 0}]}),
    )
    return t


def test_changes_order_sources_and_undo():
    env = Env()
    _seed(env)
    book = for_rt(env.rt)
    rows = book.changes("V1")
    assert [r["source"] for r in rows][:1] == [LINGO]  # 最新的在前
    texts = " | ".join(r["text"] for r in rows)
    assert "颜文字「(^_^)」换成了「(￣▽￣)」" in texts and "口头禅「笑死」先歇一阵" in texts
    assert "和你成了「熟悉」" in texts and "对你的看法·加深：你很较真" in texts
    assert "猫猫叹气" in texts
    assert book.changes("V2") == []
    swap = next(r for r in rows if "颜文字" in r["text"])
    book.undo("V1", swap["undo"])
    assert Drift(env.rt).mapping("V1") == {}  # 换回来了
    rest = next(r for r in rows if "歇" in r["text"])
    book.undo("V1", rest["undo"])
    assert "笑死" not in Drift(env.rt).resting("V1", env.rt.budget.now())
    with pytest.raises(BookError):
        book.undo("V1", swap["undo"])  # 撤回过了
    after = book.changes("V1")
    assert next(r for r in after if "颜文字" in r["text"])["undone"]
    stash = next(r for r in after if r["source"] == STASH)
    book.undo("V1", stash["undo"])
    assert not [r for r in book.changes("V1") if r["source"] == STASH]
    for bad in ("", "nope:1", "lingo:x|y|z", "stash:99", "bond:1"):
        with pytest.raises(BookError):
            book.undo("V1", bad)


def test_recent_line_and_month_counts():
    env = Env()
    t = _seed(env)
    line = recent_text(env.rt, "V1")
    assert line.startswith("这阵子的你：") and "(￣▽￣)" in line
    assert "笑死" not in line  # 40 天前的不算「这阵子」
    assert "看法" not in line  # 对你的看法另有一行
    assert len(line) <= env.rt.config.roundtable.chat.engine.growth.book.context_chars
    with pytest.raises(WorkModeError):
        for_rt(env.rt).recent_line("V1", env.rt.budget.now(), mode="work")
    counts = for_rt(env.rt).month_counts("V1", t["mid"][:7])
    assert counts.get("和你") == 1 and counts.get("收藏") == 1
    assert recent_text(env.rt, "V2") == ""


async def test_profile_changes_flow_into_book_and_report():
    env = Env()
    setup(env)
    fake_growth(env)
    svc = RoundtableService(env.rt)
    svc._local_now = lambda: NOW
    await svc.member_growth_run("V1")
    g = svc.member_growth("V1")
    prof = [c for c in g["book"] if c["source"] == PROFILE]
    assert len(prof) == 2 and all(c["undo"] for c in prof)
    assert g["book_sources"][PROFILE] == "设定"
    g = svc.member_growth_undo("V1", prof[0]["undo"])
    kinds = [c for c in g["book"] if c["source"] == PROFILE]
    assert any(c["undone"] for c in kinds) and any("撤回" in c["text"] for c in kinds)
    r = svc.growth.report("V1", "2026-10")
    assert "others" in r and lines(r)[-1] == r["note"]


def test_recent_line_reaches_chat_persona_and_report_lines():
    from roundtable.core.member.growth.report import build
    from roundtable.core.persona import chat_persona

    env = Env()
    setup(env, experiences=False)
    _seed(env)
    assert "这阵子的你：" in chat_persona(env.rt, "V1")
    r = build(
        {"timeline": []},
        "2026-10",
        rules=env.rt.config.roundtable.chat.engine.growth,
        skills=[],
        diary_days=0,
        items=0,
        others={"口癖": 2, "收藏": 1},
    )
    assert "别的变化：口癖 2 处、收藏 1 处" in lines(r)
