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


# 媒体模型（不上桌）：图片 / 语音 / 转写 / 视频，各有便宜档与高质量档
MEDIA_MODELS = [
    ("mi1", "VM1", "budget", ["image_gen"], {"unit": "image", "usd": 0.04}),
    ("mi2", "VM2", "flagship", ["image_gen", "image_edit"], {"unit": "image", "usd": 0.20}),
    ("mt1", "VM3", "budget", ["tts"], {"unit": "char", "usd": 0.00002}),
    ("ms1", "VM4", "budget", ["stt"], {"unit": "minute", "usd": 0.006}),
    ("mv1", "VM5", "budget", ["video_gen"], {"unit": "second", "usd": 0.10}),
    ("mv2", "VM6", "flagship", ["video_gen"], {"unit": "second", "usd": 0.40}),
]


def models_config(pool=POOL, disabled=(), with_media=False) -> ModelsConfig:
    media = (
        [
            {
                "id": mid,
                "vendor": vendor,
                "tier": tier,
                "tags": tags,
                "media_price": price,
                "seat": False,
                "routes": [{"channel": "c", "model": mid}],
            }
            for mid, vendor, tier, tags, price in MEDIA_MODELS
        ]
        if with_media
        else []
    )
    return ModelsConfig.model_validate(
        {
            "tag_vocabulary": REPO.models.tag_vocabulary,
            "channels": {
                "c": {"adapter": "fake", "kind": "aggregator", "base_url": "https://c.test"}
            },
            "models": media
            + [
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


def with_pipeline(roundtable, enabled: bool):
    """协同流水线开关。旧的协同测试默认关闭（沿用 v1 的拆分方式），流水线测试显式打开。"""
    collab = roundtable.collab
    return roundtable.model_copy(
        update={
            "collab": collab.model_copy(
                update={"pipeline": collab.pipeline.model_copy(update={"enabled": enabled})}
            )
        }
    )


def app_config(
    pool=POOL,
    disabled=(),
    with_media=False,
    pipeline=False,
    rt_update=None,
    **routing_overrides,
) -> AppConfig:
    routing = (
        REPO.routing.model_copy(update=routing_overrides) if routing_overrides else REPO.routing
    )
    return AppConfig(
        models=models_config(pool, disabled, with_media),
        roundtable=with_pipeline(REPO.roundtable, pipeline).model_copy(update=rt_update or {}),
        # 步骤测试用 "组员甲" 式代号：前缀放回去（真实配置里前缀为空，代号本身就是称呼）
        personas=REPO.personas.model_copy(
            update={
                "code_prefix": "组员",
                # 合成厂商的昵称：不含厂商名，否则"昵称·模式"会被当成泄露身份
                "nicknames": {
                    **{f"V{i}": f"阿{'零一二三四五六七八九'[i]}" for i in range(1, 7)},
                    **{f"VM{i}": f"媒{'零一二三四五六七八九'[i]}" for i in range(1, 7)},
                },
            }
        ),
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
