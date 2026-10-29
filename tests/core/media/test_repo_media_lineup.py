"""仓库里的视频 / 语音 / 音乐阵容（2026-10-29 换★模型）：两档都有、节电档单价上限、
旧模型留作备用。"""

from __future__ import annotations

import pytest

from roundtable.core.config import load_config
from roundtable.core.media import KIND_TAGS, unit_cost
from roundtable.core.media.pricing import speech_cost

# 节电档的单价上限：视频每秒、语音每字符、音乐每段
BUDGET_MAX = {"video": 0.05, "speech": 0.00003, "music": 0.05}


def lineup(kind):
    return [m for m in load_config().models.enabled if KIND_TAGS[kind] in m.tags]


@pytest.mark.parametrize("kind", ["video", "speech", "music"])
def test_both_tiers_covered_and_seatless(kind):
    models = lineup(kind)
    assert {m.tier for m in models} == {"budget", "flagship"}
    for m in models:
        priced = m.media_price_for(m.routes[0]) is not None or m.price is not None
        assert not m.seat and m.routes and priced


@pytest.mark.parametrize("kind", ["video", "speech", "music"])
def test_budget_tier_stays_cheap(kind):
    amount = {"video": {"seconds": 1}, "music": {"songs": 1}}.get(kind, {})
    for m in lineup(kind):
        if m.tier == "budget":
            cost = speech_cost(m, None, 1) if kind == "speech" else unit_cost(m, **amount)
            assert cost <= BUDGET_MAX[kind] + 1e-12, m.id


def test_video_and_music_have_one_default_per_tier():
    for kind in ("video", "music"):
        for tier in ("budget", "flagship"):
            defaults = [m for m in lineup(kind) if m.tier == tier and m.default]
            assert len(defaults) <= 1, (kind, tier)


def test_replaced_models_are_kept_as_disabled_backups():
    models = {m.id: m for m in load_config().models.models}
    for old in ("alibaba-wan-video", "veo-3.1", "qwen-audio-3.0-tts-flash"):
        assert old in models and not models[old].enabled
