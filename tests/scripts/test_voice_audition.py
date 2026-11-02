"""scripts/voice_audition.py 的测试：用假语音渠道，不联网、不花钱。"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from roundtable.core.config import load_config
from roundtable.core.providers import ErrorKind, Media, MediaOutput, ProviderError

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "voice_audition.py"
spec = importlib.util.spec_from_file_location("voice_audition", SCRIPT)
va = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = va
spec.loader.exec_module(va)


@pytest.fixture(scope="module")
def config():
    return load_config(ROOT / "config")


def test_candidates_file_is_valid_and_covers_every_member(config):
    data = va.load_candidates(va.CANDIDATES)
    assert set(data["members"]) == set(config.personas.nicknames)
    jobs = va.plan(data, config)
    assert jobs and all(j.estimate_usd > 0 for j in jobs)
    # 同一位成员的候选念同一句台词（带语气的只多了前面的语气描述）
    for vendor, item in data["members"].items():
        own = [c.get("line") for c in item["candidates"]]
        for j, line in zip((j for j in jobs if j.member == vendor), own, strict=True):
            assert j.text.endswith(line or item["line"])
    assert sum(j.estimate_usd for j in jobs) < 0.5


def test_style_goes_in_front_of_line():
    data = {"style_template": "用{style}的语气说：{line}"}
    assert va.spoken_text(data, "你好", "") == "你好"
    assert va.spoken_text(data, "你好", "温柔") == "用温柔的语气说：你好"


def test_bad_candidates_report_field(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "style_template: '{style}{line}'\nmembers:\n  X:\n    line: hi\n"
        "    candidates:\n      - {model: m, voice: ''}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"members\.X\.candidates\[0\]\.voice"):
        va.load_candidates(path)


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def synthesize_speech(self, model, text, params):
        self.calls.append((model, text, params))
        if params["voice"] == "nope":
            raise ProviderError(ErrorKind.BAD_REQUEST, "openrouter", "voice not found")
        return MediaOutput(Media("audio", "audio/mpeg", b"ID3fake", "speech.mp3"), cost_usd=0.001)


def test_run_writes_audio_page_and_continues_after_failure(config, tmp_path):
    data = {
        "style_template": "用{style}的语气说：{line}",
        "members": {
            "Anthropic": {
                "name": "Claude<小克>",
                "line": "嗯，记下了。",
                "candidates": [
                    {"model": "minimax-speech-2.8-hd", "voice": "nope"},
                    {"model": "minimax-speech-2.8-hd", "voice": "Chinese (Mandarin)_Soft_Girl"},
                    {"model": "gemini-3.8-flash-tts", "voice": "Leda", "style": "温柔"},
                ],
            }
        },
    }
    jobs = va.plan(data, config)
    provider = FakeProvider()
    spent = asyncio.run(va.run_jobs(provider, config, jobs, tmp_path, max_usd=0))
    va.write_report(jobs, tmp_path, spent)

    assert [j.status for j in jobs] == ["failed", "ok", "ok"]
    assert spent == pytest.approx(0.002)
    assert (tmp_path / "Anthropic_2_Chinese_Mandarin_Soft_Girl.mp3").read_bytes() == b"ID3fake"
    # 音色放进请求；Gemini 的 pcm 参数保留；语气描述在台词前面
    assert provider.calls[1][2]["voice"] == "Chinese (Mandarin)_Soft_Girl"
    assert provider.calls[2][2]["response_format"] == "pcm"
    assert provider.calls[2][1] == "用温柔的语气说：嗯，记下了。"
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Claude&lt;小克&gt;" in page and "<audio" in page and "voice not found" in page
    report = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert report["jobs"][0]["status"] == "failed"


def test_max_usd_skips_instead_of_overspending(config, tmp_path):
    data = va.load_candidates(va.CANDIDATES)
    jobs = va.plan(data, config, ["Anthropic"])
    asyncio.run(va.run_jobs(FakeProvider(), config, jobs, tmp_path, max_usd=0.005))
    assert "skipped" in [j.status for j in jobs]
    assert sum(j.cost_usd for j in jobs) <= 0.005


def test_dry_run_does_not_need_key(monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert va.main(["--dry-run", "--member", "xAI"]) == 0
    assert "预计" in capsys.readouterr().out


def test_auth_error_stops_the_run(config, tmp_path):
    class NoKey(FakeProvider):
        async def synthesize_speech(self, model, text, params):
            self.calls.append((model, text, params))
            raise ProviderError(ErrorKind.AUTH, "openrouter", "bad key")

    data = va.load_candidates(va.CANDIDATES)
    jobs = va.plan(data, config, ["Google"])
    provider = NoKey()
    assert asyncio.run(va.run_jobs(provider, config, jobs, tmp_path, max_usd=0)) == 0
    assert len(provider.calls) == 1


def test_candidate_params_go_into_request(config, tmp_path):
    data = va.load_candidates(ROOT / "scripts" / "voice_candidates_hakimi_r2.yaml")
    jobs = va.plan(data, config)
    provider = FakeProvider()
    asyncio.run(va.run_jobs(provider, config, jobs[:2], tmp_path, max_usd=0))
    assert provider.calls[1][2]["speed"] == 1.1
    assert provider.calls[1][2]["response_format"] == "wav"
