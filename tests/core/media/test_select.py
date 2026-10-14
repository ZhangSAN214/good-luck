"""媒体模型的选择：只看能力标签和档位，同档内随机，换名不影响结果。"""

from __future__ import annotations

import random

from roundtable.core.config import ModelsConfig
from roundtable.core.media import media_group, pick_media_model, pick_stt_model
from roundtable.core.providers import ChannelRouter, FakeProvider

from ..routing.conftest import models_config


def router_for(cfg: ModelsConfig) -> ChannelRouter:
    return ChannelRouter(cfg, {"c": FakeProvider("c")})


def add_budget_image(cfg: ModelsConfig, mid: str) -> ModelsConfig:
    extra = cfg.models[0].model_copy(update={"id": mid, "vendor": f"V-{mid}"})
    return cfg.model_copy(update={"models": [*cfg.models, extra]})


def test_tier_then_untiered_then_other_tier():
    router = router_for(models_config(with_media=True))
    assert [m.id for m in media_group(router, "image", "budget")] == ["mi1"]
    assert [m.id for m in media_group(router, "image", "flagship")] == ["mi2"]
    assert [m.id for m in media_group(router, "speech", "flagship")] == [
        "mt1"
    ]  # 没有该档，退到另一档
    assert media_group(router, "video", "budget")[0].id == "mv1"


def test_seat_models_are_never_media_models():
    cfg = models_config(with_media=True)
    seated = next(m for m in cfg.models if m.seat and "vision" in m.tags)
    tagged = seated.model_copy(update={"tags": [*seated.tags, "image_gen"]})
    cfg = cfg.model_copy(
        update={"models": [tagged if m.id == seated.id else m for m in cfg.models]}
    )
    assert seated.id not in {m.id for m in media_group(router_for(cfg), "image", "budget")}


def test_unavailable_models_are_skipped():
    cfg = models_config(with_media=True)
    router = ChannelRouter(cfg, {})  # 没有任何可用渠道
    assert media_group(router, "image", "budget") == []
    assert pick_media_model(router, "image", "budget", random.Random(1)) is None
    assert pick_stt_model(router, random.Random(1)) is None


def test_random_within_a_tier_is_uniform_and_reproducible():
    cfg = add_budget_image(models_config(with_media=True), "mi1b")
    router = router_for(cfg)
    picks = [pick_media_model(router, "image", "budget", random.Random(s)).id for s in range(200)]
    assert set(picks) == {"mi1", "mi1b"} and 60 < picks.count("mi1") < 140
    again = [pick_media_model(router, "image", "budget", random.Random(s)).id for s in range(200)]
    assert picks == again


def test_renaming_models_and_vendors_does_not_change_the_choice():
    cfg = add_budget_image(models_config(with_media=True), "mi1b")
    renamed = cfg.model_copy(
        update={
            "models": [
                m.model_copy(
                    update={
                        "id": f"zz-{i}",
                        "vendor": f"Other{i}",
                        "routes": [r.model_copy(update={"model": f"zz-{i}"}) for r in m.routes],
                    }
                )
                for i, m in enumerate(cfg.models)
            ]
        }
    )
    order = {m.id: i for i, m in enumerate(cfg.models)}
    new_order = {m.id: i for i, m in enumerate(renamed.models)}
    for seed in range(50):
        a = pick_media_model(router_for(cfg), "image", "budget", random.Random(seed))
        b = pick_media_model(router_for(renamed), "image", "budget", random.Random(seed))
        assert order[a.id] == new_order[b.id]


def test_stt_prefers_the_cheapest_tier():
    router = router_for(models_config(with_media=True))
    assert pick_stt_model(router, random.Random(0)).id == "ms1"


def test_prefer_tags_then_default_flag_then_random():
    cfg = models_config(with_media=True)
    tts = next(m for m in cfg.models if "tts" in m.tags)
    zh = tts.model_copy(update={"id": "mt-zh", "tags": ["tts", "zh"], "vendor": "VZ"})
    third = tts.model_copy(update={"id": "mt-3", "vendor": "V3"})
    cfg = cfg.model_copy(update={"models": [*cfg.models, zh, third]})
    router = router_for(cfg)
    seeds = range(40)
    free = {pick_media_model(router, "speech", "budget", random.Random(s)).id for s in seeds}
    assert free == {"mt1", "mt-zh", "mt-3"}  # 没有默认项：同档内随机
    zh_only = {
        pick_media_model(router, "speech", "budget", random.Random(s), ["zh"]).id for s in seeds
    }
    assert zh_only == {"mt-zh"}  # 偏好标签优先
    none_have = {
        pick_media_model(router, "speech", "budget", random.Random(s), ["fr"]).id for s in seeds
    }
    assert none_have == free  # 没有任何模型带该标签时不限制

    flagged = cfg.model_copy(
        update={"models": [m.model_copy(update={"default": m.id == "mt-3"}) for m in cfg.models]}
    )
    router = router_for(flagged)
    assert {pick_media_model(router, "speech", "budget", random.Random(s)).id for s in seeds} == {
        "mt-3"
    }
    # 偏好标签先于默认项
    assert {
        pick_media_model(router, "speech", "budget", random.Random(s), ["zh"]).id for s in seeds
    } == {"mt-zh"}
