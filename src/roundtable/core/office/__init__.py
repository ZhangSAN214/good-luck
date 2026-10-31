"""办公室模式（O1～O5，见 docs/OFFICE.md）：AI 是员工，token 是工资。

O1：名册（经理 / 员工、工资标准、本月已领工资）与每件事的工资单。
O5：绩效（按活的类型：件数、被退回、验收通过率、平均工资与用时），可一键清零。
公寓楼：员工下班后的作息与正在上班的人（只读，不调用模型）。
"""

from .apartment import Building, Resident, building, life_spots, schedule, seat_room
from .payroll import Payslip, PayslipLine, payslip
from .performance import Performance, Stats, collect, reset
from .roster import RosterEntry, roster

__all__ = [
    "Building",
    "Payslip",
    "Performance",
    "Stats",
    "PayslipLine",
    "Resident",
    "RosterEntry",
    "building",
    "collect",
    "life_spots",
    "payslip",
    "reset",
    "roster",
    "schedule",
    "seat_room",
]
