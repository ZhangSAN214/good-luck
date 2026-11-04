"""设计套件：样板页必须过质检；故意做坏的页面必须被拦下（真浏览器，没有时跳过）。"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("playwright.sync_api")

from roundtable.core.benchkit.quality import KIT_DIR, QualityError, run_check  # noqa: E402

BAD = """<!doctype html><html><head><meta charset="utf-8"><title>坏页</title>
<link rel="stylesheet" href="/kit/kit.css"></head>
<body class="k-root"><div style="width:1600px">太宽了</div>
<p style="color:#ddd">看不清的字</p><button></button><div class="k-chart" style="height:80px"></div>
<script>document.body.append('体重 ' + undefined)</script></body></html>"""


def _own_thread(fn, *args, **kw):
    """在单独的线程里跑质检。

    同一个进程里别的端到端测试已经开着 Playwright（共用的 browser 夹具）时，在这个线程里再开
    会报「Sync API inside the asyncio loop」，以前就被当成没有浏览器整个跳过了（看着是绿的，
    其实没测）。换个线程就没有这个冲突。CI 上（ROUNDTABLE_E2E_REQUIRED）浏览器起不来算失败。
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            return pool.submit(fn, *args, **kw).result()
        except QualityError as exc:
            if "浏览器启动失败" in str(exc) and not os.environ.get("ROUNDTABLE_E2E_REQUIRED"):
                pytest.skip(str(exc))
            raise


def _check(target, **kw):
    return _own_thread(run_check, target, **kw)


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


def test_generated_shell_with_kit_windows_passes(tmp_path):
    """程序生成的外壳 + 用零件库写的窗口：本身就应该过质检（生成器的起点不能是坏的）。"""
    from roundtable.core.benchkit.spec import normalize, write_shell

    spec = normalize(
        {
            "title": "旅行台",
            "subtitle": "三天东京行程",
            "icon": "plane",
            "theme": "ocean",
            "windows": [
                {"id": "plan", "title": "行程", "span": 5, "parts": ["timeline"], "icon": "route"},
                {"id": "cost", "title": "预算", "span": 7, "parts": ["donut"], "icon": "wallet"},
            ],
        },
        max_windows=8,
    )
    index = write_shell(tmp_path, spec, "b1")
    (tmp_path / "windows" / "plan.js").write_text(
        "export default (el, ctx) => ctx.kit.timeline(el, { items: ctx.data.items });",
        encoding="utf-8",
    )
    (tmp_path / "data" / "plan.json").write_text(
        '{"items": [{"time": "09:00", "title": "浅草寺", "meta": "步行 1.5 小时"}]}',
        encoding="utf-8",
    )
    (tmp_path / "windows" / "cost.js").write_text(
        "export default (el, ctx) => ctx.kit.donut(el, { items: ["
        "{ label: '住宿', value: 3200 }, { label: '交通', value: 900 }] });",
        encoding="utf-8",
    )
    r = _check(
        str(index), viewports=(("电脑", 1280, 860), ("手机", 390, 844)), modes=("light", "dark")
    )
    assert r.passed, r.summary()


def test_broken_window_is_caught(tmp_path):
    from roundtable.core.benchkit.spec import normalize, write_shell

    spec = normalize({"title": "坏台", "windows": [{"id": "x", "title": "坏窗口"}]}, max_windows=8)
    index = write_shell(tmp_path, spec, "b2")
    (tmp_path / "windows" / "x.js").write_text(
        "export default () => { throw new Error('boom'); };", encoding="utf-8"
    )
    r = _check(str(index), viewports=(("电脑", 1280, 860),), modes=("light",))
    assert "页面报错" in {f.check for f in r.blocking}, r.summary()


