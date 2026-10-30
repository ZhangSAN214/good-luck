"""人的特质（docs/BRAIN.md「人的特质」）：大脑分区之外，让人更像人的几样东西。

全是纯函数，只读传进来的数（性格分、驱力、心情精力、喜好和记忆），不读写数据库；
状态由 ``brain.py`` 存。8 位同一套规则（``chat.engine.brain.psyche``），差别只来自各自的
档案和经历；随机数由（成员, 时刻）派生，可复现。

- **性格天平①**（``sway``）：同一个人每天、每个场合表现不一样。依据 Fleeson 整体特质论
  （性格是一段分布）、Mischel & Shoda 认知情感人格系统（「如果……就……」）。
- **性格天平②**（``tensions``）：两种相反的想法同时都挺强 → 心里有点矛盾。依据 Larsen & McGraw
  混合情绪、Schwartz 价值环。
- **情绪化处理**（``strategy``）：一件事的情绪会拖一阵，按各人习惯的方式消化。依据 Gross 情绪调节
  过程模型、Nolen-Hoeksema 反刍。
- **发散联想**（``associate`` / ``cue_memory``）：顺着一句话想到别的、被一个词勾起旧记忆。依据
  Mednick 联想层级、扩散激活、Berntsen 不由自主的回忆。
- **三观**（``stance`` / ``lean``）：在几条价值轴上有一贯的立场。依据 Schwartz 基本价值理论、
  Haidt 道德直觉。
- 先果后因、突发念头的规则简单，写在 ``brain.py`` 里。
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

TRAITS = ("外向", "较真", "好奇", "温和", "幽默")
_CJK2 = re.compile("(?=([\u4e00-\u9fff]{2}))")  # 相邻两字（可重叠）


def num(x: Any, default: float = 3.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def sway(
    rules: Any,
    traits: dict[str, Any],
    member: str,
    at: datetime,
    *,
    close: float = 0.0,
    energy: float = 0.6,
    mood: float = 0.0,
) -> dict[str, float]:
    """今天、此刻的性格分（1～5）：底子 + 当天的浮动 + 场合（亲近的人、累、心情）。"""
    rng = random.Random(f"psyche-sway:{member}:{at.date().isoformat()}")
    tired = max(0.0, 0.5 - energy) * 2  # 精力 0.5 以下才算累，0 时为 1
    out: dict[str, float] = {}
    for t in TRAITS:
        v = num(traits.get(t, 3)) + rng.gauss(0, rules.daily)
        v += rules.close.get(t, 0.0) * close
        v += rules.tired.get(t, 0.0) * tired
        v += rules.mood.get(t, 0.0) * mood
        out[t] = round(min(5.0, max(1.0, v)), 2)
    return out


def inputs(
    drives: dict[str, float], energy: float, mood: float, traits: dict[str, Any]
) -> dict[str, float]:
    """对立情绪两端能读的量。"""
    x = dict(drives)
    x |= {"energy": energy, "tired": max(0.0, 1 - energy), "mood": mood, "sad": max(0.0, -mood)}
    x["base"] = 1.0
    for t in TRAITS:
        x[f"trait:{t}"] = (num(traits.get(t, 3)) - 3) / 2
    return x


def tensions(
    rules: Any, drives: dict[str, float], energy: float, mood: float, traits: dict[str, Any]
) -> list[tuple[str, str, float]]:
    """两端都过线的矛盾：[(a 的说法, b 的说法, 两端里较弱的那端强度)]，强的在前。"""
    x = inputs(drives, energy, mood, traits)
    out = []
    for pair in rules.pairs:
        a = sum(c * x.get(k, 0.0) for k, c in pair.a.weights.items())
        b = sum(c * x.get(k, 0.0) for k, c in pair.b.weights.items())
        if min(a, b) >= rules.both_above:
            out.append((pair.a.label, pair.b.label, round(min(a, b), 3)))
    return sorted(out, key=lambda p: -p[2])


def strategy(rules: Any, traits: dict[str, Any], member: str) -> str:
    """她习惯怎么消化情绪：按性格分算各方式的得分取最高；分数相同时按成员派生的随机数定（不认人）。"""
    if not rules.strategies:
        return ""
    rng = random.Random(f"psyche-strategy:{member}")
    scored = {
        name: sum(c * (num(traits.get(t, 3)) - 3) for t, c in s.traits.items()) + rng.random() * 0.1
        for name, s in rules.strategies.items()
    }
    return max(scored, key=lambda n: scored[n])


def personal(pool: Iterable[str], member: str, salt: str, share: float) -> list[str]:
    """去模板化：公共素材里每位只分到一部分（按成员 id 派生、可复现，不认人），至少一条。"""
    items = list(pool)
    if not items or share >= 1:
        return items
    rng = random.Random(f"psyche-personal:{salt}:{member}")
    n = max(1, round(len(items) * share))
    return sorted(rng.sample(items, n), key=items.index)


def decayed(strength: float, since: datetime, at: datetime, half_life_hours: float) -> float:
    hours = max((at - since).total_seconds() / 3600, 0.0)
    return strength * 0.5 ** (hours / half_life_hours)


def associate(
    rules: Any, text: str, personal: Iterable[str], curious: float, rng: random.Random
) -> tuple[str, str] | None:
    """顺着这句话联想：返回 (触发词, 想到的东西)。好奇分越高越常联想、越可能再跳一步（远联想）。"""
    if not text or rng.random() >= rules.chance + rules.curious * (curious - 3):
        return None
    keys = [k for k in rules.links if k in text]
    if not keys:
        return None
    key = rng.choice(keys)
    pool = list(rules.links[key])
    personal = [p for p in personal if p]
    if personal and rng.random() < 0.3:  # 偶尔连到她自己喜欢的东西上
        pool.append(rng.choice(personal))
    hop = rng.choice(pool)
    if rng.random() < rules.second_hop * max(0.2, 1 + 0.25 * (curious - 3)):
        nxt = [x for k, v in rules.links.items() if k in hop for x in v if x != key]
        if nxt:
            hop = rng.choice(nxt)
    return key, hop


def cue_memory(
    rules: Any, text: str, memories: Iterable[str], rng: random.Random
) -> tuple[str, str] | None:
    """话里有个词和她的某段旧记忆一样 → 有概率被勾起来：返回 (那个词, 那段记忆)。"""
    for w in rules.stop:  # 先去掉常用词，免得拼出「们今」这种跨词的两字
        text = text.replace(w, " ")
    words = set(_CJK2.findall(text))
    if not words:
        return None
    hits = [(w, m) for m in memories for w in words if w in m]
    if not hits or rng.random() >= rules.cue_memory:
        return None
    return rng.choice(hits)


def stance(axis: Any, traits: dict[str, Any], values_text: str, member: str) -> float:
    """她在这条轴上的立场：正 = 偏 a 端。性格分 + 自我档案「三观」里提到的词 + 一点个人差异。"""
    v = sum(c * (num(traits.get(t, 3)) - 3) for t, c in axis.traits.items())
    v += 0.3 * sum(values_text.count(w) for w in axis.a_words)
    v -= 0.3 * sum(values_text.count(w) for w in axis.b_words)
    v += random.Random(f"psyche-stance:{member}:{axis.a}").uniform(-0.5, 0.5)
    return round(v, 3)


def lean(axis: Any, text: str) -> int:
    """这句话偏哪端：1 = a 端，-1 = b 端，0 = 没表态或两边都有。"""
    a = any(w in text for w in axis.a_words)
    b = any(w in text for w in axis.b_words)
    return 1 if a and not b else -1 if b and not a else 0
