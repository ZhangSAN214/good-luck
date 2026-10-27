"""前端：终端首页的邮件栏（学校邮箱，只读）。用假的邮箱来源，不开真的登录窗口。

打开邮件栏 → 登录 → 重要邮件置顶、未读重要数 → 看正文 → 确认截止日期 → 改规则 → 退出登录。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.mail import MailItem

from ..core.mail.test_box import FakeSource

LAND_HUB = "try { localStorage.setItem('roundtable.landing', 'hub') } catch {}"


def source() -> FakeSource:
    now = datetime.now(UTC)
    src = FakeSource()
    src.items = [
        MailItem(
            id="AAMk/1+=",
            received_at=(now - timedelta(hours=2)).isoformat(timespec="seconds"),
            sender_name="Exams Office",
            sender_addr="exams@manchester.ac.uk",
            subject="Exam timetable published",
            preview="Your January exams",
        ),
        MailItem(
            id="m2",
            received_at=(now - timedelta(days=1)).isoformat(timespec="seconds"),
            sender_name="Friend",
            sender_addr="friend@mail.example",
            subject="Lunch on Friday?",
            preview="pizza",
            is_read=True,
        ),
    ]
    due = (now + timedelta(days=10)).date().isoformat()
    src.bodies = {"AAMk/1+=": f"Hello.\nYour exam is on {due} 09:30 in the main hall."}
    return src


def test_mail_bar_flow(serve, page):
    src = source()
    srv = serve(confirm_threshold_usd=100.0, setup=lambda env: setattr(env.rt, "mail_source", src))
    page.on("dialog", lambda d: d.accept())
    page.add_init_script(LAND_HUB)
    page.goto(srv.url)
    page.wait_for_selector("#hub .ml-btn")
    assert page.is_hidden("#hub .ml-dot")

    page.click("#hub .ml-btn")
    page.wait_for_selector("#hub .ml-panel:not([hidden]) [data-enable]")
    assert "由你本人" in page.inner_text("#hub .ml-panel")
    page.click("#hub [data-enable]")
    page.wait_for_selector("#hub [data-login]")
    assert page.locator("#hub .ml-panel input[type=password]").count() == 0  # 这里从不收密码
    page.click("#hub [data-login]")
    page.wait_for_selector("#hub .ml-row")
    assert src.opened == [True]
    assert page.inner_text("#hub .ml-dot") == "1"
    # 默认只看重要的；全部里两封
    assert page.locator("#hub .ml-row").count() == 1
    assert "学校发件人" in page.inner_text("#hub .ml-row.imp")
    page.click("#hub [data-filter='all']")
    assert page.locator("#hub .ml-row").count() == 2

    # 正文里找到的考试日期：确认后进「即将到来」
    page.click("#hub [data-do='accept']")
    page.wait_for_selector("#hub .ml-rem.accepted")
    assert "09:30" in page.inner_text("#hub .ml-rem.accepted")

    page.click("#hub [data-open='AAMk/1+=']")
    page.wait_for_selector("#hub .ml-body")
    assert "main hall" in page.inner_text("#hub .ml-body")
    page.click("#hub [data-back]")
    page.wait_for_selector("#hub .ml-row")

    # 改规则：朋友也算重要
    page.click("#hub [data-settings-open]")
    page.fill("#hub textarea[name='senders']", "friend@mail.example")
    page.click("#hub .ml-form button[type=submit]")
    page.wait_for_selector("#hub .ml-tabs")
    page.click("#hub [data-filter='important']")
    assert page.locator("#hub .ml-row").count() == 2
    assert page.inner_text("#hub .ml-dot") == "1"  # 朋友那封已读，不算进数字

    # 退出登录：清掉登录状态和缓存
    page.click("#hub [data-settings-open]")
    page.click("#hub [data-logout]")
    page.wait_for_selector("#hub [data-login]")
    assert src.cleared == 1 and page.locator("#hub .ml-row").count() == 0
    assert page.is_hidden("#hub .ml-dot")
