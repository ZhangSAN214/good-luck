"""scripts/standee_rig.py：瞳孔层、补眼白、权重图与 rig.json。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "standee_rig.py"


def load():
    spec = importlib.util.spec_from_file_location("standee_rig", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make(tmp_path):
    img = Image.new("RGB", (200, 300), (27, 35, 56))
    px = img.load()
    for y in range(40, 300):
        for x in range(60, 140):
            px[x, y] = (240, 225, 215)  # 身体
    for y in range(10, 260):
        for x in list(range(20, 60)) + list(range(140, 180)):
            px[x, y] = (225, 110, 40)  # 两侧长发
    for y in range(70, 82):
        for x in range(85, 115):
            px[x, y] = (250, 250, 250)  # 眼白
    for y in range(71, 81):
        for x in range(95, 105):
            px[x, y] = (150, 90, 30)  # 瞳孔
    img.save(tmp_path / "a.png")
    cfg = {
        "image": "a.png",
        "out": "rig",
        "eyes": [{"opening": [[85, 70], [115, 70], [115, 82], [85, 82]], "iris": [100, 76, 5, 5]}],
        "pivots": {
            "neck": [100, 100],
            "face": [100, 76],
            "elbow": [70, 180],
            "waist": [100, 180],
            "feet": [100, 400],
        },
        "regions": {
            "head": {"ellipse": [100, 70, 50, 50], "feather": 10, "fade_y": [100, 120]},
            "face": {"gauss": [100, 76, 15, 15]},
            "chest": {"poly": [[60, 100], [140, 100], [140, 180], [60, 180]], "feather": 20},
            "arm": {"poly": [[60, 120], [90, 120], [90, 200], [60, 200]], "feather": 10},
            "hair_l": {
                "poly": [[10, 0], [60, 0], [60, 270], [10, 270]],
                "hair": True,
                "root": [40, 10],
                "tip": [40, 260],
            },
            "hair_r": {
                "poly": [[140, 0], [190, 0], [190, 270], [140, 270]],
                "hair": True,
                "root": [160, 10],
                "tip": [160, 260],
            },
            "front": {
                "poly": [[80, 40], [120, 40], [120, 60], [80, 60]],
                "hair": True,
                "root": [100, 40],
                "tip": [100, 60],
            },
            "skirt": {
                "poly": [[0, 180], [200, 180], [200, 300], [0, 300]],
                "feather": 20,
                "top": 180,
                "bottom": 300,
                "ramp": 60,
            },
            "acc": {
                "parts": [
                    {
                        "poly": [[90, 200], [110, 200], [110, 240], [90, 240]],
                        "root": [100, 200],
                        "tip": [100, 240],
                    }
                ]
            },
        },
    }
    path = tmp_path / "rig_a.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def test_builds_layers_weights_and_manifest(tmp_path):
    mod = load()
    out = mod.build(make(tmp_path))
    rig = json.loads((out / "rig.json").read_text(encoding="utf-8"))
    assert rig["base"] == "rig/base.png" and rig["weights"] == [
        "rig/w1.png",
        "rig/w2.png",
        "rig/w3.png",
    ]
    assert rig["size"] == [200, 300] and rig["skirt"] == [180, 300]
    base = np.asarray(Image.open(out / "base.png"))
    iris = np.asarray(Image.open(out / "iris.png"))
    # 瞳孔被挖掉、补成眼白；瞳孔层里有瞳孔且是透明底
    assert base[76, 100].mean() > 200
    assert iris[76, 100, 3] == 255 and iris[76, 100, 0] < 200 and iris[0, 0, 3] == 0
    w1 = np.asarray(Image.open(out / "w1.png")).astype(int)
    w2 = np.asarray(Image.open(out / "w2.png")).astype(int)
    w3 = np.asarray(Image.open(out / "w3.png")).astype(int)
    assert w1[60, 100, 0] > 200 and w1[200, 100, 0] < 10  # 头在头部、身体下面没有
    assert w2[200, 30, 0] > w2[200, 100, 0]  # 左侧长发在头发上比在身体上大
    assert w2[290, 100, 3] > 200 and w2[100, 100, 3] < 10  # 裙摆只在下半身
    assert w3[250, 40, 1] > w3[30, 40, 1]  # 越靠发梢「沿发丝的位置」越大
    assert w3[76, 100, 2] > 200 and w3[30, 100, 2] == 0  # 眼眶开口


def test_main_reports_output(tmp_path, capsys):
    mod = load()
    assert mod.main([str(make(tmp_path))]) == 0
    assert "rig.json" in capsys.readouterr().out
