"""夜间整理（P2）：每位成员每天只调用一次模型，把这一天写成第一人称日记，同时拆出记忆件、
更新脑子里挂着的事；心情不平静时顺便改几件旧记忆的回忆语气和重要度（原文不改，留旧版）。

这里只放不调用模型的部分：准备材料（``materials``）、解析输出（``parse``）、写回（``apply``）。
真正的调用在 ``core/memory.py`` 的 ``MemoryKeeper``（和聊天共用调用、记账和预算）。

- 材料：当天的聊天和生活记录（``<log>``）、自己当天在工作模式里做过的事
  （``<work>``，只读，匿名未揭晓的不读）、挂着的事（``<hanging>``，带编号）、今天被想起过的旧记忆（``<recalled>``，带编号）、
  夜里随机游走想到的旧事（``<drift>``）、现在的心情（一句话）。
- 输出 JSON：``diary``（日记）、``items``（每件 text / domain / importance / fact）、
  ``hanging``（``add`` 新挂上的、``done`` 了结的编号）、
  ``retell``（编号、语气、重要度；心情平静时为空）、
  ``about``（v2：关于用户的线索，交给 P8「关于你」核对原话后写画像；本插件不写画像）。
- 日记存进 ``member_memories`` 的 ``day`` 层（原始记录：聊天、生活事件、日记都一直保留）；
  记忆件经三路分流写入；当天的件互相加强（联想网），再每晚衰减一次、随机游走几步、重算习惯摘要。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.member.memory.links import LinkNet
from roundtable.core.member.memory.store import MemoryStore, headless

NONE = "（没有）"


@dataclass
class NightResult:
    diary: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    hang: list[str] = field(default_factory=list)
    done: list[int] = field(default_factory=list)
    retell: list[dict[str, Any]] = field(default_factory=list)
    about: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Materials:
    variables: dict[str, str]
    hanging: dict[int, int]  # 编号 → 挂着的事的 id
    recalled: dict[int, int]  # 编号 → 记忆件 id
    drift: list[int]
    retell_allowed: bool


def mood_words(mood: float, threshold: float) -> str:
    if mood <= -0.6:
        return "很低落"
    if mood <= -threshold:
        return "有点低落"
    if mood >= 0.6:
        return "很开心"
    if mood >= threshold:
        return "心情不错"
    return "平静"


def _numbered(lines: list[str]) -> str:
    return "\n".join(f"{i}. {t}" for i, t in enumerate(lines, 1)) or NONE


def materials(
    rt: Any,
    member: str,
    *,
    name: str,
    day: str,
    log: str,
    work: list[str],
    mood: float,
    drift: list[int],
) -> Materials:
    rules = rt.config.roundtable.chat.memory
    repo = rt.repo
    hanging = repo.memory_hanging(member)
    recalled = [
        r
        for r in repo.memory_items(member, limit=200)
        if (r["recalled_at"] or "")[:10] == day and r["superseded_by"] is None
    ][: max(rules.retell_max * 2, 0)]
    drift_rows = repo.memory_items_by_ids(drift)
    retell_allowed = abs(mood) >= rules.mood_threshold and rules.retell_max > 0
    engine = rt.config.roundtable.chat.engine
    about = engine.about
    clues = about.clues_per_night if about.enabled and engine.enabled else 0
    variables = {
        "name": name,
        "day": day,
        "log": log or NONE,
        "work": "\n".join(f"- {w}" for w in work) or NONE,
        "hanging": _numbered([h["text"] for h in hanging]),
        "recalled": _numbered([r["tone"] or r["text"] for r in recalled]),
        "drift": "\n".join(f"- {r['text']}" for r in drift_rows) or NONE,
        "mood": mood_words(mood, rules.mood_threshold),
        "retell": str(rules.retell_max if retell_allowed else 0),
        "quota": str(rules.daily_quota),
        "hanging_max": str(rules.hanging_max),
        "domains": "、".join(rules.domains),
        "diary_chars": str(rules.diary_chars),
        "clues": str(clues),
        "about_domains": "、".join(about.domains),
    }
    return Materials(
        variables,
        {i: h["id"] for i, h in enumerate(hanging, 1)},
        {i: r["id"] for i, r in enumerate(recalled, 1)},
        [r["id"] for r in drift_rows],
        retell_allowed,
    )


def _ints(values: Any) -> list[int]:
    out = []
    for v in values if isinstance(values, list) else []:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


_DIARY = re.compile(r'"diary"\s*:\s*"((?:[^"\\]|\\.)*)"')
_ITEM = re.compile(r'\{\s*"text"\s*:\s*"(?:[^"\\]|\\.)*"[^{}]*\}')


def _salvage(text: str) -> dict[str, Any] | None:
    """JSON 写到一半被截断（输出上限）：捡回写完了的日记和条目，不把半截 JSON 当日记存。"""
    diary = _DIARY.search(text)
    if diary is None:
        return None
    items = []
    for m in _ITEM.finditer(text[diary.end() :]):
        try:
            items.append(json.loads(m.group(0)))
        except ValueError:
            continue
    return {"diary": json.loads(f'"{diary.group(1)}"'), "items": items}


def parse(text: str) -> NightResult:
    """解析模型输出；不是 JSON 时整段当日记（不丢内容）。"""
    try:
        data = extract_json_object(text)
    except JSONOutputError:
        data = _salvage(text or "")
        if data is None:
            return NightResult(diary=(text or "").strip())
    items = [i for i in data.get("items") or [] if isinstance(i, dict) and i.get("text")]
    hanging = data.get("hanging") if isinstance(data.get("hanging"), dict) else {}
    retell = [r for r in data.get("retell") or [] if isinstance(r, dict) and "id" in r]
    about = [a for a in data.get("about") or [] if isinstance(a, dict) and a.get("text")]
    return NightResult(
        diary=str(data.get("diary") or "").strip(),
        items=items,
        hang=[str(h) for h in hanging.get("add") or [] if str(h).strip()],
        done=_ints(hanging.get("done")),
        retell=retell,
        about=about,
    )


def apply(
    rt: Any,
    member: str,
    day: str,
    result: NightResult,
    mats: Materials,
    *,
    clean: Any,
    offline: bool = False,
) -> dict[str, int]:
    """把整理结果写回。clean：敏感内容过滤（``core.memory.clean_memory``）。返回各项数量。"""
    rules = rt.config.roundtable.chat.memory
    store, links = MemoryStore(rt), LinkNet(rt)
    out = {"diary": 0, "new": 0, "merge": 0, "update": 0, "hang": 0, "done": 0, "retell": 0}
    diary = clean(result.diary, rules.diary_chars)
    if headless(diary):  # 只有标题没有正文（多半是生成被截断）：不存
        diary = ""
    if diary:
        rt.repo.add_memory(member, "day", diary, period=day, offline=offline)
        out["diary"] = 1
    today_ids = []
    for item in result.items[: rules.daily_quota]:
        text = clean(str(item.get("text") or ""), 80)
        if not text:
            continue
        try:
            importance = int(item.get("importance") or 1)
        except (TypeError, ValueError):
            importance = 1
        w = store.write(
            member,
            text.lstrip("★").strip(),
            day=day,
            fact=bool(item.get("fact")) or text.startswith("★"),
            domain=str(item.get("domain") or ""),
            importance=3 if text.startswith("★") else importance,
        )
        if w.item is not None:
            out[w.route] = out.get(w.route, 0) + 1
            today_ids.append(w.item["id"])
    for n in result.done:
        if n in mats.hanging and store.settle(member, mats.hanging[n]):
            out["done"] += 1
    for text in result.hang[: rules.hanging_max]:
        if store.hang(member, clean(text, 60), day):
            out["hang"] += 1
    if mats.retell_allowed:
        for r in result.retell[: rules.retell_max]:
            try:
                item_id = mats.recalled.get(int(r["id"]))
            except (TypeError, ValueError):
                continue
            if item_id is None:
                continue
            tone = clean(str(r.get("tone") or ""), 80) or None
            imp = r.get("importance")
            imp = int(imp) if isinstance(imp, int | float | str) and str(imp).isdigit() else None
            if store.retell(item_id, tone=tone, importance=imp, why=f"{day} 夜里带着心情想起"):
                out["retell"] += 1
    links.strengthen(member, today_ids + mats.drift)
    store.train(
        member,
        "night",
        {
            "day": day,
            "inputs": {k: v for k, v in mats.variables.items() if k != "name"},
            "diary": diary,
            "items": today_ids,
            "drift": mats.drift,
            "counts": out,
        },
    )
    return out


def nightly_links(rt: Any, member: str, day: str) -> list[int]:
    """整理前：联想网衰减一次，再从近几天的件出发随机游走。返回走到的旧件（不含起点）。"""
    links = LinkNet(rt)
    links.decay(member)
    starts = [r["id"] for r in rt.repo.memory_items(member, limit=20)]
    paths = links.walk(member, starts, f"walk:{member}:{day}")
    reached = []
    for path in paths:
        for i in path[1:]:
            if i not in reached and i not in starts:
                reached.append(i)
    if paths:
        MemoryStore(rt).train(member, "walk", {"day": day, "paths": paths})
    return reached[:3]
