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


class _WirelessAdb:
    def available(self):
        return None

    def sources(self):
        return []

    def pair(self, host, port, code):
        from roundtable.core.screen import ScreenUnavailable

        if code != "123456":
            raise ScreenUnavailable("配对没成功：配对码不对或已过期")
        return "配对成功"

    def connect(self, host="", port=0):
        return ["192.168.1.5:37000"]


def setup_phone(env) -> None:
    setup(env)
    env.rt.screen_sources["android"] = _WirelessAdb()


def test_watch_pair_android_wirelessly(serve, page):
    srv = serve(confirm_threshold_usd=100.0, setup=setup_phone)
    page.goto(srv.url)
    page.locator("#watchbtn").click()
    dlg = page.locator("#watchdlg")
    dlg.get_by_text("连接安卓手机 / 平板（无线，不用插线）").click()
    dlg.get_by_text("使用配对码配对设备").wait_for()
    dlg.locator("[data-adb-host]").fill("192.168.1.5")
    dlg.locator("[data-adb-port]").fill("41234")
    dlg.locator("[data-adb-code]").fill("12")
    dlg.locator("[data-pair]").click()
    dlg.get_by_text("请填 IP 地址、端口和 6 位配对码").wait_for()
    dlg.locator("[data-adb-code]").fill("000000")
    dlg.locator("[data-pair]").click()
    dlg.get_by_text("配对码不对或已过期").wait_for()
    dlg.locator("[data-adb-code]").fill("123456")
    dlg.locator("[data-pair]").click()
    dlg.get_by_text("配对成功，已连上 192.168.1.5:37000").wait_for()
    # 面板重画后折叠区保持展开、IP 还在
    assert dlg.locator("[data-adb-host]").input_value() == "192.168.1.5"
    dlg.get_by_text("iPhone 怎么投").click()
    dlg.get_by_text("屏幕镜像").wait_for()


def test_watch_overlay_button_and_pip(serve, page, monkeypatch):
    """投屏中：「悬浮窗」按钮请电脑开置顶小窗（不切屏也能看她们说话）；浏览器支持时有「画中画」。"""
    import roundtable.core.service as service_mod

    started = []

    class Proc:
        def poll(self):
            return None

    def popen(cmd, **kw):
        started.append(cmd)
        return Proc()

    monkeypatch.setattr(service_mod.subprocess, "Popen", popen)
    monkeypatch.setattr(service_mod, "_has_tk", lambda: True)
    srv = serve(confirm_threshold_usd=100.0, setup=setup)
    page.goto(srv.url)
    page.locator("#watchbtn").click()
    dlg = page.locator("#watchdlg")
    dlg.get_by_text("哔哩哔哩").click()
    dlg.locator("[data-member='V1']").check()
    dlg.locator("[data-consent]").check()
    dlg.locator("[data-start]").click()
    dlg.locator(".wt-on").wait_for()
    dlg.locator("[data-overlay]").click()
    dlg.get_by_text("悬浮窗打开了").wait_for()
    dlg.locator("[data-overlay]").click()
    dlg.get_by_text("悬浮窗已经开着了").wait_for()
    assert len(started) == 1 and "roundtable.desktop.overlay" in started[0]
    has_pip = page.evaluate("'documentPictureInPicture' in window")
    assert dlg.locator("[data-pip]").count() == (1 if has_pip else 0)
    dlg.locator("[data-stop]").click()
    dlg.locator("[data-start]").wait_for()
