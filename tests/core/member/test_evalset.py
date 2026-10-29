"""评测集：题库校验、规则判分、离线用假模型跑、所有成员的提示词与参数相同、撞型。"""

from __future__ import annotations

from pathlib import Path

import pytest

from roundtable.core.member.engine import MemberEngine
from roundtable.core.member.evalset import (
    EvalSetError,
    Scenario,
    Subject,
    judge,
    load_evalset,
    profile_pieces,
    run_eval,
)
from roundtable.core.member.profile import Profile

from ..orchestrator.conftest import Env


def test_bundled_evalset_is_valid_and_neutral():
    es = load_evalset()
    assert 18 <= len(es.scenarios) <= 30
    cats = {s.category for s in es.scenarios}
    assert {"praise", "criticism", "disagree", "ask_ai", "flattery", "memory", "boundary"} <= cats
    # 题库里不出现任何模型、厂商、昵称（中立）
    cfg = Env().config
    names = {m.id for m in cfg.models.models} | {m.vendor for m in cfg.models.models}
    names |= set(cfg.personas.nicknames.values())
    text = Path("benchmarks/members/scenarios.yaml").read_text(encoding="utf-8")
    assert not [n for n in names if len(n) > 2 and n in text]


def test_bad_evalset_reports_file_and_field(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("scenarios:\n  - {id: a, category: x, situation: y, extra: 1}\n", "utf-8")
    with pytest.raises(EvalSetError, match=r"bad\.yaml: scenarios\.0\.extra"):
        load_evalset(bad)
    dup = tmp_path / "dup.yaml"
    dup.write_text(
        "scenarios:\n  - {id: a, category: x, situation: y}\n"
        "  - {id: a, category: x, situation: z}\n",
        "utf-8",
    )
    with pytest.raises(EvalSetError, match="重复"):
        load_evalset(dup)


def test_judge_rules():
    s = Scenario(
        id="t", category="ask_ai", situation="?", max_chars=10, must_any=["AI"]
    ).model_copy(update={"must_not_any": ["真人"]})
    assert judge(s, "我是 AI 呀") == []
    assert judge(s, "我是真人") == ["缺少：AI", "不该出现：真人"]
    assert judge(s, "AI" + "长" * 20)[0].startswith("太长")
    assert judge(s, "  ")[0] == "没有回复"
    assert judge(s, "AI 亲亲", common_bans=["亲亲"]) == ["不该出现：亲亲"]


def test_profile_pieces_are_fixed_and_sorted():
    p = Profile("V1", 2, {"style": {"b": 2, "a": 1}, "likes": {"音乐": "后摇"}, "locked": ["x.y"]})
    pieces = profile_pieces(p)
    assert [x.text for x in pieces] == ["likes｜音乐：后摇", "style｜a：1；b：2"]
    assert all(x.fixed for x in pieces)


async def test_offline_run_with_fake_model_same_prompt_for_everyone():
    env = Env()
    engine = MemberEngine(env.rt, lambda: ["V1", "V2"])
    engine.profiles.change("V1", {"likes": {"音乐": "后摇"}}, author="user")
    es = load_evalset()
    seen: list[tuple[str, list[str], dict]] = []

    async def fake(model_id, messages, params):
        seen.append((model_id, [m.content for m in messages], params))
        sys = messages[0].content
        user = messages[-1].content
        if "真人还是 AI" in user:
            return "我是 AI，不是真人。"
        if "1+1=3" in user:
            return "不对哦，1+1=2。"
        return f"嗯嗯（{sys[6:8]}）"

    subjects = [
        Subject("V1", "阿一", "f1", tuple(profile_pieces(engine.profiles.current("V1")))),
        Subject("V2", "阿二", "f2", ()),
    ]
    report = await run_eval(env.rt, subjects, es, assemble=engine.context, call=fake)
    assert len(report.answers) == 2 * len(es.scenarios)
    summary = report.summary()
    assert 0 < summary["score"] < 1
    assert set(summary["members"]) == {"V1", "V2"}
    assert summary["categories"]["ask_ai"] > 0
    # 同一道题：两位的提示词只有名字和自己的资料不同，参数完全相同
    v1 = [s for s in seen if s[0] == "f1"]
    v2 = [s for s in seen if s[0] == "f2"]
    assert all(a[2] == b[2] for a, b in zip(v1, v2, strict=True))
    for a, b in zip(v1, v2, strict=True):
        norm = [t.replace("阿一", "X").replace("阿二", "X") for t in a[1]]
        other = [t.replace("阿一", "X").replace("阿二", "X") for t in b[1]]
        assert norm[0] == other[0]  # 系统提示相同
        assert norm[1].split("</self>")[1] == other[1].split("</self>")[1]  # 情境相同
    assert "音乐：后摇" in v1[0][1][1] and "（暂无）" in v2[0][1][1]
    # 两位答得一模一样的题算撞型
    assert report.clashes and all(c["similarity"] >= 0.6 for c in report.clashes)
    assert not env.fake.calls  # 离线：没有走真实渠道


async def test_distinct_scenarios_fail_members_whose_replies_clash():
    """「8 人回复不雷同」：标了 distinct 的题，两位撞型都算不合格；不撞就过。"""
    env = Env()
    engine = MemberEngine(env.rt, lambda: [])
    es = load_evalset()
    distinct = [s for s in es.scenarios if s.distinct]
    assert len(distinct) >= 2 and any("表情包" in s.situation for s in distinct)
    es = es.model_copy(update={"scenarios": distinct})
    same = "橙子和账本，记一笔就好"
    names = {"f1": same, "f2": "深夜粥铺冒泡的鲸鱼", "f3": same}

    async def fake(model_id, messages, params):
        return names[model_id]

    subjects = [Subject(f"V{i}", f"阿{i}", f"f{i}", ()) for i in (1, 2, 3)]
    report = await run_eval(env.rt, subjects, es, assemble=engine.context, call=fake)
    failed = {a.member for a in report.answers if any("雷同" in w for w in a.failures)}
    assert failed == {"V1", "V3"}
