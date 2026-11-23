"""游戏区（docs/ARCADE.md）的配置：成员出牌的花费上限、牌桌大小、虚拟金币与下注。"""

from __future__ import annotations

from pydantic import Field

from roundtable.core.config.sections.base import _Strict


class HoldemRules(_Strict):
    stack: int = Field(default=1000, ge=100, le=1_000_000)  # 每人上桌带多少筹码
    small_blind: int = Field(default=10, ge=1)
    big_blind: int = Field(default=20, ge=2)
    max_members: int = Field(default=7, ge=1, le=8)  # 你之外最多几位成员（一桌最多 9 人）


class CardRules(_Strict):
    """斗地主 / 跑得快 / 升级：你上桌时一局的得分换成金币（输的最多输光，不会欠）。"""

    coin_per_point: int = Field(default=10, ge=1, le=10_000)
    coin_per_level: int = Field(
        default=50, ge=1, le=10_000
    )  # 升级：赢一局按升了几级算（至少 1 级）


class WalletRules(_Strict):
    """虚拟金币：只在圆桌里用，不能充值、不能换真钱。"""

    start: int = Field(default=2000, ge=0)  # 第一次进游戏区给多少
    daily_gift: int = Field(default=500, ge=0)  # 每天第一次进来送多少
    max_bet: int = Field(default=500, ge=1)  # 旁观时一手最多押多少


class ArcadeRules(_Strict):
    enabled: bool = True
    # 成员用自己的模型（群聊里那个，便宜档）想怎么出；关掉、到上限、超时、出错时本地电脑接手
    members_think: bool = True
    daily_usd: float = Field(default=0.10, ge=0)  # 成员出牌每天最多花多少（你 2026-11-23 定的）
    estimate_usd: float = Field(default=0.001, gt=0)  # 每次出牌按这个数先看够不够
    max_tokens: int = Field(default=200, ge=50, le=2000)
    timeout_s: float = Field(default=25.0, gt=0, le=120)  # 想太久就换本地电脑出
    think_delay_s: float = Field(default=0.8, ge=0, le=10)  # 本地电脑出牌前停一下，看得清
    holdem: HoldemRules = HoldemRules()
    cards: CardRules = CardRules()
    wallet: WalletRules = WalletRules()
