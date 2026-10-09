"""协同模式的步骤插件：

    decompose → volunteer → assign → work → cross_review → rework → merge

统筹拆分子任务；组员自荐；统筹分配（代码校验：每人至少一块、每块至少一人、负担均衡，
不合格重试一次，仍不行由代码补齐）；按依赖分批并行完成；交叉审查（永远不审自己的）；
作者按有效审查修改；统筹合并成完整成果并标注每份成果的采纳情况。
所有成员产出都做实质内容检查（不合格打回重做一次，仍不合格标记敷衍）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from roundtable.core.allocation import shuffled
from roundtable.core.config.schema import KindRule
from roundtable.core.media import KIND_LABELS, KINDS, Placement

from .base import (
    StepFailed,
    StepResult,
    TableContext,
    call_and_parse,
    call_model,
    neutralize,
    register_step,
    review_block,
)
from .collab_schemas import (
    STANCE_SCORE,
    Assignment,
    CollabState,
    Merge,
    Subtask,
    Volunteer,
    coverage_problems,
    default_volunteer,
    fallback_decomposition,
    fallback_merge,
    layers,
    parse_assignment,
    parse_decomposition,
    parse_merge,
    parse_volunteer,
    repair_assignment,
    volunteer_problems,
)
from .effort import record_effort, redo_call, text_problems
from .media import media_gate, media_payload
from .pipeline import (
    ancestors,
    assignment_conflicts,
    build_template,
    normalize_media,
    pick_template,
    pipeline_problems,
    solve_assignment,
)
from .review import review_problems
from .schemas import (
    CheckedReview,
    EffortRecord,
    ReviewParse,
    Revision,
    parse_decisions,
    parse_reviews,
    parse_revision,
)

TAGS = ("question", "subtask", "your_subtask", "dependency", "work", "plan", "volunteer")


def _c(ctx: TableContext) -> CollabState:
    return ctx.state.collab


def _require_coordinator(ctx: TableContext) -> str:
    if not ctx.coordinator:
        raise StepFailed("协同模式需要统筹")
    if ctx.coordinator in ctx.members.values():
        raise StepFailed("统筹不能兼任组员")
    return ctx.coordinator


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


def subtask_block(s: Subtask, tag: str = "subtask", ctx: TableContext | None = None) -> str:
    lines = [f"标题：{s.title}"]
    rule = ctx.config.roundtable.collab.kinds.get(s.kind or "") if ctx is not None else None
    if rule is not None:
        lines.append(f"类型：{rule.label}")
    if s.requirements:
        lines.append(f"要求：{s.requirements}")
    if s.acceptance:
        lines.append(f"验收标准：{s.acceptance}")
    if s.tags:
        lines.append(f"建议能力标签：{', '.join(s.tags)}")
    if s.depends_on:
        lines.append(f"依赖：{', '.join(s.depends_on)}")
    body = neutralize("\n".join(lines), TAGS)
    return f'<{tag} id="{s.id}">\n{body}\n</{tag}>'


def subtasks_block(subtasks: Sequence[Subtask], ctx: TableContext | None = None) -> str:
    return "\n\n".join(subtask_block(s, ctx=ctx) for s in subtasks)


# --- 流水线辅助 ----------------------------------------------------------------


def pipeline_wanted(ctx: TableContext) -> bool:
    """成员够多且配置了类型与模板时，按流水线拆分（否则沿用旧的拆分方式）。"""
    rules = ctx.config.roundtable.collab
    return (
        rules.pipeline.enabled
        and bool(rules.kinds)
        and bool(rules.templates)
        and len(ctx.active) >= rules.pipeline.min_members
    )


def kind_rule(ctx: TableContext, s: Subtask) -> KindRule | None:
    """流水线子任务的类型规则；旧流程的子任务（没有类型）返回 None。"""
    if not _c(ctx).pipeline:
        return None
    return ctx.config.roundtable.collab.kinds.get(s.kind or "")


def kinds_text(ctx: TableContext) -> str:
    """给统筹的类型词表：每种类型的含义和规则（来自配置）。"""
    kinds = ctx.config.roundtable.collab.kinds
    lines = []
    for name, k in kinds.items():
        rules = []
        rules.append(
            "可按内容拆成多个平行子任务" if k.parallel else "只能一人负责，不能拆成平行子任务"
        )
        if k.needs:
            rules.append(
                "必须依赖「" + "、".join(kinds[x].label for x in k.needs) + "」类型的子任务"
            )
        if k.audit:
            rules.append("必须依赖被审查的成果（非审查类子任务）")
        if k.min_upstream:
            rules.append(f"至少依赖 {k.min_upstream} 个上游子任务")
        if k.requires_media:
            rules.append("需要媒体生成模型，并标注 media")
        if k.avoid_hard or k.avoid_soft:
            rules.append(
                "负责人不能同时负责上游的「"
                + "、".join(kinds[x].label for x in [*k.avoid_hard, *k.avoid_soft])
                + "」"
            )
        meaning = f"：{k.meaning}" if k.meaning else ""
        lines.append(f"- {name}（{k.label}）{meaning}。{'；'.join(rules)}。")
    return "\n".join(lines)


def available_tools(ctx: TableContext) -> list[str]:
    return ctx.toolbox.tools_for("work") if ctx.toolbox else []


def has_image_attachment(ctx: TableContext) -> bool:
    return any(a.kind == "image" for a in ctx.attachments)


def _tags_at_table(ctx: TableContext) -> list[str]:
    by_id = {m.id: m for m in ctx.config.models.models}
    tags: dict[str, None] = {}
    for model in ctx.members.values():
        for t in by_id[model].tags if model in by_id else ():
            tags[t] = None
    return list(tags) or list(ctx.config.models.tag_vocabulary)


def _member_tags(ctx: TableContext) -> dict[str, tuple[str, ...]]:
    by_id = {m.id: m for m in ctx.config.models.models}
    return {c: tuple(by_id[m].tags) if m in by_id else () for c, m in ctx.members.items()}


def media_kinds(ctx: TableContext) -> list[str]:
    """当前可以生成的媒体种类（有可用的模型）。"""
    if ctx.media is None:
        return []
    return [k for k in KINDS if ctx.media.unavailable_reason(k, ctx.media_tier) is None]


def media_kinds_text(kinds: Sequence[str]) -> str:
    if not kinds:
        return "本次没有可用的媒体生成能力，所有子任务的 media 一律写 null。"
    listed = "、".join(f"{k}（{KIND_LABELS[k]}）" for k in kinds)
    return f"media 只能取这些值：{listed}，或 null。"


def media_prompt(ctx: TableContext, role: str, kind: str, **values: str):
    """媒体类子任务使用专门的提示词（成员写的是生成提示词）。"""
    template = ctx.prompts.get(role, ctx.prompt_version(role))
    return template.render(
        medium=KIND_LABELS[kind], **{k: v for k, v in values.items() if k in template.variables}
    )


async def make_media(
    ctx: TableContext, kind: str, text: str, step: str, sid: str, code: str, round_no: int
) -> dict:
    assert ctx.media is not None
    res = await ctx.media.generate(
        kind, text, Placement(ctx.table_no, step, round_no, code, sid), tier=ctx.media_tier
    )
    return media_payload(res, kind, round_no)


def _item_expected(ctx: TableContext) -> int | None:
    """单个子任务的预估长度：整题长度按子任务数平分。"""
    if ctx.expected_answer_tokens is None:
        return None
    return ctx.expected_answer_tokens // max(1, len(_c(ctx).subtasks))


# --- 拆分 ---------------------------------------------------------------------


class DecomposeStep:
    name = "decompose"

    async def run(self, ctx: TableContext) -> StepResult:
        c = _c(ctx)
        if c.subtasks:
            return StepResult(self.name)
        coordinator = _require_coordinator(ctx)
        rules = ctx.config.roundtable.collab
        tags = _tags_at_table(ctx)
        kinds = media_kinds(ctx)
        pipeline = pipeline_wanted(ctx)
        members = len(ctx.active)
        values = dict(
            question=ctx.question.text,
            member_count=str(members),
            max_subtasks=str(rules.max_subtasks),
            tags=", ".join(tags),
            media_kinds=media_kinds_text(kinds),
            kinds=kinds_text(ctx),
        )

        def parse(text: str):
            return parse_decomposition(
                text,
                max_subtasks=rules.max_subtasks,
                vocabulary=tags,
                media_kinds=kinds,
                kinds=rules.kinds if pipeline else (),
            )

        def check(value) -> tuple[tuple[Subtask, ...], list[str]]:
            value = normalize_media(value, rules, kinds)
            return value, pipeline_problems(
                value,
                rules=rules,
                members=members,
                question=ctx.question.text,
                media_available=kinds,
            )

        first = await call_and_parse(
            ctx,
            step=self.name,
            role="coordinator",
            model_id=coordinator,
            prompt=ctx.render(self.name, **values),
            parse=parse,
        )
        calls = 1
        info: dict = {"source": "coordinator", "problems": []}
        subtasks: tuple[Subtask, ...] | None = None
        call_id = first.call_id
        error = first.error
        if not pipeline:
            subtasks = first.value
        else:
            problems: list[str] = []
            if first.value is not None:
                value, problems = check(first.value)
                if not problems:
                    subtasks = value
            else:
                problems = [first.error or "输出格式不符，无法解析成 JSON"]
            if subtasks is None:  # 不合格：把具体问题列给统筹，重拆一次
                info["problems"] = problems
                retry = await call_and_parse(
                    ctx,
                    step=self.name,
                    role="coordinator",
                    model_id=coordinator,
                    prompt=ctx.render(
                        "decompose_retry",
                        **values,
                        previous=first.raw_text or "（上次没有可用的输出）",
                        problems="\n".join(f"- {p}" for p in problems),
                    ),
                    parse=parse,
                )
                calls += 1
                call_id = retry.call_id
                if retry.value is not None:
                    value, again = check(retry.value)
                    if not again:
                        subtasks, info["source"] = value, "retry"
                    else:
                        info["retry_problems"] = again
                else:
                    info["retry_problems"] = [retry.error or "输出格式不符"]
        degraded = subtasks is None
        notes: tuple[str, ...] = ()
        if subtasks is None and pipeline:
            template = pick_template(
                rules,
                question=ctx.question.text,
                image_attachment=has_image_attachment(ctx),
                media_available=kinds,
                tools=available_tools(ctx),
            )
            subtasks = build_template(template, members, rules.max_subtasks)
            info.update(source="template", template=template.name, template_label=template.label)
            why = "；".join(info.get("retry_problems") or info["problems"])
            notes = (f"统筹两次都没有拆出合格的流水线（{why}），已按模板「{template.label}」生成",)
        elif subtasks is None:
            subtasks = fallback_decomposition()
            notes = ("统筹的拆分不可用，整道题作为一个子任务由全员各自完成",)
        elif info["source"] == "retry":
            notes = ("统筹的第一次拆分不符合流水线规则，已按指出的问题重拆",)
        c.subtasks, c.subtasks_degraded = subtasks, degraded
        c.pipeline, c.pipeline_info = pipeline, info
        _save(
            ctx,
            self.name,
            "subtasks",
            {
                "subtasks": [s.to_dict() for s in c.subtasks],
                "degraded": degraded,
                "error": error if degraded else None,
                "pipeline": pipeline,
                "info": info,
            },
            call_id=call_id,
        )
        return StepResult(
            self.name, calls=calls, degraded=("coordinator",) if degraded else (), notes=notes
        )


# --- 自荐 ---------------------------------------------------------------------


class VolunteerStep:
    name = "volunteer"

    async def run(self, ctx: TableContext) -> StepResult:
        c = _c(ctx)
        ids = [s.id for s in c.subtasks]
        todo = [code for code in ctx.active if code not in c.volunteers]
        rule = ctx.effort_rule
        min_reason = ctx.config.roundtable.review_quality.min_checked_chars
        block = subtasks_block(c.subtasks, ctx)
        degraded: list[str] = []
        redone: list[str] = []
        notes: list[str] = []

        async def work(code: str) -> None:
            prompt = ctx.render(
                self.name, code=ctx.label(code), question=ctx.question.text, subtasks=block
            )

            def parse(text: str) -> Volunteer:
                return parse_volunteer(text, ids)

            result = await call_and_parse(
                ctx,
                step=self.name,
                role="member",
                model_id=ctx.members[code],
                prompt=prompt,
                code=code,
                parse=parse,
            )
            if result.failed_call:
                ctx.drop(code, self.name, "调用失败")
                return
            value: Volunteer | None = result.value
            call_id = result.call_id
            first = volunteer_problems(value, min_reason) if value and rule.enabled else []
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
                    retry = _try(parse, again.completion.text) if again.completion else None
                    if retry is not None:
                        value, call_id = retry, again.call_id
                        final = volunteer_problems(retry, min_reason)
                status = "lazy" if final else "redone"
                record_effort(
                    ctx,
                    EffortRecord(self.name, code, status, tuple(first), tuple(final), rule.redo),
                )
                if final:
                    notes.append(f"{ctx.label(code)} 的自荐被标记为敷衍")
            if value is None:
                degraded.append(code)
                value = default_volunteer(ids)
            c.volunteers[code] = value
            _save(ctx, self.name, "volunteer", value.to_dict(), code=code, call_id=call_id)

        before = set(ctx.state.dropped)
        await asyncio.gather(*(work(code) for code in todo))
        return StepResult(
            self.name,
            calls=len(todo) + len(redone),
            dropped=tuple(x for x in ctx.state.dropped if x not in before),
            degraded=tuple(degraded),
            notes=tuple(notes),
        )


def _try(parse, text: str):
    try:
        return parse(text)
    except ValueError:
        return None


# --- 分配 ---------------------------------------------------------------------


def volunteers_block(ctx: TableContext) -> str:
    c = _c(ctx)
    tags = _member_tags(ctx)
    titles = {s.id: s.title for s in c.subtasks}
    blocks = []
    for code in ctx.active:
        v = c.volunteers.get(code)
        lines = [f"能力标签：{', '.join(tags.get(code, ())) or '无'}"]
        if v is not None:
            lines.append(f"擅长：{v.strengths or '（未说明）'}")
            for sid, (stance, reason) in v.preferences.items():
                lines.append(f"{sid}「{titles.get(sid, '')}」：{stance}　{reason}")
        body = neutralize("\n".join(lines), TAGS)
        blocks.append(f'<volunteer code="{ctx.label(code)}">\n{ctx.scrub(body)}\n</volunteer>')
    return "\n\n".join(blocks)


class AssignStep:
    name = "assign"

    async def run(self, ctx: TableContext) -> StepResult:
        c = _c(ctx)
        if c.assignment is not None:
            return StepResult(self.name)
        coordinator = _require_coordinator(ctx)
        ids = [s.id for s in c.subtasks]
        codes = list(ctx.active)
        prompt = ctx.render(
            self.name,
            question=ctx.question.text,
            subtasks=subtasks_block(c.subtasks, ctx),
            volunteers=volunteers_block(ctx),
            members="、".join(ctx.label(x) for x in codes),
        )
        calls = 0
        best: Assignment | None = None
        call_id = None
        problems: list[str] = []
        kinds = ctx.config.roundtable.collab.kinds
        pipeline = c.pipeline
        for _ in range(2):  # 不符合规则时重新分配一次
            result = await call_and_parse(
                ctx,
                step=self.name,
                role="coordinator",
                model_id=coordinator,
                prompt=prompt,
                parse=lambda text: parse_assignment(text, ids, ctx.to_code),
            )
            calls += 1
            call_id = result.call_id
            if result.value is None:
                continue
            best = result.value
            problems = coverage_problems(best, codes)
            if pipeline:
                problems += self._pipeline_problems(ctx, best, codes)
            if not problems:
                break
        degraded = best is None
        empty = Assignment({s: () for s in ids}, degraded=True)
        if pipeline:
            # 统筹的分配合格就原样采用；否则只作为起点，由代码按流水线规则重新求解
            assignment = (
                best
                if best is not None and not problems
                else solve_assignment(
                    best or empty,
                    codes,
                    c.subtasks,
                    c.volunteers,
                    _member_tags(ctx),
                    kinds,
                    ctx.rng,
                )
            )
        else:
            assignment = repair_assignment(
                best or empty, codes, c.subtasks, c.volunteers, _member_tags(ctx), ctx.rng
            )
        c.assignment = assignment
        _save(ctx, self.name, "assignment", assignment.to_dict(), call_id=call_id)
        notes = []
        if assignment.repaired:
            notes.append(f"统筹的分配不符合规则（{'；'.join(problems) or '不可用'}），已由代码补齐")
        return StepResult(
            self.name,
            calls=calls,
            degraded=("coordinator",) if degraded else (),
            notes=tuple(notes),
        )

    @staticmethod
    def _pipeline_problems(ctx: TableContext, a: Assignment, codes: Sequence[str]) -> list[str]:
        """流水线的分配规则：每块恰好一人（子任务够分时）、不可并行的类型一人、审查者回避上游作者。"""
        c = _c(ctx)
        found: list[str] = []
        if len(c.subtasks) >= len(codes):
            multi = [s for s, owners in a.owners.items() if len(owners) > 1]
            if multi:
                found.append(f"这些子任务有多人负责（应恰好一人，不能几个人做同一份内容）：{multi}")
        hard, soft = assignment_conflicts(a.owners, c.subtasks, ctx.config.roundtable.collab.kinds)
        return [*found, *hard, *soft]


# --- 完成子任务 -----------------------------------------------------------------


def plan_block(ctx: TableContext) -> str:
    c = _c(ctx)
    lines = []
    for s in c.subtasks:
        owners = c.assignment.owners.get(s.id, ()) if c.assignment else ()
        lines.append(f"{s.id}「{s.title}」：{'、'.join(ctx.label(o) for o in owners)}")
    return "<plan>\n" + neutralize("\n".join(lines), TAGS) + "\n</plan>"


def _attr(text: str) -> str:
    return " ".join(text.split()).replace('"', "'").replace("<", "‹").replace(">", "›")


def upstream_files(ctx: TableContext, subtask: Subtask) -> list[tuple[str, str, dict]]:
    """依赖链上游子任务的负责人生成的文件（最新版本）：(上游子任务, 作者, 文件行)。"""
    c = _c(ctx)
    anc = ancestors(c.subtasks).get(subtask.id, set())
    seen: set[str] = set()
    found = []
    for s in c.subtasks:  # 子任务顺序，同一个文件只算一次
        if s.id not in anc:
            continue
        for (sid, author), _ in c.latest().items():
            if sid != s.id:
                continue
            for row in ctx.latest_files(author):
                if row["id"] not in seen:
                    seen.add(row["id"])
                    found.append((sid, author, row))
    return found


def stage_inputs(ctx: TableContext, subtask: Subtask, code: str) -> dict[str, str]:
    """把上游生成的文件放进负责人的工作目录 in/（没有工具环境时不放）。

    返回 文件 id → in/ 下的文件名。
    """
    if ctx.toolbox is None or not _c(ctx).pipeline:
        return {}
    rows = upstream_files(ctx, subtask)
    if not rows:
        return {}
    labels = {row["id"]: sid for sid, _, row in rows}
    return ctx.toolbox.workspace(code).add_inputs([row for _, _, row in rows], labels)


def dependencies_block(
    ctx: TableContext,
    subtask: Subtask,
    code: str | None = None,
    inputs: dict[str, str] | None = None,
) -> str:
    """上游产出：每块标明来自哪个子任务、谁交的、交的是什么；上游生成的文件写成 in/ 下的路径。"""
    c = _c(ctx)
    latest = c.latest()
    titles = {s.id: s.title for s in c.subtasks}
    by_id = {s.id: s for s in c.subtasks}
    parts = []
    for dep in subtask.depends_on:
        for (sid, author), text in latest.items():
            if sid != dep:
                continue
            if c.pipeline:
                note = ctx.files_note(author, inputs) + ctx.sources_note(author)
            else:
                note = ctx.member_notes(author)
            body = neutralize(ctx.scrub(text + note), TAGS)
            attrs = f'subtask="{dep}" from="{ctx.label(author)}"'
            if c.pipeline:
                gives = _attr(subtask.handoff(dep, titles))
                kind = kind_rule(ctx, by_id[dep]) if dep in by_id else None
                attrs += f' gives="{gives}"' + (f' kind="{kind.label}"' if kind else "")
            parts.append(f"<dependency {attrs}>\n{body}\n</dependency>")
    return "\n\n".join(parts) or "（这个子任务没有前置依赖）"


def record_handoffs(ctx: TableContext, subtask: Subtask, code: str, inputs: dict[str, str]) -> None:
    """下游子任务开始时记录交接：谁把什么交给了谁（只含代号，匿名揭晓前也安全）。"""
    c = _c(ctx)
    titles = {s.id: s.title for s in c.subtasks}
    done = {(h["from_subtask"], h["from_code"], h["to_subtask"], h["to_code"]) for h in c.handoffs}
    for dep in subtask.depends_on:
        for sid, author in [k for k in c.works if k[0] == dep]:
            if (sid, author, subtask.id, code) in done:
                continue
            record = {
                "from_subtask": sid,
                "from_code": author,
                "to_subtask": subtask.id,
                "to_code": code,
                "gives": subtask.handoff(dep, titles),
                "files": [
                    {"id": row["id"], "name": row["path"], "kind": row["kind"]}
                    for row in ctx.latest_files(author)
                ],
            }
            c.handoffs.append(record)
            _save(ctx, "work", "handoff", record, code=code)
            ctx.emit(
                "handoff",
                "work",
                code,
                from_code=author,
                from_subtask=sid,
                to_subtask=subtask.id,
                gives=record["gives"],
            )


class WorkStep:
    name = "work"

    async def run(self, ctx: TableContext) -> StepResult:
        c = _c(ctx)
        if c.assignment is None:
            raise StepFailed("还没有分配子任务")
        by_id = {s.id: s for s in c.subtasks}
        rule = ctx.effort_rule
        redone: list[str] = []
        notes: list[str] = []
        calls = 0

        def problems(text: str, code: str, medium: str | None = None) -> list[str]:
            if not rule.enabled:
                return []
            return text_problems(
                text,
                rule=rule,
                question=ctx.question.text,
                expected_tokens=None if medium else _item_expected(ctx),
            ) + ctx.citation_problems(self.name, code, text)

        generate = True  # 用户在确认卡片上选了"不生成"时为 False

        async def work(sid: str, code: str) -> None:
            nonlocal calls
            subtask = by_id[sid]
            medium = subtask.media if ctx.media is not None else None
            item = {v: k for k, v in c.items().items()}[(sid, code)]  # 可能刚改派过
            krule = kind_rule(ctx, subtask)
            inputs = stage_inputs(ctx, subtask, code) if krule else {}
            if krule:
                record_handoffs(ctx, subtask, code, inputs)
            values = dict(
                code=ctx.label(code),
                question=ctx.question.text,
                plan=plan_block(ctx),
                subtask=subtask_block(subtask, "your_subtask", ctx),
                dependencies=dependencies_block(ctx, subtask, code, inputs),
            )
            if krule:
                template = ctx.prompts.get(krule.prompt, ctx.prompt_version(krule.prompt))
                extra = {"medium": KIND_LABELS[medium]} if medium else {}
                prompt = template.render(
                    **{k: v for k, v in {**values, **extra}.items() if k in template.variables}
                )
            elif medium:
                prompt = media_prompt(ctx, "work_media", medium, **values)
            else:
                prompt = ctx.render(self.name, **values)
            model_id = ctx.members[code]
            out = await call_model(
                ctx, step=self.name, role="member", model_id=model_id, prompt=prompt, code=code
            )
            calls += 1
            text = out.completion.text.strip() if out.completion else ""
            if not text:
                ctx.drop(code, self.name, "调用失败" if out.completion is None else "回答为空")
                return
            call_id = out.call_id
            first = problems(text, code, medium)
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
                    calls += 1
                    retry = again.completion.text.strip() if again.completion else ""
                    if retry:
                        text, call_id, final = retry, again.call_id, problems(retry, code, medium)
                record_effort(
                    ctx,
                    EffortRecord(
                        self.name,
                        code,
                        "lazy" if final else "redone",
                        tuple(first),
                        tuple(final),
                        rule.redo,
                        item,
                    ),
                )
                if final:
                    notes.append(f"{ctx.label(code)} 的 {sid} 被标记为敷衍")
            c.works[(sid, code)] = text
            info = None
            if medium and generate:
                info = await make_media(ctx, medium, text, self.name, sid, code, 1)
                if not info["ok"]:
                    notes.append(f"{ctx.label(code)} 的 {sid} 生成失败：{info['error']}")
            _save(
                ctx,
                self.name,
                "work",
                {"subtask": sid, "item": item, "text": text, **({"media": info} if info else {})},
                code=code,
                call_id=call_id,
            )

        def pending(layer: Sequence[str]) -> list[tuple[str, str]]:
            return [
                (sid, code)
                for sid in layer
                for code in c.assignment.owners.get(sid, ())
                if (sid, code) not in c.works and code not in ctx.state.dropped
            ]

        before = set(ctx.state.dropped)
        for number, layer in enumerate(layers(c.subtasks)):
            # 按依赖分批：同一批并行；后一批能拿到前一批的成果
            kinds = [by_id[sid].media for sid, _ in pending(layer) if by_id[sid].media]
            if kinds and ctx.media is not None:
                generate = (
                    media_gate(
                        ctx,
                        f"work:{ctx.table_no}:{number}",
                        kinds,
                        what=f"第 {number + 1} 批子任务",
                        chars=800,
                    )
                    == "go"
                )
                if not generate:
                    notes.append("按你的选择，这一批的媒体成果没有生成")
            await asyncio.gather(*(work(sid, code) for sid, code in pending(layer)))
            # 负责人中途退出、这一块一份成果都没有：改派给在场的组员，在下一批开始前补上
            if self._reassign(ctx, layer, notes):
                await asyncio.gather(*(work(sid, code) for sid, code in pending(layer)))
        missing = [
            f"{sid}（{ctx.label(code)}）"
            for sid, code in c.items().values()
            if (sid, code) not in c.works
        ]
        if missing:
            notes.append(f"以下成果缺失：{'、'.join(missing)}")
        return StepResult(
            self.name,
            calls=calls,
            dropped=tuple(x for x in ctx.state.dropped if x not in before),
            notes=tuple(notes),
        )

    def _reassign(self, ctx: TableContext, layer: Sequence[str], notes: list[str]) -> bool:
        """把没有任何成果、且负责人都已退出的子任务改派给在场组员（替换退出者的位置，
        不改变其他成果的编号）。选合适程度最高、负担最轻的人；改动存为新的分配记录。"""
        c = _c(ctx)
        owners = {k: list(v) for k, v in c.assignment.owners.items()}
        load = {code: 0 for code in ctx.active}
        for v in owners.values():
            for code in v:
                if code in load:
                    load[code] += 1
        tags = _member_tags(ctx)
        by_id = {s.id: s for s in c.subtasks}
        changes: list[str] = []
        for sid in layer:
            done = any(key[0] == sid for key in c.works)
            gone = [code for code in owners[sid] if code in ctx.state.dropped]
            if done or not gone or not load:
                continue
            candidates = [code for code in load if code not in owners[sid]]
            if not candidates:
                continue

            def fit(code: str, sid: str = sid) -> int:
                v = c.volunteers.get(code)
                stance = v.stance(sid) if v else "can"
                overlap = len(set(tags.get(code, ())) & set(by_id[sid].tags))
                return STANCE_SCORE[stance] + overlap

            pick = sorted(candidates, key=lambda x: (load[x], -fit(x), x))[0]
            owners[sid][owners[sid].index(gone[0])] = pick
            load[pick] += 1
            changes.append(f"{sid} 的负责人{ctx.label(gone[0])}已退出，改派给{ctx.label(pick)}")
        if not changes:
            return False
        c.assignment = Assignment(
            {k: tuple(v) for k, v in owners.items()},
            c.assignment.rationale,
            (*c.assignment.repaired, *changes),
            c.assignment.degraded,
        )
        _save(ctx, "work", "assignment", c.assignment.to_dict())
        notes.append("；".join(changes))
        return True


# --- 交叉审查 -------------------------------------------------------------------


def cross_review_plan(ctx: TableContext, reviewers: Sequence[str]) -> dict[str, list[str]]:
    """每份成果由 reviews_per_answer 位非作者审查（尽量避开同一子任务的其他负责人），
    负担均衡、随机打破平局。返回 评审者 → 成果编号（顺序随机）。"""
    c = _c(ctx)
    k = ctx.config.roundtable.reviews_per_answer
    owners_of = {sid: set(codes) for sid, codes in c.assignment.owners.items()}
    items = [(i, key) for i, key in c.items().items() if key in c.works]
    plan: dict[str, list[str]] = {r: [] for r in reviewers}
    order = shuffled(list(reviewers), ctx.rng)
    rank = {r: n for n, r in enumerate(order)}

    def pool(sid: str, author: str) -> list[str]:
        others = [r for r in reviewers if r != author]  # 永远不审自己的
        outsiders = [r for r in others if r not in owners_of.get(sid, set())]
        return outsiders if len(outsiders) >= min(k, len(others)) else others

    # 可选评审者最少的成果先分配，负担更均衡
    queue = shuffled(items, ctx.rng)
    queue.sort(key=lambda x: len(pool(*x[1])))
    for item, (sid, author) in queue:
        chosen = sorted(pool(sid, author), key=lambda r: (len(plan[r]), rank[r]))[:k]
        for r in chosen:
            plan[r].append(item)
    # 局部调整：把最重的人手里的一份交给最轻、且可以审这份的人，直到相差不超过 1
    info = dict(items)
    for _ in range(len(items) * k):
        heavy = max(reviewers, key=lambda r: (len(plan[r]), -rank[r]))
        light = min(reviewers, key=lambda r: (len(plan[r]), rank[r]))
        if len(plan[heavy]) - len(plan[light]) <= 1:
            break
        movable = [i for i in plan[heavy] if light in pool(*info[i]) and i not in plan[light]]
        if not movable:
            break
        plan[heavy].remove(movable[0])
        plan[light].append(movable[0])
    return {r: shuffled(v, ctx.rng) for r, v in plan.items() if v}


def items_block(ctx: TableContext, item_ids: Sequence[str]) -> str:
    c = _c(ctx)
    items = c.items()
    by_id = {s.id: s for s in c.subtasks}
    parts = []
    for item in item_ids:
        sid, code = items[item]
        s = by_id[sid]
        head = [f"子任务：{s.id}「{s.title}」"]
        if s.requirements:
            head.append(f"要求：{s.requirements}")
        if s.acceptance:
            head.append(f"验收标准：{s.acceptance}")
        if s.media:
            head.append(
                f"这一块需要生成{KIND_LABELS.get(s.media, s.media)}：下面的成果是作者写的"
                "生成提示词，生成结果在文件说明中（图片 / 视频截帧会作为随附图片发给你）。"
            )
        work = ctx.scrub(c.works[(sid, code)] + ctx.member_notes(code))
        body = neutralize("\n".join(head) + "\n\n" + work, TAGS)
        parts.append(f'<work id="{item}">\n{body}\n</work>')
    return "\n\n".join(parts)


class CrossReviewStep:
    name = "cross_review"

    async def run(self, ctx: TableContext) -> StepResult:
        c = _c(ctx)
        reviewers = list(ctx.active)
        plan = cross_review_plan(ctx, reviewers)
        todo = [r for r in plan if r not in c.cross_reviews]
        quality = ctx.config.roundtable.review_quality
        rule = ctx.effort_rule
        degraded: list[str] = []
        redone: list[str] = []
        notes: list[str] = []
        items = c.items()

        def to_item(text: str) -> str | None:
            t = text.strip().upper()
            return t if t in items else None

        async def work(reviewer: str) -> None:
            targets = [t for t in plan[reviewer] if items[t][1] != reviewer]
            prompt = ctx.render(
                self.name,
                code=ctx.label(reviewer),
                question=ctx.question.text,
                items=items_block(ctx, targets),
            )

            def parse(text: str) -> ReviewParse:
                return parse_reviews(
                    text, reviewer=reviewer, expected=targets, to_code=to_item, quality=quality
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
                    retry = _try(parse, again.completion.text) if again.completion else None
                    if retry is not None:
                        value, call_id = retry, again.call_id
                        final = review_problems(retry, targets)
                record_effort(
                    ctx,
                    EffortRecord(
                        self.name,
                        reviewer,
                        "lazy" if final else "redone",
                        tuple(first),
                        tuple(final),
                        rule.redo,
                    ),
                )
                if final:
                    notes.append(f"{ctx.label(reviewer)} 的审查被标记为敷衍")
            if value is None:
                degraded.append(reviewer)
                reviews, missing = (), tuple(targets)
            else:
                reviews, missing = value.reviews, value.missing
            c.cross_reviews[reviewer] = reviews
            _save(
                ctx,
                self.name,
                "cross_review",
                {
                    "reviews": [r.to_dict() for r in reviews],
                    "missing": list(missing),
                    "degraded": value is None,
                    "error": result.error if value is None else None,
                },
                code=reviewer,
                call_id=call_id,
            )

        before = set(ctx.state.dropped)
        await asyncio.gather(*(work(r) for r in todo))
        return StepResult(
            self.name,
            calls=len(todo) + len(redone),
            dropped=tuple(x for x in ctx.state.dropped if x not in before),
            degraded=tuple(degraded),
            notes=tuple(notes),
        )


# --- 修改 ---------------------------------------------------------------------


def reviews_of_item(ctx: TableContext, item: str, author: str) -> list[CheckedReview]:
    """作者某份成果收到的有效审查（排除作者自己），顺序随机。"""
    received = {
        reviewer: r
        for reviewer, reviews in _c(ctx).cross_reviews.items()
        if reviewer != author
        for r in reviews
        if r.target == item and r.valid
    }
    return [received[k] for k in shuffled(sorted(received), ctx.rng)]


def has_valid_review(ctx: TableContext, item: str, author: str) -> bool:
    return any(
        r.target == item and r.valid
        for reviewer, reviews in _c(ctx).cross_reviews.items()
        if reviewer != author
        for r in reviews
    )


class ReworkStep:
    name = "rework"

    async def run(self, ctx: TableContext) -> StepResult:
        c = _c(ctx)
        by_id = {s.id: s for s in c.subtasks}
        ids_by_key = {v: k for k, v in c.items().items()}
        todo = [key for key in c.works if key not in c.reworks and key[1] not in ctx.state.dropped]
        rule = ctx.effort_rule
        degraded: list[str] = []
        redone: list[str] = []
        notes: list[str] = []
        called = 0
        generate = True  # 用户在确认卡片上选了"不生成"时为 False

        def problems(revision: Revision, code: str, medium: str | None = None) -> list[str]:
            if not rule.enabled or revision.degraded:
                return []
            found = text_problems(
                revision.answer,
                rule=rule,
                question=ctx.question.text,
                expected_tokens=None if medium else _item_expected(ctx),
            ) + ctx.citation_problems(self.name, code, revision.answer)
            if not revision.responses.strip():
                found.append("没有回应审查意见")
            return found

        def with_decisions(revision: Revision) -> Revision:
            return Revision(
                revision.answer,
                revision.responses,
                decisions=parse_decisions(revision.responses, ctx.to_code),
            )

        async def work(key: tuple[str, str]) -> None:
            nonlocal called
            sid, code = key
            item = ids_by_key[key]
            original = c.works[key]
            received = reviews_of_item(ctx, item, code)
            medium = by_id[sid].media if ctx.media is not None else None
            call_id = None
            if not received:
                revision = Revision(original, "", skipped=True)
            else:
                called += 1
                block = "\n\n".join(review_block(ctx.label(r.reviewer), r) for r in received)
                values = dict(
                    code=ctx.label(code),
                    question=ctx.question.text,
                    subtask=subtask_block(by_id[sid], "your_subtask", ctx),
                    own_work=original,
                    reviews_of_you=ctx.scrub(block),
                )
                prompt = (
                    media_prompt(ctx, "rework_media", medium, **values)
                    if medium
                    else ctx.render(self.name, **values)
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
                    first = problems(revision, code, medium)
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
                            if retry is not None:
                                revision, call_id = with_decisions(retry), again.call_id
                                final = problems(revision, code, medium)
                        record_effort(
                            ctx,
                            EffortRecord(
                                self.name,
                                code,
                                "lazy" if final else "redone",
                                tuple(first),
                                tuple(final),
                                rule.redo,
                                item,
                            ),
                        )
                        if final:
                            notes.append(f"{ctx.label(code)} 对 {sid} 的修改被标记为敷衍")
                else:
                    degraded.append(code)
                    if result.failed_call:
                        ctx.drop(code, self.name, "调用失败")
                    text = (result.raw_text or "").strip()
                    revision = Revision(text or original, "", degraded=True)
            c.reworks[key] = revision
            info = None
            changed = revision.answer.strip() != original.strip()
            if medium and generate and not revision.skipped and not revision.degraded and changed:
                info = await make_media(ctx, medium, revision.answer, self.name, sid, code, 2)
                if not info["ok"]:
                    notes.append(f"{ctx.label(code)} 的 {sid} 重新生成失败：{info['error']}")
            _save(
                ctx,
                self.name,
                "rework",
                {
                    "subtask": sid,
                    "item": item,
                    **revision.to_dict(),
                    **({"media": info} if info else {}),
                },
                code=code,
                call_id=call_id,
            )

        before = set(ctx.state.dropped)
        kinds = [
            by_id[sid].media
            for sid, code in todo
            if by_id[sid].media and has_valid_review(ctx, ids_by_key[(sid, code)], code)
        ]
        if kinds and ctx.media is not None:
            generate = (
                media_gate(
                    ctx,
                    f"rework:{ctx.table_no}",
                    kinds,
                    what="根据审查意见重新生成",
                    chars=800,
                )
                == "go"
            )
            if not generate:
                notes.append("按你的选择，修改后的媒体成果没有重新生成")
        await asyncio.gather(*(work(key) for key in todo))
        return StepResult(
            self.name,
            calls=called + len(redone),
            dropped=tuple(x for x in ctx.state.dropped if x not in before),
            degraded=tuple(degraded),
            notes=tuple(notes),
        )


# --- 合并 ---------------------------------------------------------------------


def works_block(ctx: TableContext) -> str:
    """按子任务分组的各份最终成果（同一子任务内顺序随机，作者只以代号标注）。"""
    c = _c(ctx)
    latest = c.latest()
    ids_by_key = {v: k for k, v in c.items().items()}
    parts = []
    for s in c.subtasks:
        keys = [k for k in latest if k[0] == s.id]
        for key in shuffled(sorted(keys), ctx.rng):
            item = ids_by_key.get(key, "")
            flagged = ctx.state.flagged(key[1], item)
            attr = ' flagged="未通过实质内容检查"' if flagged else ""
            body = neutralize(ctx.scrub(latest[key] + ctx.member_notes(key[1])), TAGS)
            parts.append(
                f'<work id="{item}" subtask="{s.id}" from="{ctx.label(key[1])}"{attr}>\n'
                f"{body}\n</work>"
            )
    return "\n\n".join(parts)


class MergeStep:
    name = "merge"

    async def run(self, ctx: TableContext) -> StepResult:
        c = _c(ctx)
        if c.merge is not None:
            return StepResult(self.name)
        coordinator = _require_coordinator(ctx)
        latest = c.latest()
        if not latest:
            raise StepFailed("没有可合并的成果")
        ids = [s.id for s in c.subtasks]
        prompt = ctx.render(
            self.name,
            question=ctx.question.text,
            subtasks=subtasks_block(c.subtasks, ctx),
            works=works_block(ctx),
        )
        result = await call_and_parse(
            ctx,
            step=self.name,
            role="coordinator",
            model_id=coordinator,
            prompt=prompt,
            parse=lambda text: parse_merge(text, ids, ctx.to_code),
        )
        labelled = {(sid, ctx.label(code)): text for (sid, code), text in latest.items()}
        merge: Merge = result.value or fallback_merge(
            result.error or "未知错误", c.subtasks, labelled
        )
        c.merge = merge
        _save(ctx, self.name, "merge", merge.to_dict(), call_id=result.call_id)
        notes = (f"统筹合并：{merge.error}",) if merge.error else ()
        return StepResult(
            self.name,
            calls=1,
            degraded=("coordinator",) if merge.degraded else (),
            notes=notes,
        )


for _step in (
    DecomposeStep(),
    VolunteerStep(),
    AssignStep(),
    WorkStep(),
    CrossReviewStep(),
    ReworkStep(),
    MergeStep(),
):
    register_step(_step)
