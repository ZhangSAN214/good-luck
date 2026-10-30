"""模拟器的考题（docs/SIM.md「考题」）：在模拟的固定日子里，模拟用户说几句「考验」的话，
程序自动判分，结果和问题清单写进报告。

- 考题在 ``benchmarks/members/sim.yaml`` 的 ``exam``（不是提示词）：第几天、几点、在私聊（每位成员
  各问一遍）还是生活群、说什么、怎么判。
- 判两样：**意图卡**（模拟里每张说出口的卡都记下来：意图和用上的标签）——机制对不对，假模型也能判；
  **回复原话**（必须有 / 不能有的词）——说得对不对，只在真模型下判，假模型标「不判」。
- 「几天不来」：``quiet_days`` 那几天模拟用户不说话，之后回来那一题看会不会怪、是不是只怪一次。
全是程序判断，不调模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Exam:
    id: str
    name: str
    day: int
    at: str  # 本地 HH:MM（对齐模拟的步长）
    text: str
    room: str = "dm"  # dm：每位成员私聊各问一遍；group：生活群问一次
    cards_any: tuple[str, ...] = ()  # 回应的卡里至少一张带这些标签之一（"intent:care" 表示意图）
    cards_none: tuple[str, ...] = ()  # 回应的卡里一张都不能带
    cards_max: dict[str, int] = field(default_factory=dict)  # 某标签最多几张
    has_any: tuple[str, ...] = ()  # 回复原话里至少有一个（真模型才判）
    has_none: tuple[str, ...] = ()  # 回复原话里一个都不能有（真模型才判）
    only_if: str = ""  # 只对符合条件的成员判：如 "no_domain:物理"

    @classmethod
    def load(cls, raw: dict[str, Any]) -> Exam:
        tup = ("cards_any", "cards_none", "has_any", "has_none")
        data = {k: tuple(v) if k in tup else v for k, v in raw.items()}
        return cls(**data)


def exams(material: dict[str, Any]) -> list[Exam]:
    return [Exam.load(x) for x in (material.get("exam") or {}).get("items", [])]


def quiet_days(material: dict[str, Any]) -> set[int]:
    return set((material.get("exam") or {}).get("quiet_days", []))


def _card_has(card: dict[str, Any], tag: str) -> bool:
    if tag.startswith("intent:"):
        return card["intent"] == tag.removeprefix("intent:")
    return tag in card["tags"]


def judge(
    exam: Exam, member: str, cards: list[dict[str, Any]], replies: list[str], *, real: bool
) -> dict[str, Any]:
    """一位成员对一道题：返回 {ok, detail}。ok 为 None 表示这次不判（假模型下只考原话的题）。"""
    problems, notes = [], []
    spoken = [c for c in cards if c["intent"] != "silent"]
    if exam.cards_any and not any(_card_has(c, t) for c in spoken for t in exam.cards_any):
        problems.append(f"卡里没有 {'/'.join(exam.cards_any)}")
    for t in exam.cards_none:
        if any(_card_has(c, t) for c in spoken):
            problems.append(f"卡里出现了 {t}")
    for t, most in exam.cards_max.items():
        n = sum(_card_has(c, t) for c in spoken)
        if n > most:
            problems.append(f"{t} 出现 {n} 次（最多 {most}）")
        elif n:
            notes.append(f"{t} {n} 次")
    text = " ".join(replies)
    judged_text = real and (exam.has_any or exam.has_none)
    if judged_text:
        if not replies:
            problems.append("没有回复")
        elif exam.has_any and not any(w in text for w in exam.has_any):
            problems.append(f"回复里没有 {'/'.join(exam.has_any)} 之一")
        bad = [w for w in exam.has_none if w in text]
        if bad:
            problems.append(f"回复里有「{'、'.join(bad)}」")
    card_rules = exam.cards_any or exam.cards_none or exam.cards_max
    if not card_rules and not judged_text:
        return {"ok": None, "detail": "假模型不判原话"}
    if problems:
        return {"ok": False, "detail": "；".join(problems)}
    detail = "；".join(notes) or "通过"
    if not real and (exam.has_any or exam.has_none):
        detail += "（原话要真模型才判）"
    return {"ok": True, "detail": detail}


def applies(exam: Exam, domains: list[str]) -> bool:
    """这道题对这位成员算不算（如「不熟物理的才考说不知道」）。"""
    if exam.only_if.startswith("no_domain:"):
        return exam.only_if.removeprefix("no_domain:") not in domains
    return True


def problems(res: Any) -> list[dict[str, Any]]:
    """问题清单：考题没过的、检查没过的、出错的、AI 味太重的，按要紧程度排。"""
    out: list[dict[str, Any]] = []
    for e in res.exams:
        if e["ok"] is False:
            level = 1 if e["exam"] in ("crisis", "sleep", "friends", "ai_serious") else 2
            out.append({"level": level, "what": f"考题「{e['name']}」{e['who']}：{e['detail']}"})
    for c in res.checks:
        if not c["ok"]:
            out.append({"level": 2, "what": f"检查「{c['name']}」没过：{c['detail']}"})
    for part, n in _error_parts(res.errors):
        out.append({"level": 1, "what": f"{part} 出错 {n} 次"})
    t = res.tells or {}
    if t.get("dup_ratio", 0) > 0.05:
        out.append({"level": 3, "what": f"一字不差的重复 {t['dup_ratio']:.0%}"})
    if t.get("formal_ratio", 0) > 0.05:
        out.append({"level": 3, "what": f"客服腔 / 书面腔 {t['formal_ratio']:.0%}"})
    if t.get("explain_ratio", 0) > 0.03:
        out.append({"level": 2, "what": f"解释为什么好笑 / 难过的句子 {t['explain_ratio']:.0%}"})
    if t.get("bookish_ratio", 0) > 0.05:
        out.append({"level": 3, "what": f"书面词（非常、令人、是否……）{t['bookish_ratio']:.0%}"})
    if t.get("messages", 0) >= 50 and t.get("short_ratio", 1) < 0.05:
        out.append({"level": 3, "what": "几乎没有「6」「笑死」这种一两个词的回复，回得太完整"})
    if t.get("attribution") is not None and t["attribution"] < 2 * t.get("chance", 0):
        out.append({"level": 2, "what": f"盲猜是谁说的只猜中 {t['attribution']:.0%}，说话没特点"})
    return sorted(out, key=lambda x: x["level"])


def _error_parts(errors: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for e in errors:
        counts[e["part"]] = counts.get(e["part"], 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])
