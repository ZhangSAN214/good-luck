"""编排引擎：把路由、确认、预算、步骤和升级串成一场完整的讨论。

所有进度都落库，任何时候都可以中断后用 resume() 继续：
    路由 → [单题花费确认] → 第 0 张桌子逐步执行（每步前查预算）
         → [满足条件时询问是否升级：确认后新建桌子] → 完成
需要用户拍板时创建确认点并返回；用户用 respond() 回复后继续。
"""

from __future__ import annotations

import asyncio
import logging
import random
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from roundtable.core.allocation import member_codes
from roundtable.core.attachments import Attachment, prepare_attachments
from roundtable.core.cards import CardOption, ConfirmationCard, money
from roundtable.core.media import KIND_LABELS, MediaService
from roundtable.core.routing import (
    EstimateHistory,
    Question,
    RoutingDecision,
    RoutingError,
    RoutingRecord,
    UserChoice,
    answer_tokens,
    attachment_tokens,
    cost_card,
    escalate,
    escalation_card,
    estimate_lineup,
    history_from_calls,
    option_lineup,
    route_question,
    style_wanted,
)
from roundtable.core.runtime import PROJECT_ROOT, Runtime
from roundtable.core.steps import (
    Event,
    NeedsApproval,
    StepFailed,
    TableContext,
    final_answer,
    get_step,
    outcome_signals,
    restore_state,
    table_contributions,
)
from roundtable.core.storage import CheckpointView, NotFound
from roundtable.core.tools import ToolBox

log = logging.getLogger(__name__)

# 至少需要两名组员才有意义的步骤
PEER_STEPS = frozenset({"review", "revise", "cross_review", "rework"})
DONE, SKIPPED = "done", "skipped"
FINISHED = frozenset({"completed", "stopped", "failed"})

SessionEventSink = Callable[[str, Event], None]


class OrchestratorError(RuntimeError):
    """请求不合法（例如回复了不存在的选项）。"""


@dataclass(frozen=True)
class RunResult:
    session_id: str
    status: str
    checkpoint: CheckpointView | None
    final_answer: str | None
    cost_usd: float
    warnings: tuple[str, ...] = ()
    # 还没有人上桌时的错误（如手动勾选了不存在的模型），不涉及匿名，可以直接显示
    error: str | None = None


