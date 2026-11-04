"""编辑器底座（K2）+ 样例修图台（应用型产出 K1）+ 企业级验收（K5），真浏览器。

样例修图台在 tests/web/photo_bench/：规格（编辑器类）+ 三个功能（亮度对比度、裁剪、去背景），
由程序生成外壳，功能文件照搬。没有 playwright 或浏览器时跳过；不联网、不用真 key、不跑生成器。
"""

from __future__ import annotations

import io
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from PIL import Image  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

import roundtable.core.service as service_mod  # noqa: E402
from roundtable.core.benchkit.appcheck import run_app_check  # noqa: E402
from roundtable.core.benchkit.builder import BenchBuilder  # noqa: E402
from roundtable.core.benchkit.quality import QualityError, run_check  # noqa: E402
from roundtable.core.benchkit.spec import normalize, write_shell  # noqa: E402

from ..core.benchkit.test_builder import FakeSC  # noqa: E402
from ..core.benchkit.test_builder import render as fake_render  # noqa: E402

HERE = Path(__file__).with_name("photo_bench")
CHECKS = {"undoMin": 20, "bigPx": 4000, "bigMaxMs": 8000}
RUN_LIMIT_S = 240  # 一次质检 / 企业级验收最多等这么久


def build_photo_bench(folder: Path, extra: dict[str, str] | None = None) -> Path:
    """按样例规格生成应用外壳，再放进功能文件；extra = {功能 id: 代码} 额外加功能。"""
    raw = json.loads((HERE / "plan.json").read_text("utf-8"))
    for wid in extra or {}:
        raw["windows"].append({"id": wid, "title": f"功能 {wid}", "icon": "sparkles"})
    spec = normalize(raw, max_windows=8, kinds={"editor"}, apps={"editor": "editor"})
    assert spec.output == "app"
    index = write_shell(folder, spec, folder.name, app_checks=CHECKS)
    shutil.copytree(HERE / "windows", folder / "windows", dirs_exist_ok=True)
    for wid, code in (extra or {}).items():
        (folder / "windows" / f"{wid}.js").write_text(code, encoding="utf-8")
    return index


def _run(fn, *a, **kw):
    """在自己的线程里跑（同一进程里的端到端测试已经开着 asyncio 循环，同步 Playwright 会报错，
    以前会被当成没有浏览器而跳过）。"""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        # 浏览器卡住时别把整个 CI 拖到超时：限时等结果，超时直接算失败
        return pool.submit(fn, *a, **kw).result(timeout=RUN_LIMIT_S)
    except QualityError as exc:
        if "浏览器启动失败" in str(exc) and not os.environ.get("ROUNDTABLE_E2E_REQUIRED"):
            pytest.skip(str(exc))
        raise
    except FutureTimeout:
        pytest.fail(f"{fn.__name__} 超过 {RUN_LIMIT_S} 秒没跑完（浏览器可能卡住了）")
    finally:
        pool.shutdown(wait=False)


