"""出图时先试哪个模型（多渠道自动挑选）。只看渠道类型、价格、额度和当前状态，不看品牌。

自动顺序（同一档内保持配置顺序）：
  0 本地（渠道全是 kind: local，如电脑上的 ComfyUI，不花钱）
  1 不花钱的：单价为 0，或走的渠道还有本月赠送额度（monthly_credit_usd）
  2 花钱的：按每张单价从低到高（花费照旧算进创作输出的月上限）
正在歇的（限速、额度用完、ComfyUI 显卡在忙或没开）排到最后：其余都画不出来时才再试它。
没配 key 的渠道本来就不可用（router.available_models() 不含），自动不用。

手动设置（DrawPrefs）：关掉的模型不用；手动排了先后的按手动顺序排在最前，其余按上面的规则。
同样的模型在免费渠道和付费渠道都有时，走哪条路由渠道优先顺序决定（priority.py），这里只排模型。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from roundtable.core.config import AppConfig, ModelSpec
from roundtable.core.providers import ChannelRouter
from roundtable.core.providers.cost import image_cost
from roundtable.core.providers.priority import DrawPrefs

if TYPE_CHECKING:  # storage → routing → media：只在类型检查时导入，避免循环
    from roundtable.core.storage import Repository

BANDS = ("local", "free", "paid")


def usable_channels(router: ChannelRouter, model: ModelSpec) -> list[str]:
    return [r.channel for r in router.plan(model.id).usable]


def band(router: ChannelRouter, model: ModelSpec) -> str:
    """local / free / paid（见模块说明）。"""
    channels = usable_channels(router, model) or [r.channel for r in model.routes]
    kinds = router_kinds(router)
    if channels and all(kinds.get(c) == "local" for c in channels):
        return "local"
    if image_cost(model, None, 1) <= 0 or any(router.credit_left(c) for c in channels):
        return "free"
    return "paid"


def router_kinds(router: ChannelRouter) -> dict[str, str]:
    return {name: spec.kind for name, spec in router.channels.items()}


def resting(router: ChannelRouter, model: ModelSpec) -> bool:
    """能走的渠道全都在歇（限速、额度用完、显卡在忙、连不上之后的冷却）。"""
    channels = usable_channels(router, model)
    return bool(channels) and all(router.cooling_s(c) > 0 for c in channels)


def draw_key(
    router: ChannelRouter, model: ModelSpec, prefer_tags: Iterable[str] = ()
) -> tuple[bool, int, int, int, float]:
    """排序键（越小越先试）：在歇 → 手动顺序 → 档 → 偏好标签多 → 单价。"""
    order = router.draw_prefs.order
    manual = order.index(model.id) if model.id in order else len(order)
    prefer = set(prefer_tags)
    return (
        resting(router, model),
        manual,
        BANDS.index(band(router, model)),
        -len(prefer & set(model.tags)),
        image_cost(model, None, 1),
    )


def rank_draw_models(
    router: ChannelRouter, models: Sequence[ModelSpec], prefer_tags: Iterable[str] = ()
) -> list[ModelSpec]:
    """去掉手动关掉的，按 draw_key 排（稳定排序，同键保持原顺序）。"""
    prefer = tuple(prefer_tags)
    off = set(router.draw_prefs.disabled)
    pool = [m for m in models if m.id not in off]
    return sorted(pool, key=lambda m: draw_key(router, m, prefer))


# --- 手动设置的存取（界面「出图渠道」） ------------------------------------------------

SETTING_KEY = "media.draw"


def load_draw_prefs(config: AppConfig, repo: Repository) -> DrawPrefs:
    """读界面里保存过的设置；已不存在的模型忽略。"""
    raw = repo.setting(SETTING_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    known = {m.id for m in config.models.models}

    def ids(key: str) -> tuple[str, ...]:
        value = data.get(key)
        if not isinstance(value, list):
            return ()
        return tuple(dict.fromkeys(x for x in value if isinstance(x, str) and x in known))

    return DrawPrefs(disabled=ids("disabled"), order=ids("order"))


def save_draw_prefs(
    config: AppConfig, repo: Repository, *, disabled: Sequence[str], order: Sequence[str]
) -> DrawPrefs:
    """校验并保存；只认能出图的模型。"""
    drawable = {m.id for m in config.models.models if "image_gen" in m.tags}
    unknown = sorted({*disabled, *order} - drawable)
    if unknown:
        raise ValueError(f"不是出图模型：{unknown}")
    data = {"disabled": list(dict.fromkeys(disabled)), "order": list(dict.fromkeys(order))}
    repo.set_setting(SETTING_KEY, json.dumps(data, ensure_ascii=False))
    return load_draw_prefs(config, repo)
