"""上下文组装器：固定前缀在前、总长预算、每个来源上限、按相关度挑、截断、可复现。"""

from __future__ import annotations

from roundtable.core.member.context import Piece, assemble


def test_fixed_prefix_first_and_stable():
    pieces = [
        Piece("memory", "昨天下雨", score=0.9),
        Piece("card", "我是阿一，爱看书", fixed=True),
        Piece("rules", "只说一两句", fixed=True),
    ]
    ctx = assemble(pieces, budget_chars=600)
    assert ctx.prefix == "我是阿一，爱看书\n只说一两句"
    assert ctx.body == "昨天下雨"
    assert ctx.text.startswith(ctx.prefix)
    # 可变部分不同，前缀不变（利于缓存）
    other = assemble([*pieces[1:], Piece("mood", "有点困", score=0.5)])
    assert other.prefix == ctx.prefix


def test_budget_is_never_exceeded_and_best_first():
    pieces = [Piece("memory", f"记忆{i}" * 20, score=i) for i in range(10)]
    ctx = assemble(pieces, budget_chars=100, per_source=10)
    assert ctx.chars <= 100
    assert ctx.used[0].text.startswith("记忆9")
    assert ctx.dropped


def test_per_source_limit_and_tie_order():
    pieces = [
        Piece("memory", "a", 1),
        Piece("memory", "b", 1),
        Piece("memory", "c", 1),
        Piece("mood", "d", 1),
    ]
    ctx = assemble(pieces, per_source=2)
    assert [p.text for p in ctx.used] == ["a", "b", "d"]
    assert [p.text for p in ctx.dropped] == ["c"]
    assert assemble(pieces, per_source=2).text == ctx.text


def test_last_piece_truncated_when_room_left():
    ctx = assemble([Piece("card", "甲" * 50, fixed=True), Piece("memory", "乙" * 100, 1)], 80)
    assert ctx.chars <= 80
    assert ctx.used[-1].text.endswith("…")
    tiny = assemble([Piece("card", "甲" * 70, fixed=True), Piece("memory", "乙" * 100, 1)], 80)
    assert [p.source for p in tiny.used] == ["card"]


def test_empty_pieces_skipped():
    assert assemble([Piece("x", "   "), Piece("y", "")]).text == ""