def png(w: int, h: int) -> bytes:
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (x * 255 // w, y * 255 // h, 160)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_sample_photo_bench_passes_enterprise_checks(tmp_path):
    index = build_photo_bench(tmp_path / "photo")
    r = _run(run_app_check, str(index), tmp_path / "out")
    assert r.passed, r.summary()
    ids = {x["id"] for x in r.results}
    assert {"k5-undo", "k5-big", "k5-export", "k5-error", "k5-keys"} <= ids
    features = {x["feature"] for x in r.results if x["id"].startswith("test:")}
    assert features == {"adjust", "crop", "background"}  # 每个功能自带的测试都跑了
    big = next(x for x in r.results if x["id"] == "k5-big")
    assert "4000×3000" in big["detail"]
    export = next(x for x in r.results if x["id"] == "k5-export")
    assert "4000×3000" in export["detail"]
    assert {Path(s).name for s in r.shots} == {"应用_电脑.png", "应用_手机.png", "应用_自检.png"}
    report = json.loads((tmp_path / "out" / "app_report.json").read_text("utf-8"))
    assert report["passed"] and report["ok"] == report["total"] == len(r.results)


def test_sample_photo_bench_passes_kit_check(tmp_path):
    """应用型照样过质检（三种屏宽 × 浅 / 深色）：不报错、不溢出、按钮有名字、对比度够。"""
    index = build_photo_bench(tmp_path / "photo")
    r = _run(run_check, str(index), tmp_path / "out")
    assert r.passed, r.summary()
    assert r.score >= 94, r.summary()


BROKEN = """export default (el, ctx) => {
  el.innerHTML = '<p class="k-muted">会把撤销弄坏的功能</p>';
  ctx.editor.undo = () => false;   // 坏：替换了底座的撤销
};
export async function test(t) { t.assert(false, '故意没过'); }
"""


def test_broken_features_are_caught_and_sent_to_their_authors(tmp_path):
    index = build_photo_bench(tmp_path / "photo", {"broken": BROKEN})
    r = _run(run_app_check, str(index), tmp_path / "out")
    assert not r.passed
    by = {f.check: f for f in r.findings}
    assert "撤销步数不够" in by and "broken" in by["撤销步数不够"].windows
    assert by["应用自带测试没过"].windows == ["broken"]
    assert "故意没过" in by["应用自带测试没过"].detail


def test_selftest_page_shows_results(tmp_path, page):
    """用户自己验收：打开 index.html?selftest=1，右下角列出每一项。"""
    from roundtable.core.benchkit.quality import _serve

    build_photo_bench(tmp_path / "photo")
    with _serve(tmp_path / "photo") as base:
        page.goto(f"{base}/index.html?selftest=1")
        box = page.locator(".k-ie-report")
        box.wait_for(timeout=60000)
        assert "自检全部通过" in box.locator("h2").text_content()
        assert box.locator("li").count() == 8


def test_photo_bench_end_to_end_in_roundtable(serve, page, monkeypatch, tmp_path):
    """在圆桌里打开样例修图台（/bench/<编号>/，CSP 沙箱）：打开图片 → 加图层 → 画一笔 →
    撤销 / 重做 → 隐藏图层 → 调亮度 → 导出（尺寸不变）→ 裁剪后导出 → 打开坏文件有提示。"""
    root = tmp_path / "bench"
    folder = root / "b-photo"
    build_photo_bench(folder)
    (folder / "build.json").write_text(
        json.dumps({"id": "b-photo", "request": "修照片", "status": "done", "title": "修图台"}),
        encoding="utf-8",
    )

    def factory(rules, *, root: Path, **kw):  # noqa: ARG001
        return BenchBuilder(
            rules, root=tmp_path / "bench", supercomputer=FakeSC(), render=fake_render
        )

    monkeypatch.setattr(service_mod, "BenchBuilder", factory)
    srv = serve(confirm_threshold_usd=100.0)
    resp = page.goto(f"{srv.url}/bench/b-photo/index.html")
    assert "sandbox" in resp.headers["content-security-policy"]
    page.locator("html[data-app='ready']").wait_for(state="attached")  # 沙箱禁 eval，用定位等
    status = page.locator(".k-ie-status")
    assert status.text_content() == "还没有图片"
    assert page.locator(".k-ie-drop").is_visible()  # 没图时的样子

    page.locator('input[type="file"]').set_input_files(
        {"name": "猫.png", "mimeType": "image/png", "buffer": png(640, 480)}
    )
    expect(status).to_contain_text("640 × 480")
    layers = page.locator(".k-ie-layer")
    assert layers.count() == 1 and "猫" in layers.first.text_content()

    page.locator('[data-cmd="add-layer"]').click()
    assert layers.count() == 2
    page.locator('[data-tool="brush"]').click()
    box = page.locator(".k-ie-view").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx - 40, cy)
    page.mouse.down()
    page.mouse.move(cx + 40, cy + 20, steps=5)
    page.mouse.up()
    assert "可撤销 3 步" in status.text_content()

    page.locator('[data-cmd="undo"]').click()  # 撤销画笔
    assert "可撤销 2 步" in status.text_content() and layers.count() == 2
    page.keyboard.press("Control+z")  # 撤销新建图层
    assert layers.count() == 1
    page.keyboard.press("Control+Shift+z")
    assert layers.count() == 2

    top = layers.first  # 列表最上面是最上层
    top.locator('[data-act="vis"]').click()
    assert top.locator('[data-act="vis"]').get_attribute("aria-pressed") == "false"
    page.locator(".k-ie-layer", has_text="猫").locator('[data-act="pick"]').click()

    adjust = page.locator('[data-window="adjust"]')
    adjust.locator('[data-k="brightness"]').fill("150")
    assert adjust.locator('[data-out="brightness"]').text_content() == "150%"
    adjust.locator('[data-do="apply"]').click()
    assert "可撤销 4 步" in status.text_content()

    with page.expect_download() as d:
        page.locator('[data-cmd="export"]').click()
    out = Image.open(d.value.path())
    assert out.size == (640, 480)
    assert out.getpixel((320, 240))[2] > 160  # 亮度调过了

    page.locator('[data-tool="select"]').click()
    page.mouse.move(cx - 60, cy - 40)
    page.mouse.down()
    page.mouse.move(cx + 60, cy + 40, steps=4)
    page.mouse.up()
    crop = page.locator('[data-window="crop"]')
    assert "选区" in crop.locator("[data-info]").text_content()
    crop.locator('[data-do="crop"]').click()
    size = status.text_content().split(" 像素")[0]
    w, h = (int(x) for x in size.split(" × "))
    assert w < 640 and h < 480
    with page.expect_download() as d:
        page.keyboard.press("Control+s")
    assert Image.open(d.value.path()).size == (w, h)  # 裁完导出的就是裁完的尺寸

    page.locator('input[type="file"]').set_input_files(
        {"name": "坏.png", "mimeType": "image/png", "buffer": b"not a png"}
    )
    alert = page.locator(".k-ie-alert")
    alert.wait_for()
    assert "打开失败" in alert.text_content()
    assert layers.count() == 2  # 坏文件没多出图层


