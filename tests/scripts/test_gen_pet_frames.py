"""scripts/gen_pet_frames.py 的测试：不联网，用假的图像提供方。"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen_pet_frames.py"
spec = importlib.util.spec_from_file_location("gen_pet_frames", SCRIPT)
tool = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tool
spec.loader.exec_module(tool)


class FakeProvider:
    def __init__(self, cost=0.1):
        self.cost = cost
        self.prompts = []

    async def generate_image(self, model, prompt, params, images=None, api=None):
        self.prompts.append(prompt)
        img = SimpleNamespace(mime="image/png", data=b"png")
        return SimpleNamespace(images=[img], cost_usd=self.cost)


class FakeModel:
    def image_api_for(self, route):
        return "chat"

    def params_for(self, route):
        return {}


def args(tmp_path, frames, max_usd=1.0):
    return argparse.Namespace(
        frame=frames, out=str(tmp_path), member="whale", action="sign", extra="", max_usd=max_usd
    )


def run(a, provider):
    route = SimpleNamespace(model="m")
    return asyncio.run(tool.generate(a, provider, FakeModel(), route, []))


def test_parse_frames_sorted_and_validated():
    assert tool.parse_frames(["k1=b", "k0=a"]) == [(0, "a"), (1, "b")]
    with pytest.raises(ValueError):
        tool.parse_frames(["bad"])
    with pytest.raises(ValueError):
        tool.parse_frames(["k0=a", "k0=b"])


def test_prompt_has_rules_and_no_text_request():
    p = tool.build_prompt("双臂高举", "牌子是木板")
    assert "双臂高举" in p and "牌子是木板" in p
    assert "不要画文字" in p and "92%" in p


def test_writes_named_files_and_skips_existing(tmp_path):
    (tmp_path / "whale_sign_k0.png").write_bytes(b"old")
    provider = FakeProvider()
    run(args(tmp_path, ["k0=a", "k1=b"]), provider)
    assert (tmp_path / "whale_sign_k0.png").read_bytes() == b"old"
    assert (tmp_path / "whale_sign_k1.png").read_bytes() == b"png"
    assert len(provider.prompts) == 1


def test_stops_at_cost_limit(tmp_path):
    provider = FakeProvider(cost=0.3)
    run(args(tmp_path, ["k0=a", "k1=b", "k2=c", "k3=d"], max_usd=0.5), provider)
    assert len(provider.prompts) == 2
    assert not (tmp_path / "whale_sign_k2.png").exists()


def test_missing_key_reports(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"x")
    rc = tool.main(
        [
            "--ref",
            str(ref),
            "--member",
            "w",
            "--action",
            "sign",
            "--frame",
            "k0=a",
            "--out",
            str(tmp_path / "o"),
        ]
    )
    assert rc == 1
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err
