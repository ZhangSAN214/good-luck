"""群聊接口：发消息后成员在后台回应；轮询拿到新消息；搜索；一键清空。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env


def wait_for(c, after, n, timeout=5.0, room="main"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = c.get("/api/chat", params={"after": after, "room": room}).json()
        if len(data["messages"]) >= n and not data["typing"]:
            return data
        time.sleep(0.02)
    raise AssertionError("等不到回应")


def test_chat_round_trip():
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        first = c.get("/api/chat").json()
        assert first["messages"] == [] and len(first["members"]) == 6
        r = c.post("/api/chat", json={"text": "@阿一 你好"})
        assert r.status_code == 202 and r.json()["speakers"] == ["阿一"]
        data = wait_for(c, 0, 2)
        user, reply = data["messages"]
        assert (user["author"], user["name"], user["text"]) == ("user", "我", "@阿一 你好")
        assert reply["author"] == "V1" and reply["name"] == "阿一" and "我们聊聊" in reply["text"]
        assert reply["reply_to"] == user["id"]
        # 只拿新消息
        assert c.get("/api/chat", params={"after": reply["id"]}).json()["messages"] == []
        # 搜索
        found = c.get("/api/chat", params={"q": "你好"}).json()["messages"]
        assert [m["id"] for m in found] == [user["id"]]
        # 校验
        assert c.post("/api/chat", json={"text": "  "}).status_code == 400
        assert c.post("/api/chat", json={"text": "x" * 2001}).status_code == 400
        assert c.post("/api/chat", json={"text": "hi", "x": 1}).status_code == 422
        # 一键清空
        assert c.delete("/api/chat").json() == {"deleted": 2}
        assert c.get("/api/chat").json()["messages"] == []


def test_rooms_dm_and_unread():
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        data = c.get("/api/chat/rooms").json()
        ids = [r["id"] for r in data["rooms"]]
        assert ids[:2] == ["main", "work"] and "dm:V1" in ids
        assert data["budget"] == {"spent_usd": 0, "limit_usd": 5.0}
        # 私聊：只有对方回应
        r = c.post("/api/chat", json={"text": "大家好", "room": "dm:V1"})
        assert r.status_code == 202 and r.json()["speakers"] == ["阿一"]
        # 还没打开私聊时，回应算未读
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            rooms = {x["id"]: x for x in c.get("/api/chat/rooms").json()["rooms"]}
            if rooms["dm:V1"]["unread"] == 1:
                break
            time.sleep(0.02)
        assert rooms["dm:V1"]["unread"] == 1 and rooms["dm:V1"]["last"]["name"] == "阿一"
        view = wait_for(c, 0, 2, room="dm:V1")
        assert view["room"] == {"id": "dm:V1", "kind": "dm", "title": "阿一"}
        assert [m["id"] for m in view["members"]] == ["V1"]
        # 打开（拉取）之后已读
        rooms = {x["id"]: x for x in c.get("/api/chat/rooms").json()["rooms"]}
        assert rooms["dm:V1"]["unread"] == 0
        assert c.get("/api/chat/rooms").json()["budget"]["spent_usd"] > 0
        # 群里没有私聊的消息
        assert c.get("/api/chat").json()["messages"] == []
        # 清空只清这个会话
        assert c.delete("/api/chat", params={"room": "dm:V1"}).json() == {"deleted": 2}


def test_unknown_room_is_404():
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        assert c.get("/api/chat", params={"room": "nope"}).status_code == 404
        assert c.post("/api/chat", json={"text": "hi", "room": "dm:X"}).status_code == 404
        assert c.delete("/api/chat", params={"room": "nope"}).status_code == 404


def test_handoff_endpoint():
    env = Env(confirm_threshold_usd=100.0)
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        r = c.post("/api/chat/handoff", json={"text": "写一份周报"})
        assert r.status_code == 202 and r.json()["session_id"]
        assert c.get(f"/api/sessions/{r.json()['session_id']}").json()["workflow"] == "office"
        assert c.post("/api/chat/handoff", json={"text": " "}).status_code == 400
        assert c.post("/api/chat/handoff", json={"text": "x", "y": 1}).status_code == 422


def test_personas_and_memories_api():
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        members = c.get("/api/chat/personas").json()["members"]
        assert members[0]["id"] == "V1" and members[0]["source"] == "empty"
        r = c.put("/api/chat/personas/V1", json={"persona": {"personality": "爱较真"}})
        assert r.status_code == 200 and r.json()["persona"]["personality"] == "爱较真"
        assert c.get("/api/chat/personas").json()["members"][0]["source"] == "local"
        assert c.put("/api/chat/personas/V1", json={"persona": None}).status_code == 200
        assert c.get("/api/chat/personas").json()["members"][0]["source"] == "empty"
        bad = {"persona": {"personality": "x" * 400}}
        assert c.put("/api/chat/personas/V1", json=bad).status_code == 400
        assert c.put("/api/chat/personas/X", json={"persona": None}).status_code == 404

        r = c.post("/api/chat/memories", json={"member": "V2", "text": "用户下周考试"})
        assert r.status_code == 201
        mid = r.json()["id"]
        assert r.json()["layer"] == "note" and r.json()["source"] == "user"
        sens = {"member": "V2", "text": "密码 123"}
        assert c.post("/api/chat/memories", json=sens).status_code == 400
        assert c.post("/api/chat/memories", json={"text": "x"}).status_code == 400
        data = c.get("/api/chat/memories", params={"member": "V2"}).json()
        assert [m["text"] for m in data["memories"]] == ["用户下周考试"]
        assert data["memories"][0]["name"] == "阿二"
        assert c.patch(f"/api/chat/memories/{mid}", json={"text": "改过"}).json()["text"] == "改过"
        assert c.patch("/api/chat/memories/999", json={"text": "x"}).status_code == 404
        assert c.post("/api/chat/memories/tidy").json() == {"written": 0}
        assert c.delete(f"/api/chat/memories/{mid}").json() == {"deleted": 1}
        assert c.delete(f"/api/chat/memories/{mid}").status_code == 404
        c.post("/api/chat/memories", json={"member": "V1", "text": "a"})
        c.post("/api/chat/memories", json={"member": "V2", "text": "b"})
        assert c.delete("/api/chat/memories", params={"member": "V1"}).json() == {"deleted": 1}
        assert c.delete("/api/chat/memories").json() == {"deleted": 1}
