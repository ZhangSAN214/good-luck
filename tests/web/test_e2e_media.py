"""前端：输出类型选择、媒体成果显示与播放、视频每次确认、重新生成循环、媒体面板、匿名。"""

from __future__ import annotations

from ..core.orchestrator.conftest import MEDIUM
from ..core.orchestrator.test_media import REFINE, REVIEW, script
from ..core.steps.conftest import media_decision_reply, media_review_reply
from .test_e2e import ask, leaks, wait_done

PICKS = ("b1", "b2", "b3", "f1")


def pick_lineup(page) -> None:
    """自选：b2 带 vision 标签当组员（评审者），f1 当统筹。"""
    page.click("#tier button[data-v='custom']")
    for m in PICKS:
        page.check(f"#picks input[value='{m}']")
    page.select_option("#coordinator", "f1")


def choose_output(page, kind: str) -> None:
    page.click(f"#media button[data-v='{kind}']")
    page.wait_for_selector(f"#media button[data-v='{kind}'][aria-pressed='true']")


def test_output_selector_reflects_availability(serve, page):
    srv = serve(confirm_threshold_usd=100.0)  # 没有媒体模型
    page.goto(srv.url)
    page.wait_for_selector("#tier button")
    assert page.is_hidden("#media") or page.locator("#media button:not([disabled])").count() <= 1
    srv2 = serve(confirm_threshold_usd=100.0, with_media=True)
    page.goto(srv2.url)
    page.wait_for_selector("#media button[data-v='image']:not([disabled])")
    labels = page.locator("#media button").all_inner_texts()
    assert labels == ["文字", "图片", "语音", "视频"]
    assert page.is_hidden("#mediatier")  # 选了媒体输出后才出现质量档位
    choose_output(page, "video")
    assert page.is_visible("#mediatier")
    page.click("#workflow button[data-v='collab']")
    assert (
        page.locator("#media button[data-v='image'][disabled]").count() == 1
    )  # 协同模式由统筹决定
    assert page.locator("#media button[data-v=''][aria-pressed='true']").count() == 1
    assert page.is_visible("#mediatier")  # 媒体子任务也用这个档位


def test_estimate_lists_media_step(serve, page):
    srv = serve(confirm_threshold_usd=100.0, with_media=True)
    page.goto(srv.url)
    page.fill("#ask", MEDIUM)
    page.wait_for_selector("#estimate table")
    before = page.inner_text("#estimate")
    choose_output(page, "video")
    page.wait_for_function("(t) => document.querySelector('#estimate').innerText !== t", arg=before)
    page.click("#estimate summary")
    assert "生成媒体" in page.inner_text("#estimate")


def test_image_round_is_shown_with_prompt_review_and_media_panel(serve, page):
    srv = serve(confirm_threshold_usd=100.0, with_media=True)
    page.goto(srv.url)
    pick_lineup(page)
    choose_output(page, "image")
    ask(page, MEDIUM)
    wait_done(page)
    assert page.locator(".phase:text-is('生成媒体')").count() == 1
    job = page.locator(".mediajob[data-state='completed']")
    assert job.count() == 1
    assert "图片" in job.inner_text() and "第 1 轮" in job.inner_text()
    img = job.locator("img.thumb")
    assert img.count() == 1
    assert img.evaluate("e => e.complete && e.naturalWidth > 0")  # 图片真的加载了
    assert "mi1" in job.inner_text()  # 匿名关闭：显示模型
    page.click(".mediajob details summary")
    assert "最大值 2，最小值 -2" in page.inner_text(".mediajob")  # 生成提示词
    assert page.locator(".msg:has-text('评审第 1 轮')").count() == 1  # vision 成员的评审
    assert "题目" in page.inner_text("#question")

    page.click('#tabs button[data-t="media"]')
    body = page.inner_text("#pbody")
    assert "媒体花费" in body and "第 1 轮" in body and "图片" in body and "完成" in body
    assert page.locator("#pbody img.thumb").count() == 1
    assert page.inner_text("#media-cost").startswith("$0.04")


