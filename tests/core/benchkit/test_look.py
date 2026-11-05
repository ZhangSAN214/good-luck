"""每个台有自己的样子、评分标尺（生成器第二版第 4 块）。"""

from __future__ import annotations

import base64
import random

import pytest

from roundtable.core.benchkit.look import (
    copy_ruler,
    distinct,
    recent_looks,
    ruler_name,
    ruler_refs,
    ruler_text,
    same_keys,
    signature,
)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
OPTIONS = {
    "theme": ["ink", "ocean", "crimson", "forest"],
    "type": ["sans", "serif", "mono"],
    "material": ["solid", "glass", "paper"],
    "bg": ["soft", "plain", "grid"],
}


def design(**picks):
    return {"picks": {"type": "sans", "material": "solid", "bg": "soft"} | picks}


def test_alike_counts_hue_not_exact_hex():
    a = signature(design(accent="#2f5bea"), "ocean")
    b = signature(design(accent="#3060f0"), "ocean")
    assert same_keys(a, b) == ["theme", "accent", "type", "material", "bg"]
    c = signature(design(accent="#ea2f2f", type="serif"), "crimson")
    assert same_keys(a, c) == ["material", "bg"]


def test_distinct_changes_only_what_collides():
    mine = design(accent="#2f5bea")
    recent = [{"title": "旅行台", "theme": "ocean", "design": design(accent="#3366ee")}]
    theme, changes = distinct(mine, "ocean", recent, OPTIONS, random.Random(1))
    keys = [c["key"] for c in changes]
    assert keys == ["theme", "accent", "type"]  # 5 项全撞：换掉前 3 项，只剩 2 项一样
    assert theme != "ocean" and mine["picks"]["theme"] == theme
    new_accent = signature(design(accent=mine["picks"]["accent"]), "x")
    assert "accent" not in same_keys(new_accent, signature(design(accent="#3366ee"), "y"))
    assert mine["picks"]["type"] in ("serif", "mono")
    assert all("旅行台" in c["why"] for c in changes)
    same = design(accent="#e11d48", type="serif", material="glass", bg="grid")
    assert distinct(same, "crimson", recent, OPTIONS, random.Random(1)) == ("crimson", [])


def test_recent_looks_reads_specs(tmp_path):
    import json

    for i, theme in enumerate(["ocean", "ink"]):
        d = tmp_path / f"2026-11-0{i + 1}-x"
        d.mkdir()
        (d / "spec.json").write_text(
            json.dumps({"title": f"台{i}", "theme": theme, "design": design()}), encoding="utf-8"
        )
    (tmp_path / "2026-11-09-me").mkdir()
    looks = recent_looks(tmp_path, skip="2026-11-09-me")
    assert [x["title"] for x in looks] == ["台1", "台0"]
    assert recent_looks(None, "x") == []


def test_ruler_files_and_text(tmp_path):
    src = tmp_path / "_ruler"
    src.mkdir()
    (src / ruler_name(8, "清爽的 仪表盘!", ".png")).write_bytes(PNG)
    (src / "6分.jpg").write_bytes(b"x")
    (src / "随便.png").write_bytes(PNG)  # 文件名没写分数：不算
    (src / "11分-太高.png").write_bytes(PNG)
    refs = ruler_refs(src)
    assert [(r["score"], r["note"]) for r in refs] == [(8, "清爽的-仪表盘"), (6, "")]
    got = copy_ruler(src, tmp_path / "bench" / "ruler")
    assert (tmp_path / "bench" / "ruler" / refs[0]["file"]).read_bytes() == PNG
    assert got == refs
    text = ruler_text(refs)
    assert "ruler/8分-清爽的-仪表盘.png：这是 8 分的样子（清爽的-仪表盘）" in text
    assert "没有标尺" in ruler_text([])
    assert copy_ruler(tmp_path / "nope", tmp_path / "x") == []


# --- 接进生成器 ------------------------------------------------------------------------


async def test_builder_switches_look_and_hands_ruler_to_review(tmp_path):
    from tests.core.benchkit.test_builder import FakeSC, make

    first = make(tmp_path, FakeSC())
    b1 = first.submit("帮我做个东京三天旅行的工作台")
    await first.wait()
    assert not b1.get("look_changes")
    first.add_ruler("ref.png", PNG, 8, "清爽")
    with pytest.raises(ValueError, match="不是一张图片"):
        first.add_ruler("ref.png", b"<html>", 8)
    with pytest.raises(ValueError, match="只收"):
        first.add_ruler("ref.gif", PNG, 8)
    with pytest.raises(ValueError, match="0～10"):
        first.add_ruler("ref.png", PNG, 12)
    sc = FakeSC()
    second = make(tmp_path, sc)
    b2 = second.submit("帮我做个京都三天旅行的工作台")
    await second.wait()
    assert b2["status"] == "done"
    assert b2["look_changes"] and "旅行台" in b2["look_changes"][0]["why"]
    assert any("撞了样子" in x["text"] for x in b2["log"])
    review = next(
        p for c, p in zip(sc.calls, sc.prompts, strict=True) if c["role"] == "bench_review"
    )
    assert "ruler/8分-清爽.png：这是 8 分的样子" in review
    assert (tmp_path / b2["id"] / "ruler" / "8分-清爽.png").is_file()
    assert b2["rounds"][0]["ruler"] == [{"file": "8分-清爽.png", "score": 8}]
    assert second.drop_ruler("8分-清爽.png") == []
    with pytest.raises(LookupError):
        second.drop_ruler("8分-清爽.png")