def test_icon_buttons_named_and_long_windows_caught(tmp_path):
    """第一次真跑的教训：只有图标、图标带了说明的按钮不能误报；手机上太长的窗口要抓到，并记在哪个窗口。"""
    from roundtable.core.benchkit.spec import normalize, write_shell

    spec = normalize(
        {
            "title": "清单台",
            "windows": [{"id": "tools", "title": "工具"}, {"id": "list", "title": "清单"}],
        },
        max_windows=8,
    )
    index = write_shell(tmp_path, spec, "b3")
    (tmp_path / "windows" / "tools.js").write_text(
        "export default (el, ctx) => { el.innerHTML = `<details open><summary>更多</summary>"
        "<button class=\"k-btn\">${ctx.kit.icon('download', { label: '导出' })}</button>"
        "</details>`; };",
        encoding="utf-8",
    )
    (tmp_path / "windows" / "list.js").write_text(
        "export default (el, ctx) => { el.innerHTML = Array.from({ length: 120 }, (_, i) =>"
        " `<p class=\"k-sub\">${ctx.kit.esc('第 ' + (i + 1) + ' 项')}</p>`).join(''); };",
        encoding="utf-8",
    )
    r = _check(str(index), viewports=(("手机", 390, 844),), modes=("light",))
    found = {f.check: f for f in r.findings}
    assert "按钮没有名字" not in found, r.summary()
    assert found["窗口太长"].windows == ["list"], r.summary()


def test_windows_share_user_data_and_small_rings_stay_readable(tmp_path):
    """ctx.state：一个窗口加进去，另一个窗口马上看得到、改了也收到；小圆环的字不小于 12px。"""
    from roundtable.core.benchkit.spec import normalize, write_shell

    spec = normalize(
        {
            "title": "旅行台",
            "windows": [
                {"id": "eat", "title": "吃", "span": 6, "icon": "utensils"},
                {"id": "plan", "title": "行程", "span": 6, "icon": "route"},
            ],
            "state": {"itinerary": "行程条目：id、标题"},
        },
        max_windows=8,
    )
    index = write_shell(tmp_path, spec, "b3")
    (tmp_path / "windows" / "eat.js").write_text(
        "export default (el, ctx) => { ctx.state.set('itinerary', [{ id: 'a', title: '浅草寺' }]);"
        " ctx.kit.ring(el, 40, { size: 64, label: '已排进行程' }); };",
        encoding="utf-8",
    )
    (tmp_path / "windows" / "plan.js").write_text(
        "export default (el, ctx) => { let seen = [];"
        " ctx.state.on('itinerary', (v) => { seen.push(v.length);"
        " el.innerHTML = '<p>已加入 ' + v.length + ' 项</p>'; }, []);"
        " ctx.state.update('itinerary', (l) => [...l, { id: 'b', title: '上野' }], []);"
        " if (seen.join() !== '1,2') throw new Error('没收到：' + seen.join()); };",
        encoding="utf-8",
    )
    r = _check(str(index), viewports=(("手机", 390, 844),), modes=("light",))
    found = {f.check for f in r.findings}
    assert "页面报错" not in found and "字太小" not in found, r.summary()


