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


def test_people_dialog_persona_and_memories(serve, page):
    from roundtable.core.persona import persona_of

    srv = serve(confirm_threshold_usd=100.0)
    rt = srv.env.rt
    rt.repo.add_memory("V2", "day", "和用户聊了爬山", period="2026-10-21")
    open_chat(srv, page)
    page.click(".gc-room[data-room='dm:V2']")
    page.wait_for_function("document.querySelector('#gc-title').textContent === '与 阿二 私聊'")
    page.click("#gc-people")  # 私聊里打开：直接是对方
    page.wait_for_selector("#people[open] .pp-who.cur[data-member='V2']")
    assert "和用户聊了爬山" in page.inner_text("#people .pp-list")
    page.fill("#people input[name=personality]", "爱较真")
    page.fill("#people input[name=interests]", "天文，数学")
    page.click("#people #pp-persona button[type=submit]")
    page.wait_for_selector("#people .pp-msg:has-text('人设已保存')")
    p = persona_of(rt, "V2")
    assert p.personality == "爱较真" and p.interests == ["天文", "数学"]
    # 记一条、改、删
    page.fill("#people #pp-add input", "我下周三考试")
    page.click("#people #pp-add button")
    page.wait_for_selector("#people .pp-list li:has-text('我下周三考试')")
    page.locator("#people .pp-list li:has-text('我下周三考试') [data-edit]").click()
    page.fill("#people .pp-editf textarea", "我下周四考试")
    page.click("#people .pp-editf button")
    page.wait_for_selector("#people .pp-list li:has-text('我下周四考试')")
    page.locator("#people .pp-list li:has-text('我下周四考试') [data-del]").click()
    page.wait_for_selector("#people .pp-list li:has-text('我下周四考试')", state="detached")
    # 两步清空
    page.click("#pp-clear-one")
    assert "再点一次" in page.inner_text("#pp-clear-one")
    assert rt.repo.memories("V2")
    page.click("#pp-clear-one")
    page.wait_for_selector("#people .pp-msg:has-text('已清空')")
    assert rt.repo.memories("V2") == []
    page.click("#people [data-close]")
    assert not page.locator("#people").evaluate("d => d.open")


def test_pair_room_is_view_only_with_away_tag(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    repo = srv.env.rt.repo
    repo.ensure_chat_room(
        "pair:V1+V2", kind="pair", title="阿一 和 阿二", members=["V1", "V2"], with_user=False
    )
    repo.add_chat_message("pair:V1+V2", "V1", "晚上打游戏吗", meta={"life": True, "offline": True})
    open_chat(srv, page)
    page.wait_for_selector(".gc-room[data-room='pair:V1+V2']")
    assert "成员之间" in page.inner_text("#gc-rooms")
    page.click(".gc-room[data-room='pair:V1+V2']")
    page.wait_for_selector(".gc-msg[data-author='V1'] .gc-tag.away")
    assert page.is_hidden("#gc-compose") and page.is_visible("#gc-readonly")
    page.click(".gc-room[data-room='main']")
    page.wait_for_selector("#gc-compose", state="visible")


def test_shared_link_card_and_feeds_dialog(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    share = {
        "title": "新望远镜",
        "url": "https://news.test/a",
        "feed": "星空新闻",
        "published": "2026-10-22T08:00:00.000+00:00",
        "unverified": True,
    }
    srv.env.rt.repo.add_chat_message(
        "main", "V1", "看到一条消息", meta={"life": True, "share": share}
    )
    bad = {**share, "url": "javascript:alert(1)", "title": "坏链接"}
    srv.env.rt.repo.add_chat_message("main", "V2", "另一条", meta={"life": True, "share": bad})
    open_chat(srv, page)
    page.wait_for_selector(".gc-msg[data-author='V1'] a.gc-src")
    link = page.locator(".gc-msg[data-author='V1'] a.gc-src")
    assert link.get_attribute("href") == "https://news.test/a"
    assert "noopener" in link.get_attribute("rel") and link.get_attribute("target") == "_blank"
    assert "未核实" in page.inner_text(".gc-msg[data-author='V1'] .gc-srcm")
    assert page.locator(".gc-msg[data-author='V2'] a.gc-src").count() == 0  # 只接受 http(s)
    page.click("#gc-feeds")
    page.wait_for_selector("#feeds .fd-list li")
    page.fill("#fd-add input[name=name]", "我的源")
    page.fill("#fd-add input[name=url]", "https://mine.test/rss")
    page.fill("#fd-add input[name=topics]", "美食，旅行")
    page.click("#fd-add button[type=submit]")
    page.wait_for_selector("#feeds .fd-list li:has-text('我的源')")
    assert "你改过的清单" in page.inner_text("#feeds")
    page.click("#feeds [data-reset]")
    page.wait_for_selector("#feeds .pp-msg:has-text('已恢复默认清单')")  # 等保存完再看列表
    assert "我的源" not in page.inner_text("#feeds")


def test_moments_dialog_post_like_comment(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    srv.env.rt.repo.add_moment("V2", "今天在健身房", offline=True)
    open_chat(srv, page)
    page.wait_for_selector("#gc-moments .gc-unread")
    page.click("#gc-moments")
    page.wait_for_selector("#moments .mo")
    assert "你不在时" in page.inner_text("#moments .mo")
    page.click("#moments [data-like]")
    page.wait_for_selector("#moments .mo-likes:has-text('我')")
    page.click("#moments .mo-act [data-reply]")
    page.fill("#moments .mo-cf input", "加油")
    page.click("#moments .mo-cf button")
    page.wait_for_selector("#moments .mo-comments li:has-text('阿二')")  # 作者回了一句
    page.fill("#mo-new textarea", "周末去爬山")
    page.click("#mo-new button")
    page.wait_for_selector("#moments .mo:has-text('周末去爬山') .mo-comments li")  # 成员来评论
    page.click("#moments [data-author='V2']")
    page.wait_for_selector("#moments .vh:has-text('阿二')")
    assert "周末去爬山" not in page.inner_text("#moments .mo-wrap")
    page.click("#moments [data-close]")
    assert page.locator("#gc-moments .gc-unread").count() == 0
