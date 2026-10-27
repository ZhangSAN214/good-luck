"""终端首页大立绘清单：读取、校验、缺图降级、防路径穿越。"""

from __future__ import annotations

import json

from roundtable.core.standee import load_standee, standee_file

from .members_samples import PNG_1PX


def write(root, data, *images):
    root.mkdir(parents=True, exist_ok=True)
    for rel in images:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(PNG_1PX)
    path = root / "standee.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


SAMPLE = {
    "name": "立绘一",
    "variants": [
        {
            "id": "regular",
            "label": "常规",
            "image": "s/a.png",
            "head": [0.5, 0.2],
            "expressions": {
                "blink": {"image": "s/a_blink.png"},
                "smile": {"image": "s/face.png", "box": [0.4, 0.1, 0.2, 0.1]},
                "surprise": {"image": "s/gone.png"},
            },
        },
        {"id": "special", "label": "特殊", "image": "s/b.png", "cutout": False},
    ],
    "lines": {"tap": ["  嗯？ ", ""], "greet": ["欢迎回来。"]},
}


def test_loads_variants_expressions_and_lines(tmp_path):
    st = load_standee(write(tmp_path, SAMPLE, "s/a.png", "s/a_blink.png", "s/face.png", "s/b.png"))
    assert st.available and st.reason is None and st.name == "立绘一"
    a, b = st.variants
    assert (a.id, a.image, a.head, a.cutout) == ("regular", "s/a.png", (0.5, 0.2), True)
    assert a.expressions == {
        "blink": {"image": "s/a_blink.png", "box": None},
        "smile": {"image": "s/face.png", "box": [0.4, 0.1, 0.2, 0.1]},
    }
    assert a.missing == ("surprise",)  # 写了但文件不在：当作没有
    assert (b.id, b.cutout, b.expressions) == ("special", False, {})
    assert st.lines == {"tap": ["嗯？"], "greet": ["欢迎回来。"]}


def test_missing_variant_image_is_skipped_and_all_missing_is_unavailable(tmp_path):
    st = load_standee(write(tmp_path, SAMPLE, "s/b.png"))
    assert st.available and [v.id for v in st.variants] == ["special"]
    st = load_standee(write(tmp_path / "x", SAMPLE))
    assert not st.available and "找不到" in st.reason


def test_unavailable_reasons_never_raise(tmp_path):
    assert not load_standee(None).available
    assert "还没有立绘" in load_standee(tmp_path / "standee.json").reason
    bad = tmp_path / "standee.json"
    bad.write_text("{", encoding="utf-8")
    assert "无法读取" in load_standee(bad).reason
    for broken in (
        {**SAMPLE, "extra": 1},
        {**SAMPLE, "variants": []},
        {**SAMPLE, "lines": {"dance": ["x"]}},
        {
            **SAMPLE,
            "variants": [{**SAMPLE["variants"][0], "expressions": {"wink": {"image": "a"}}}],
        },  # noqa: E501
        {**SAMPLE, "variants": [{**SAMPLE["variants"][1], "id": "../x"}]},
        {**SAMPLE, "variants": [{**SAMPLE["variants"][0], "head": [2, 0]}]},
    ):
        st = load_standee(write(tmp_path / "b", broken, "s/a.png", "s/b.png"))
        assert not st.available and "不对" in st.reason, broken


def test_duplicate_ids_and_out_of_range_box(tmp_path):
    dup = {**SAMPLE, "variants": [SAMPLE["variants"][1], SAMPLE["variants"][1]]}
    assert "重复" in load_standee(write(tmp_path, dup, "s/b.png")).reason
    box = {"image": "s/a.png", "box": [0.9, 0.1, 0.2, 0.1]}
    over = {**SAMPLE, "variants": [{**SAMPLE["variants"][1], "expressions": {"smile": box}}]}
    assert "不对" in load_standee(write(tmp_path / "o", over, "s/b.png")).reason


def test_files_stay_inside_the_manifest_folder(tmp_path):
    (tmp_path / "secret.png").write_bytes(PNG_1PX)
    st = load_standee(write(tmp_path / "root", SAMPLE, "s/a.png", "s/b.png"))
    (tmp_path / "root" / "s" / "note.txt").write_text("x", encoding="utf-8")
    assert standee_file(st, "s/a.png") == (PNG_1PX, "image/png")
    for bad in ("../secret.png", "s/note.txt", "/etc/passwd", "s/none.png", ""):
        assert standee_file(st, bad) is None
