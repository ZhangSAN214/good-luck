"""设计套件：样板页必须过质检；故意做坏的页面必须被拦下（真浏览器，没有时跳过）。"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from roundtable.core.benchkit.quality import KIT_DIR, QualityError, run_check  # noqa: E402

BAD = """<!doctype html><html><head><meta charset="utf-8"><title>坏页</title>
<link rel="stylesheet" href="/kit/kit.css"></head>
<body class="k-root"><div style="width:1600px">太宽了</div>
<p style="color:#ddd">看不清的字</p><button></button><div class="k-chart" style="height:80px"></div>
<script>document.body.append('体重 ' + undefined)</script></body></html>"""


def _check(target, **kw):
    try:
        return run_check(target, **kw)
    except QualityError as exc:
        if "浏览器启动失败" in str(exc):
            pytest.skip(str(exc))
        raise


def test_gallery_passes(tmp_path):
    r = _check(str(KIT_DIR / "gallery.html"), out_dir=tmp_path)
    assert r.passed, r.summary()
    assert r.score == 100, r.summary()
    assert r.combos == 6 and len(r.shots) == 6
    assert (tmp_path / "report.json").is_file()


def test_bad_page_is_blocked(tmp_path):
    page = tmp_path / "bad.html"
    page.write_text(BAD, encoding="utf-8")
    r = _check(str(page), viewports=(("手机", 390, 844),), modes=("light",))
    found = {f.check for f in r.blocking}
    assert {"横向溢出", "坏字", "按钮没有名字", "图表空白", "对比度不够"} <= found, r.summary()
