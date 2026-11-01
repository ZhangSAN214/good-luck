"""P6 成长（docs/MEMBER_ENGINE.md「P6 成长」）。只用于生活模式（聊天、朋友圈、生活）。

- ``week``：一周的经历（日记按句、记下的事、用户说的话），带编号，供引用。
- ``gate``：程序把关（只能这样改、锁定项不动、必须引用真实经历、核心性格每月一格、
  8 人不撞型、受用户影响不超过三成）。
- ``timeline``：成长时间线（可一条条撤回）和每周本地备份。

**成长是唯一能以成长（growth）的身份写自我档案 personality / values / style / likes 的插件**
（测试守卫）；调模型的部分在 ``core/growth.py``。工作模式不挂载（``core/member/mode.py``）。
"""

from roundtable.core.member.growth.gate import Gate, Ledger, Proposal, Verdict, parse
from roundtable.core.member.growth.timeline import GrowthLog
from roundtable.core.member.growth.week import Line, Week, build, monday, week_id

__all__ = [
    "Gate",
    "GrowthLog",
    "Ledger",
    "Line",
    "Proposal",
    "Verdict",
    "Week",
    "build",
    "monday",
    "parse",
    "week_id",
]
