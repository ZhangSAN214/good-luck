"""成员的称呼。

- 匿名开启：从代号池（塔罗牌）随机抽取，代号不含任何身份信息；
- 匿名关闭：用"昵称·模式"（厂商昵称 + 档位名，都在 personas.yaml 里配置）。同一桌里厂商和档位
  都相同的两个模型，后一个加序号（"鲸鱼娘·全力2"）；统筹显示为"昵称·模式（统筹）"。
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from roundtable.core.config import AppConfig
from roundtable.core.config.schema import PersonasConfig


def display_name(model_id: str, config: AppConfig) -> str:
    """模型的称呼：昵称·模式。没有配置昵称的厂商用厂商名，没有档位的不带模式。"""
    personas = config.personas
    spec = next((m for m in config.models.models if m.id == model_id), None)
    if spec is None:
        return model_id
    nick = personas.nicknames.get(spec.vendor, spec.vendor)
    if spec.tier is None:
        return nick
    return f"{nick}{personas.nickname_separator}{personas.tier_labels.get(spec.tier, spec.tier)}"


def coordinator_name(model_id: str, config: AppConfig) -> str:
    return display_name(model_id, config) + config.personas.coordinator_suffix


def tarot_codes(
    model_ids: Sequence[str], personas: PersonasConfig, rng: random.Random
) -> dict[str, str]:
    """匿名：随机抽取代号并随机分给模型。返回 代号 → 模型 id（按抽取顺序）。"""
    if len(set(model_ids)) != len(model_ids):
        raise ValueError("模型 id 不能重复")
    if len(model_ids) > len(personas.codes):
        raise ValueError(f"代号池只有 {len(personas.codes)} 个，不够 {len(model_ids)} 个座位")
    models = list(model_ids)
    rng.shuffle(models)
    return dict(zip(rng.sample(personas.codes, len(models)), models, strict=True))


def nickname_codes(
    model_ids: Sequence[str], config: AppConfig, rng: random.Random
) -> dict[str, str]:
    """匿名关闭：称呼 = 昵称·模式（重名加序号）。返回 称呼 → 模型 id，座位顺序随机。"""
    models = list(model_ids)
    if len(set(models)) != len(models):
        raise ValueError("模型 id 不能重复")
    rng.shuffle(models)
    seen: dict[str, int] = {}
    result: dict[str, str] = {}
    for model in models:
        base = display_name(model, config)
        seen[base] = seen.get(base, 0) + 1
        result[base if seen[base] == 1 else f"{base}{seen[base]}"] = model
    return result


def member_codes(
    model_ids: Sequence[str], config: AppConfig, rng: random.Random, *, anonymous: bool
) -> dict[str, str]:
    """一张桌子的 代号 → 模型 id。"""
    if anonymous:
        return tarot_codes(model_ids, config.personas, rng)
    return nickname_codes(model_ids, config, rng)
