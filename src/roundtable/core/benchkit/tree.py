"""生成器的三层分工树（docs/KIT.md「生成器第二版 · 第 1 块」）：大阶段 → 小项目 → 小线程。

- 大阶段：立项、执行、交付、革新。
- 小项目：每个大阶段下面的具体任务（访谈、调研、找素材、方案会、方案书；第 n 段；质检与评审、
  对抗挑错、你的验收；下一版提案）。
- 小线程：每一件派给助手的活（``BenchBuilder._step`` 每调一次记一条）。

线程记在 ``build.json`` 的 ``threads`` 里；旧项目没有这个字段时，从协作看板（``team.cards``）
和窗口记录换算出来，不丢记录。这里只做纯数据换算，不调模型。
"""

from __future__ import annotations

from typing import Any

PHASES = ("立项", "执行", "交付", "革新")
THREADS_KEPT = 400  # 一个项目最多留多少条线程记录（最早的先丢）

# 线程的角色 → (大阶段, 小项目)；实施 / 评审要看做到哪一步了（见 node_of）
ROLE_NODE: dict[str, tuple[str, str]] = {
    "访谈": ("立项", "访谈"),
    "调研": ("立项", "调研"),
    "看素材": ("立项", "找素材"),
    "主案候选": ("立项", "方案会"),
    "投票": ("立项", "方案会"),
    "拆需求": ("立项", "方案书"),
    "对抗": ("交付", "对抗挑错"),
    "革新": ("革新", "下一版提案"),
}
# 每个大阶段下面小项目的固定顺序（分段按段号排在执行下面）
PROJECT_ORDER: dict[str, tuple[str, ...]] = {
    "立项": ("访谈", "调研", "找素材", "方案会", "方案书"),
    "交付": ("质检与评审", "对抗挑错", "你的验收"),
    "革新": ("下一版提案",),
}
STATUS_LABEL = {
    "running": "进行中",
    "done": "完成",
    "failed": "没成",
    "waiting": "排队",
    "paused": "暂停",
}


def stage_title(b: dict[str, Any], n: int) -> str:
    ms = (b.get("proposal") or {}).get("milestones") or []
    title = ms[n - 1].get("title", "") if 0 < n <= len(ms) else ""
    return f"第 {n} 段" + (f"：{title}" if title else "")


def node_of(b: dict[str, Any], role: str) -> tuple[str, str]:
    """这一件活挂在哪个大阶段、哪个小项目下面。"""
    if role in ROLE_NODE:
        return ROLE_NODE[role]
    if b.get("stage_task") is None and b.get("status") in ("checking", "reworking"):
        return ("交付", "质检与评审")  # 最后一段做完以后的几轮质检、评审、返工
    n = b.get("stage")
    if isinstance(n, int) and n > 0:
        return ("执行", stage_title(b, n))
    if b.get("rounds"):
        return ("交付", "质检与评审")
    return ("执行", "写窗口")


def _legacy_threads(b: dict[str, Any]) -> list[dict[str, Any]]:
    """旧项目：协作看板的每张卡算一条线程（没有用时和次数，只有谁、干了什么、成没成）。"""
    out = []
    for i, c in enumerate(((b.get("team") or {}).get("cards")) or []):
        phase, project = ROLE_NODE.get(c.get("role", ""), ("执行", "写窗口"))
        if c.get("role") == "评审" and b.get("rounds"):
            phase, project = "交付", "质检与评审"
        out.append(
            {
                "id": f"old-{i + 1}",
                "phase": phase,
                "project": project,
                "role": c.get("role", ""),
                "title": c.get("task", ""),
                "worker": c.get("who"),
                "label": c.get("label"),
                "status": "done" if c.get("ok") else "failed",
                "started_at": c.get("at"),
                "finished_at": c.get("at"),
                "attempts": 1,
                "legacy": True,
            }
        )
    return out


