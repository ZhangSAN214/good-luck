from __future__ import annotations

from roundtable.core.config import ModelSpec


def model(
    mid: str, vendor: str, tier: str | None = "budget", tags=(), price=(1.0, 1.0)
) -> ModelSpec:
    return ModelSpec.model_validate(
        {
            "id": mid,
            "vendor": vendor,
            "tier": tier,
            "tags": list(tags),
            "price": {"input": price[0], "output": price[1]},
            "routes": [{"channel": "c", "model": mid}],
        }
    )
