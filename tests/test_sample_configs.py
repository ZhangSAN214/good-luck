"""示例配置的基本一致性检查（完整的 schema 校验在阶段 1 实现）。"""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "config"


def load(name: str) -> dict:
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))


def test_models_yaml_is_consistent():
    data = load("models.yaml")
    vocabulary = set(data["tag_vocabulary"])
    models = data["models"]
    ids = [m["id"] for m in models]
    assert len(ids) == len(set(ids)), "模型 id 必须唯一"
    for m in models:
        assert {"id", "provider", "model", "vendor", "price", "tags", "enabled"} <= m.keys()
        assert set(m["tags"]) <= vocabulary, f"{m['id']} 含未知标签"
        assert m["price"]["input"] >= 0 and m["price"]["output"] >= 0


def test_enough_models_for_default_table():
    models = [m for m in load("models.yaml")["models"] if m["enabled"]]
    rt = load("roundtable.yaml")
    assert len(models) >= rt["seats"] + 1, "默认配置应能坐满组员并选出统筹"


def test_roundtable_yaml_defaults():
    rt = load("roundtable.yaml")
    assert rt["budget"] == {"total_usd": 20.0, "warn_ratio": 0.8}
    assert rt["pipeline"] == ["answer", "review", "revise", "synthesize", "reveal"]
    assert set(rt["prompts"]) == {"answer", "review", "revise", "synthesize"}


def test_personas_has_enough_codes():
    personas = load("personas.yaml")
    rt = load("roundtable.yaml")
    assert len(set(personas["codes"])) >= rt["seats"]
