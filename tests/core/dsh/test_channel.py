from __future__ import annotations

import httpx
import pytest

from roundtable.core.dsh.channel import BalanceError, ModelKey, parse_balance

KEY = "sk-" + "test" * 6
OTHER = "sk-or-" + "other" * 5


def test_key_loaded_from_env_and_child_env_is_minimal():
    environ = {
        "DSH_KEY": KEY,
        "OPENROUTER_API_KEY": OTHER,
        "PATH": "/bin",
        "HOME": "/h",
        "RANDOM": "1",
    }
    key = ModelKey.load("DSH_KEY", environ=environ, dotenv_path=None)
    assert key.present
    env = key.child_env({"DSH_HOME": "/d"}, environ)
    assert env == {"PATH": "/bin", "HOME": "/h", "DSH_HOME": "/d", "DSH_KEY": KEY}
    assert OTHER not in env.values()  # 圆桌的其他 key 一个都不给 dsh
    assert KEY not in repr(key.__dict__)  # Secret 不显示明文


def test_missing_key():
    key = ModelKey.load("DSH_KEY", environ={}, dotenv_path=None)
    assert not key.present
    assert "DSH_KEY" not in key.child_env({}, {"PATH": "/bin"})


def test_parse_balance_converts_currency():
    data = {"balance_infos": [{"currency": "CNY", "total_balance": "72.00"}]}
    b = parse_balance(data, {"CNY": 7.2})
    assert (round(b.amount, 6), b.currency, b.raw) == (10.0, "CNY", 72.0)
    usd = {
        "balance_infos": [
            {"currency": "CNY", "total_balance": "7"},
            {"currency": "USD", "total_balance": "3.5"},
        ]
    }
    assert parse_balance(usd, {}).amount == 3.5
    with pytest.raises(ValueError):
        parse_balance({"balance_infos": [{"currency": "EUR", "total_balance": "1"}]}, {})


async def test_balance_request_uses_key_and_redacts_errors():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        if request.url.path == "/bad":
            return httpx.Response(401, text=f"invalid key {KEY}")
        return httpx.Response(
            200, json={"balance_infos": [{"currency": "USD", "total_balance": "4.2"}]}
        )

    key = ModelKey.load("DSH_KEY", environ={"DSH_KEY": KEY}, dotenv_path=None)
    transport = httpx.MockTransport(handler)
    bal = await key.balance("https://b.test/ok", {}, transport=transport)
    assert bal.amount == 4.2 and seen["auth"] == f"Bearer {KEY}"
    with pytest.raises(BalanceError) as exc:
        await key.balance("https://b.test/bad", {}, transport=transport)
    assert KEY not in str(exc.value) and "401" in str(exc.value)


async def test_balance_without_key():
    with pytest.raises(BalanceError):
        await ModelKey(None, None).balance("https://b.test/ok", {})
