"""routing.yaml 的校验。"""

from __future__ import annotations

import pytest

from roundtable.core.config import load_config

from .test_loader import config_dir, edit, expect_error  # noqa: F401 - fixture


def test_repo_routing_defaults():
    r = load_config().routing
    assert r.confirm_threshold_usd == 0.20
    assert set(r.plans) == {"simple", "medium", "hard"}
    assert r.difficulty_plans == {"simple": "simple", "medium": "medium", "hard": "hard"}
    assert set(r.presets) == {"saver", "balanced", "strongest"}
    assert r.plans["medium"].escalate_to == "hard"
    assert r.plans["simple"].coordinator is None


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
        (lambda d: d["difficulty_plans"].pop("hard"), "缺少难度"),
        (lambda d: d["difficulty_plans"].update(hard="giant"), "不存在的方案"),
        (lambda d: d["presets"]["saver"].update(plan="giant"), "不存在的方案"),
        (lambda d: d["plans"]["hard"].update(escalate_to="medium"), "成环"),
        (lambda d: d["plans"]["medium"]["members"].update(min=4, max=3), "min 不能大于"),
        (lambda d: d["plans"]["medium"]["members"].update(tiers=["budget", "budget"]), "不能重复"),
        (lambda d: d["plans"]["medium"]["members"].update(tiers=["premium"]), "tiers"),
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
        (lambda d: d["plans"]["hard"]["members"].update(max=9), "超过座位数"),
        (lambda d: d["plans"]["medium"].update(coordinator=None), "coordinator"),
        (lambda d: d["plans"]["simple"].update(coordinator={"tiers": ["budget"]}), "coordinator"),
        (lambda d: d["plans"]["medium"]["members"].update(min=1), "至少为 2"),
        (lambda d: d["triage"][0]["then"].update(require_tags=["telepathy"]), "未知标签"),
        (lambda d: d["triage"][0]["then"].update(task_type="cooking"), "task_type"),
    ],
)
def test_routing_cross_checks(config_dir, change, fragment):  # noqa: F811
    edit(config_dir, "routing.yaml", change)
    expect_error(config_dir, "配置交叉校验", fragment)


def test_planner_prompt_version_required(config_dir):  # noqa: F811
    edit(config_dir, "roundtable.yaml", lambda d: d["prompts"].pop("planner"))
    expect_error(config_dir, "planner")
