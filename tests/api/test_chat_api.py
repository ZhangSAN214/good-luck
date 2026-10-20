"""群聊接口：发消息后成员在后台回应；轮询拿到新消息；搜索；一键清空。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env


def wait_for(c, after, n, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = c.get("/api/chat", params={"after": after}).json()
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
