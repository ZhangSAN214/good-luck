"""工资单：按组员代号、按桌的统筹、外包分别汇总；揭晓前不含模型 id。"""

from __future__ import annotations

from roundtable.core.office import payslip
from roundtable.core.storage.repository import CallView, SeatView


def call(i, role, code=None, cost=0.01, table_no=0, failed=False, model_id=None):
    return CallView(i, table_no, "answer", role, code, 100, 50, cost, 1.0, failed, None, model_id)


SEATS = (
    SeatView(0, "member", "甲", "m1"),
    SeatView(0, "member", "乙", "m2"),
    SeatView(0, "coordinator", None, "m3"),
)


def test_groups_members_coordinator_and_outsourced():
    calls = [
        call(1, "member", "甲", 0.02),
        call(2, "member", "甲", 0.01, failed=True),
        call(3, "member", "乙", 0.05),
        call(4, "coordinator", None, 0.04),
        call(5, "tool", "甲", 0.003),
        call(6, "planner", None, 0.001),
    ]
    slip = payslip(calls, SEATS, coordinator_label=lambda mid: f"统筹({mid})")
    by = {line.who: line for line in slip.lines}
    assert by["甲"].calls == 2 and by["甲"].failed_calls == 1 and by["甲"].model_id == "m1"
    assert abs(by["甲"].cost_usd - 0.03) < 1e-12
    assert by["统筹(m3)"].role == "coordinator" and by["统筹(m3)"].model_id == "m3"
    assert by["外包：搜索与工具"].model_id is None and by["规划员"].calls == 1
    assert [line.cost_usd for line in slip.lines] == sorted(
        (line.cost_usd for line in slip.lines), reverse=True
    )
    assert abs(slip.total_usd - sum(c.cost_usd for c in calls)) < 1e-12


def test_coordinators_are_split_by_table_and_hidden_before_reveal():
    seats = (
        *SEATS[:2],
        SeatView(0, "coordinator", None, None),
        SeatView(1, "coordinator", None, None),
    )
    calls = [call(1, "coordinator", table_no=0), call(2, "coordinator", table_no=1)]
    slip = payslip(calls, seats, coordinator_label=lambda mid: "统筹" if mid is None else mid)
    assert [line.who for line in slip.lines] == ["统筹", "统筹"]
    assert all(line.model_id is None for line in slip.lines)


def test_empty():
    slip = payslip([], SEATS)
    assert slip.lines == () and slip.total_usd == 0
