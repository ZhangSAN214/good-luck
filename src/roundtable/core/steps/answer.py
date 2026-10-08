"""独立作答：每个组员各自作答，互不可见。"""

from __future__ import annotations

from .base import StepResult, TableContext, call_model, gather_members, register_step


class AnswerStep:
    name = "answer"

    async def run(self, ctx: TableContext) -> StepResult:
        todo = [c for c in ctx.active if c not in ctx.state.answers]  # 恢复时跳过已完成的

        async def work(code: str) -> None:
            prompt = ctx.render(self.name, code=ctx.label(code), question=ctx.question.text)
            out = await call_model(
                ctx,
                step=self.name,
                role="member",
                model_id=ctx.members[code],
                prompt=prompt,
                code=code,
            )
            text = out.completion.text.strip() if out.completion else ""
            if not text:
                ctx.drop(code, self.name, "调用失败" if out.completion is None else "回答为空")
                return
            ctx.state.answers[code] = text
            ctx.repo.save_output(
                ctx.session_id,
                table_no=ctx.table_no,
                step=self.name,
                kind="answer",
                code=code,
                content=text,
                call_id=out.call_id,
            )

        before = set(ctx.state.dropped)
        await gather_members(ctx, todo, work)
        dropped = tuple(c for c in ctx.state.dropped if c not in before)
        return StepResult(self.name, calls=len(todo), dropped=dropped)


register_step(AnswerStep())
