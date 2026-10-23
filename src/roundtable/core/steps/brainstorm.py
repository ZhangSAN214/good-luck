"""头脑风暴（R16）的步骤插件：ideate → crossbreed → vote → organize
（旧流程 ideate → expand → organize 仍可用）。

每位组员按随机抽到的发散手法独立出点子；再随机拿到几个别人的点子做碰撞杂交
（杂交 / 反转放大 / 挑刺补救）；然后匿名投票（不投自己，代码算分排名）；统筹整合成思维导图，
并为前几名写对比表、场景故事、行动方案和黑马理由
（导图同时给出 Markdown 大纲、Mermaid 和 SVG）。同一步骤所有组员使用完全相同的提示词模板和参数，
发散手法按随机种子分配、与成员是谁无关；别人的点子按随机顺序展示，且永远不让组员拓展或投票给自己的点子。
"""

from __future__ import annotations

import asyncio

from roundtable.core import ideaboard
from roundtable.core.allocation import shuffled
from roundtable.core.ideaboard import Ballot, Board, pick_dark_horse, rank
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
    parse_ballot,
    parse_board_extras,
    parse_crossbreed,
    parse_expansions,
    parse_ideas,
    parse_mindmap,
)

TAGS = ("question", "idea", "ranking")
KIND_NAMES = {
    "build": "深化",
    "combine": "组合",
    "challenge": "质疑改进",
    "new": "全新想法",
    "hybrid": "杂交",
    "flip": "反转放大",
    "fix": "挑刺补救",
}
FREE_LENS = ("自由发散", "不限手法，从原理、做法、资源、风险、反方向、跨领域类比等不同方向想")


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


def assign_lenses(ctx: TableContext) -> dict[str, tuple[str, str]]:
    """发散手法：手法清单按本步骤的随机数打乱后依次分给座位上的组员（人多时循环），与成员是谁无关。"""
    lenses = [(x.name, x.hint) for x in ctx.config.roundtable.brainstorm.lenses]
    if not lenses:
        return {}
    pool = shuffled(lenses, ctx.rng)
    return {code: pool[n % len(pool)] for n, code in enumerate(ctx.members)}


