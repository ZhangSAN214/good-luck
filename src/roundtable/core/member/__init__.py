"""成员引擎（成员智能体系统的骨架，docs/MEMBER_ENGINE.md）。

只用于聊天、朋友圈、生活；工作模式（讨论、协同、办公室、个人作答）不读取这里的任何东西。

- ``profile``：自我档案（每位成员一份，带版本和修改记录，可撤回）。
- ``bus``：事件总线（聊天、生活产生的事发成事件，模块用程序订阅更新，不调用模型）。
- ``heartbeat``：心跳（看谁醒着、有没有值得做的事；没事就什么都不调）。
- ``context``：上下文组装器（固定部分放最前，总长固定预算，约 600 字）。
- ``scheduler``：预算调度器（用户直接找她 > 主动找你 > 群聊 > 生活；到上限降级为只记一行）。
- ``state`` / ``instinct``：状态（心情、精力，快慢两层、回到基线）和本能反射（规则表），全由程序算。
- ``evalset``：评测集（每位成员同一套情境题，规则判分；离线用假模型）。
"""

from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.context import Context, Piece, assemble
from roundtable.core.member.engine import MemberEngine
from roundtable.core.member.heartbeat import Heartbeat, Intent
from roundtable.core.member.instinct import Instinct, Reflex, RuleInstinct, Stimulus
from roundtable.core.member.mode import Mode, WorkModeError, mode_of, require_life
from roundtable.core.member.profile import Profile, ProfileError, ProfileStore
from roundtable.core.member.scheduler import BudgetScheduler, Decision, Priority
from roundtable.core.member.state import StatePlugin

__all__ = [
    "BudgetScheduler",
    "Context",
    "Decision",
    "Event",
    "EventBus",
    "Heartbeat",
    "Instinct",
    "Intent",
    "MemberEngine",
    "Mode",
    "WorkModeError",
    "Piece",
    "Priority",
    "Profile",
    "ProfileError",
    "ProfileStore",
    "Reflex",
    "RuleInstinct",
    "StatePlugin",
    "Stimulus",
    "assemble",
    "mode_of",
    "require_life",
]
