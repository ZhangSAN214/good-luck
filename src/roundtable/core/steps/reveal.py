"""揭晓：讨论结束，可以揭晓身份。真正的揭晓由用户点击触发（不在这里自动执行）。"""

from __future__ import annotations

from .base import StepResult, TableContext, register_step


class RevealStep:
    name = "reveal"

    async def run(self, ctx: TableContext) -> StepResult:
        ctx.state.ready_for_reveal = True
        return StepResult(self.name)


register_step(RevealStep())
