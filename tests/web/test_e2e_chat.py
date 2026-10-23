"""前端：群聊。Enter 发送、@ 候选用键盘选、成员回应、搜索、两步清空；从公寓楼 @ 住户。"""

from __future__ import annotations


def open_chat(srv, page):
    page.goto(srv.url + "/#m=chat")
    page.wait_for_selector("#gc-members button[data-name]")


def test_send_with_mention_and_get_a_reply(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    open_chat(srv, page)
    assert "还没有消息" in page.inner_text("#gc-feed")
    page.click("#chat-input")
    page.keyboard.type("@阿")
    page.wait_for_selector("#gc-suggest:not([hidden])")
    page.keyboard.press("ArrowDown")  # 第二位
    page.keyboard.press("Enter")  # 选中候选，不发送
    assert page.input_value("#chat-input") == "@阿二 "
    page.keyboard.type("在吗")
    page.keyboard.press("Shift+Enter")  # 换行，不发送
    page.keyboard.type("周末一起看书？")
    page.keyboard.press("Enter")
    page.wait_for_selector(".gc-msg[data-author='V2']")
    assert page.input_value("#chat-input") == ""
    mine = page.inner_text(".gc-msg.me")
    assert "在吗" in mine and "周末一起看书？" in mine
    assert "我们聊聊" in page.inner_text(".gc-msg[data-author='V2']")
    # 只有被 @ 的人回应
    assert page.locator(".gc-msg:not(.me)").count() == 1


def test_search_and_two_step_clear(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    repo = srv.env.rt.repo
    repo.add_chat_message("main", "user", "周末去爬山")
    repo.add_chat_message("main", "user", "今天下雨")
    open_chat(srv, page)
    page.wait_for_selector(".gc-msg.me")
    page.fill("#gc-q", "爬山")
    page.keyboard.press("Enter")
    page.wait_for_selector("#gc-exit-search")
    assert page.locator(".gc-msg").count() == 1
    page.press("#gc-q", "Escape")
    page.wait_for_selector("#gc-exit-search", state="detached")
    assert page.locator(".gc-msg").count() == 2
    page.click("#gc-clear")
    assert "再点一次" in page.inner_text("#gc-clear")
    assert page.locator(".gc-msg").count() == 2  # 第一次只是提醒
    page.click("#gc-clear")
    page.wait_for_selector(".gc-msg", state="detached")
    assert repo.chat_messages("main") == []


def test_apartment_resident_can_be_mentioned_in_chat(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url + "/#m=home")
    # 夜里住户睡在床上（SVG），点被子；白天点人
    page.locator("#home .tenant:not(.sleeper), #home .sleeper rect").first.click()
    page.click("#resident-card [data-chat]")
    page.wait_for_selector("#gchat:not([hidden])")
    assert page.input_value("#chat-input").startswith("@")
    page.keyboard.press("Alt+Digit3")  # 已在群聊
    assert page.url.endswith("#m=chat")


def test_rooms_dm_and_unread_badge(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    repo = srv.env.rt.repo
    open_chat(srv, page)
    page.wait_for_selector(".gc-room[data-room='work']")
    assert page.locator(".gc-room[data-room='main']").get_attribute("aria-current") == "true"
    # 工作群里有一条别人的消息：显示未读数
    repo.add_chat_message("work", "V1", "报告写完了")
    page.wait_for_selector(".gc-room[data-room='work'] .gc-unread", timeout=15000)
    # 打开和阿二的私聊：没有成员栏，发消息只有对方回
    page.click(".gc-room[data-room='dm:V2']")
    page.wait_for_function("document.querySelector('#gc-title').textContent === '与 阿二 私聊'")
    assert page.locator("#gc-aside").is_hidden()
    page.fill("#chat-input", "大家好")
    page.keyboard.press("Enter")
    page.wait_for_selector(".gc-msg[data-author='V2']")
    assert page.locator(".gc-msg:not(.me)").count() == 1
    assert [r["author"] for r in repo.chat_messages("dm:V2")] == ["user", "V2"]
    assert repo.chat_messages("main") == []
    # 打开工作群后未读清零；刷新后回到上次的会话
    page.click(".gc-room[data-room='work']")
    page.wait_for_selector(".gc-msg[data-author='V1']")
    page.wait_for_selector(".gc-room[data-room='work'] .gc-unread", state="detached")
    page.reload()
    page.wait_for_function("document.querySelector('#gc-title').textContent === '工作群'")
    assert "本月聊天 $" in page.inner_text("#gc-budget")


def test_apartment_resident_dm(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url + "/#m=home")
    page.locator("#home .tenant:not(.sleeper), #home .sleeper rect").first.click()
    page.click("#resident-card [data-dm]")
    page.wait_for_selector("#gchat:not([hidden])")
    page.wait_for_function("document.querySelector('#gc-title').textContent.startsWith('与 ')")


def test_work_group_handoff_and_open_in_office(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    open_chat(srv, page)
    page.click(".gc-room[data-room='work']")
    page.wait_for_selector("#gc-handoff:not([hidden])")
    page.fill("#chat-input", "求函数 f(x)=x^3-3x 在区间 [-2, 2] 上的最大值与最小值")
    page.click("#gc-handoff")
    page.wait_for_selector(".gc-msg.me .gc-tag")
    page.wait_for_function(
        "document.querySelector('#gc-feed').textContent.includes('交付了')", timeout=20000
    )
    assert "派给了" in page.inner_text("#gc-feed")
    page.locator(".gc-sys [data-session]").last.click()
    page.wait_for_function("location.hash.includes('s=')")
    assert page.locator("#gchat").is_hidden()
    # 私聊和生活群里没有这个按钮
    page.keyboard.press("Alt+Digit3")  # 回到聊天
    page.click(".gc-room[data-room='main']")
    page.wait_for_function("document.querySelector('#gc-title').textContent === '大家庭'")
    assert page.locator("#gc-handoff").is_hidden()
