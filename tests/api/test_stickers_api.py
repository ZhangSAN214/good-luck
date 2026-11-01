"""成员专属表情包接口：列表、画、审、取图。"""

from __future__ import annotations

import json

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


def test_stash_listing_delete_and_clear():
    import json

    from roundtable.core.member.stash import STASH_KEY

    env = Env()
    service = RoundtableService(env.rt)
    at = env.rt.budget.now().isoformat()
    rows = [{"n": 1, "id": "a" * 32 + ".gif", "label": "翻白眼的猫", "at": at, "uses": 0}]
    env.rt.repo.set_setting(STASH_KEY, json.dumps({"V1": rows, "V2": rows}))
    with TestClient(create_app(service)) as c:
        stash = c.get("/api/stickers").json()["stash"]
        assert [s["id"] for s in stash] == ["V1", "V2"] and stash[0]["items"][0][
            "label"
        ] == "翻白眼的猫"
        assert c.delete("/api/stickers/stash/V1/1").json() == {"deleted": 1}
        assert c.delete("/api/stickers/stash/V1/1").status_code == 404
        assert c.delete("/api/stickers/stash").json() == {"cleared": 1}
        assert c.get("/api/stickers").json()["stash"] == []


def test_emoji_image_api(tmp_path):
    from roundtable.core.emoji_images import EmojiImages

    env = Env()
    service = RoundtableService(env.rt)
    png = b"\x89PNG\r\n\x1a\n" + b"y" * 8
    service.emoji = EmojiImages(env.rt, tmp_path, lambda url: png if "1f602" in url else None)
    with TestClient(create_app(service)) as c:
        assert c.get("/api/emoji").json() == {"enabled": True, "style": "apple"}
        r = c.get("/api/emoji/1f602.png")
        assert (
            r.status_code == 200 and r.content == png and r.headers["content-type"] == "image/png"
        )
        assert c.get("/api/emoji/1f9ff.png").status_code == 404
        assert c.get("/api/emoji/abc..png").status_code == 404


def test_sticker_pack_api(tmp_path):
    from ..core.test_sticker_packs import make_pack

    env = Env()
    env.rt.sticker_dir = tmp_path / "stickers"
    service = RoundtableService(env.rt)
    zipped = make_pack(tmp_path)
    catalog = {"packs": [{"id": "whale", "name": "鲸鱼", "archiveUrl": "https://x/w.zip"}]}
    service.packs._fetch = lambda url, limit: (
        json.dumps(catalog).encode() if url.endswith("catalog.json") else zipped
    )
    with TestClient(create_app(service)) as c:
        assert c.get("/api/sticker-packs").json() == {"packs": []}
        assert c.get("/api/sticker-packs/catalog").json()["packs"][0]["id"] == "whale"
        r = c.post("/api/sticker-packs", json={"id": "whale", "owner": "V1"})
        assert r.status_code == 200 and r.json()["label"] == "鲸鱼"
        assert c.post("/api/sticker-packs", json={"id": "nope"}).status_code == 400
        [pack] = c.get("/api/chat/stickers").json()["packs"]
        assert pack["pack"] == "鲸鱼" and pack["member"] == "V1"
        img = c.get(pack["items"][2]["url"])
        assert img.status_code == 200 and img.headers["content-type"] == "image/gif"
        assert c.get("/api/sticker-packs/whale/99").status_code == 404
        r = c.patch("/api/sticker-packs/whale", json={"owner": ""})
        assert r.json()["owner"] == ""
        assert c.get("/api/chat/stickers").json()["packs"][0]["member"] == "*"
        assert c.delete("/api/sticker-packs/whale").json()["removed"]
        assert c.get("/api/chat/stickers").json()["packs"] == []
