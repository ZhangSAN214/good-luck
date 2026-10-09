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


def mock_media_lists(**responses):
    """媒体模型的额外列表：默认返回空列表；可按名称指定响应。"""
    for name, url in check_models.MEDIA_URLS.items():
        respx.get(url).mock(
            return_value=responses.get(name, httpx.Response(200, json={"data": []}))
        )


@respx.mock
def test_main_sends_key_and_exit_codes(monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    monkeypatch.setattr(check_models, "load_dotenv", lambda *_: None)
    monkeypatch.setattr(check_models, "load_local_models", lambda: [local()])
    mock_media_lists()
    route = respx.get(check_models.MODELS_URL.split("?")[0]).mock(
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
    mock_media_lists()
    route = respx.get(check_models.MODELS_URL)
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)
    assert check_models.main([]) == 2
    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.out + captured.err


def media_local(mid="img", model="v/img", unit="image", usd=0.04):
    return {**local(mid, model), "media_price": {"unit": unit, "usd": usd}}


def test_media_models_are_found_but_price_is_left_to_humans():
    remote_data = {"data": [{"id": "v/img", "pricing": {"image": "0.04"}}]}
    [f] = check_models.compare([media_local()], remote_data)
    assert f.status == "manual" and "按张计价" in f.detail and "0.04" in f.detail


def test_missing_media_model_is_reported():
    [f] = check_models.compare([media_local()], {"data": []})
    assert f.status == "missing"


def test_local_config_has_media_models_with_unit_prices():
    media = [m for m in check_models.load_local_models() if m["media_price"]]
    units = {m["media_price"]["unit"] for m in media}
    assert {"second", "minute", "char"} <= units
    image = [m for m in check_models.load_local_models() if m["image_tokens"]]
    assert image and all(not m["media_price"] for m in image)  # 图像模型按 token 计价


@respx.mock
def test_main_merges_media_lists_and_survives_failed_ones(monkeypatch, capsys):
    monkeypatch.setattr(check_models, "load_dotenv", lambda *_: None)
    monkeypatch.setattr(
        check_models,
        "load_local_models",
        lambda: [local(), media_local(), media_local("vid", "v/vid", "second", 0.1)],
    )
    respx.get(check_models.MODELS_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [*remote(("v/a", 1.0, 2.0))["data"], {"id": "v/img", "pricing": {}}]}
        )
    )
    mock_media_lists(video=httpx.Response(500))
    assert check_models.main([]) == 0  # 找不到的媒体模型是 unverified，不算失败
    captured = capsys.readouterr()
    assert "manual" in captured.out and "unverified" in captured.out
    assert "video" in captured.err


def test_image_token_models_compare_against_image_output():
    entry = {**local("g", "v/g", price=(0.5, 60.0)), "image_tokens": 1290}
    ok = {
        "data": [
            {
                "id": "v/g",
                "pricing": {"prompt": "5e-7", "completion": "3e-6", "image_output": "6e-5"},
            }
        ]
    }
    assert check_models.compare([entry], ok)[0].status == "ok"
    off = {
        "data": [
            {
                "id": "v/g",
                "pricing": {"prompt": "5e-7", "completion": "6e-5", "image_output": "3e-5"},
            }
        ]
    }
    [f] = check_models.compare([entry], off)
    assert f.status == "price_mismatch" and "output" in f.detail and "30" in f.detail
