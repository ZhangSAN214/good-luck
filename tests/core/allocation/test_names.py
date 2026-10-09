"""成员称呼：匿名 = 塔罗牌代号；匿名关闭 = 昵称·模式。"""

from __future__ import annotations

import random

import pytest

from roundtable.core.allocation import coordinator_name, display_name, member_codes
from roundtable.core.config import load_config

CFG = load_config()
TAROT = [
    "愚者", "魔术师", "女祭司", "皇后", "皇帝", "教皇", "恋人", "战车", "力量", "隐者", "命运之轮",
    "正义", "倒吊人", "死神", "节制", "恶魔", "高塔", "星星", "月亮", "太阳", "审判", "世界",
]  # fmt: skip
SEATED = [m.id for m in CFG.models.models if m.seat and m.enabled]


def test_code_pool_is_the_22_major_arcana():
    assert CFG.personas.codes == TAROT
    assert len(CFG.personas.codes) >= CFG.roundtable.seats


def test_vendor_nicknames_and_tier_names_from_config():
    p = CFG.personas
    assert p.nicknames == {
        "OpenAI": "小白龙",
        "Anthropic": "克劳德",
        "xAI": "歌若柯",
        "DeepSeek": "鲸鱼娘",
        "Google": "双子星",
        "Alibaba": "千问",
    }
    assert p.tier_labels == {"flagship": "全力", "budget": "节电"}
    assert display_name("deepseek-v4-pro", CFG) == "鲸鱼娘·全力"
    assert display_name("deepseek-v4.1-flash", CFG) == "鲸鱼娘·节电"
    assert display_name("gpt-6-sol", CFG) == "小白龙·全力"
    assert coordinator_name("claude-haiku-4.5", CFG) == "克劳德·节电（统筹）"


def test_every_seated_model_has_a_distinct_display_name():
    names = [display_name(m, CFG) for m in SEATED]
    assert len(set(names)) == len(names)
    assert all("·" in n for n in names)


def test_plan_labels_are_modes():
    assert CFG.routing.plans["flagship"].label == "全力模式"
    assert CFG.routing.plans["budget"].label == "节电模式"


def test_anonymous_codes_are_random_tarot_cards():
    seen = set()
    for seed in range(30):
        codes = member_codes(SEATED, CFG, random.Random(seed), anonymous=True)
        assert set(codes.values()) == set(SEATED)
        assert set(codes) <= set(TAROT) and len(codes) == len(SEATED)
        seen.add(tuple(codes))
    assert len(seen) > 20  # 不同种子的抽牌与座次不同


def test_anonymous_codes_reveal_nothing():
    names = {n for n in CFG.personas.nicknames.values()}
    vendors = {m.vendor for m in CFG.models.models}
    for seed in range(10):
        for code in member_codes(SEATED, CFG, random.Random(seed), anonymous=True):
            assert code not in names and code not in vendors


def test_nickname_codes_follow_model_not_seat():
    codes = member_codes(SEATED, CFG, random.Random(1), anonymous=False)
    assert {c: m for c, m in codes.items()} == {display_name(m, CFG): m for m in SEATED}


def test_same_seed_same_codes():
    a = member_codes(SEATED, CFG, random.Random("s:codes:0"), anonymous=True)
    b = member_codes(SEATED, CFG, random.Random("s:codes:0"), anonymous=True)
    assert a == b and list(a) == list(b)


def test_duplicate_vendor_and_tier_get_a_number():
    cfg = CFG.model_copy(deep=True)
    twin = next(m for m in cfg.models.models if m.id == "deepseek-v4-pro").model_copy(
        update={"id": "deepseek-v4-pro-b"}
    )
    cfg.models.models.append(twin)
    codes = member_codes(
        ["deepseek-v4-pro", "deepseek-v4-pro-b"], cfg, random.Random(0), anonymous=False
    )
    assert set(codes) == {"鲸鱼娘·全力", "鲸鱼娘·全力2"}


def test_unknown_vendor_falls_back_to_vendor_name():
    cfg = CFG.model_copy(deep=True)
    cfg.personas.nicknames.pop("xAI")
    assert display_name("grok-4.7", cfg) == "xAI·全力"


def test_too_many_models_for_the_pool():
    with pytest.raises(ValueError, match="代号池"):
        member_codes([f"m{i}" for i in range(23)], CFG, random.Random(0), anonymous=True)


def test_nicknames_must_be_distinct_and_not_collide_with_codes():
    from roundtable.core.config.schema import PersonasConfig

    with pytest.raises(ValueError, match="昵称不能重复"):
        PersonasConfig(codes=["甲", "乙"], nicknames={"A": "同名", "B": "同名"})
    with pytest.raises(ValueError, match="匿名代号"):
        PersonasConfig(codes=["甲", "乙"], nicknames={"A": "甲"})
