"""前端：头脑风暴显示抽到的手法、碰撞杂交、投票和灵感结果页（点子榜、导图、对比表等页签）。"""

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
    # 默认显示点子榜；切到导图页签后图片真的渲染出来了（有尺寸），而不是坏图
    assert board.locator(".rank li").count() >= 3
    assert board.locator(".rank").is_visible()
    board.locator(".btabs label", has_text="导图").click()
    img = board.locator(".mindmap img")
    assert img.is_visible() and not board.locator(".rank").is_visible()
    src = img.get_attribute("src")
    assert src.startswith("data:image/svg+xml") and "%3Csvg" in src
    assert img.evaluate("e => e.complete && e.naturalWidth > 100")
    assert board.locator(".mindmap a[download='mindmap.svg']").count() == 1
    # 页面重绘（如改变窗口大小）后，选中的页签不被重置
    page.set_viewport_size({"width": 1100, "height": 900})
    page.wait_for_timeout(300)
    assert board.locator(".mindmap img").is_visible()
    for tab, text in (("对比表", "投入"), ("场景故事", "的故事"), ("行动方案", "做一次小试")):
        board.locator(".btabs label", has_text=tab).click()
        assert text in board.locator(".pane:visible").inner_text(), tab
