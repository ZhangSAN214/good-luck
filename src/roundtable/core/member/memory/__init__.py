"""P2 记忆与联想（docs/MEMBER_ENGINE.md「P2 记忆与联想」）。只用于生活模式（聊天、朋友圈、生活）。

- ``store``：记忆件写入（三路分流：合并计数 / 更新留旧 / 新增）、心情只改语气和重要度（留旧版）、
  脑子里挂着的事（最多 5 件）、生活只读自己的工作记录、全量经历的习惯摘要、训练数据。
- ``retrieve``：召回（候选只看几百件，半衰期带保底，同一次对话不重复，联想扩散），注入字数固定。
- ``links``：联想网（赫布学习、扩散激活、夜间随机游走）。
- ``night``：夜间整理的材料、解析和写回（每位成员每天一次调用，写第一人称日记）。
- ``diary``：翻日记工具（只在生活模式，每天有次数上限）。

这些表（``memory_*``）只有本插件写；工作模式不挂载（``core/member/mode.py``，有测试守卫）。
"""

from roundtable.core.member.memory.diary import TOOL, DiaryTool
from roundtable.core.member.memory.links import LinkNet
from roundtable.core.member.memory.retrieve import Recaller, Recollection
from roundtable.core.member.memory.store import MemoryStore, Written

__all__ = [
    "TOOL",
    "DiaryTool",
    "LinkNet",
    "MemoryStore",
    "Recaller",
    "Recollection",
    "Written",
]
