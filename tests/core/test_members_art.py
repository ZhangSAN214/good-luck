"""成员形象素材：读取 members.json、容错、防路径穿越、按 vendor 对应模型。"""

from __future__ import annotations

import json

from roundtable.core.members_art import load_members, resolve_file

from .members_samples import PNG_1PX, write_assets


def test_reads_members_and_resolves_images(tmp_path):
    root = write_assets(tmp_path / "assets", "VendorA")
    art = load_members(root)
    assert art.available and art.reason is None
    (m,) = art.members
    assert (m.id, m.name, m.nickname, m.display, m.theme_color) == (
        "m1",
        "成员一",
        "小一",
        "小一",
        "#3366cc",
    )
    assert m.images["avatar_circle_light"] == {
        "256": None,  # 文件不存在记 None
        "128": "m1/c128.png",
        "64": "m1/c64.png",
    }
    assert m.images["chibi_transparent"] == "m1/chibi.png"
    assert m.images["avatar"] is None


def test_missing_directory_is_unavailable(tmp_path):
    art = load_members(tmp_path / "nowhere")
    assert not art.available and art.reason and art.members == ()
    assert not load_members(None).available
    (tmp_path / "empty").mkdir()
    assert not load_members(tmp_path / "empty").available


def test_broken_json_is_unavailable(tmp_path):
    (tmp_path / "members.json").write_text("{not json", encoding="utf-8")
    art = load_members(tmp_path)
    assert not art.available and "members.json" in art.reason
    (tmp_path / "members.json").write_text('{"members": [{"id": 1}]}', encoding="utf-8")
    assert not load_members(tmp_path).available
    (tmp_path / "members.json").write_bytes(b"\xff\xfe\x00")
    assert not load_members(tmp_path).available


def test_path_traversal_is_rejected(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    (tmp_path / "secret.png").write_bytes(PNG_1PX)
    (root / "ok.png").write_bytes(PNG_1PX)
    (root / "note.txt").write_text("x", encoding="utf-8")
    assert resolve_file(root, "ok.png") == (root / "ok.png").resolve()
    for bad in (
        "../secret.png",
        "sub/../../secret.png",
        str(tmp_path / "secret.png"),
        "/etc/passwd",
        "note.txt",
        "ok.png\x00.txt",
        "",
    ):
        assert resolve_file(root, bad) is None, bad
    assert resolve_file(None, "ok.png") is None

    write_assets(root, "VendorA")
    data = json.loads((root / "members.json").read_text(encoding="utf-8"))
    data["members"][0]["images"]["chibi_transparent"] = "../secret.png"
    (root / "members.json").write_text(json.dumps(data), encoding="utf-8")
    assert load_members(root).members[0].images["chibi_transparent"] is None


def test_bad_theme_color_is_dropped(tmp_path):
    extra = [{"id": "m2", "name": "二", "vendor": "VendorB", "theme_color": "red;}x{", "order": 2}]
    art = load_members(write_assets(tmp_path, "VendorA", extra=extra))
    assert [m.id for m in art.members] == ["m1", "m2"]
    assert art.members[1].theme_color is None
    assert art.members[1].display == "二"


def test_vendor_maps_models_to_members(tmp_path):
    extra = [{"id": "m2", "name": "二", "vendor": "VendorB", "order": 2}]
    art = load_members(write_assets(tmp_path, "VendorA", extra=extra))
    models = [("x-1", "vendora"), ("x-2", "VendorA"), ("y-1", " VendorB "), ("z-1", "Other")]
    assert art.model_map(models) == {"x-1": "m1", "x-2": "m1", "y-1": "m2"}
