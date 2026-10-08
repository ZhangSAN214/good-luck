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
from .effort import record_effort, redo_call
from .schemas import EffortRecord, ReviewParse, parse_reviews


def review_problems(parsed: ReviewParse, expected: list[str]) -> list[str]:
    """一位评审者的整份评审是否敷衍：应评审的答案一份有效评审都没有。"""
    if not expected or any(r.valid for r in parsed.reviews):
        return []
    if not parsed.reviews:
        return ["没有给出任何有效评审"]
    reasons = dict.fromkeys(reason for r in parsed.reviews for reason in r.invalid_reasons)
    return [f"所有评审都无效：{'；'.join(reasons)}"]


class ReviewStep:
    name = "review"

    async def run(self, ctx: TableContext) -> StepResult:
        authors = [c for c in ctx.members if c in ctx.state.answers]
        reviewers = [c for c in ctx.active if c in ctx.state.answers]
        plan = review_assignments(authors, ctx.rng, ctx.config.roundtable.reviews_per_answer)
        todo = [c for c in reviewers if c not in ctx.state.reviews]
        version = ctx.prompt_version("review")
        quality = ctx.config.roundtable.review_quality
        rule = ctx.effort_rule
        degraded: list[str] = []
        notes: list[str] = []
        redone: list[str] = []

        async def work(reviewer: str) -> None:
            targets = [t for t in plan[reviewer] if t != reviewer]  # 双重保险：不评自己
            if not targets:
                ctx.state.reviews[reviewer] = ()
                return
            peers = "\n\n".join(
                answer_block(ctx.label(t), ctx.scrub(ctx.state.answers[t] + ctx.files_note(t)))
                for t in targets
            )
            prompt = ctx.prompts.render(
                "review",
                version,
                code=ctx.label(reviewer),
                question=ctx.question.text,
                peer_answers=peers,
            )

            def parse(text: str) -> ReviewParse:
                return parse_reviews(
                    text,
                    reviewer=reviewer,
                    expected=targets,
                    to_code=ctx.to_code,
                    quality=quality,
                )

            result = await call_and_parse(
                ctx,
                step=self.name,
                role="member",
                model_id=ctx.members[reviewer],
                prompt=prompt,
                code=reviewer,
                parse=parse,
            )
            if result.failed_call:
                ctx.drop(reviewer, self.name, "调用失败")
            value, call_id = result.value, result.call_id
            first = review_problems(value, targets) if value is not None and rule.enabled else []
            if first:
                final = first
                if rule.redo:
                    redone.append(reviewer)
                    again = await redo_call(
                        ctx,
                        step=self.name,
                        role="member",
                        model_id=ctx.members[reviewer],
                        prompt=prompt,
                        previous=result.raw_text or "",
                        reasons=first,
                        code=reviewer,
                    )
                    if again.completion is not None:
                        try:
                            retry = parse(again.completion.text)
                        except ValueError:  # 重做的输出格式不符：保留第一次的评审
                            retry = None
                        if retry is not None:
                            value, call_id, final = (
                                retry,
                                again.call_id,
                                review_problems(retry, targets),
                            )
                record_effort(
                    ctx,
                    EffortRecord(
                        self.name,
                        reviewer,
                        "lazy" if final else "redone",
                        tuple(first),
                        tuple(final),
                    ),
                )
                if final:
                    notes.append(f"{ctx.label(reviewer)} 的评审被标记为敷衍")
            if value is None:
                degraded.append(reviewer)
                reviews, missing, ignored = (), tuple(targets), ()
            else:
                reviews, missing, ignored = value.reviews, value.missing, value.ignored
                if missing:
                    notes.append(f"{ctx.label(reviewer)} 漏评了 {len(missing)} 份答案")
            ctx.state.reviews[reviewer] = reviews
            ctx.repo.save_output(
                ctx.session_id,
                table_no=ctx.table_no,
                step=self.name,
                kind="review",
                code=reviewer,
                call_id=call_id,
                content={
                    "reviews": [r.to_dict() for r in reviews],
                    "missing": list(missing),
                    "ignored": list(ignored),
                    "degraded": value is None,
                    "error": result.error if value is None else None,
                },
            )

        before = set(ctx.state.dropped)
        await gather_members(ctx, todo, work)
        return StepResult(
            self.name,
            calls=len(todo) + len(redone),
            dropped=tuple(c for c in ctx.state.dropped if c not in before),
            degraded=tuple(degraded),
            notes=tuple(notes),
        )


register_step(ReviewStep())
