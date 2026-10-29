"""工作台质检的纯函数：颜色、对比度、判定（不开浏览器）。"""

from __future__ import annotations

import pytest

from roundtable.core.benchkit import quality as q


def probe(**kw):
    base = {
        "title": "t",
        "kit": True,
        "charset": "UTF-8",
        "scripts": [],
        "overflow": [],
        "scrollWidth": 1280,
        "width": 1280,
        "bodyText": "今天练了 3 组",
        "texts": [],
        "buttons": [],
        "images": [],
        "charts": [],
    }
    base.update(kw)
    return base


def text(t="字", size=14.0, color="rgb(17, 20, 24)", layers=("rgb(255, 255, 255)",), **kw):
    return {
        "text": t,
        "where": "div",
        "size": size,
        "weight": 400,
        "color": color,
        "bg": {"layers": list(layers)},
        "disabled": False,
        **kw,
    }


def run(p, phone=False, errors=()):
    return q.evaluate(p, "电脑·浅色", errors=list(errors), phone=phone, local_hosts=("127.0.0.1",))


def checks(findings):
    return {f.check: f.severity for f in findings}


def test_parse_color_formats():
    assert q.parse_color("rgb(255, 0, 0)") == (1.0, 0.0, 0.0, 1.0)
    assert q.parse_color("rgba(0, 0, 0, 0.5)")[3] == 0.5
    assert q.parse_color("color(srgb 1 0.5 0 / 0.14)") == (1.0, 0.5, 0.0, 0.14)
    assert q.parse_color("#fff") == (1.0, 1.0, 1.0, 1.0)
    assert q.parse_color("nonsense") is None


def test_contrast_ratio_extremes():
    white, black = (1, 1, 1, 1), (0, 0, 0, 1)
    assert q.contrast_ratio(black, white) == pytest.approx(21, rel=1e-3)
    assert q.contrast_ratio(white, white) == pytest.approx(1)


def test_translucent_background_is_blended_over_parent():
    # 14% 的橙色叠在深色卡片上，应该还是深色
    bg = q.effective_background(["color(srgb 0.94 0.69 0.29 / 0.14)", "rgb(20, 23, 28)"])
    assert q.luminance(bg) < 0.05


def test_needed_ratio_large_text():
    assert q.needed_ratio(14, 400) == 4.5
    assert q.needed_ratio(24, 400) == 3.0
    assert q.needed_ratio(19, 700) == 3.0


def test_clean_page_passes():
    assert (
        run(
            probe(
                texts=[text()],
                buttons=[{"where": "b", "name": "发送", "w": 40, "h": 40, "tag": "button"}],
            )
        )
        == []
    )


def test_blocking_problems_are_found():
    f = checks(
        run(
            probe(
                kit=False,
                charset="windows-1252",
                scrollWidth=1400,
                bodyText="体重 undefined kg，NaN 天",
                scripts=[
                    "https://evil.example.com/x.js",
                    "https://cdnjs.cloudflare.com/a.js",
                    "http://127.0.0.1:1/k.js",
                ],
                buttons=[{"where": "b", "name": "", "w": 40, "h": 40, "tag": "button"}],
                images=[{"where": "img", "alt": None, "ok": False}],
                charts=[{"where": ".k-chart", "drawn": False}],
            ),
            errors=["TypeError: x is undefined"],
        )
    )
    for name in (
        "页面报错",
        "横向溢出",
        "坏字",
        "没用设计套件",
        "没有声明编码",
        "外链脚本",
        "按钮没有名字",
        "图片没有说明",
        "图片打不开",
        "图表空白",
    ):
        assert f[name] == "block", name


def test_bad_text_needs_word_boundary():
    # 英文单词里包含 NaN 之类的字母不算
    assert "坏字" not in checks(run(probe(bodyText="Banana and nullable are fine")))


def test_low_contrast_and_small_text():
    f = run(probe(texts=[text(color="rgb(200, 200, 200)"), text(size=10)]))
    c = checks(f)
    assert c["对比度不够"] == "block"  # 浅灰字在白底上不到 3:1
    assert c["字太小"] == "warn"
    mid = checks(run(probe(texts=[text(color="rgb(130, 130, 130)")])))
    assert mid["对比度不够"] == "warn"  # 3～4.5 之间只提醒


def test_disabled_and_image_backgrounds_skip_contrast():
    t1 = text(color="rgb(230, 230, 230)", disabled=True)
    t2 = {**text(color="rgb(230, 230, 230)"), "bg": {"image": True}}
    assert "对比度不够" not in checks(run(probe(texts=[t1, t2])))


def test_tap_targets_only_on_phone():
    b = [{"where": "b", "name": "删", "w": 24, "h": 24, "tag": "button"}]
    assert "手机上按钮太小" not in checks(run(probe(buttons=b)))
    assert checks(run(probe(buttons=b), phone=True))["手机上按钮太小"] == "warn"


def test_merge_and_report():
    a = q.Finding("字太小", "warn", "电脑·浅色", "x")
    b = q.Finding("字太小", "warn", "手机·深色", "y")
    c = q.Finding("横向溢出", "block", "手机·深色", "z")
    merged = q.merge([a, b, c])
    assert [m.check for m in merged] == ["横向溢出", "字太小"]
    assert merged[1].where == "电脑·浅色、手机·深色"
    r = q.Report("p.html", findings=merged, combos=6)
    assert not r.passed and r.score == 100 - 15 - 3
    assert r.to_dict()["passed"] is False
    assert "不通过" in r.summary()


def test_static_handler_stays_inside_folders(tmp_path):
    h = type("H", (q._Handler,), {"root": str(tmp_path)})
    t = h.translate_path
    assert t(h, "/page.html") == str((tmp_path / "page.html").resolve())
    assert t(h, "/kit/kit.css") == str((q.KIT_DIR / "kit.css").resolve())
    assert "__forbidden__" in t(h, "/../../etc/passwd")
    assert "__forbidden__" in t(h, "/kit/../../src/roundtable/cli.py")


def test_missing_file_raises(tmp_path):
    pytest.importorskip("playwright.sync_api")
    with pytest.raises(q.QualityError):
        q.run_check(str(tmp_path / "nope.html"))
