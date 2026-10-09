"""沙箱功能（需要已安装的 wasm 沙箱，否则跳过）：中文字体、文档生成。"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from roundtable.core.config import load_config
from roundtable.core.tools import WasmSandbox

SANDBOX = WasmSandbox(load_config().roundtable.tools.python)
pytestmark = pytest.mark.skipif(
    SANDBOX.unavailable_reason() is not None, reason=SANDBOX.unavailable_reason() or ""
)


def run(code: str):
    job = Path(tempfile.mkdtemp(prefix="roundtable-test-"))
    try:
        (job / "in").mkdir()
        (job / "out").mkdir()
        (job / "main.py").write_text(code, encoding="utf-8")
        result = asyncio.run(SANDBOX.run(job))
        return result, {p.name: p.read_bytes() for p in (job / "out").iterdir()}
    finally:
        shutil.rmtree(job, ignore_errors=True)


def test_chinese_text_in_charts_and_pillow():
    result, out = run(
        "import warnings, matplotlib.pyplot as plt\n"
        "with warnings.catch_warnings(record=True) as w:\n"
        "    warnings.simplefilter('always')\n"
        "    plt.title('函数图像：最大值与最小值')\n"
        "    plt.plot([1, 2, 3])\n"
        "    plt.savefig('out/zh.png')\n"
        "print('missing', sum('missing' in str(x.message) for x in w))\n"
        "from PIL import Image, ImageDraw, ImageFont\n"
        "f = ImageFont.truetype('/usr/share/fonts/roundtable/cjk.otf', 24)\n"
        "im = Image.new('RGB', (120, 40), 'white')\n"
        "ImageDraw.Draw(im).text((4, 4), '中文', font=f, fill='black')\n"
        "im.save('out/pil.png')\n"
    )
    assert result.ok, result.stderr
    assert "missing 0" in result.stdout  # 没有缺字形的警告
    assert out["zh.png"].startswith(b"\x89PNG") and out["pil.png"].startswith(b"\x89PNG")


def test_office_documents():
    result, out = run(
        "import docx, openpyxl\n"
        "d = docx.Document(); d.add_paragraph('报告'); d.save('out/r.docx')\n"
        "wb = openpyxl.Workbook(); wb.active['A1'] = '表'; wb.save('out/t.xlsx')\n"
    )
    assert result.ok, result.stderr
    assert set(out) == {"r.docx", "t.xlsx"}