def threads_of(b: dict[str, Any]) -> list[dict[str, Any]]:
    threads = b.get("threads")
    return list(threads) if threads is not None else _legacy_threads(b)


def _seconds(t: dict[str, Any]) -> float | None:
    from datetime import datetime

    a, z = t.get("started_at"), t.get("finished_at")
    if not (a and z):
        return None
    try:
        return max(0.0, (datetime.fromisoformat(z) - datetime.fromisoformat(a)).total_seconds())
    except ValueError:
        return None


def _roll(items: list[dict[str, Any]]) -> dict[str, Any]:
    """一组线程合起来的状态：有在跑的算进行中；全成算完成；有没成的算没成。"""
    if not items:
        return {"status": "waiting", "attempts": 0, "seconds": 0.0, "count": 0}
    states = {t["status"] for t in items}
    if "running" in states:
        status = "running"
    elif "paused" in states:
        status = "paused"
    elif states <= {"done"}:
        status = "done"
    elif "failed" in states and "done" not in states:
        status = "failed"
    else:
        status = "done" if items[-1]["status"] == "done" else "failed"
    return {
        "status": status,
        "attempts": sum(int(t.get("attempts") or 0) for t in items),
        "seconds": round(sum(_seconds(t) or 0.0 for t in items), 1),
        "count": len(items),
    }


def _project_key(phase: str, name: str) -> tuple[int, str]:
    order = PROJECT_ORDER.get(phase, ())
    if name in order:
        return (order.index(name), name)
    if name.startswith("第 "):
        digits = "".join(ch for ch in name[2:6] if ch.isdigit())
        return (int(digits or 0), name)
    return (len(order) + 100, name)


def tree_view(b: dict[str, Any], paused: list[str] | None = None) -> dict[str, Any]:
    """界面右栏「计划」用的树：每个节点有状态、负责的助手、额度次数、用时、交了什么。"""
    paused = paused if paused is not None else list(b.get("paused") or [])
    threads = threads_of(b)
    milestones = (b.get("proposal") or {}).get("milestones") or []
    phases = []
    for phase in PHASES:
        mine = [t for t in threads if t.get("phase") == phase]
        names: list[str] = list(dict.fromkeys(t["project"] for t in mine))
        if phase == "执行":  # 方案书里定好的分段，还没开工也先列出来
            for i in range(1, len(milestones) + 1):
                title = stage_title(b, i)
                if title not in names:
                    names.append(title)
        if phase == "交付" and (b.get("gate") or {}).get("kind") == "accept":
            names.append("你的验收")
        projects = []
        for name in sorted(dict.fromkeys(names), key=lambda x: _project_key(phase, x)):
            items = [t for t in mine if t["project"] == name]
            roll = _roll(items)
            if f"{phase}/{name}" in paused and roll["status"] != "running":
                roll["status"] = "paused"
            if name == "你的验收" and not items:
                roll["status"] = "waiting"
            projects.append(
                {
                    "name": name,
                    "key": f"{phase}/{name}",
                    "workers": sorted(
                        {t.get("label") or t.get("worker") or "" for t in items} - {""}
                    ),
                    "threads": [
                        t | {"status_label": STATUS_LABEL.get(t["status"], t["status"])}
                        for t in items
                    ],
                    "paused": f"{phase}/{name}" in paused,
                }
                | roll
            )
        phases.append({"name": phase, "projects": projects} | _roll(mine))
    stale = b.get("stale") or {}
    titles = {w["id"]: w.get("title", w["id"]) for w in b.get("windows") or []}
    return {
        "phases": phases,
        "legacy": b.get("threads") is None,
        "paused": paused,
        # 改了上游、要重做的窗口（第 2 块「只重做受影响的」）
        "stale": [
            {"window": w, "title": titles.get(w, w), "why": [r["why"] for r in rs]}
            for w, rs in stale.items()
        ],
        "conflict": b.get("edit_conflict"),
    }
