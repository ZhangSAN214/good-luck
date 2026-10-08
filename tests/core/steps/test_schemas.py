from __future__ import annotations

import json

import pytest

from roundtable.core.config import ConfigError, load_config
from roundtable.core.steps import (
    check_pipelines,
    check_review,
    get_step,
    parse_reviews,
    parse_revision,
    parse_synthesis,
    register_step,
    step_names,
)
from roundtable.core.steps.schemas import PeerReview, _is_vague

Q = load_config().roundtable.review_quality


def review(**kw):
    return PeerReview.model_validate({"target": "组员乙", **kw})


ISSUE = {"location": "第 3 行", "problem": "符号错误", "suggestion": "改为减号"}


@pytest.mark.parametrize(
    "kw, valid",
    [
        ({"verdict": "partially_correct", "issues": [ISSUE]}, True),
        ({"verdict": "correct", "checked": "逐项核对了求导结果、驻点位置和两个端点的函数值"}, True),
        ({"verdict": "correct", "checked": "挺好"}, False),
        ({"verdict": "correct", "checked": "挺好！！没问题，同意。很棒 looks good"}, False),
        ({"verdict": "correct", "checked": "核对了"}, False),  # 太短
        ({"verdict": "correct"}, False),
        (
            {"verdict": "incorrect", "checked": "逐项核对了求导结果、驻点位置和两个端点的函数值"},
            False,
        ),
        ({"issues": [{"location": "", "problem": "不对", "suggestion": "改"}]}, False),
        ({"issues": [ISSUE, {"location": "", "problem": "x", "suggestion": "y"}]}, True),
    ],
)
def test_check_review(kw, valid):
    checked = check_review(review(**kw), "甲", "乙", Q)
    assert checked.valid is valid, checked.invalid_reasons


def test_incomplete_issues_are_dropped():
    checked = check_review(
        review(issues=[ISSUE, {"location": "", "problem": "x", "suggestion": "y"}]), "甲", "乙", Q
    )
    assert len(checked.issues) == 1


def test_is_vague():
    assert _is_vague("挺好！", ["挺好"]) and _is_vague("   ", [])
    assert not _is_vague("挺好，但第三步符号错了", ["挺好"])


def test_unknown_verdict_and_severity_normalized():
    r = review(verdict="perfect", issues=[{**ISSUE, "severity": "catastrophic"}])
    assert r.verdict == "unclear" and r.issues[0].severity == "minor"


def to_code(text):
    t = text.removeprefix("组员").strip()
    return t if t in ("甲", "乙", "丙") else None


def test_parse_reviews_filters_targets():
    text = json.dumps(
        {
            "reviews": [
                {"target": "组员乙", "issues": [ISSUE]},
                {"target": "组员乙", "issues": [ISSUE]},  # 重复
                {"target": "组员甲", "issues": [ISSUE]},  # 自评
                {"target": "组员戊", "issues": [ISSUE]},  # 未知
            ]
        },
        ensure_ascii=False,
    )
    parsed = parse_reviews(text, reviewer="甲", expected=["乙", "丙"], to_code=to_code, quality=Q)
    assert [r.target for r in parsed.reviews] == ["乙"]
    assert parsed.missing == ("丙",)
    assert parsed.ignored == ("组员乙", "组员甲", "组员戊")


@pytest.mark.parametrize("text", ["不是 JSON", '{"reviews": "x"}', "{}"])
def test_parse_reviews_errors(text):
    with pytest.raises(ValueError):
        parse_reviews(text, reviewer="甲", expected=["乙"], to_code=to_code, quality=Q)


def test_parse_revision():
    r = parse_revision("前言\n## 修订后的答案\n新答案\n\n## 对审阅意见的回应\n采纳第 1 条")
    assert (r.answer, r.responses) == ("新答案", "采纳第 1 条")
    assert parse_revision("## 修订后的答案\n只有答案").responses == ""
    assert parse_revision("没有标题") is None
    assert parse_revision("## 修订后的答案\n\n## 对审阅意见的回应\n只有回应") is None


def test_parse_synthesis():
    text = json.dumps(
        {
            "final_answer": "答案",
            "confidence": "VERY HIGH",
            "disagreements": [
                {
                    "point": "a",
                    "positions": [{"members": ["组员甲", "乙", "某人"]}],
                    "resolved": True,
                },
                {"point": "b"},
            ],
        },
        ensure_ascii=False,
    )
    s = parse_synthesis(text, to_code)
    assert s.output.confidence == "medium"
    assert s.output.disagreements[0].positions[0].members == ["甲", "乙"]
    assert s.unresolved_disagreements == 1  # 没写 resolved 视为未解决
    with pytest.raises(ValueError):
        parse_synthesis('{"final_answer": ""}', to_code)


def test_registry():
    assert step_names() == sorted(
        [
            "answer",
            "reveal",
            "review",
            "revise",
            "synthesize",
            "decompose",
            "volunteer",
            "assign",
            "work",
            "cross_review",
            "rework",
            "merge",
        ]
    )
    with pytest.raises(ValueError):
        register_step(get_step("answer"))
    with pytest.raises(KeyError, match="debate"):
        get_step("debate")


def test_check_pipelines():
    cfg = load_config()
    check_pipelines(cfg)
    bad_rt = cfg.roundtable.model_copy(update={"pipeline": ["answer", "debate", "synthesize"]})
    with pytest.raises(ConfigError, match="debate"):
        check_pipelines(cfg.model_copy(update={"roundtable": bad_rt}))
    plans = dict(cfg.routing.plans)
    plans["budget"] = plans["budget"].model_copy(update={"pipeline": ["answer", "poll"]})
    bad_routing = cfg.routing.model_copy(update={"plans": plans})
    with pytest.raises(ConfigError, match="poll"):
        check_pipelines(cfg.model_copy(update={"routing": bad_routing}))
