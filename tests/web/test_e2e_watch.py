"""前端：「一起看」投屏陪伴。用假的画面来源，不真截屏。

顶栏「一起看」→ 选来源和陪看成员 → 不勾同意会提示 → 勾同意开始 → 顶栏变红点、预览出现、
成员发言 → 说一句、被 @ 的成员回 → 输入时状态刷新不清空输入框 → 停止。
"""

from __future__ import annotations

import re

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


DECODE_QR = """async () => {
  await new Promise((r) => {
    const s = document.createElement('script');
    s.src = 'boot/assets/jsqr.js';
    s.onload = r;
    document.head.appendChild(s);
  });
  const svg = new XMLSerializer().serializeToString(document.querySelector('svg.wt-qr'));
  const img = new Image();
  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
  await img.decode();
  const c = document.createElement('canvas');
  c.width = c.height = 400;
  const g = c.getContext('2d');
  g.imageSmoothingEnabled = false;
  g.drawImage(img, 0, 0, 400, 400);
  return jsQR(g.getImageData(0, 0, 400, 400).data, 400, 400)?.data || '';
}"""


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

    polls = 0

    def qr_ready(self):
        pass

    def qr_pair(self, name, password):
        self.polls += 1  # 第二次问的时候「手机扫了」
        return "192.168.1.5:40021" if self.polls >= 2 else None


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
    # 填成配对端口点连接：自动改用连接端口
    dlg.locator("[data-adb-port]").fill("40021")
    dlg.locator("[data-connect]").click()
    dlg.get_by_text("已自动改用手机的连接端口").wait_for()
    assert dlg.locator("[data-adb-port]").input_value() == "37000"
    # 方式一：扫二维码
    dlg.locator("[data-qr]").click()
    dlg.locator("svg.wt-qr").wait_for()
    # 画出来的二维码能被识别，内容是安卓扫码配对的格式
    text = page.evaluate(DECODE_QR)
    assert re.fullmatch(r"WIFI:T:ADB;S:roundtable-[a-z0-9]{8};P:[a-z0-9]{12};;", text)
    dlg.get_by_text("使用二维码配对设备」→ 扫上面这张图").wait_for()
    dlg.get_by_text("扫码配对成功，已连上 192.168.1.5:37000").wait_for(timeout=10000)
    assert dlg.locator("svg.wt-qr").count() == 0
    dlg.get_by_text("iPhone 怎么投").click()
    dlg.get_by_text("屏幕镜像").wait_for()
