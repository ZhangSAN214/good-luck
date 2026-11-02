"""scripts/live2d_import.py：图层名映射、效果层合并、给两个 Live2D 工具的输入。"""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "live2d_import.py"

# 克劳德 hd_v3 素材里全部 53 个部件层和表情层的名字（效果层合并进它们）
HD_V3_PARTS = [
    "back hair-upper",
    "back hair-lower",
    "footwear",
    "neck",
    "inner skirt",
    "overskirt",
    "skirt bow-l",
    "skirt bow-r",
    "skirt hem ruffle",
    "topwear",
    "collar ruffle",
    "jabot",
    "cravat",
    "brooch",
    "handwear-l",
    "cuff-l",
    "ears-r",
    "eyebrow-r",
    "ears-l",
    "eyebrow-l",
    "face",
    "eye_closed-l",
    "eye_closed-r",
    "mouth_open-a",
    "mouth_open-o",
    "beauty mark",
    "nose",
    "mouth",
    "eyewhite-r",
    "irides-r",
    "eyelash-r",
    "eyewhite-l",
    "handwear-r",
    "cuff-r",
    "irides-l",
    "eyelash-l",
    "earwear",
    *[f"bangs-{i}" for i in range(1, 9)],
    *[f"side hair-{s}-{i}" for s in "rl" for i in (1, 2, 3)],
    "headwear",
    "hair flower",
]


