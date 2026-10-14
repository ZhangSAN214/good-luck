"""前端：头脑风暴显示想法、拓展和思维导图（SVG 放在图片里，下载与代码可展开）。"""

from __future__ import annotations

from ..core.orchestrator.test_brainstorm import (
    EXPAND,
    IDEATE,
    ORGANIZE,
    TOPIC,
    expand_reply,
    ideate_reply,
    organize_reply,
)
from ..core.orchestrator.test_media import script
from .test_e2e import ask, wait_done


def test_brainstorm_shows_ideas_and_mindmap(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    script(srv.env, **{IDEATE: ideate_reply, EXPAND: expand_reply, ORGANIZE: organize_reply})
    page.goto(srv.url)
    page.wait_for_selector("#workflow button[data-v='brainstorm']")
    page.click("#workflow button[data-v='brainstorm']")
    assert "头脑风暴输出思维导图" in page.inner_text("#media")
    ask(page, TOPIC)
    wait_done(page)
    chat = page.inner_text("#chat")
    assert "发散" in chat and "拓展" in chat and "整合成导图" in chat
    img = page.locator("#chat .mindmap img")
    assert img.count() == 1
    src = img.get_attribute("src")
    assert src.startswith("data:image/svg+xml") and "%3Csvg" in src
    # 图片真的渲染出来了（有尺寸），而不是坏图
    assert img.evaluate("e => e.complete && e.naturalWidth > 100")
    assert page.locator("#chat .mindmap a[download='mindmap.svg']").count() == 1
    assert "mindmap" in page.locator("#chat .mindmap details").nth(1).inner_html()
