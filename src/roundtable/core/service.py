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
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from roundtable.core.allocation import coordinator_name, display_name
from roundtable.core.attachments import Attachment, UploadError, ingest
from roundtable.core.attachments.detect import TYPES as UPLOAD_TYPES
from roundtable.core.budget.guard import month_window
from roundtable.core.chat import GroupChat
from roundtable.core.media import KIND_LABELS, KINDS, MediaService
from roundtable.core.members_art import MEMBERS_FILE, load_members, mime_of, resolve_file
from roundtable.core.notes import NoteError, check_can_add, clean_note
from roundtable.core.office import building, payslip, roster
from roundtable.core.office import collect as collect_performance
from roundtable.core.office import reset as reset_performance
from roundtable.core.orchestrator import Orchestrator, OrchestratorError, RunResult
from roundtable.core.preview import render_preview
from roundtable.core.routing import (
    AUTO,
    CUSTOM,
    WORKFLOWS,
    Question,
    RoutingError,
    UserChoice,
    attachment_tokens,
    collab_advice,
    confirm_threshold,
    detect_multi_media,
    preview_estimates,
)
from roundtable.core.runtime import Runtime
from roundtable.core.steps import Event, restore_state
from roundtable.core.steps.office import group_estimates
from roundtable.core.storage import NotFound

log = logging.getLogger(__name__)

