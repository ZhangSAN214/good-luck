"""前端：风格参考图勾选、风格规范卡片、参考图张数、风格校验与重画、匿名。"""

from __future__ import annotations

from ..core.attachments.samples import PNG
from ..core.orchestrator.conftest import MEDIUM
from ..core.orchestrator.test_media import script
from ..core.orchestrator.test_pipeline import WHOLE
from ..core.orchestrator.test_style import (
    CHECKLIST,
    EXTRACT_MARK,
    EXTRACTION,
    MERGE_MARK,
    REVIEW_MARK,
    merge_reply,
    review_reply,
    with_style_replies,
)
from .test_e2e import ask, leaks, wait_done
from .test_e2e_media import choose_output, pick_lineup
from .test_e2e_v2 import upload


def attach_reference(page) -> None:
    page.wait_for_selector("#attach")
    upload(page, "ref.png", PNG, "image/png")
    page.wait_for_selector("#attachments .chip[data-att]:has-text('ref.png')")


def test_image_attachment_has_a_style_reference_checkbox_on_by_default(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    attach_reference(page)
    box = page.locator("#attachments .chip .sref input")
    assert box.count() == 1 and box.is_checked()
    upload(page, "笔记.txt", "第一章".encode(), "text/plain")
    page.wait_for_selector("#attachments .chip[data-att]:has-text('笔记.txt')")
    assert page.locator("#attachments .chip .sref").count() == 1  # 只有图片有这个勾选
    box.uncheck()
    assert not box.is_checked()


def test_style_card_reference_count_and_media_panel(serve, page):
    srv = serve(confirm_threshold_usd=100.0, with_media=True)
    with_style_replies(srv.env)
    page.goto(srv.url)
    pick_lineup(page)
    choose_output(page, "image")
    page.click("#mediatier button[data-v='flagship']")  # 高质量档位有支持参考图的模型
    attach_reference(page)
    ask(page, MEDIUM)
    wait_done(page)
    assert page.locator(".phase:text-is('风格规范')").count() == 1
    card = page.locator(".sys:has-text('风格规范已提取')")
    assert card.count() == 1
    card.locator("summary").click()
    text = card.inner_text()
    assert "合并后" in text and f"风格清单（{len(CHECKLIST)} 条）" in text and CHECKLIST[0] in text
    job = page.locator(".mediajob")
    assert "参考图 1 张" in job.inner_text()
    page.click('#tabs button[data-t="media"]')
    assert "参考图 1 张" in page.inner_text("#pbody")
    # 评审对照清单逐条判定
    assert page.locator(".msg:has-text('对照风格清单')").count() >= 1
    assert page.locator(".msg ul.gate li:has-text('符合')").count() >= 1


def test_unchecked_image_is_a_plain_attachment(serve, page):
    srv = serve(confirm_threshold_usd=100.0, with_media=True)
    with_style_replies(srv.env)
    page.goto(srv.url)
    pick_lineup(page)
    choose_output(page, "image")
    attach_reference(page)
    page.uncheck("#attachments .chip .sref input")
    ask(page, MEDIUM)
    wait_done(page)
    assert page.locator(".sys:has-text('风格规范已提取')").count() == 0
    assert page.locator(".phase:text-is('风格规范')").count() == 0
    assert "参考图" not in page.locator(".mediajob").inner_text()


def test_no_reference_model_shows_a_clear_warning(serve, page):
    srv = serve(confirm_threshold_usd=100.0, with_media=True)
    with_style_replies(srv.env)
    page.goto(srv.url)
    pick_lineup(page)
    choose_output(page, "image")  # 默认档位的画图模型不支持参考图
    attach_reference(page)
    ask(page, MEDIUM)
    wait_done(page)
    job = page.locator(".mediajob")
    assert "没有支持参考图的画图模型，只能按文字风格规范生成" in job.inner_text()


def test_collab_style_gate_lines_and_anonymous_view(serve, page):
    srv = serve(confirm_threshold_usd=100.0, with_media=True, pipeline=True)
    script(
        srv.env,
        **{
            "把任务拆成子任务": lambda m, msgs: WHOLE,
            "没有通过代码检查": lambda m, msgs: WHOLE,
            EXTRACT_MARK: lambda m, msgs: EXTRACTION,
            MERGE_MARK: merge_reply,
            REVIEW_MARK: review_reply([False, True]),
        },
    )
    page.goto(srv.url)
    page.click("#tier button[data-v='custom']")
    for m in ("b1", "b2", "f1", "b3"):
        page.check(f"#picks input[value='{m}']")
    page.select_option("#coordinator", "b3")
    page.click("#workflow button[data-v='collab']")
    page.check("#anonymous")
    attach_reference(page)
    ask(page, "参考这张图做一张四格人物介绍卡片")
    wait_done(page)
    chat = page.inner_text("#chat")
    assert "风格规范已提取" in chat
    assert page.locator(".msg:has-text('风格校验')").count() >= 2  # 第一次不通过，重画后通过
    assert "不通过，退回重画" in chat and "通过" in chat
    assert page.locator(".msg:has-text('按风格意见改提示词')").count() == 1
    page.click('#tabs button[data-t="split"]')
    assert page.locator("#pbody .chain .node").count() == 6
    # 自选面板列着所有模型 id（那是提交前用户自己的选择），所以只检查过程区、圆桌和各面板
    terms = srv.anonymous_terms()
    assert leaks(terms, page.inner_text("#feed")) == []
    assert leaks(terms, page.inner_text("#stage")) == []
    for tab in ("flow", "reviews", "cast", "split", "media"):
        page.click(f'#tabs button[data-t="{tab}"]')
        assert leaks(terms, page.inner_text("#pbody")) == [], tab
