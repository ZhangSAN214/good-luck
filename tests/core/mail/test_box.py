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

    def html(self, ids: list[str]) -> dict[str, tuple[str, dict[str, str]]]:
        return {i: self.src.html[i] for i in ids if i in self.src.html}


class FakeSource:
    def __init__(self) -> None:
        self.items: list[MailItem] = []
        self.bodies: dict[str, str] = {}
        self.html: dict[str, tuple[str, dict[str, str]]] = {}
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


def test_marks_and_categories_are_local_and_reversible(env):
    box, src, _, _ = env
    box.set_settings(enabled=True)
    src.items = [
        item(1, subject="Exam timetable"),
        item(2, addr="mate@student.manchester.ac.uk", subject="Lunch?"),
    ]
    box.login()
    view = box.view()
    assert [m["category"] for m in view["messages"]] == ["exam", "personal"]
    counts = {c["key"]: c["count"] for c in view["categories"]}
    assert counts["exam"] == 1 and counts["personal"] == 1 and counts["other"] == 0
    assert box.set_mark("m2", "important") and box.set_category("m1", "event")
    view = box.view()
    m1, m2 = view["messages"]
    assert m2["important"] and m2["reasons"] == ["你标为重要"]
    assert (m1["category"], m1["category_set"]) == ("event", True)
    assert box.set_mark("m1", "normal")
    assert not box.view()["messages"][0]["important"]
    # 刷新后手动标记和分类仍在
    src.items = [item(1, subject="Exam timetable", is_read=True)]
    box.refresh(manual=False) or box._fetch(interactive=False)
    assert box.message("m1")["mark"] == "normal" and box.message("m1")["category"] == "event"
    assert box.set_mark("m1", None) and box.set_category("m1", None)
    assert box.message("m1")["category"] == "exam" and box.message("m1")["important"]
    with pytest.raises(ValueError):
        box.set_mark("m1", "urgent")
    with pytest.raises(ValueError):
        box.set_category("m1", "nope")
    assert box.set_mark("nope", "important") is False


def test_local_digest_counts_today_important_and_due(env):
    box, src, clock, _ = env
    box.set_settings(enabled=True)
    src.items = [
        item(1, days_ago=0.1, subject="Coursework deadline"),
        item(2, days_ago=2, subject="Exam", is_read=True),
        item(3, days_ago=0.2, addr="mate@gmail.example", subject="Hi"),
    ]
    src.bodies = {"m1": "Submit by 2026-11-05 12:00."}
    box.login()
    digest = box.view()["digest"]
    assert digest["today"] == 2 and digest["important_unread"] == 1
    assert [t["id"] for t in digest["top"]] == ["m1"]
    assert digest["due"] == [
        {"due": "2026-11-05T12:00", "snippet": digest["due"][0]["snippet"], "message_id": "m1"}
    ]


def test_load_images_stores_cleaned_html(env):
    box, src, _, repo = env
    box.set_settings(enabled=True)
    src.items = [item(1)]
    box.login()
    src.html = {
        "m1": (
            '<p onclick="x()">Hi</p><script>bad()</script><img src="cid:a@b">'
            '<img src="https://t.example/p.gif">',
            {"a@b": "data:image/png;base64,AAAA"},
        )
    }
    box.load_images("m1")
    got = box.message("m1")["html"]
    assert got["embedded"] == 1 and got["remote"] == 1
    assert "script" not in got["html"] and "onclick" not in got["html"]
    assert 'src="data:image/png;base64,AAAA"' in got["html"]
    assert any(a["action"] == "images" and a["ok"] for a in repo.mail_audit())
    assert box.view()["messages"][0]["has_html"]
    with pytest.raises(ValueError, match="没有这封"):
        box.load_images("nope")


def test_load_images_failures_and_preconditions(env):
    box, src, _, repo = env
    box.set_settings(enabled=True)
    src.items = [item(1)]
    with pytest.raises(ValueError):
        box.load_images("m1")  # 还没登录、也没这封
    box.login()
    box.load_images("m1")  # 邮箱没给图文版
    assert "图文版没取到" in box.view()["message"] and box.message("m1")["html"] is None
    src.fail = SessionExpired("登录状态过期了")
    box.load_images("m1")
    assert box.view()["status"] == "expired"
    with pytest.raises(ValueError, match="还没登录"):
        box.load_images("m1")
