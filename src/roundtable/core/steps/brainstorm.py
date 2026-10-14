"""头脑风暴（R16）的步骤插件：ideate → expand → organize。

每位组员独立发散；再看别人的想法做拓展（深化 / 组合 / 质疑改进 / 全新）；统筹整合成思维导图
（同时给出 Markdown 大纲、Mermaid 和 SVG）。同一步骤所有组员使用完全相同的提示词和参数；
别人的想法按随机顺序展示，且永远不让组员拓展自己的想法。
"""

from __future__ import annotations

import asyncio

from roundtable.core.allocation import shuffled
from roundtable.core.mindmap import MindMap, to_markdown, to_mermaid, to_svg

from .base import (
    StepFailed,
    StepResult,
    TableContext,
    call_and_parse,
    neutralize,
    register_step,
)
from .brain_schemas import (
    BrainState,
    Expansion,
    Idea,
    fallback_mindmap,
    parse_expansions,
    parse_ideas,
    parse_mindmap,
)

TAGS = ("question", "idea")
KIND_NAMES = {"build": "深化", "combine": "组合", "challenge": "质疑改进", "new": "全新想法"}


def _b(ctx: TableContext) -> BrainState:
    return ctx.state.brain


def _save(ctx: TableContext, step: str, kind: str, content, code=None, call_id=None) -> None:
    ctx.repo.save_output(
        ctx.session_id,
        table_no=ctx.table_no,
        step=step,
        kind=kind,
        code=code,
        content=content,
        call_id=call_id,
    )


def idea_block(ctx: TableContext, idea: Idea) -> str:
    body = idea.title + (f"：{idea.detail}" if idea.detail else "")
    return f'<idea id="{idea.id}">\n{neutralize(ctx.scrub(body), TAGS)}\n</idea>'


class IdeateStep:
    name = "ideate"

    async def run(self, ctx: TableContext) -> StepResult:
        b = _b(ctx)
        rule = ctx.config.roundtable.brainstorm
        todo = [c for c in ctx.active if c not in b.ideas]

        async def ask(code: str) -> None:
            prompt = ctx.render(
                self.name,
                code=ctx.label(code),
                question=ctx.question.text,
                max_ideas=str(rule.max_ideas),
            )
            result = await call_and_parse(
                ctx,
                step=self.name,
                role="member",
                model_id=ctx.members[code],
                prompt=prompt,
                code=code,
                parse=lambda text: parse_ideas(text, rule.max_ideas),
            )
            if result.value is None:
                reason = "调用失败" if result.failed_call else "没有给出可用的想法"
                ctx.drop(code, self.name, reason)
                return
            # 编号在全部想法收齐后统一分配（按代号排序），与谁先返回无关
            b.ideas[code] = tuple(Idea("", t, d) for t, d in result.value)
            b.pending_calls[code] = result.call_id

        await asyncio.gather(*(ask(c) for c in todo))
        n = 0
        for code in sorted(b.ideas):
            numbered = []
            for i in b.ideas[code]:
                n += 1
                numbered.append(Idea(f"I{n}", i.title, i.detail))
            b.ideas[code] = tuple(numbered)
            if code in b.pending_calls:
                _save(
                    ctx,
                    self.name,
                    "ideas",
                    {"ideas": [i.to_dict() for i in numbered]},
                    code=code,
                    call_id=b.pending_calls.pop(code),
                )
        if not b.ideas:
            raise StepFailed("没有任何组员给出可用的想法")
        return StepResult(self.name, calls=len(todo))


class ExpandStep:
    name = "expand"

    async def run(self, ctx: TableContext) -> StepResult:
        b = _b(ctx)
        rule = ctx.config.roundtable.brainstorm
        authors = [c for c in ctx.active if c in b.ideas]
        if len(authors) < 2:
            return StepResult(self.name)  # 只有一个人的想法，没有"别人的想法"可拓展
        todo = [c for c in authors if c not in b.expansions]

        async def ask(code: str) -> None:
            others = [i for c, i in b.all_ideas() if c != code and c in ctx.active]
            block = "\n\n".join(idea_block(ctx, i) for i in shuffled(others, ctx.rng))
            prompt = ctx.render(
                self.name,
                code=ctx.label(code),
                question=ctx.question.text,
                ideas=block,
                max_items=str(rule.max_expansions),
            )
            ids = [i.id for i in others]
            result = await call_and_parse(
                ctx,
                step=self.name,
                role="member",
                model_id=ctx.members[code],
                prompt=prompt,
                code=code,
                parse=lambda text: parse_expansions(text, ids, rule.max_expansions),
            )
            items: tuple[Expansion, ...] = result.value or ()
            b.expansions[code] = items
            _save(
                ctx,
                self.name,
                "expansions",
                {"items": [x.to_dict() for x in items], "degraded": result.value is None},
                code=code,
                call_id=result.call_id,
            )

        # 依次进行：每位组员看到的顺序由同一个步骤随机数依次抽取，结果可复现
        for code in todo:
            await ask(code)
        return StepResult(self.name, calls=len(todo))


