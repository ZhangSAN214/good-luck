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


def test_pair_room_is_listed_and_read_only():
    env = Env()
    env.rt.repo.ensure_chat_room(
        "pair:V1+V2", kind="pair", title="阿一 和 阿二", members=["V1", "V2"], with_user=False
    )
    env.rt.repo.add_chat_message(
        "pair:V1+V2", "V1", "晚上打游戏吗", meta={"life": True, "offline": True}
    )
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        rooms = c.get("/api/chat/rooms").json()
        assert any(r["id"] == "pair:V1+V2" and r["kind"] == "pair" for r in rooms["rooms"])
        assert rooms["life"]["daily_usd"] > 0
        data = c.get("/api/chat", params={"room": "pair:V1+V2"}).json()
        assert data["room"]["kind"] == "pair" and data["messages"][0]["meta"]["offline"]
        r = c.post("/api/chat", json={"text": "我也来", "room": "pair:V1+V2"})
        assert r.status_code == 400


def test_feeds_settings_round_trip():
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        data = c.get("/api/chat/feeds").json()
        assert data["source"] == "config" and data["feeds"] and data["search_per_day"] == 5
        mine = [{"name": "星空", "url": "https://news.test/rss", "topics": ["天文"]}]
        data = c.put("/api/chat/feeds", json={"feeds": mine}).json()
        assert data["source"] == "local" and data["feeds"][0]["kind"] == "news"
        bad = [{"name": "x", "url": "ftp://x"}]
        assert c.put("/api/chat/feeds", json={"feeds": bad}).status_code == 400
        r = c.post("/api/chat/feeds/refresh").json()  # 测试里抓取一律失败
        assert r["added"] == 0 and r["failed"] == 1 and r["last_refresh"]
        assert c.put("/api/chat/feeds", json={"feeds": None}).json()["source"] == "config"


def test_moments_round_trip():
    env = Env()
    env.rt.repo.add_moment("V1", "今天在天台看星星")
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        assert c.get("/api/chat/rooms").json()["moments_unseen"] == 1
        data = c.get("/api/moments").json()
        (m,) = data["moments"]
        assert m["name"] == "阿一" and m["likes"] == [] and not m["liked"]
        assert c.get("/api/chat/rooms").json()["moments_unseen"] == 0  # 看过了
        assert c.post(f"/api/moments/{m['id']}/like").json() == {"liked": True}
        assert c.get("/api/moments").json()["moments"][0]["likes"] == [{"id": "user", "name": "我"}]
        assert c.post(f"/api/moments/{m['id']}/like").json() == {"liked": False}
        r = c.post(f"/api/moments/{m['id']}/comments", json={"text": "好美"})
        assert r.status_code == 201
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            comments = c.get("/api/moments").json()["moments"][0]["comments"]
            if len(comments) == 2:
                break
            time.sleep(0.02)
        mine, reply = comments
        assert (mine["name"], reply["author"], reply["reply_to"]) == ("我", "V1", mine["id"])
        assert reply["reply_name"] == "我"
        assert c.post("/api/moments", json={"text": " "}).status_code == 400
        assert c.post("/api/moments/999/like").status_code == 404
        bad = {"text": "x", "reply_to": 999}
        assert c.post(f"/api/moments/{m['id']}/comments", json=bad).status_code == 400
        assert c.get("/api/moments", params={"author": "V1"}).json()["moments"]
        assert c.get("/api/moments", params={"author": "nobody"}).status_code == 404
        assert c.delete(f"/api/moments/comments/{reply['id']}").json() == {"deleted": 1}
        assert c.delete(f"/api/moments/{m['id']}").json() == {"deleted": 1}
        assert c.get("/api/moments").json()["moments"] == []


def test_chat_and_moments_media(tmp_path):
    env = Env()
    env.rt.chat_media_dir = tmp_path / "chat_media"
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        r = c.post("/api/chat/media", content=png, headers={"Content-Type": "image/png"})
        assert r.status_code == 201
        up = r.json()
        assert up["kind"] == "image" and up["size"] == len(png)
        got = c.get(f"/api/chat/media/{up['id']}")
        assert got.content == png and got.headers["content-type"] == "image/png"
        assert got.headers["x-content-type-options"] == "nosniff"
        part = c.get(f"/api/chat/media/{up['id']}", headers={"Range": "bytes=0-7"})
        assert part.status_code == 206 and part.content == png[:8]
        assert c.get("/api/chat/media/" + "0" * 32 + ".png").status_code == 404
        bad = c.post(
            "/api/chat/media", content=b"<svg/>", headers={"Content-Type": "image/svg+xml"}
        )
        assert bad.status_code == 400
        # 只发图片：成员看到占位文字，前端按 meta 显示图片
        r = c.post("/api/chat", json={"room": "dm:V1", "media": [{"id": up["id"], "w": 2, "h": 1}]})
        assert r.status_code == 202
        msg = wait_for(c, 0, 2, room="dm:V1")["messages"][0]
        assert msg["text"] == "[图片]"
        assert msg["meta"] == {
            "media": [{"id": up["id"], "kind": "image", "w": 2.0, "h": 1.0}],
            "auto_text": True,
        }
        # 文字 + 图片
        c.post("/api/chat", json={"room": "dm:V2", "text": "看", "media": [{"id": up["id"]}]})
        msg = wait_for(c, 0, 2, room="dm:V2")["messages"][0]
        assert msg["text"] == "看\n[图片]" and msg["meta"]["caption"] == "看"
        missing = c.post("/api/chat", json={"text": "x", "media": [{"id": "0" * 32 + ".png"}]})
        assert missing.status_code == 400
        # 朋友圈带图
        r = c.post("/api/moments", json={"text": "今天", "media": [{"id": up["id"]}]})
        assert r.status_code == 201
        mo = c.get("/api/moments").json()["moments"][0]
        assert mo["meta"]["media"][0]["id"] == up["id"] and mo["meta"]["caption"] == "今天"
        assert c.post("/api/moments", json={}).status_code == 400
