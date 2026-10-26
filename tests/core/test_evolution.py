"""革新例会的纯逻辑：资料卡、周、例会记录、采纳单。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from roundtable.core.config.schema import EvolutionRules
from roundtable.core.evolution import (
    Meeting,
    adoption_markdown,
    adoption_path,
    dump_meetings,
    load_cards,
    load_meetings,
    meeting_question,
    week_key,
    week_slot,
)
from roundtable.core.ideaboard import Board, CompareRow, Ranked


def test_week_key_and_slot():
    now = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)  # 周三
    assert week_key(now) == "2026-W44"
    assert week_slot(now, 1, 9) == datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
    assert week_slot(now, 7, 20) == datetime(2026, 11, 1, 20, 0, tzinfo=UTC)
    assert week_key(datetime(2027, 1, 1, tzinfo=UTC)) == "2026-W53"


def test_load_cards_sorts_limits_and_truncates(tmp_path):
    (tmp_path / "2-b.md").write_text("# 第二张\n内容", encoding="utf-8")
    (tmp_path / "1-a.txt").write_text("﻿\n\n标题行\n正文", encoding="utf-8")
    (tmp_path / "3-long.md").write_text("# 长\n" + "字" * 500, encoding="utf-8")
    (tmp_path / "4-extra.md").write_text("# 多出来的", encoding="utf-8")
    (tmp_path / "empty.md").write_text("  ", encoding="utf-8")
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe\xfa")
    (tmp_path / "skip.pdf").write_bytes(b"%PDF")
    (tmp_path / ".hidden.md").write_text("# 隐藏", encoding="utf-8")
    cards, notes = load_cards(tmp_path, max_cards=3, card_chars=200)
    assert [c.file for c in cards] == ["1-a.txt", "2-b.md", "3-long.md"]
    assert [c.title for c in cards] == ["标题行", "第二张", "长"]
    assert cards[2].truncated and len(cards[2].text) < 230
    assert any("4-extra.md" in n for n in notes)
    assert any("3-long.md" in n for n in notes)
    assert cards[0].public() == {"file": "1-a.txt", "title": "标题行", "chars": 6}


def test_load_cards_missing_inbox():
    assert load_cards(None, max_cards=5, card_chars=500) == ([], [])


def test_meeting_question_lists_cards(tmp_path):
    (tmp_path / "a.md").write_text("# 记忆衰减\n正文", encoding="utf-8")
    cards, _ = load_cards(tmp_path, max_cards=5, card_chars=500)
    q = meeting_question("  请提建议。 ", cards)
    assert q.startswith("请提建议。") and "资料卡 1：记忆衰减（附件 a.md）" in q
    assert "正文" not in q  # 正文只作为附件


def test_meetings_roundtrip_and_bad_data():
    ms = [Meeting("s1", "2026-W44", "2026-10-26T09:00:00", True)]
    assert load_meetings(dump_meetings(ms)) == ms
    assert load_meetings("not json") == [] and load_meetings(None) == []


def _board() -> Board:
    ranking = (
        Ranked("I2", "加一个半衰期记忆", ("愚者",), "idea", (), 24, 2, 4.0, 1, 4.0, 4.0),
        Ranked("E1", "挑刺：记忆删错了怎么办", ("魔术师",), "fix", ("I2",), 10, 1, 3, 0, 3, 4),
        Ranked("I1", "朋友圈去重", ("女祭司",), "idea", (), 11, 1, 3.0, 0, 4.0, 4.0),
        Ranked("I3", "没人投", ("皇后",), "idea", (), 0, 0, 0.0),
    )
    return Board(ranking, top=3, compare=(CompareRow("I2", risk="可能误删"),))


def test_adoption_markdown_lists_top_with_objections():
    text = adoption_markdown(
        week="2026-W44",
        session_id="abc",
        board=_board(),
        cards=["记忆衰减.md"],
        top=3,
        cost_usd=0.1234,
        created_at="2026-10-26",
    )
    assert text.startswith("# 革新例会采纳单 · 2026-W44")
    assert "### 1. I2 加一个半衰期记忆" in text and "### 2. I1 朋友圈去重" in text
    assert "### 3." not in text  # 没票的不进采纳单；挑刺不算建议
    assert "E1（魔术师）：挑刺：记忆删错了怎么办" in text and "可能误删" in text
    assert "$0.1234" in text and "1. 记忆衰减.md" in text
    assert "附录：完整点子榜" in text


def test_adoption_markdown_without_votes():
    text = adoption_markdown(
        week="2026-W44", session_id="x", board=Board(), cards=[], top=3,
        cost_usd=0, created_at="",
    )  # fmt: skip
    assert "本周无改动" in text


def test_adoption_path_never_overwrites(tmp_path):
    assert adoption_path(tmp_path, "2026-W44").name == "2026-W44.md"
    (tmp_path / "2026-W44.md").write_text("x")
    (tmp_path / "2026-W44-2.md").write_text("x")
    assert adoption_path(tmp_path, "2026-W44").name == "2026-W44-3.md"


def test_rules_validation():
    assert not EvolutionRules().enabled  # 默认关闭
    with pytest.raises(ValidationError, match="topic"):
        EvolutionRules(enabled=True)
    with pytest.raises(ValidationError, match="monthly_usd"):
        EvolutionRules(meeting_usd=2, monthly_usd=1)
    for bad in ("../x", "a/b", "C:", ".."):
        with pytest.raises(ValidationError):
            EvolutionRules(inbox_dir=bad)


def test_repo_config_defaults():
    from roundtable.core.config import load_config

    cfg = load_config()
    rules = cfg.roundtable.evolution
    assert not rules.enabled and rules.meeting_usd <= 0.40 and rules.monthly_usd <= 1.5
    assert rules.plan in cfg.routing.plans and rules.topic.strip()
