"""本能反射（P3 状态的一部分，docs/MEMBER_ENGINE.md「P3 状态」）：刺激 → 反射，全由程序判断。

- **刺激** ``Stimulus``：某位成员在某个时刻碰到的一件事，用标签描述（被 @、私聊、被批评、被夸、
  自己说话、生活里的事、干完活……），带上她那一刻是不是该睡觉、精力和心情。
- **反射** ``Reflex``：心情 / 精力改多少、给之后的「决定与表达」的动作（秒回、犯困、害羞……）、
  记成「刚才…」的一行说明。
- 现在用 ``RuleInstinct``：``chat.engine.state.reflexes`` 规则表，按顺序逐条匹配，命中的都算。
  以后换成小网络（M22 档 1）只要实现同一个 ``Instinct.react``，状态插件不用改。
- 所有成员同一张表；标签只看事件本身，不看是哪个模型（中立）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class Stimulus:
    member: str
    at: datetime
    tags: frozenset[str]
    night: bool = False  # 她这个时候本该在睡觉
    energy: float = 0.5
    mood: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)  # 来源事件的数据（备用，规则表不看）


@dataclass(frozen=True)
class Reflex:
    name: str
    mood: float = 0.0
    energy: float = 0.0
    act: str = ""
    note: str = ""


class Instinct(Protocol):
    def react(self, stimulus: Stimulus) -> list[Reflex]: ...


def _hit(rule: Any, s: Stimulus) -> bool:
    if not set(rule.tags) <= s.tags:
        return False
    if rule.night is not None and rule.night != s.night:
        return False
    if rule.energy_below is not None and s.energy >= rule.energy_below:
        return False
    return not (rule.mood_below is not None and s.mood >= rule.mood_below)


class RuleInstinct:
    """规则表实现。rules() 每次现取，配置改了马上生效。"""

    def __init__(self, rules: Callable[[], Iterable[Any]]) -> None:
        self.rules = rules

    def react(self, stimulus: Stimulus) -> list[Reflex]:
        return [
            Reflex(r.name, r.mood, r.energy, r.act, r.note)
            for r in self.rules()
            if _hit(r, stimulus)
        ]