def organize_block(ctx: TableContext) -> tuple[str, dict[str, str]]:
    """统筹看到的材料：想法按随机顺序，各自带上拓展；返回（文本，编号 → 提出者代号）。"""
    b = _b(ctx)
    authors = dict(b.authors())
    by_idea: dict[str, list[tuple[str, str, Expansion]]] = {}
    free: list[tuple[str, Expansion]] = []
    n = 0
    for code in sorted(b.expansions):
        for x in b.expansions[code]:
            n += 1
            xid = f"E{n}"
            authors[xid] = code
            if x.on:
                by_idea.setdefault(x.on, []).append((xid, code, x))
            else:
                free.append((xid, x))
    chunks = []
    ideas = [i for _, i in b.all_ideas()]
    for idea in shuffled(ideas, ctx.rng):
        lines = [idea_block(ctx, idea)]
        for xid, _, x in by_idea.get(idea.id, []):
            text = neutralize(ctx.scrub(x.text), TAGS)
            lines.append(
                f'<idea id="{xid}" on="{idea.id}" kind="{KIND_NAMES[x.kind]}">\n{text}\n</idea>'
            )
        chunks.append("\n".join(lines))
    for xid, x in free:
        text = neutralize(ctx.scrub(x.text), TAGS)
        chunks.append(f'<idea id="{xid}" kind="{KIND_NAMES["new"]}">\n{text}\n</idea>')
    return "\n\n".join(chunks), authors


def render_all(m: MindMap) -> dict:
    return {
        "map": m.to_dict(),
        "markdown": to_markdown(m),
        "mermaid": to_mermaid(m),
        "svg": to_svg(m),
    }


class OrganizeStep:
    name = "organize"

    async def run(self, ctx: TableContext) -> StepResult:
        b = _b(ctx)
        rule = ctx.config.roundtable.brainstorm
        if b.mindmap is not None:
            return StepResult(self.name)
        if not ctx.coordinator:
            raise StepFailed("头脑风暴需要统筹来整合")
        if ctx.coordinator in ctx.members.values():
            raise StepFailed("统筹不能兼任组员")
        if not b.ideas:
            raise StepFailed("没有可整合的想法")
        block, authors = organize_block(ctx)
        prompt = ctx.render(
            self.name,
            question=ctx.question.text,
            ideas=block,
            max_branches=str(rule.max_branches),
            max_ideas_per_branch=str(rule.max_ideas_per_branch),
            max_next_steps=str(rule.max_next_steps),
        )
        result = await call_and_parse(
            ctx,
            step=self.name,
            role="coordinator",
            model_id=ctx.coordinator,
            prompt=prompt,
            parse=lambda text: parse_mindmap(
                text,
                authors,
                ctx.label,
                max_branches=rule.max_branches,
                max_ideas_per_branch=rule.max_ideas_per_branch,
                max_next_steps=rule.max_next_steps,
            ),
        )
        mindmap: MindMap = result.value or fallback_mindmap(
            ctx.question.text[:20],
            b.ideas,
            ctx.label,
            max_branches=rule.max_branches,
            max_ideas_per_branch=rule.max_ideas_per_branch,
        )
        b.mindmap = mindmap
        payload = render_all(mindmap)
        b.svg = payload["svg"]
        _save(ctx, self.name, "mindmap", payload, call_id=result.call_id)
        notes = ("统筹没能整合出导图，已按成员列出各自的想法",) if mindmap.degraded else ()
        return StepResult(
            self.name,
            calls=1,
            degraded=("coordinator",) if mindmap.degraded else (),
            notes=notes,
        )


for _step in (IdeateStep(), ExpandStep(), OrganizeStep()):
    register_step(_step)
