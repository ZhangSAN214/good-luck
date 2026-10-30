"""前端：「工作台」→「生成器」。助手、质检、找插件都是假的，不跑命令行、不联网。

一句话 → 进度条走完 → 窗口分工表、插件清单（直接用 / 只借思路）、每轮分数和截图 →
打开成品（沙箱 iframe）→ 成品里的对话窗经 postMessage 问到后端、拿到回答。
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import roundtable.core.service as service_mod
from roundtable.core.benchkit.builder import BenchBuilder
from roundtable.core.benchkit.quality import Report
from roundtable.core.benchkit.scout import Candidate
from roundtable.core.supercomputer.runner import RunResult

from ..core.benchkit.test_builder import FakeSC, W
from ..core.benchkit.test_builder import render as fake_render

# 1×1 的 PNG：截图要能真的显示出来
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
ASK_WINDOW = (
    "export default async (el, ctx) => {"
    " el.innerHTML = '<p class=\"answer k-sub\">' + ctx.kit.esc('…') + '</p>';"
    " el.querySelector('.answer').textContent = await ctx.ask('第一天去哪');"
    " };"
)


class SlowSC(FakeSC):
    """写窗口时放一个会提问的窗口；其余同 FakeSC。"""

    async def step(self, kind, prompt, **kw):
        chosen, result = await super().step(kind, prompt, **kw)
        p = json.loads(prompt)
        if p["role"] == "bench_window" and p["file"].endswith("plan.js"):
            (kw["folder"] / p["file"]).write_text(ASK_WINDOW, encoding="utf-8")
        if p["role"] == "bench_ask":
            return W(chosen.id), RunResult(0, "先去浅草寺，再去上野")
        return chosen, result


class FakeScout:
    async def search(self, need, keywords):
        return [
            Candidate(
                "github",
                "acme/maps",
                "https://github.com/acme/maps",
                stars=900,
                license="MIT",
                verdict="adopt",
                why="MIT，900 星",
                need=need,
            ),
            Candidate(
                "dsh",
                "solo/tiny",
                "https://github.com/solo/tiny",
                stars=12,
                license="MIT",
                verdict="idea",
                why="12 星，不到 100 星，只借思路",
                need=need,
            ),
        ]

    async def fetch(self, c, dest):
        dest.mkdir(parents=True, exist_ok=True)
        return {"repo": c.repo, "commit": "0" * 40, "sha256": "x", "files": 1}


def check(target, out_dir=None, **kw):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shots = []
    for name in ("电脑_light", "手机_dark"):
        (out / f"{name}.png").write_bytes(PNG)
        shots.append(str(out / f"{name}.png"))
    return Report(target=target, shots=shots, combos=2)


def test_benchgen_flow(serve, page, monkeypatch, tmp_path):
    def factory(rules, *, root, **kw):
        return BenchBuilder(
            rules,
            root=tmp_path / "bench",
            supercomputer=SlowSC(),
            render=fake_render,
            check=check,
            scout=FakeScout(),
            seed=3,
        )

    monkeypatch.setattr(service_mod, "BenchBuilder", factory)
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.locator('#entries [data-m="bench"]').click()
    pane = page.locator('#bench [data-pane="gen"]')
    pane.locator("[data-text]").wait_for()
    assert page.locator('#bench [data-tab="gen"]').get_attribute("aria-selected") == "true"
    pane.locator("[data-text]").fill("帮我做一个东京三天旅行的工作台")
    pane.locator("[data-submit]").click()
    pane.locator(".bg-status.bg-done").wait_for(timeout=15000)

    assert pane.locator(".bg-title").text_content() == "旅行台"
    assert pane.locator(".bg-steps li.past").count() == 5  # 一轮就过线：返工那格跳过
    assert pane.locator(".bg-steps li.skip").text_content() == "返工（跳过）"
    assert pane.locator(".sc-subs tr", has_text="写好了").count() == 3
    src = pane.locator(".bg-sources")
    assert "直接用 1 个，只借思路 1 个" in src.locator("summary").text_content()
    assert src.locator(".bg-v-adopt").text_content() == "直接用"
    rnd = pane.locator(".bg-round").first
    assert "过线" in rnd.locator("summary").text_content()
    assert rnd.locator(".bg-scores li").count() == 7
    shot = rnd.locator(".bg-shot img").first
    page.wait_for_function(
        "(img) => img.complete && img.naturalWidth > 0", arg=shot.element_handle()
    )
    assert "手机 · 深色" in rnd.locator(".bg-shots").text_content()

    pane.locator("button[data-preview]").click()
    frame = page.frame_locator("iframe.bg-frame")
    frame.locator(".answer", has_text="先去浅草寺").wait_for(timeout=10000)
    assert frame.locator("h1").text_content() == "旅行台"
    if shots := os.environ.get("ROUNDTABLE_SHOTS"):  # 版本说明用的截图
        page.set_viewport_size({"width": 1280, "height": 2600})
        page.screenshot(path=str(Path(shots) / "benchgen.png"))
