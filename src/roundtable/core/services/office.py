"""办公室：名册与工资单、资料浏览器、会话、提交前预估、附件、工具与生成的文件、事件、革新例会、插件清单、插件榜。

从 service.py 拆出（代码精简）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from roundtable.core import clipper
from roundtable.core.allocation import coordinator_name, display_name
from roundtable.core.attachments import Attachment, UploadError, ingest
from roundtable.core.budget.guard import month_window
from roundtable.core.carry import carry_question, compact
from roundtable.core.chat_work import WORK_EVENTS, post_office_event
from roundtable.core.config.loader import ConfigError
from roundtable.core.evolution.dev import DevAgent, DevError
from roundtable.core.evolution.meeting import (
    DECISIONS,
    DECISIONS_KEY,
    MEETINGS_KEY,
    NEXT_THEME_KEY,
    TODO_FILE,
    Meeting,
    adoptable,
    adoption_markdown,
    adoption_path,
    choose_theme,
    decision_key,
    dump_meetings,
    load_cards,
    load_decisions,
    load_meetings,
    meeting_question,
    plan_meeting,
    todo_markdown,
    week_key,
    week_slot,
)
from roundtable.core.evolution.meeting import KINDS as MEETING_KINDS
from roundtable.core.evolution.plugins import (
    PLUGINS_KEY,
    PluginError,
    PluginWriter,
    by_scene,
    dump_lists,
    load_lists,
    plugins_from,
    plugins_markdown,
)
from roundtable.core.evolution.scout import SCOUT_WEEK_KEY, Scout, ScoutError
from roundtable.core.export import FORMATS as EXPORT_FORMATS
from roundtable.core.export import Style as ExportStyle
from roundtable.core.export import deliverable
from roundtable.core.export import filename as export_filename
from roundtable.core.export import options as export_choices
from roundtable.core.hometz import home_zone
from roundtable.core.ideaboard import Board
from roundtable.core.media import KIND_LABELS, KINDS, MediaService
from roundtable.core.notes import NoteError, check_can_add, clean_note
from roundtable.core.office import building, life_spots, roster
from roundtable.core.office import collect as collect_performance
from roundtable.core.office import reset as reset_performance
from roundtable.core.orchestrator import OrchestratorError
from roundtable.core.plugin_board import board_view, load_board
from roundtable.core.preview import render_preview
from roundtable.core.providers import Completion, Message
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
from roundtable.core.runtime import PROJECT_ROOT
from roundtable.core.search import SearchUnavailable
from roundtable.core.services.common import (
    EXPORT_FOLDER_KEY,
    MAX_QUESTION_CHARS,
    RESTING,
    REVEALABLE,
    WORKING_WINDOW_H,
    ServiceError,
    log,
)
from roundtable.core.steps import Event, restore_state
from roundtable.core.steps.office import group_estimates
from roundtable.core.storage import NotFound, iso


class OfficeService:
    """办公室：名册与工资单、资料浏览器、会话、提交前预估、附件、工具与生成的文件、事件、革新例会、插件清单、插件榜。"""

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

    def model_directory(self) -> dict[str, Any]:
        """模型图鉴的最近结果：聚合渠道上的全部模型，分类、打标签、标出已在圆桌里的。只浏览。"""
        if self.rt.directory is None:
            return {"fetched_at": None, "stale": False, "available": False}
        return self.rt.directory.view()

    async def refresh_model_directory(self) -> dict[str, Any]:
        """重新拉取聚合渠道的全部模型（公开列表，不需要 key）。"""
        if self.rt.directory is None:
            raise ServiceError("没有可用的模型列表来源", 404)
        try:
            return await self.rt.directory.refresh()
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

        hour / weekday 用浏览器的本地时间（按主时区）。匿名未揭晓的会话只计数，不说是谁。
        问的正是主时区的这一小时时，生活系统这一小时记下的去处优先（和生活群、朋友圈对得上）。
        """
        if not (0 <= hour < 24 and 0 <= weekday < 7):
            raise ServiceError("hour 应为 0～23，weekday 应为 0～6")
        now = self.rt.budget.now()
        local = home_zone(self.rt).local(now)
        spots = None
        if (local.hour, local.weekday()) == (hour, weekday):
            slot = local.replace(minute=0, second=0, microsecond=0)
            spots = life_spots(
                self.rt.repo.life_events_between(iso(slot), iso(slot + timedelta(hours=1)))
            )
        start, end = month_window(now)
        working = self.rt.repo.working_seats(now - timedelta(hours=WORKING_WINDOW_H))
        b = building(
            self.rt.config,
            hour=hour,
            weekday=weekday,
            working=working,
            month_usage=self.rt.repo.spent_by_model(start, end),
            meetings=self._office_meetings(working),
            life=spots,
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

    # --- 资料浏览器（办公室） --------------------------------------------------------

    def _clip_rules(self):
        rules = self.rt.config.roundtable.clipper
        if not rules.enabled:
            raise ServiceError("资料浏览器已在配置里关闭", 403)
        return rules

    def clips(self) -> dict[str, Any]:
        rules = self.rt.config.roundtable.clipper
        return {
            "enabled": rules.enabled,
            "search": self.rt.search.available,
            "search_estimate_usd": rules.search_estimate_usd,
            "max_clip_chars": rules.max_clip_chars,
            "clips": [clipper.public_clip(c) for c in clipper.clips_of(self.rt.repo)],
        }

    async def clip_open(self, url: str) -> dict[str, Any]:
        """打开网页：直接抓取并提取正文（不调用模型、不花钱）。"""
        rules = self._clip_rules()
        try:
            url = clipper.normalize_url(url)
            fetched = await clipper.http_fetch(url, rules.timeout_s, rules.max_page_kb * 1024)
            page = clipper.parse_fetched(fetched, max_chars=rules.max_page_chars)
        except clipper.ClipError as exc:
            raise ServiceError(str(exc), 422) from None
        out = page.public()
        if not page.blocks:
            out["hint"] = "没提取到正文：这个网页可能要登录，或者内容是用脚本加载的"
        return out

    async def clip_search(self, query: str) -> dict[str, Any]:
        """联网搜索（有花费，计入总预算）。只返回标题、网址和摘要。"""
        rules = self._clip_rules()
        query = (query or "").strip()[:200]
        if not query:
            raise ServiceError("请输入要搜索的内容")
        rt = self.rt
        if not rt.search.available:
            raise ServiceError(f"联网搜索不可用：{rt.search.reason()}", 409)
        if not rt.budget.check(rules.search_estimate_usd).allowed:
            raise ServiceError("本月或今天的预算已用完，不能搜索", 409)
        try:
            result, call = await rt.search.search(query, rules.search_results)
        except SearchUnavailable as exc:
            raise ServiceError(str(exc), 502) from None
        completion = Completion(
            text="",
            model_id=f"search:{call.provider}",
            channel=call.provider,
            channel_kind="search",
            route_model="search",
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost_usd=call.cost_usd,
            cost_source=call.cost_source,  # type: ignore[arg-type]
            latency_s=call.latency_s,
            attempts=call.attempts,
        )
        rt.repo.record_call(
            rt.repo.chat_session(clipper.SEARCH_ROOM),
            step=clipper.SEARCH_STEP,
            role="tool",
            model_id=completion.model_id,
            messages=[Message("user", f"search: {query}")],
            completion=completion,
        )
        hits = [
            {
                "title": clipper.strip_tags(h.title)[:300] or h.url,
                "url": h.url,
                "snippet": clipper.strip_tags(h.snippet)[:400],
            }
            for h in result.hits
            if h.url.startswith(("http://", "https://"))
        ]
        return {"query": query, "hits": hits, "cost_usd": call.cost_usd}

    def clip_save(
        self, url: str, title: str, text: str, *, partial: bool = False, note: str = ""
    ) -> dict[str, Any]:
        """把正文或选中的段落存进资料夹（只存本机）。"""
        rules = self._clip_rules()
        try:
            url = clipper.normalize_url(url)
        except clipper.ClipError as exc:
            raise ServiceError(str(exc)) from None
        text = (text or "").strip()
        if not text:
            raise ServiceError("没有可保存的内容")
        cut = len(text) > rules.max_clip_chars
        text = text[: rules.max_clip_chars]
        chars = len(text)
        if cut:
            text += "\n\n（后面还有内容，超过资料字数上限没有保存）"
        title = " ".join((title or "").split())[:200] or clipper.site_of(url)
        saved_at = self._local_now().strftime("%Y-%m-%d %H:%M")
        body = clipper.clip_markdown(title, url, text, saved_at, " ".join(note.split())[:300])
        key = self.rt.files.save(body.encode("utf-8"), "md")
        clip = {
            "id": clipper.new_clip_id(),
            "title": title,
            "url": url,
            "site": clipper.site_of(url),
            "chars": chars,
            "excerpt": " ".join(text.replace("## ", "").split())[:160],
            "partial": partial,
            "saved_at": self.rt.budget.now().isoformat(),
            "key": key,
        }
        clips = [clip, *clipper.clips_of(self.rt.repo)][: rules.max_clips]
        clipper.save_clips(self.rt.repo, clips)
        return {**clipper.public_clip(clip), "truncated": cut}

    def _clip(self, clip_id: str) -> dict[str, Any]:
        for clip in clipper.clips_of(self.rt.repo):
            if clip["id"] == clip_id:
                return clip
        raise ServiceError("资料不存在", 404)

    def clip_text(self, clip_id: str) -> dict[str, Any]:
        clip = self._clip(clip_id)
        try:
            text = self.rt.files.load(clip["key"]).decode("utf-8")
        except (OSError, KeyError, ValueError):
            raise ServiceError("资料文件找不到了", 404) from None
        return {**clipper.public_clip(clip), "text": text}

    def clip_delete(self, clip_id: str) -> dict[str, Any]:
        self._clip(clip_id)
        rest = [c for c in clipper.clips_of(self.rt.repo) if c["id"] != clip_id]
        clipper.save_clips(self.rt.repo, rest)
        return self.clips()

    def clip_attach(self, clip_id: str) -> dict[str, Any]:
        """提问时附上：用这份资料新建一个 Markdown 附件（附件只能用一次，所以每次新建）。"""
        clip = self._clip(clip_id)
        try:
            data = self.rt.files.load(clip["key"])
        except (OSError, KeyError, ValueError):
            raise ServiceError("资料文件找不到了", 404) from None
        return self.upload(clipper.safe_name(clip["title"]), data)

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
        carry_from: str | None = None,
    ) -> str:
        """创建会话并在后台开始执行，立即返回会话 id。

        tier：档位名或 "custom"（自选，models 为上桌的模型）；anonymous 默认关闭；
        workflow：discussion（讨论模式）或 collab（协同模式）；
        attachments：先用 upload() 上传得到的附件 id；
        style_refs：其中作为风格参考的图片附件 id（None = 全部图片；传空表示都不是）；
        media：讨论模式的输出类型（image / speech / video / music，默认文字）；
        media_tier：媒体模型的档位（budget / flagship）；
        carry_from：接力的上一场会话 id，question 是这一场的要求，上一场的成果摘要附在后面。
        """
        text = self._carried(question, carry_from, anonymous)
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

    def carry(self, session_id: str) -> dict[str, Any]:
        """接力用：上一场的题目和将带入下一场的成果摘要（揭晓前为匿名视图）。"""
        row, result = self._carry_source(session_id)
        return {
            "session_id": session_id,
            "question": row["question"],
            "workflow": row["workflow"],
            "anonymous": bool(row["anonymous"]),
            "summary": compact(
                self.rt.scrubber.scrub(result, row["question"]) if row["anonymous"] else result
            ),
        }

    def _carry_source(self, session_id: str) -> tuple[dict[str, Any], str]:
        row = self._require(session_id)
        if row["status"] != "completed":
            raise ServiceError("上一场还没完成，完成后才能接着做")
        result = self.orc.final_answer(session_id)
        if not result:
            raise ServiceError("上一场没有可以接着用的成果")
        return row, result

    def _carried(self, question: str, carry_from: str | None, anonymous: bool) -> str:
        """这一场的题目；接力时附上上一场的题目和成果摘要（只带成果，不带过程）。"""
        text = question.strip()
        if not text:
            raise ServiceError("题目不能为空")
        if carry_from is None:
            return text
        row, result = self._carry_source(carry_from)
        prev = row["question"]
        if anonymous or row["anonymous"]:  # 匿名的一方：成员名称一律遮蔽（原题里的名称保留）
            result = self.rt.scrubber.scrub(result, prev)
        return carry_question(prev, result, text)

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
        carry_from: str | None = None,
    ) -> dict[str, Any]:
        """提交前预估：各模式 × 各档位（及自选）的上桌人数、缺席、预计花费与步骤明细。

        不调用任何模型（答案长度只用规则判断，判断不出时用默认值）。返回的 seed 在提交时
        一并传给 create()，上桌名单就与预估一致。匿名开启时不返回模型名单。
        """
        text = self._carried(question, carry_from, anonymous)
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
                "lyrics": scrub(j["params"]["lyrics"]) if j["params"].get("lyrics") else None,
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

    def export_document(
        self, session_id: str, fmt: str, options: dict[str, Any] | None = None
    ) -> tuple[bytes, str, str]:
        """导出最终成果（内容、文件名、类型）。本地转换存库的成果，不调用模型、不花 token。

        options：size / theme / ratio / question / process（见 core/export/style.py），
        缺省用默认样式。
        """
        self._require(session_id)
        spec = EXPORT_FORMATS.get(fmt)
        if spec is None:
            raise ServiceError(f"不支持的导出格式：{fmt}（可选 {'、'.join(EXPORT_FORMATS)}）")
        try:
            style = ExportStyle(**{k: v for k, v in (options or {}).items() if v is not None})
        except (TypeError, ValueError) as exc:
            raise ServiceError(f"导出选项不对：{exc}") from None
        view = self.rt.repo.session_view(session_id, scrub=self.rt.scrubber.scrub)
        doc = deliverable(view, question=style.question, process=style.process)
        if doc is None:
            raise ServiceError("这一场还没有可以导出的成果", 409)
        return spec.render(doc, style), export_filename(doc, session_id, spec.ext), spec.mime

    def export_options(self) -> dict[str, Any]:
        """导出面板的可选项，以及记住的保存文件夹（没设过时给一个建议值）。"""
        saved = self.rt.repo.setting(EXPORT_FOLDER_KEY)
        return {
            "formats": [{"value": k, "label": f.label} for k, f in EXPORT_FORMATS.items()],
            **export_choices(),
            "folder": saved or "",
            "suggested_folder": str(Path.home() / "Documents" / "圆桌导出"),
        }

    def export_save(
        self, session_id: str, fmt: str, folder: str, options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """把导出的文件直接存进电脑上的文件夹（只允许本机调用，由接口层把关），并记住这个文件夹。"""
        target_dir = Path(folder.strip()).expanduser() if folder and folder.strip() else None
        if target_dir is None or not target_dir.is_absolute():
            raise ServiceError("请填写完整的文件夹路径，例如 D:\\圆桌导出")
        data, name, _ = self.export_document(session_id, fmt, options)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / name
            stem, n = target.stem, 2
            while target.exists():  # 不覆盖已有文件：加 (2)、(3)……
                target = target_dir / f"{stem} ({n}){target.suffix}"
                n += 1
            target.write_bytes(data)
        except OSError as exc:
            raise ServiceError(f"保存失败：{exc.strerror or '无法写入这个文件夹'}") from None
        self.rt.repo.set_setting(EXPORT_FOLDER_KEY, str(target_dir))
        return {"path": str(target), "name": target.name, "folder": str(target_dir)}

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
        if event.type in WORK_EVENTS:
            try:  # 工作群消息只是旁路记录，出错不能影响办公室里的事
                post_office_event(self.rt, session_id, event.type, event.data, event.code)
            except Exception:  # noqa: BLE001
                log.exception("会话 %s：写工作群消息失败", session_id)
            try:  # 聊天里交过来的活：结果发回原来的会话（旁路，出错不影响办公室）
                self._handoff_back(session_id, event.type)
            except Exception:  # noqa: BLE001
                log.exception("会话 %s：交办结果发回聊天失败", session_id)
            try:  # 老板同意交给工作台的大项目：开一个项目室（旁路，出错写进这场对话）
                self._bench_handoff(session_id, event.type)
            except Exception:  # noqa: BLE001
                log.exception("会话 %s：交给工作台失败", session_id)
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

    # --- 革新例会（docs/evolution/README.md） -------------------------------------------

    def _evolution_dirs(self) -> tuple[Path | None, Path | None]:
        base = self.rt.evolution_dir
        if base is None:
            return None, None
        rules = self.rt.config.roundtable.evolution
        return base / rules.inbox_dir, base / rules.adopted_dir

    def _meetings(self) -> list[Meeting]:
        return load_meetings(self.rt.repo.setting(MEETINGS_KEY))

    def _save_meetings(self, meetings: Sequence[Meeting]) -> None:
        self.rt.repo.set_setting(MEETINGS_KEY, dump_meetings(meetings))

    def _evolution_spent(self, meetings: Sequence[Meeting]) -> float:
        """本月革新例会已花：开过的例会 + 自己找资料（隐藏会话）。"""
        start, end = month_window(self.rt.budget.now())
        return self.rt.repo.spent_in_sessions(
            [m.session_id for m in meetings], start, end
        ) + self.rt.repo.spent_in_workflow("evolution_scout", start, end)

    def _archive_cards(self, inbox: Path, files: Sequence[str], week: str) -> None:
        """开过会的资料卡移到 已用/<周>/，下周不会再读；同名的加序号，不覆盖。"""
        base = self.rt.evolution_dir
        if base is None:
            return
        folder = base / self.rt.config.roundtable.evolution.used_dir / week
        folder.mkdir(parents=True, exist_ok=True)
        for name in files:
            src, dst, n = inbox / name, folder / name, 2
            while dst.exists():
                dst = folder / f"{Path(name).stem}-{n}{Path(name).suffix}"
                n += 1
            with contextlib.suppress(OSError):
                src.replace(dst)

    async def evolution_scout(self, *, now: datetime | None = None) -> dict[str, Any]:
        """圆桌自己找资料写卡：搜白名单网站、读网页、一位成员写卡、另一位复核，通过的进收件箱。

        花费上限：本月革新例会还剩的额度（monthly_usd − 已花）。
        """
        rules = self.rt.config.roundtable.evolution
        if not rules.enabled:
            raise ServiceError(
                "革新例会没有开启：在 config/roundtable.local.yaml 里写 evolution: {enabled: true}",
                409,
            )
        inbox, _ = self._evolution_dirs()
        if inbox is None:
            raise ServiceError("没有数据目录，放不了资料卡", 409)
        week = week_key((now or self._local_now()).astimezone(self._tz()))
        left = rules.monthly_usd - self._evolution_spent(self._meetings())
        try:
            report = await Scout(self.rt).run(inbox, week, left)
        except ScoutError as exc:
            raise ServiceError(str(exc), 409) from None
        self.rt.repo.set_setting(SCOUT_WEEK_KEY, week)
        return {"week": week, **report.public()}

    async def evolution_scout_tick(self, now: datetime | None = None) -> dict[str, Any] | None:
        """自动部分：到了本周开会时间、本周还没开会、收件箱是空的、本周还没找过，就先找一次资料。"""
        rules = self.rt.config.roundtable.evolution
        if not (rules.enabled and rules.auto and rules.scout.enabled):
            return None
        now = (now or self._local_now()).astimezone(self._tz())
        week = week_key(now)
        if now < week_slot(now, rules.schedule.weekday, rules.schedule.hour):
            return None
        if self._slot_met(week):
            return None
        if self.rt.repo.setting(SCOUT_WEEK_KEY) == week:
            return None
        inbox, _ = self._evolution_dirs()
        cards, _ = load_cards(inbox, max_cards=rules.max_cards, card_chars=rules.card_chars)
        if cards:
            return None
        try:
            return await self.evolution_scout(now=now)
        except ServiceError as exc:
            self.rt.repo.set_setting(SCOUT_WEEK_KEY, week)  # 本周不再重试，免得反复花钱
            log.info("革新例会：本次没有找资料（%s）", exc)
            return {"week": week, "skipped": str(exc)}

    def _tz(self) -> tzinfo | None:
        """主时区（None = 本机），见 core/hometz.py。"""
        return home_zone(self.rt).tz()

    def _local_now(self) -> datetime:
        return self.rt.budget.now().astimezone(self._tz())

    def evolution(self) -> dict[str, Any]:
        """革新例会概况：开关、收件箱里的资料卡、本月花费与上限、最近的例会。"""
        rules = self.rt.config.roundtable.evolution
        inbox, adopted = self._evolution_dirs()
        cards, notes = load_cards(inbox, max_cards=rules.max_cards, card_chars=rules.card_chars)
        meetings = self._meetings()
        week = week_key(self._local_now())
        lists = load_lists(self.rt.repo.setting(PLUGINS_KEY))
        recent = []
        for m in reversed(meetings[-10:]):
            try:
                row = self.rt.repo.session_row(m.session_id)
            except NotFound:
                continue
            recent.append(
                {
                    **asdict(m),
                    "status": row["status"],
                    "cost_usd": self.rt.repo.session_cost(m.session_id),
                    # 最近 3 场开完的附上得票前几名，网页上可以点「做这条 / 不做」
                    "ideas": self._meeting_ideas(m.session_id, m.kind) if len(recent) < 3 else [],
                    "kind_label": MEETING_KINDS.get(m.kind, m.kind),
                    "top": self._top(m.kind),
                    # 插件清单（写过的才有）：做什么、有什么用、优缺点、为什么做、场景
                    "plugins": self._meeting_plugins(m.session_id, lists)
                    if len(recent) < 3
                    else None,
                }
            )
        return {
            "enabled": rules.enabled,
            "auto": rules.auto,
            "schedule": rules.schedule.model_dump(),
            "plan": rules.plan,
            "plan_label": getattr(
                self.rt.config.routing.plans.get(rules.plan), "label", rules.plan
            ),
            "anonymous": rules.anonymous,
            "inbox": str(inbox) if inbox else None,
            "adopted_dir": str(adopted) if adopted else None,
            "cards": [c.public() for c in cards],
            "notes": notes,
            "week": week,
            "met_this_week": self._slot_met(week),
            "meeting_usd": rules.meeting_usd,
            "monthly_usd": rules.monthly_usd,
            "month_spent_usd": self._evolution_spent(meetings),
            "kinds": {
                k: {"label": label, "top": self._top(k)} for k, label in MEETING_KINDS.items()
            },
            "themes": rules.major_themes,
            "theme_chars": rules.theme_chars,
            "next": self.evolution_plan(),  # 下一场：自动判断开哪种、为什么、主题
            "next_theme": self._next_theme(),  # 你定的下次大版本主题（没定 = 轮换）
            "plugins": {
                "enabled": rules.plugins.enabled,
                "count": rules.plugins.count,
                "scenes": rules.plugins.scenes,
                "estimate_usd": self._plugins_estimate(),
            },
            "meetings": recent,
            "scout": {
                "enabled": rules.scout.enabled,
                "domains": rules.scout.domains,
                "last_week": self.rt.repo.setting(SCOUT_WEEK_KEY),
                "can_search": self.rt.search.available and self.rt.search.can_fetch,
            },
        }

    def _top(self, kind: str) -> int:
        rules = self.rt.config.roundtable.evolution
        return rules.major_top if kind == "major" else rules.minor_top

    def _slot_met(self, week: str) -> bool:
        """本周的例会开过没有：按自动判断开的会（不管大小版本）和小版本都算；
        你手动改成大版本另开的一场不占每周的名额。"""
        return any(
            m.week == week and (m.kind == "minor" or (m.reason is not None and not m.override))
            for m in self._meetings()
        )

    def _next_theme(self) -> str | None:
        return (self.rt.repo.setting(NEXT_THEME_KEY) or "").strip() or None

    def _plan(self, now: datetime | None = None, meetings: Sequence[Meeting] | None = None):
        rules = self.rt.config.roundtable.evolution
        return plan_meeting(
            (now or self._local_now()).astimezone(self._tz()),
            self._meetings() if meetings is None else meetings,
            gap_days=rules.major_gap_days,
            themes=rules.major_themes,
            user_theme=self._next_theme(),
        )

    def evolution_plan(self) -> dict[str, Any]:
        """下一场自动判断的结果：开哪种、为什么、大版本的主题（你定的还是轮换的）。"""
        plan = self._plan()
        rules = self.rt.config.roundtable.evolution
        major_theme, _ = choose_theme(rules.major_themes, self._meetings(), self._next_theme())
        return {
            **asdict(plan),
            "label": MEETING_KINDS[plan.kind],
            "top": self._top(plan.kind),
            "major_theme": major_theme,  # 手动改成大版本时会用的主题
        }

    def evolution_set_theme(self, theme: str | None) -> dict[str, Any]:
        """定下一次大版本的主题（空 = 交给自动轮换）。开完那场大版本后自动清掉。"""
        theme = (theme or "").strip()
        limit = self.rt.config.roundtable.evolution.theme_chars
        if len(theme) > limit:
            raise ServiceError(f"主题太长了，最多 {limit} 字")
        self.rt.repo.set_setting(NEXT_THEME_KEY, theme)
        return self.evolution_plan()

    def evolution_meet(
        self,
        *,
        auto: bool = False,
        now: datetime | None = None,
        kind: str | None = None,
        theme: str | None = None,
    ) -> dict[str, Any]:
        """开一场革新例会：资料卡作为附件（全员同一份），走头脑风暴，所选档位全员上桌。

        kind 不给 = 按时间自动判断（evolution.plan_kind）；给了且和自动判断不同 = 你手动改的。
        minor 小版本主题不限，列前 minor_top 名；major 大版本围绕主题，列前 major_top 名，
        主题依次取：这次给的 → 你定的下次主题 → 现成主题轮换。

        开会前预估花费（不调用模型）：超过每场上限，或本月已花 + 本场预计超过每月上限，就不开。
        """
        rules = self.rt.config.roundtable.evolution
        if not rules.enabled:
            raise ServiceError(
                "革新例会没有开启：在 config/roundtable.local.yaml 里写 evolution: {enabled: true}",
                409,
            )
        if rules.plan not in self.rt.config.routing.plans:
            raise ServiceError(f"evolution.plan 不是已有的档位：{rules.plan!r}")
        if kind is not None and kind not in MEETING_KINDS:
            raise ServiceError(f"例会类型只能是 {'、'.join(MEETING_KINDS)}：{kind!r}")
        now = (now or self._local_now()).astimezone(self._tz())
        meetings = self._meetings()
        plan = self._plan(now, meetings)
        override = kind is not None and kind != plan.kind
        kind = kind or plan.kind
        reason = f"你手动改成了{MEETING_KINDS[kind]}" if override else plan.reason
        theme = (theme or "").strip() or None
        from_setting = False
        if kind == "minor":
            theme = None  # 小版本主题不限
        elif theme is None:
            theme, source = choose_theme(rules.major_themes, meetings, self._next_theme())
            from_setting = source == "user"
        if kind == "major" and theme is None:
            raise ServiceError("大版本要先定一个主题（例如「群聊更自然」）")
        if theme and len(theme) > rules.theme_chars:
            raise ServiceError(f"主题太长了，最多 {rules.theme_chars} 字")
        inbox, _ = self._evolution_dirs()
        if inbox is None:
            raise ServiceError("没有数据目录，读不到资料卡", 409)
        week = week_key(now)
        if auto and self._slot_met(week):
            raise ServiceError(f"{week} 已经开过革新例会", 409)
        cards, notes = load_cards(inbox, max_cards=rules.max_cards, card_chars=rules.card_chars)
        if not cards:
            raise ServiceError(f"收件箱里没有资料卡（.md / .txt）：{inbox}", 409)
        attachments = [self.upload(c.file, c.attachment_bytes())["id"] for c in cards]
        question = meeting_question(rules.topic, cards, kind=kind, theme=theme)
        est = self.estimate(
            question,
            tier=rules.plan,
            workflow="brainstorm",
            anonymous=rules.anonymous,
            attachments=attachments,
        )
        option = next(
            (
                o
                for o in est["options"]
                if o["workflow"] == "brainstorm" and o["plan"] == rules.plan
            ),
            None,
        )
        if option is None or "estimate_usd" not in option:
            reason = option["reason"] if option else "没有这个档位"
            raise ServiceError(f"现在开不了革新例会：{reason}", 409)
        # 开完后还要写插件清单：一起算进每场 / 每月上限
        cost = option["estimate_usd"] + self._plugins_estimate()
        if cost > rules.meeting_usd:
            raise ServiceError(
                f"本场预计 ${cost:.2f}（含插件清单），超过每场上限 ${rules.meeting_usd:.2f}，"
                "没有开会（可以少放几张资料卡）",
                409,
            )
        spent = self._evolution_spent(meetings)
        if spent + cost > rules.monthly_usd + 1e-9:
            raise ServiceError(
                f"本月革新例会已花 ${spent:.2f}，加上本场预计 ${cost:.2f} 会超过每月上限"
                f" ${rules.monthly_usd:.2f}，没有开会",
                409,
            )
        sid = self.create(
            question,
            tier=rules.plan,
            workflow="brainstorm",
            anonymous=rules.anonymous,
            seed=est["seed"],
            attachments=attachments,
        )
        meetings.append(
            Meeting(
                sid,
                week,
                now.isoformat(timespec="seconds"),
                auto,
                kind=kind,
                theme=theme,
                reason=reason,
                override=override,
            )
        )
        self._save_meetings(meetings)
        if from_setting:
            self.rt.repo.set_setting(NEXT_THEME_KEY, "")  # 你定的主题用过了，下次回到轮换
        self._archive_cards(inbox, [c.file for c in cards], week)
        return {
            "session_id": sid,
            "week": week,
            "kind": kind,
            "theme": theme,
            "reason": reason,
            "override": override,
            "estimate_usd": cost,
            "cards": [c.public() for c in cards],
            "notes": notes,
        }

    def evolution_export(self, session_id: str) -> dict[str, Any]:
        """导出采纳单（Markdown）到 evolution/采纳单/。揭晓前用匿名视图，提出者只写代号。"""
        meetings = self._meetings()
        meeting = next((m for m in meetings if m.session_id == session_id), None)
        if meeting is None:
            raise ServiceError("这场不是革新例会", 404)
        self._require(session_id)
        view = self.rt.repo.session_view(session_id, scrub=self.rt.scrubber.scrub)
        if view.status != "completed":
            raise ServiceError("例会还没开完，开完才能导出采纳单", 409)
        _, folder = self._evolution_dirs()
        if folder is None:
            raise ServiceError("没有数据目录，无法保存采纳单", 409)
        board = self._meeting_board(view)
        text = adoption_markdown(
            week=meeting.week,
            session_id=session_id,
            board=board,
            cards=[a["name"] for a in self._attachments(session_id)],
            top=self._top(meeting.kind),
            kind=meeting.kind,
            theme=meeting.theme,
            cost_usd=view.cost_usd,
            created_at=view.created_at,
        )
        entry = load_lists(self.rt.repo.setting(PLUGINS_KEY)).get(session_id)
        if entry and entry.get("file"):
            count = len(entry.get("plugins") or [])
            text += f"\n插件清单（{count} 个）另存为 插件清单/{entry['file']}。\n"
        folder.mkdir(parents=True, exist_ok=True)
        path = adoption_path(folder, meeting.week)
        path.write_text(text, encoding="utf-8")
        meeting.adopted = path.name
        self._save_meetings(meetings)
        return {"file": path.name, "path": str(path), "markdown": text}

    @staticmethod
    def _meeting_board(view: Any) -> Board:
        maps = [o for o in view.outputs if o.kind == "mindmap"]
        data = json.loads(maps[-1].content) if maps else {}
        return Board.from_dict(data["board"]) if data.get("board") else Board()

    def _meeting_ideas(self, session_id: str, kind: str = "minor") -> list[dict[str, Any]]:
        """开完的例会得票前几名，附上你的决定（做 / 不做 / 还没定）。揭晓前用匿名视图。"""
        view = self.rt.repo.session_view(session_id, scrub=self.rt.scrubber.scrub)
        if view.status != "completed":
            return []
        decisions = load_decisions(self.rt.repo.setting(DECISIONS_KEY))
        top = self._top(kind)
        out = []
        for r in adoptable(self._meeting_board(view))[:top]:
            key = decision_key(session_id, r.id)
            entry = decisions.get(key, {})
            job = dict(entry.get("job") or {})
            if job.get("status") == "running" and key not in self._dev_tasks:
                job["status"] = "interrupted"  # 服务中途重启过，开发侧没跑完
            out.append(
                {
                    "id": r.id,
                    "text": r.text,
                    "score": r.score,
                    "votes": r.votes,
                    "decision": entry.get("decision"),
                    "job": job or None,
                }
            )
        return out

    # --- 插件清单 ----------------------------------------------------------------------

    def _plugins_estimate(self) -> float:
        rules = self.rt.config.roundtable.evolution.plugins
        return rules.estimate_usd if rules.enabled else 0.0

    def _meeting_plugins(
        self, session_id: str, lists: dict[str, dict[str, Any]]
    ) -> dict[str, Any] | None:
        entry = lists.get(session_id)
        if entry is None:
            return None
        plugins = plugins_from(entry)
        return {
            "items": [asdict(p) for p in plugins],
            "scenes": by_scene(plugins, self.rt.config.roundtable.evolution.plugins.scenes),
            "cost_usd": entry.get("cost_usd", 0.0),
            "file": entry.get("file"),
            "error": entry.get("error"),
            "at": entry.get("at"),
        }

    async def evolution_plugins(self, session_id: str) -> dict[str, Any]:
        """给开完的例会写插件清单（至少 count 个）：所选档位里预计最便宜的一位成员，根据本场资料卡
        和点子榜写；存到 app_settings 和数据目录 evolution/插件清单/<周>.md。再点一次会重写。

        花费上限：本月革新例会还剩的额度（monthly_usd − 已花）。
        """
        rules = self.rt.config.roundtable.evolution
        if not rules.enabled:
            raise ServiceError(
                "革新例会没有开启：在 config/roundtable.local.yaml 里写 evolution: {enabled: true}",
                409,
            )
        meetings = self._meetings()
        meeting = next((m for m in meetings if m.session_id == session_id), None)
        if meeting is None:
            raise ServiceError("这场不是革新例会", 404)
        self._require(session_id)
        view = self.rt.repo.session_view(session_id, scrub=self.rt.scrubber.scrub)
        if view.status != "completed":
            raise ServiceError("例会还没开完，开完才能写插件清单", 409)
        cards = [
            (a.name, a.text or "")
            for a in (Attachment.from_row(r) for r in self.rt.repo.session_attachments(session_id))
        ]
        left = rules.monthly_usd - self._evolution_spent(meetings)
        lists = load_lists(self.rt.repo.setting(PLUGINS_KEY))
        at = self.rt.budget.now().isoformat(timespec="seconds")
        try:
            report = await PluginWriter(self.rt).run(cards, self._meeting_board(view), left)
        except PluginError as exc:
            lists[session_id] = {**lists.get(session_id, {}), "error": str(exc), "at": at}
            self.rt.repo.set_setting(PLUGINS_KEY, dump_lists(lists))
            raise ServiceError(str(exc), 409) from None
        text = plugins_markdown(
            week=meeting.week,
            session_id=session_id,
            plugins=report.plugins,
            cost_usd=report.cost_usd,
            scenes=rules.plugins.scenes,
        )
        file = None
        if self.rt.evolution_dir is not None:
            folder = self.rt.evolution_dir / rules.plugins.dir
            folder.mkdir(parents=True, exist_ok=True)
            path = adoption_path(folder, meeting.week)
            path.write_text(text, encoding="utf-8")
            file = path.name
        lists[session_id] = {
            **report.public(),
            "file": file,
            "at": at,
            "short": len(report.plugins) < rules.plugins.count,
        }
        self.rt.repo.set_setting(PLUGINS_KEY, dump_lists(lists))
        return {"session_id": session_id, "file": file, "markdown": text, **report.public()}

    async def evolution_plugins_tick(self) -> list[str]:
        """自动部分：开完、还没写过插件清单（也没失败过）的例会各写一次。"""
        rules = self.rt.config.roundtable.evolution
        if not (rules.enabled and rules.auto and rules.plugins.enabled):
            return []
        done = []
        lists = load_lists(self.rt.repo.setting(PLUGINS_KEY))
        for m in self._meetings()[-3:]:
            if m.session_id in lists or self.running(m.session_id):
                continue
            try:
                row = self.rt.repo.session_row(m.session_id)
            except NotFound:
                continue
            if row["status"] != "completed":
                continue
            try:
                await self.evolution_plugins(m.session_id)
                done.append(m.session_id)
            except ServiceError as exc:  # 失败已记下，不反复花钱；网页上可以手动再写
                log.info("革新例会：插件清单没有写成（%s）", exc)
        return done

    def _save_job(self, key: str, **job: Any) -> None:
        decisions = load_decisions(self.rt.repo.setting(DECISIONS_KEY))
        if key in decisions:
            decisions[key]["job"] = {**(decisions[key].get("job") or {}), **job}
            self.rt.repo.set_setting(DECISIONS_KEY, json.dumps(decisions, ensure_ascii=False))

    def _dev(self) -> DevAgent:
        if self.rt.evolution_dir is None:
            raise ServiceError("没有数据目录，放不了开发副本", 409)
        rules = self.rt.config.roundtable.evolution.implement
        return DevAgent(rules, self.rt.evolution_dir, PROJECT_ROOT, self.dev_run)

    async def _dev_implement(self, key: str, entry: dict[str, Any]) -> None:
        try:
            status, pr, log_file = await self._dev().implement(
                week=entry["week"], session_id=entry["session_id"], idea=entry["idea"],
                text=entry["text"],
            )  # fmt: skip
            self._save_job(key, status=status, pr=pr, log=log_file)
        except (DevError, ServiceError, OSError) as exc:
            self._save_job(key, status="failed", error=str(exc)[:300])
        finally:
            self._dev_tasks.pop(key, None)

    def _start_dev(self, key: str, entry: dict[str, Any]) -> bool:
        """「做这条」后交给开发侧（开启 implement 时）；已经在做或做完的不重复叫。"""
        if not self.rt.config.roundtable.evolution.implement.enabled:
            return False
        job = entry.get("job") or {}
        if key in self._dev_tasks or job.get("status") == "done":
            return False
        self._save_job(
            key, status="running", started=self._local_now().isoformat(timespec="seconds")
        )
        self._dev_tasks[key] = asyncio.get_running_loop().create_task(
            self._dev_implement(key, entry)
        )
        return True

    async def evolution_merge(self, session_id: str, idea_id: str) -> dict[str, Any]:
        """你点了「合并」：把开发侧为这条建议开的 PR 合并（gh pr merge）。只有你点才会合并。"""
        key = decision_key(session_id, idea_id)
        entry = load_decisions(self.rt.repo.setting(DECISIONS_KEY)).get(key)
        job = (entry or {}).get("job") or {}
        if not job.get("pr") or job.get("status") != "done":
            raise ServiceError("这条建议还没有开好的 PR", 409)
        if job.get("merged"):
            raise ServiceError("已经合并过了", 409)
        dev = self._dev()
        try:
            ok = await dev.merge(job["pr"], dev.log_path(entry["week"], idea_id))
        except DevError as exc:
            raise ServiceError(str(exc), 409) from None
        if not ok:
            raise ServiceError("合并没有成功（可能有冲突或检查没通过），详见开发记录", 409)
        self._save_job(key, merged=self._local_now().isoformat(timespec="seconds"))
        return {"idea": idea_id, "pr": job["pr"], "merged": True}

    def evolution_decide(self, session_id: str, idea_id: str, decision: str) -> dict[str, Any]:
        """你对采纳单里某条建议的决定：do（做这条）/ skip（不做）。

        「做这条」的写进 evolution/待实现.md，开发侧按它实现、开 PR；合并仍由你点。
        """
        if decision not in DECISIONS:
            raise ServiceError(f"决定只能是 {'、'.join(DECISIONS)}", 400)
        meeting = next((m for m in self._meetings() if m.session_id == session_id), None)
        if meeting is None:
            raise ServiceError("这场不是革新例会", 404)
        self._require(session_id)
        idea = next((i for i in self._meeting_ideas(session_id) if i["id"] == idea_id), None)
        if idea is None:
            raise ServiceError("采纳单的前几名里没有这条建议（或例会还没开完）", 404)
        decisions = load_decisions(self.rt.repo.setting(DECISIONS_KEY))
        key = decision_key(session_id, idea_id)
        entry = {
            "decision": decision,
            "session_id": session_id,
            "week": meeting.week,
            "idea": idea_id,
            "text": idea["text"],
            "at": self._local_now().isoformat(timespec="seconds"),
            **({"job": decisions[key]["job"]} if decisions.get(key, {}).get("job") else {}),
        }
        decisions[key] = entry
        self.rt.repo.set_setting(DECISIONS_KEY, json.dumps(decisions, ensure_ascii=False))
        todo = None
        if self.rt.evolution_dir is not None:
            self.rt.evolution_dir.mkdir(parents=True, exist_ok=True)
            todo = self.rt.evolution_dir / TODO_FILE
            todo.write_text(todo_markdown(decisions), encoding="utf-8")
        started = decision == "do" and self._start_dev(key, entry)
        return {
            "idea": idea_id,
            "decision": decision,
            "todo": str(todo) if todo else None,
            "dev_started": started,
        }

    def evolution_tick(self, now: datetime | None = None) -> dict[str, Any]:
        """自动部分：开完的例会导出采纳单；到了本周开会时间、本周还没开过就开一场。"""
        rules = self.rt.config.roundtable.evolution
        out: dict[str, Any] = {"started": None, "exported": [], "skipped": None}
        if not (rules.enabled and rules.auto):
            return out
        for m in self._meetings():
            if m.adopted is None and not self.running(m.session_id):
                with contextlib.suppress(ServiceError, NotFound):
                    out["exported"].append(self.evolution_export(m.session_id)["file"])
        now = (now or self._local_now()).astimezone(self._tz())
        week = week_key(now)
        if now < week_slot(now, rules.schedule.weekday, rules.schedule.hour) or self._slot_met(
            week
        ):
            return out
        try:
            out["started"] = self.evolution_meet(auto=True, now=now)["session_id"]
        except ServiceError as exc:
            out["skipped"] = str(exc)
            log.info("革新例会：本次没有开会（%s）", exc)
        return out

    # --- 插件榜 ------------------------------------------------------------------

    def plugin_board(self) -> dict[str, Any]:
        """自我学习革新调研过的插件 / 项目榜单（只读；每次重新读 config/plugin_board.yaml）。"""
        try:
            board = load_board(self.rt.plugin_board)
        except ConfigError as exc:
            raise ServiceError(str(exc), 500) from None
        return board_view(board)
