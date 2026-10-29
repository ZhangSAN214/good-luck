"""仓库里的整份画图模型名单：分档按单价、两种接口都有、每档都能接替。"""

from __future__ import annotations

from roundtable.core.config import load_config
from roundtable.core.providers import image_cost

from .conftest import Rig

BUDGET_MAX_USD = 0.02  # 节电档只放每张 ≤ $0.02 的画图模型


def image_models():
    return [m for m in load_config().models.enabled if "image_gen" in m.tags]


def test_budget_tier_only_has_cheap_image_models():
    models = image_models()
    budget = [m for m in models if m.tier == "budget"]
    assert len(budget) >= 2 and len([m for m in models if m.tier == "flagship"]) >= 2
    for m in budget:
        assert image_cost(m, None, 1) <= BUDGET_MAX_USD, m.id


def test_every_image_model_is_seatless_and_routed():
    for m in image_models():
        assert not m.seat and m.tier and m.routes and m.image_api in {"chat", "images"}


def test_all_images_rig_picks_within_tier():
    rig = Rig(all_images=True)
    tiers = {m.id: m.tier for m in image_models()}
    for tier in ("budget", "flagship"):
        assert tiers[rig.service().model_for("image", tier).id] == tier
