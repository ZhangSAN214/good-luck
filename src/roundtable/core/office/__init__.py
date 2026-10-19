"""办公室模式（O1～O5，见 docs/OFFICE.md）：AI 是员工，token 是工资。

O1：名册（经理 / 员工、工资标准、本月已领工资）与每件事的工资单。
"""

from .payroll import Payslip, PayslipLine, payslip
from .roster import RosterEntry, roster

__all__ = ["Payslip", "PayslipLine", "RosterEntry", "payslip", "roster"]