class IdeateStep:
    name = "ideate"

    async def run(self, ctx: TableContext) -> StepResult:
        b = _b(ctx)
        rule = ctx.config.roundtable.brainstorm
        lenses = assign_lenses(ctx)  # 先于一切随机抽取，恢复时结果相同
        role = ctx.prompt_role(self.name)
        if "lens" not in ctx.prompts.get(role, ctx.prompt_version(role)).variables:
            lenses = {}  # 旧版本提示词不分手法
        todo = [c for c in ctx.active if c not in b.ideas]

        async def ask(code: str) -> None:
            lens, hint = lenses.get(code, FREE_LENS)
            prompt = ctx.render(
                self.name,
                code=ctx.label(code),
                question=ctx.question.text,
                max_ideas=str(rule.max_ideas),
                lens=lens,
                lens_hint=hint,
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
            if code in lenses:
                b.lenses[code] = lens

        await asyncio.gather(*(ask(c) for c in todo))
        n = 0
        for code in sorted(b.ideas):
            numbered = []
            for i in b.ideas[code]:
                n += 1
                numbered.append(Idea(f"I{n}", i.title, i.detail))
            b.ideas[code] = tuple(numbered)
            if code in b.pending_calls:
                content = {"ideas": [i.to_dict() for i in numbered]}
                if code in b.lenses:
                    content["lens"] = b.lenses[code]
                _save(
                    ctx,
                    self.name,
                    "ideas",
                    content,
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


class CrossbreedStep:
    """碰撞杂交：每人随机拿到几个别人的点子（永远不含自己的），杂交 / 反转放大 / 挑刺补救。"""

    name = "crossbreed"

    async def run(self, ctx: TableContext) -> StepResult:
        b = _b(ctx)
        rule = ctx.config.roundtable.brainstorm
        authors = [c for c in ctx.active if c in b.ideas]
        if len(authors) < 2:
            return StepResult(self.name)
        # 每人拿到的点子先按座位顺序依次抽好（与谁先返回、是否中途恢复无关），再并发调用
        samples = {
            code: shuffled([i for c, i in b.all_ideas() if c != code], ctx.rng)[
                : rule.crossbreed_sample
            ]
            for code in authors
        }
        todo = [c for c in authors if c not in b.expansions]

        async def ask(code: str) -> None:
            mine = samples[code]
            prompt = ctx.render(
                self.name,
                code=ctx.label(code),
                question=ctx.question.text,
                ideas="\n\n".join(idea_block(ctx, i) for i in mine),
                max_items=str(rule.max_expansions),
            )
            ids = [i.id for i in mine]
            result = await call_and_parse(
                ctx,
                step=self.name,
                role="member",
                model_id=ctx.members[code],
                prompt=prompt,
                code=code,
                parse=lambda text: parse_crossbreed(text, ids, rule.max_expansions),
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

        await asyncio.gather(*(ask(c) for c in todo))
        return StepResult(self.name, calls=len(todo))


def candidate_block(ctx: TableContext, cid: str, text: str) -> str:
    return f'<idea id="{cid}">\n{neutralize(ctx.scrub(text), TAGS)}\n</idea>'


class VoteStep:
    """匿名投票：每人看到别人的点子（随机顺序，不含自己的），选出最好的几个打分，另投一个黑马。"""

    name = "vote"

    async def run(self, ctx: TableContext) -> StepResult:
        b = _b(ctx)
        rule = ctx.config.roundtable.brainstorm
        cands = b.candidates()
        voters = [c for c in ctx.active if any(x.code != c for x in cands)]
        if len({x.code for x in cands}) < 2:
            return StepResult(self.name)  # 只有一个人的点子，投票没有意义
        orders = {code: shuffled([x for x in cands if x.code != code], ctx.rng) for code in voters}
        todo = [c for c in voters if c not in b.votes]

        async def ask(code: str) -> None:
            shown = orders[code]
            prompt = ctx.render(
                self.name,
                code=ctx.label(code),
                question=ctx.question.text,
                ideas="\n\n".join(candidate_block(ctx, x.id, x.text) for x in shown),
                max_picks=str(rule.vote_picks),
            )
            ids = [x.id for x in shown]
            result = await call_and_parse(
                ctx,
                step=self.name,
                role="member",
                model_id=ctx.members[code],
                prompt=prompt,
                code=code,
                parse=lambda text: parse_ballot(text, ids, rule.vote_picks),
            )
            ballot: Ballot = result.value or Ballot(degraded=True)
            b.votes[code] = ballot
            _save(ctx, self.name, "votes", ballot.to_dict(), code=code, call_id=result.call_id)

        await asyncio.gather(*(ask(c) for c in todo))
        return StepResult(self.name, calls=len(todo))


def organize_block(ctx: TableContext) -> tuple[str, dict[str, str]]:
    """统筹看到的材料：想法按随机顺序，各自带上拓展；返回（文本，编号 → 提出者代号）。"""
    b = _b(ctx)
    authors = dict(b.authors())
    by_idea: dict[str, list[tuple[str, str, Expansion]]] = {}
    free: list[tuple[str, Expansion]] = []
    for xid, code, x in b.numbered_expansions():
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
            attrs = f'id="{xid}" on="{idea.id}"' + (f' with="{x.also}"' if x.also else "")
            lines.append(f'<idea {attrs} kind="{KIND_NAMES[x.kind]}">\n{text}\n</idea>')
        chunks.append("\n".join(lines))
    for xid, x in free:
        text = neutralize(ctx.scrub(x.text), TAGS)
        chunks.append(f'<idea id="{xid}" kind="{KIND_NAMES["new"]}">\n{text}\n</idea>')
    return "\n\n".join(chunks), authors


def render_all(m: MindMap, board: Board | None = None) -> dict:
    out = {
        "map": m.to_dict(),
        "markdown": to_markdown(m),
        "mermaid": to_mermaid(m),
        "svg": to_svg(m),
    }
    if board is not None:
        out["board"] = board.to_dict()
        # 完整文档（点子榜各部分 + 导图大纲），网页一键导出
        out["document"] = ideaboard.to_markdown(board) + out["markdown"]
    return out


def ranking_text(board: Board) -> str:
    """统筹看到的排名（只有编号与得分，点子内容在 <idea> 里）。"""
    if not any(r.voted for r in board.ranking):
        return "没有投票结果"
    voted = [r for r in board.ranking if r.voted]
    rows = [f"{n}. {r.id}：{r.score} 分，{r.votes} 票" for n, r in enumerate(voted, 1)]
    return "\n".join(rows)


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
        cands = b.candidates()
        ranking = tuple(rank(cands, b.votes, ctx.label)) if b.votes else ()
        top = [r.id for r in ranking if r.votes][: rule.top]
        dark = pick_dark_horse(ranking, rule.top)
        board = Board(
            ranking,
            rule.top,
            dark_horse=dark.id if dark else "",
            lenses={ctx.label(c): name for c, name in sorted(b.lenses.items())},
        )
        story_ids = top[: rule.stories]
        plan_id = top[0] if top else ""
        none = "无"
        prompt = ctx.render(
            self.name,
            question=ctx.question.text,
            ideas=block,
            ranking=neutralize(ranking_text(board), TAGS),
            top_ids="、".join(top) or none,
            story_ids="、".join(story_ids) or none,
            plan_id=plan_id or none,
            dark_horse_id=board.dark_horse or none,
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
        if ranking or b.lenses:
            extras = (
                parse_board_extras(
                    result.raw_text,
                    top_ids=top,
                    story_ids=story_ids,
                    plan_id=plan_id,
                    dark_horse=board.dark_horse,
                )
                if result.value is not None and result.raw_text and ranking
                else {}
            )
            board = Board(
                board.ranking,
                board.top,
                extras.get("compare", ()),
                extras.get("stories", ()),
                extras.get("plan"),
                board.dark_horse,
                extras.get("dark_horse_why", ""),
                board.lenses,
            )
            b.board = board
        payload = render_all(mindmap, b.board)
        b.svg = payload["svg"]
        _save(ctx, self.name, "mindmap", payload, call_id=result.call_id)
        notes = ("统筹没能整合出导图，已按成员列出各自的想法",) if mindmap.degraded else ()
        return StepResult(
            self.name,
            calls=1,
            degraded=("coordinator",) if mindmap.degraded else (),
            notes=notes,
        )


for _step in (IdeateStep(), ExpandStep(), CrossbreedStep(), VoteStep(), OrganizeStep()):
    register_step(_step)
