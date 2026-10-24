"""前端：开机动画（「按下开机」→ 晨会协议片头 → GMU 访问授权 → 首页）与「片头」设置。"""

from __future__ import annotations

import json
from pathlib import Path

BOOT = Path(__file__).resolve().parents[2] / "web" / "boot"


def child(page, name: str):
    for _ in range(50):
        for f in page.frames:
            if f.url.endswith(name) or name + "?" in f.url:
                f.wait_for_load_state()
                return f
        page.wait_for_timeout(100)
    raise AssertionError(f"没有找到 {name}：{[f.url for f in page.frames]}")


def test_intro_then_permit_then_home(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.wait_for_selector("#stage .seat")
    assert page.locator(".rt-boot").count() == 0  # 测试默认关着
    page.evaluate("localStorage.setItem('roundtable.boot.mode', 'full')")
    page.reload()
    # 浏览器还不让出声：先是「按下开机」，点了才开始放
    page.wait_for_selector(".rt-boot .rt-boot-gate")
    assert page.locator(".rt-boot iframe[src]").count() == 0
    page.click(".rt-boot-gate")
    page.wait_for_selector(".rt-boot iframe[src='boot/dawn.html']")
    intro = child(page, "dawn.html")
    intro.wait_for_selector(".seat.on", timeout=15000)  # 七个席位依次醒来
    # 片头放完 → 换成授权页
    intro.evaluate("parent.postMessage('boot:done', '*')")
    page.wait_for_selector(".rt-boot iframe[src^='boot/gmu.html']")
    page.wait_for_timeout(300)
    child(page, "gmu.html").evaluate("parent.postMessage('gmu:done', '*')")
    page.wait_for_selector(".rt-boot", state="detached")
    page.click("#new")  # 首页能点了
    # 同一个标签再刷新不再放
    page.reload()
    page.wait_for_selector("#stage .seat")
    page.wait_for_timeout(200)
    assert page.locator(".rt-boot").count() == 0


def test_skip_button_and_escape(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.evaluate(
        "localStorage.setItem('roundtable.boot.mode', 'permit');"
        "localStorage.setItem('roundtable.boot.when', 'always')"
    )
    page.reload()
    page.keyboard.press("Enter")  # 回车也能开机
    page.wait_for_selector(".rt-boot iframe[src^='boot/gmu.html']")
    page.click(".rt-boot-skip")
    page.wait_for_selector(".rt-boot", state="detached")
    page.reload()
    page.wait_for_selector(".rt-boot .rt-boot-gate")
    page.keyboard.press("Escape")
    page.wait_for_selector(".rt-boot", state="detached")
    page.goto(srv.url + "/?boot=0")
    page.wait_for_selector("#stage .seat")
    assert page.locator(".rt-boot").count() == 0


def test_settings_customize_card_photo_and_qr(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click("#bootcfg")
    page.wait_for_selector("#bootdlg[open] .bt")
    page.check("#bootdlg input[name='mode'][value='intro']")
    page.fill("#bt-card input[name='name']", "张三")
    page.fill("#bt-card input[name='org']", "GMU")
    page.click("#bt-card button[type='submit']")
    page.wait_for_selector("#bootdlg .pp-msg:has-text('卡片已保存')")
    page.fill("#bt-qr input[name='qr']", "https://example.com/me")
    page.click("#bt-qr button[type='submit']")
    page.wait_for_selector("#bootdlg .pp-msg:has-text('二维码已生成')")
    # 头像用页面里现画的一张图
    page.evaluate(
        """async () => {
          const c = document.createElement('canvas'); c.width = 40; c.height = 30;
          c.getContext('2d').fillRect(0, 0, 40, 30);
          const blob = await new Promise((r) => c.toBlob(r));
          const input = document.querySelector('#bootdlg [data-photo]');
          const dt = new DataTransfer();
          dt.items.add(new File([blob], 'me.png', { type: 'image/png' }));
          input.files = dt.files; input.dispatchEvent(new Event('change', { bubbles: true }));
        }"""
    )
    page.wait_for_selector("#bootdlg .pp-msg:has-text('头像已保存')")
    saved = page.evaluate(
        "() => Object.fromEntries(['mode', 'card', 'qr', 'qrText', 'photo']"
        ".map(k => [k, localStorage.getItem('roundtable.boot.' + k)]))"
    )
    assert saved["mode"] == "intro"
    assert json.loads(saved["card"]) == {"org": "GMU", "name": "张三"}
    assert saved["qrText"] == "https://example.com/me"
    n, bits = saved["qr"].split(":")
    assert int(n) >= 21 and bits
    assert saved["photo"].startswith("data:image/jpeg")
    # 授权页读到这些设置
    page.goto(srv.url + "/boot/gmu.html?auto=0&audio=0")
    assert page.inner_text("#idName") == "张三"
    assert page.inner_text("#idOrg") == "GMU"
    assert "data:image/jpeg" in page.eval_on_selector("#idPhoto", "e => e.style.backgroundImage")
    assert page.locator("#idQr canvas").count() == 1


def test_no_ueg_left_in_boot_pages():
    """所有 UEG 都换成了 GMU（早上好联合 / GOOD MORNING UNION），徽记是自绘的。"""
    for path in BOOT.rglob("*"):
        if path.suffix in {".html", ".js", ".svg"}:
            text = path.read_text(encoding="utf-8")
            assert "UEG" not in text and "ueg" not in text.lower(), path
            assert "联合政府" not in text and "UNITED EARTH" not in text, path
    assert not list(BOOT.rglob("*.png")) and not list(BOOT.rglob("*.wav"))


def test_sound_cues_and_silent_mode(serve, page):
    """动画页只发音效名和台词，外层收下；音效和播报都关掉时不用「按下开机」。"""
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.evaluate(
        "localStorage.setItem('roundtable.boot.mode', 'intro');"
        "localStorage.setItem('roundtable.boot.when', 'always');"
        "localStorage.setItem('roundtable.boot.sound', 'off');"
        "localStorage.setItem('roundtable.boot.voice', 'off')"
    )
    page.reload()
    page.wait_for_selector(".rt-boot iframe[src='boot/dawn.html']")
    assert page.locator(".rt-boot-gate").count() == 0
    intro = child(page, "dawn.html")
    intro.wait_for_selector(".hello.on", timeout=30000)  # 一路放到「早上好」
    page.wait_for_selector(".rt-boot", state="detached", timeout=15000)


def test_voice_settings_and_preview(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click("#bootcfg")
    page.wait_for_selector("#bootdlg[open] [data-voices]")
    page.select_option("#bootdlg select[name='lang']", "en")
    page.wait_for_selector("#bootdlg .pp-msg:has-text('已保存')")
    page.uncheck("#bootdlg input[name='sound']")
    saved = page.evaluate(
        "() => ['lang', 'sound'].map(k => localStorage.getItem('roundtable.boot.' + k))"
    )
    assert saved == ["en", "off"]
    page.click("#bootdlg [data-listen]")
    msg = "#bootdlg .pp-msg"
    page.wait_for_selector(f"{msg}:has-text('试听'), {msg}:has-text('没有可用')")


def test_no_wandering_earth_or_550c_left():
    """片头是圆桌原创；授权页里 550C 和那部电影的元素都换掉了。"""
    web = BOOT.parent
    pages = [
        *BOOT.rglob("*.html"),
        *BOOT.rglob("*.svg"),
        web / "js" / "boot.js",
        web / "js" / "bootcfg.js",
    ]
    banned = ("550C", "550c", "流浪地球", "MOSS", "FOR ALL MANKIND", "培强", "启航", "生活区通行")
    for path in pages:
        text = path.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, (path, word)
    assert not (BOOT / "550c.html").exists()
