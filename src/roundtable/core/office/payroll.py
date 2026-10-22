"""工资单：一件事（一场会话）里每个人领了多少工资。

按会话视图（session_view）里的调用记录汇总，所以匿名会话揭晓前只按代号列出、不含模型 id；
没有代号的调用（规划员、分诊小组、搜索、媒体生成等）归入"外包"一行，按角色分开。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from roundtable.core.storage.repository import CallView, SeatView

OUTSOURCED = {
    "planner": "规划员",
    "triage": "分诊小组",
    "tool": "外包：搜索与工具",
    "media": "外包：媒体生成",
    "preprocess": "外包：附件处理",
    "reviewer": "复核经理",  # 办公室模式·项目分工：另一位经理最终验收（不上桌）
}


@dataclass(frozen=True)
class PayslipLine:
    who: str  # 称呼（代号 / 昵称·模式）或外包项目名
    role: str  # member / coordinator / 外包时为调用角色
    model_id: str | None  # 揭晓前为 None
    calls: int
    failed_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class Payslip:
    lines: tuple[PayslipLine, ...]
    total_usd: float


def payslip(
    calls: Sequence[CallView],
    seats: Sequence[SeatView],
    label: Callable[[str], str] = lambda code: code,
    coordinator_label: Callable[[str | None], str] = lambda model_id: "统筹",
) -> Payslip:
    """calls / seats 来自 session_view()。

    label 把组员代号转成显示用的称呼；coordinator_label 按统筹的模型 id（揭晓前为 None）给出称呼。
    统筹的座位没有代号，按桌区分（升级重做后每桌一位统筹）。
    """
    member_model = {s.code: s.model_id for s in seats if s.code}
    coordinators = {s.table_no: s.model_id for s in seats if s.role == "coordinator"}
    groups: dict[tuple[str, str], list[CallView]] = {}
    for c in calls:
        if c.role == "member" and c.code:
            key = ("member", c.code)
        elif c.role == "coordinator":
            key = ("coordinator", str(c.table_no))
        else:
            key = ("outsourced", c.role)
        groups.setdefault(key, []).append(c)

    lines = []
    for (kind, name), group in groups.items():
        if kind == "member":
            who, role, model_id = label(name), "member", member_model.get(name)
        elif kind == "coordinator":
            model_id = coordinators.get(group[0].table_no)
            who, role = coordinator_label(model_id), "coordinator"
        else:
            who = OUTSOURCED.get(name, f"外包：{name}")
            role = name
            model_id = None
        lines.append(
            PayslipLine(
                who=who,
                role=role,
                model_id=model_id,
                calls=len(group),
                failed_calls=sum(1 for c in group if c.failed),
                input_tokens=sum(c.input_tokens for c in group),
                output_tokens=sum(c.output_tokens for c in group),
                cost_usd=sum(c.cost_usd for c in group),
            )
        )
    lines.sort(key=lambda line: -line.cost_usd)
    return Payslip(tuple(lines), sum(line.cost_usd for line in lines))
