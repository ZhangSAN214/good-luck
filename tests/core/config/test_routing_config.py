"""routing.yaml 的校验。"""

from __future__ import annotations

import pytest

from roundtable.core.config import load_config

from .test_loader import config_dir, edit, expect_error  # noqa: F401 - fixture


def test_repo_routing_defaults():
    r = load_config().routing
    assert r.confirm_threshold_usd == 0.30
    assert set(r.plans) == {"budget", "flagship"}
    assert r.default_plan == "budget"
    assert r.plans["budget"].tiers == ["budget"] and r.plans["flagship"].tiers == ["flagship"]
    assert r.plans["budget"].escalate_to == "flagship"
    assert r.plans["flagship"].escalate_to is None
    assert r.custom.label == "自选"


def test_repo_routing_mentions_no_models_or_vendors():
    """规则只看档位和标签：routing.yaml 中不得出现任何模型 id、厂商、别称或渠道上的模型名。"""
    cfg = load_config()
    from roundtable.core.config.loader import DEFAULT_CONFIG_DIR

    text = (DEFAULT_CONFIG_DIR / "routing.yaml").read_text(encoding="utf-8").lower()
    terms = set()
    for m in cfg.models.models:
        terms |= {m.id, m.vendor, *m.aliases, *(r.model for r in m.routes)}
    found = sorted(t for t in terms if t.lower() in text)
    assert found == []


@pytest.mark.parametrize(
    "change, fragment",
    [
        (lambda d: d.update(default_plan="giant"), "default_plan"),
        (lambda d: d["plans"]["budget"].update(escalate_to="giant"), "不存在的档位"),
        (lambda d: d["plans"]["flagship"].update(escalate_to="budget"), "成环"),
        (lambda d: d["plans"]["budget"].update(tiers=["budget", "budget"]), "不能重复"),
        (lambda d: d["plans"]["budget"].update(tiers=["premium"]), "tiers"),
        (lambda d: d["plans"]["budget"].update(tiers=[]), "tiers"),
        (lambda d: d["plans"].update(custom={"label": "x", "tiers": ["budget"]}), "保留名"),
        (lambda d: d["plans"]["budget"].update(members={"tiers": ["budget"]}), "members"),
        (lambda d: d.update(presets={}), "presets"),
        (lambda d: d.update(confirm_threshold_usd=-1), "confirm_threshold_usd"),
        (lambda d: d.update(default_difficulty="extreme"), "default_difficulty"),
        (
            lambda d: d["triage"].append({"name": "x", "when": {}, "then": {"difficulty": "hard"}}),
            "条件",
        ),
        (lambda d: d["triage"].append({"name": "x", "when": {"max_chars": 5}, "then": {}}), "动作"),
        (lambda d: d["triage"].append(dict(d["triage"][0])), "规则名不能重复"),
        (lambda d: d["estimate"]["answer_tokens"].pop("hard"), "缺少难度"),
        (lambda d: d["escalation"].update(confidence=["unsure"]), "confidence"),
    ],
)
def test_invalid_routing(config_dir, change, fragment):  # noqa: F811
    edit(config_dir, "routing.yaml", change)
    expect_error(config_dir, fragment)


@pytest.mark.parametrize(
    "change, fragment",
    [
        (lambda d: d["triage"][0]["then"].update(require_tags=["telepathy"]), "未知标签"),
        (lambda d: d["triage"][0]["then"].update(task_type="cooking"), "task_type"),
        (lambda d: d["plans"]["budget"].update(prompt_roles={"answer": "ghost"}), "ghost"),
    ],
)
def test_routing_cross_checks(config_dir, change, fragment):  # noqa: F811
    edit(config_dir, "routing.yaml", change)
    expect_error(config_dir, "配置交叉校验", fragment)


def test_planner_prompt_version_required(config_dir):  # noqa: F811
    edit(config_dir, "roundtable.yaml", lambda d: d["prompts"].pop("planner"))
    expect_error(config_dir, "planner")
