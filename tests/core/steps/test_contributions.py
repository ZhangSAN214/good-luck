"""贡献统计：采纳情况解析、汇总的 adopted_from、按桌计算每位组员的贡献。"""

from __future__ import annotations

import json

from roundtable.core.steps import (
    EffortRecord,
    ReviewDecision,
    Revision,
    TableState,
    get_step,
    parse_decisions,
    parse_synthesis,
    table_contributions,
)
from roundtable.core.steps.schemas import CheckedReview, ReviewIssue

from .conftest import MEMBERS, Table


def to_code(text: str):
    t = text.strip().removeprefix("组员").strip()
    return t if t in MEMBERS else None


def test_parse_decisions_formats():
    text = """- 组员乙 · 问题 1：采纳 —— 对
- **组员乙** · 问题 2：不采纳 —— 不对
- 组员丙：部分采纳 —— 一半
* 组员乙 · 问题1: 部分采纳
- 组员丁：采纳（不在桌上）
随便写的一行
- 组员乙 · 问题 1：不采纳 —— 重复的以第一次为准"""
    assert parse_decisions(text, to_code) == (
        ReviewDecision("乙", 1, "accepted"),
        ReviewDecision("乙", 2, "rejected"),
        ReviewDecision("丙", None, "partial"),
    )
    assert parse_decisions("采纳。", to_code) == ()


def test_revision_without_decisions_still_loads():
    old = {"answer": "a", "responses": "r", "skipped": False, "degraded": False}
    assert Revision.from_dict(old).decisions == ()
    r = Revision("a", "r", decisions=(ReviewDecision("乙", 1, "accepted"),))
    assert Revision.from_dict(r.to_dict()) == r


def test_adopted_from_normalized_to_codes():
    text = json.dumps(
        {
            "final_answer": "x",
            "adopted_from": [
                {"point": "要点一", "members": ["组员甲", "甲", "组员戊"]},
                {"point": "", "members": ["组员乙"]},
                {"point": "要点三", "members": ["没有这个人"]},
            ],
        },
        ensure_ascii=False,
    )
    s = parse_synthesis(text, to_code)
    assert [(a.point, a.members) for a in s.output.adopted_from] == [("要点一", ["甲"])]


def review(reviewer, target, issues=1, valid=True):
    return CheckedReview(
        reviewer,
        target,
        "partially_correct",
        tuple(ReviewIssue(location="l", problem="p", suggestion="s") for _ in range(issues)),
        "",
        "",
        () if valid else ("无效",),
    )


def test_table_contributions_counts():
    from roundtable.core.steps.schemas import Adoption, Synthesis, SynthesisOutput

    state = TableState(
        answers={"甲": "a", "乙": "b", "丙": "c"},
        reviews={
            "甲": (review("甲", "乙", issues=2), review("甲", "丙", valid=False)),
            "乙": (review("乙", "甲", issues=1),),
            "丙": (review("丙", "甲", issues=0),),
        },
        revisions={
            "乙": Revision(
                "b2",
                "r",
                decisions=(
                    ReviewDecision("甲", 1, "accepted"),
                    ReviewDecision("甲", 2, "partial"),
                    ReviewDecision("甲", 3, "accepted"),  # 没有第 3 个问题：不计
                    ReviewDecision("丙", None, "accepted"),  # 丙没评乙：不计
                ),
            ),
            "甲": Revision(
                "a2",
                "r",
                decisions=(
                    ReviewDecision("乙", 1, "rejected"),
                    ReviewDecision("丙", None, "accepted"),  # 整条采纳按 1 计
                ),
            ),
        },
        synthesis=Synthesis(
            SynthesisOutput(
                final_answer="x",
                adopted_from=[
                    Adoption(point="p1", members=["甲", "乙"]),
                    Adoption(point="p2", members=["甲"]),
                ],
            )
        ),
        dropped={"丙": "调用失败"},
        effort={
            ("answer", "丙"): EffortRecord("answer", "丙", "lazy", ("短",), ("短",)),
            ("review", "乙"): EffortRecord("review", "乙", "redone", ("空",)),
        },
    )
    c = table_contributions(state, ["甲", "乙", "丙"])
    assert dict(c["甲"]) == {
        "answered": 1,
        "adopted": 2,
        "valid_review": 1,
        "valid_issue": 2,
        "issue_accepted": 2,
    }
    assert dict(c["乙"]) == {
        "answered": 1,
        "adopted": 1,
        "valid_review": 1,
        "valid_issue": 1,
        "redo": 1,
    }
    assert dict(c["丙"]) == {
        "answered": 1,
        "valid_review": 1,
        "issue_accepted": 1,
        "dropped": 1,
        "redo": 1,
        "lazy": 1,
    }


def test_degraded_synthesis_gives_no_adoption_credit():
    from roundtable.core.steps.schemas import fallback_synthesis

    state = TableState(answers={"甲": "a"}, synthesis=fallback_synthesis("坏了"))
    assert "adopted" not in table_contributions(state, ["甲"])["甲"]


async def test_full_table_contributions():
    t = Table(5)
    for step in ("answer", "review", "revise", "synthesize"):
        await get_step(step).run(t.ctx)
    c = table_contributions(t.ctx.state, list(MEMBERS))
    for code in MEMBERS:
        assert c[code]["answered"] == 1 and c[code]["valid_review"] == 2
        assert c[code]["issue_accepted"] == 2  # 两位作者都采纳了他的问题 1
    assert sum(c[code]["adopted"] for code in MEMBERS) == 4  # 3 + 1
