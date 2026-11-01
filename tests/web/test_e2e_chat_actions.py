"""前端：微信式动作——右键引用、撤回、双击头像拍一拍、成员拆条和表情包。"""

from __future__ import annotations

from roundtable.core.chat import GroupChat
from tests.web.conftest import wait_until


def open_chat(srv, page):
    page.goto(srv.url + "/#m=chat")
    page.wait_for_selector("#gc-members button[data-name]")


def test_quote_then_recall_own_message(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    repo = srv.env.rt.repo
    first = repo.add_chat_message("main", "V1", "周末去爬山吗")
    open_chat(srv, page)
    page.click(f".gc-msg[data-id='{first['id']}'] .gc-bub", button="right")
    page.click("#gc-menu [data-act='quote']")
    assert "周末去爬山吗" in page.inner_text("#gc-quotebar")
    page.fill("#chat-input", "去！")
    page.keyboard.press("Enter")
    page.wait_for_selector(".gc-msg.me .gc-quote")
    mine = [m for m in repo.chat_messages("main") if m["author"] == "user"][0]
    assert mine["meta"]["quote"]["id"] == first["id"]
    assert page.is_hidden("#gc-quotebar")
    # 撤回自己的消息
    page.click(f".gc-msg[data-id='{mine['id']}'] .gc-bub", button="right")
    page.click("#gc-menu [data-act='recall']")
    page.wait_for_selector(".gc-sys.recalled")
    assert "你撤回了一条消息" in page.inner_text("#gc-feed")
    assert repo.chat_message(mine["id"])["text"] == ""
    # 别人的消息没有「撤回」
    page.click(f".gc-msg[data-id='{first['id']}'] .gc-bub", button="right")
    assert page.locator("#gc-menu [data-act='recall']").count() == 0


def test_pat_and_split_reply_with_sticker(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    srv.env.rt.repo.add_chat_message("main", "V2", "在呢")
    srv.env.fake.queue("b2", "干嘛拍我\n\n[表情:小黄脸·思考]")
    open_chat(srv, page)
    page.dblclick(".gc-msg[data-author='V2'] .gc-av")
    page.wait_for_selector(".gc-sys.pat")
    assert "你拍了拍 阿二" in page.inner_text("#gc-feed")
    wait_until(lambda: page.locator(".gc-msg[data-author='V2']").count() == 3, timeout=10)
    assert page.locator(".gc-msg[data-author='V2'] .gc-bub.stk-only").count() == 1


def test_card_shows_and_message_can_be_forwarded(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    repo = srv.env.rt.repo
    card = {"kind": "video", "title": "土星环延时", "url": "https://www.youtube.com/watch?v=1"}
    src = GroupChat(srv.env.rt, "main").send_as("V1", "这个好看", card=card)
    open_chat(srv, page)
    page.wait_for_selector(f".gc-msg[data-id='{src['id']}'] a.cm-card.video")
    assert "土星环延时" in page.inner_text(f".gc-msg[data-id='{src['id']}'] .cm-card")
    page.click(f".gc-msg[data-id='{src['id']}'] .gc-words", button="right")
    page.click("#gc-menu [data-act='forward']")
    page.click("#gc-menu [data-act='to:main']")
    wait_until(lambda: any(m["author"] == "user" for m in repo.chat_messages("main")), timeout=10)
    fwd = [m for m in repo.chat_messages("main") if m["author"] == "user"][0]
    assert fwd["meta"]["card"]["title"] == "土星环延时" and fwd["meta"]["forwarded"]
    page.wait_for_selector(".gc-msg.me .gc-fwd")
