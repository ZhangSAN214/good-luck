"""对外 facade：Web 服务（以及以后的其他界面）只通过这里访问核心逻辑。

- 所有返回值都是可直接转成 JSON 的 dict / list；揭晓前不含任何模型身份。
- 讨论在后台任务中执行，提交后立即返回会话 id；进度通过事件订阅获得。
- 每当讨论完成、失败、停止或需要用户确认时，会发出一个 "state" 事件。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict
from typing import Any

from roundtable.core.allocation import coordinator_name, display_name
from roundtable.core.attachments import Attachment, UploadError, ingest
from roundtable.core.attachments.detect import TYPES as UPLOAD_TYPES
from roundtable.core.media import KIND_LABELS, KINDS, MediaService
from roundtable.core.orchestrator import Orchestrator, OrchestratorError, RunResult
from roundtable.core.preview import render_preview
from roundtable.core.routing import (
    CUSTOM,
    WORKFLOWS,
    Question,
    RoutingError,
    UserChoice,
    attachment_tokens,
    preview_estimates,
)
from roundtable.core.runtime import Runtime
from roundtable.core.steps import Event
from roundtable.core.storage import NotFound

log = logging.getLogger(__name__)

# 讨论停下来（结束或等待用户）的状态：此时推送流会关闭
RESTING = frozenset({"completed", "stopped", "failed", "awaiting_confirmation", "paused"})
REVEALABLE = frozenset({"completed", "stopped", "failed"})
MAX_QUESTION_CHARS = 20_000

__all__ = [
    "MAX_QUESTION_CHARS",
    "NotFound",
    "RoundtableService",
    "ServiceError",
]


class ServiceError(ValueError):
    """请求不合法（参数错误、状态不允许等）。status 为建议的 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class RoundtableService:
    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime
        self.orc = Orchestrator(runtime, on_event=self._publish)
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def aclose(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.rt.aclose()

    # --- 概况 ----------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """渠道、模型、预算、方案与预设。不涉及任何具体会话，不影响匿名。"""
        cfg = self.rt.config
        available = {m.id for m in self.rt.router.available_models()}
        return {
            "channel_mode": self.rt.router.mode,
            "channels": {
                name: {
                    "kind": spec.kind,
                    "available": name not in self.rt.unavailable_channels,
                    "reason": self.rt.unavailable_channels.get(name),
                }
                for name, spec in cfg.models.channels.items()
            },
            "models": [
                {
                    "id": m.id,
                    "tier": m.tier,
                    "tags": m.tags,
                    "price": {"input": m.price.input, "output": m.price.output},
                    "available": m.id in available,
                    "seat": m.seat,
                }
                for m in cfg.models.models
                if m.enabled
            ],
            # 成员档位（所选档位的可用模型全部上桌）与自选
            "plans": {name: p.label for name, p in cfg.routing.plans.items()},
            "default_plan": cfg.routing.default_plan,
            "custom_label": cfg.routing.custom.label,
            "workflows": {"discussion": "讨论模式", "collab": "协同模式"},
            "uploads": {
                "max_files": cfg.roundtable.uploads.max_files,
                "max_file_mb": cfg.roundtable.uploads.max_file_mb,
                "types": sorted(UPLOAD_TYPES),
            },
            "tools": self._tools_status(),
            "media": self._media_status(),
            "min_members": cfg.roundtable.min_members,
            "confirm_threshold_usd": cfg.routing.confirm_threshold_usd,
            "max_members": cfg.roundtable.seats,
            "code_prefix": cfg.personas.code_prefix,
            "rice": cfg.roundtable.display.rice.model_dump(),
            "collab_kinds": {name: k.label for name, k in cfg.roundtable.collab.kinds.items()},
            "budget": self.budget(),
        }

    def budget(self) -> dict[str, Any]:
        month, day = self.rt.budget.status()

        def status(s):
            if s is None:
                return None
            return {
                "limit_usd": s.limit_usd,
                "spent_usd": s.spent_usd,
                "ratio": s.ratio,
                "warn": s.warn,
                "exhausted": s.exhausted,
                "resets_at": s.resets_at.isoformat(),
            }

        return {
            "month": status(month),
            "day": status(day),
            "warn_ratio": self.rt.config.roundtable.budget.warn_ratio,
            # 按渠道的累计花费：不对应任何座位，可随时展示
            "by_channel": {
                k: {"calls": v.calls, "failed_attempts": v.failed_attempts, "cost_usd": v.cost_usd}
                for k, v in self.rt.repo.spent_by_channel().items()
            },
            "total_usd": self.rt.repo.total_spent(),
        }

    # --- 会话 ----------------------------------------------------------------------

    def create(
        self,
        question: str,
        *,
        tier: str | None = None,
        models: Sequence[str] = (),
        coordinator: str | None = None,
        anonymous: bool = False,
        workflow: str = "discussion",
        seed: int | None = None,
        attachments: Sequence[str] = (),
        media: str | None = None,
        media_tier: str | None = None,
    ) -> str:
        """创建会话并在后台开始执行，立即返回会话 id。

        tier：档位名或 "custom"（自选，models 为上桌的模型）；anonymous 默认关闭；
        workflow：discussion（讨论模式）或 collab（协同模式）；
        attachments：先用 upload() 上传得到的附件 id；
        media：讨论模式的输出类型（image / speech / video，默认文字）；
        media_tier：媒体模型的档位（budget / flagship）。
        """
        text = question.strip()
        if not text:
            raise ServiceError("题目不能为空")
        if len(text) > MAX_QUESTION_CHARS:
            raise ServiceError(f"题目过长（超过 {MAX_QUESTION_CHARS} 字）")
        try:
            choice = UserChoice(tier, tuple(models), coordinator, workflow, media, media_tier)
        except (RoutingError, ValueError) as exc:
            raise ServiceError(str(exc)) from None
        self._check_media(media, media_tier)
        if tier is not None and tier != CUSTOM and tier not in self.rt.config.routing.plans:
            raise ServiceError(f"未知的档位 {tier!r}")
        if not self.rt.router.available_models():
            raise ServiceError("没有可用的模型：请在 .env 中填写至少一个渠道的 key", 503)
        self._check_capacity()
        try:
            sid = self.orc.open(
                Question(text), choice, seed=seed, anonymous=anonymous, attachments=attachments
            )
        except OrchestratorError as exc:
            raise ServiceError(str(exc)) from None
        self._spawn(sid, self.orc.run(sid))
        return sid

    def _check_media(self, media: str | None, media_tier: str | None) -> None:
        if media is None:
            return
        reason = self._media_service().unavailable_reason(media, media_tier)
        if reason:
            raise ServiceError(reason)

    def _media_service(self) -> MediaService:
        return MediaService(
            session_id="",
            config=self.rt.config,
            router=self.rt.router,
            repo=self.rt.repo,
            store=self.rt.files,
            scrubber=self.rt.scrubber,
        )

    # --- 提交前预估 ------------------------------------------------------------------

    def estimate(
        self,
        question: str,
        *,
        tier: str | None = None,
        models: Sequence[str] = (),
        coordinator: str | None = None,
        workflow: str | None = None,
        anonymous: bool = False,
        attachments: Sequence[str] = (),
        seed: int | None = None,
        media: str | None = None,
        media_tier: str | None = None,
    ) -> dict[str, Any]:
        """提交前预估：各模式 × 各档位（及自选）的上桌人数、缺席、预计花费与步骤明细。

        不调用任何模型（答案长度只用规则判断，判断不出时用默认值）。返回的 seed 在提交时
        一并传给 create()，上桌名单就与预估一致。匿名开启时不返回模型名单。
        """
        text = question.strip()
        if not text:
            raise ServiceError("题目不能为空")
        if workflow is not None and workflow not in WORKFLOWS:
            raise ServiceError(f"未知的模式 {workflow!r}")
        custom = None
        try:
            UserChoice(None, (), None, workflow or "discussion", media, media_tier)
        except (RoutingError, ValueError) as exc:
            raise ServiceError(str(exc)) from None
        self._check_media(media, media_tier)
        if tier == CUSTOM:
            try:
                custom = UserChoice(CUSTOM, tuple(models), coordinator)
            except (RoutingError, ValueError) as exc:
                raise ServiceError(str(exc)) from None
        rows = []
        for attachment_id in attachments:
            try:
                rows.append(self.rt.repo.attachment(attachment_id))
            except NotFound:
                raise ServiceError(f"附件不存在：{attachment_id}") from None
        cfg = self.rt.config
        question_obj = Question(
            text,
            tuple(r["kind"] for r in rows),
            attachment_tokens(rows, cfg.routing.estimate),
            media,
            media_tier,
        )
        seed = secrets.randbelow(2**31) if seed is None else seed
        assessment, options = preview_estimates(
            question_obj,
            config=cfg,
            router=self.rt.router,
            seed=seed,
            recent_coordinators=self.rt.repo.recent_coordinators(),
            history=self.orc.estimate_history(),
            workflows=(workflow,) if workflow else WORKFLOWS,
            custom=custom,
        )
        threshold = cfg.routing.confirm_threshold_usd
        selected_plan = tier or cfg.routing.default_plan
        scrub = self.rt.scrubber.scrub if anonymous else (lambda t: t)
        out = []
        for o in options:
            selected = o.plan == selected_plan and workflow in (None, o.workflow)
            item: dict[str, Any] = {
                "workflow": o.workflow,
                "plan": o.plan,
                "label": o.label,
                "available": o.available,
                "reason": scrub(o.reason),
                "selected": selected,
            }
            if o.estimate is not None and o.lineup is not None:
                e = o.estimate
                item.update(
                    estimate_usd=e.total_usd,
                    max_usd=e.max_usd,
                    calibrated=e.calibrated,
                    over_threshold=e.total_usd > threshold,
                    steps=[
                        {"step": st.step, "cost_usd": st.cost_usd, "max_usd": st.max_usd}
                        for st in e.steps
                        if st.step != "reveal"
                    ],
                    members=len(o.lineup.members),
                    absent=len(o.lineup.absent),
                )
                if not anonymous:
                    item["lineup"] = {
                        "members": list(o.lineup.members),
                        "coordinator": o.lineup.coordinator,
                        "absent": list(o.lineup.absent),
                    }
            out.append(item)
        return {
            "seed": seed,
            "answer_length": assessment.difficulty,
            "length_source": assessment.source,
            "confirm_threshold_usd": threshold,
            "options": out,
        }

    # --- 附件 ----------------------------------------------------------------------

    @property
    def max_upload_bytes(self) -> int:
        return int(self.rt.config.roundtable.uploads.max_file_mb * 1024 * 1024)

    def upload(self, name: str, data: bytes) -> dict[str, Any]:
        """上传一个文件：识别类型、提取文字并登记，返回附件信息（提交题目时带上它的 id）。

        图片的文字版和音频的转写在提交题目后、讨论开始前生成（费用计入本场）。
        """
        try:
            attachment = ingest(
                name,
                data,
                config=self.rt.config,
                router=self.rt.router,
                repo=self.rt.repo,
                store=self.rt.files,
            )
        except UploadError as exc:
            raise ServiceError(str(exc)) from None
        return attachment.public()

    def _attachments(self, session_id: str) -> list[dict[str, Any]]:
        return [
            Attachment.from_row(r).public() for r in self.rt.repo.session_attachments(session_id)
        ]

    def respond(self, session_id: str, response: str, note: str | None = None) -> None:
        """回复确认卡片；讨论在后台继续。"""
        self._require(session_id)
        if self.running(session_id):
            raise ServiceError("讨论正在进行中", 409)
        checkpoint = self.rt.repo.pending_checkpoint(session_id)
        if checkpoint is None:
            raise ServiceError("该会话没有待回复的确认点", 409)
        keys = [o["key"] for o in checkpoint.card["options"]]
        if response not in keys:
            raise ServiceError(f"无效的选项 {response!r}，可选：{keys}")
        self._check_capacity()
        self._spawn(session_id, self.orc.respond(session_id, response, note))

    def resume(self, session_id: str) -> None:
        self._require(session_id)
        if not self.running(session_id):
            self._check_capacity()
            self._spawn(session_id, self.orc.resume(session_id))

    def reveal_identities(self, session_id: str) -> dict[str, Any]:
        """揭晓身份：只用于匿名开启的会话，且只允许在讨论结束后。"""
        row = self._require(session_id)
        if not row["anonymous"]:
            raise ServiceError("这场讨论没有开启匿名，身份一直是公开的", 409)
        if row["status"] not in REVEALABLE or self.running(session_id):
            raise ServiceError("讨论结束后才能揭晓身份", 409)
        self.orc.reveal_identities(session_id)
        return self.session(session_id)

    def session(self, session_id: str) -> dict[str, Any]:
        """会话详情（揭晓前为匿名视图）。"""
        self._require(session_id)
        view = self.rt.repo.session_view(session_id, scrub=self.rt.scrubber.scrub)
        data = asdict(view)
        data["running"] = self.running(session_id)
        data["pending_checkpoint"] = next(
            (c for c in data["checkpoints"] if c["status"] == "pending"), None
        )
        if not view.anonymous:  # 匿名关闭：统筹显示为"昵称·模式（统筹）"（成员的代号本身就是昵称）
            for seat in data["seats"]:
                if seat["role"] == "coordinator" and seat["model_id"]:
                    seat["label"] = coordinator_name(seat["model_id"], self.rt.config)
        data["can_reveal"] = view.anonymous and view.status in REVEALABLE and not view.revealed
        data["tables"] = self._tables(session_id)
        data["contributions"] = self._contributions(session_id, view.revealed)
        data["attachments"] = self._attachments(session_id)
        data["files"] = self._files(session_id)
        data["tool_calls"] = self._tool_calls(session_id, view.revealed)
        data["media"] = self._media_jobs(session_id, view.revealed)
        choice = self.rt.repo.session_row(session_id)["choice"] or {}
        data["media_kind"] = choice.get("media")
        data["media_tier"] = choice.get("media_tier")
        return data

    # --- 工具与生成的文件 ----------------------------------------------------------

    def _tools_status(self) -> dict[str, Any]:
        rules = self.rt.config.roundtable.tools
        _, reason = self.rt.sandbox() if rules.enabled else (None, None)
        unavailable = {}
        if reason:
            unavailable["python"] = reason
        if not any("image_gen" in m.tags for m in self.rt.router.available_models()):
            unavailable["generate_image"] = "没有可用的图像生成模型（需要带 image_gen 标签的模型）"
        if not self.rt.search.available:
            unavailable["search"] = unavailable["fetch"] = self.rt.search.reason()
        elif not self.rt.search.can_fetch:
            unavailable["fetch"] = "当前的搜索服务都不支持读取网页正文"
        return {"enabled": rules.enabled, "by_step": rules.by_step, "unavailable": unavailable}

    def _media_status(self) -> dict[str, Any]:
        """各种媒体的可用情况（不含模型名）：可选的档位、不可用的原因、确认与轮数规则。"""
        rules = self.rt.config.roundtable.media
        svc = self._media_service()
        kinds = {}
        for kind in KINDS:
            tiers = {tier: svc.model_for(kind, tier) is not None for tier in ("budget", "flagship")}
            kinds[kind] = {
                "label": KIND_LABELS[kind],
                "available": svc.unavailable_reason(kind) is None,
                "reason": svc.unavailable_reason(kind),
                "tiers": tiers,
                "estimate_usd": svc.estimate(kind, rules.default_tier, chars=500),
            }
        return {
            "enabled": rules.enabled,
            "default_tier": rules.default_tier,
            "max_rounds": rules.max_rounds,
            "confirm_video": rules.confirm_video,
            "kinds": kinds,
        }

    def _media_jobs(self, session_id: str, revealed: bool) -> list[dict[str, Any]]:
        """每次媒体生成：提示词、第几轮、花费、状态、文件；模型和渠道只在身份公开后给出。"""
        scrub = (lambda t: t) if revealed else self.rt.scrubber.scrub
        return [
            {
                "id": j["id"],
                "table_no": j["table_no"],
                "step": j["step"],
                "code": j["code"],
                "subtask": j["subtask"],
                "round": j["round"],
                "attempt": j["attempt"],
                "kind": j["kind"],
                "state": j["state"],
                "prompt": scrub(j["prompt"]),
                "cost_usd": j["cost_usd"],
                "error": j["error"],
                "file_id": j["file_id"],
                "extra_files": j["params"].get("extra_files", []),
                "model_id": j["model_id"] if revealed else None,
                "channel": j["channel"] if revealed else None,
            }
            for j in self.rt.repo.media_jobs(session_id)
        ]

    def _files(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.rt.repo.files(session_id)
        latest = {(r["table_no"], r["code"], r["path"]): r["id"] for r in rows}
        return [
            {
                "id": r["id"],
                "table_no": r["table_no"],
                "step": r["step"],
                "code": r["code"],
                "path": r["path"],
                "kind": r["kind"],
                "mime": r["mime"],
                "size": r["size"],
                "tool_call_id": r["tool_call_id"],
                "latest": latest[(r["table_no"], r["code"], r["path"])] == r["id"],
            }
            for r in rows
        ]

    def _tool_calls(self, session_id: str, revealed: bool) -> list[dict[str, Any]]:
        scrub = (lambda t: t) if revealed else self.rt.scrubber.scrub
        out = []
        for r in self.rt.repo.tool_calls(session_id):
            inputs = {k: scrub(v) if isinstance(v, str) else v for k, v in r["input"].items()}
            out.append(
                {
                    "id": r["id"],
                    "table_no": r["table_no"],
                    "step": r["step"],
                    "code": r["code"],
                    "round": r["round"],
                    "tool": r["tool"],
                    "input": inputs,
                    "output": scrub((r["output"] or "")[:4000]),
                    "status": r["status"],
                    "duration_s": r["duration_s"],
                }
            )
        return out

    def file_content(self, session_id: str, file_id: str) -> tuple[bytes, dict[str, Any]]:
        """下载：文件内容与元数据（文件名只取路径最后一段）。"""
        self._require(session_id)
        try:
            row = self.rt.repo.file(session_id, file_id)
        except NotFound:
            raise ServiceError("文件不存在", 404) from None
        return self.rt.files.load(row["storage_key"]), row

    def file_preview(self, session_id: str, file_id: str) -> dict[str, Any]:
        """预览：文本类给出文字（匿名会话揭晓前经身份遮蔽），表格给出前几行，文档给出文字。

        图片由前端用下载地址直接显示；HTML 只提供下载、不在页面内渲染。
        """
        data, row = self.file_content(session_id, file_id)
        view = self.rt.repo.session_view(session_id)
        scrub = (lambda t: t) if view.revealed else self.rt.scrubber.scrub
        try:
            preview = render_preview(row["path"], data)
        except Exception:  # noqa: BLE001 - 文件由模型生成，格式可能有误
            preview = {"type": "none", "reason": "无法预览这个文件，请下载查看"}
        if "text" in preview:
            preview["text"] = scrub(preview["text"])
        if "sheets" in preview:
            for sheet in preview["sheets"]:
                sheet["rows"] = [[scrub(c) for c in r] for r in sheet["rows"]]
        return {"file": {k: row[k] for k in ("id", "path", "kind", "mime", "size")}, **preview}

    def _contributions(self, session_id: str, revealed: bool) -> list[dict[str, Any]]:
        """本场每位组员的贡献（按桌、代号）；身份未公开时不含模型 id。"""
        grouped: dict[tuple[int, str], dict[str, Any]] = {}
        for r in self.rt.repo.contributions(session_id):
            row = grouped.setdefault(
                (r["table_no"], r["code"]),
                {
                    "table_no": r["table_no"],
                    "code": r["code"],
                    "model_id": r["model_id"] if revealed else None,
                    "counts": {},
                },
            )
            row["counts"][r["kind"]] = r["amount"]
        return list(grouped.values())

    def contributions(self) -> list[dict[str, Any]]:
        """跨会话按模型汇总的贡献（只统计身份已公开的会话），供以后按历史表现分工。"""
        by_model: dict[str, dict[str, Any]] = {}
        for r in self.rt.repo.contribution_history():
            row = by_model.setdefault(
                r["model_id"],
                {
                    "model_id": r["model_id"],
                    "label": display_name(r["model_id"], self.rt.config),  # 昵称·模式
                    "sessions": 0,
                    "counts": {},
                },
            )
            row["counts"][r["kind"]] = r["amount"]
            row["sessions"] = max(row["sessions"], r["sessions"])
        return list(by_model.values())

    def _plan_label(self, plan: str | None) -> str:
        if plan == CUSTOM:
            return self.rt.config.routing.custom.label
        return plan or ""

    def _tables(self, session_id: str) -> list[dict[str, Any]]:
        """每张桌子的执行计划与进度（只含代号，不含模型）。"""
        plans = self.rt.config.routing.plans
        out = []
        for t in self.rt.repo.tables(session_id):
            plan = plans.get(t["plan"])
            out.append(
                {
                    "table_no": t["table_no"],
                    "plan": t["plan"],
                    "plan_label": plan.label if plan else self._plan_label(t["plan"]),
                    "pipeline": list(t["pipeline"]),
                    "codes": list(t["members"]),
                    "status": t["status"],
                    "steps_done": self.rt.repo.completed_steps(session_id, t["table_no"]),
                    "estimate_usd": t["estimate"].get("total"),
                    "escalation_reason": t["escalation_reason"],
                }
            )
        return out

    def sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        return [asdict(s) for s in self.rt.repo.list_sessions(limit)]

    def _check_capacity(self) -> None:
        """同时在后台执行的讨论数上限（limits.max_running_sessions）；等待确认的不占名额。"""
        limit = self.rt.config.roundtable.limits.max_running_sessions
        busy = sum(1 for t in self._tasks.values() if not t.done())
        if busy >= limit:
            raise ServiceError(f"同时进行的讨论已达上限（{limit} 场），请等其中一场结束后再试", 429)

    def running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return task is not None and not task.done()

    async def wait(self, session_id: str) -> None:
        """等待后台任务结束（测试与命令行用）。"""
        task = self._tasks.get(session_id)
        if task is not None:
            await asyncio.shield(task)

    # --- 事件 ----------------------------------------------------------------------

    async def events(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        """订阅事件，直到讨论停下来（完成 / 失败 / 停止 / 等待确认）为止。

        第一条总是 "snapshot"（当前状态），之后是实时事件，最后一条是 "state"。
        """
        self._require(session_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.setdefault(session_id, set()).add(queue)
        try:
            snapshot = self._state(session_id)
            yield {"type": "snapshot", **snapshot}
            if not self.running(session_id) and snapshot["status"] in RESTING | {"created"}:
                return
            while True:
                event = await queue.get()
                yield event
                if event["type"] == "state" and event["status"] in RESTING:
                    return
        finally:
            self._subscribers.get(session_id, set()).discard(queue)

    def _publish(self, session_id: str, event: Event) -> None:
        payload = {
            "type": event.type,
            "step": event.step or None,
            "table_no": event.table_no,
            "code": event.code,
            "data": event.data,
        }
        for queue in self._subscribers.get(session_id, ()):
            queue.put_nowait(payload)

    def _publish_state(self, session_id: str) -> None:
        payload = {"type": "state", **self._state(session_id)}
        for queue in self._subscribers.get(session_id, ()):
            queue.put_nowait(payload)

    def _state(self, session_id: str) -> dict[str, Any]:
        row = self.rt.repo.session_row(session_id)
        checkpoint = self.rt.repo.pending_checkpoint(session_id)
        return {
            "status": row["status"],
            "running": self.running(session_id),
            "checkpoint": asdict(checkpoint) if checkpoint else None,
            "cost_usd": self.rt.repo.session_cost(session_id),
        }

    # --- 后台任务 --------------------------------------------------------------------

    def _spawn(self, session_id: str, work) -> None:
        async def runner() -> None:
            try:
                result: RunResult = await work
                log.info("会话 %s：%s", session_id, result.status)
            except (OrchestratorError, RoutingError) as exc:
                self.rt.repo.set_status(session_id, "failed", error=str(exc))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 后台任务不能把异常丢掉
                # 意外错误（如断网）：标记为暂停，可用 resume 从中断处继续
                log.exception("会话 %s 执行出错", session_id)
                self.rt.repo.set_status(
                    session_id, "paused", error=f"执行中断（{type(exc).__name__}），可以继续"
                )
            finally:
                self._tasks.pop(session_id, None)
                self._publish_state(session_id)

        self._tasks[session_id] = asyncio.get_running_loop().create_task(runner())

    def _require(self, session_id: str) -> dict[str, Any]:
        try:
            return self.rt.repo.session_row(session_id)
        except NotFound:
            raise ServiceError("会话不存在", 404) from None
