"""前端：余额与用量 → 走哪条路（自动判断 / 我来排、每月附送额度），保存后后端路由立即生效。"""

from __future__ import annotations

import os

from .test_e2e_hub import LAND_HUB


def test_channel_priority_panel(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.add_init_script(LAND_HUB)
    page.goto(srv.url)
    page.click("#hub .hb-bal")
    page.wait_for_selector("#usagedlg [data-pstrat]")
    text = page.inner_text("#usagedlg")
    assert "走哪条路" in text and "每月附送额度" in text
    assert page.get_attribute("#usagedlg [data-pstrat='auto']", "aria-pressed") == "true"

    page.click("#usagedlg [data-pstrat='custom']")
    page.wait_for_selector("#usagedlg .us-pno")
    channel = page.get_attribute("#usagedlg [data-credit]", "data-credit")
    page.fill(f"#usagedlg [data-credit='{channel}']", "100")
    page.click("#usagedlg [data-psave]")
    page.wait_for_selector("#usagedlg >> text=已保存")
    assert "本月额度还剩" in page.inner_text("#usagedlg")
    prio = srv.env.rt.router.priority
    assert prio.strategy == "custom" and prio.credits[channel] == 100.0
    shot = os.environ.get("ROUNDTABLE_SHOT")
    if shot:
        page.locator("#usagedlg .grp").nth(1).screenshot(path=shot)
