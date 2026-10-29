"""月度「这个月的我」报告（P6，借成员头脑风暴 I15「可检验指标」）：全由程序按记录数出来，
不调用模型。

- 这个月：有经历的天数、记下的事、反思了几周、改了几处（撤回了几处、被拦下几处）、
  五项性格分从多少到多少、列表加了 / 去了什么、习惯前几项、熟练度升了几级。
- 熟练度的「升了几级」和上一份报告存下的等级比（第一份报告和 0 级比）。
- 报告里写明：这些数字说明她「表现得像」在变化，不等于她有主观体验。
- 每位成员每月一份，存在成长插件自己的 JSON 里，最多留 ``report.keep`` 份。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from roundtable.core.member.growth.gate import ADD, SCORE
from roundtable.core.member.growth.habits import habits as habit_list
from roundtable.core.member.growth.timeline import CHANGE, REJECTED, REVERT, SKIP

NOTE = (
    "以上都是程序按记录数出来的：说明她「表现得像」在变化，不等于她有主观体验或真的感受到了成长。"
)


def prev_month(today: date) -> str:
    y, m = today.year, today.month - 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y}-{m:02d}"


def _in(entry: dict[str, Any], month: str) -> bool:
    return str(entry.get("at") or "")[:7] == month


def build(
    blob: dict[str, Any],
    month: str,
    *,
    rules: Any,
    skills: list[dict[str, Any]],
    diary_days: int,
    items: int,
) -> dict[str, Any]:
    """这位成员 month（YYYY-MM）的报告。blob：成长数据（时间线、习惯计数、上次的熟练度）。"""
    tl = [e for e in blob.get("timeline") or [] if _in(e, month)]
    changes = [e for e in tl if e["kind"] == CHANGE]
    kept = [e for e in changes if not e.get("reverted")]
    traits: dict[str, list[int]] = {}
    for e in kept:
        if e["op"] == SCORE:
            trait = e["path"].rsplit(".", 1)[-1]
            first = traits.setdefault(trait, [e["old"], e["new"]])
            first[1] = e["new"]
    lists = [
        f"{e['path'].split('.', 1)[-1]} {'加上' if e['op'] == ADD else '去掉'}「{e['value']}」"
        for e in kept
        if e["op"] != SCORE
    ]
    before = blob.get("skills_at") or {}
    ups = [
        {"domain": s["domain"], "from": before.get(s["domain"], 0), "to": s["level"]}
        for s in skills
        if s["level"] > before.get(s["domain"], 0)
    ]
    weeks = {e["week"] for e in tl if e["kind"] in (CHANGE, REJECTED, SKIP)}
    return {
        "month": month,
        "diary_days": diary_days,
        "items": items,
        "weeks": len(weeks),
        "changes": len(changes),
        "reverted": sum(1 for e in tl if e["kind"] == REVERT),
        "rejected": sum(1 for e in tl if e["kind"] == REJECTED),
        "user_share": round(sum(1 for e in kept if e.get("user")) / len(kept), 2) if kept else 0,
        "traits": {k: {"from": v[0], "to": v[1]} for k, v in traits.items()},
        "lists": lists,
        "habits": [h["label"] for h in habit_list(blob, rules.habits)[:5]],
        "skill_ups": ups,
        "note": NOTE,
    }


def lines(r: dict[str, Any]) -> list[str]:
    """报告写成几行大白话（网页和测试用）。"""
    out = [
        f"{r['month']}：有经历的日子 {r['diary_days']} 天，记下的事 {r['items']} 件，"
        f"反思了 {r['weeks']} 周。",
        f"改了 {r['changes']} 处（你撤回 {r['reverted']} 处），被拦下 {r['rejected']} 处；"
        f"受你影响的占 {round(r['user_share'] * 100)}%。",
    ]
    if r["traits"]:
        out.append(
            "性格分：" + "、".join(f"{k} {v['from']} → {v['to']}" for k, v in r["traits"].items())
        )
    if r["lists"]:
        out.append("设定：" + "；".join(r["lists"]))
    if r["habits"]:
        out.append("习惯：" + "、".join(r["habits"]))
    if r["skill_ups"]:
        out.append(
            "熟练度："
            + "、".join(f"{u['domain']} {u['from']} → {u['to']} 级" for u in r["skill_ups"])
        )
    out.append(r["note"])
    return out
