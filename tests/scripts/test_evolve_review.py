"""scripts/evolve_review.py：没有 key / 模型时跳过（退出码 0）；有 key 时交给模型复审。不联网。"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "evolve_review.py"
spec = importlib.util.spec_from_file_location("evolve_review", SCRIPT)
evolve_review = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = evolve_review
spec.loader.exec_module(evolve_review)

FAKE_KEY = "test-" + "key-not-real"


def test_skips_without_key(capsys, monkeypatch):
    monkeypatch.setattr(evolve_review, "git_diff", lambda base: "diff --git a b")
    assert evolve_review.main(["--base", "x"], environ={}) == 0
    out = capsys.readouterr().out
    assert "跳过复审" in out and "EVOLVE_LLM_API_KEY" in out


def test_skips_without_model(capsys, monkeypatch):
    monkeypatch.setattr(evolve_review, "git_diff", lambda base: "diff --git a b")
    assert evolve_review.main(["--base", "x"], environ={"EVOLVE_LLM_API_KEY": FAKE_KEY}) == 0
    assert "EVOLVE_REVIEW_MODEL" in capsys.readouterr().out


def test_unknown_channel_skips():
    env = {"EVOLVE_LLM_API_KEY": FAKE_KEY, "EVOLVE_REVIEW_MODEL": "m", "EVOLVE_LLM_CHANNEL": "nope"}
    with pytest.raises(evolve_review.Skip, match="不是 models.yaml"):
        asyncio.run(evolve_review.review("diff", "rules", environ=env))


def test_review_sends_rules_and_diff_in_tags():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "【建议】补测试"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.01},
            },
        )

    env = {"EVOLVE_LLM_API_KEY": FAKE_KEY, "EVOLVE_REVIEW_MODEL": "review-model"}
    text, cost = asyncio.run(
        evolve_review.review(
            "+ new line", "规则 A", environ=env, transport=httpx.MockTransport(handler)
        )
    )
    assert text == "【建议】补测试" and cost == 0.01
    body = seen["body"]
    assert body["model"] == "review-model" and body["max_tokens"] == evolve_review.MAX_TOKENS
    user = body["messages"][-1]["content"]
    assert "<rules>\n规则 A" in user and "<diff>\n+ new line" in user
    assert seen["auth"] == f"Bearer {FAKE_KEY}"


def test_clip():
    assert evolve_review.clip("abc", 5, "x") == "abc"
    assert "截掉" in evolve_review.clip("abcdef", 3, "改动")
