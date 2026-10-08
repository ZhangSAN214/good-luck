"""匿名互评：每份答案由 reviews_per_answer 位其他组员评审（永远不评自己），顺序独立打乱。"""

from __future__ import annotations

from roundtable.core.allocation import review_assignments

from .base import (
    StepResult,
    TableContext,
    answer_block,
    call_and_parse,
    gather_members,
    register_step,
)
from .schemas import parse_reviews


class ReviewStep:
    name = "review"

    async def run(self, ctx: TableContext) -> StepResult:
        authors = [c for c in ctx.members if c in ctx.state.answers]
        reviewers = [c for c in ctx.active if c in ctx.state.answers]
        plan = review_assignments(authors, ctx.rng, ctx.config.roundtable.reviews_per_answer)
        todo = [c for c in reviewers if c not in ctx.state.reviews]
        version = ctx.prompt_version("review")
        quality = ctx.config.roundtable.review_quality
        degraded: list[str] = []
        notes: list[str] = []

        async def work(reviewer: str) -> None:
            targets = [t for t in plan[reviewer] if t != reviewer]  # 双重保险：不评自己
            if not targets:
                ctx.state.reviews[reviewer] = ()
                return
            peers = "\n\n".join(
                answer_block(ctx.label(t), ctx.scrub(ctx.state.answers[t])) for t in targets
            )
            prompt = ctx.prompts.render(
                "review",
                version,
                code=ctx.label(reviewer),
                question=ctx.question.text,
                peer_answers=peers,
            )
            result = await call_and_parse(
                ctx,
                step=self.name,
                role="member",
                model_id=ctx.members[reviewer],
                prompt=prompt,
                code=reviewer,
                parse=lambda text: parse_reviews(
                    text,
                    reviewer=reviewer,
                    expected=targets,
                    to_code=ctx.to_code,
                    quality=quality,
                ),
            )
            if result.failed_call:
                ctx.drop(reviewer, self.name, "调用失败")
            if result.value is None:
                degraded.append(reviewer)
                reviews, missing, ignored = (), tuple(targets), ()
            else:
                reviews, missing, ignored = (
                    result.value.reviews,
                    result.value.missing,
                    result.value.ignored,
                )
                if missing:
                    notes.append(f"{ctx.label(reviewer)} 漏评了 {len(missing)} 份答案")
            ctx.state.reviews[reviewer] = reviews
            ctx.repo.save_output(
                ctx.session_id,
                table_no=ctx.table_no,
                step=self.name,
                kind="review",
                code=reviewer,
                call_id=result.call_id,
                content={
                    "reviews": [r.to_dict() for r in reviews],
                    "missing": list(missing),
                    "ignored": list(ignored),
                    "degraded": result.value is None,
                    "error": result.error,
                },
            )

        before = set(ctx.state.dropped)
        await gather_members(ctx, todo, work)
        return StepResult(
            self.name,
            calls=len(todo),
            dropped=tuple(c for c in ctx.state.dropped if c not in before),
            degraded=tuple(degraded),
            notes=tuple(notes),
        )


register_step(ReviewStep())
