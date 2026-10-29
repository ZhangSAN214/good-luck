"""人物卡导入接口：先预览（对上成员）再导入；随身卡进聊天提示词；锁住的项不被重新导入覆盖。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.persona import chat_persona, set_persona
from roundtable.core.service import RoundtableService

from ..core.member.test_card import CARDS
from ..core.orchestrator.conftest import Env


def test_import_preview_then_import():
    env = Env()
    svc = RoundtableService(env.rt)
    ids = [m.id for m in svc.chats.everyone()]
    with TestClient(create_app(svc)) as c:
        r = c.post("/api/chat/profiles/import", json={"text": CARDS, "dry_run": True}).json()
        assert [x["name"] for x in r["cards"]] == ["甲", "乙"]
        assert all(x["member"] is None and x["version"] is None for x in r["cards"])
        assert {m["id"] for m in r["members"]} == set(ids)
        assert c.get(f"/api/chat/profiles/{ids[0]}").json()["version"] == 0

        mapping = {"甲": ids[0], "乙": ""}
        r = c.post("/api/chat/profiles/import", json={"text": CARDS, "mapping": mapping}).json()
        assert r["imported"] == 1 and r["cards"][0]["version"] == 1
        data = c.get(f"/api/chat/profiles/{ids[0]}").json()
        assert data["data"]["identity"]["小名"] == "阿甲"
        assert data["history"][0]["reason"] == "导入人物卡：测试成员角色卡 v1（2026-10-29）"
        assert data["card"]["text"].startswith("甲，小名阿甲") and not data["card"]["custom"]
        assert data["card"]["chars"] <= data["card"]["budget"] == 400

        # 锁住的项重新导入时不动；用户加的项保留
        c.patch(f"/api/chat/profiles/{ids[0]}", json={"patch": {"identity": {"小名": "小甲甲"}}})
        c.patch(f"/api/chat/profiles/{ids[0]}", json={"patch": {"likes": {"最近": "拼图"}}})
        c.post(f"/api/chat/profiles/{ids[0]}/lock", json={"path": "identity.小名"})
        c.post("/api/chat/profiles/import", json={"text": CARDS, "mapping": mapping})
        data = c.get(f"/api/chat/profiles/{ids[0]}").json()
        assert data["data"]["identity"]["小名"] == "小甲甲"
        assert data["data"]["likes"]["最近"] == "拼图"

        listing = c.get("/api/chat/profiles").json()
        row = next(m for m in listing["members"] if m["id"] == ids[0])
        assert row["version"] >= 1 and row["card_chars"] > 0 and listing["budget"] == 400


def test_import_errors():
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        r = c.post("/api/chat/profiles/import", json={"text": "# 什么都没有"})
        assert r.status_code == 400 and "人物卡" in r.json()["detail"]
        bad = {"text": CARDS, "mapping": {"甲": "nobody"}}
        assert c.post("/api/chat/profiles/import", json=bad).status_code == 400


def test_pocket_card_replaces_simple_persona_in_chat_prompt():
    env = Env()
    svc = RoundtableService(env.rt)
    mid = svc.chats.everyone()[0].id
    set_persona(env.rt, mid, {"personality": "旧人设标记"})
    assert "旧人设标记" in chat_persona(env.rt, mid)
    svc.member_profile_import(CARDS, {"甲": mid, "乙": ""})
    text = chat_persona(env.rt, mid)
    assert "旧人设标记" not in text and text.startswith("甲，小名阿甲")
    assert len(text) <= env.rt.config.roundtable.chat.engine.context.budget_chars
    assert json.dumps(text, ensure_ascii=False)
