"""scripts/check_pet_art.py 的测试：用代码生成小图，不依赖真实素材。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_pet_art.py"
spec = importlib.util.spec_from_file_location("check_pet_art", SCRIPT)
tool = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tool
spec.loader.exec_module(tool)


def png(path: Path, *, foot=0.92, size=tool.CANVAS, alpha=255):
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    h = size[1]
    y1 = int(h * foot)
    block = Image.new("RGBA", (100, 50), (10, 20, 30, alpha))
    im.paste(block, (min(100, size[0] - 100), max(0, y1 - 50)))
    im.save(path)


def fill(folder: Path, member="whale", skip=()):
    for name, (need, *_rest) in tool.ACTIONS.items():
        if name in skip:
            continue
        for i in range(need):
            png(folder / f"{member}_{name}_k{i}.png")


def test_complete_set_writes_pet_json(tmp_path):
    fill(tmp_path)
    png(tmp_path / "whale_face_happy.png")
    assert tool.main([str(tmp_path), "--member", "whale", "--write", "--strict"]) == 0
    pet = json.loads((tmp_path / "pet.json").read_text(encoding="utf-8"))
    assert pet["actions"]["idle"]["frames"] == [
        "whale_idle_k0.png",
        "whale_idle_k1.png",
        "whale_idle_k2.png",
    ]
    assert pet["actions"]["idle"]["loop"] is True
    assert pet["expressions"]["happy"]["frames"] == ["whale_face_happy.png"]


def test_pet_json_is_accepted_by_backend_loader(tmp_path):
    from roundtable.core.members_art import _pet

    fill(tmp_path)
    tool.main([str(tmp_path), "--member", "whale", "--write"])
    pet = _pet(tmp_path, "whale", "pet.json")
    assert pet is not None and "idle" in pet["actions"]


def test_missing_required_action_is_error(tmp_path, capsys):
    fill(tmp_path, skip=("land",))
    assert tool.main([str(tmp_path), "--member", "whale", "--write"]) == 1
    assert "缺少动作 land" in capsys.readouterr().out
    assert not (tmp_path / "pet.json").exists()


def test_optional_missing_is_warning_only(tmp_path):
    fill(tmp_path, skip=("walk", "jump", "sleep"))
    assert tool.main([str(tmp_path), "--member", "whale"]) == 0
    assert tool.main([str(tmp_path), "--member", "whale", "--strict"]) == 1


def test_bad_images_reported(tmp_path, capsys):
    fill(tmp_path)
    png(tmp_path / "whale_idle_k0.png", size=(100, 100))
    png(tmp_path / "whale_wave_k0.png", alpha=100)
    png(tmp_path / "whale_talk_k0.png", foot=0.5)
    (tmp_path / "whale_type_k0.png").write_bytes(b"not a png")
    assert tool.main([str(tmp_path), "--member", "whale"]) == 1
    out = capsys.readouterr().out
    assert "尺寸 100×100" in out
    assert "半透明" in out
    assert "脚底" in out
    assert "打不开" in out


def test_naming_and_gaps(tmp_path, capsys):
    fill(tmp_path)
    (tmp_path / "whale_idle_k1.png").rename(tmp_path / "whale_idle_k5.png")
    png(tmp_path / "other_idle_k0.png")
    png(tmp_path / "whale_dance_k0.png")
    assert tool.main([str(tmp_path), "--member", "whale"]) == 1
    out = capsys.readouterr().out
    assert "序号不连续" in out
    assert "命名不符" in out
    assert "未知动作 dance" in out


def test_expressions_match_backend():
    from roundtable.core import members_art

    assert tuple(tool.EXPRESSIONS) == tuple(members_art.PET_EXPRESSIONS)
    assert set(tool.ACTIONS) <= set(members_art.PET_ACTIONS)
