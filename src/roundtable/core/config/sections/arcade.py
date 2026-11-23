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


class TankRules(_Strict):
    """原创坦克对战「铁甲突围」：实时的，成员坦克由本地电脑操控（不调模型、不花钱）。"""

    lives: int = Field(default=3, ge=1, le=9)  # 每辆坦克几条命
    minutes: float = Field(default=3.0, ge=0.5, le=15)  # 一局最长几分钟，到点按击毁数排名
    max_members: int = Field(default=3, ge=1, le=3)  # 你之外最多几位成员上场（地图四个角）
    win_coins: int = Field(default=100, ge=0, le=100_000)  # 你拿第一给多少金币
    kill_coins: int = Field(default=20, ge=0, le=100_000)  # 每击毁一次给多少
    daily_cap: int = Field(default=600, ge=0, le=1_000_000)  # 坦克每天最多领多少金币
    min_seconds: float = Field(default=20, ge=0, le=600)  # 一局至少打这么久才算数


class WorkshopRules(_Strict):
    """游戏工坊：你写要求，工作台的编程助手（超级电脑同一条通道）做成一个能玩的网页小游戏。

    lead / assist 填 supercomputer.workers 里的编号（只在这里按你的安排点名，代码不认具体是谁）。
    主做的写游戏；辅助的依次试玩、找错、直接修（永远不审自己写的）；机器检查还有问题时交回作者返工。
    只用这几位：别的助手（包括按 API 额度花钱的）一律不派。
    """

    enabled: bool = True
    lead: str = "claude"  # 主做（2026-11-23 你定：Claude Code）
    assist: list[str] = Field(default_factory=lambda: ["opencode-ds", "codex"])  # 辅助，按顺序
    fix_rounds: int = Field(default=1, ge=0, le=3)  # 机器检查没过时交回作者返工几次
    max_games: int = Field(default=30, ge=1, le=500)  # 最多存几个做好的游戏
    max_request_chars: int = Field(default=2000, ge=50, le=20000)
    max_bytes: int = Field(default=800_000, ge=10_000, le=20_000_000)  # game.html 最大多大
    browser_check: bool = True  # 做完在本机浏览器里打开试一下（没装 Playwright 就跳过）


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
    max_tokens: int = Field(default=1200, ge=50, le=4000)
    timeout_s: float = Field(default=25.0, gt=0, le=120)  # 想太久就换本地电脑出
    think_delay_s: float = Field(default=0.8, ge=0, le=10)  # 本地电脑出牌前停一下，看得清
    holdem: HoldemRules = HoldemRules()
    cards: CardRules = CardRules()
    tanks: TankRules = TankRules()
    workshop: WorkshopRules = WorkshopRules()
    wallet: WalletRules = WalletRules()
