"""讨论模式下题目需要多个媒体文件时，提交前建议切换到协同模式。"""

from __future__ import annotations

import pytest

from roundtable.core.config.schema import MultiMediaRule
from roundtable.core.routing import collab_advice, detect_multi_media

RULE = MultiMediaRule(
    nouns=["图", "图片", "插画", "卡片", "卡", "视频", "语音", "海报", "头像"],
    classifiers=["张", "个", "段", "格", "套"],
    hints=["多张", "拼图", "拼接", "拼成", "四格", "一套", "分镜"],
)


@pytest.mark.parametrize(
    "text, count",
    [
        ("画四张图，然后拼成一张", 4),
        ("生成3个短视频", 3),
        ("把这十二张插画合成长图", 12),
        ("给我两段语音", 2),
        ("每人一张，一共 5 张不同风格的海报", 5),
        ("画 3 张图", 3),
    ],
)
def test_counts_are_detected(text, count):
    hint = detect_multi_media(text, RULE)
    assert hint is not None and hint.count == count


@pytest.mark.parametrize(
    "text",
    ["画多张图并拼图", "做一张四格人物介绍卡", "生成一套头像", "先画分镜再拼接成视频"],
)
def test_hints_without_a_number_are_detected(text):
    hint = detect_multi_media(text, RULE)
    assert hint is not None and hint.count is None


@pytest.mark.parametrize(
    "text",
    ["请画一只猫", "生成一张海报", "写一首诗", "一张图片里有两只猫", "解释拼接字符串的方法", ""],
)
def test_single_or_unrelated_requests_are_not_flagged(text):
    assert detect_multi_media(text, RULE) is None


def test_disabled_or_missing_rule_never_flags():
    assert detect_multi_media("画四张图", None) is None
    assert detect_multi_media("画四张图", RULE.model_copy(update={"enabled": False})) is None


def test_advice_is_json_friendly_and_names_the_trigger():
    hint = detect_multi_media("画四张图，然后拼成一张", RULE)
    advice = collab_advice(hint)
    assert advice["suggest_workflow"] == "collab" and advice["applies_to"] == "discussion"
    assert (
        advice["count"] == 4 and "四张图" in advice["message"] and "协同模式" in advice["message"]
    )


def test_repo_config_has_a_working_rule():
    from roundtable.core.config import load_config

    rule = load_config().routing.multi_media
    assert rule is not None
    assert detect_multi_media("做一张四格人物介绍卡，四个角色", rule) is not None
    assert detect_multi_media("1+1 等于几", rule) is None