class PhotoSC(FakeSC):
    """假的超级电脑：拆需求给样例修图台的规格（编辑器类），写功能时照搬样例的功能文件。"""

    def __init__(self):
        plan = json.loads((HERE / "plan.json").read_text("utf-8"))
        plan["requirements"] = []
        super().__init__(plan=plan)

    async def step(self, kind, prompt, *, folder, **kw):
        chosen, result = await super().step(kind, prompt, folder=folder, **kw)
        p = json.loads(prompt)
        if p["role"] in ("bench_window", "bench_rework"):
            shutil.copyfile(HERE / p["file"], folder / p["file"])
        return chosen, result


def test_control_room_builds_and_opens_the_photo_bench(serve, page, monkeypatch, tmp_path):
    """控制室：「修照片」→ 按应用型做 → 每轮显示企业级验收 → 中间的成品是编辑器（不是卡片拼盘）。"""
    from roundtable.core.benchkit.appcheck import AppReport
    from roundtable.core.benchkit.quality import Report
    from roundtable.core.config.schema import BenchProject, BenchTeam

    from .test_e2e_benchgen import PNG

    def check(target, out_dir=None, **kw):
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "电脑_light.png").write_bytes(PNG)
        return Report(target=target, shots=[str(out / "电脑_light.png")], combos=1)

    def app_check(target, out_dir=None, **kw):
        (Path(out_dir) / "应用_电脑.png").write_bytes(PNG)
        results = [
            {"id": "k5-undo", "name": "撤销至少 20 步", "ok": True, "detail": "撤销 20 步对得上"},
            {"id": "test:裁剪", "name": "裁剪", "ok": True, "detail": "", "feature": "crop"},
        ]
        return AppReport(
            target=target, results=results, shots=[str(Path(out_dir) / "应用_电脑.png")]
        )

    def factory(rules, *, root, **kw):  # noqa: ARG001
        return BenchBuilder(
            rules.model_copy(
                update={
                    "baseline": [],
                    "project": BenchProject(enabled=False),
                    "team": BenchTeam(enabled=False),
                }
            ),
            root=tmp_path / "bench",
            supercomputer=PhotoSC(),
            render=fake_render,
            check=check,
            app_check=app_check,
            probe=None,
            seed=3,
        )

    monkeypatch.setattr(service_mod, "BenchBuilder", factory)
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.locator('#entries [data-m="bench"]').click()
    pane = page.locator('#bench [data-pane="gen"]')
    pane.locator("[data-text]").fill("修照片")
    pane.locator("[data-submit]").click()
    pane.locator(".bg-status.bg-done").wait_for(timeout=20000)
    assert "企业级验收 2/2" in pane.locator(".bg-plan").text_content()
    pane.locator('[data-drawer="rounds"]').click()
    rnd = pane.locator(".bg-round").first
    assert "企业级验收 2/2" in rnd.locator("summary").text_content()
    assert rnd.locator(".bg-app li.bg-met").count() == 2
    assert "应用 · 电脑" in rnd.locator(".bg-shots").text_content()
    page.keyboard.press("Escape")
    frame = page.frame_locator("iframe.bg-frame")
    frame.locator("html[data-app='ready']").wait_for(
        state="attached", timeout=15000
    )  # 功能都装好了
    assert frame.locator(".k-ie-tools [data-tool='brush']").count() == 1
    assert frame.locator(".k-ie-brand h1").text_content() == "修图台"
    assert frame.locator('[data-window="background"] [data-do="strip"]').count() == 1
    assert frame.locator(".k-grid").count() == 0
