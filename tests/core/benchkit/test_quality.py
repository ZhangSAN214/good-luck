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


def test_findings_know_their_windows_and_merge_them():
    p = probe(
        buttons=[
            {
                "where": "[窗口 plan] button",
                "win": "plan",
                "name": "",
                "w": 40,
                "h": 40,
                "tag": "button",
                "type": "",
            },
            {
                "where": "[窗口 cost] button",
                "win": "cost",
                "name": "",
                "w": 40,
                "h": 40,
                "tag": "button",
                "type": "",
            },
            {
                "where": "button.k-btn",
                "win": "",
                "name": "",
                "w": 40,
                "h": 40,
                "tag": "button",
                "type": "",
            },
        ]
    )
    f = next(x for x in run(p) if x.check == "按钮没有名字")
    assert f.windows == ["cost", "plan"] and "[窗口 plan]" in f.detail
    other = q.Finding("按钮没有名字", "block", "手机·深色", "x", ["menu"])
    merged = q.merge([f, other])
    assert merged[0].windows == ["cost", "menu", "plan"]


def test_long_windows_on_phone():
    p = probe(height=844, windows=[{"id": "list", "h": 844 * 5}, {"id": "ok", "h": 600}])
    f = next(x for x in run(p, phone=True) if x.check == "窗口太长")
    assert f.severity == "block" and f.windows == ["list"]
    p = probe(height=844, windows=[{"id": "list", "h": 844 * 2.5}])
    assert checks(run(p, phone=True))["窗口太长"] == "warn"
    assert "窗口太长" not in checks(run(p, phone=False))  # 只看手机


def test_page_errors_name_their_window():
    f = next(x for x in run(probe(), errors=["窗口 trip-setup 出错：SyntaxError", "别的错"]))
    assert f.check == "页面报错" and f.windows == ["trip-setup"]


def test_evaluate_pokes_without_browser():
    pokes = [
        {"win": "spots", "name": "加入行程", "changed": ["spots", "plan"], "page": False},
        {"win": "spots", "name": "看详情", "changed": [], "page": False},
        {"win": "spots", "name": "打开说明", "changed": [], "page": True},  # 弹了对话框：算有反应
        {"win": "fav", "name": "收藏这家", "changed": ["fav"], "page": False},
        {"win": "fav", "name": "下一页", "changed": ["fav"], "page": False},  # 不是「加入」类
    ]
    found = {f.check: f for f in q.evaluate_pokes(pokes, "电脑·浅色", errors=[])}
    assert set(found) == {"点了没反应", "加入后别处不变"}
    assert found["点了没反应"].windows == ["spots"] and "看详情" in found["点了没反应"].detail
    assert found["加入后别处不变"].windows == ["fav"]
    # 只有一个窗口的页面：没有「别处」可言
    solo = [{"win": "a", "name": "加入", "changed": ["a"], "page": False}]
    assert q.evaluate_pokes(solo, "x", errors=[]) == []
    err = q.evaluate_pokes([], "x", errors=["窗口 fav 出错：坏了"])
    assert err[0].check == "点了报错" and err[0].windows == ["fav"]


def test_load_failure_text_blocks_and_ai_taste_warns():
    f = checks(run(probe(bodyText="示例照片加载失败 🎨 修图 ✨ 一键 🚀 赋能创作 一站式")))
    assert f["素材打不开"] == "block"
    assert f["表情符号当图标"] == "warn"
    assert f["空话"] == "warn"


def test_plain_copy_has_no_ai_taste_findings():
    f = checks(run(probe(bodyText="导入照片 · 裁剪 · 调色 ✓ 已保存 ★★★★☆ 开启通知")))
    assert not {"素材打不开", "表情符号当图标", "空话"} & set(f)
