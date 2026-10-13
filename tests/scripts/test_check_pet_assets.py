"""scripts/check_pet_assets.py 的测试：用临时目录里的假图片，不读用户本机素材。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from roundtable.core.members_art import PET_ACTIONS, PET_EXPRESSIONS, load_members  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_pet_assets.py"
spec = importlib.util.spec_from_file_location("check_pet_assets", SCRIPT)
check = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check
spec.loader.exec_module(check)

SIZE = 64


def good(path: Path, size: int = SIZE, box=None) -> None:
    """透明背景、脚底在底边正中的小方块。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    box = box or (size // 2 - 10, size // 2, size // 2 + 10, size - 2)
    ImageDraw.Draw(im).rectangle(box, fill=(200, 80, 80, 255))
    im.save(path)


def member(root: Path, mid: str = "m1", actions=("idle",), faces=()) -> Path:
    for a in actions:
        for i in range(check.ACTION_SPEC[a][0]):
            good(root / mid / a / f"{i:02d}.png")
    for f in faces:
        good(root / mid / "face" / f / "00.png")
    return root / mid


def run(root, mid="m1"):
    return check.check_member(root, mid, SIZE)


def test_minimal_member_passes_with_warnings(tmp_path):
    member(tmp_path)
    r = run(tmp_path)
    assert r.ok and not r.errors
    assert any("缺少动作 walk" in w for w in r.warnings)
    assert r.pet["actions"]["idle"]["frames"] == ["idle/00.png", "idle/01.png"]
    assert r.pet["actions"]["idle"]["loop"] is True


def test_missing_idle_is_error(tmp_path):
    member(tmp_path, actions=("wave",))
    r = run(tmp_path)
    assert not r.ok and any("idle" in e for e in r.errors)


def test_missing_member_folder(tmp_path):
    assert not check.check_member(tmp_path, "nobody", SIZE).ok


def test_wrong_size_and_no_alpha_and_background(tmp_path):
    member(tmp_path)
    good(tmp_path / "m1" / "idle" / "00.png", size=SIZE * 2)
    Image.new("RGB", (SIZE, SIZE), (255, 255, 255)).save(tmp_path / "m1" / "idle" / "01.png")
    r = run(tmp_path)
    assert any("尺寸" in e for e in r.errors)
    assert any("没有透明通道" in e for e in r.errors)
    Image.new("RGBA", (SIZE, SIZE), (255, 255, 255, 255)).save(tmp_path / "m1" / "idle" / "01.png")
    assert any("角不透明" in e for e in run(tmp_path).errors)


def test_empty_image_and_corrupt_file(tmp_path):
    member(tmp_path)
    Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0)).save(tmp_path / "m1" / "idle" / "00.png")
    (tmp_path / "m1" / "idle" / "01.png").write_bytes(b"not an image")
    errors = run(tmp_path).errors
    assert any("全是透明" in e or "整张图都是透明" in e for e in errors)
    assert any("打不开" in e for e in errors)


def test_bad_names_and_gaps(tmp_path):
    member(tmp_path)
    good(tmp_path / "m1" / "idle" / "frame.png")
    r = run(tmp_path)
    assert any("文件名应为" in e for e in r.errors)
    (tmp_path / "m1" / "idle" / "frame.png").unlink()
    (tmp_path / "m1" / "idle" / "01.png").rename(tmp_path / "m1" / "idle" / "03.png")
    assert any("序号不连续" in e for e in run(tmp_path).errors)


def test_anchor_and_edge_warnings(tmp_path):
    member(tmp_path)
    good(tmp_path / "m1" / "idle" / "00.png", box=(2, 10, 20, 30))  # 悬空、偏左
    warnings = run(tmp_path).warnings
    assert any("脚底离画布底边" in w for w in warnings)
    assert any("水平偏离" in w for w in warnings)
    good(tmp_path / "m1" / "idle" / "00.png", box=(0, 20, 30, 50))
    assert any("贴到画布边缘" in w for w in run(tmp_path).warnings)


def test_jump_skips_anchor_check(tmp_path):
    member(tmp_path, actions=("idle", "jump"))
    good(tmp_path / "m1" / "jump" / "00.png", box=(25, 5, 40, 20))
    assert not any("脚底" in w for w in run(tmp_path).warnings)


def test_unknown_folders_ignored_with_warning(tmp_path):
    member(tmp_path, faces=("happy",))
    good(tmp_path / "m1" / "dance" / "00.png")
    good(tmp_path / "m1" / "face" / "evil" / "00.png")
    r = run(tmp_path)
    assert r.ok
    assert any("dance" in w for w in r.warnings) and any("evil" in w for w in r.warnings)
    assert set(r.pet["expressions"]) == {"happy"}


def test_generated_pet_json_is_loaded_by_the_app(tmp_path):
    root = tmp_path
    member(root, actions=("idle", "wave", "walk"), faces=("happy", "sad"))
    (root / "members.json").write_text(
        json.dumps({"version": 2, "members": [{"id": "m1", "name": "M", "vendor": "V"}]}),
        encoding="utf-8",
    )
    r = run(root)
    assert r.ok
    path = check.write_pet(root, r)
    assert path == root / "m1" / "pet.json"
    art = load_members(root)
    pet = next(iter(art.members)).pet
    assert pet and set(pet["actions"]) == {"idle", "wave", "walk"}
    assert set(pet["expressions"]) == {"happy", "sad"}
    assert pet["actions"]["wave"]["loop"] is False


def test_write_refused_when_failed(tmp_path):
    member(tmp_path)
    (tmp_path / "m1" / "idle" / "00.png").write_bytes(b"x")
    r = run(tmp_path)
    assert check.write_pet(tmp_path, r) is None
    assert not (tmp_path / "m1" / "pet.json").exists()


def test_spec_covers_every_action():
    assert set(check.ACTION_SPEC) == set(PET_ACTIONS)
    assert len(PET_EXPRESSIONS) == 9


def test_main_exit_codes(tmp_path, capsys):
    assert check.main(["--dir", str(tmp_path / "none")]) == 2
    member(tmp_path)
    assert check.main(["--dir", str(tmp_path), "--size", str(SIZE), "--write"]) == 0
    assert (tmp_path / "m1" / "pet.json").is_file()
    (tmp_path / "m1" / "idle" / "00.png").write_bytes(b"x")
    assert check.main(["--dir", str(tmp_path), "--size", str(SIZE)]) == 1
    assert "不通过" in capsys.readouterr().out


def test_member_discovery_uses_members_json(tmp_path):
    member(tmp_path, "a")
    member(tmp_path, "b")
    assert check.member_ids(tmp_path, []) == ["a", "b"]
    (tmp_path / "members.json").write_text(
        json.dumps({"members": [{"id": "b"}, {"id": "ghost"}]}), encoding="utf-8"
    )
    assert check.member_ids(tmp_path, []) == ["b"]
    assert check.member_ids(tmp_path, ["a"]) == ["a"]