def load():
    spec = importlib.util.spec_from_file_location("live2d_import", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live2d_import"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_split_name():
    m = load()
    assert m.split_name("side hair-l-2") == ("side hair", "l", "2")
    assert m.split_name("bangs-3") == ("bangs", None, "3")
    assert m.split_name("mouth_open-a") == ("mouth_open", None, "a")
    assert m.split_name("eyelash-r") == ("eyelash", "r", None)
    assert m.split_name("back hair-upper") == ("back hair", None, "upper")
    assert m.split_name("face") == ("face", None, None)


def test_every_hd_v3_layer_is_mapped():
    m = load()
    assert len(HD_V3_PARTS) == 53
    for name in HD_V3_PARTS:
        m.i2l_role(name)  # 没有映射会抛 KeyError
        m.p2l_name(name)


def test_image2live2d_roles():
    m = load()
    assert m.i2l_role("eyelash-l") == "eye_l"
    assert m.i2l_role("irides-r") == "pupil_r"
    assert m.i2l_role("eye_closed-l") == "eye_closed_l"
    assert m.i2l_role("side hair-r-2") == "hair_side"
    assert m.i2l_role("topwear") == "torso"
    assert m.i2l_role("cuff-r") == "arm_r"
    # 它自己合成口腔：张嘴表情层不给它；泪痣并进脸
    assert m.i2l_role("mouth_open-a") is None
    assert m.i2l_role("beauty mark") is None
    with pytest.raises(KeyError):
        m.i2l_role("tail")


def test_psd2live_names_follow_its_vocabulary():
    m = load()
    # 它要「张到最大」的嘴，闭嘴线另一层；「哦」口型由参数变形出来
    assert m.p2l_name("mouth_open-a") == "mouth"
    assert m.p2l_name("mouth") == "mouth close"
    assert m.p2l_name("mouth_open-o") is None
    assert m.p2l_name("eye_closed-r") == "eye close-r"
    # 侧发在它的词表里属于前发；序号放最后，左右用 -l / -r
    assert m.p2l_name("side hair-l-3") == "front hair side-l 3"
    assert m.p2l_name("bangs-8") == "bangs 8"
    assert m.p2l_name("cuff-l") == "handwear cuff-l"
    assert m.p2l_name("inner skirt") == "skirt inner"
    assert m.p2l_name("beauty mark") == "facedetail mole"


def test_blend_modes():
    m = load()
    base = np.zeros((1, 3, 4), dtype=np.float32)
    base[..., :3] = 0.5
    base[..., 3] = [1.0, 1.0, 0.0]
    eff = np.zeros_like(base)
    eff[..., :3] = 0.5
    eff[..., 3] = 1.0
    mul = m.blend_into(base, eff, "multiply")
    scr = m.blend_into(base, eff, "screen")
    assert mul[0, 0, 0] == pytest.approx(0.25)
    assert scr[0, 0, 0] == pytest.approx(0.75)
    # 透明度保持部件本身的（剪切蒙版：效果只画在部件上）
    assert list(mul[0, :, 3]) == [1.0, 1.0, 0.0]
    with pytest.raises(ValueError):
        m.blend_into(base, eff, "overlay")


def make_src(tmp_path):
    """两层部件 + 一层贴在脸上的正片叠底阴影 + 一个默认隐藏的表情层。"""
    src = tmp_path / "src"
    (src / "layers").mkdir(parents=True)
    Image.new("RGBA", (20, 20), (200, 160, 140, 255)).save(src / "layers/face.png")
    Image.new("RGBA", (10, 10), (128, 128, 128, 255)).save(src / "layers/face_shadow.png")
    Image.new("RGBA", (6, 4), (90, 40, 40, 255)).save(src / "layers/mouth.png")
    Image.new("RGBA", (6, 6), (120, 30, 30, 255)).save(src / "layers/mouth_open.png")
    spec = {
        "canvas": [40, 60],
        "layers": [
            {
                "order": 0,
                "name": "face",
                "file": "layers/face.png",
                "bbox": [10, 10, 30, 30],
                "blend_mode": "normal",
                "type": "part",
                "clip_to": None,
            },
            {
                "order": 1,
                "name": "face_shadow2",
                "file": "layers/face_shadow.png",
                "bbox": [5, 5, 15, 15],
                "blend_mode": "multiply",
                "type": "effect",
                "clip_to": "face",
            },
            {
                "order": 2,
                "name": "mouth",
                "file": "layers/mouth.png",
                "bbox": [17, 24, 23, 28],
                "blend_mode": "normal",
                "type": "part",
                "clip_to": None,
            },
            {
                "order": 3,
                "name": "mouth_open-a",
                "file": "layers/mouth_open.png",
                "bbox": [17, 23, 23, 29],
                "blend_mode": "normal",
                "type": "expression",
                "clip_to": None,
                "visible": False,
                "replaces": ["mouth"],
            },
        ],
    }
    (src / "layers.json").write_text(json.dumps(spec), encoding="utf-8")
    return src


def test_merge_parts_clips_effects(tmp_path):
    m = load()
    src = make_src(tmp_path)
    parts = m.merge_parts(src, tmp_path / "work")
    assert [p.name for p in parts] == ["face", "mouth", "mouth_open-a"]
    face = np.asarray(Image.open(tmp_path / "work" / parts[0].file))
    assert face.shape == (20, 20, 4)
    # 阴影覆盖脸的左上 5×5（画布 10..15），其余不变；超出脸的部分被剪掉
    assert tuple(face[0, 0, :3]) == (100, 80, 70)
    assert tuple(face[10, 10, :3]) == (200, 160, 140)
    assert parts[0].effects == ["face_shadow2"]
    assert parts[2].kind == "expression" and parts[2].visible is False
    assert parts[2].replaces == ["mouth"]


def test_unknown_clip_target_is_rejected(tmp_path):
    m = load()
    src = make_src(tmp_path)
    spec = json.loads((src / "layers.json").read_text(encoding="utf-8"))
    spec["layers"][1]["clip_to"] = "nobody"
    (src / "layers.json").write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError, match="clip_to"):
        m.load_spec(src)


def test_prepare_writes_both_inputs(tmp_path):
    m = load()
    src = make_src(tmp_path)
    work = tmp_path / "work"
    info = m.prepare(src, work, i2l_scale=0.5)
    assert info == {
        "parts": 3,
        "effects_merged": 1,
        "i2l_layers": str(work / "i2l_layers"),
        "psd": str(work / "psd2live.psd"),
    }
    # image2live2d：整画布（按比例缩放）、从下到上编号、张嘴表情层不给
    files = sorted(p.name for p in (work / "i2l_layers").iterdir())
    assert files == ["000_face_base.png", "001_mouth.png"]
    assert Image.open(work / "i2l_layers" / files[0]).size == (20, 30)
    parts = json.loads((work / "parts.json").read_text(encoding="utf-8"))
    assert parts["canvas"] == [40, 60] and len(parts["parts"]) == 3