def test_poke_clicks_every_window_and_finds_dead_lonely_and_broken_buttons(tmp_path):
    """自动点一遍：加入后行程跟着变（不报）；点了没反应、只有自己变、点了报错都指出是哪个窗口。
    名字像「删除」「导出」的按钮不点。"""
    from roundtable.core.benchkit.spec import normalize, write_shell

    spec = normalize(
        {
            "title": "旅行台",
            "windows": [
                {"id": "spots", "title": "景点", "span": 4},
                {"id": "plan", "title": "行程", "span": 4},
                {"id": "fav", "title": "收藏", "span": 4},
            ],
            "state": {"itinerary": "行程条目"},
        },
        max_windows=8,
    )
    index = write_shell(tmp_path, spec, "b9")
    spots = [
        "export default (el, ctx) => { el.innerHTML = '",
        '<button class="k-btn" data-add>加入行程</button>',
        '<button class="k-btn" data-dead>看详情</button>',
        '<button class="k-btn" data-del>删除</button>\';',
        " el.querySelector('[data-add]').onclick =",
        " () => ctx.state.update('itinerary', (l) => [...l, 1], []);",
        " el.querySelector('[data-del]').onclick = () => { throw new Error('不该点到'); }; };",
    ]
    (tmp_path / "windows" / "spots.js").write_text("".join(spots), encoding="utf-8")
    (tmp_path / "windows" / "plan.js").write_text(
        "export default (el, ctx) => { ctx.state.on('itinerary', (v) => {"
        " el.innerHTML = '<p>已加入 ' + v.length + ' 项</p>'; }, []); };",
        encoding="utf-8",
    )
    fav = [
        'export default (el, ctx) => { let n = 0; el.innerHTML = \'<p class="k-sub">0</p>',
        '<button class="k-btn" data-fav>收藏这家</button>',
        '<button class="k-btn" data-boom>刷新</button>\';',
        " el.querySelector('[data-fav]').onclick =",
        " () => { el.querySelector('p').textContent = ++n; };",
        " el.querySelector('[data-boom]').onclick =",
        " () => { throw new Error('窗口 fav 出错：刷新坏了'); }; };",
    ]
    (tmp_path / "windows" / "fav.js").write_text("".join(fav), encoding="utf-8")
    r = _check(str(index), viewports=(("电脑", 1280, 860),), modes=("light",))
    found = {f.check: f for f in r.findings}
    assert found["点了没反应"].windows == ["fav", "spots"], r.summary()
    assert "看详情" in found["点了没反应"].detail and "删除" not in found["点了没反应"].detail
    assert found["加入后别处不变"].windows == ["fav"], r.summary()
    assert "加入行程" not in found["加入后别处不变"].detail
    assert found["点了报错"].severity == "block" and "不该点到" not in found["点了报错"].detail
    # 只在第一种组合里点
    two = _check(str(index), viewports=(("电脑", 1280, 860), ("手机", 390, 844)), modes=("light",))
    assert "、" not in {f.check: f for f in two.findings}["点了没反应"].where


def test_run_probes_fills_clicks_and_reads_the_page(tmp_path):
    """程序真的去填「京都」、点「生成」：做到的判过；写死的、找不到按钮的判不过；
    要请助手现场生成的判「查不了」。"""
    from roundtable.core.benchkit.quality import run_probes
    from roundtable.core.benchkit.spec import normalize, write_shell

    spec = normalize(
        {
            "title": "旅行台",
            "windows": [
                {"id": "form", "title": "条件", "span": 6},
                {"id": "plan", "title": "行程", "span": 6},
            ],
        },
        max_windows=8,
    )
    index = write_shell(tmp_path, spec, "b10")
    form = [
        "export default (el, ctx) => { el.innerHTML = '<label>目的地 <input class=k-input",
        " data-to></label><button class=k-btn data-go>生成行程</button>",
        "<button class=k-btn data-ai>智能推荐</button>';",
        " const to = el.querySelector('[data-to]');",
        " el.querySelector('[data-go]').onclick = () => ctx.state.set('to', to.value);",
        " el.querySelector('[data-ai]').onclick = () => ctx.askJson('推荐 ' + to.value); };",
    ]
    (tmp_path / "windows" / "form.js").write_text("".join(form), encoding="utf-8")
    plan = [
        "export default (el, ctx) => { el.innerHTML = '<p>东京三日游</p>';",
        " ctx.state.on('to', (v) => {",
        " if (v) el.innerHTML = '<p>' + ctx.kit.esc(v) + '三日游</p>'; }); };",
    ]
    (tmp_path / "windows" / "plan.js").write_text("".join(plan), encoding="utf-8")
    fill = {"do": "fill", "target": "目的地", "value": "京都"}

    def click(t):
        return {"do": "click", "target": t}

    probes = [
        {"id": "r1", "steps": [fill, click("生成行程")], "expect": ["京都三日游"]},
        {"id": "r2", "steps": [click("生成行程")], "expect": [], "changes": True},
        {"id": "r3", "steps": [click("导出 PDF")], "expect": ["已导出"]},
        {"id": "r4", "steps": [fill, click("智能推荐")], "expect": ["推荐结果"]},
    ]
    got = {x["id"]: x for x in _own_thread(run_probes, str(index), probes)}
    assert got["r1"]["ok"] is True, got
    assert got["r2"]["ok"] is False and "一点没变" in got["r2"]["why"]  # 空输入点生成：没变
    assert got["r3"]["ok"] is False and "找不到「导出 PDF」" in got["r3"]["why"]
    assert got["r4"]["ok"] is None and "现场生成" in got["r4"]["why"]


