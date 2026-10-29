"""前端：「上网」成员自己上网。用假浏览器，不真开网页。

顶栏「上网」→ 选成员和网站 → 去逛逛 → 顶栏变「● 某某在逛」、面板显示正在看的页面 →
浏览记录里有每页的感想和小结 → 逛完顶栏恢复。
"""

from __future__ import annotations

import json

from roundtable.core.config.schema import WanderSite
from roundtable.core.wander import FakeWeb, Snapshot

HOME = "https://www.bilibili.com/"
VIDEO = "https://www.bilibili.com/video/BV1"


def setup(env) -> None:
    site = WanderSite(id="bilibili", name="B站", home=HOME, hosts=["bilibili.com"])
    chat = env.rt.config.roundtable.chat
    w = chat.wander.model_copy(update={"sites": [site], "step_wait_seconds": 1.5})
    rt = env.rt.config.roundtable.model_copy(update={"chat": chat.model_copy(update={"wander": w})})
    env.rt.config = env.rt.config.model_copy(update={"roundtable": rt})
    web = FakeWeb(
        {
            HOME: Snapshot(HOME, "哔哩哔哩首页", "推荐：猫咪合集", [("猫咪合集", VIDEO)]),
            VIDEO: Snapshot(VIDEO, "猫咪合集", "一只橘猫在睡觉", []),
        }
    )
    env.rt.wander_web = lambda: web
    step = lambda **kw: json.dumps(kw, ensure_ascii=False)  # noqa: E731
    env.fake.queue("b1", step(note="首页推荐了猫咪合集，看看", action="open", link=1))
    env.fake.queue(
        "b1", step(note="橘猫睡得好香", action="done", summary="今天看了猫咪合集，很治愈")
    )


def test_wander_panel_flow(serve, page):
    srv = serve(confirm_threshold_usd=100.0, setup=setup)
    page.goto(srv.url)
    btn = page.locator("#wanderbtn")
    assert btn.text_content() == "上网"
    btn.click()
    dlg = page.locator("#wanderdlg")
    dlg.get_by_text("还没自己上过网").wait_for()
    dlg.locator("[data-who]").select_option("V1")
    dlg.locator("[data-site]").select_option("bilibili")
    dlg.locator("[data-go]").click()
    page.wait_for_function("document.querySelector('#wanderbtn').classList.contains('wd-on')")
    assert "阿一" in btn.get_attribute("title")
    dlg.locator(".wd-bar", has_text="正在逛B站").wait_for()
    dlg.locator(".wd-page", has_text="首页推荐了猫咪合集").wait_for()
    link = dlg.locator(".wd-page a", has_text="哔哩哔哩首页")
    assert link.get_attribute("href") == HOME and link.get_attribute("rel") == "noopener noreferrer"
    dlg.locator(".wd-sum", has_text="今天看了猫咪合集").wait_for(timeout=15000)
    idle = "!document.querySelector('#wanderbtn').classList.contains('wd-on')"
    page.wait_for_function(idle, timeout=15000)
    assert btn.text_content() == "上网"
    dlg.locator(".wd-tab.on span", has_text="1").wait_for()
