from __future__ import annotations

import pytest

from roundtable.core.config.schema import TriageRule
from roundtable.core.routing import Question, triage

from .conftest import REPO

RULES = REPO.routing.triage


@pytest.mark.parametrize(
    "text, difficulty, rule",
    [
        ("1+1=?", "simple", "very_short"),
        ("法国的首都是哪里？", "simple", "very_short"),
        ("证明根号2是无理数", None, None),  # 短，但含"证明"
        ("Why is the sky blue?", None, None),  # 含 why
        ("x" * 1600, "hard", "very_long"),
        ("求函数 f(x)=x^3-3x 在区间 [-2, 2] 上的最大值与最小值，并写出过程。", None, None),
    ],
)
def test_repo_rules(text, difficulty, rule):
    r = triage(Question(text), RULES)
    assert (r.difficulty, r.difficulty_rule) == (difficulty, rule)


def test_image_requires_vision_and_keeps_going():
    r = triage(Question("这张图里是什么？", attachments=("image",)), RULES)
    assert r.require_tags == ("vision",)
    assert r.difficulty == "simple"
    assert r.matched == ("has_image", "very_short")


def make(rules):
    return [TriageRule.model_validate(x) for x in rules]


def test_first_difficulty_wins_but_tags_accumulate():
    rules = make(
        [
            {"name": "a", "when": {"max_chars": 100}, "then": {"difficulty": "simple"}},
            {
                "name": "b",
                "when": {"any_keywords": ["代码"]},
                "then": {"difficulty": "hard", "require_tags": ["code"], "task_type": "code"},
            },
        ]
    )
    r = triage(Question("写一段代码"), rules)
    assert (r.difficulty, r.difficulty_rule) == ("simple", "a")
    assert r.require_tags == ("code",) and r.task_type == "code"
    assert r.matched == ("a", "b")


def test_conditions_are_anded_and_case_insensitive():
    rules = make(
        [
            {
                "name": "x",
                "when": {"min_chars": 3, "max_chars": 20, "any_keywords": ["PROVE"]},
                "then": {"difficulty": "hard"},
            }
        ]
    )
    assert triage(Question("please prove it"), rules).difficulty == "hard"
    assert triage(Question("pr"), rules).difficulty is None  # 太短
    assert triage(Question("please show it"), rules).difficulty is None


def test_no_rules_no_decision():
    r = triage(Question("anything"), [])
    assert r.difficulty is None and r.require_tags == () and r.matched == ()
