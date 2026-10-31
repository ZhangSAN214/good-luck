"""成员专属表情包接口：列表、画、审、取图。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.member_stickers import StickerBudgetExceeded, StickerError
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env


def test_stickers_api():
    env = Env()
    service = RoundtableService(env.rt)
    with TestClient(create_app(service)) as c:
        data = c.get("/api/stickers").json()
        assert data["review"] is True and data["month_spent_usd"] == 0
        first = data["members"][0]
        assert first["id"] == "OpenAI" and len(first["stickers"]) == 14
        assert {s["status"] for s in first["stickers"]} == {"missing"}
        assert data["monthly_usd"] == env.rt.config.roundtable.media.stickers.monthly_usd
        # 没有形象卡：404
        assert c.post("/api/stickers/V1/晚安/draw").status_code == 404
        assert c.get("/api/stickers/V1/晚安/image").status_code == 404

        async def draw(member, name):
            if name == "超了":
                raise StickerBudgetExceeded("快超过上限")
            return {"name": name, "caption": name, "status": "pending"}

        reviewed = []

        def review(member, name, status):
            if name == "没画":
                raise StickerError("这个表情还没画")
            reviewed.append((member, name, status))
            return {"name": name, "status": status}

        service.stickers.draw = draw
        service.stickers.review = review
        files = {"晚安": b"\x89PNG", "转圈": b"GIF89a"}
        service.stickers.file = lambda member, name: files.get(name)
        assert c.post("/api/stickers/V1/晚安/draw").json()["status"] == "pending"
        assert c.post("/api/stickers/V1/超了/draw").status_code == 402
        r = c.post("/api/stickers/V1/晚安/review", json={"status": "approved"})
        assert r.status_code == 200 and reviewed == [("V1", "晚安", "approved")]
        assert c.post("/api/stickers/V1/晚安/review", json={"status": "ok"}).status_code == 422
        bad = c.post("/api/stickers/V1/没画/review", json={"status": "approved"})
        assert bad.status_code == 400
        img = c.get("/api/stickers/V1/晚安/image")
        assert img.status_code == 200 and img.content == b"\x89PNG"
        assert img.headers["content-type"] == "image/png"
        assert c.get("/api/stickers/V1/转圈/image").headers["content-type"] == "image/gif"


def test_chat_sticker_packs_list_only_approved_ones_under_the_nickname(tmp_path):
    from ..core.test_chat_actions import give_stickers

    env = Env()
    give_stickers(env, tmp_path, "V2", ["收到"], pending=["没审"])
    service = RoundtableService(env.rt)
    with TestClient(create_app(service)) as c:
        [pack] = c.get("/api/chat/stickers").json()["packs"]
        assert pack["pack"] == "阿二" and pack["member"] == "V2"
        [item] = pack["items"]
        assert item["name"] == "收到" and item["caption"] == "收到！"
        img = c.get(item["url"])
        assert img.status_code == 200 and img.content == b"\x89PNG"
