"""scripts/check_models.py 的测试。不联网：HTTP 用 respx 模拟。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import respx

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_models.py"
spec = importlib.util.spec_from_file_location("check_models", SCRIPT)
check_models = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check_models
spec.loader.exec_module(check_models)

FAKE_KEY = "test-key-not-real"


def remote(*models):
    return {
        "data": [
            {"id": mid, "pricing": {"prompt": str(i / 1e6), "completion": str(o / 1e6)}}
            for mid, i, o in models
        ]
    }


def local(mid="a", model="v/a", provider="openrouter", price=(1.0, 2.0)):
    return {
        "id": mid,
        "model": model,
        "provider": provider,
        "price": {"input": price[0], "output": price[1]},
    }


def test_compare_ok():
    [f] = check_models.compare([local()], remote(("v/a", 1.0, 2.0)))
    assert f.status == "ok"


def test_compare_missing():
    [f] = check_models.compare([local()], remote(("v/other", 1.0, 2.0)))
    assert f.status == "missing"


def test_compare_price_mismatch_reports_side():
    [f] = check_models.compare([local()], remote(("v/a", 1.0, 3.0)))
    assert f.status == "price_mismatch"
    assert "output" in f.detail and "input" not in f.detail


def test_compare_within_tolerance():
    [f] = check_models.compare([local()], remote(("v/a", 1.04, 2.0)), tolerance=0.05)
    assert f.status == "ok"


def test_compare_skips_non_openrouter():
    [f] = check_models.compare([local(provider="local")], remote())
    assert f.status == "skipped"


def test_compare_missing_remote_price():
    payload = {"data": [{"id": "v/a", "pricing": {}}]}
    [f] = check_models.compare([local()], payload)
    assert f.status == "price_mismatch"


def test_local_config_loads():
    models = check_models.load_local_models()
    assert len(models) >= 5


@respx.mock
def test_main_sends_key_and_exit_codes(monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    monkeypatch.setattr(check_models, "load_dotenv", lambda *_: None)
    monkeypatch.setattr(check_models, "load_local_models", lambda: [local()])
    route = respx.get(check_models.MODELS_URL).mock(
        return_value=httpx.Response(200, json=remote(("v/a", 1.0, 2.0)))
    )
    assert check_models.main([]) == 0
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in capsys.readouterr().out

    route.mock(return_value=httpx.Response(200, json=remote()))
    assert check_models.main([]) == 1


@respx.mock
@pytest.mark.parametrize(
    "response",
    [httpx.Response(401, json={"error": "bad key"}), httpx.ConnectError("boom")],
)
def test_main_http_failure_does_not_leak_key(monkeypatch, capsys, response):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    monkeypatch.setattr(check_models, "load_dotenv", lambda *_: None)
    route = respx.get(check_models.MODELS_URL)
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)
    assert check_models.main([]) == 2
    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.out + captured.err