def test_regeneration_rounds_are_shown(serve, page):
    srv = serve(confirm_threshold_usd=100.0, with_media=True)
    n = {"i": 0}

    def review(model, messages):
        n["i"] += 1
        return media_review_reply(n["i"] >= 2)(model, messages)

    def refine(model, messages):
        return media_decision_reply(n["i"] >= 2, prompt="画面更明亮的猫")(model, messages)

    script(srv.env, **{REVIEW: review, REFINE: refine})
    page.goto(srv.url)
    pick_lineup(page)
    choose_output(page, "image")
    ask(page, MEDIUM)
    wait_done(page)
    jobs = page.locator(".mediajob")
    assert jobs.count() == 2
    assert "第 1 轮" in jobs.nth(0).inner_text() and "第 2 轮" in jobs.nth(1).inner_text()
    assert page.locator(".msg:has-text('决定（第 1 轮）')").count() == 1
    page.locator(".mediajob").nth(1).locator("summary").click()
    assert "画面更明亮的猫" in jobs.nth(1).inner_text()


def test_video_asks_each_time_then_plays(serve, page):
    srv = serve(confirm_threshold_usd=100.0, with_media=True)
    page.goto(srv.url)
    pick_lineup(page)
    choose_output(page, "video")
    ask(page, MEDIUM)
    page.wait_for_selector(".card[data-kind='media'] .opt[data-opt='generate']")
    card = page.inner_text(".card[data-kind='media']")
    assert "媒体生成" in card and "视频" in card and "不生成" in card
    assert page.locator(".mediajob").count() == 0  # 确认之前没有生成、没有花钱
    page.click(".card[data-kind='media'] .opt[data-opt='generate']")
    wait_done(page)
    video = page.locator(".mediajob video.player")
    assert video.count() == 1
    src = video.get_attribute("src")
    assert src.endswith("?inline=1")
    resp = page.request.get(srv.url + src)
    assert resp.status == 200 and resp.headers["content-type"] == "video/mp4"
    assert resp.headers["accept-ranges"] == "bytes"
    assert page.locator(".card[data-kind='media'].resolved").count() == 1
    assert "已选择：生成" in page.inner_text(".card[data-kind='media']")
    # 下载链接
    assert page.locator(".mediajob a[download]").count() == 1


def test_video_skip_finishes_without_generation(serve, page):
    srv = serve(confirm_threshold_usd=100.0, with_media=True)
    page.goto(srv.url)
    pick_lineup(page)
    choose_output(page, "video")
    ask(page, MEDIUM)
    page.click(".card[data-kind='media'] .opt[data-opt='skip']")
    wait_done(page)
    assert page.locator(".mediajob").count() == 0


def test_speech_plays_in_page(serve, page):
    srv = serve(confirm_threshold_usd=100.0, with_media=True)
    page.goto(srv.url)
    pick_lineup(page)
    choose_output(page, "speech")
    ask(page, MEDIUM)
    wait_done(page)
    audio = page.locator(".mediajob audio.player")
    assert audio.count() == 1
    resp = page.request.get(srv.url + audio.get_attribute("src"))
    assert resp.status == 200 and resp.headers["content-type"] == "audio/wav"


def test_anonymous_media_session_hides_models_until_reveal(serve, page):
    srv = serve(confirm_threshold_usd=100.0, with_media=True)
    page.goto(srv.url)
    pick_lineup(page)
    choose_output(page, "image")
    page.check("#anonymous")
    ask(page, MEDIUM)
    wait_done(page)
    # 提问区的"自选"勾选框本来就列出模型名（提交前的选择），只检查会话相关的区域
    text = "\n".join(page.inner_text(sel) for sel in ("header", "#stage", "#question", "#feed"))
    for tab in ("flow", "reviews", "cast", "tools"):
        page.click(f'#tabs button[data-t="{tab}"]')
        text += page.inner_text("#pbody")
    page.click('#tabs button[data-t="media"]')
    text += page.inner_text("#pbody")
    assert page.locator(".mediajob img.thumb").count() == 1
    assert leaks(srv.anonymous_terms(), text) == []
    page.click("#reveal")
    page.wait_for_selector(".mediajob .ai")
    assert "mi1" in page.inner_text(".mediajob")


def test_collab_media_subtask_shows_generation_note_and_player(serve, page):
    from ..core.steps.conftest import decompose_reply

    srv = serve(confirm_threshold_usd=100.0, with_media=True)
    script(srv.env, **{"把任务拆成子任务": decompose_reply(2, media={"T2": "image"})})
    page.goto(srv.url)
    pick_lineup(page)
    page.click("#workflow button[data-v='collab']")
    ask(page, MEDIUM)
    wait_done(page)
    assert page.locator(".msg:has-text('已生成图片（第 1 轮')").count() >= 1
    assert page.locator(".filebar-wrap img.thumb").count() >= 1
    page.click('#tabs button[data-t="media"]')
    assert "T2" in page.inner_text("#pbody") and "第 2 轮" in page.inner_text("#pbody")
