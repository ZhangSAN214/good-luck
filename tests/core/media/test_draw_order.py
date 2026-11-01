"""出图多渠道自动挑选：本地 → 不花钱（单价 0 或渠道还有赠送额度）→ 按价；在歇的放最后；
手动关掉的不用、手动排的在最前。只看渠道类型、价格、额度和状态，换名不影响。"""

from __future__ import annotations

import random

import pytest

from roundtable.core.config import ModelsConfig
from roundtable.core.media import media_group, pick_media_model
from roundtable.core.media.draw_order import (
    band,
    load_draw_prefs,
    rank_draw_models,
    resting,
    save_draw_prefs,
)
from roundtable.core.providers import ChannelRouter, FakeProvider
from roundtable.core.providers.errors import AllChannelsFailed, ErrorKind, ProviderError
from roundtable.core.providers.priority import ChannelPriority, DrawPrefs
from roundtable.core.storage import Repository, connect

from ..routing.conftest import REPO

CHANNELS = {
    "pc": {"adapter": "fake", "kind": "local", "base_url": "http://127.0.0.1:8188"},
    "hub": {"adapter": "fake", "kind": "aggregator", "base_url": "https://hub.test"},
    "gift": {"adapter": "fake", "kind": "direct", "base_url": "https://gift.test"},
}
# id, 渠道, 每张单价
IMAGE = [
    ("dear", "hub", 0.20),
    ("cheap", "hub", 0.01),
    ("gifted", "gift", 0.05),
    ("zero", "hub", 0.0),
    ("local", "pc", 0.0),
]


def config(rename: dict[str, str] | None = None) -> ModelsConfig:
    rename = rename or {}
    return ModelsConfig.model_validate(
        {
            "tag_vocabulary": REPO.models.tag_vocabulary,
            "channels": CHANNELS,
            "models": [
                {
                    "id": rename.get(mid, mid),
                    "vendor": f"V-{rename.get(mid, mid)}",
                    "tier": "budget",
                    "tags": ["image_gen", "image_edit"],
                    "media_price": {"unit": "image", "usd": usd},
                    "seat": False,
                    "routes": [{"channel": ch, "model": mid}],
                }
                for mid, ch, usd in IMAGE
            ],
        }
    )


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def router(cfg: ModelsConfig | None = None, clock: Clock | None = None) -> ChannelRouter:
    cfg = cfg or config()
    providers = {name: FakeProvider(name) for name in CHANNELS}
    return ChannelRouter(cfg, providers, clock=clock or Clock(), wall=lambda: 1_800_000_000.0)


def ids(models) -> list[str]:
    return [m.id for m in models]


def test_local_then_free_then_by_price():
    r = router()
    group = media_group(r, "image", "budget")
    assert ids(group) == ["local", "zero", "cheap", "gifted", "dear"]
    assert [band(r, m) for m in group] == ["local", "free", "paid", "paid", "paid"]


def test_a_channel_with_gift_credit_left_counts_as_free_until_used_up():
    r = router()
    spent = {"gift": 0.0}
    r.set_priority(ChannelPriority(credits={"gift": 5.0}), lambda ch: spent.get(ch, 0.0))
    assert ids(media_group(r, "image", "budget")) == ["local", "zero", "gifted", "cheap", "dear"]
    spent["gift"] = 5.0  # 本月赠送额度用完：回到按价
    assert ids(media_group(r, "image", "budget")) == ["local", "zero", "cheap", "gifted", "dear"]


async def test_resting_models_go_last_and_come_back_after_the_cooldown():
    clock = Clock()
    r = router(clock=clock)

    async def busy(provider, route, model):
        raise ProviderError(
            ErrorKind.RATE_LIMIT, route.channel, "显卡在忙：ComfyUI 里还有 1 个任务"
        )

    with pytest.raises(AllChannelsFailed):
        await r.invoke("local", busy)
    group = media_group(r, "image", "budget")
    assert ids(group)[-1] == "local" and resting(r, group[-1])
    note = r.last_skip("pc")
    assert note is not None and "显卡在忙" in note.reason and note.kind == "rate_limit"
    clock.now += 10_000  # 歇够了
    assert ids(media_group(r, "image", "budget"))[0] == "local"

    async def ok(provider, route, model):
        return "done"

    await r.invoke("local", ok)
    assert r.last_skip("pc") is None  # 成功一次就清掉


def test_models_without_a_usable_channel_are_never_used():
    cfg = config()
    providers = {"hub": FakeProvider("hub")}  # 本地和赠送渠道都没配
    r = ChannelRouter(cfg, providers, unavailable={"pc": "没有 key", "gift": "没有 key"})
    assert ids(media_group(r, "image", "budget")) == ["zero", "cheap", "dear"]


def test_manual_disable_and_order():
    r = router()
    r.set_draw_prefs(DrawPrefs(disabled=("local",), order=("dear", "cheap")))
    assert ids(media_group(r, "image", "budget")) == ["dear", "cheap", "zero", "gifted"]


def test_pick_media_model_takes_the_best_and_only_randomises_ties():
    r = router()
    picks = {pick_media_model(r, "image", "budget", random.Random(s)).id for s in range(30)}
    assert picks == {"local"}
    r.set_draw_prefs(DrawPrefs(disabled=("local", "zero")))
    picks = {pick_media_model(r, "image", "budget", random.Random(s)).id for s in range(30)}
    assert picks == {"cheap"}


def test_prefer_tags_break_ties_inside_a_band_only():
    cfg = config()
    tagged = [
        m.model_copy(update={"tags": [*m.tags, "zh"]}) if m.id == "dear" else m for m in cfg.models
    ]
    r = router(cfg.model_copy(update={"models": tagged}))
    ranked = rank_draw_models(r, media_group(r, "image", "budget"), ["zh"])
    assert ids(ranked) == ["local", "zero", "dear", "cheap", "gifted"]  # 花钱的那档里它先


def test_renaming_models_and_vendors_does_not_change_the_order():
    rename = {"dear": "aa", "cheap": "zz", "gifted": "mm", "zero": "qq", "local": "bb"}
    back = {v: k for k, v in rename.items()}
    r = router(config(rename))
    assert [back[m.id] for m in media_group(r, "image", "budget")] == ids(
        media_group(router(), "image", "budget")
    )


def test_prefs_are_saved_validated_and_unknown_ids_dropped_on_load():
    from roundtable.core.config import AppConfig

    cfg = AppConfig(
        models=config(),
        roundtable=REPO.roundtable,
        personas=REPO.personas,
        routing=REPO.routing,
    )
    repo = Repository(connect(":memory:"))
    assert load_draw_prefs(cfg, repo) == DrawPrefs()
    saved = save_draw_prefs(cfg, repo, disabled=["dear"], order=["cheap", "cheap", "zero"])
    assert saved == DrawPrefs(disabled=("dear",), order=("cheap", "zero"))
    assert load_draw_prefs(cfg, repo) == saved
    with pytest.raises(ValueError, match="不是出图模型"):
        save_draw_prefs(cfg, repo, disabled=["nope"], order=[])
    smaller = cfg.model_copy(
        update={"models": cfg.models.model_copy(update={"models": cfg.models.models[1:]})}
    )
    assert load_draw_prefs(smaller, repo).disabled == ()  # 模型删掉后忽略
