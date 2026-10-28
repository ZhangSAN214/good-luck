"""邮件栏接口：默认关；打开后登录、读列表、看正文、处理提醒、退出（用假的邮箱来源）。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.mail.test_box import FakeSource, item
from ..core.orchestrator.conftest import Env


def client():
    env = Env()
    src = FakeSource()
    env.rt.mail_source = src
    service = RoundtableService(env.rt)
    service.mail.spawn = lambda fn: fn()
    service.mail.now = lambda: datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    return TestClient(create_app(service)), src, service


def test_mail_flow():
    c, src, service = client()
    with c:
        data = c.get("/api/mail").json()
        assert (data["enabled"], data["status"], data["available"]) == (False, "off", True)
        assert c.post("/api/mail/login").status_code == 400  # 还没打开

        assert c.put("/api/mail/settings", json={"enabled": True}).json()["status"] == "never"
        src.items = [replace(item(1, subject="Exam timetable"), id="AAMk/1+="), item(2)]
        src.bodies = {"AAMk/1+=": "Exam on 2026-11-10 09:00 in the hall."}
        data = c.post("/api/mail/login").json()
        assert data["status"] == "connected" and data["counts"]["important"] == 2
        [rem] = data["reminders"]
        assert rem["due"] == "2026-11-10T09:00" and rem["subject"] == "Exam timetable"

        detail = c.get(f"/api/mail/messages/{quote('AAMk/1+=', safe='')}").json()
        assert detail["body"].startswith("Exam on") and detail["reasons"]
        assert c.get("/api/mail/messages/nope").status_code == 404

        r = c.post(f"/api/mail/reminders/{rem['id']}", json={"action": "accept"})
        assert r.json()["reminders"][0]["status"] == "accepted"
        assert c.post(f"/api/mail/reminders/{rem['id']}", json={"action": "x"}).status_code == 422
        assert c.post("/api/mail/reminders/999", json={"action": "accept"}).status_code == 404

        assert c.post("/api/mail/refresh", json={"manual": False}).json()["started"] is False
        service.mail.now = lambda: datetime(2026, 10, 27, 12, 0, tzinfo=UTC) + timedelta(minutes=1)
        assert c.post("/api/mail/refresh", json={}).json()["started"] is True

        bad = c.put("/api/mail/settings", json={"days": 999})
        assert bad.status_code == 400 and "保留天数" in bad.json()["detail"]
        assert c.put("/api/mail/settings", json={"color": 1}).status_code == 422

        service.mail.busy = "refresh"
        assert c.post("/api/mail/logout").status_code == 409
        service.mail.busy = None
        data = c.post("/api/mail/logout").json()
        assert data["status"] == "never" and data["messages"] == [] and src.cleared == 1


def test_mail_unavailable_without_data_dir():
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        assert c.get("/api/mail").json()["available"] is False
        c.put("/api/mail/settings", json={"enabled": True})
        r = c.post("/api/mail/login")
        assert r.status_code == 400 and "用不了" in r.json()["detail"]


def test_mail_marks_categories_images_and_ai():
    c, src, service = client()
    with c:
        c.put("/api/mail/settings", json={"enabled": True})
        src.items = [replace(item(1, subject="Exam timetable"), id="AAMk/1+="), item(2)]
        src.bodies = {"AAMk/1+=": "Exam on 2026-11-10 09:00 in the hall."}
        src.html = {
            "AAMk/1+=": ('<p onclick="x()">Exam</p><img src="https://t.example/a.png">', {})
        }
        data = c.post("/api/mail/login").json()
        assert data["ai"]["ok"] is False and data["ai"]["monthly_usd"] == 0.30
        assert {x["key"] for x in data["categories"]} >= {"exam", "other"}
        assert data["digest"]["important_unread"] == 2

        r = c.post("/api/mail/mark", json={"id": "m2", "mark": "normal"})
        assert [m["important"] for m in r.json()["messages"]] == [True, False]
        assert c.post("/api/mail/mark", json={"id": "m2", "mark": "x"}).status_code == 422
        assert c.post("/api/mail/mark", json={"id": "nope", "mark": None}).status_code == 404
        r = c.post("/api/mail/category", json={"id": "AAMk/1+=", "category": "event"})
        assert r.json()["messages"][0]["category"] == "event"
        assert c.post("/api/mail/category", json={"id": "m2", "category": "x"}).status_code == 400

        got = c.post("/api/mail/images", json={"id": "AAMk/1+="}).json()
        assert got["html"]["remote"] == 1 and "onclick" not in got["html"]["html"]

        no = c.post("/api/mail/ai/summary", json={"id": "AAMk/1+="})
        assert no.status_code == 409 and "同意" in no.json()["detail"]
        assert c.put("/api/mail/settings", json={"ai_ok": True}).json()["ai"]["ok"] is True
        got = c.post("/api/mail/ai/summary", json={"id": "AAMk/1+="}).json()
        assert got["summary"] and got["ai"]["today_calls"] == 1
        got = c.post("/api/mail/ai/translate", json={"id": "AAMk/1+="}).json()
        assert got["translation"] and got["ai"]["today_calls"] == 2
        assert c.post("/api/mail/ai/digest", json={}).json()["ai"]["digest"]["text"]
        assert c.post("/api/mail/ai/explode", json={}).status_code == 422
        assert c.post("/api/mail/ai/summary", json={}).status_code == 400

        assert c.post("/api/mail/logout").json()["ai"]["ok"] is False
