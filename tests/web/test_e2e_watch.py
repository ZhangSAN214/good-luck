"""前端：「一起看」投屏陪伴。用假的画面来源，不真截屏。

顶栏「一起看」→ 选来源和陪看成员 → 不勾同意会提示 → 勾同意开始 → 顶栏变红点、预览出现、
成员发言 → 说一句、被 @ 的成员回 → 输入时状态刷新不清空输入框 → 停止。
"""

from __future__ import annotations

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from roundtable.core.screen import FakeScreenSource, SourceInfo  # noqa: E402


def setup(env) -> None:
    env.rt.screen_sources = {
        "pc": FakeScreenSource(
            [Image.new("RGB", (640, 360), (30, 30, 30))],
            sources=[SourceInfo("pc", "pc", "整个屏幕"), SourceInfo("pc:42", "window", "哔哩哔哩")],
        )
    }


def test_watch_panel_flow(serve, page):
    srv = serve(confirm_threshold_usd=100.0, setup=setup)
    page.goto(srv.url)
    btn = page.locator("#watchbtn")
    btn.click()
    dlg = page.locator("#watchdlg")
    dlg.get_by_text("哔哩哔哩").click()
    dlg.locator("[data-member='V1']").check()
    dlg.locator("[data-start]").click()
    dlg.get_by_text("请先勾选同意").wait_for()
    dlg.locator("[data-consent]").check()
    dlg.locator("[data-start]").click()
    dlg.locator(".wt-on").wait_for()
    page.wait_for_function("document.querySelector('#watchbtn').textContent.includes('投屏中')")
    dlg.get_by_text("正在投屏「哔哩哔哩」").wait_for()
    dlg.locator(".wt-msg b", has_text="阿一").first.wait_for()
    dlg.locator(".wt-frame:not([hidden])").wait_for()
    # 正在输入时状态区刷新，输入框里的字还在
    box = dlg.locator(".wt-say input")
    box.fill("@阿一 你看")
    page.wait_for_timeout(3500)
    assert box.input_value() == "@阿一 你看"
    box.press("Enter")
    dlg.locator(".wt-msg.me", has_text="@阿一 你看").wait_for()
    page.wait_for_function("document.querySelectorAll('#watchdlg .wt-msg:not(.me)').length >= 2")
    dlg.locator("[data-stop]").click()
    dlg.locator("[data-start]").wait_for()
    assert btn.text_content() == "一起看"
