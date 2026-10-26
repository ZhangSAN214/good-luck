"""半衰期记忆的召回：切片段、鲜活度、事实不变淡、★ 总是带、相关的排前面、上限、原因。"""

from __future__ import annotations

from datetime import date

from roundtable.core.recall import freshness, grams, pieces, recall, recall_notes, render

TODAY = date(2026, 10, 26)


def row(mid: int, layer: str, text: str, *, period: str = "", source: str = "auto") -> dict:
    return {
        "id": mid,
        "layer": layer,
        "period": period,
        "text": text,
        "source": source,
        "created_at": "2026-10-01T00:00:00+00:00",
    }


def test_grams_ignore_punctuation_and_need_two_chars():
    assert grams("拉面！") == {"拉面"}
    assert grams("车") == set()
    assert {"面馆", "那家面馆"} <= grams("那家面馆")


def test_freshness_halves_every_half_life_and_facts_do_not_fade():
    assert freshness(date(2026, 9, 26), TODAY, 30) == 0.5
    assert freshness(TODAY, TODAY, 30) == 1
    assert freshness(None, TODAY, 30) == 1


def test_pieces_split_lines_and_mark_facts():
    rows = [
        row(1, "profile", "喜欢户外\n讨厌香菜"),
        row(2, "day", "★用户说不要催他\n聊了拉面", period="2026-10-20"),
        row(3, "note", "用户写的随手记", source="user"),
        row(4, "week", "那周很忙", period="2026-W42"),
    ]
    ps = pieces(rows)
    assert [(p.text, p.fact, p.importance) for p in ps] == [
        ("喜欢户外", True, 2),
        ("讨厌香菜", True, 2),
        ("用户说不要催他", True, 3),
        ("聊了拉面", False, 1),
        ("用户写的随手记", True, 2),
        ("那周很忙", False, 1),
    ]
    assert ps[3].when == date(2026, 10, 20) and ps[0].when is None


def test_related_old_memory_beats_unrelated_recent_and_star_always_comes():
    rows = [
        row(1, "day", "★用户要求说话简短", period="2026-08-01"),
        row(2, "day", "和用户去了一家叫一兰的拉面店", period="2026-09-01"),
        *[row(10 + i, "day", f"今天整理了第{i}份文件", period=f"2026-10-2{i}") for i in range(5)],
    ]
    chosen = recall(rows, "上次那家拉面店叫什么来着", TODAY, half_life_days=30, inject_max=3)
    texts = [p.text for p in chosen]
    assert "用户要求说话简短" in texts  # ★ 不管多久、相不相关都带
    assert "和用户去了一家叫一兰的拉面店" in texts  # 相关的旧事压过不相关的新事
    assert len(chosen) == 3
    notes = recall_notes(chosen)
    assert notes[1] == "标了重要，总是记得"
    assert "『拉面店』" in notes[2]
    out = render(chosen)
    assert out.splitlines()[0] == "【长期】用户要求说话简短"  # 事实在前


def test_without_query_newest_events_win():
    rows = [row(i, "day", f"第{i}天的事情记录", period=f"2026-10-{10 + i:02d}") for i in range(8)]
    chosen = recall(rows, "", TODAY, half_life_days=30, inject_max=2)
    assert [p.memory_id for p in chosen] == [6, 7]
    assert set(recall_notes(chosen).values()) == {"最近的事"}


def test_common_fragments_do_not_count_as_hits():
    rows = [row(i, "day", f"和用户聊天{i}", period="2026-10-20") for i in range(4)]
    chosen = recall(rows, "和用户", TODAY, half_life_days=30, inject_max=4)
    assert all(not p.hits for p in chosen)  # 每条都有「和用户」，不算命中


def test_empty():
    assert recall([], "随便", TODAY, half_life_days=30, inject_max=6) == []
