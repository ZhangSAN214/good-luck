"""修订：每个组员只看到别人对自己答案的有效评审（乱序），据此修订。"""

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
from .schemas import CheckedReview, Revision, parse_revision


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
        degraded: list[str] = []
        called: list[str] = []

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
                    revision = result.value
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
            calls=len(called),
            dropped=tuple(c for c in ctx.state.dropped if c not in before),
            degraded=tuple(degraded),
        )


register_step(ReviseStep())
