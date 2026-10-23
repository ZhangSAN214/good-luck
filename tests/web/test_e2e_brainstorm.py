"""前端：头脑风暴显示抽到的手法、碰撞杂交、投票和灵感结果页（灵感墙、象限图、谱系、路线图等页签）。"""

from __future__ import annotations

from ..core.orchestrator.test_brainstorm import (
    CROSS,
    IDEATE,
    ORGANIZE,
    TOPIC,
    VOTE,
    cross_reply,
    ideate_reply,
    organize_reply,
    vote_reply,
)
from ..core.orchestrator.test_media import script
from .test_e2e import ask, wait_done


def test_brainstorm_shows_board_tabs_and_mindmap(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    script(
        srv.env,
        **{IDEATE: ideate_reply, CROSS: cross_reply, VOTE: vote_reply, ORGANIZE: organize_reply},
    )
    page.goto(srv.url)
    page.wait_for_selector("#workflow button[data-v='brainstorm']")
    page.click("#workflow button[data-v='brainstorm']")
    assert "点子榜" in page.inner_text("#media")
    assert "适合找点子" in page.inner_text("#mode-hint")
    ask(page, TOPIC)
    wait_done(page)
    chat = page.inner_text("#chat")
    for part in ("发散", "抽到「", "碰撞杂交", "杂交", "匿名投票", "投给", "灵感结果", "本场抽签"):
        assert part in chat, part
    board = page.locator("#chat .final.ideas")
    assert board.count() == 1
    tabs = board.locator(".btabs label").all_inner_texts()
    for t in ("灵感墙", "象限图", "点子榜", "杂交谱系", "路线图", "故事板", "对比矩阵", "导图"):
        assert t in tabs, t
    # 默认是灵感墙：按主题分组的便利贴
    assert board.locator(".wall .note").count() >= 1 and board.locator(".wall").is_visible()
    assert board.locator("a[download='头脑风暴.md']").count() == 1

    def show(tab):
        board.locator(".btabs label", has_text=tab).click()
        return board.locator(f".pane[data-view='{tab}']")

    assert show("象限图").locator("svg.quad circle").count() >= 2
    assert show("点子榜").locator(".rank li").count() >= 3
    assert show("杂交谱系").locator(".lrow").count() >= 1
    assert show("路线图").locator(".road li").count() >= 3 and "做一次小试" in board.inner_text()
    assert "的故事" in show("故事板").inner_text()
    assert show("对比矩阵").locator(".mbar").count() >= 3
    pane = show("导图")
    assert pane.locator(".mbranch").count() >= 1 and not board.locator(".wall").is_visible()
    src = pane.locator("a[download='mindmap.svg']").get_attribute("href")
    assert src.startswith("data:image/svg+xml") and "%3Csvg" in src
    # 页面重绘（如改变窗口大小）后，选中的页签不被重置
    page.set_viewport_size({"width": 1100, "height": 900})
    page.wait_for_timeout(300)
    assert pane.locator(".mbranch").first.is_visible()
