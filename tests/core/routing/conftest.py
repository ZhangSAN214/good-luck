"""合成的模型池：厂商与 id 都是虚构的，保证路由逻辑不依赖任何真实品牌。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.config import AppConfig, ModelsConfig, load_config
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import ChannelRouter, FakeProvider

REPO = load_config()

# (id, vendor, tier, tags, price_in, price_out)
POOL = [
    ("b1", "V1", "budget", ["math"], 0.10, 0.40),
    ("b2", "V2", "budget", ["math", "vision"], 0.30, 1.20),
    ("b3", "V3", "budget", ["writing"], 0.70, 3.50),
    ("f1", "V1", "flagship", ["math", "vision"], 2.0, 10.0),
    ("f2", "V2", "flagship", ["writing"], 2.0, 12.0),
    ("f3", "V4", "flagship", ["math"], 2.0, 6.0),
    ("f4", "V5", "flagship", ["code"], 3.0, 15.0),
    ("f5", "V6", "flagship", ["research"], 1.0, 4.0),
]


def models_config(pool=POOL, disabled=()) -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "tag_vocabulary": REPO.models.tag_vocabulary,
            "channels": {
                "c": {"adapter": "fake", "kind": "aggregator", "base_url": "https://c.test"}
            },
            "models": [
                {
                    "id": mid,
                    "vendor": vendor,
                    "tier": tier,
                    "tags": tags,
                    "price": {"input": pin, "output": pout},
                    "enabled": mid not in disabled,
                    "seat": tier is not None,  # 没有档位的是工具模型（如图像生成），不上桌
                    "routes": [{"channel": "c", "model": mid}],
                }
                for mid, vendor, tier, tags, pin, pout in pool
            ],
        }
    )


def app_config(pool=POOL, disabled=(), **routing_overrides) -> AppConfig:
    routing = (
        REPO.routing.model_copy(update=routing_overrides) if routing_overrides else REPO.routing
    )
    return AppConfig(
        models=models_config(pool, disabled),
        roundtable=REPO.roundtable,
        personas=REPO.personas,
        routing=routing,
    )


def planner_reply(difficulty="medium", task_type="math", tokens=800, reason="r") -> str:
    return json.dumps(
        {
            "task_type": task_type,
            "difficulty": difficulty,
            "expected_answer_tokens": tokens,
            "reason": reason,
        }
    )


class Env:
    def __init__(self, config: AppConfig, fake: FakeProvider):
        self.config = config
        self.fake = fake
        self.router = ChannelRouter(config.models, {"c": fake})
        self.prompts = PromptLibrary()


@pytest.fixture
def env() -> Env:
    return Env(app_config(), FakeProvider("c", default=planner_reply()))
