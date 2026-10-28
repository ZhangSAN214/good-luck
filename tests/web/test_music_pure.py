"""音乐卡片的纯函数（web/js/music.js）：mp3 内嵌封面、外观设置清理、主色上的图标色。

用 node 跑，没有就跳过。
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web" / "js"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(40))

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="需要 node")


def run(body: str):
    script = (
        f"const M = await import({json.dumps((WEB / 'music.js').as_uri())});"
        f"const out = await (async () => {{ {body} }})();"
        "process.stdout.write(JSON.stringify(out));"
    )
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _syncsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def _id3(ver: int, enc: int = 0) -> bytes:
    desc = "封面".encode("utf-16-le") + b"\0\0" if enc == 1 else b"cover\0"
    body = bytes([enc]) + b"image/png\0" + b"\x03" + desc + PNG
    size = _syncsafe(len(body)) if ver == 4 else struct.pack(">I", len(body))
    title = b"TIT2" + (_syncsafe(4) if ver == 4 else struct.pack(">I", 4)) + b"\0\0" + b"\0abc"
    frames = title + b"APIC" + size + b"\0\0" + body
    return b"ID3" + bytes([ver, 0, 0]) + _syncsafe(len(frames) + 16) + frames + b"\0" * 16


@pytest.mark.parametrize(("ver", "enc"), [(3, 0), (3, 1), (4, 3), (4, 1)])
def test_id3_embedded_cover_is_found(ver, enc):
    data = list(_id3(ver, enc) + b"\xff\xfb" * 50)
    out = run(
        f"const p = M.id3Picture(new Uint8Array({data}).buffer);"
        "return p && { type: p.type, n: p.data.length, head: Array.from(p.data.slice(0, 4)) };"
    )
    assert out == {"type": "image/png", "n": len(PNG), "head": list(PNG[:4])}


def test_id3_without_cover_or_not_mp3():
    assert run("return M.id3Picture(new Uint8Array([1,2,3,4,5,6,7,8,9,10,11]).buffer)") is None
    no_pic = list(b"ID3\x03\x00\x00" + _syncsafe(10) + b"TIT2" + struct.pack(">I", 2) + b"\0\0hi")
    assert run(f"return M.id3Picture(new Uint8Array({no_pic}).buffer)") is None


def test_skin_settings_are_cleaned():
    out = run(
        "return [M.cleanSkin(null), M.cleanSkin({preset:'night', accent:'#C0392B', text:'red',"
        " alpha: 9, tag: 'x'.repeat(80)}), M.rgba('#102030', 0.5),"
        " M.onColor('#5e8e91'), M.onColor('#eef2f2')]"
    )
    default, night, rgba, dark_on, light_on = out
    assert default["preset"] == "mist" and default["tag"] == ""
    assert night["accent"] == "#c0392b"  # 自定义颜色保留
    assert night["text"] == "#eceff3"  # 不合法的颜色回到预设
    assert night["alpha"] == 1 and len(night["tag"]) == 40
    assert rgba == "rgba(16,32,48,0.5)"
    assert (dark_on, light_on) == ("#ffffff", "#14181f")
