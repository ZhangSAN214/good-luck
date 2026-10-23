"""模型人才市场：对照聚合渠道的公开模型列表，只提示，不改配置。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.catalog import STALE_AFTER_S, ModelCatalog, aggregator_channel, analyze
from roundtable.core.config import Route

from .routing.conftest import app_config

DAY = 24 * 3600


def config():
    """合成模型池，路由 id 改成「厂商/模型」的形式。"""
    cfg = app_config()
    models = [
        m.model_copy(update={"routes": [Route(channel="c", model=f"{m.vendor}/{m.id}")]})
        for m in cfg.models.models
    ]
    return cfg.model_copy(update={"models": cfg.models.model_copy(update={"models": models})})


def entry(rid, pin, pout, created=100, inputs=("text",), outputs=("text",), name=None):
    return {
        "id": rid,
        "name": name or rid,
        "created": created,
        "context_length": 1000,
        "pricing": {"prompt": str(pin / 1e6), "completion": str(pout / 1e6)},
        "architecture": {"input_modalities": list(inputs), "output_modalities": list(outputs)},
    }


def remote(cfg, *extra, skip=(), prices=None):
    data = []
    for m in cfg.models.models:
        rid = m.routes[0].model
        if m.id in skip:
            continue
        pin, pout = (prices or {}).get(m.id, (m.price.input, m.price.output))
        data.append(entry(rid, pin, pout, created=100))
    return {"data": data + list(extra)}


def test_price_changes_and_missing_routes():
    cfg = config()
    report = analyze(cfg, remote(cfg, skip=("f5",), prices={"b1": (0.10, 0.60)}), source="c")
    assert [(p.model_id, p.configured, p.actual) for p in report.price_changes] == [
        ("b1", (0.10, 0.40), (0.10, 0.60))
    ]
    assert report.missing == [{"model_id": "f5", "route": "V6/f5"}]
    assert report.candidates == []


def test_newer_model_of_a_configured_family_is_suggested_with_tier():
    cfg = config()
    report = analyze(
        cfg,
        remote(
            cfg,
            entry("V1/b1-next", 0.12, 0.5, created=200, inputs=("text", "image")),
            entry("V1/old", 0.1, 0.4, created=50),  # 比已配置的旧
            entry("V1/b1-next:free", 0, 0, created=300),  # 变体
            entry("V9/x", 0.1, 0.4, created=300),  # 没配置过的系列
            entry("V1/painter", 0.1, 0.4, created=300, outputs=("image",)),  # 不输出文字
            entry("V1/ultra", 50, 100, created=250),  # 比所有档位都贵得多
        ),
        source="c",
    )
    got = {c.route: c for c in report.candidates}
    assert list(got) == ["V1/ultra", "V1/b1-next"]  # 新的在前
    nxt = got["V1/b1-next"]
    assert nxt.tier == "budget" and nxt.replaces == ["b1"] and nxt.vision
    assert nxt.price == (0.12, 0.5)
    assert got["V1/ultra"].tier is None and got["V1/ultra"].replaces == []


def test_flagship_priced_newcomer_replaces_the_flagship_of_its_family():
    cfg = config()
    report = analyze(cfg, remote(cfg, entry("V2/next", 2.0, 11.0, created=200)), source="c")
    (c,) = report.candidates
    assert c.tier == "flagship" and c.replaces == ["f2"]


def test_at_most_three_per_family():
    cfg = config()
    extra = [entry(f"V1/n{i}", 0.1, 0.4, created=200 + i) for i in range(5)]
    report = analyze(cfg, remote(cfg, *extra), source="c")
    assert [c.route for c in report.candidates] == ["V1/n4", "V1/n3", "V1/n2"]


def test_renaming_vendors_changes_nothing_but_names():
    """无品牌偏好：换掉系列名，结果结构不变。"""
    cfg = config()
    a = analyze(cfg, remote(cfg, entry("V1/n", 0.1, 0.4, created=200)), source="c", now=1)
    renamed = [
        m.model_copy(
            update={"routes": [Route(channel="c", model=m.routes[0].model.replace("V1/", "Z/"))]}
        )
        for m in cfg.models.models
    ]
    cfg2 = cfg.model_copy(update={"models": cfg.models.model_copy(update={"models": renamed})})
    b = analyze(cfg2, remote(cfg2, entry("Z/n", 0.1, 0.4, created=200)), source="c", now=1)
    same = [(c.tier, c.replaces) for c in b.candidates]
    assert [(c.tier, c.replaces) for c in a.candidates] == same


def test_aggregator_channel_is_taken_from_config():
    assert aggregator_channel(config()) == ("c", "https://c.test/models")


async def test_catalog_caches_to_file_and_goes_stale(tmp_path):
    cfg = config()
    now = [1000.0]
    urls = []

    async def fetch(url):
        urls.append(url)
        return remote(cfg, entry("V1/n", 0.1, 0.4, created=200))

    path = tmp_path / "data" / "model_candidates.json"
    cat = ModelCatalog(cfg, path, fetch, clock=lambda: now[0])
    assert cat.view() == {"fetched_at": None, "stale": True, "available": True}
    view = await cat.refresh()
    assert urls == ["https://c.test/models"]
    assert view["stale"] is False and view["candidates"][0]["route"] == "V1/n"
    assert json.loads(path.read_text(encoding="utf-8"))["fetched_at"] == 1000.0
    # 重新打开：从文件读出；过了一天算过期
    now[0] += STALE_AFTER_S + 1
    again = ModelCatalog(cfg, path, fetch, clock=lambda: now[0]).view()
    assert again["stale"] is True and again["candidates"][0]["route"] == "V1/n"


async def test_catalog_without_aggregator_cannot_refresh():
    cfg = config()
    channels = {"c": cfg.models.channels["c"].model_copy(update={"kind": "direct"})}
    cfg = cfg.model_copy(update={"models": cfg.models.model_copy(update={"channels": channels})})
    cat = ModelCatalog(cfg, None)
    assert cat.view()["available"] is False
    with pytest.raises(LookupError):
        await cat.refresh()