def read_psd_layers(data: bytes):
    """读回 psd_bytes 写的 PSD：返回画布尺寸和每层 (名字, 上左下右, 是否可见)。"""
    assert data[:4] == b"8BPS"
    _, channels, h, w, depth, mode = struct.unpack(">H6xHIIHH", data[4:26])
    assert (channels, depth, mode) == (4, 8, 3)
    pos = 26
    for _ in range(2):  # 颜色模式数据、图像资源
        (n,) = struct.unpack(">I", data[pos : pos + 4])
        pos += 4 + n
    pos += 8  # 图层与蒙版信息长度、图层信息长度
    (count,) = struct.unpack(">h", data[pos : pos + 2])
    pos += 2
    layers = []
    for _ in range(abs(count)):
        top, left, bottom, right, nch = struct.unpack(">iiiiH", data[pos : pos + 18])
        pos += 18 + 6 * nch
        assert data[pos : pos + 8] == b"8BIMnorm"
        flags = data[pos + 10]
        (extra,) = struct.unpack(">I", data[pos + 12 : pos + 16])
        ex = data[pos + 16 : pos + 16 + extra]
        name_len = ex[8]
        layers.append((ex[9 : 9 + name_len].decode(), (top, left, bottom, right), not flags & 2))
        pos += 16 + extra
    return (w, h), layers


def test_psd_for_psd2live(tmp_path):
    m = load()
    src = make_src(tmp_path)
    work = tmp_path / "work"
    m.prepare(src, work)
    size, layers = read_psd_layers((work / "psd2live.psd").read_bytes())
    assert size == (40, 60)
    assert layers == [
        ("face", (10, 10, 30, 30), True),
        ("mouth close", (24, 17, 28, 23), True),
        ("mouth", (23, 17, 29, 23), True),
    ]


def test_psd2live_cli_args(tmp_path):
    m = load()
    args = m.psd2live_cli_args(tmp_path / "a.psd", tmp_path / "out")
    assert args[:2] == ["--input", str(tmp_path / "a.psd")]
    assert "--atlas" in args and args[args.index("--atlas") + 1] == "4096"


def test_runner_uses_public_bundle_path():
    """image2live2d 的 --live2d 默认只写 JSON；这里走它公开的单图集 .moc3 流程。"""
    m = load()
    for name in ("build_atlas", "rig_to_moc3", "write_moc3", "rig_to_cmo3", "rig_from_stack"):
        assert name in m._I2L_RUNNER
    compile(m._I2L_RUNNER, "runner", "exec")


def test_demo_timeline_gaze_leads_head():
    m = load()
    # 2.4 秒：视线已经看向左边，头才刚开始转
    f = m.demo_frame(2.4)
    assert f["ParamEyeBallX"] < -0.9 and abs(f["ParamAngleX"]) < 8
    # 4 秒：头转到左边约 30 度；7 秒：右边
    assert m.demo_frame(4.0)["ParamAngleX"] < -25
    assert m.demo_frame(7.0)["ParamAngleX"] > 25
    # 说话段嘴会张开，最后在笑
    assert max(m.demo_frame(9.5 + i / 30)["ParamMouthOpenY"] for i in range(100)) > 0.6
    assert m.demo_frame(17.5)["ParamEyeLSmile"] == pytest.approx(1.0)
    # 有眨眼
    assert min(m.demo_frame(1.3 + i / 100)["ParamEyeLOpen"] for i in range(25)) < 0.05


def test_demo_clip_clamps_and_skips_missing():
    m = load()
    clip = m.demo_clip({"ParamAngleX": (-20.0, 20.0), "ParamMouthOpenY": (0.0, 1.0)})
    assert clip["id"] == "Demo" and clip["duration"] == m.DEMO_SECONDS
    curves = {c["parameter"]: c["keys"] for c in clip["curves"]}
    assert set(curves) == {"ParamAngleX", "ParamMouthOpenY"}
    xs = [k["value"] for k in curves["ParamAngleX"]]
    assert min(xs) == -20.0 and max(xs) == 20.0
    times = [k["time"] for k in curves["ParamAngleX"]]
    assert times[0] == 0 and times[-1] == m.DEMO_SECONDS and times == sorted(times)
