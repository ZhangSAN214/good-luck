"""接力：上一场的成果摘要带进下一场（继续讨论 / 转协同 / 交给某人单独做）；只带成果不带过程。"""

from __future__ import annotations

import json

from .test_app import MEDIUM, client, leaks, sse_events


def run(c, **body) -> str:
    sid = c.post("/api/sessions", json={"question": MEDIUM, **body}).json()["session_id"]
    assert sse_events(c, sid)[-1]["status"] == "completed"
    return sid


def test_carry_into_collab_and_solo_pick():
    env, c = client(confirm_threshold_usd=100.0)
    with c:
        first = run(c, workflow="discussion")
        info = c.get(f"/api/sessions/{first}/carry").json()
        assert info["question"] == MEDIUM and info["summary"]
        assert "```mermaid" not in info["summary"]

        est = c.post(
            "/api/estimate",
            json={"question": "照这个做", "workflow": "collab", "carry_from": first},
        )
        assert est.status_code == 200

        n = len(env.fake.calls)
        nxt = c.post(
            "/api/sessions",
            json={"question": "照这个做", "workflow": "collab", "carry_from": first},
        ).json()["session_id"]
        assert sse_events(c, nxt)[-1]["status"] == "completed"
        q = c.get(f"/api/sessions/{nxt}").json()["question"]
        assert q.startswith("照这个做") and "<previous_result>" in q
        assert info["summary"][:50] in q
        sent = ["\n".join(m.content for m in call.messages) for call in env.fake.calls[n:]]
        assert any("<previous_result>" in s for s in sent)

        solo = c.post(
            "/api/sessions",
            json={
                "question": "单独写一版",
                "workflow": "solo",
                "tier": "custom",
                "models": ["b1"],
                "carry_from": first,
            },
        ).json()["session_id"]
        assert sse_events(c, solo)[-1]["status"] == "completed"
        tables = env.rt.repo.tables(solo)
        assert list(tables[0]["members"].values()) == ["b1"]


def test_carry_requires_completed_session_and_hides_identities():
    env, c = client(confirm_threshold_usd=100.0)
    with c:
        assert c.get("/api/sessions/nope/carry").status_code == 404
        r = c.post("/api/sessions", json={"question": "接着做", "carry_from": "nope"})
        assert r.status_code == 404

        anon = run(c, workflow="discussion", anonymous=True)
        info = c.get(f"/api/sessions/{anon}/carry")
        assert info.json()["anonymous"] is True
        assert leaks(env, info.text) == []

        nxt = c.post(
            "/api/sessions",
            json={"question": "继续", "carry_from": anon, "anonymous": True},
        ).json()["session_id"]
        assert sse_events(c, nxt)[-1]["status"] == "completed"
        view = c.get(f"/api/sessions/{nxt}").json()
        assert leaks(env, json.dumps(view["question"], ensure_ascii=False)) == []
