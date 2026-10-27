"""scripts/room_art.py 的测试：临时目录里的假图，不读用户本机素材。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "room_art.py"
spec = importlib.util.spec_from_file_location("room_art", SCRIPT)
room_art = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = room_art
spec.loader.exec_module(room_art)


def scene(path: Path) -> Path:
    """棕色房间，中间一块纯绿窗玻璃，玻璃边上一圈抗锯齿的半绿。"""
    im = Image.new("RGB", (50, 20), (120, 80, 50))
    for x in range(20, 30):
        for y in range(5, 12):
            im.putpixel((x, y), (0, 255, 0))
    for y in range(5, 12):
        im.putpixel((19, y), (90, 160, 60))
    im.save(path)
    return path


def test_member_room_keys_out_green_glass(tmp_path, capsys):
    src = scene(tmp_path / "in.png")
    assert room_art.main([str(src), "--dir", str(tmp_path / "a"), "--member", "m1"]) == 0
    out = tmp_path / "a" / "members" / "m1" / "room_day.webp"
    im = Image.open(out).convert("RGBA")
    assert im.getpixel((25, 8))[3] == 0  # 窗玻璃透明
    assert im.getpixel((2, 2))[3] == 255  # 房间不透明
    r, g, b, a = im.getpixel((19, 8))
    assert 0 < a < 255 and g <= max(r, b) + 8  # 边缘半透明、去掉了绿色溢出
    assert "抠掉" in capsys.readouterr().out


def test_night_sky_and_bad_names(tmp_path, capsys):
    src = scene(tmp_path / "in.png")
    d = tmp_path / "a"
    assert room_art.main([str(src), "--dir", str(d), "--member", "m1", "--night"]) == 0
    assert (d / "members" / "m1" / "room_night.webp").is_file()
    assert room_art.main([str(src), "--dir", str(d), "--sky", "night_rain"]) == 0
    sky = Image.open(d / "sky" / "night_rain.webp")
    assert sky.mode == "RGB"  # 天空图不抠绿
    assert room_art.main([str(src), "--dir", str(d), "--sky", "noon_fog"]) == 1
    assert room_art.main([str(tmp_path / "nope.png"), "--member", "m1"]) == 1


def test_no_green_is_reported(tmp_path, capsys):
    src = tmp_path / "plain.png"
    Image.new("RGB", (10, 10), (100, 100, 100)).save(src)
    assert room_art.main([str(src), "--out", str(tmp_path / "o.webp")]) == 0
    assert "没有找到绿色窗玻璃" in capsys.readouterr().out
