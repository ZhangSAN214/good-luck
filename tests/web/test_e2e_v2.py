"""前端改版（阶段 18）：附件与拖放、提交前预估、工具调用与文件预览、来源链接、分工面板、12 座。"""

from __future__ import annotations

import re

from ..core.attachments.samples import PNG
from ..core.orchestrator.conftest import MEDIUM, Env
from ..core.orchestrator.test_search import FINAL, member_script, with_search
from ..core.orchestrator.test_tools import ANSWER
from .test_e2e import anonymous_text, ask, leaks, wait_done

NOTE_HTML = "<h1>标题</h1><script>window.__pwn = 1</script>"


def script_answer(env: Env, first: str) -> None:
    """作答步骤的成员第一次输出 first（申请工具），收到工具结果后给出正常答案。"""
    original = env.reply

    def reply(model, messages):
        users = [m for m in messages if m.role == "user"]
        if ANSWER in messages[0].content and "<tool_result" not in users[-1].content:
            return first
        return original(model, messages)

    env.fake._default = reply


def upload(page, name: str, data: bytes, mime: str = "application/octet-stream") -> None:
    page.set_input_files("#file", files=[{"name": name, "mimeType": mime, "buffer": data}])


def test_upload_text_and_image_reach_members(serve, page, tmp_path):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.wait_for_selector("#attach")
    upload(page, "讲义.txt", "第一章 函数的极值".encode(), "text/plain")
    page.wait_for_selector("#attachments .chip[data-att]:has-text('讲义.txt')")
    upload(page, "图.png", PNG, "image/png")
    page.wait_for_selector("#attachments .chip[data-att]:has-text('图.png')")
    assert page.locator("#attachments .chip").count() == 2

    # 移除一个后只剩一个
    page.click("#attachments .chip:has-text('图.png') .x")
    assert page.locator("#attachments .chip").count() == 1
    upload(page, "图.png", PNG, "image/png")
    page.wait_for_selector("#attachments .chip[data-att]:has-text('图.png')")

    page.click("#tier button[data-v='custom']")  # b2 带 vision 标签，当组员；f1 当统筹
    for m in ("b1", "b2", "b3", "f1"):
        page.check(f"#picks input[value='{m}']")
    page.select_option("#coordinator", "f1")
    ask(page, MEDIUM)
    wait_done(page)
    assert page.locator("#attachments .chip").count() == 0  # 提交后清空
    calls = srv.env.fake.calls
    answers = [c for c in calls if ANSWER in c.messages[0].content]
    assert answers
    text = lambda c: " ".join(m.content for m in c.messages)  # noqa: E731
    with_text = [c for c in answers if "第一章 函数的极值" in text(c)]
    assert len(with_text) == len(answers)  # 每位成员都收到文档文字
    by_model = {c.model: bool(any(m.media for m in c.messages)) for c in answers}
    vision = {m.id for m in srv.env.config.models.models if "vision" in m.tags}
    assert any(by_model.values()) and all(v == (k in vision) for k, v in by_model.items())