# 讨论停下来（结束或等待用户）的状态：此时推送流会关闭
RESTING = frozenset({"completed", "stopped", "failed", "awaiting_confirmation", "paused"})
REVEALABLE = frozenset({"completed", "stopped", "failed"})
MAX_QUESTION_CHARS = 20_000
MAX_CHAT_CHARS = 2000
# 公寓楼：进行中的会话这么久没有更新就不算在上班（服务意外退出后留下的 running）
WORKING_WINDOW_H = 3

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
        self._art_names_cache: tuple[int | None, dict[str, str]] | None = None
        self.chat = GroupChat(runtime, names=self._art_names)
        self._chat_tasks: set[asyncio.Task[Any]] = set()

    async def aclose(self) -> None:
        for task in [*self._tasks.values(), *self._chat_tasks]:
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
            "workflows": {
                "office": "办公室",
                "discussion": "讨论模式",
                "collab": "协同模式",
                "solo": "个人作答",
                "brainstorm": "头脑风暴",
                "auto": "自动",
            },
            "uploads": {
                "max_files": cfg.roundtable.uploads.max_files,
                "max_file_mb": cfg.roundtable.uploads.max_file_mb,
                "types": sorted(UPLOAD_TYPES),
            },
            "tools": self._tools_status(),
            "media": self._media_status(),
            "min_members": cfg.roundtable.min_members,
            "confirm_threshold_usd": cfg.routing.confirm_threshold_usd,
            # 办公室模式单人办的确认门槛（每件事的工资上限）
            "office_threshold_usd": cfg.roundtable.office.task_caps_usd.single,
            "max_members": cfg.roundtable.seats,
            "code_prefix": cfg.personas.code_prefix,
            "rice": cfg.roundtable.display.rice.model_dump(),
            "collab_kinds": {name: k.label for name, k in cfg.roundtable.collab.kinds.items()},
            "budget": self.budget(),
        }

    def _art_names(self) -> dict[str, str]:
        """成员形象里的称呼（小写厂商名 → 称呼），给群聊里没有昵称的成员用。"""
        root = self.rt.members_dir
        try:
            stamp = (root / MEMBERS_FILE).stat().st_mtime_ns if root else None
        except OSError:
            stamp = None
        if self._art_names_cache is None or self._art_names_cache[0] != stamp:
            out: dict[str, str] = {}
            for m in load_members(root).members:
                out.setdefault(m.vendor.strip().lower(), m.display)
            self._art_names_cache = (stamp, out)
        return self._art_names_cache[1]

    def members(self) -> dict[str, Any]:
        """成员形象（头像、小人）与「模型 id → 成员 id」。不涉及具体会话。

        前端只在显示模型时（匿名关闭或已揭晓）才用它查头像。
        """
        art = load_members(self.rt.members_dir)

        def url(rel: str | None) -> str | None:
            return f"/api/members/files/{quote(rel)}" if rel else None

        def avatar(images: dict[str, Any], size: str) -> str | None:
            for key in ("avatar_circle_light", "avatar_circle"):
                sizes = images.get(key)
                if isinstance(sizes, dict) and sizes.get(size):
                    return url(sizes[size])
            return None

        def pet(raw: dict[str, Any] | None) -> dict[str, Any] | None:
            if not raw:
                return None

            def clips(group: dict[str, Any]) -> dict[str, Any]:
                return {k: {**c, "frames": [url(f) for f in c["frames"]]} for k, c in group.items()}

            out = {
                "fps": raw["fps"],
                "actions": clips(raw["actions"]),
                "expressions": clips(raw["expressions"]),
            }
            for key in ("special_idle", "easter_eggs"):
                if raw.get(key):
                    out[key] = raw[key]
            return out

        models = [(m.id, m.vendor) for m in self.rt.config.models.models]
        return {
            "available": art.available,
            "reason": art.reason,
            "members": [
                {
                    "id": m.id,
                    "name": m.name,
                    "nickname": m.nickname,
                    "display": m.display,
                    "theme_color": m.theme_color,
                    "order": m.order,
                    "status": m.status,
                    "images": {
                        "avatar_64": avatar(m.images, "64"),
                        "avatar_128": avatar(m.images, "128"),
                        "avatar_256": avatar(m.images, "256"),
                        "chibi": url(m.images.get("chibi_transparent")),
                    },
                    "pet": pet(m.pet),
                }
                for m in art.members
            ],
            "models": art.model_map(models),
        }

    def member_file(self, path: str) -> tuple[bytes, str]:
        """成员形象图片：只允许素材目录内的 png / jpg / webp。返回 (内容, MIME)。"""
        found = resolve_file(self.rt.members_dir, path)
        if found is None:
            raise ServiceError("文件不存在", 404)
        try:
            return found.read_bytes(), mime_of(found)
        except OSError:
            raise ServiceError("文件不存在", 404) from None

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

    # --- 办公室：名册与工资单 ------------------------------------------------------

    def model_candidates(self) -> dict[str, Any]:
        """模型人才市场的最近结果：价格变动、下架、可能更合适的新模型。只提示，不改配置。"""
        if self.rt.catalog is None:
            return {"fetched_at": None, "stale": False, "available": False}
        return self.rt.catalog.view()

    async def refresh_model_candidates(self) -> dict[str, Any]:
        """重新查询聚合渠道的公开模型列表（不需要 key）。"""
        if self.rt.catalog is None:
            raise ServiceError("没有可用的模型列表来源", 404)
        try:
            return await self.rt.catalog.refresh()
        except LookupError as e:
            raise ServiceError(str(e), 404) from None
        except Exception as e:  # noqa: BLE001 - 网络错误只报类型，不带外部文本
            raise ServiceError(f"查询模型列表失败（{type(e).__name__}）", 502) from None

    def office(self) -> dict[str, Any]:
        """名册：经理与员工、工资标准（每百万 token 的美元价格）、本月已领工资。不涉及具体会话。"""
        cfg = self.rt.config
        start, end = month_window(self.rt.budget.now())
        entries = roster(
            cfg,
            (m.id for m in self.rt.router.available_models()),
            self.rt.repo.spent_by_model(start, end),
        )
        perf = collect_performance(self.rt.repo).to_dict()
        return {
            "staff": [
                {**asdict(e), "performance": perf["by_model"].get(e.model_id)} for e in entries
            ],
            # 绩效统计起点（清零时刻，没清零过为 None）与显示 / 参考所需的最少件数
            "performance_since": perf["since"],
            "performance_min_jobs": cfg.roundtable.office.performance.min_jobs,
            "task_caps_usd": cfg.roundtable.office.task_caps_usd.model_dump(),
            "manager_strategy": cfg.roundtable.office.manager_strategy,
            "month": self.budget()["month"],
        }

    def reset_performance(self) -> dict[str, Any]:
        """绩效一键清零：之后只统计此刻以后的办公室会话（不删除任何会话或调用记录）。"""
        reset_performance(self.rt.repo, self.rt.repo.clock())
        return self.office()

    def apartment(self, hour: int, weekday: int) -> dict[str, Any]:
        """公寓楼：住户按作息在各房间活动，正在办事的在办公室 / 会议室。

        hour / weekday 用浏览器的本地时间。匿名未揭晓的会话只计数，不说是谁。
        """
        if not (0 <= hour < 24 and 0 <= weekday < 7):
            raise ServiceError("hour 应为 0～23，weekday 应为 0～6")
        now = self.rt.budget.now()
        start, end = month_window(now)
        working = self.rt.repo.working_seats(now - timedelta(hours=WORKING_WINDOW_H))
        b = building(
            self.rt.config,
            hour=hour,
            weekday=weekday,
            working=working,
            month_usage=self.rt.repo.spent_by_model(start, end),
            meetings=self._office_meetings(working),
        )
        return {
            "floors": b.floors,
            "residents": [asdict(r) for r in b.residents],
            "hidden_meetings": b.hidden_meetings,
        }

    def _office_meetings(self, working: Sequence[dict[str, Any]]) -> dict[str, dict[int, Any]]:
        """办公室模式里在会议室的桌子：开会的参会者，项目分工的整个项目组（匿名未揭晓的不看）。"""
        out: dict[str, dict[int, Any]] = {}
        sids = {
            s["session_id"] for s in working if s.get("workflow") == "office" and not s["hidden"]
        }
        for sid in sids:
            for table in self.rt.repo.tables(sid):
                no = table["table_no"]
                if "intake" not in table["pipeline"]:
                    out.setdefault(sid, {})[no] = None  # 项目组
                    continue
                intake = restore_state(self.rt.repo, sid, no).office.intake or {}
                if intake.get("mode") == "meeting":
                    out.setdefault(sid, {})[no] = list(intake.get("attendees") or [])
        return out

    # --- 群聊 ----------------------------------------------------------------------

    def chat_view(self, after: int = 0, query: str | None = None) -> dict[str, Any]:
        """群成员、消息（id 大于 after 的；有 query 时为搜索结果）与正在输入的成员。"""
        members = self.chat.members()
        names = {m.id: m.name for m in members} | {"user": "我", "system": "系统"}
        rows = self.rt.repo.chat_messages(self.chat.room, after=after, query=query or None)
        return {
            "members": [asdict(m) for m in members],
            "messages": [
                {
                    "id": r["id"],
                    "author": r["author"],
                    "name": names.get(r["author"], r["author"]),
                    "text": r["text"],
                    "reply_to": r["reply_to"],
                    "cost_usd": r["cost_usd"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
            "typing": list(self.chat.typing),
        }

    async def chat_post(self, text: str) -> dict[str, Any]:
        """用户发一条消息；被选中的成员在后台依次回应（前端轮询 chat_view 拿到）。"""
        text = (text or "").strip()
        if not text:
            raise ServiceError("消息不能为空")
        if len(text) > MAX_CHAT_CHARS:
            raise ServiceError(f"消息太长（最多 {MAX_CHAT_CHARS} 字）")
        msg, speakers = self.chat.post(text)
        if speakers:
            task = asyncio.get_running_loop().create_task(self.chat.replies(msg["id"], speakers))
            self._chat_tasks.add(task)
            task.add_done_callback(self._chat_tasks.discard)
        return {"id": msg["id"], "speakers": [m.name for m in speakers]}

    def chat_clear(self) -> dict[str, Any]:
        """一键清空聊天记录（花费记录保留，预算照算）。"""
        return {"deleted": self.rt.repo.clear_chat(self.chat.room)}

    def payslip(self, session_id: str) -> dict[str, Any]:
        """一件事的工资单：每位组员 / 统筹各领多少，外包（搜索、媒体、规划员等）另列。

        按会话视图汇总：匿名会话揭晓前只列代号和"统筹"，不含模型 id。
        """
        self._require(session_id)
        view = self.rt.repo.session_view(session_id)

        office = view.workflow == "office"  # 办公室模式里统筹就是经理

        def coordinator_label(model_id: str | None) -> str:
            if model_id:
                return coordinator_name(model_id, self.rt.config, office=office)
            return "经理" if office else "统筹"

        slip = payslip(view.calls, view.seats, coordinator_label=coordinator_label)
        return {"lines": [asdict(line) for line in slip.lines], "total_usd": slip.total_usd}

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
        style_refs: Sequence[str] | None = None,
    ) -> str:
        """创建会话并在后台开始执行，立即返回会话 id。

        tier：档位名或 "custom"（自选，models 为上桌的模型）；anonymous 默认关闭；
        workflow：discussion（讨论模式）或 collab（协同模式）；
        attachments：先用 upload() 上传得到的附件 id；
        style_refs：其中作为风格参考的图片附件 id（None = 全部图片；传空表示都不是）；
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
                Question(text),
                choice,
                seed=seed,
                anonymous=anonymous,
                attachments=attachments,
                style_refs=style_refs,
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
        if workflow == AUTO:
            workflow = None  # 自动模式：预估列出全部方式，供对比
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
        threshold = confirm_threshold(cfg, workflow or "discussion")
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
                    over_threshold=e.total_usd > confirm_threshold(cfg, o.workflow),
                    threshold_usd=confirm_threshold(cfg, o.workflow),
                    steps=[
                        {"step": st.step, "cost_usd": st.cost_usd, "max_usd": st.max_usd}
                        for st in e.steps
                        if st.step != "reveal"
                    ],
                    members=len(o.lineup.members),
                    absent=len(o.lineup.absent),
                )
                if o.workflow == "office":  # 经理如果决定开会 / 项目分工，大约花多少
                    item["groups"] = group_estimates(cfg, self.rt.router, question_obj, o.lineup)
                if not anonymous:
                    item["lineup"] = {
                        "members": list(o.lineup.members),
                        "coordinator": o.lineup.coordinator,
                        "absent": list(o.lineup.absent),
                    }
            out.append(item)
        advice = []
        hint = detect_multi_media(text, cfg.routing.multi_media)
        if hint is not None and workflow in (None, "discussion"):
            advice.append(collab_advice(hint))
        return {
            "seed": seed,
            "answer_length": assessment.difficulty,
            "length_source": assessment.source,
            "confirm_threshold_usd": threshold,
            "options": out,
            "advice": advice,
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

    def add_note(self, session_id: str, text: str) -> dict[str, Any]:
        """讨论进行中补充意见：下一个步骤开始时带给统筹和组员；不打断进行中的调用。"""
        row = self._require(session_id)
        try:
            cleaned = clean_note(text)
        except NoteError as exc:
            raise ServiceError(str(exc), 400) from None
        try:
            check_can_add(row["status"], len(self.rt.repo.pending_notes(session_id)))
        except NoteError as exc:
            raise ServiceError(str(exc), 409) from None
        note_id = self.rt.repo.add_note(session_id, cleaned)
        self._publish(
            session_id, Event("note_added", "", 0, None, {"id": note_id, "text": cleaned})
        )
        return {"id": note_id, "text": cleaned}

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
                    seat["label"] = coordinator_name(
                        seat["model_id"], self.rt.config, office=view.workflow == "office"
                    )
        data["can_reveal"] = view.anonymous and view.status in REVEALABLE and not view.revealed
        data["tables"] = self._tables(session_id)
        data["notes"] = self.rt.repo.notes(session_id)
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
                "reference_count": j["reference_count"],
                "warning": j["params"].get("warning"),
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
