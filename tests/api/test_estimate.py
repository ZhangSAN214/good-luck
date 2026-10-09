"""提交前预估：不调用模型；阵容与提交后一致（同一 seed）；匿名时不含模型名；各模式 × 各档位。"""

from __future__ import annotations

import json

from ..core.attachments.samples import PNG
from .test_app import MEDIUM, client, leaks, sse_events


def test_estimate_all_modes_and_tiers_without_calls():
    env, c = client()
    with c:
        r = c.post("/api/estimate", json={"question": MEDIUM, "seed": 7})
        assert r.status_code == 200
        data = r.json()
        assert env.fake.calls == []  # 不调用任何模型（包括规划员）
        assert data["seed"] == 7 and data["length_source"] in ("rule", "default")
        keys = {(o["workflow"], o["plan"]) for o in data["options"]}
        assert keys == {(w, p) for w in ("discussion", "collab") for p in ("budget", "flagship")}
        selected = [o for o in data["options"] if o["selected"]]
        assert {(o["workflow"], o["plan"]) for o in selected} == {
            ("discussion", "budget"),
            ("collab", "budget"),
        }
        for o in data["options"]:
            assert o["available"] and o["estimate_usd"] > 0 and o["max_usd"] >= o["estimate_usd"]
            assert {s["step"] for s in o["steps"]} and o["members"] >= 2
            assert o["over_threshold"] == (o["estimate_usd"] > data["confirm_threshold_usd"])
            assert "lineup" in o  # 匿名关闭：显示模型
        collab = next(o for o in data["options"] if o["workflow"] == "collab")
        assert "decompose" in {s["step"] for s in collab["steps"]}


def test_estimate_matches_actual_lineup_with_same_seed():
    env, c = client(confirm_threshold_usd=100.0)
    with c:
        est = c.post(
            "/api/estimate", json={"question": MEDIUM, "seed": 11, "workflow": "discussion"}
        ).json()
        option = next(o for o in est["options"] if o["selected"])
        sid = c.post("/api/sessions", json={"question": MEDIUM, "seed": est["seed"]}).json()[
            "session_id"
        ]
        assert sse_events(c, sid)[-1]["status"] == "completed"
        table = env.rt.repo.tables(sid)[0]
        assert sorted(table["members"].values()) == sorted(option["lineup"]["members"])
        assert table["coordinator"] == option["lineup"]["coordinator"]


def test_estimate_anonymous_custom_and_attachments():
    env, c = client()
    with c:
        anon = c.post(
            "/api/estimate", json={"question": MEDIUM, "anonymous": True, "workflow": "collab"}
        )
        body = anon.json()
        assert {o["workflow"] for o in body["options"]} == {"collab"}
        assert all("lineup" not in o for o in body["options"])
        assert leaks(env, json.dumps(body, ensure_ascii=False)) == []

        custom = c.post(
            "/api/estimate",
            json={
                "question": MEDIUM,
                "tier": "custom",
                "models": ["b1", "b2", "b3"],
                "coordinator": "b1",
                "workflow": "discussion",
            },
        ).json()
        mine = next(o for o in custom["options"] if o["plan"] == "custom")
        assert mine["selected"] and mine["lineup"]["coordinator"] == "b1"

        plain = c.post(
            "/api/estimate", json={"question": MEDIUM, "seed": 3, "workflow": "discussion"}
        ).json()
        image = c.post("/api/uploads", params={"name": "g.png"}, content=PNG).json()
        with_image = c.post(
            "/api/estimate",
            json={
                "question": MEDIUM,
                "seed": 3,
                "workflow": "discussion",
                "attachments": [image["id"]],
            },
        ).json()
        price = lambda d: next(o for o in d["options"] if o["selected"])["estimate_usd"]  # noqa: E731
        assert price(with_image) > price(plain)

        assert c.post("/api/estimate", json={"question": " "}).status_code == 400
        assert (
            c.post("/api/estimate", json={"question": MEDIUM, "workflow": "x"}).status_code == 400
        )
        assert (
            c.post("/api/estimate", json={"question": MEDIUM, "attachments": ["nope"]}).status_code
            == 400
        )
        assert env.fake.calls == []
