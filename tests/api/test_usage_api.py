"""余额与用量面板：本机账本汇总、钱折算 token、渠道余额与对账；匿名未揭晓不按成员展示，不含 key。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.providers import AccountBalance, ErrorKind, ProviderError
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import MEDIUM, Env
from .test_app import FAKE_KEY, leaks, sse_events


def client(balance=None) -> tuple[Env, TestClient]:
    env = Env(confirm_threshold_usd=100.0)
    if balance is not None:
        channels = env.rt.config.models.channels
        channels["c"] = channels["c"].model_copy(update={"balance": "credits"})

        async def account_balance():
            if isinstance(balance, Exception):
                raise balance
            return balance

        env.fake.account_balance = account_balance
    return env, TestClient(create_app(RoundtableService(env.rt)))


def run(c: TestClient, anonymous: bool) -> str:
    r = c.post("/api/sessions", json={"question": MEDIUM, "seed": 2, "anonymous": anonymous})
    sid = r.json()["session_id"]
    sse_events(c, sid)
    return sid


def test_usage_matches_budget_and_hides_unrevealed_members():
    env, c = client()
    with c:
        sid = run(c, anonymous=True)
        u = c.get("/api/usage").json()
        assert u["totals"]["calls"] > 0
        # 与顶栏「本月已用」同一来源
        assert u["totals"]["cost_usd"] == round(u["budget"]["month"]["spent_usd"], 6)
        assert u["by_member"] == [] and u["hidden"]["calls"] == u["totals"]["calls"]
        assert [f["key"] for f in u["by_feature"]] == ["work"]
        assert len(u["daily"]) == 30 and u["daily"][-1]["calls"] == u["totals"]["calls"]
        # 揭晓前，用量响应里只有总数，没有任何模型 id / 厂商 / 渠道
        keys = ("totals", "by_feature", "by_member", "hidden", "daily")
        body = json.dumps({k: u[k] for k in keys})
        assert leaks(env, body) == []

        c.post(f"/api/sessions/{sid}/reveal")
        u = c.get("/api/usage").json()
        assert u["hidden"] is None and u["by_member"]
        assert sum(m["calls"] for m in u["by_member"]) == u["totals"]["calls"]
        assert all(m["name"] for m in u["by_member"])


def test_usage_ranges_and_equivalents():
    _, c = client()
    with c:
        run(c, anonymous=False)
        for span in ("day", "week", "month", "all"):
            u = c.get(f"/api/usage?range={span}").json()
            assert u["range"] == span and u["totals"]["calls"] > 0
        assert c.get("/api/usage?range=year").status_code == 400
        eq = c.get("/api/usage").json()["equivalents"]
        assert eq["basis"] == "budget_remaining" and eq["amount_usd"] > 0
        keys = [r["key"] for r in eq["rows"]]
        assert keys[0].startswith("tier:") and all(r["input_tokens"] for r in eq["rows"])


def test_balance_reconciles_with_ledger():
    bal = AccountBalance(total_credits=10, total_usage=2.5, usage_monthly=1.0, usage_daily=0.2)
    _, c = client(balance=bal)
    with c:
        run(c, anonymous=False)
        b = c.get("/api/usage/balance").json()
        (row,) = b["channels"]
        assert row["ok"] and row["remaining"] == 7.5
        assert row["ledger_month_usd"] > 0
        assert row["diff_month_usd"] == round(1.0 - row["ledger_month_usd"], 6)
        assert FAKE_KEY not in json.dumps(b)


def test_balance_failure_is_reported_not_raised():
    err = ProviderError(ErrorKind.AUTH, "c", f"bad key {FAKE_KEY}", status=401)
    _, c = client(balance=err)
    with c:
        b = c.get("/api/usage/balance?refresh=1").json()
        (row,) = b["channels"]
        assert row["ok"] is False and "auth" in row["error"]
        assert FAKE_KEY not in json.dumps(b)


def test_balance_without_configured_channel_is_empty():
    _, c = client()
    with c:
        assert c.get("/api/usage/balance").json()["channels"] == []


def test_channel_priority_get_and_put():
    env, c = client()
    with c:
        p = c.get("/api/channels/priority").json()
        assert p["strategy"] == "auto"
        names = [row["channel"] for row in p["channels"]]
        assert "c" in names
        row = next(r for r in p["channels"] if r["channel"] == "c")
        assert row["credit_usd"] is None and row["available"] and row["members"]

        r = c.put(
            "/api/channels/priority",
            json={"strategy": "custom", "order": names[::-1], "credits": {"c": 50}},
        )
        assert r.status_code == 200
        p = r.json()
        assert p["strategy"] == "custom"
        assert [row["channel"] for row in p["channels"]][: len(names)] == names[::-1]
        row = next(r for r in p["channels"] if r["channel"] == "c")
        assert row["credit_usd"] == 50 and row["credit_left_usd"] == 50
        assert env.rt.router.priority.credits["c"] == 50
        assert FAKE_KEY not in r.text

        assert c.put("/api/channels/priority", json={"strategy": "x"}).status_code == 400
        assert c.put("/api/channels/priority", json={"strategy": "auto", "x": 1}).status_code == 422
