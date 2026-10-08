"""编排引擎：把路由、确认、预算、步骤和升级串成一场完整的讨论。

所有进度都落库，任何时候都可以中断后用 resume() 继续：
    路由 → [单题花费确认] → 第 0 张桌子逐步执行（每步前查预算）
         → [满足条件时升级：新建桌子，必要时确认] → 完成
需要用户拍板时创建确认点并返回；用户用 respond() 回复后继续。
"""

from __future__ import annotations

import asyncio
import logging
import random
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from roundtable.core.allocation import assign_codes
from roundtable.core.cards import CardOption, ConfirmationCard
from roundtable.core.routing import (
    Question,
    RoutingDecision,
    RoutingError,
    RoutingRecord,
    UserChoice,
    cost_card,
    escalate,
    escalation_card,
    estimate_lineup,
    option_lineup,
    route_question,
)
from roundtable.core.runtime import Runtime
from roundtable.core.steps import (
    Event,
    StepFailed,
    TableContext,
    final_answer,
    get_step,
    outcome_signals,
    restore_state,
)
from roundtable.core.storage import CheckpointView

log = logging.getLogger(__name__)

# 至少需要两名组员才有意义的步骤
PEER_STEPS = frozenset({"review", "revise"})
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


class Orchestrator:
    def __init__(self, runtime: Runtime, on_event: SessionEventSink | None = None) -> None:
        self.rt = runtime
        self.on_event = on_event
        self._locks: dict[str, asyncio.Lock] = {}
        self._warnings: dict[str, list[str]] = {}

    # --- 对外接口 ------------------------------------------------------------------

    async def start(
        self, question: Question, choice: UserChoice | None = None, *, seed: int | None = None
    ) -> RunResult:
        choice = choice or UserChoice()
        seed = secrets.randbelow(2**31) if seed is None else seed
        repo = self.rt.repo
        sid = repo.create_session(
            question.text,
            seed=seed,
            mode=choice.mode,
            preset=choice.preset,
            attachments=question.attachments,
        )
        async with self._lock(sid):
            verdict = self.rt.budget.check(0.0)
            if not verdict.allowed:
                # 预算已用满：连规划员也先不调用
                card = verdict.card()
                self._checkpoint(sid, card, stage="routing", choice=_choice_dict(choice))
                return self._result(sid)
            try:
                await self._route(sid, question, choice, seed)
            except RoutingError as exc:
                repo.set_status(sid, "failed", error=str(exc))
                self._emit(sid, "failed", error=str(exc))
                return self._result(sid)
            return await self._advance(sid)

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
                if details.get("stage") == "routing":
                    row = repo.session_row(session_id)
                    question = Question(row["question"], tuple(row["attachments"]))
                    try:
                        await self._route(
                            session_id, question, _choice_from(details["choice"]), row["seed"]
                        )
                    except RoutingError as exc:
                        repo.set_status(session_id, "failed", error=str(exc))
                        return self._result(session_id)
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

    async def _route(self, sid: str, question: Question, choice: UserChoice, seed: int) -> None:
        repo = self.rt.repo
        decision = await route_question(
            question,
            choice,
            config=self.rt.config,
            router=self.rt.router,
            prompts=self.rt.prompts,
            seed=seed,
            recent_coordinators=repo.recent_coordinators(),
        )
        if decision.assessment.planner:
            repo.record_planner(sid, decision.assessment.planner)
        repo.save_routing(sid, decision.record(question))
        self._warn(sid, *decision.warnings)
        self._emit(
            sid,
            "routed",
            difficulty=decision.assessment.difficulty,
            source=decision.assessment.source,
            plan=decision.plan,
            members=len(decision.lineup.members),
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
        codes = assign_codes(
            list(decision.lineup.members),
            self.rt.config.personas.codes,
            random.Random(f"{seed}:codes:{table_no}"),
        )
        fields: dict[str, Any] = dict(
            plan=decision.plan,
            pipeline=decision.lineup.pipeline,
            members=codes,
            coordinator=decision.lineup.coordinator,
            escalate_to=decision.escalate_to,
            estimate={
                "total": decision.estimate.total_usd,
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
        """用户在花费卡片上改选其他方案：重建该方案的阵容（与卡片上的估价一致）。"""
        cfg, repo = self.rt.config, self.rt.repo
        if plan_name not in cfg.routing.plans:
            raise OrchestratorError(f"未知方案 {plan_name!r}")
        row = repo.session_row(sid)
        record = RoutingRecord.from_dict(repo.routing_record(sid))
        assessment = record.assessment()
        question = Question(row["question"], tuple(row["attachments"]))
        lineup = option_lineup(
            plan_name, assessment, seed=row["seed"], config=cfg, router=self.rt.router
        )
        estimate = estimate_lineup(lineup, question, assessment, config=cfg, router=self.rt.router)
        allow_escalation = row["mode"] == "auto" or (
            row["mode"] == "preset" and cfg.routing.presets[row["preset"]].escalate
        )
        decision = RoutingDecision(
            seed=row["seed"],
            choice=UserChoice(),
            assessment=assessment,
            plan=plan_name,
            lineup=lineup,
            estimate=estimate,
            options={},
            escalate_to=cfg.routing.plans[plan_name].escalate_to if allow_escalation else None,
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
                estimated_cost_usd=estimate.total_usd,
                extra={**record.extra, "switched_by_user": True},
            ),
        )

    # --- 主循环 --------------------------------------------------------------------

    async def _advance(self, sid: str) -> RunResult:
        repo = self.rt.repo
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
        """本桌完成后判断是否升级；需要升级时新建下一张桌子。"""
        if not table["escalate_to"]:
            return False
        repo, cfg = self.rt.repo, self.rt.config
        signals = outcome_signals(restore_state(repo, sid, table["table_no"]))
        if signals is None:
            return False
        row = repo.session_row(sid)
        record = RoutingRecord.from_dict(repo.routing_record(sid))
        try:
            decision = escalate(
                seed=row["seed"],
                choice=UserChoice(),
                assessment=record.assessment(),
                from_plan=table["plan"],
                escalate_to=table["escalate_to"],
                question=Question(row["question"], tuple(row["attachments"])),
                signals=signals,
                config=cfg,
                router=self.rt.router,
                recent_coordinators=repo.recent_coordinators(),
            )
        except RoutingError as exc:
            self._warn(sid, f"需要升级但无法组建阵容：{exc}")
            return False
        if decision is None:
            return False
        table_no = table["table_no"] + 1
        needs = decision.needs_confirmation
        self._create_table(sid, table_no, decision, status="pending" if needs else "approved")
        self._emit(
            sid,
            "escalating",
            table_no=table_no,
            plan=decision.plan,
            reason=decision.escalation_reason,
            estimate_usd=decision.estimate.total_usd,
        )
        if needs:
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
            question=Question(row["question"], tuple(row["attachments"])),
            members=dict(table["members"]),
            coordinator=table["coordinator"],
            config=cfg,
            router=self.rt.router,
            prompts=self.rt.prompts,
            repo=repo,
            scrubber=self.rt.scrubber,
            rng=random.Random(),
            state=restore_state(repo, sid, table_no),
            on_event=lambda e: self._forward(sid, e),
        )
        done = set(repo.completed_steps(sid, table_no))
        for step in table["pipeline"]:
            if step in done:
                continue
            if not self._budget_ok(sid, table, step):
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
        return RunResult(
            session_id=sid,
            status=row["status"],
            checkpoint=repo.pending_checkpoint(sid),
            final_answer=answer,
            cost_usd=repo.session_cost(sid),
            warnings=tuple(self._warnings.get(sid, [])),
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


def _choice_dict(choice: UserChoice) -> dict[str, Any]:
    return {
        "mode": choice.mode,
        "preset": choice.preset,
        "members": list(choice.members),
        "coordinator": choice.coordinator,
    }


def _choice_from(d: dict[str, Any]) -> UserChoice:
    return UserChoice(
        d["mode"], d.get("preset"), tuple(d.get("members") or ()), d.get("coordinator")
    )
