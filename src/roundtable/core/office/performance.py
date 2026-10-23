"""绩效：每位员工按活的类型记录干了几件、被退回几次、验收通过率、平均工资、平均用时。

只从已有的记录推算（办公室模式会话的产出与调用记录），不另外存数据：
- 单人办：被派到的员工记一件；经理过目了算「验收过」，退回修改算「被退回」。
- 开会：每位发言的员工记一件（开会不验收）。
- 项目分工：项目组成员负责的每个子任务记一件；主办经理逐条验收过的算「验收过」，
  没通过的算「被退回」。
工资和用时是该员工在这张桌子上全部调用的花费与耗时合计。

匿名未揭晓的会话不计入（不暴露身份）。老板可以一键清零：记下清零时刻，之后只统计这之后的会话。
绩效只用于名册展示和派活参考（同等条件下优先表现好的），不改变工资标准，也不改变提示词模板。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from roundtable.core.storage import Repository

RESET_KEY = "office.performance_since"
KINDS = ("single", "meeting", "project")
KIND_LABELS = {"single": "单人办", "meeting": "开会", "project": "项目分工"}


@dataclass
class Stats:
    jobs: int = 0
    checked: int = 0  # 经理验收过的件数
    returned: int = 0  # 被退回的件数
    cost_usd: float = 0.0
    seconds: float = 0.0

    @property
    def pass_rate(self) -> float | None:
        return (self.checked - self.returned) / self.checked if self.checked else None

    def add(self, other: Stats) -> None:
        self.jobs += other.jobs
        self.checked += other.checked
        self.returned += other.returned
        self.cost_usd += other.cost_usd
        self.seconds += other.seconds

    def to_dict(self) -> dict[str, Any]:
        jobs = self.jobs or 1
        return {
            **asdict(self),
            "pass_rate": self.pass_rate,
            "avg_cost_usd": self.cost_usd / jobs,
            "avg_seconds": self.seconds / jobs,
        }


@dataclass
class Performance:
    """model_id → 活的类型 → 统计；since 为统计起点（清零时刻，没有清零过为 None）。"""

    by_model: dict[str, dict[str, Stats]] = field(default_factory=dict)
    since: str | None = None

    def stats(self, model_id: str, kind: str) -> Stats:
        return self.by_model.setdefault(model_id, {}).setdefault(kind, Stats())

    def total(self, model_id: str) -> Stats:
        out = Stats()
        for s in self.by_model.get(model_id, {}).values():
            out.add(s)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "since": self.since,
            "by_model": {
                m: {
                    **{k: s.to_dict() for k, s in kinds.items()},
                    "total": self.total(m).to_dict(),
                }
                for m, kinds in self.by_model.items()
            },
        }


def reset(repo: Repository, now: str) -> None:
    """一键清零：之后只统计 now 以后创建的会话。"""
    repo.set_setting(RESET_KEY, now)


def collect(repo: Repository, limit: int = 200) -> Performance:
    """统计最近 limit 场办公室模式会话（清零之后的）。"""
    from roundtable.core.steps import restore_state

    since = repo.setting(RESET_KEY)
    perf = Performance(since=since)
    for sid in repo.office_sessions(since, limit):
        for table in repo.tables(sid):
            table_no = table["table_no"]
            models = {s["code"]: s["model_id"] for s in repo.seats(sid, table_no) if s["code"]}
            if not models:
                continue
            totals = repo.member_call_totals(sid, table_no)
            state = restore_state(repo, sid, table_no)
            jobs = _jobs(state)
            for code, (kind, n, checked, returned) in jobs.items():
                if code not in models:
                    continue
                cost, secs = totals.get(code, (0.0, 0.0))
                perf.stats(models[code], kind).add(Stats(n, checked, returned, cost, secs))
    return perf


def _jobs(state: Any) -> dict[str, tuple[str, int, int, int]]:
    """一张桌子上每位员工（代号）的 (类型, 件数, 验收过, 被退回)。"""
    o, c = state.office, state.collab
    out: dict[str, tuple[str, int, int, int]] = {}
    if o.intake is not None and o.intake.get("mode") == "meeting":
        for code in o.speeches:
            out[code] = ("meeting", 1, 0, 0)
    elif o.worker is not None:
        check = o.check or {}
        checked = 1 if check and not check.get("degraded") else 0
        returned = 1 if checked and check.get("verdict") == "revise" else 0
        out[o.worker] = ("single", 1, checked, returned)
    elif o.intake is None and c.assignment is not None and c.merge is not None:
        # 项目组桌子（只有项目组桌子会走到合并；办公室会话里的协同只来自项目分工）
        acc = c.acceptance
        results = acc.results if acc is not None and not acc.degraded else {}
        for sid, owners in c.assignment.owners.items():
            for code in owners:
                kind, n, checked, returned = out.get(code, ("project", 0, 0, 0))
                passed = results.get(sid)
                out[code] = (
                    kind,
                    n + 1,
                    checked + (passed is not None),
                    returned + (passed is not None and not passed[0]),
                )
    return out


def brief(perf: Performance, model_id: str, min_jobs: int = 1) -> str:
    """一行绩效摘要（给经理派活参考）：没有记录时为空。"""
    parts = []
    for kind in KINDS:
        s = perf.by_model.get(model_id, {}).get(kind)
        if s is None or s.jobs < min_jobs:
            continue
        rate = f"，通过率 {s.pass_rate:.0%}" if s.pass_rate is not None else ""
        parts.append(f"{KIND_LABELS[kind]} {s.jobs} 件{rate}")
    return "；".join(parts)