def test_upload_error_is_shown_and_blocks_nothing(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    upload(page, "旧.doc", b"\xd0\xcf\x11\xe0 not really", "application/msword")
    page.wait_for_selector("#attachments .chip.bad")
    assert ".doc" in page.inner_text("#attachments") or "doc" in page.inner_text("#attachments")
    ask(page, MEDIUM)  # 失败的附件不会随题目提交
    wait_done(page)


def test_drag_and_drop_upload(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.wait_for_selector("#composer")
    page.evaluate(
        """() => {
          const dt = new DataTransfer();
          dt.items.add(new File(['拖进来的文字'], '拖放.txt', {type: 'text/plain'}));
          const c = document.querySelector('#composer');
          const init = {dataTransfer: dt, bubbles: true, cancelable: true};
          c.dispatchEvent(new DragEvent('dragover', init));
          c.dispatchEvent(new DragEvent('drop', init));
        }"""
    )
    page.wait_for_selector("#attachments .chip[data-att]:has-text('拖放.txt')")


def test_estimate_before_submit_and_over_threshold(serve, page):
    srv = serve(confirm_threshold_usd=0.0005)
    page.goto(srv.url)
    page.wait_for_selector("#tier button[aria-pressed='true']")
    assert page.is_hidden("#estimate")
    page.fill("#ask", MEDIUM)
    page.wait_for_selector("#estimate table")
    est = page.inner_text("#estimate")
    for label in ("便宜档全员", "旗舰档全员"):
        assert label in est
    assert page.locator("#estimate tr.sel").count() == 1
    assert page.locator("#estimate tr.over-row").count() >= 1  # 超过门槛的标红
    assert "超过门槛" in est
    assert "b1" in est  # 匿名关闭：显示上桌名单

    page.check("#anonymous")
    page.wait_for_function("!document.querySelector('#estimate').innerText.includes('b1')")
    assert "匿名已开启" in page.inner_text("#estimate")

    # 切到协同模式后重新预估
    before = page.inner_text("#estimate")
    page.click("#workflow button[data-v='collab']")
    page.wait_for_function("(t) => document.querySelector('#estimate').innerText !== t", arg=before)

    # 提交后超过门槛，仍弹确认卡片
    page.click("#submit")
    page.wait_for_selector(".card[data-kind='cost'] .opt")


def test_estimate_seed_matches_actual_lineup(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.fill("#ask", MEDIUM)
    page.wait_for_selector("#estimate table")
    listed = re.search(r"上桌：([^\n]+)", page.inner_text("#estimate")).group(1)
    page.click("#submit")
    wait_done(page)
    stage = page.inner_text("#stage")
    for model in re.split(r"[、；]", listed.split("缺席")[0]):
        if model.strip():
            assert model.strip() in stage


def test_tool_calls_files_and_preview(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    script_answer(
        srv.env,
        '记下来。\n<tool_call name="write_file" path="notes.md">要点：先求导。</tool_call>\n'
        f'<tool_call name="write_file" path="page.html">{NOTE_HTML}</tool_call>',
    )
    page.goto(srv.url)
    ask(page, MEDIUM)
    wait_done(page)

    tool = page.locator("details.tool[data-tool='write_file']")
    assert tool.count() == 4  # 2 位组员 × 2 个文件
    assert page.locator(".fchip:has-text('notes.md')").count() == 2
    page.locator(".fchip:has-text('notes.md') button[data-preview]").first.click()
    page.wait_for_selector("#viewer[open] pre")
    assert "要点：先求导。" in page.inner_text("#viewer")
    page.click("#viewer [data-close]")
    assert not page.locator("#viewer[open]").count()

    # HTML 只显示源代码，不在页面内渲染、不执行
    page.locator(".fchip:has-text('page.html') button[data-preview]").first.click()
    page.wait_for_selector("#viewer[open] pre")
    assert "<h1>标题</h1>" in page.inner_text("#viewer pre")
    assert page.locator("#viewer h1").count() == 0
    assert page.evaluate("window.__pwn") is None
    page.click("#viewer [data-close]")

    # 下载链接始终是附件下载
    href = page.locator(".fchip:has-text('notes.md') a").first.get_attribute("href")
    resp = page.request.get(srv.url + href)
    assert resp.headers["content-disposition"].startswith("attachment")
    assert resp.headers["x-content-type-options"] == "nosniff"

    # 工具面板：调用列表、文件、花费
    page.click('#tabs button[data-t="tools"]')
    body = page.inner_text("#pbody")
    assert "写文件" in body and "notes.md" in body and "工具花费" in body
    page.locator("#pbody .fchip button[data-preview]").first.click()
    page.wait_for_selector("#viewer[open]")
    page.keyboard.press("Escape")


def test_tool_output_and_files_hide_identity_until_reveal(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    script_answer(
        srv.env,
        '<tool_call name="write_file" path="who.md">我是 b1，来自 V1。</tool_call>',
    )
    page.goto(srv.url)
    page.check("#anonymous")
    ask(page, MEDIUM)
    wait_done(page)
    parts = [anonymous_text(page)]
    for tab in ("tools", "split"):
        page.click(f'#tabs button[data-t="{tab}"]')
        parts.append(page.inner_text("#pbody"))
    page.locator(".fchip:has-text('who.md') button[data-preview]").first.click()
    page.wait_for_selector("#viewer[open] pre")
    parts.append(page.inner_text("#viewer"))
    assert leaks(srv.identity_terms(), "\n".join(parts)) == []


def test_search_sources_become_links(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    with_search(srv.env)
    member_script(
        srv.env,
        lambda model, done, messages: (
            '<tool_call name="search">函数 极值</tool_call>'
            if done == 0
            else f"{FINAL} 依据见 [S1]，另有 [S9]。"
        ),
    )
    page.goto(srv.url)
    ask(page, MEDIUM)
    for _ in range(5):  # 搜索费用超出预估时会询问，选「继续」
        go = ".card:not(.resolved) .opt[data-opt='continue']:not([disabled])"
        page.wait_for_selector(f".sys.ok, {go}", timeout=20000)
        if page.locator(".sys.ok").count():
            break
        page.click(go)
    page.wait_for_selector(".msg a.cite")
    link = page.locator(".msg a.cite").first
    assert link.inner_text() == "[S1]"
    assert link.get_attribute("href").startswith("https://example.test/")
    assert link.get_attribute("target") == "_blank"
    assert "noopener" in link.get_attribute("rel")
    cited = page.locator(".msg:has(a.cite) .bubble").first.inner_text()
    assert "[S9]" in cited  # 没检索到的编号不加链接
    assert page.locator("details.tool[data-tool='search']").count() >= 3


def test_collab_split_panel(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click('#tabs button[data-t="split"]')
    assert "没有分工" in page.inner_text("#pbody") or "提交题目后" in page.inner_text("#pbody")
    page.click("#workflow button[data-v='collab']")
    ask(page, MEDIUM)
    wait_done(page)
    page.click('#tabs button[data-t="split"]')
    page.wait_for_selector("#pbody table")
    body = page.inner_text("#pbody")
    assert "子任务与负责人" in body and "T1" in body and "T2" in body
    assert "已完成" in body and "自荐表态" in body
    assert "组员" in body
    rows = page.locator("#pbody table").first.locator("tbody tr").count()
    assert rows == 2


def test_discussion_split_panel_explains(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    ask(page, MEDIUM)
    wait_done(page)
    page.click('#tabs button[data-t="split"]')
    assert "讨论模式" in page.inner_text("#pbody")


def test_twelve_seats_do_not_overlap(serve, browser):
    pool = [(f"m{i}", f"V{i}", "budget", ["math"], 0.1, 0.4) for i in range(13)]
    srv = serve(pool=pool, confirm_threshold_usd=100.0)
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    page.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    page.route("https://fonts.gstatic.com/**", lambda r: r.abort())
    try:
        page.goto(srv.url)
        ask(page, MEDIUM)
        wait_done(page)
        assert page.locator("#stage.dense").count() == 1
        seats = page.locator("#stage .seat")
        assert seats.count() == 12 + 1 + 1  # 12 位组员 + 统筹 + 你
        boxes = [seats.nth(i).bounding_box() for i in range(seats.count())]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                apart = (
                    a["x"] + a["width"] <= b["x"] + 1
                    or b["x"] + b["width"] <= a["x"] + 1
                    or a["y"] + a["height"] <= b["y"] + 1
                    or b["y"] + b["height"] <= a["y"] + 1
                )
                assert apart, (a, b)
    finally:
        ctx.close()


def test_estimate_calls_no_model(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    upload(page, "a.txt", b"hello", "text/plain")
    page.wait_for_selector("#attachments .chip[data-att]")
    page.fill("#ask", MEDIUM)
    page.wait_for_selector("#estimate table")
    assert not [c for c in srv.env.fake.calls if ANSWER in c.messages[0].content]
