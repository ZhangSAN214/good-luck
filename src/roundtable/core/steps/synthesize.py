"""汇总：统筹（不是组员）阅读各组员的最终稿（匿名、乱序），给出共识、分歧和最终答案。"""

from __future__ import annotations

from roundtable.core.allocation import shuffled
from roundtable.core.routing import OutcomeSignals

from .base import StepFailed, StepResult, TableContext, answer_block, call_and_parse, register_step
from .schemas import Synthesis, TableState, fallback_synthesis, parse_synthesis


def latest_texts(ctx: TableContext) -> dict[str, str]:
    """每个组员的最终稿：有修订用修订，否则用原答案（中途退出的组员已有的内容也保留）。"""
    return {
        code: (ctx.state.revisions[code].answer if code in ctx.state.revisions else answer)
        for code, answer in ((c, ctx.state.answers.get(c)) for c in ctx.members)
        if answer
    }


class SynthesizeStep:
    name = "synthesize"

    async def run(self, ctx: TableContext) -> StepResult:
        if ctx.state.synthesis is not None:
            return StepResult(self.name)
        if not ctx.coordinator:
            raise StepFailed("该方案没有统筹，不能执行汇总步骤")
        if ctx.coordinator in ctx.members.values():
            raise StepFailed("统筹不能兼任组员")
        texts = latest_texts(ctx)
        if not texts:
            raise StepFailed("没有可汇总的答案")
        order = shuffled(sorted(texts), ctx.rng)
        block = "\n\n".join(
            answer_block(ctx.label(c), ctx.scrub(texts[c]), flagged=ctx.state.flagged(c))
            for c in order
        )
        prompt = ctx.prompts.render(
            "synthesize",
            ctx.prompt_version("synthesize"),
            question=ctx.question.text,
            revised_answers=block,
        )
        result = await call_and_parse(
            ctx,
            step=self.name,
            role="coordinator",
            model_id=ctx.coordinator,
            prompt=prompt,
            parse=lambda text: parse_synthesis(text, ctx.to_code),
        )
        synthesis: Synthesis = result.value or fallback_synthesis(result.error or "未知错误")
        ctx.state.synthesis = synthesis
        ctx.repo.save_output(
            ctx.session_id,
            table_no=ctx.table_no,
            step=self.name,
            kind="synthesis",
            content=synthesis.to_dict(),
            call_id=result.call_id,
        )
        return StepResult(
            self.name, calls=1, degraded=("coordinator",) if synthesis.degraded else ()
        )


register_step(SynthesizeStep())


def outcome_signals(state: TableState) -> OutcomeSignals | None:
    """供升级判断：未解决的分歧数与把握程度。汇总降级时视为把握低。"""
    s = state.synthesis
    if s is None:
        return None
    confidence = "low" if s.degraded else s.output.confidence
    return OutcomeSignals(s.unresolved_disagreements, confidence)


def final_answer(state: TableState) -> str | None:
    """本桌的最终答案：有汇总用汇总；只有一个组员时用他的最终稿。"""
    if state.synthesis is not None:
        return state.synthesis.output.final_answer
    if len(state.answers) == 1:
        ((code, answer),) = state.answers.items()
        return state.revisions[code].answer if code in state.revisions else answer
    return None
