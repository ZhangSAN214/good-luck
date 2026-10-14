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
    assert {"second", "char"} <= units
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


def test_transcription_and_speech_lists_are_queried():
    urls = check_models.MEDIA_URLS
    assert urls["transcription"].endswith("output_modalities=transcription")
    assert urls["speech"].endswith("output_modalities=speech")


def test_local_config_stt_and_tts_models():
    models = {m["id"]: m for m in check_models.load_local_models()}
    assert models["whisper-large-v3-turbo"]["model"] == "openai/whisper-large-v3-turbo"
    assert models["whisper-large-v3-turbo"]["media_price"] == {"unit": "second", "usd": 0.000003}
    assert models["gemini-3.8-flash-tts"]["speech_tokens_per_char"] == 3.0
    assert models["qwen-audio-3.0-tts-flash"]["media_price"] == {"unit": "char", "usd": 0.000015}
    assert "gpt-4o-mini-tts" not in models and "whisper-1" not in models


def test_token_priced_tts_is_listed_for_manual_check():
    entry = {**local("t", "v/t", price=(0.5, 9.0)), "speech_tokens_per_char": 3.0}
    remote_data = {"data": [{"id": "v/t", "pricing": {"prompt": "5e-7", "audio_output": "9e-6"}}]}
    [f] = check_models.compare([entry], remote_data)
    assert f.status == "manual" and "audio_output" in f.detail and "输入 0.5" in f.detail


# --- 输入模态核对（阶段 22）--------------------------------------------------------------


def tagged(mid, model, *tags, provider="openrouter"):
    return {**local(mid, model, provider), "tags": list(tags)}


def with_modalities(model, input_modalities=None):
    arch = (
        {} if input_modalities is None else {"architecture": {"input_modalities": input_modalities}}
    )
    return {"id": model, "pricing": {"prompt": "1e-6", "completion": "2e-6"}, **arch}


def test_vision_and_image_edit_models_must_accept_image_input():
    entries = [
        tagged("seer", "v/seer", "vision"),
        tagged("editor", "v/editor", "image_gen", "image_edit"),
        tagged("blind", "v/blind", "vision"),
        tagged("both", "v/both", "vision", "image_edit"),
        tagged("plain", "v/plain", "math"),
        tagged("direct", "v/direct", "vision", provider="local"),
    ]
    remote_data = {
        "data": [
            with_modalities("v/seer", ["text", "image"]),
            with_modalities("v/editor", ["text", "image"]),
            with_modalities("v/blind", ["text"]),
            with_modalities("v/both", None),
            with_modalities("v/plain", ["text"]),
        ]
    }
    found = {f.model_id: f for f in check_models.check_modalities(entries, remote_data)}
    assert found["seer"].status == "input_ok" and found["editor"].status == "input_ok"
    assert found["blind"].status == "modality_mismatch" and "不含 image" in found["blind"].detail
    assert found["both"].status == "unverified"
    assert "plain" not in found and "direct" not in found  # 没有图片相关标签 / 非 openrouter 渠道


@respx.mock
def test_main_fails_when_a_tagged_model_has_no_image_input(monkeypatch, capsys):
    monkeypatch.setattr(check_models, "load_dotenv", lambda *_: None)
    monkeypatch.setattr(
        check_models, "load_local_models", lambda: [tagged("blind", "v/blind", "vision")]
    )
    mock_media_lists()
    route = respx.get(check_models.MODELS_URL.split("?")[0]).mock(
        return_value=httpx.Response(200, json={"data": [with_modalities("v/blind", ["text"])]})
    )
    assert check_models.main([]) == 1
    assert "modality_mismatch" in capsys.readouterr().out
    route.mock(
        return_value=httpx.Response(
            200, json={"data": [with_modalities("v/blind", ["text", "image"])]}
        )
    )
    assert check_models.main([]) == 0


def test_local_config_image_models_carry_the_tags_that_get_checked():
    models = {m["id"]: m for m in check_models.load_local_models()}
    for mid in ("gemini-3.1-flash-image", "gpt-image-1-mini", "gpt-image-2"):
        assert "image_edit" in models[mid]["tags"]  # 支持参考图输入，由脚本核对远端
    assert "vision" in models["claude-sonnet-5.5"]["tags"]
