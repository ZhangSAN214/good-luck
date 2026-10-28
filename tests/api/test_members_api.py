"""成员形象接口：/api/members 与 /api/members/files/…。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.members_samples import PNG_1PX, write_assets
from ..core.orchestrator.conftest import Env


def client(members_dir=None) -> tuple[Env, TestClient]:
    env = Env(members_dir=members_dir)
    return env, TestClient(create_app(RoundtableService(env.rt)))


def test_members_and_files(tmp_path):
    vendor_env = Env()
    model = vendor_env.config.models.models[0]
    root = write_assets(tmp_path / "assets", model.vendor)
    (tmp_path / "secret.png").write_bytes(PNG_1PX)
    (root / "note.txt").write_text("x", encoding="utf-8")
    env, c = client(root)
    with c:
        r = c.get("/api/members")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is True and data["reason"] is None
        (m,) = data["members"]
        assert m == {
            "id": "m1",
            "name": "成员一",
            "nickname": "小一",
            "display": "小一",
            "theme_color": "#3366cc",
            "order": 1,
            "status": "active",
            "images": {
                "avatar_64": "/api/members/files/m1/c64.png",
                "avatar_128": "/api/members/files/m1/c128.png",
                "avatar_256": None,
                "chibi": "/api/members/files/m1/chibi.png",
                "room_day": "/api/members/files/m1/room_day.webp",
                "room_night": None,
            },
            "pet": None,
            "room": None,
        }
        assert data["sky"] == {} and data["rooms"] == {}
        same_vendor = {x.id for x in env.config.models.models if x.vendor == model.vendor}
        assert {k for k, v in data["models"].items() if v == "m1"} == same_vendor

        f = c.get(m["images"]["avatar_128"])
        assert f.status_code == 200 and f.content == PNG_1PX
        assert f.headers["content-type"] == "image/png"
        assert f.headers["x-content-type-options"] == "nosniff"
        assert f.headers["cache-control"] == "private, max-age=3600"

        for bad in (
            "/api/members/files/m1/nope.png",
            "/api/members/files/note.txt",
            "/api/members/files/..%2Fsecret.png",
            "/api/members/files/m1%2F..%2F..%2Fsecret.png",
            "/api/members/files/%2Fetc%2Fpasswd",
            "/api/members/files/members.json",
        ):
            assert c.get(bad).status_code == 404, bad


def test_no_assets(tmp_path):
    _, c = client(tmp_path / "nowhere")
    with c:
        data = c.get("/api/members").json()
        assert data["available"] is False and data["reason"]
        assert data["members"] == [] and data["models"] == {}
        assert c.get("/api/members/files/a.png").status_code == 404


def test_pet_frames_become_urls(tmp_path):
    model = Env().config.models.models[0]
    root = write_assets(tmp_path / "assets", model.vendor)
    (root / "m1" / "pet.json").write_text(
        '{"actions": {"wave": {"frames": ["chibi.png", "c64.png"], "fps": 6}},'
        ' "expressions": {"shy": {"frames": ["c128.png"]}}}',
        encoding="utf-8",
    )
    _, c = client(root)
    with c:
        (m,) = c.get("/api/members").json()["members"]
        assert m["pet"] == {
            "fps": 8,
            "actions": {
                "wave": {
                    "frames": ["/api/members/files/m1/chibi.png", "/api/members/files/m1/c64.png"],
                    "fps": 6,
                    "loop": False,
                }
            },
            "expressions": {
                "shy": {"frames": ["/api/members/files/m1/c128.png"], "fps": 8, "loop": False}
            },
        }
        assert c.get(m["pet"]["actions"]["wave"]["frames"][1]).status_code == 200
        assert c.get("/api/members/files/m1/pet.json").status_code == 404  # 只给图片


def test_pet_special_idle_and_eggs_reach_the_api(tmp_path):
    model = Env().config.models.models[0]
    root = write_assets(tmp_path / "assets", model.vendor)
    (root / "m1" / "pet.json").write_text(
        '{"actions": {"letter": {"frames": ["chibi.png"]},'
        ' "gift": {"frames": ["c64.png"], "hold_last_s": 3}},'
        ' "special_idle": {"action": "letter", "after_s": 600},'
        ' "easter_eggs": [{"action": "gift", "probability": 0.05}]}',
        encoding="utf-8",
    )
    _, c = client(root)
    with c:
        (m,) = c.get("/api/members").json()["members"]
        assert m["pet"]["special_idle"] == {"action": "letter", "after_s": 600}
        assert m["pet"]["easter_eggs"][0]["action"] == "gift"
        assert m["pet"]["actions"]["gift"]["hold_last_s"] == 3.0
        assert m["pet"]["actions"]["letter"]["loop"] is True
