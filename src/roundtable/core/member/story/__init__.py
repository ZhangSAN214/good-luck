"""P7 生活引擎（L0 底座 + L10 擅长觉醒，docs/LIFE_STORY.md）。只用于生活模式。

- ``engine``：生活事件库 + 长线剧情 + 每人的生活状态；空闲时按各自爱好挑去处；住户变化当事件。
- ``talent``：从人物卡种子和生活练习里长出擅长（萌芽 → 觉醒 → 深耕 → 瓶颈与突破 / 生疏）。

**只发事件、不越权**：挑出来的事写成 ``life_events``（kind = story），由成员引擎转到总线；
心情精力由 P3 按反射表接、记忆由 P2 夜间整理接、写档案由 P6 成长接（本包从不写档案）。
**单向隔离**：工作模式（讨论、协同、办公室……）不 import 本包，生活状态和擅长从不进派活、
绩效和工作提示词（测试守卫）；擅长的证据也不看办公室表现。
"""

from roundtable.core.member.story.engine import (
    MARK,
    STORY,
    Happening,
    StoryEngine,
    story_meta,
)
from roundtable.core.member.story.talent import Talents

__all__ = ["MARK", "STORY", "Happening", "StoryEngine", "Talents", "story_meta"]
