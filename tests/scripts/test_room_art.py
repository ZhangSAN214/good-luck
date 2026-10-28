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
    im.putpixel((5, 17), (0, 255, 0))  # 地板上一点绿色倒影：不能抠穿
    im.save(path)
    return path


def test_member_room_keys_out_green_glass(tmp_path, capsys):
    src = scene(tmp_path / "in.png")
    assert room_art.main([str(src), "--dir", str(tmp_path / "a"), "--member", "m1"]) == 0
    out = tmp_path / "a" / "members" / "m1" / "room_day.webp"
    im = Image.open(out).convert("RGBA")
    assert im.getpixel((25, 8))[3] == 0  # 窗玻璃透明
    assert im.getpixel((2, 2))[3] == 255  # 房间不透明
    assert im.getpixel((5, 17))[3] == 255  # 零星的绿不抠
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


def test_install_pack_into_members_json(tmp_path, capsys):
    import json

    root = tmp_path / "assets"
    (root / "members" / "c").mkdir(parents=True)
    members = [
        {
            "id": "c",
            "name": "C",
            "nickname": "克劳德",
            "vendor": "v",
            "images": {"chibi": "members/c/chibi.png"},
        },
        {"id": "w", "name": "W", "vendor": "v2", "images": {}},
    ]
    (root / "members.json").write_text(json.dumps({"members": members}), encoding="utf-8")
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "克劳德.webp").write_bytes(b"day")
    (pack / "w.webp").write_bytes(b"w")
    room = {"bed": {"x": 10, "y": 57}, "hots": []}
    rooms = {
        "克劳德": {"day": "克劳德.webp", "room": room},
        "w": {"day": "w.webp"},
        "谁": {"day": "x.webp"},
    }
    (pack / "rooms.json").write_text(json.dumps(rooms, ensure_ascii=False), encoding="utf-8")
    (pack / "sky").mkdir()
    (pack / "sky" / "day_clear.webp").write_bytes(b"sky")
    assert room_art.main(["--install", str(pack), "--dir", str(root)]) == 0
    assert (root / "sky" / "day_clear.webp").read_bytes() == b"sky"
    data = json.loads((root / "members.json").read_text(encoding="utf-8"))
    c, w = data["members"]
    assert c["images"]["room_day"] == "members/c/room_day.webp" and c["room"] == room
    assert (root / "members" / "c" / "room_day.webp").read_bytes() == b"day"
    assert w["images"]["room_day"] == "members/w/room_day.webp" and "room" not in w
    assert (root / "members.json.bak").is_file()
    out = capsys.readouterr().out
    assert "谁：members.json 里没有这位成员" in out and "天空图：1 张" in out
    assert room_art.main(["--install", str(tmp_path), "--dir", str(root)]) == 1
