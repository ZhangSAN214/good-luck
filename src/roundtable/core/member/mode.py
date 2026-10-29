"""工作与生活分开（用户 2026-10-29 硬性要求，docs/MEMBER_ENGINE.md「工作与生活」）。

- **工作模式**（讨论、协同、办公室、个人作答、头脑风暴……任何 ``workflow != 'chat'`` 的会话）：
  不挂载成员引擎，情绪、精力、习惯、关系、用户画像、生活经历、人格、说话风格一概不进提示词，
  只为条件和效率服务。编排引擎、步骤、路由不 import 本包（测试守卫），发给模型的每条消息
  也不含成员模块的内容（测试守卫）。
- **生活模式**（聊天、朋友圈、生活，``workflow = 'chat'``）：可以用成员引擎；还能**只读**成员
  自己在工作模式里做过什么（参加了哪场、题目），作为 ``work.done`` 经历事件进总线，之后由记忆
  订阅；从不反写工作。匿名开启且未揭晓的场次不读。
"""

from __future__ import annotations

from enum import StrEnum

LIFE_WORKFLOWS = frozenset({"chat"})


class Mode(StrEnum):
    WORK = "work"
    LIFE = "life"


class WorkModeError(RuntimeError):
    """在工作模式里用了成员引擎。"""


def mode_of(workflow: str) -> Mode:
    return Mode.LIFE if workflow in LIFE_WORKFLOWS else Mode.WORK


def require_life(mode: Mode | str) -> None:
    if Mode(mode) is not Mode.LIFE:
        raise WorkModeError("工作模式不挂载成员引擎（情绪、人格、生活经历等都不进工作提示词）")
