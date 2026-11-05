"""roundtable.yaml 的 mail、calendar、gamemode、backup：终端邮件栏、日历、游戏模式、备份。

从 schema.py 拆出（代码精简）。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from roundtable.core.config.sections.base import _Strict


class MailAIRules(_Strict):
    """终端邮件栏的 AI 总结 / 翻译（docs/MAIL.md）：只在用户点了按钮、
    并同意把这封邮件发给模型后才调用；所选档位里按预计价格挑最便宜的一位；
    花费计入总预算和这里的每月上限，每天次数也有上限。"""

    plan: str = "budget"
    monthly_usd: float = Field(default=0.30, ge=0)
    daily_calls: int = Field(default=20, ge=0, le=500)
    max_chars: int = Field(default=6000, ge=500, le=40000)  # 每封邮件最多发多少字
    max_tokens: int = Field(default=900, ge=100, le=8000)
    estimate_usd: float = Field(default=0.005, ge=0)  # 每次调用预估
    digest_mails: int = Field(default=10, ge=1, le=30)  # 「总结重要邮件」最多带几封
    language: str = Field(default="简体中文", min_length=1, max_length=20)  # 翻译成什么语言


class MailConfig(_Strict):
    ai: MailAIRules = MailAIRules()


class CalendarRegion(_Strict):
    """一个国家 / 地区的法定假日（开源库 holidays 离线计算）。"""

    country: str = Field(pattern=r"^[A-Z]{2}$")  # ISO 国家代码，如 GB、CN、US
    subdiv: str | None = None  # 地区，如 GB 的 ENG（英格兰）
    label: str = Field(min_length=1, max_length=12)  # 界面和事件里的称呼，如「英国」
    language: str | None = None  # 节日名的语言（holidays 支持的才行，如 zh_CN）


_DATE_RULE = re.compile(
    r"^(\d{2}-\d{2}|easter[+-]\d{1,3}|[1-5]-(mon|tue|wed|thu|fri|sat|sun)-\d{2}"
    r"|last-(mon|tue|wed|thu|fri|sat|sun)-\d{2})$"
)


class CalendarObservance(_Strict):
    """不放假的节日 / 纪念日。rule：MM-DD；easter+N / easter-N（复活节前后 N 天）；
    N-周几-MM（某月第 N 个周几，如 2-sun-05）；last-周几-MM（某月最后一个周几）。"""

    name: str = Field(min_length=1, max_length=20)
    rule: str
    label: str = Field(default="国际", max_length=12)  # 哪里的节日

    @field_validator("rule")
    @classmethod
    def _rule(cls, rule: str) -> str:
        if not _DATE_RULE.match(rule):
            raise ValueError(
                f"日期规则写法不对：{rule}（MM-DD / easter±N / 2-sun-05 / last-sun-10）"
            )
        return rule


class LunarFestival(_Strict):
    """农历节日：lunar 写 MM-DD，或 MM-last（这个月最后一天，如除夕 12-last）。"""

    name: str = Field(min_length=1, max_length=12)
    lunar: str = Field(pattern=r"^\d{2}-(\d{2}|last)$")


class CalendarRules(_Strict):
    """日历底座（docs/CALENDAR.md）：只算「今天是什么日子」，发事件给成员系统；不调模型、不联网。

    怎么过节由生活引擎 / 成员系统决定；工作模式不挂载。"""

    enabled: bool = True
    # 成员的休息日按哪里的法定假日算（成员住曼彻斯特：英格兰）
    rest: CalendarRegion | None = CalendarRegion(country="GB", subdiv="ENG", label="英国")
    cultures: list[CalendarRegion] = Field(default_factory=list)  # 也过、但不放假的国家
    lunar_festivals: list[LunarFestival] = Field(default_factory=list)
    solar_terms: bool = True  # 二十四节气
    observances: list[CalendarObservance] = Field(default_factory=list)
    names: dict[str, str] = Field(default_factory=dict)  # 外文节日名 → 中文
    upcoming_days: int = Field(default=3, ge=0, le=30)  # 提前几天发「快到了」
    board_days: int = Field(default=7, ge=1, le=60)  # 公告栏显示未来几天
    max_user_dates: int = Field(default=100, ge=0, le=1000)  # 你能加的纪念日上限


class BackupRules(_Strict):
    """整库备份（docs/BACKUP.md）：每天一份 + 存档点，坏了一键恢复；不花钱。"""

    enabled: bool = True  # 每天自动备份（关掉后仍能手动存档点、恢复）
    keep_daily: int = Field(default=14, ge=1, le=365)  # 每天的备份留几份
    keep_checkpoints: int = Field(default=10, ge=1, le=100)  # 存档点留几份（和每天的分开数）
    check_minutes: int = Field(default=60, ge=5, le=1440)  # 开着时每隔多久看一眼「今天备份了没」


class GameModeRules(_Strict):
    """游戏模式（docs/GAMEMODE.md）：你在玩游戏或别的程序在用显卡时，圆桌让出显卡。

    忙的判断（任一）：手动开关；在跑游戏（「关于你」的游戏表 + Steam / Epic 装的）；前台是全屏程序；
    显卡占用或别的程序占的显存超过门槛（读 NVIDIA 驱动，只在电脑上）。
    忙时：用显卡的渠道（渠道配置 ``gpu: true``，如本地出图、本地配音）一律不调用，换下一个模型；
    朋友圈配图不改走付费模型，先排队；连续空闲 ``idle_minutes`` 分钟后逐个补跑。
    云端聊天不受影响。"""

    auto: bool = True  # 自动识别（关掉后只认手动开关）
    poll_seconds: float = Field(default=10.0, ge=2, le=300)  # 多久看一次
    idle_minutes: float = Field(default=5.0, ge=0, le=120)  # 连续空闲多久才重新用显卡、补跑排队的活
    detect_games: bool = True  # 认在跑的游戏（不管在不在前台）
    detect_fullscreen: bool = True  # 前台是全屏程序（看视频全屏也算）
    # Steam / Epic 目录里不算游戏的（启动器、壁纸、运行库）：按游戏文件夹名，不分大小写
    not_games: list[str] = Field(
        default_factory=lambda: [
            "Launcher",
            "wallpaper_engine",
            "Steamworks Shared",
            "Epic Online Services",
            "DirectXRedist",
        ],
        max_length=50,
    )
    busy_util_pct: int = Field(default=40, ge=0, le=100)  # 显卡占用达到它算忙（0 = 不看）
    busy_vram_mb: int = Field(default=1500, ge=0)  # 别的程序占的显存达到它算忙（0 = 不看）
    # 算自己人的程序（本地出图、本地配音都是 Python 服务）：它们占的显存不算「别的程序」
    own_processes: list[str] = Field(
        default_factory=lambda: ["python.exe", "pythonw.exe", "python", "python3"],
        max_length=20,
    )
    # 自己的显卡活刚做完，这么久内不看占用
    own_grace_seconds: float = Field(default=60.0, ge=0, le=600)
    # 忙时圆桌自己的 CPU：low = 降到最低优先级（只在 Windows 上）；pause = 再停掉后台的生活、
    # 例会、管家、周报（聊天和讨论照常）；normal = 不管
    cpu_when_busy: Literal["low", "pause", "normal"] = "low"
    # 忙时朋友圈配图：defer = 不花钱的模型能画就画，否则排队等显卡空了补画；cloud = 照旧换付费模型
    photos_when_busy: Literal["defer", "cloud"] = "defer"
    queue_max: int = Field(default=50, ge=1, le=500)  # 排队最多几件（满了最早的丢掉）
