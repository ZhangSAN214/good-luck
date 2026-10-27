"""邮件栏的状态、后台任务、缓存与提醒（用假的邮箱来源，不开浏览器）。"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

import pytest

from roundtable.core.mail import LoginCancelled, MailBox, MailBusy, MailItem, SessionExpired
from roundtable.core.storage import Repository, connect

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def item(i: int, days_ago: float = 1, **kw) -> MailItem:
    when = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return MailItem(
        id=f"m{i}",
        received_at=when,
        sender_name="Tutor",
        sender_addr=kw.pop("addr", "tutor@manchester.ac.uk"),
        subject=kw.pop("subject", f"Hello {i}"),
        preview=kw.pop("preview", "hi"),
        **kw,
    )


class FakeSession:
    def __init__(self, src: FakeSource) -> None:
        self.src = src
        self.blocked = ["邮箱写入操作（UpdateItem）"]

    def items(self, limit: int) -> list[MailItem]:
        return self.src.items[:limit]

    def bodies(self, ids: list[str]) -> dict[str, str]:
        self.src.body_calls.append(list(ids))
        return {i: self.src.bodies[i] for i in ids if i in self.src.bodies}


class FakeSource:
    def __init__(self) -> None:
        self.items: list[MailItem] = []
        self.bodies: dict[str, str] = {}
        self.fail: Exception | None = None
        self.opened: list[bool] = []
        self.body_calls: list[list[str]] = []
        self.cleared = 0

    @contextlib.contextmanager
    def open(self, *, interactive: bool):
        self.opened.append(interactive)
        if self.fail:
            raise self.fail
        yield FakeSession(self)

    def has_login(self) -> bool:
        return True

    def clear(self) -> None:
        self.cleared += 1


class Clock:
    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        return self.t


@pytest.fixture
def env():
    src = FakeSource()
    clock = Clock()
    repo = Repository(connect())
    box = MailBox(repo, src, now=clock, spawn=lambda fn: fn())
    return box, src, clock, repo


def test_off_by_default_and_needs_enabling(env):
    box, src, _, _ = env
    view = box.view()
    assert (view["enabled"], view["status"], view["messages"]) == (False, "off", [])
    with pytest.raises(ValueError, match="还没打开"):
        box.login()
    assert src.opened == []


def test_login_reads_inbox_bodies_and_reminders(env):
    box, src, _, repo = env
    box.set_settings(enabled=True)
    src.items = [
        item(1, subject="Coursework 2 deadline"),
        item(2, addr="friend@gmail.example", subject="Lunch?"),
        item(3, days_ago=30),  # 超出保留天数
    ]
    src.bodies = {"m1": "Please submit by 5 Nov 2026 at 23:59.", "m2": "Due tomorrow lol"}
    box.login()
    assert src.opened == [True]
    view = box.view()
    assert view["status"] == "connected" and "读到最近 2 封" in view["message"]
    assert [m["id"] for m in view["messages"]] == ["m1", "m2"]
    assert view["messages"][0]["important"] and view["messages"][0]["reasons"][0] == "学校发件人"
    assert not view["messages"][1]["important"]
    assert view["counts"] == {"total": 2, "unread": 2, "important": 1}
    # 只有重要邮件找日期
    assert [(r["message_id"], r["due"]) for r in view["reminders"]] == [("m1", "2026-11-05T23:59")]
    assert view["blocked"] == ["邮箱写入操作（UpdateItem）"]
    assert repo.mail_audit()[0]["action"] == "login" and repo.mail_audit()[0]["ok"]
    detail = box.message("m1")
    assert detail["body"].startswith("Please submit") and not detail["loading"]


def test_refresh_is_rate_limited_and_keeps_bodies(env):
    box, src, clock, repo = env
    box.set_settings(enabled=True)
    src.items = [item(1)]
    src.bodies = {"m1": "body"}
    box.login()
    assert box.refresh(manual=False) is False  # 自动刷新至少隔 10 分钟
    clock.t += timedelta(seconds=30)
    src.items = [item(1, is_read=True), item(4)]
    assert box.refresh(manual=True) is True
    assert src.opened == [True, False]
    assert src.body_calls == [["m1"], ["m4"]]  # 已有正文的不再取
    assert repo.mail_message("m1")["is_read"] and repo.mail_message("m1")["body"] == "body"
    assert box.refresh(manual=True) is False


def test_refresh_needs_login_first(env):
    box, _, _, _ = env
    box.set_settings(enabled=True)
    with pytest.raises(ValueError, match="还没登录"):
        box.refresh(manual=True)
    assert box.refresh(manual=False) is False


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (SessionExpired("登录状态过期了"), "expired"),
        (LoginCancelled("登录窗口被关掉了"), "never"),
        (RuntimeError("boom https://x.example/a?token=abc"), "error"),
    ],
)
def test_failures_are_recorded_without_crashing(env, error, status):
    box, src, _, repo = env
    box.set_settings(enabled=True)
    src.fail = error
    box.login()
    view = box.view()
    assert view["status"] == status and view["busy"] is None
    assert "token=abc" not in view["message"]
    assert repo.mail_audit()[0]["ok"] is False


def test_open_message_without_body_fetches_it(env):
    box, src, clock, _ = env
    box.set_settings(enabled=True)
    src.items = [item(1)]
    box.login()  # 来源没给正文
    src.bodies = {"m1": "now here"}
    detail = box.message("m1")
    assert detail["loading"] is True or detail["body"] == "now here"
    assert box.message("m1")["body"] == "now here"
    assert box.message("nope") is None


def test_only_one_job_at_a_time(env):
    box, src, _, _ = env
    box.set_settings(enabled=True)
    box.busy = "login"
    with pytest.raises(MailBusy):
        box.login()
    with pytest.raises(MailBusy):
        box.logout()
    assert box.view()["status"] == "busy" and box.view()["busy_text"]


def test_logout_clears_login_state_and_cache(env):
    box, src, _, repo = env
    box.set_settings(enabled=True)
    src.items = [item(1, subject="exam")]
    src.bodies = {"m1": "exam due 6 Nov 2026"}
    box.login()
    box.logout()
    assert src.cleared == 1
    view = box.view()
    assert view["status"] == "never" and view["messages"] == [] and view["reminders"] == []
    assert repo.mail_audit()[0]["action"] == "logout"


def test_reminders_accept_dismiss_and_hide_past(env):
    box, src, clock, _ = env
    box.set_settings(enabled=True)
    src.items = [item(1, subject="exam")]
    src.bodies = {"m1": "Exam on 2026-11-02. Submit by 2026-11-09."}
    box.login()
    first, second = box.view()["reminders"]
    assert box.set_reminder(first["id"], "accept")
    assert box.set_reminder(second["id"], "dismiss")
    assert [r["status"] for r in box.view()["reminders"]] == ["accepted"]
    with pytest.raises(ValueError):
        box.set_reminder(first["id"], "explode")
    clock.t = datetime(2026, 11, 3, tzinfo=UTC)
    assert box.view()["reminders"] == []


def test_settings_validation_and_rules_change_importance(env):
    box, src, _, _ = env
    box.set_settings(enabled=True)
    src.items = [item(1, addr="friend@gmail.example", subject="Party")]
    box.login()
    assert not box.view()["messages"][0]["important"]
    box.set_settings(rules={"senders": ["friend@gmail.example"]})
    assert box.view()["messages"][0]["reasons"] == ["你加的发件人"]
    with pytest.raises(ValueError, match="规则有误"):
        box.set_settings(rules={"keywords": "x", "nope": 1})
    with pytest.raises(ValueError, match="保留天数"):
        box.set_settings(days=0)
    with pytest.raises(ValueError, match="额外网站"):
        box.set_settings(extra_hosts=["https://a.example/x"])
    box.set_settings(extra_hosts=["*.SSO.example.ac.uk"], days=7, show_window=True)
    view = box.view()
    assert (view["extra_hosts"], view["days"], view["show_window"]) == (
        ["sso.example.ac.uk"],
        7,
        True,
    )


def test_disabled_box_hides_everything(env):
    box, src, _, _ = env
    box.set_settings(enabled=True)
    src.items = [item(1)]
    box.login()
    box.set_settings(enabled=False)
    assert box.view()["messages"] == [] and box.message("m1") is None