class Orchestrator:
    def __init__(self, runtime: Runtime, on_event: SessionEventSink | None = None) -> None:
        self.rt = runtime
        self.on_event = on_event
        self._locks: dict[str, asyncio.Lock] = {}
        self._warnings: dict[str, list[str]] = {}

    # --- 对外接口 ------------------------------------------------------------------

    def open(
        self,
        question: Question,
        choice: UserChoice | None = None,
        *,
        seed: int | None = None,
        anonymous: bool = False,
        attachments: Sequence[str] = (),
        style_refs: Sequence[str] | None = None,
    ) -> str:
        """只创建会话（立即返回 id）；之后用 run() 执行。Web 服务先拿 id 再在后台运行。

        anonymous 决定称呼：开启时成员是塔罗牌代号（界面与发给模型的内容都一样），
        关闭时是"昵称·模式"；任何情况下发给模型的内容都不含模型 id、厂商名。
        attachments：已上传（尚未使用）的附件 id，按顺序关联到本场。
        style_refs：其中作为风格参考的图片附件 id；None = 全部图片都是风格参考（用户可取消勾选）。
        """
        choice = choice or UserChoice()
        seed = secrets.randbelow(2**31) if seed is None else seed
        repo = self.rt.repo
        limit = self.rt.config.roundtable.uploads.max_files
        if len(attachments) > limit:
            raise OrchestratorError(f"每道题最多 {limit} 个附件")
        if len(set(attachments)) != len(attachments):
            raise OrchestratorError("附件不能重复")
        if style_refs is not None and not set(style_refs) <= set(attachments):
            raise OrchestratorError("风格参考必须是本次提交的附件")
        kinds = []
        for attachment_id in attachments:
            try:
                row = repo.attachment(attachment_id)
            except NotFound:
                raise OrchestratorError(f"附件不存在：{attachment_id}") from None
            if row["session_id"] is not None:
                raise OrchestratorError(f"附件「{row['name']}」已用于其他讨论，请重新上传")
            kinds.append(row["kind"])
        sid = repo.create_session(
            question.text,
            seed=seed,
            tier=choice.tier or self.rt.config.routing.default_plan,
            anonymous=anonymous,
            workflow=choice.workflow,
            attachments=tuple(kinds) or question.attachments,
            choice=choice.to_dict(),
        )
        repo.attach_to_session(sid, attachments, style_refs)
        return sid

    async def run(self, session_id: str) -> RunResult:
        """执行已创建的会话，直到完成或需要用户确认。"""
        async with self._lock(session_id):
            row = self.rt.repo.session_row(session_id)
            if row["status"] in FINISHED or self.rt.repo.pending_checkpoint(session_id):
                return self._result(session_id)
            return await self._advance(session_id)

    async def start(
        self,
        question: Question,
        choice: UserChoice | None = None,
        *,
        seed: int | None = None,
        anonymous: bool = False,
        attachments: Sequence[str] = (),
        style_refs: Sequence[str] | None = None,
    ) -> RunResult:
        return await self.run(
            self.open(
                question,
                choice,
                seed=seed,
                anonymous=anonymous,
                attachments=attachments,
                style_refs=style_refs,
            )
        )

    async def respond(self, session_id: str, response: str, note: str | None = None) -> RunResult:
        async with self._lock(session_id):
            repo = self.rt.repo
            cp = repo.pending_checkpoint(session_id)
            if cp is None:
                raise OrchestratorError("该会话没有待回复的确认点")
            keys = [o["key"] for o in cp.card["options"]]
            if response not in keys:
                raise OrchestratorError(f"无效的选项 {response!r}，可选：{keys}")
            repo.answer_checkpoint(cp.id, response, note)
            self._emit(session_id, "checkpoint_answered", kind=cp.kind, response=response)

            if response == "stop":
                return self._finish(session_id, "stopped")
            details = cp.card.get("details", {})
            if cp.kind == "budget":
                repo.set_budget_override(session_id)
                # 路由前被拦下的情况由 _advance 继续路由
            elif cp.kind == "cost":
                table_no = details["table_no"]
                if response.startswith("plan:"):
                    self._switch_plan(session_id, table_no, response.removeprefix("plan:"))
                else:
                    repo.set_table_status(session_id, table_no, "approved")
            elif cp.kind == "escalation":
                table_no = details["table_no"]
                status = "approved" if response == "continue" else SKIPPED
                repo.set_table_status(session_id, table_no, status)
            # members：同意继续即可，记录在确认点中
            return await self._advance(session_id)

    async def resume(self, session_id: str) -> RunResult:
        """中断或重启后继续执行；已完成的步骤不会重复调用。"""
        async with self._lock(session_id):
            row = self.rt.repo.session_row(session_id)
            if row["status"] in FINISHED or self.rt.repo.pending_checkpoint(session_id):
                return self._result(session_id)
            return await self._advance(session_id)

    def reveal_identities(self, session_id: str) -> None:
        self.rt.repo.mark_revealed(session_id)
        self._emit(session_id, "revealed")

    # --- 路由 ----------------------------------------------------------------------

    def _question(self, row: dict[str, Any]) -> Question:
        """会话的题目与附件（附件的 token 数计入花费预估：图片取原图与文字版的较大者）。"""
        rows = self.rt.repo.session_attachments(row["id"])
        tokens = attachment_tokens(rows, self.rt.config.routing.estimate)
        choice = row["choice"] or {}
        return Question(
            row["question"],
            tuple(row["attachments"]),
            tokens,
            choice.get("media"),
            choice.get("media_tier"),
        )

    def _table_question(self, row: dict[str, Any]) -> Question:
        """桌上成员看到的题目。选了媒体输出时，把需求改写成"商定并写出生成提示词"的任务。"""
        question = self._question(row)
        if not question.media:
            return question
        cfg = self.rt.config
        template = self.rt.prompts.get("media_brief", cfg.roundtable.prompts["media_brief"])
        brief = template.render(medium=KIND_LABELS[question.media], question=question.text)
        return replace(question, text=next(m.content for m in brief.messages if m.role == "user"))

    async def _prepare(self, sid: str, row: dict[str, Any]) -> bool:
        """图片生成文字版、音频转写（每个文件只做一次）。音频转写失败时无法继续。"""
        repo = self.rt.repo
        if not any(a["status"] == "pending" for a in repo.session_attachments(sid)):
            return True
        self._emit(sid, "step_started", step="attachments", table_no=None)
        files = await prepare_attachments(
            sid,
            config=self.rt.config,
            router=self.rt.router,
            prompts=self.rt.prompts,
            repo=repo,
            store=self.rt.files,
            scrubber=self.rt.scrubber.bound(row["question"], enabled=bool(row["anonymous"])),
            question=row["question"],
            rng=random.Random(f"{row['seed']}:attachments"),
        )
        self._emit(sid, "step_finished", step="attachments", table_no=None)
        for a in files:
            if a.status != "failed":
                continue
            if a.kind == "audio":
                error = f"音频「{a.name}」转写失败：{a.error}"
                repo.set_status(sid, "failed", error=error)
                self._emit(sid, "failed", error=error)
                return False
            self._warn(
                sid, f"图片「{a.name}」的文字版生成失败（{a.error}），看不到图片的成员无法参考它"
            )
        return True

    async def _begin(self, sid: str) -> bool:
        """还没路由的会话：先查预算，再处理附件、路由。返回是否可以继续执行。"""
        repo = self.rt.repo
        row = repo.session_row(sid)
        if not repo.budget_override(sid):
            verdict = self.rt.budget.check(0.0)
            if not verdict.allowed:
                # 预算已用满：连规划员也先不调用
                self._checkpoint(sid, verdict.card(), stage="routing")
                return False
        if not await self._prepare(sid, row):
            return False
        try:
            await self._route(
                sid,
                self._question(row),
                UserChoice.from_dict(row["choice"]),
                row["seed"],
            )
        except RoutingError as exc:
            repo.set_status(sid, "failed", error=str(exc))
            self._emit(sid, "failed", error=str(exc))
            return False
        return True

    async def _route(self, sid: str, question: Question, choice: UserChoice, seed: int) -> None:
        repo = self.rt.repo
        recent = repo.recent_coordinators()
        decision = await route_question(
            question,
            choice,
            config=self.rt.config,
            router=self.rt.router,
            prompts=self.rt.prompts,
            seed=seed,
            recent_coordinators=recent,
            history=self.estimate_history(),
        )
        if decision.assessment.planner:
            repo.record_planner(sid, decision.assessment.planner)
        record = decision.record(question)
        # 记下轮换依据：用户在花费卡片上改选档位时，重建出与卡片估价一致的阵容
        record = replace(record, extra={**record.extra, "recent_coordinators": list(recent)})
        repo.save_routing(sid, record)
        self._warn(sid, *decision.warnings)
        self._emit(
            sid,
            "routed",
            difficulty=decision.assessment.difficulty,
            source=decision.assessment.source,
            plan=decision.plan,
            members=len(decision.lineup.members),
            absent=len(decision.lineup.absent),
            estimate_usd=decision.estimate.total_usd,
            options={
                k: (v.estimate.total_usd if v.estimate else None)
                for k, v in decision.options.items()
            },
        )
        needs = decision.needs_confirmation
        self._create_table(sid, 0, decision, status="pending" if needs else "approved")
        if needs:
            self._checkpoint(sid, cost_card(decision), table_no=0)

    def estimate_history(self) -> EstimateHistory:
        """本机历史调用的 token 统计，用于校准花费预估（推理 token、重试、重做都已包含在内）。"""
        params = self.rt.config.routing.estimate
        rows = self.rt.repo.call_samples(params.history_sessions)
        return history_from_calls(rows, params.history_min_samples)

    def _create_table(
        self,
        sid: str,
        table_no: int,
        decision: RoutingDecision,
        *,
        status: str,
        replace_existing: bool = False,
    ) -> None:
        seed = self.rt.repo.session_row(sid)["seed"]
        codes = member_codes(
            list(decision.lineup.members),
            self.rt.config,
            random.Random(f"{seed}:codes:{table_no}"),
            anonymous=bool(self.rt.repo.session_row(sid)["anonymous"]),
        )
        pipeline = list(decision.lineup.pipeline)
        choice = self.rt.repo.session_row(sid)["choice"] or {}
        if choice.get("media") and "media" not in pipeline:
            pipeline.insert(
                pipeline.index("reveal") if "reveal" in pipeline else len(pipeline), "media"
            )
        # 有风格参考图又要画图：先提取全员共用的风格规范（放在最前面）
        if "style" not in pipeline and style_wanted(
            self.rt.config,
            self.rt.router,
            has_style_images=any(
                a["kind"] == "image" and a["style_ref"]
                for a in self.rt.repo.session_attachments(sid)
            ),
            media=choice.get("media"),
            collab="decompose" in pipeline,
            tier=choice.get("media_tier"),
        ):
            pipeline.insert(0, "style")
        fields: dict[str, Any] = dict(
            plan=decision.plan,
            pipeline=tuple(pipeline),
            members=codes,
            coordinator=decision.lineup.coordinator,
            escalate_to=decision.escalate_to,
            estimate={
                "total": decision.estimate.total_usd,
                "max": decision.estimate.max_usd,
                "calibrated": decision.estimate.calibrated,
                "steps": {s.step: s.cost_usd for s in decision.estimate.steps},
            },
            status=status,
            escalation_reason=decision.escalation_reason,
        )
        if replace_existing:
            self.rt.repo.replace_table(sid, table_no, **fields)
        else:
            self.rt.repo.create_table(sid, table_no, **fields)

    def _switch_plan(self, sid: str, table_no: int, plan_name: str) -> None:
        """用户在花费卡片上改选其他档位：重建该档位的阵容（与卡片上的估价一致）。"""
        cfg, repo = self.rt.config, self.rt.repo
        if plan_name not in cfg.routing.plans:
            raise OrchestratorError(f"未知档位 {plan_name!r}")
        row = repo.session_row(sid)
        record = RoutingRecord.from_dict(repo.routing_record(sid))
        assessment = record.assessment()
        question = self._question(row)
        try:
            lineup = option_lineup(
                plan_name,
                seed=row["seed"],
                config=cfg,
                router=self.rt.router,
                recent_coordinators=record.extra.get("recent_coordinators", ()),
                workflow=row["workflow"],
            )
        except RoutingError as exc:
            raise OrchestratorError(str(exc)) from None
        estimate = estimate_lineup(
            lineup,
            question,
            assessment,
            config=cfg,
            router=self.rt.router,
            history=self.estimate_history(),
        )
        decision = RoutingDecision(
            seed=row["seed"],
            choice=UserChoice(plan_name, workflow=row["workflow"]),
            assessment=assessment,
            plan=plan_name,
            lineup=lineup,
            estimate=estimate,
            options={},
            escalate_to=cfg.routing.plans[plan_name].escalate_to,
            confirm_threshold_usd=cfg.routing.confirm_threshold_usd,
        )
        self._create_table(sid, table_no, decision, status="approved", replace_existing=True)
        repo.save_routing(
            sid,
            replace(
                record,
                plan=plan_name,
                members=lineup.members,
                coordinator=lineup.coordinator,
                absent=lineup.absent,
                estimated_cost_usd=estimate.total_usd,
                extra={**record.extra, "switched_by_user": True},
            ),
        )

    # --- 主循环 --------------------------------------------------------------------

    async def _advance(self, sid: str) -> RunResult:
        repo = self.rt.repo
        needs_routing = repo.routing_record(sid) is None and not repo.pending_checkpoint(sid)
        if needs_routing and not await self._begin(sid):
            return self._result(sid)
        while True:
            if repo.pending_checkpoint(sid):
                return self._result(sid)
            tables = repo.tables(sid)
            current = next((t for t in tables if t["status"] not in (DONE, SKIPPED)), None)
            if current is not None:
                if current["status"] == "pending":
                    return self._result(sid)  # 等待确认（理论上已有确认点）
                outcome = await self._run_table(sid, current)
                if outcome != DONE:
                    return self._result(sid)
                await self._maybe_escalate(sid, current)
                continue
            return self._complete(sid, tables)

    async def _maybe_escalate(self, sid: str, table: dict[str, Any]) -> bool:
        """本桌完成后判断是否需要升级；需要时新建下一张桌子并询问用户（从不自动升级）。"""
        repo, cfg = self.rt.repo, self.rt.config
        if not table["escalate_to"] or table["escalate_to"] not in cfg.routing.plans:
            return False
        signals = outcome_signals(restore_state(repo, sid, table["table_no"]))
        if signals is None:
            return False
        row = repo.session_row(sid)
        record = RoutingRecord.from_dict(repo.routing_record(sid))
        try:
            decision = escalate(
                seed=row["seed"],
                choice=UserChoice(table["escalate_to"], workflow=row["workflow"]),
                assessment=record.assessment(),
                from_plan=table["plan"],
                escalate_to=table["escalate_to"],
                question=self._question(row),
                signals=signals,
                config=cfg,
                router=self.rt.router,
                recent_coordinators=repo.recent_coordinators(),
                history=self.estimate_history(),
            )
        except RoutingError as exc:
            self._warn(sid, f"需要升级但无法组建阵容：{exc}")
            return False
        if decision is None:
            return False
        table_no = table["table_no"] + 1
        self._create_table(sid, table_no, decision, status="pending")
        self._emit(
            sid,
            "escalating",
            table_no=table_no,
            plan=decision.plan,
            reason=decision.escalation_reason,
            estimate_usd=decision.estimate.total_usd,
        )
        self._checkpoint(sid, escalation_card(decision), table_no=table_no)
        return True

    async def _run_table(self, sid: str, table: dict[str, Any]) -> str:
        repo, cfg = self.rt.repo, self.rt.config
        table_no = table["table_no"]
        row = repo.session_row(sid)
        if not repo.seats(sid, table_no):
            repo.add_seats(sid, table_no, table["members"], table["coordinator"])
        repo.set_table_status(sid, table_no, "running")
        repo.set_status(sid, "running")
        self._emit(
            sid,
            "table_started",
            table_no=table_no,
            plan=table["plan"],
            members=list(table["members"]),
        )

        ctx = TableContext(
            session_id=sid,
            table_no=table_no,
            question=self._table_question(row),
            members=dict(table["members"]),
            coordinator=table["coordinator"],
            config=cfg,
            router=self.rt.router,
            prompts=self.rt.prompts,
            repo=repo,
            scrubber=self.rt.scrubber,
            rng=random.Random(),
            anonymous=bool(row["anonymous"]),
            state=restore_state(repo, sid, table_no),
            on_event=lambda e: self._forward(sid, e),
            prompt_roles=self._prompt_roles(table),
            expected_answer_tokens=self._expected_tokens(sid),
            attachments=tuple(
                Attachment.from_row(a).with_data(self.rt.files.load)
                for a in repo.session_attachments(sid)
            ),
        )
        ctx.file_store = self.rt.files
        # 匿名关闭：生成提示词、工具描述都不做身份遮蔽；匿名开启时题目和附件里本来就有的名称保留
        table_scrubber = self.rt.scrubber.bound(
            "\n".join([ctx.question.text, *(a.text or "" for a in ctx.attachments)]),
            enabled=bool(row["anonymous"]),
        )
        choice = row["choice"] or {}
        ctx.media = MediaService(
            session_id=sid,
            config=cfg,
            router=self.rt.router,
            repo=repo,
            store=self.rt.files,
            scrubber=table_scrubber,
            seed=row["seed"],
            emit=lambda type_, step, code, **data: self._forward(
                sid, Event(type_, step, table_no, code, data)
            ),
            **self.rt.media_hooks,
        )
        if self.rt.frame_extractor is not None:
            ctx.frame_extractor = self.rt.frame_extractor
        ctx.media_kind = choice.get("media")
        ctx.media_tier = choice.get("media_tier") or cfg.roundtable.media.default_tier
        ctx.approval = self._approval_lookup(sid)
        ctx.budget_gate = self._budget_gate(sid)
        if cfg.roundtable.tools.enabled:
            sandbox, reason = self.rt.sandbox()
            ctx.toolbox = ToolBox(
                session_id=sid,
                table_no=table_no,
                config=cfg,
                router=self.rt.router,
                prompts=self.rt.prompts,
                repo=repo,
                store=self.rt.files,
                scrubber=table_scrubber,
                attachments=ctx.attachments,
                sandbox=sandbox,
                sandbox_reason=reason,
                budget_ok=lambda: repo.budget_override(sid) or self.rt.budget.check(0.0).allowed,
                seed=str(row["seed"]),
                project_root=PROJECT_ROOT,
                search=self.rt.search,
                media_tier=(row["choice"] or {}).get("media_tier"),
            )
        try:
            return await self._run_steps(sid, table, ctx, row)
        finally:
            if ctx.toolbox is not None:
                ctx.toolbox.close()

    async def _run_steps(
        self, sid: str, table: dict[str, Any], ctx: TableContext, row: dict[str, Any]
    ) -> str:
        repo, cfg = self.rt.repo, self.rt.config
        table_no = table["table_no"]
        done = set(repo.completed_steps(sid, table_no))
        for step in table["pipeline"]:
            if step in done:
                continue
            # 步骤做到一半停下来问过用户（如视频生成确认）后重新进入：预估已在开始时检查过，
            # 此时本桌花费里已含这一步已花的部分，再检查一次会把这一步的预估算两遍
            midstep = self._answered_midstep(sid, table_no, step)
            if not midstep and not self._budget_ok(sid, table, step):
                return "paused"
            if not midstep and not self._overrun_ok(sid, table, step, done):
                return "paused"
            if step in PEER_STEPS and len(ctx.active) < cfg.roundtable.min_members:
                if not ctx.active:
                    return self._fail(sid, "所有组员都调用失败")
                if not self._approved(sid, "members", table_no):
                    self._members_checkpoint(sid, table_no, step, len(ctx.active))
                    return "paused"
            # 每个步骤使用由 seed 派生的独立随机数：中途恢复也与不中断时结果一致
            ctx.rng = random.Random(f"{row['seed']}:{table_no}:{step}")
            self._emit(sid, "step_started", step=step, table_no=table_no)
            try:
                result = await get_step(step).run(ctx)
            except NeedsApproval as need:
                return self._needs_approval(sid, table_no, step, need)
            except StepFailed as exc:
                return self._fail(sid, f"步骤 {step} 失败：{exc}")
            repo.mark_step_done(sid, table_no, step)
            self._warn(sid, *result.notes)
            self._emit(
                sid,
                "step_finished",
                step=step,
                table_no=table_no,
                dropped=list(result.dropped),
                degraded=list(result.degraded),
                cost_usd=repo.session_cost(sid),
            )
        repo.set_table_status(sid, table_no, DONE)
        return DONE

    def _expected_tokens(self, sid: str) -> int | None:
        """路由时估计的答案长度（实质内容检查的字数下限依据）。"""
        stored = self.rt.repo.routing_record(sid)
        if stored is None:
            return None
        return answer_tokens(self.rt.config, RoutingRecord.from_dict(stored).assessment())

    def _record_contributions(self, sid: str) -> None:
        """按各桌当前状态重算贡献（可重复调用；只统计已经入座的桌子）。"""
        repo = self.rt.repo
        for table in repo.tables(sid):
            table_no = table["table_no"]
            if not repo.seats(sid, table_no):
                continue
            members = dict(table["members"])
            counts = table_contributions(restore_state(repo, sid, table_no), list(members))
            rows = [
                (code, members[code], kind, amount)
                for code, counter in counts.items()
                for kind, amount in counter.items()
            ]
            repo.replace_contributions(sid, table_no, rows)

    def _prompt_roles(self, table: dict[str, Any]) -> dict[str, str]:
        """档位指定的提示词角色（自选与旧版本的方案没有）。"""
        plans = self.rt.config.routing.plans
        plan = table["plan"]
        return dict(plans[plan].prompt_roles) if plan in plans else {}

    # --- 预算与确认 ----------------------------------------------------------------

    def _budget_ok(self, sid: str, table: dict[str, Any], step: str) -> bool:
        if self.rt.repo.budget_override(sid):
            return True
        estimate = table["estimate"]["steps"].get(step, 0.0)
        verdict = self.rt.budget.check(estimate)
        for w in verdict.warnings:
            if w not in self._warnings.get(sid, []):
                self._warn(sid, w)
                self._emit(sid, "budget_warning", message=w)
        if verdict.allowed:
            return True
        self.rt.repo.set_status(sid, "paused")
        self._checkpoint(sid, verdict.card(), table_no=table["table_no"], step=step)
        return False

    def _overrun_ok(self, sid: str, table: dict[str, Any], step: str, done: set[str]) -> bool:
        """本桌实际花费 + 下一步预估超过"本桌预估 × overrun_factor"时暂停询问，防止花费失控。

        用户选择继续后，上限提高到"已花费 + 剩余步骤预估"再乘以倍数；之后再超出会再次询问。
        """
        repo = self.rt.repo
        factor = self.rt.config.routing.estimate.overrun_factor
        estimate = table["estimate"]
        # 媒体生成有自己的确认（视频每次问、超过门槛问）和预算检查，不计入超支保护：
        # 协同模式的媒体子任务在预估时还不知道，否则每次确认之后都会再弹一张超支卡片
        media_estimate = estimate["steps"].get("media", 0.0)
        total = (estimate.get("total") or 0.0) - media_estimate
        if step == "media" or factor is None or total <= 0:
            return True
        table_no = table["table_no"]
        limit = total * factor
        for c in repo.session_view(sid).checkpoints:
            details = c.card.get("details", {})
            if c.kind == "overrun" and c.response == "continue" and details["table_no"] == table_no:
                limit = max(limit, details["next_limit"])
        spent = repo.table_cost(sid, table_no, include_media=False)
        step_estimate = estimate["steps"].get(step, 0.0)
        if spent + step_estimate <= limit + 1e-9:
            return True
        remaining = sum(estimate["steps"].get(s, 0.0) for s in table["pipeline"] if s not in done)
        card = ConfirmationCard(
            kind="overrun",
            situation=(
                f"本桌已花费 {money(spent)}，执行前预估 {money(total)}；"
                f"下一步「{step}」预计 {money(step_estimate)}，"
                f"会超过预估的 {factor:g} 倍（{money(limit)}）。"
            ),
            options=(
                CardOption("continue", "继续", remaining, "剩余步骤按预估计算"),
                CardOption("stop", "停止", 0.0, "保留已完成的部分"),
            ),
            recommendation="continue",
            reason="剩余步骤预计 " + money(remaining) + "；之后如果再超出会再次询问",
        )
        repo.set_status(sid, "paused")
        self._checkpoint(
            sid,
            card,
            table_no=table_no,
            step=step,
            next_limit=(spent + remaining) * factor,
        )
        return False

    def _answered_midstep(self, sid: str, table_no: int, step: str) -> bool:
        return any(
            c.kind == "media"
            and c.response is not None
            and c.card.get("details", {}).get("table_no") == table_no
            and c.card.get("details", {}).get("step") == step
            for c in self.rt.repo.session_view(sid).checkpoints
        )

    def _approval_lookup(self, sid: str) -> Callable[[str], str | None]:
        """步骤用它读取用户对某个确认点（key）的回复。"""

        def lookup(key: str) -> str | None:
            for c in self.rt.repo.session_view(sid).checkpoints:
                if c.kind == "media" and c.card.get("details", {}).get("key") == key and c.response:
                    return c.response
            return None

        return lookup

    def _budget_gate(self, sid: str) -> Callable[[float], ConfirmationCard | None]:
        """步骤内部（如媒体生成前）查预算：不允许时返回要弹出的预算卡片。"""

        def gate(estimate: float) -> ConfirmationCard | None:
            if self.rt.repo.budget_override(sid):
                return None
            verdict = self.rt.budget.check(estimate)
            return None if verdict.allowed else verdict.card()

        return gate

    def _needs_approval(self, sid: str, table_no: int, step: str, need: NeedsApproval) -> str:
        if need.card.kind == "budget":
            self.rt.repo.set_status(sid, "paused")
        self._checkpoint(sid, need.card, table_no=table_no, step=step, key=need.key)
        return "paused"

    def _approved(self, sid: str, kind: str, table_no: int) -> bool:
        """用户是否已对这张桌子的此类确认点选择了继续（同意一次即对整张桌子有效）。"""
        view = self.rt.repo.session_view(sid)
        return any(
            c.kind == kind
            and c.response == "continue"
            and c.card.get("details", {}).get("table_no") == table_no
            for c in view.checkpoints
        )

    def _members_checkpoint(self, sid: str, table_no: int, step: str, active: int) -> None:
        card = ConfirmationCard(
            kind="members",
            situation=(
                f"只剩 {active} 位组员能继续（少于 {self.rt.config.roundtable.min_members} 位）。"
            ),
            options=(
                CardOption("continue", "用剩下的组员继续", None),
                CardOption("stop", "停止", 0.0, "保留已完成的部分"),
            ),
            recommendation="continue",
            reason="已经完成的答案仍会参与汇总",
        )
        self.rt.repo.set_status(sid, "paused")
        self._checkpoint(sid, card, table_no=table_no, step=step)

    def _checkpoint(self, sid: str, card: ConfirmationCard, **details: Any) -> None:
        data = card.to_dict()
        data["details"] = {**data.get("details", {}), **details}
        self.rt.repo.create_checkpoint(sid, card.kind, data)
        if self.rt.repo.session_row(sid)["status"] != "paused":
            self.rt.repo.set_status(sid, "awaiting_confirmation")
        self._emit(sid, "checkpoint", kind=card.kind)

    # --- 结束 ----------------------------------------------------------------------

    def _complete(self, sid: str, tables: list[dict[str, Any]]) -> RunResult:
        finished = [t for t in tables if t["status"] == DONE]
        answer = None
        if finished:
            answer = final_answer(restore_state(self.rt.repo, sid, finished[-1]["table_no"]))
        self._update_record(sid, tables)
        self.rt.repo.set_status(sid, "completed")
        self._emit(sid, "completed", cost_usd=self.rt.repo.session_cost(sid))
        return self._result(sid, answer)

    def _finish(self, sid: str, status: str) -> RunResult:
        self._update_record(sid, self.rt.repo.tables(sid))
        self.rt.repo.set_status(sid, status)
        self._emit(sid, status)
        return self._result(sid)

    def _fail(self, sid: str, error: str) -> str:
        self._update_record(sid, self.rt.repo.tables(sid))
        self.rt.repo.set_status(sid, "failed", error=error)
        self._emit(sid, "failed")
        return "failed"

    def _update_record(self, sid: str, tables: list[dict[str, Any]]) -> None:
        repo = self.rt.repo
        self._record_contributions(sid)
        stored = repo.routing_record(sid)
        if stored is None:
            return
        record = RoutingRecord.from_dict(stored)
        escalated = [t for t in tables if t["table_no"] > 0 and t["status"] == DONE]
        offered = [t for t in tables if t["table_no"] > 0]
        confirmed = [
            c for c in repo.session_view(sid).checkpoints if c.kind == "cost" and c.response
        ]
        repo.save_routing(
            sid,
            replace(
                record,
                actual_cost_usd=repo.session_cost(sid),
                escalated=bool(escalated),
                escalation_reason=offered[0]["escalation_reason"] if offered else None,
                escalated_plan=escalated[-1]["plan"] if escalated else None,
                escalation_estimated_cost_usd=offered[0]["estimate"]["total"] if offered else None,
                user_confirmed=(confirmed[-1].response != "stop") if confirmed else None,
            ),
        )

    # --- 工具 ----------------------------------------------------------------------

    def _result(self, sid: str, answer: str | None = None) -> RunResult:
        repo = self.rt.repo
        row = repo.session_row(sid)
        if answer is None and row["status"] == "completed":
            done = [t for t in repo.tables(sid) if t["status"] == DONE]
            if done:
                answer = final_answer(restore_state(repo, sid, done[-1]["table_no"]))
        seated = bool(repo.seats(sid))
        return RunResult(
            session_id=sid,
            status=row["status"],
            checkpoint=repo.pending_checkpoint(sid),
            final_answer=answer,
            cost_usd=repo.session_cost(sid),
            warnings=tuple(self._warnings.get(sid, [])),
            error=row["error"] if row["error"] and not seated else None,
        )

    def _lock(self, sid: str) -> asyncio.Lock:
        return self._locks.setdefault(sid, asyncio.Lock())

    def _warn(self, sid: str, *messages: str) -> None:
        self._warnings.setdefault(sid, []).extend(m for m in messages if m)

    def _emit(
        self, sid: str, type_: str, *, step: str = "", table_no: int = 0, **data: Any
    ) -> None:
        if self.on_event:
            self.on_event(sid, Event(type_, step, table_no, None, data))

    def _forward(self, sid: str, event: Event) -> None:
        if self.on_event:
            self.on_event(sid, event)
