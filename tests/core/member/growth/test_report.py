"""月度「这个月的我」：程序按记录数出来；写明「表现像」不等于有主观体验。"""

from __future__ import annotations

from datetime import date

from roundtable.core.member.growth.report import NOTE, build, lines, prev_month

from ...orchestrator.conftest import Env


def test_prev_month():
    assert prev_month(date(2026, 11, 1)) == "2026-10" and prev_month(date(2026, 1, 9)) == "2025-12"


def test_report_counts_month_only_and_skill_ups():
    rules = Env().config.roundtable.chat.engine.growth

    def e(kind, path="x", op="score", at="2026-10-26T12:00:00+00:00", week="2026-W43", **kw):
        return {"at": at, "week": week, "kind": kind, "op": op, "path": path, **kw}

    tl = [
        e("change", "personality.五项.好奇", old=3, new=4, user=False, reverted=False),
        e("change", "likes.最爱", "add", value="看星星", user=True, reverted=False),
        e("change", "likes.讨厌", "add", value="下雨", user=False, reverted=True),
        e("revert", "likes.讨厌", of=3),
        e("rejected"),
        e("change", "personality.五项.外向", at="2026-09-30T12:00:00+00:00", old=1, new=2),
    ]
    blob = {
        "timeline": tl,
        "counts": {"说话:深夜": {"n": 30, "weeks": 5, "last": "2026-W43"}},
        "skills_at": {"学术": 2},
    }
    skills = [{"domain": "学术", "level": 4}, {"domain": "日常", "level": 0}]
    r = build(blob, "2026-10", rules=rules, skills=skills, diary_days=12, items=40)
    assert r["changes"] == 3 and r["reverted"] == 1 and r["rejected"] == 1 and r["weeks"] == 1
    assert r["traits"] == {"好奇": {"from": 3, "to": 4}}  # 9 月的不算
    assert r["lists"] == ["最爱 加上「看星星」"]  # 撤回的不算
    assert r["user_share"] == 0.5 and r["habits"] == ["常在深夜说话"]
    assert r["skill_ups"] == [{"domain": "学术", "from": 2, "to": 4}]
    text = "\n".join(lines(r))
    assert "好奇 3 → 4" in text and "学术 2 → 4 级" in text and "有经历的日子 12 天" in text
    assert lines(r)[-1] == NOTE and "表现得像" in NOTE and "不等于 TA 有主观体验" in NOTE
