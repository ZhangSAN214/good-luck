"""修订：每个组员只看到别人对自己答案的有效评审（乱序），据此修订。

修订稿同样做实质内容检查（含"照抄别人的答案"和"没有回应审阅意见"），不合格打回重做一次。
回应中逐条的采纳情况被解析出来，用于记录评审者的贡献。
"""

from __future__ import annotations

from roundtable.core.allocation import shuffled

from .base import (
    StepResult,
    TableContext,
    call_and_parse,
    gather_members,
    register_step,
    review_block,
)
from .effort import record_effort, redo_call, text_problems
from .schemas import CheckedReview, EffortRecord, Revision, parse_decisions, parse_revision


def reviews_received(ctx: TableContext, author: str) -> list[CheckedReview]:
    """作者收到的有效评审，排除自评，顺序随机。"""
    received = {
        reviewer: r
        for reviewer, reviews in ctx.state.reviews.items()
        if reviewer != author
        for r in reviews
        if r.target == author and r.valid
    }
    return [received[k] for k in shuffled(sorted(received), ctx.rng)]


class ReviseStep:
    name = "revise"

    async def run(self, ctx: TableContext) -> StepResult:
        todo = [c for c in ctx.active if c in ctx.state.answers and c not in ctx.state.revisions]
        version = ctx.prompt_version("revise")
        rule = ctx.effort_rule
        degraded: list[str] = []
        called: list[str] = []
        redone: list[str] = []
        notes: list[str] = []

        def problems(code: str, revision: Revision) -> list[str]:
            if not rule.enabled or revision.degraded:
                return []
            peers = {c: a for c, a in ctx.state.answers.items() if c != code}
            found = text_problems(
                revision.answer,
                rule=rule,
                question=ctx.question.text,
                expected_tokens=ctx.expected_answer_tokens,
                peers=peers,
                label=ctx.label,
            ) + ctx.citation_problems(self.name, code, revision.answer)
            if not revision.responses.strip():
                found.append("没有回应审阅意见")
            return found

        def with_decisions(revision: Revision) -> Revision:
            decisions = parse_decisions(revision.responses, ctx.to_code)
            return Revision(revision.answer, revision.responses, decisions=decisions)

        async def work(code: str) -> None:
            original = ctx.state.answers[code]
            received = reviews_received(ctx, code)
            call_id = None
            if not received:
                revision = Revision(original, "", skipped=True)  # 没有有效评审：不花钱，沿用原答案
            else:
                called.append(code)
                block = "\n\n".join(review_block(ctx.label(r.reviewer), r) for r in received)
                prompt = ctx.prompts.render(
                    "revise",
                    version,
                    code=ctx.label(code),
                    question=ctx.question.text,
                    own_answer=original,
                    reviews_of_you=ctx.scrub(block),
                )
                result = await call_and_parse(
                    ctx,
                    step=self.name,
                    role="member",
                    model_id=ctx.members[code],
                    prompt=prompt,
                    code=code,
                    parse=parse_revision,
                )
                call_id = result.call_id
                if result.value is not None:
                    revision = with_decisions(result.value)
                    first = problems(code, revision)
                    if first:
                        final = first
                        if rule.redo:
                            redone.append(code)
                            again = await redo_call(
                                ctx,
                                step=self.name,
                                role="member",
                                model_id=ctx.members[code],
                                prompt=prompt,
                                previous=result.raw_text or "",
                                reasons=first,
                                code=code,
                            )
                            retry = (
                                parse_revision(again.completion.text) if again.completion else None
                            )
                            if retry is not None:  # 重做失败或格式不符：保留第一次的修订稿
                                revision, call_id = with_decisions(retry), again.call_id
                                final = problems(code, revision)
                        status = "lazy" if final else "redone"
                        record_effort(
                            ctx,
                            EffortRecord(
                                self.name, code, status, tuple(first), tuple(final), rule.redo
                            ),
                        )
                        if final:
                            notes.append(f"{ctx.label(code)} 的修订稿被标记为敷衍")
                else:
                    degraded.append(code)
                    if result.failed_call:
                        ctx.drop(code, self.name, "调用失败")
                    # 格式不符时整段作为修订稿；调用失败时沿用原答案
                    text = (result.raw_text or "").strip()
                    revision = Revision(text or original, "", degraded=True)
            ctx.state.revisions[code] = revision
            ctx.repo.save_output(
                ctx.session_id,
                table_no=ctx.table_no,
                step=self.name,
                kind="revision",
                code=code,
                content=revision.to_dict(),
                call_id=call_id,
            )

        before = set(ctx.state.dropped)
        await gather_members(ctx, todo, work)
        return StepResult(
            self.name,
            calls=len(called) + len(redone),
            dropped=tuple(c for c in ctx.state.dropped if c not in before),
            degraded=tuple(degraded),
            notes=tuple(notes),
        )


register_step(ReviseStep())
