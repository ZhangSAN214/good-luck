"""前端 md()：表格渲染，单元格里的 <br> 变成换行（用 node 跑 view.js，没有 node 时跳过）。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VIEW = Path(__file__).resolve().parents[2] / "web" / "js" / "view.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="需要 node")


def render(text: str) -> str:
    script = (
        "global.document={};global.window={};"
        "global.localStorage={getItem(){return null},setItem(){}};"
        f"const {{md}}=await import({json.dumps(VIEW.as_uri())});"
        f"process.stdout.write(md({json.dumps(text)}));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return out.stdout


def test_br_in_table_cells_becomes_a_line_break():
    html = render("| 名称 | 说明 |\n|---|---|\n| A | 第一行<br>第二行 |\n| B<BR/>2 | 无 |")
    assert "<table" in html and "&lt;br" not in html
    assert "<td>第一行<br>第二行</td>" in html and "<td>B<br>2</td>" in html


def test_br_outside_tables_becomes_a_newline_and_other_html_stays_escaped():
    html = render("第一行<br>第二行 <b>x</b> <script>")
    assert html == "第一行\n第二行 &lt;b&gt;x&lt;/b&gt; &lt;script&gt;"


def test_table_cells_are_escaped_and_bold_still_works():
    html = render("| a | b |\n|--|--|\n| **粗** | <img src=x onerror=alert(1)> |")
    assert "<b>粗</b>" in html and "<img" not in html and "&lt;img" in html


def test_plain_text_with_pipes_is_not_a_table():
    assert "<table" not in render("a | b | c\n| 只有一行 |")
