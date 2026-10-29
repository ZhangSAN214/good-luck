"""模型图鉴：聚合渠道上的全部模型，分类、打标签、标出已在圆桌里的；只浏览，不改配置。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.model_directory import (
    CATEGORIES,
    STALE_AFTER_S,
    ModelDirectory,
    build_directory,
    categorize,
    tags_of,
)

from .test_catalog import config, entry


def arch(outputs, inputs=("text",), **extra):
    return {
        "architecture": {"input_modalities": list(inputs), "output_modalities": list(outputs)},
        **extra,
    }


@pytest.mark.parametrize(
    ("remote", "category"),
    [
        (arch(["text"]), "text"),
        ({}, "text"),
        (arch(["image", "text"]), "image"),
        (arch(["video"], ["text", "image"]), "video"),
        (arch(["speech"]), "speech"),
        (arch(["audio"], supported_voices=["a"]), "speech"),
        (arch(["audio"]), "audio"),
        (arch(["text", "audio"], ["text", "audio"]), "voice_chat"),
        (arch(["transcription"], ["audio"]), "transcription"),
        (arch(["embeddings"]), "embeddings"),
        (arch(["rerank"]), "rerank"),
        (arch(["decisions"]), "other"),
    ],
)
def test_categorize_by_output_modalities(remote, category):
    assert categorize(remote) == category
    assert category in CATEGORIES


def test_tags_from_inputs_params_context_and_price():
    r = {
        "id": "x/m:free",
        "context_length": 1_000_000,
        "supported_parameters": ["tools", "reasoning", "response_format"],
        "pricing": {"prompt": "0", "completion": "0"},
        **arch(["text"], ["text", "image", "audio", "video", "file"]),
    }
    assert tags_of(r) == [
        "free",
        "vision",
        "audio_in",
        "video_in",
        "file_in",
        "tools",
        "reasoning",
        "structured",
        "long_context",
        "huge_context",
        "variant",
    ]
    paid = {"id": "x/m", "context_length": 1000, "pricing": {"prompt": "0", "request": "0.04"}}
    assert tags_of(paid) == []


def test_build_directory_marks_configured_and_sorts_by_category():
    cfg = config()
    first = cfg.models.models[0]
    data = {
        "data": [
            entry(first.routes[0].model, first.price.input, first.price.output, created=5),
            {
                **entry("new/painter", 0, 0, created=9, outputs=("image",)),
                "pricing": {"prompt": "0", "completion": "0", "image_output": "0.02"},
                "description": "word " * 200,
            },
            entry("new/chat", 0.2, 0.8, created=7),
            entry("new/chat", 0.2, 0.8, created=7),  # 重复的只算一次
        ]
    }
    d = build_directory(cfg, data, source="c", now=1.0)
    assert [e.route for e in d.entries] == ["new/chat", first.routes[0].model, "new/painter"]
    chat, mine, painter = d.entries
    assert chat.in_roundtable is None and chat.price_band == "cheap" and chat.author == "new"
    assert mine.in_roundtable == {
        "model_id": first.id,
        "seat": first.seat,
        "enabled": first.enabled,
        "tier": first.tier,
    }
    assert painter.category == "image" and painter.price_band is None
    assert painter.extra_prices == {"image_output": 0.02}
    assert painter.suggested_tier is None and len(painter.description) <= 240
    out = d.to_dict()
    assert out["total"] == 3
    counts = {c["key"]: c["count"] for c in out["categories"]}
    assert counts["text"] == 2 and counts["image"] == 1 and counts["video"] == 0
    json.dumps(out)


async def test_refresh_caches_to_file_and_reports_staleness(tmp_path):
    cfg = config()
    now = [1000.0]
    urls = []

    async def fetch(url):
        urls.append(url)
        return {"data": [entry("new/chat", 0.2, 0.8)]}

    path = tmp_path / "dir.json"
    d = ModelDirectory(cfg, path, fetch, clock=lambda: now[0])
    assert d.view() == {"fetched_at": None, "stale": True, "available": True}
    view = await d.refresh()
    assert urls[0].endswith("/models?output_modalities=all")
    assert view["stale"] is False and view["total"] == 1 and view["source"] == "c"
    now[0] += STALE_AFTER_S + 1
    again = ModelDirectory(cfg, path, fetch, clock=lambda: now[0])
    assert again.view()["stale"] is True and again.view()["total"] == 1


async def test_no_aggregator_channel():
    cfg = config()
    channels = {k: v.model_copy(update={"kind": "direct"}) for k, v in cfg.models.channels.items()}
    cfg = cfg.model_copy(update={"models": cfg.models.model_copy(update={"channels": channels})})
    d = ModelDirectory(cfg, None)
    assert d.view()["available"] is False
    with pytest.raises(LookupError):
        await d.refresh()