def _layout_boxes(folder, sizes):
    """在真浏览器里量外壳每个窗口的位置：{(屏宽): {窗口 id: (x, y, 宽)}, 页面宽, 网格宽}。"""
    from playwright.sync_api import sync_playwright

    from roundtable.core.benchkit.quality import _serve

    out = {}
    with _serve(folder) as base, sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("ROUNDTABLE_CHROMIUM") or None)
        try:
            for w, h in sizes:
                page = b.new_page(viewport={"width": w, "height": h})
                page.goto(f"{base}/index.html")
                page.wait_for_selector('[data-window="hero"] > *', timeout=10000)
                out[w] = page.evaluate(
                    """() => {
                      const g = document.querySelector('main.k-grid').getBoundingClientRect();
                      const boxes = {};
                      for (const el of document.querySelectorAll('[data-window]')) {
                        const r = el.closest('section').getBoundingClientRect();
                        boxes[el.dataset.window] = [r.x, r.y, r.width];
                      }
                      const page = document.documentElement.scrollWidth;
                      return { boxes, grid: g.width, page, vw: innerWidth };
                    }"""
                )
                page.close()
        finally:
            b.close()
    return out


@pytest.mark.parametrize(
    "layout", ["hero-side", "hero-strip", "quad", "flow", "workspace", "stream"]
)
def test_layout_templates_hold_on_desk_tablet_and_phone(tmp_path, layout):
    """D 布局模板：6 种版型在电脑 1280 / 平板 820 / 手机 390 都过质检（不横向溢出），
    电脑上照模板摆，平板主视觉一整行，手机一列、主视觉在最上面。"""
    from roundtable.core.benchkit.spec import layout_grid, normalize, write_shell

    ids = ["hero", "a", "b", "c", "d"]
    spec = normalize(
        {
            "title": "版型台",
            "hero": "hero",
            "design": {"picks": {"layout": layout}},
            "windows": [{"id": i, "title": f"窗口{i}", "icon": "map"} for i in ids],
        },
        max_windows=8,
    )
    index = write_shell(tmp_path, spec, "lay")
    for i in ids:
        (tmp_path / "windows" / f"{i}.js").write_text(
            "export default (el, ctx) => ctx.kit.timeline(el, { items: ["
            "{ time: '09:00', title: '第一项', meta: '说明' },"
            " { time: '10:00', title: '第二项' }] });",
            encoding="utf-8",
        )
    r = _check(
        str(index),
        viewports=(("电脑", 1280, 860), ("平板", 820, 1100), ("手机", 390, 844)),
        modes=("light",),
    )
    assert r.passed, r.summary()
    m = _own_thread(_layout_boxes, tmp_path, [(1280, 860), (820, 1100), (390, 844)])
    grid = layout_grid(ids, "hero", layout)
    for w, got in m.items():
        assert got["page"] <= got["vw"], (layout, w)  # 不横向溢出
    desk, tab, phone = m[1280], m[820], m[390]
    # 电脑：主视觉的宽度和模板一致（12 列里占几列，容许间距误差）
    want = grid["spans"]["hero"] / 12 * desk["grid"]
    assert abs(desk["boxes"]["hero"][2] - want) < 40, (layout, desk["boxes"]["hero"], want)
    if layout in ("workspace", "flow"):  # 主视觉在中间：左边有一格
        assert desk["boxes"]["a"][0] < desk["boxes"]["hero"][0]
    # 平板：主视觉占一整行、排最上面
    assert tab["boxes"]["hero"][2] > tab["grid"] - 4
    assert tab["boxes"]["hero"][1] == min(v[1] for v in tab["boxes"].values())
    # 手机：一列，主视觉在最上面
    ys = {k: v[1] for k, v in phone["boxes"].items()}
    assert ys["hero"] == min(ys.values())
    assert all(v[2] > phone["grid"] - 4 for v in phone["boxes"].values())
