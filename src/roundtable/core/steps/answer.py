"""独立作答：每个组员各自作答，互不可见。没有实质内容的答案打回重做一次，仍不合格标记敷衍。"""

from __future__ import annotations

from .base import StepResult, TableContext, call_model, gather_members, register_step
from .effort import forgive_truncation, record_effort, redo_call, text_problems
from .schemas import EffortRecord


class AnswerStep:
    name = "answer"

    async def run(self, ctx: TableContext) -> StepResult:
        todo = [c for c in ctx.active if c not in ctx.state.answers]  # 恢复时跳过已完成的
        rule = ctx.effort_rule
        notes: list[str] = []
        redone: list[str] = []

        def problems(text: str, code: str, call_id: int | None) -> list[str]:
            if not rule.enabled:
                return []
            found = text_problems(
                text,
                rule=rule,
                question=ctx.question.text,
                expected_tokens=ctx.expected_answer_tokens,
            ) + ctx.citation_problems(self.name, code, text)
            return forgive_truncation(ctx, self.name, code, call_id, found)

        async def work(code: str) -> None:
            prompt = ctx.render(self.name, code=ctx.label(code), question=ctx.question.text)
            model_id = ctx.members[code]
            out = await call_model(
                ctx, step=self.name, role="member", model_id=model_id, prompt=prompt, code=code
            )
            text = out.completion.text.strip() if out.completion else ""
            if not text:
                ctx.drop(code, self.name, "调用失败" if out.completion is None else "回答为空")
                return
            call_id = out.call_id
            first = problems(text, code, call_id)
            if first:
                final = first
                if rule.redo:
                    redone.append(code)
                    again = await redo_call(
                        ctx,
                        step=self.name,
                        role="member",
                        model_id=model_id,
                        prompt=prompt,
                        previous=text,
                        reasons=first,
                        code=code,
                    )
                    retry = again.completion.text.strip() if again.completion else ""
                    if retry:  # 重做调用失败时保留第一次的答案
                        text, call_id = retry, again.call_id
                        final = problems(retry, code, call_id)
                status = "lazy" if final else "redone"
                record_effort(
                    ctx,
                    EffortRecord(self.name, code, status, tuple(first), tuple(final), rule.redo),
                )
                if final:
                    notes.append(f"{ctx.label(code)} 的答案被标记为敷衍：{'；'.join(final)}")
            ctx.state.answers[code] = text
            ctx.repo.save_output(
                ctx.session_id,
                table_no=ctx.table_no,
                step=self.name,
                kind="answer",
                code=code,
                content=text,
                call_id=call_id,
            )

        before = set(ctx.state.dropped)
        await gather_members(ctx, todo, work)
        dropped = tuple(c for c in ctx.state.dropped if c not in before)
        return StepResult(
            self.name, calls=len(todo) + len(redone), dropped=dropped, notes=tuple(notes)
        )


register_step(AnswerStep())
