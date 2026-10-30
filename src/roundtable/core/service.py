"""对外 facade：Web 服务（以及以后的其他界面）只通过这里访问核心逻辑。

- 所有返回值都是可直接转成 JSON 的 dict / list；揭晓前不含任何模型身份。
- 讨论在后台任务中执行，提交后立即返回会话 id；进度通过事件订阅获得。
- 每当讨论完成、失败、停止或需要用户确认时，会发出一个 "state" 事件。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import secrets
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import quote

from roundtable.core import clipper
from roundtable.core.allocation import coordinator_name, display_name
from roundtable.core.almanac import Almanac
from roundtable.core.attachments import Attachment, UploadError, ingest
from roundtable.core.attachments.detect import TYPES as UPLOAD_TYPES
from roundtable.core.benchkit.builder import BenchBuilder
from roundtable.core.benchkit.scout import Scout as PluginScout
from roundtable.core.benchkit.scout import http_fetchers
from roundtable.core.browse import REFRESH_KEY, feeds_of, set_feeds
from roundtable.core.budget import report as usage_report
from roundtable.core.budget.channels import MonthSpend, save_priority
from roundtable.core.budget.guard import day_window, month_window
from roundtable.core.carry import carry_question, compact
from roundtable.core.changelog import changelog_view, load_changelog
from roundtable.core.chat import ROOM, WORK, ChatHub, chat_budget, dm_room
from roundtable.core.chat_actions import card_from_url, is_bare_url
from roundtable.core.chat_actions import card_text as chat_card_text
from roundtable.core.chat_media import MAX_BYTES as CHAT_MEDIA_MAX
from roundtable.core.chat_media import ChatMediaStore, MediaError, clean_items, placeholder
from roundtable.core.chat_work import WORK_EVENTS, post_handoff, post_office_event
from roundtable.core.config.loader import DEFAULT_CONFIG_DIR, ConfigError
from roundtable.core.evolution import (
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
from roundtable.core.evolution import KINDS as MEETING_KINDS
from roundtable.core.evolution_dev import DevAgent, DevError, run_process
from roundtable.core.evolution_dev import Run as DevRun
from roundtable.core.evolution_plugins import (
    PLUGINS_KEY,
    PluginError,
    PluginWriter,
    by_scene,
    dump_lists,
    load_lists,
    plugins_from,
    plugins_markdown,
)
from roundtable.core.evolution_scout import SCOUT_WEEK_KEY, Scout, ScoutError
from roundtable.core.export import FORMATS as EXPORT_FORMATS
from roundtable.core.export import Style as ExportStyle
from roundtable.core.export import deliverable
from roundtable.core.export import filename as export_filename
from roundtable.core.export import options as export_choices
from roundtable.core.growth import GrowthKeeper
from roundtable.core.hometz import home_zone
from roundtable.core.ideaboard import Board
from roundtable.core.insight import InsightKeeper
from roundtable.core.lan import COOKIE as LAN_COOKIE
from roundtable.core.lan import LanAccess, is_local, is_remote, qr_svg
from roundtable.core.life import LIFE_STEPS, LifeEngine
from roundtable.core.mail import MailAI, MailAIError, MailBox, MailBusy, OwaBrowser
from roundtable.core.media import KIND_LABELS, KINDS, MediaService
from roundtable.core.member import MemberEngine, ProfileError
from roundtable.core.member.about import AboutError, AboutMissing
from roundtable.core.member.bus import Event as MemberEvent
from roundtable.core.member.card import (
    card_patch,
    match_members,
    parse_cards,
    source_title,
    without_locked,
)
from roundtable.core.member.growth import monday, week_id
from roundtable.core.member.growth.report import lines as report_lines
from roundtable.core.member.memory import DiaryTool
from roundtable.core.member.memory.store import query_terms
from roundtable.core.member.relation import view_live
from roundtable.core.member.voice import VoicePolicy
from roundtable.core.member.yourstate import UserStatus
from roundtable.core.member_stickers import (
    MemberStickers,
    StickerBudgetExceeded,
    StickerError,
    StickerUnavailable,
)
from roundtable.core.member_voice import MAX_CHARS as VOICE_MAX_CHARS
from roundtable.core.member_voice import (
    SAMPLE_TEXT,
    MemberVoices,
    VoiceBudgetExceeded,
    VoiceClip,
    VoiceUnavailable,
)
from roundtable.core.members_art import MEMBERS_FILE, load_members, mime_of, resolve_file
from roundtable.core.memory import LAYERS, MemoryKeeper, clean_memory, prepare_items
from roundtable.core.moments import SEEN_KEY as MOMENTS_SEEN
from roundtable.core.notes import NoteError, check_can_add, clean_note
from roundtable.core.nowplaying import COVERS_DIR, NowPlaying, SmtcSource, list_covers, read_cover
from roundtable.core.office import building, payslip, roster
from roundtable.core.office import collect as collect_performance
from roundtable.core.office import reset as reset_performance
from roundtable.core.orchestrator import Orchestrator, OrchestratorError, RunResult
from roundtable.core.perception import Perceiver, PerceptionCache, SenseItem
from roundtable.core.persona import card_text, is_empty, persona_of, set_persona
from roundtable.core.plugin_board import board_view, load_board
from roundtable.core.preview import render_preview
from roundtable.core.providers import (
    Completion,
    Message,
    ProviderError,
    UnsupportedCapability,
    redact,
)
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
from roundtable.core.runtime import PROJECT_ROOT, WANDER_DIR, Runtime, data_dir
from roundtable.core.screen import (
    AdbSource,
    PcSource,
    ScreenUnavailable,
    ScreenWatch,
    new_qr_pairing,
    qr_pairing_payload,
)
from roundtable.core.search import SearchUnavailable
from roundtable.core.standee import load_standee
from roundtable.core.standee import standee_file as read_standee_file
from roundtable.core.steps import Event, restore_state
from roundtable.core.steps.office import group_estimates
from roundtable.core.storage import NotFound
from roundtable.core.supercomputer import Supercomputer
from roundtable.core.supercomputer.launch import CommandLauncher, HookLauncher
from roundtable.core.wander import Wanderer
from roundtable.core.yourpc import WindowsPc, game_of

try:  # dsh 工作台是可拔掉的外设：删掉整个 core/dsh/ 圆桌照常运行（docs/DSH.md，CI「拔掉 dsh」任务）
    from roundtable.core.dsh import DshManager
except ImportError:  # pragma: no cover - 只在拔掉 dsh 时走到
    DshManager = None  # type: ignore[assignment,misc]

log = logging.getLogger(__name__)

# 讨论停下来（结束或等待用户）的状态：此时推送流会关闭
RESTING = frozenset({"completed", "stopped", "failed", "awaiting_confirmation", "paused"})
REVEALABLE = frozenset({"completed", "stopped", "failed"})
ORPHANED_ERROR = "服务上次意外退出，做到一半中断了，可以点「继续」接着做"
MAX_QUESTION_CHARS = 20_000
MAX_CHAT_CHARS = 2000
MAX_MOMENT_CHARS = 500
ADB_QR_TTL_S = 180  # 配对二维码几秒内有效（手机上的扫码页面本身也会超时）
# 公寓楼：进行中的会话这么久没有更新就不算在上班（服务意外退出后留下的 running）
WORKING_WINDOW_H = 3

__all__ = [
    # 局域网访问（api 层经 service 使用，见 core/lan.py）
    "LAN_COOKIE",
    "LanAccess",
    "data_dir",
    "is_local",
    "is_remote",
    "qr_svg",
    "CHAT_MEDIA_MAX",
    "MAX_QUESTION_CHARS",
    "NotFound",
    "RoundtableService",
    "ServiceError",
]


EXPORT_FOLDER_KEY = "export.folder"  # 导出时「存到固定文件夹」记住的文件夹


def _has_tk() -> bool:
    """这台电脑的 Python 带不带 tkinter（一起看的悬浮窗要用）。"""
    import importlib.util

    return importlib.util.find_spec("tkinter") is not None


class ServiceError(ValueError):
    """请求不合法（参数错误、状态不允许等）。status 为建议的 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# 转发时带过去的附加信息（图片、卡片、表情包说明文字）
FORWARD_KEYS = ("media", "caption", "auto_text", "card", "perception")


class RoundtableService:
    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime
        self.orc = Orchestrator(runtime, on_event=self._publish)
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._art_names_cache: tuple[int | None, dict[str, str]] | None = None
        self.chats = ChatHub(runtime, names=self._art_names)
        if runtime.config.roundtable.chat.engine.voice.enabled:  # P5 表达：照人物卡说话风格
            self.chats.policy = VoicePolicy(runtime)
        self.voices = MemberVoices(runtime, runtime.voice_cache_dir)
        self.stickers = MemberStickers(runtime, runtime.sticker_dir)
        self.memory = MemoryKeeper(runtime, self.chats.everyone)
        self.life = LifeEngine(runtime, self.chats, self.memory)
        self.perceiver = Perceiver(runtime, PerceptionCache(runtime.perception_dir))
        self.engine = MemberEngine(
            runtime,
            lambda: [m.id for m in self.chats.everyone()],
            lambda: {m.id: m.name for m in self.chats.everyone()},
        )
        state = getattr(self.engine, "state", None)  # 状态插件（心情）装上后，夜间整理按心情改语气
        if state is not None:
            self.memory.mood_of = state.mood_of
        about = getattr(self.engine, "about", None)  # P8 关于你：夜间整理交来的线索写进画像
        if about is not None:
            self.memory.on_clues = about.observe
        # P6 成长：每周一用成员自己的模型反思上一周，程序把关后才写档案；花费计入生活每天的上限
        self.growth = GrowthKeeper(runtime, self.chats.everyone, folder=runtime.growth_dir)
        self.growth.allow = self.life.allowed
        # P4 关系·对你的见解：和成长同一天，每位按自己的性格读一遍画像；花费计入生活每天的上限
        self.insight = InsightKeeper(runtime, self.chats.everyone)
        self.insight.allow = self.life.allowed
        # P8 第二块：你的状态（每项默认关）；聊天时现问一次，读到的不写库
        self._pc = runtime.pc_source if runtime.pc_source is not None else WindowsPc()
        runtime.user_status = UserStatus(
            runtime,
            {
                "music": self._say_music,
                "idle": self._say_idle,
                "game": self._say_game,
                "mail": self._say_mail,
            },
            {
                "music": lambda: self._np().source.available(),
                "idle": self._pc.available,
                "game": self._pc.available,
                "mail": lambda: None if self.mail.enabled() else "邮件栏还没打开",
            },
        )
        self.calendar = Almanac(runtime, self.chats.everyone)
        self.mail = MailBox(runtime.repo)
        self.mail_ai = MailAI(runtime, self.mail)
        if runtime.mail_source is not None:
            self.mail.source = runtime.mail_source
        elif runtime.mail_dir is not None:
            self.mail.source = OwaBrowser(
                runtime.mail_dir,
                extra_hosts=self.mail.extra_hosts,
                show_window=self.mail.show_window,
            )
        self._nowplaying: NowPlaying | None = None
        watch_rules = runtime.config.roundtable.chat.watch
        self.watch = ScreenWatch(
            runtime,
            self.chats.everyone,
            runtime.screen_sources
            if runtime.screen_sources is not None
            else {
                "pc": PcSource(watch_rules.ignore_windows),
                "android": AdbSource(watch_rules.adb),
            },
            publish=self.engine.bus.publish,
        )
        self._adb_qr: dict[str, Any] | None = None  # 正在等手机扫的配对二维码（只在内存里）
        base = runtime.mail_dir.parent if runtime.mail_dir is not None else data_dir()
        self.wander = Wanderer(
            runtime,
            self.chats,
            self.life.allowed,
            profiles=base / WANDER_DIR,
            publish=self.engine.bus.publish,
        )
        self.engine.heartbeat.register("wander", self.wander.want)
        self.dsh = self._build_dsh(runtime, base if runtime.mail_dir is not None else None)
        self.supercomputer = self._build_super(
            runtime, base if runtime.mail_dir is not None else None
        )
        self.bench = BenchBuilder(
            runtime.config.roundtable.bench,
            root=(base / "bench") if runtime.mail_dir is not None else None,
            supercomputer=self.supercomputer,
            render=self._super_render,
            scout=self._build_scout(runtime.config.roundtable.bench),
        )
        self._chat_tasks: set[asyncio.Task[Any]] = set()
        self._life_task: asyncio.Task[None] | None = None
        self._evolution_task: asyncio.Task[None] | None = None
        self._dev_tasks: dict[str, asyncio.Task[None]] = {}  # 「做这条」后正在实现的建议
        self.dev_run: DevRun = run_process  # 叫外部命令（测试换成假的）

    @staticmethod
    def _build_dsh(runtime: Runtime, base: Path | None) -> Any:
        """dsh 工作台（可选）：core/dsh 不在时为 None，其余功能不受影响。"""
        if DshManager is None:
            return None
        version = runtime.config.roundtable.prompts.get("dsh_workbench")

        def persona() -> str:
            if version is None:
                return ""
            return runtime.prompts.render("dsh_workbench", version).messages[0].content

        return DshManager(
            runtime.config.roundtable.dsh,
            catalog_path=DEFAULT_CONFIG_DIR / "dsh_plugins.yaml",
            root=base / "dsh" if base is not None else None,
            persona=persona,
            shared_spend=runtime.repo.spent_on_channels,
            dotenv_path=PROJECT_ROOT / ".env",
        )

    @staticmethod
    def _build_scout(rules: Any) -> PluginScout:
        fetch_json, fetch_bytes = http_fetchers()
        return PluginScout(
            fetch_json=fetch_json,
            fetch_bytes=fetch_bytes,
            min_stars=rules.min_stars,
            max_kb=rules.max_repo_mb * 1024,
        )

    def _super_render(self, role: str, **values: str) -> str:
        """超级电脑和工作台生成器发给助手的全文：系统和用户两段拼在一起（命令行助手只收一段）。"""
        rendered = self.rt.prompts.render(role, self.rt.config.roundtable.prompts[role], **values)
        return "\n\n".join(m.content for m in rendered.messages)

    def _build_super(self, runtime: Runtime, base: Path | None) -> Supercomputer:
        """超级电脑（docs/SUPERCOMPUTER.md）：命令行助手 + dsh（带了 dsh 才有）。"""
        config = runtime.config
        keys = [c.key_env for c in config.models.channels.values() if c.key_env]
        keys += [p.key_env for p in config.models.search_providers.values() if p.key_env]
        if config.roundtable.dsh.key_env:
            keys.append(config.roundtable.dsh.key_env)
        launchers: dict[str, Any] = {"command": CommandLauncher(drop_env=keys)}
        if self.dsh is not None:
            dsh = self.dsh
            launchers["dsh"] = HookLauncher(
                dsh.job_blocked, dsh.job_launch, dsh.job_started, dsh.job_finished
            )
        return Supercomputer(
            config.roundtable.supercomputer,
            root=base / "super" if base is not None else None,
            render=self._super_render,
            launchers=launchers,
        )

    def reconcile(self) -> list[str]:
        """开机对账：上次服务意外退出（或被关掉）时还在跑的讨论，数据库里会一直留着「进行中」。

        启动时把它们改成「已暂停」，界面上点「继续」就用 resume 从中断处接着做
        （已完成的步骤不重复）。
        只在正式启动的服务里、开始接受请求前调用一次。
        """
        ids = self.rt.repo.pause_orphaned(ORPHANED_ERROR)
        if ids:
            log.info("开机对账：%d 场讨论在上次退出时中断，已改为暂停", len(ids))
        return ids

    def start_life(self) -> None:
        """后台推进成员的生活（只在正式启动的服务里调用；测试直接调 life_tick）。"""
        if self._life_task is None and self.rt.config.roundtable.chat.life.enabled:
            self._life_task = asyncio.get_running_loop().create_task(self._life_loop())

    async def _life_loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):  # 生活出错不影响其他功能，下次再试
                await self.life_tick()
            await asyncio.sleep(self.rt.config.roundtable.chat.life.tick_minutes * 60)

    def start_evolution(self) -> None:
        """后台按时开革新例会（只在正式启动的服务里调用；测试直接调 evolution_tick）。"""
        rules = self.rt.config.roundtable.evolution
        if self._evolution_task is None and rules.enabled and rules.auto:
            self._evolution_task = asyncio.get_running_loop().create_task(self._evolution_loop())

    async def _evolution_loop(self) -> None:
        while True:
            try:
                await self.evolution_scout_tick()
                await self.evolution_plugins_tick()
                self.evolution_tick()
            except Exception:  # noqa: BLE001 - 例会出错不影响其他功能，下次再试
                log.exception("革新例会：自动检查出错")
            await asyncio.sleep(self.rt.config.roundtable.evolution.tick_minutes * 60)

    async def life_tick(self) -> dict[str, int]:
        """推进一次生活（开机后第一次会补上关机期间的时间），再推进一次成员引擎（不花钱）。"""
        now = self._local_now()
        out = await self.life.tick(now)
        with contextlib.suppress(Exception):  # 日历出错不影响生活
            self.calendar.tick(now, self.engine.bus.publish)
        with contextlib.suppress(Exception):  # 引擎出错不影响生活
            await self.engine.tick(now)
        with contextlib.suppress(Exception):  # 醒来回消息出错不影响生活
            await self.member_turns(now)
        with contextlib.suppress(Exception):  # 成长出错不影响生活，下次推进再试
            await self.growth.tick(now)
        with contextlib.suppress(Exception):  # 见解出错不影响生活，下次推进再试
            await self.insight.tick(now)
        return out

    async def member_turns(self, now: datetime) -> int:
        """成员自己开口的时候（不是回应你刚发的那条）：睡着没看到你的消息、醒了来回。
        返回开口的人数。"""
        from roundtable.core.member.rhythm import Rhythm

        rhythm, n = Rhythm(self.rt), 0
        for entry in rhythm.due(now):
            try:
                chat = self.chats.room(entry["room"])
            except KeyError:
                continue
            member = next((m for m in chat.members() if m.id == entry["member"]), None)
            mine = [r for r in chat.recent() if r["author"] == "user" and r["id"] >= entry["msg"]]
            if member is None or not member.available or not mine:
                continue
            chat.notes[member.id] = rhythm.late_note(entry)
            said = await chat.replies(mine[-1]["id"], [member])
            n += bool(said)
        return n + await self._reach_out(now)

    async def _reach_out(self, now: datetime) -> int:
        """主动找你：这次推进最多一位，有真事由才开口（core/member/reach.py 挑时机）。"""
        from roundtable.core.member.reach import Reach

        reach = Reach(self.rt)
        members = {m.id: m for m in self.chats.everyone() if m.available}
        urge = reach.pick(list(members), dm_room, now)
        if urge is None:
            return 0
        chat = self.chats.room(dm_room(urge.member))
        rows = chat.recent()
        chat.notes[urge.member] = f"{urge.note}\n{reach.rules.tail}"
        if urge.reason == "longing":  # 想念记成她的一段情绪经历（意图卡会带上她自己理解的想念）
            from roundtable.core.member.feel import Feelings

            feel = Feelings(self.rt)
            feel.record(urge.member, feel.rules.missing, feel.rules.reach_miss, now)
        said = await chat.replies(rows[-1]["id"] if rows else 0, [members[urge.member]])
        chat.notes.pop(urge.member, None)
        if not said:
            return 0
        reach.sent(urge, said[0]["id"], now)
        return 1

    async def aclose(self) -> None:
        self.watch.stop("服务关闭，投屏停了")
        with contextlib.suppress(Exception):
            await self.bench.aclose()
            await self.supercomputer.aclose()
        if self.dsh is not None:
            with contextlib.suppress(Exception):
                await self.dsh.aclose()
        await self.wander.aclose()
        tasks = [*self._tasks.values(), *self._chat_tasks]
        tasks += [t for t in (self._life_task, self._evolution_task) if t is not None]
        for task in tasks:
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
                        # 公寓宿舍的场景图（美术出图，白天 / 夜里），没有时网页用自绘场景
                        "room_day": url(m.images.get("room_day")),
                        "room_night": url(m.images.get("room_night")),
                    },
                    "pet": pet(m.pet),
                    "room": m.room,
                }
                for m in art.members
            ],
            "models": art.model_map(models),
            "sky": {k: url(v) for k, v in art.sky.items()},
            # 公共房间的场景图（美术出图）与家具点位，没有的房间网页用自绘场景
            "rooms": {
                k: {"day": url(v["day"]), "night": url(v["night"]), "room": v["room"]}
                for k, v in art.rooms.items()
            },
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

    # ---------- 音乐卡片：电脑上正在放的歌（QQ 音乐等，经系统媒体控制）与本地封面 ----------

    def _np(self) -> NowPlaying:
        if self._nowplaying is None:
            source = self.rt.nowplaying_source
            self._nowplaying = NowPlaying(source if source is not None else SmtcSource())
        return self._nowplaying

    def nowplaying(self, player: str = "qqmusic") -> dict[str, Any]:
        """只读：歌名、歌手、专辑、进度、能不能控制、封面地址。不存库、不调用模型。"""
        return self._np().view(player)

    def nowplaying_cover(self, player: str = "qqmusic") -> tuple[bytes, str]:
        found = self._np().cover(player)
        if found is None:
            raise ServiceError("没有封面", 404)
        return found

    def nowplaying_control(
        self, player: str, action: str, position: float | None = None
    ) -> dict[str, Any]:
        """播放 / 暂停 / 上一首 / 下一首 / 跳到某处：经系统媒体控制转给播放器。

        播放器不响应时 409。
        """
        try:
            ok = self._np().control(player, action, position)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        if not ok:
            raise ServiceError("播放器没有响应（可能没打开，或不支持这个操作）", 409)
        return self._np().view(player)

    # ---------- 投屏陪伴（docs/WATCH.md） ----------

    def watch_status(self, devices: bool = False) -> dict[str, Any]:
        """一起看的状态；devices=True 时另列可投的来源（会调 adb / 系统接口，较慢）。"""
        out = self.watch.status()
        if devices:
            out["devices"] = self.watch.devices()
            out["scrcpy"] = shutil.which(self.rt.config.roundtable.chat.watch.scrcpy) is not None
        return out

    def _adb(self) -> Any:
        src = self.watch.sources.get("android")
        if src is None or not hasattr(src, "pair"):
            raise ServiceError("这台电脑不能连安卓设备")
        return src

    def watch_adb_pair(self, host: str, port: int, code: str) -> dict[str, Any]:
        """安卓无线调试：用配对码配对（只建立 adb 连接）。配对后自动试着连上。"""
        src = self._adb()
        try:
            msg = src.pair(host, port, code)
        except ScreenUnavailable as e:
            raise ServiceError(str(e)) from None
        try:
            connected = src.connect()
        except ScreenUnavailable:
            connected = []
        return {"message": msg, "connected": connected}

    def watch_adb_connect(self, host: str = "", port: int = 0) -> dict[str, Any]:
        try:
            return {"connected": self._adb().connect(host, port)}
        except ScreenUnavailable as e:
            raise ServiceError(str(e)) from None

    def watch_adb_qr(self) -> dict[str, Any]:
        """安卓扫码配对：生成新的配对二维码（旧的作废），再用 watch_adb_qr_poll 等扫码。"""
        src = self._adb()
        try:
            src.qr_ready()
        except ScreenUnavailable as e:
            raise ServiceError(str(e)) from None
        name, password = new_qr_pairing()
        self._adb_qr = {"name": name, "password": password, "at": time.monotonic()}
        return {"payload": qr_pairing_payload(name, password), "expires_s": ADB_QR_TTL_S}

    def watch_adb_qr_poll(self) -> dict[str, Any]:
        """看手机扫了没有，扫了就配对并连上。state：waiting / connected / paired / expired / none"""
        qr = self._adb_qr
        if qr is None:
            return {"state": "none", "connected": []}
        if time.monotonic() - qr["at"] > ADB_QR_TTL_S:
            self._adb_qr = None
            return {"state": "expired", "connected": []}
        src = self._adb()
        try:
            addr = src.qr_pair(qr["name"], qr["password"])
        except ScreenUnavailable as e:
            self._adb_qr = None
            raise ServiceError(str(e)) from None
        if addr is None:
            return {"state": "waiting", "connected": []}
        self._adb_qr = None
        host = addr.rsplit(":", 1)[0]
        connected: list[str] = []
        for _ in range(3):  # 刚配对完手机要一两秒才广播连接端口
            try:
                connected = src.connect(host, 0)
                break
            except ScreenUnavailable:
                time.sleep(1.5)
        return {"state": "connected" if connected else "paired", "connected": connected}

    async def watch_start(self, source: str, watchers: list[str], consent: bool) -> dict[str, Any]:
        devices = await asyncio.to_thread(self.watch.devices)
        infos = {s["id"]: s for d in devices.values() for s in d["sources"]}
        if source not in infos:
            raise ServiceError("找不到这个画面来源（刷新一下列表）")
        try:
            self.watch.start(source, infos[source]["title"], watchers, consent=consent)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        task = asyncio.get_running_loop().create_task(self.watch.run())
        self.watch.attach(task)
        self._chat_tasks.add(task)
        task.add_done_callback(self._chat_tasks.discard)
        return self.watch.status()

    def watch_stop(self) -> dict[str, Any]:
        self.watch.stop()
        return self.watch.status()

    def watch_quiet(self, quiet: bool) -> dict[str, Any]:
        self.watch.set_quiet(quiet)
        return self.watch.status()

    def watch_frame(self) -> bytes:
        data = self.watch.preview()
        if data is None:
            raise ServiceError("现在没有画面", 404)
        return data

    def watch_messages(self, after: int = 0) -> dict[str, Any]:
        return {"messages": self.watch.messages(after), "typing": list(self.watch.typing)}

    async def watch_say(self, text: str) -> dict[str, Any]:
        try:
            msg, speakers = self.watch.post(text)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        if speakers:
            self._background(self.watch.replies(msg["id"], speakers))
        return {"id": msg["id"], "speakers": [m.name for m in speakers]}

    def watch_scrcpy(self, source: str) -> dict[str, Any]:
        """用 scrcpy 打开安卓设备的流畅投屏窗口（用户自己的工具，圆桌不读它的画面）。"""
        exe = shutil.which(self.rt.config.roundtable.chat.watch.scrcpy)
        if exe is None:
            raise ServiceError("没有找到 scrcpy（可选：装好并加入 PATH 后才能开流畅窗口）")
        serial = source.removeprefix("android:")
        if not source.startswith("android:") or not re.fullmatch(r"[A-Za-z0-9._:\-]{1,64}", serial):
            raise ServiceError("只有安卓设备能开 scrcpy 窗口")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen([exe, "-s", serial], creationflags=flags)  # noqa: S603
        except OSError:
            raise ServiceError("scrcpy 启动失败") from None
        return {"started": True}

    def watch_overlay(self, port: int) -> dict[str, Any]:
        """打开一起看的置顶悬浮窗（另一个进程，tkinter；只通过 HTTP 连本机圆桌，docs/WATCH.md）。"""
        import sys

        if not self.watch.running:
            raise ServiceError("现在没在投屏")
        proc = getattr(self, "_overlay", None)
        if proc is not None and proc.poll() is None:
            return {"started": False, "already": True}
        if not _has_tk():
            raise ServiceError("这台电脑的 Python 没带 tkinter，开不了悬浮窗；可以用「画中画」小窗")
        state = data_dir() / "desktop" / "watch_overlay.json"
        cmd = [
            sys.executable,
            "-m",
            "roundtable.desktop.overlay",
            "--url",
            f"http://127.0.0.1:{int(port)}",
            "--state",
            str(state),
        ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._overlay = subprocess.Popen(cmd, creationflags=flags)  # noqa: S603
        except OSError:
            raise ServiceError("悬浮窗启动失败") from None
        return {"started": True, "already": False}

    # --- 成员自己上网 ---------------------------------------------------------------------

    def wander_status(self) -> dict[str, Any]:
        return self.wander.status()

    def wander_start(self, member: str, site: str | None = None) -> dict[str, Any]:
        try:
            self.wander.start(member, site or None)
        except ValueError as e:
            raise ServiceError(str(e)) from None
        return self.wander.status()

    def wander_stop(self) -> dict[str, Any]:
        self.wander.stop()
        return self.wander.status()

    def wander_log(self, member: str, after: int = 0) -> list[dict[str, Any]]:
        if member not in {m.id for m in self.chats.everyone()}:
            raise ServiceError("没有这位成员")
        return self.wander.log(member, after)

    def wander_frame(self) -> bytes | None:
        return self.wander.frame()

    # --- dsh 工作台（docs/DSH.md）：可拔掉的外设 ---------------------------------------

    NO_DSH = "这份圆桌没有带 dsh 工作台（core/dsh 不在），其他功能不受影响"

    def dsh_status(self) -> dict[str, Any]:
        if self.dsh is None:
            return {
                "enabled": False,
                "installed": False,
                "unavailable": self.NO_DSH,
                "state": "stopped",
            }
        return self.dsh.status()

    async def dsh_start(self) -> dict[str, Any]:
        if self.dsh is None:
            raise ServiceError(self.NO_DSH, 409)
        try:
            return await self.dsh.start()
        except ValueError as exc:
            raise ServiceError(str(exc), 409) from None

    async def dsh_stop(self) -> dict[str, Any]:
        if self.dsh is None:
            return self.dsh_status()
        return await self.dsh.stop()

    async def dsh_check(self) -> dict[str, Any]:
        if self.dsh is None:
            return self.dsh_status()
        return await self.dsh.check_now()

    # --- 超级电脑（docs/SUPERCOMPUTER.md） ----------------------------------------------

    def super_status(self) -> dict[str, Any]:
        return self.supercomputer.status()

    def super_submit(self, text: str) -> dict[str, Any]:
        try:
            self.supercomputer.submit(text)
        except RuntimeError as exc:
            raise ServiceError(str(exc), 409) from None
        except ValueError as exc:
            raise ServiceError(str(exc), 400) from None
        return self.super_status()

    async def super_cancel(self) -> dict[str, Any]:
        await self.supercomputer.cancel()
        return self.super_status()

    def super_task(self, task_id: str) -> dict[str, Any]:
        task = self.supercomputer.get(task_id)
        if task is None:
            raise ServiceError("没有这件活", 404)
        return task

    def super_set_limit(self, worker: str, limit: int) -> dict[str, Any]:
        try:
            self.supercomputer.set_limit(worker, limit)
        except ValueError as exc:
            raise ServiceError(str(exc), 400) from None
        return self.super_status()

    def super_clear_cooldown(self, worker: str) -> dict[str, Any]:
        try:
            self.supercomputer.clear_cooldown(worker)
        except ValueError as exc:
            raise ServiceError(str(exc), 400) from None
        return self.super_status()

    # --- 工作台生成器（docs/KIT.md） --------------------------------------------------

    def bench_status(self) -> dict[str, Any]:
        return self.bench.status()

    def bench_submit(self, request: str) -> dict[str, Any]:
        try:
            self.bench.submit(request)
        except RuntimeError as exc:
            raise ServiceError(str(exc), 409) from None
        except ValueError as exc:
            raise ServiceError(str(exc), 400) from None
        return self.bench_status()

    async def bench_cancel(self) -> dict[str, Any]:
        await self.bench.cancel()
        return self.bench_status()

    def bench_get(self, bench_id: str) -> dict[str, Any]:
        b = self.bench.get(bench_id)
        if b is None:
            raise ServiceError("没有这个工作台", 404)
        return b

    async def bench_ask(self, bench_id: str, question: str, json: bool = False) -> dict[str, Any]:
        try:
            if json:
                return await self.bench.ask_json(bench_id, question)
            return {"text": await self.bench.ask(bench_id, question)}
        except LookupError as exc:
            raise ServiceError(str(exc), 404) from None
        except ValueError as exc:
            raise ServiceError(str(exc), 400) from None

    def bench_net_origins(self) -> list[str]:
        """生成的工作台能联网访问的网址（配置里的公开数据名单）；沙箱只放行这些。"""
        out: list[str] = []
        for svc in self.bench.rules.net:
            out += [o for o in svc.origins if o not in out]
        return out

    def bench_file(self, bench_id: str, path: str) -> Path:
        """生成的工作台里的一个文件（只读；不许跳出它自己的文件夹）。"""
        if self.bench.root is None or self.bench.get(bench_id) is None:
            raise ServiceError("没有这个工作台", 404)
        base = self.bench.folder(bench_id).resolve()
        full = (base / (path or "index.html")).resolve()
        if not full.is_relative_to(base) or not full.is_file() or full.name == "build.json":
            raise ServiceError("没有这个文件", 404)
        return full

    def start_dsh(self) -> None:
        """服务启动时：配置了 autostart 才顺带启动 dsh（默认不启动）。失败只记日志。"""
        if self.dsh is None or not self.rt.config.roundtable.dsh.autostart:
            return

        async def boot() -> None:
            try:
                await self.dsh.start()
            except Exception as exc:  # noqa: BLE001 - dsh 起不来不影响圆桌
                log.warning("dsh 工作台自动启动失败：%s", exc)

        self._background(boot())

    def _covers_dir(self) -> Path | None:
        sf = self.rt.standee_file
        return sf.parent / COVERS_DIR if sf is not None else None

    def music_covers(self) -> dict[str, Any]:
        """本机 data/avatars/music_covers/ 里可选作封面的图片（成员侧脸等，不进 git）。"""
        return {"covers": list_covers(self._covers_dir())}

    def music_cover(self, name: str) -> tuple[bytes, str]:
        found = read_cover(self._covers_dir(), name)
        if found is None:
            raise ServiceError("文件不存在", 404)
        return found

    def standee(self) -> dict[str, Any]:
        """终端首页大立绘：各套立绘与表情差分的地址、台词。只读本机文件，不调用模型。

        清单写了 persona 时，这位成员人设里的口头禅也加进点击台词（网页里改过的优先）。
        """
        st = load_standee(self.rt.standee_file)
        if not st.available:
            return {"available": False, "reason": st.reason}

        def url(rel: str) -> str:
            return f"/api/standee/files/{quote(rel)}"

        lines = {k: list(v) for k, v in st.lines.items()}
        if st.persona:
            tap = lines.get("tap", [])
            extra = [c.strip() for c in persona_of(self.rt, st.persona).catchphrases if c.strip()]
            lines["tap"] = tap + [c for c in extra if c not in tap]
        return {
            "available": True,
            "reason": None,
            "name": st.name,
            "variants": [
                {
                    "id": v.id,
                    "label": v.label,
                    "image": url(v.image),
                    "head": list(v.head) if v.head else None,
                    "cutout": v.cutout,
                    "expressions": {
                        k: {"image": url(e["image"]), "box": e["box"]}
                        for k, e in v.expressions.items()
                    },
                    "missing": list(v.missing),
                    "rig": (
                        {
                            **v.rig,
                            "base": url(v.rig["base"]),
                            "iris": url(v.rig["iris"]),
                            "weights": [url(w) for w in v.rig["weights"]],
                        }
                        if v.rig
                        else None
                    ),
                }
                for v in st.variants
            ],
            "lines": lines,
        }

    def standee_file(self, path: str) -> tuple[bytes, str]:
        """立绘图片：只允许清单所在目录内的 png / jpg / webp。返回 (内容, MIME)。"""
        st = load_standee(self.rt.standee_file)
        found = read_standee_file(st, path) if st.root is not None else None
        if found is None:
            raise ServiceError("文件不存在", 404)
        return found

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

    # --- 余额与用量（只读：本机账本 + 渠道账户余额，不改预算、不上传） -----------------------

    USAGE_RANGES = ("day", "week", "month", "all")
    USAGE_DAYS = 30  # 按天图表的天数
    BALANCE_TTL_S = 60  # 账户余额缓存：面板反复打开不会一直去查

    def _usage_window(self, span: str) -> tuple[datetime | None, datetime | None]:
        now = self.rt.budget.now()
        if span == "day":
            return day_window(now)
        if span == "week":  # 本周：UTC 周一 0 点起
            start, _ = day_window(now)
            start -= timedelta(days=start.weekday())
            return start, start + timedelta(days=7)
        if span == "month":
            return month_window(now)
        return None, None

    def _prices(self) -> list[usage_report.PriceRow]:
        cfg = self.rt.config
        return [
            usage_report.PriceRow(
                m.id, display_name(m.id, cfg), m.tier, m.price.input, m.price.output
            )
            for m in cfg.models.enabled
            if m.seat and m.media_price is None and (m.price.input or m.price.output)
        ]

    def _tier_label(self, tier: str) -> str:
        cfg = self.rt.config
        plan = next((p for p in cfg.routing.plans.values() if p.tiers == [tier]), None)
        return plan.label if plan else cfg.personas.tier_labels.get(tier, tier)

    def usage(self, span: str = "month") -> dict[str, Any]:
        """本机账本的详细用量：总数、按天、按功能、按成员、按渠道，以及钱折算成 token 的对照。

        匿名开启且未揭晓的会话只计入总数 / 按天 / 按功能，按成员时归入「匿名进行中」。
        """
        if span not in self.USAGE_RANGES:
            raise ServiceError(f"range 应为 {' / '.join(self.USAGE_RANGES)}")
        cfg = self.rt.config
        meetings = [m.session_id for m in self._meetings()]
        start, end = self._usage_window(span)
        report = usage_report.summarize(self.rt.repo.usage_rows(start, end, meetings), LIFE_STEPS)
        today, _ = day_window(self.rt.budget.now())
        first = today - timedelta(days=self.USAGE_DAYS - 1)
        days = usage_report.daily(
            self.rt.repo.usage_rows(first, today + timedelta(days=1), meetings),
            today.date(),
            self.USAGE_DAYS,
            LIFE_STEPS,
        )
        everything = usage_report.summarize(self.rt.repo.usage_rows(None, None), LIFE_STEPS)
        share = usage_report.output_share(everything.totals)
        budget = self.budget()
        month = budget["month"]
        remaining = max(0.0, month["limit_usd"] - month["spent_usd"]) if month else 0.0
        prices = self._prices()
        tiers = usage_report.tier_averages(prices, self._tier_label)
        specs = {m.id: m for m in cfg.models.models}
        return {
            "range": span,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "budget": budget,
            "totals": report.totals.to_dict(),
            "by_feature": [
                {"key": k, "label": usage_report.FEATURES[k], **t.to_dict()}
                for k, t in report.by_feature.items()
            ],
            "by_member": [
                {
                    "model_id": mid,
                    "name": display_name(mid, cfg),
                    "tier": specs[mid].tier if mid in specs else None,
                    **t.to_dict(),
                }
                for mid, t in report.by_model.items()
            ],
            "hidden": report.hidden.to_dict() if report.hidden.calls else None,
            "by_channel": {k: t.to_dict() for k, t in report.by_channel.items()},
            "daily": days,
            "features": usage_report.FEATURES,
            # 钱 → token：按本月预算剩余折算（有账户余额时前端可换成余额，用同一组价格）
            "output_share": share,
            "prices": [p.__dict__ for p in (*tiers, *prices)],
            "equivalents": {
                "basis": "budget_remaining",
                "amount_usd": remaining,
                "rows": usage_report.token_equivalents(remaining, (*tiers, *prices), share),
            },
            "outside": "「做这条」的自动开发用的是你电脑上的编程助手，不经过圆桌账本，不计入这里。",
        }

    async def account_balance(self, *, refresh: bool = False) -> dict[str, Any]:
        """配置了 balance 的渠道：账户余额与 key 的已用（实时查，缓存一分钟），并和本机账本对账。

        只返回金额；key 永远不出现在结果里。查不到时 ok 为假，附脱敏后的原因，面板改用本机估算。
        """
        now = self.rt.budget.now()
        cached = getattr(self, "_balance_cache", None)
        if cached and not refresh and (now - cached[0]).total_seconds() < self.BALANCE_TTL_S:
            return cached[1]
        start, end = month_window(now)
        ledger = usage_report.summarize(self.rt.repo.usage_rows(start, end), LIFE_STEPS)
        today = usage_report.summarize(self.rt.repo.usage_rows(*day_window(now)), LIFE_STEPS)
        out = []
        for name, spec in self.rt.config.models.channels.items():
            if spec.balance is None:
                continue
            row: dict[str, Any] = {"channel": name, "kind": spec.kind, "ok": False}
            ledger_month = ledger.by_channel.get(name, usage_report.Totals()).cost_usd
            ledger_day = today.by_channel.get(name, usage_report.Totals()).cost_usd
            row["ledger_month_usd"] = round(ledger_month, 6)
            row["ledger_day_usd"] = round(ledger_day, 6)
            if name in self.rt.unavailable_channels:
                row["error"] = self.rt.unavailable_channels[name]
                out.append(row)
                continue
            try:
                bal = await self.rt.router.provider(name).account_balance()
            except (ProviderError, UnsupportedCapability, KeyError) as exc:
                row["error"] = "渠道未启用" if isinstance(exc, KeyError) else redact(str(exc))
                out.append(row)
                continue
            row.update(ok=True, **{k: v for k, v in asdict(bal).items() if k != "notes"})
            row["remaining"] = bal.remaining
            row["notes"] = list(bal.notes)
            if bal.usage_monthly is not None:
                # 渠道报的本月已用 − 本机账本记的本月已用：正数多半是别的程序也在用这个 key
                row["diff_month_usd"] = round(bal.usage_monthly - ledger_month, 6)
            out.append(row)
        result = {"fetched_at": now.isoformat(), "channels": out}
        self._balance_cache = (now, result)
        return result

    # --- 渠道优先顺序（同一成员有多条路时先走哪条；不影响谁上桌） ---------------------------

    def channel_priority(self) -> dict[str, Any]:
        cfg = self.rt.config
        prio = self.rt.router.priority
        spent = MonthSpend(self.rt.repo, self.rt.budget.now, ttl_s=0).all()
        names = list(cfg.models.channels)
        if prio.strategy == "custom":
            names = [*prio.order, *(n for n in names if n not in prio.order)]
        rows = []
        for name in names:
            spec = cfg.models.channels[name]
            credit = prio.credits.get(name)
            used = spent.get(name, 0.0)
            left = None if credit is None else round(max(0.0, credit - used), 6)
            members = [
                display_name(m.id, cfg)
                for m in cfg.models.enabled
                if m.seat and any(r.channel == name for r in m.routes)
            ]
            rows.append(
                {
                    "channel": name,
                    "kind": spec.kind,
                    "available": name not in self.rt.unavailable_channels,
                    "reason": self.rt.unavailable_channels.get(name),
                    "credit_usd": credit,
                    "spent_month_usd": round(used, 6),
                    "credit_left_usd": left,
                    "members": list(dict.fromkeys(members)),
                }
            )
        return {"strategy": prio.strategy, "channels": rows}

    def set_channel_priority(
        self,
        *,
        strategy: str,
        order: list[str] | None = None,
        credits: dict[str, float | None] | None = None,
    ) -> dict[str, Any]:
        cfg = self.rt.config
        prio = self.rt.router.priority
        current: dict[str, float | None] = {n: prio.credits.get(n) for n in cfg.models.channels}
        try:
            saved = save_priority(
                cfg,
                self.rt.repo,
                strategy=strategy,
                order=order if order is not None else list(prio.order),
                credits={**current, **(credits or {})},
            )
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        self.rt.router.set_priority(saved)
        return self.channel_priority()

    # --- 插件榜 ------------------------------------------------------------------

    def plugin_board(self) -> dict[str, Any]:
        """自我学习革新调研过的插件 / 项目榜单（只读；每次重新读 config/plugin_board.yaml）。"""
        try:
            board = load_board(self.rt.plugin_board)
        except ConfigError as exc:
            raise ServiceError(str(exc), 500) from None
        return board_view(board)

    # --- 公告栏 ------------------------------------------------------------------

    def changelog(self) -> dict[str, Any]:
        """版本更新说明（只读；每次重新读 config/changelog.yaml）。"""
        try:
            log = load_changelog(self.rt.changelog)
        except ConfigError as exc:
            raise ServiceError(str(exc), 500) from None
        return changelog_view(log)

    # --- 终端邮件栏（用户本人学校邮箱，只读；docs/MAIL.md） --------------------------------

    def _mail_view(self) -> dict[str, Any]:
        return {**self.mail.view(), "ai": self.mail_ai.status()}

    def mail_view(self) -> dict[str, Any]:
        return self._mail_view()

    # --- 主时区 ----------------------------------------------------------------------

    def home_tz(self) -> dict[str, Any]:
        """整个圆桌按哪个时区过日子（公寓、群聊、成员作息、日记）；``local`` = 跟随电脑。"""
        return home_zone(self.rt).view(self.rt.budget.now())

    def set_home_tz(self, zone: str) -> dict[str, Any]:
        try:
            home_zone(self.rt).set(zone)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        return self.home_tz()

    def mail_settings(self, *, ai_ok: bool | None = None, **changes: Any) -> dict[str, Any]:
        try:
            self.mail.set_settings(**{k: v for k, v in changes.items() if v is not None})
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        if ai_ok is not None:
            self.mail_ai.set_consent(ai_ok)
        return self._mail_view()

    def _mail_do(self, fn: Any, *args: Any, **kw: Any) -> Any:
        try:
            return fn(*args, **kw)
        except MailBusy as exc:
            raise ServiceError(str(exc), 409) from None
        except ValueError as exc:
            raise ServiceError(str(exc)) from None

    def mail_login(self) -> dict[str, Any]:
        """弹出专用窗口，由用户本人登录（圆桌不代填、不保存密码）。"""
        self._mail_do(self.mail.login)
        return self._mail_view()

    def mail_refresh(self, *, manual: bool) -> dict[str, Any]:
        started = self._mail_do(self.mail.refresh, manual=manual)
        return {**self._mail_view(), "started": started}

    def mail_message(self, message_id: str) -> dict[str, Any]:
        m = self.mail.message(message_id)
        if m is None:
            raise ServiceError("没有这封邮件（可能已超出保留天数）", 404)
        return m

    def mail_reminder(self, reminder_id: int, action: str) -> dict[str, Any]:
        if not self._mail_do(self.mail.set_reminder, reminder_id, action):
            raise ServiceError("没有这条提醒", 404)
        return self._mail_view()

    def mail_logout(self) -> dict[str, Any]:
        self._mail_do(self.mail.logout)
        self.mail_ai.set_consent(False)  # 退出 = 清除全部，包括「同意发给 AI」
        return self._mail_view()

    def mail_clear(self) -> dict[str, Any]:
        self.mail.clear_cache()
        return self._mail_view()

    def mail_mark(self, message_id: str, mark: str | None) -> dict[str, Any]:
        """手动标重要 / 不重要（None 交回规则）。只记在本机，不写回邮箱。"""
        if not self._mail_do(self.mail.set_mark, message_id, mark):
            raise ServiceError("没有这封邮件（可能已超出保留天数）", 404)
        return self._mail_view()

    def mail_category(self, message_id: str, category: str | None) -> dict[str, Any]:
        if not self._mail_do(self.mail.set_category, message_id, category):
            raise ServiceError("没有这封邮件（可能已超出保留天数）", 404)
        return self._mail_view()

    def mail_images(self, message_id: str) -> dict[str, Any]:
        """在后台取这封信的图文版正文（含邮件自带的图片）。"""
        self._mail_do(self.mail.load_images, message_id)
        return self.mail_message(message_id)

    async def mail_ai_do(self, kind: str, message_id: str | None, *, again: bool) -> dict:
        """AI 总结 / 翻译 / 重要邮件汇总：只在用户点了以后调用，有每月和每天上限。"""
        try:
            if kind == "digest":
                await self.mail_ai.digest()
                return self._mail_view()
            if not message_id:
                raise ServiceError("缺少邮件 id")
            fn = self.mail_ai.summarize if kind == "summary" else self.mail_ai.translate
            await fn(message_id, again=again)
        except MailAIError as exc:
            raise ServiceError(str(exc), 409) from None
        return {**self.mail_message(message_id), "ai": self.mail_ai.status()}

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

    def _chat_room(self, room: str) -> Any:
        try:
            return self.chats.room(room or ROOM)
        except KeyError:
            raise ServiceError(f"没有这个聊天：{room}", 404) from None

    def chat_rooms(self) -> dict[str, Any]:
        """会话列表（群、私聊，带未读数和最后一条消息）与聊天本月预算。"""
        budget = chat_budget(self.rt)
        return {
            "rooms": self.chats.rooms(),
            "members": [asdict(m) for m in self.chats.everyone()],
            "typing": self.chats.typing(),
            "queued": self.chats.queued(),
            "budget": {"spent_usd": budget.spent_usd, "limit_usd": budget.limit_usd},
            "moments_unseen": self.rt.repo.unseen_moments(
                int(self.rt.repo.setting(MOMENTS_SEEN) or 0)
            ),
            "life": {
                "enabled": self.rt.config.roundtable.chat.life.enabled,
                "spent_today_usd": self.life.spent_today(),
                "daily_usd": self.rt.config.roundtable.chat.life.daily_usd,
                "messages_today": self.life.guard.messages_today(),
                "daily_messages": self.rt.config.roundtable.chat.life.guard.daily_messages,
                "halted": self.life.guard.halted(),
            },
        }

    def chat_view(
        self, after: int = 0, query: str | None = None, room: str = ROOM
    ) -> dict[str, Any]:
        """会话成员、消息（id 大于 after 的；有 query 时为搜索结果）与正在输入的成员。

        不是搜索时把会话标为已读。
        """
        chat = self._chat_room(room)
        everyone = chat.everyone()
        names = {m.id: m.name for m in everyone} | {"user": "我", "system": "系统"}
        rows = self.rt.repo.chat_messages(chat.room, after=after, query=query or None)
        if rows and not query:
            self.rt.repo.mark_chat_read(chat.room, rows[-1]["id"])
        return {
            "room": {"id": chat.room, "kind": chat.kind, "title": chat.title},
            "members": [asdict(m) for m in chat.members()],
            "messages": [
                {
                    "id": r["id"],
                    "author": r["author"],
                    "name": names.get(r["author"], r["author"]),
                    "text": r["text"],
                    "reply_to": r["reply_to"],
                    "cost_usd": r["cost_usd"],
                    "meta": r["meta"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
            "typing": list(chat.typing),
            "queued": list(chat.queued),
            "recall_seconds": self.rt.config.roundtable.chat.actions.recall_seconds,
        }

    # --- 聊天 / 朋友圈里的图片、视频、语音（只存本机） ------------------------------

    @property
    def chat_media(self) -> ChatMediaStore:
        return ChatMediaStore(self.rt.chat_media_dir)

    def chat_media_save(self, data: bytes, mime: str) -> dict[str, Any]:
        """存一个用户发的图片 / 视频 / 语音，返回 id 和种类；发消息时把 id 带上。"""
        try:
            saved = self.chat_media.save(data, mime)
        except MediaError as exc:
            raise ServiceError(str(exc), 413 if "太大" in str(exc) else 400) from None
        return {"id": saved.id, "kind": saved.kind, "mime": saved.mime, "size": saved.size}

    def chat_media_file(self, name: str) -> tuple[bytes, str]:
        found = self.chat_media.load(name)
        if found is None:
            raise ServiceError("文件不存在", 404)
        return found

    def _media_meta(self, text: str, media: list[dict] | None) -> tuple[str, dict | None]:
        """检查附件；没有文字时用「[图片]」之类的占位当作给成员看的内容。"""
        try:
            items = clean_items(self.chat_media, media)
        except MediaError as exc:
            raise ServiceError(str(exc)) from None
        if not items:
            return text, None
        meta: dict[str, Any] = {"media": items}
        tag = placeholder(items)
        if not text:
            meta["auto_text"] = True
            return tag, meta
        return f"{text}\n{tag}", meta | {"caption": text}

    async def chat_post(
        self,
        text: str,
        room: str = ROOM,
        media: list[dict] | None = None,
        quote: int | None = None,
    ) -> dict[str, Any]:
        """用户发一条消息（可带图片 / 视频 / 语音，可引用一条消息）；被选中的成员在后台依次回应。"""
        chat = self._chat_room(room)
        if chat.kind == "pair":
            raise ServiceError("这是成员之间的私聊，你只能看，不能发言")
        text = (text or "").strip()
        if len(text) > MAX_CHAT_CHARS:
            raise ServiceError(f"消息太长（最多 {MAX_CHAT_CHARS} 字）")
        text, meta = self._media_meta(text, media)
        if not text:
            raise ServiceError("消息不能为空")
        if meta is None and is_bare_url(text):  # 只发了一个链接：显示成卡片
            hosts = self.rt.config.roundtable.chat.actions.video_hosts
            card = card_from_url(text, hosts, self.rt.repo.web_item_by_url(text))
            text, meta = chat_card_text(card), {"card": card}
        try:
            msg, speakers = chat.post(text, meta, quote)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        self.rt.repo.mark_chat_read(chat.room, msg["id"])
        with contextlib.suppress(Exception):  # 状态出错不影响聊天
            self.engine.observe(self.rt.budget.now())  # 被 @、被批评的反射在回应前算进状态
        mode = self.perceiver.mode_for(text) if meta else None
        if mode:
            self._background(self._look_and_reply(chat, msg["id"], speakers, mode, chat.round))
        else:
            self._chat_replies(chat, msg["id"], speakers)
        return {"id": msg["id"], "speakers": [m.name for m in speakers], "look": mode}

    async def _look_and_reply(
        self, chat: Any, message_id: int, speakers: list[Any], mode: str, round_no: int
    ) -> None:
        """消息带图片 / 视频 / 语音时，先由感官整理成感知卡（成员读卡），再依次回应。"""
        with contextlib.suppress(Exception):  # 没看成也照常回应，成员只看到占位
            await self._perceive_message(chat.room, message_id, mode)
        if speakers:  # 用户在感官看的时候又发了消息或撤回：按策略作废这一轮
            await chat.replies(message_id, speakers, round_no)

    def _sense_items(self, meta: dict[str, Any]) -> list[SenseItem]:
        items = []
        for it in meta.get("media") or []:
            found = self.chat_media.load(it["id"])
            if found is not None:
                items.append(SenseItem(it["id"], it["kind"], found[0], found[1]))
        return items

    async def _perceive_message(self, room: str, message_id: int, mode: str) -> list[dict]:
        row = self.rt.repo.chat_message(message_id)
        if row is None or row["room"] != room:
            return []
        meta = dict(row["meta"] or {})
        items = self._sense_items(meta)
        if not items:
            return []
        chat = self._chat_room(room)
        chat.typing.append("感官")
        chat.on_change()
        try:
            cards = await self.perceiver.perceive(room, items, mode)
        finally:
            chat.typing.remove("感官")
            chat.on_change()
        if cards:
            meta["perception"] = [
                {"id": c.id, "kind": c.kind, "mode": c.mode, "text": c.text} for c in cards
            ]
            self.rt.repo.set_chat_message_meta(message_id, meta)
            chat.on_change()
        return meta.get("perception", [])

    async def chat_look(self, room: str, message_id: int) -> dict[str, Any]:
        """「细看」：把一条带图片 / 视频 / 语音的消息按精细模式重新整理，再请一位成员看过后回应。

        转写稿和缓存复用；已经是精细的直接用缓存（不花钱）。
        """
        chat = self._chat_room(room)
        row = self.rt.repo.chat_message(message_id)
        if row is None or row["room"] != chat.room or row["author"] != "user":
            raise ServiceError("消息不存在", 404)
        if not (row["meta"] or {}).get("media"):
            raise ServiceError("这条消息没有图片、视频或语音")
        if not self.rt.config.roundtable.perception.enabled:
            raise ServiceError("感官已关闭（perception.enabled）")
        cards = await self._perceive_message(chat.room, message_id, "fine")
        if not any(c["mode"] == "fine" for c in cards):
            raise ServiceError("这次没能细看（没有可用的看图模型，或本月感官额度已用完）")
        replied = [
            r["author"]
            for r in self.rt.repo.chat_messages(chat.room, after=message_id, limit=50)
            if r["reply_to"] == message_id and r["author"] not in ("user", "system")
        ]
        online = [m for m in chat.members() if m.available]
        speakers = [m for m in online if m.id in replied][:1] or chat.pick(row["text"])[:1]
        self._chat_replies(chat, message_id, speakers)
        return {"id": message_id, "speakers": [m.name for m in speakers]}

    def _chat_replies(self, chat: Any, reply_to: int, speakers: list[Any]) -> None:
        if not speakers:
            return
        coro = chat.replies(reply_to, speakers, chat.round)
        task = asyncio.get_running_loop().create_task(coro)
        self._chat_tasks.add(task)
        task.add_done_callback(self._chat_tasks.discard)

    async def chat_pat(self, member: str, room: str = ROOM) -> dict[str, Any]:
        """拍一拍会话里的一位成员：发 chat.pat 事件给成员系统，按策略（默认回一句）让 TA 回应。"""
        chat = self._chat_room(room)
        if chat.kind == "pair":
            raise ServiceError("这是成员之间的私聊，你只能看")
        try:
            msg = chat.pat(member)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        self.rt.repo.mark_chat_read(chat.room, msg["id"])
        now = self.rt.budget.now()
        self.engine.bus.publish(
            MemberEvent(
                "chat.pat", now, member, {"room": chat.room, "id": msg["id"], "actor": "user"}
            )
        )
        target = next((m for m in chat.members() if m.id == member and m.available), None)
        speakers = []
        if target is not None and self.chats.policy.reply_to_pat(member, chat.kind):
            speakers = [target]
            chat.round += 1  # 拍一拍也算新的一轮：上一轮还没发出的按策略作废
        self._chat_replies(chat, msg["id"], speakers)
        return {"id": msg["id"], "text": msg["text"], "speakers": [m.name for m in speakers]}

    async def chat_forward(self, message_id: int, room: str) -> dict[str, Any]:
        """把一条消息（文字、图片、表情包、卡片）原样转发到另一个会话，以你的身份发出；
        那边的成员照常回应。"""
        row = self.rt.repo.chat_message(message_id)
        if row is None or row["author"] == "system" or (row["meta"] or {}).get("recalled"):
            raise ServiceError("这条消息不能转发")
        src = self._chat_room(row["room"])
        chat = self._chat_room(room)
        if chat.kind == "pair":
            raise ServiceError("这是成员之间的私聊，你只能看")
        meta = {k: v for k, v in (row["meta"] or {}).items() if k in FORWARD_KEYS}
        names = {m.id: m.name for m in src.everyone()} | {"user": "我"}
        meta["forwarded"] = {"from": names.get(row["author"], row["author"]), "room": src.title}
        msg, speakers = chat.post(row["text"], meta)
        self.rt.repo.mark_chat_read(chat.room, msg["id"])
        self._chat_replies(chat, msg["id"], speakers)
        return {"id": msg["id"], "speakers": [m.name for m in speakers]}

    def chat_recall(self, message_id: int, room: str = ROOM) -> dict[str, Any]:
        """撤回你自己发的一条消息（发出后几分钟内）。"""
        chat = self._chat_room(room)
        try:
            row = chat.recall(message_id, self.rt.budget.now())
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        return {"id": row["id"], "recalled": True}

    def chat_handoff(self, text: str) -> dict[str, Any]:
        """在工作群里交办：按办公室模式（默认档位）开一件事，工作群里记下这条交办。"""
        text = (text or "").strip()
        if not text:
            raise ServiceError("要交办的事不能为空")
        sid = self.create(text, workflow="office")
        msg = post_handoff(self.rt, sid, text)
        self.rt.repo.mark_chat_read(WORK, msg["id"])
        return {"id": msg["id"], "session_id": sid}

    # --- 成员设定与记忆（只用于聊天） ------------------------------------------------

    def _chat_member(self, member: str) -> Any:
        found = next((m for m in self.chats.everyone() if m.id == member), None)
        if found is None:
            raise ServiceError(f"没有这位成员：{member}", 404)
        return found

    def member_voices(self) -> dict[str, Any]:
        """每位成员的固定音色：现在会用哪个音色（不含模型名，匿名无关：聊天不匿名）。"""
        out = []
        for m in self.chats.everyone():
            pick = self.voices.model_for(m.id, SAMPLE_TEXT)
            out.append(
                {
                    "id": m.id,
                    "name": m.name,
                    "available": pick is not None,
                    "voice": pick[1] if pick else None,
                }
            )
        return {"members": out, "max_chars": VOICE_MAX_CHARS}

    async def member_voice(self, member: str, text: str | None = None) -> VoiceClip:
        """用成员的固定音色念一段文字（省略 text 时念一句试听）。花费计入创作输出的每月上限。"""
        found = self._chat_member(member)
        try:
            return await self.voices.speak(member, text or SAMPLE_TEXT.format(name=found.name))
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        except VoiceBudgetExceeded as exc:
            raise ServiceError(str(exc), 402) from None
        except VoiceUnavailable as exc:
            raise ServiceError(str(exc), 503) from None

    def member_stickers(self) -> dict[str, Any]:
        """每位有形象卡的成员的专属表情：画了没有、待审还是可以用（不含模型名）。"""
        names = {m.id: m.name for m in self.chats.everyone()}
        rules = self.rt.config.roundtable.media.stickers
        members = [
            {
                "id": member,
                "name": names.get(member, member),
                "stickers": self.stickers.listing(member),
            }
            for member in self.rt.config.personas.sticker_cards
        ]
        return {
            "members": members,
            "available": bool(self.stickers.draw_models()),
            "review": rules.review,
            "month_spent_usd": round(self.stickers.month_spent(), 4),
            "monthly_usd": rules.monthly_usd,
        }

    async def draw_sticker(self, member: str, name: str) -> dict[str, Any]:
        """画（或重画）成员的一个专属表情；花费计入表情包和创作输出的每月上限。"""
        try:
            return await self.stickers.draw(member, name)
        except StickerError as exc:
            raise ServiceError(str(exc), 404) from None
        except StickerBudgetExceeded as exc:
            raise ServiceError(str(exc), 402) from None
        except StickerUnavailable as exc:
            raise ServiceError(str(exc), 503) from None

    def review_sticker(self, member: str, name: str, status: str) -> dict[str, Any]:
        try:
            return self.stickers.review(member, name, status)
        except StickerError as exc:
            raise ServiceError(str(exc)) from None

    def sticker_file(self, member: str, name: str) -> bytes:
        data = self.stickers.file(member, name)
        if data is None:
            raise ServiceError("这个表情还没画", 404)
        return data

    def chat_personas(self) -> dict[str, Any]:
        """每位成员的聊天人设；source：local 网页里改过 / config 配置文件 / empty 没有设定。"""
        out = []
        for m in self.chats.everyone():
            persona = persona_of(self.rt, m.id)
            if self.rt.repo.member_persona(m.id) is not None:
                source = "local"
            elif not is_empty(persona):
                source = "config"
            else:
                source = "empty"
            out.append(
                {"id": m.id, "name": m.name, "persona": persona.model_dump(), "source": source}
            )
        return {"members": out}

    def chat_set_persona(self, member: str, data: dict[str, Any] | None) -> dict[str, Any]:
        self._chat_member(member)
        try:
            persona = set_persona(self.rt, member, data)
        except ValueError as exc:
            raise ServiceError(f"人设不合法：{exc}") from None
        return {"id": member, "persona": persona.model_dump()}

    def chat_memories(self, member: str | None = None) -> dict[str, Any]:
        names = {m.id: m.name for m in self.chats.everyone()}
        if member is not None:
            self._chat_member(member)
        rows = self.rt.repo.memories(member)
        return {
            "memories": [{**r, "name": names.get(r["member"], r["member"])} for r in rows],
            "layers": list(LAYERS),
        }

    def _memory_text(self, text: str) -> str:
        limit = self.rt.config.roundtable.chat.memory.entry_chars
        clean = clean_memory(text, limit)
        if not clean:
            raise ServiceError("记忆不能为空（含密码、证件号、银行卡等敏感内容的行不会保存）")
        return clean

    def chat_add_memory(self, member: str, text: str) -> dict[str, Any]:
        """用户替成员记一条（随手记，source = user）；也记成一件事实（不变淡）。"""
        self._chat_member(member)
        row = self.rt.repo.add_memory(member, "note", self._memory_text(text), source="user")
        self.memory.store.write(
            member,
            row["text"],
            day=self._local_now().date(),
            fact=True,
            importance=2,
            source="user",
            source_key=f"note:{row['id']}",
        )
        return row

    def chat_update_memory(self, memory_id: int, text: str) -> dict[str, Any]:
        old = self.rt.repo.memory(memory_id)
        if old is None:
            raise ServiceError("没有这条记忆", 404)
        row = self.rt.repo.update_memory(memory_id, self._memory_text(text)) or {}
        item = self.rt.repo.memory_item_by_key(old["member"], f"note:{memory_id}")
        if item is not None and row:
            self.memory.store.user_edit(item["id"], row["text"])
        return row

    def chat_delete_memory(self, memory_id: int) -> dict[str, Any]:
        old = self.rt.repo.memory(memory_id)
        if old is None or not self.rt.repo.delete_memory(memory_id):
            raise ServiceError("没有这条记忆", 404)
        item = self.rt.repo.memory_item_by_key(old["member"], f"note:{memory_id}")
        if item is not None:
            self.rt.repo.delete_memory_item(item["id"])
        return {"deleted": 1}

    def chat_clear_memories(self, member: str | None = None) -> dict[str, Any]:
        """一键清空：日记和分层记忆，加上记忆件、挂着的事、联想网、习惯、翻日记记录和训练数据。"""
        if member is not None:
            self._chat_member(member)
        n = self.rt.repo.clear_memories(member)
        return {"deleted": n, "cleared": self.rt.repo.clear_memory_p2(member)}

    # --- 记忆与联想（P2）：记忆件、挂着的事、习惯、翻日记 ----------------------------

    def chat_memory_items(
        self, member: str, q: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        """一位成员的记忆件（q 为空时最近的在前，否则按全文索引找）、挂着的事、习惯、统计。"""
        self._chat_member(member)
        repo = self.rt.repo
        if self.rt.config.roundtable.chat.memory.recall == "half_life":
            prepare_items(self.rt)
        limit = min(max(limit, 1), 500)
        if q:
            ids = repo.search_memory_items(member, query_terms(q), limit)
            items = sorted(repo.memory_items_by_ids(ids), key=lambda r: r["last_at"], reverse=True)
        else:
            items = repo.memory_items(member, current=False, limit=limit)
        digest = repo.memory_digest(member)
        return {
            "items": items,
            "hanging": repo.memory_hanging(member),
            "habits": digest["text"] if digest else "",
            "count": repo.count_memory_items(member),
            "links": repo.count_memory_links(member),
            "diary_reads_left": DiaryTool(self.rt).remaining(member),
            "domains": list(self.rt.config.roundtable.chat.memory.domains),
        }

    def chat_memory_item_history(self, item_id: int) -> dict[str, Any]:
        item = self.rt.repo.memory_item(item_id)
        if item is None:
            raise ServiceError("没有这件记忆", 404)
        return {"item": item, "revisions": self.rt.repo.memory_revisions(item_id)}

    def chat_update_memory_item(self, item_id: int, text: str) -> dict[str, Any]:
        if self.rt.repo.memory_item(item_id) is None:
            raise ServiceError("没有这件记忆", 404)
        return self.memory.store.user_edit(item_id, self._memory_text(text)) or {}

    def chat_delete_memory_item(self, item_id: int) -> dict[str, Any]:
        if not self.rt.repo.delete_memory_item(item_id):
            raise ServiceError("没有这件记忆", 404)
        return {"deleted": 1}

    def chat_drop_hanging(self, hanging_id: int) -> dict[str, Any]:
        if not self.rt.repo.close_memory_hanging(hanging_id, "dropped"):
            raise ServiceError("没有这件挂着的事", 404)
        return {"dropped": 1}

    def chat_read_diary(self, member: str, q: str) -> dict[str, Any]:
        """你在网页上翻成员的日记（不占成员每天翻日记的次数）。"""
        self._chat_member(member)
        return {"found": DiaryTool(self.rt).find(member, q)}

    async def chat_tidy_memories(self) -> dict[str, Any]:
        """马上整理一次记忆（到昨天为止还没整理的日子）。"""
        now = self._local_now()
        return {"written": await self.memory.catch_up(now)}

    # --- 朋友圈 --------------------------------------------------------------------

    def _background(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._chat_tasks.add(task)
        task.add_done_callback(self._chat_tasks.discard)

    def _moment_out(self, row: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
        def who(author: str) -> str:
            return names.get(author, author)

        by_id = {c["id"]: c for c in row["comments"]}
        return {
            "id": row["id"],
            "author": row["author"],
            "name": who(row["author"]),
            "text": row["text"],
            "meta": row["meta"],
            "offline": row["offline"],
            "created_at": row["created_at"],
            "likes": [{"id": w, "name": who(w)} for w in row["likes"]],
            "liked": "user" in row["likes"],
            "comments": [
                {
                    "id": c["id"],
                    "author": c["author"],
                    "name": who(c["author"]),
                    "text": c["text"],
                    "reply_to": c["reply_to"],
                    "reply_name": who(by_id[c["reply_to"]]["author"])
                    if c["reply_to"] in by_id
                    else None,
                    "offline": c["offline"],
                    "created_at": c["created_at"],
                }
                for c in row["comments"]
            ],
        }

    def _moment(self, moment_id: int) -> dict[str, Any]:
        row = self.rt.repo.moment(moment_id)
        if row is None:
            raise ServiceError("没有这条动态", 404)
        return row

    def moments_view(self, author: str | None = None, before: int | None = None) -> dict[str, Any]:
        """朋友圈（新的在前，每页 20 条）；author 只看某位成员（或 user）。看第一页时标为已读。"""
        if author and author != "user":
            self._chat_member(author)
        everyone = self.chats.everyone()
        names = {m.id: m.name for m in everyone} | {"user": "我"}
        rows = self.rt.repo.moments(author=author or None, before=before, limit=20)
        if not before and not author:
            self.rt.repo.set_setting(MOMENTS_SEEN, str(self.rt.repo.latest_moment_id()))
        return {
            "moments": [self._moment_out(r, names) for r in rows],
            "members": [asdict(m) for m in everyone],
            "enabled": self.rt.config.roundtable.chat.moments.enabled,
        }

    def _moment_text(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            raise ServiceError("内容不能为空")
        if len(text) > MAX_MOMENT_CHARS:
            raise ServiceError(f"内容太长（最多 {MAX_MOMENT_CHARS} 字）")
        return text

    async def moments_post(self, text: str, media: list[dict] | None = None) -> dict[str, Any]:
        """用户发一条动态（可带图片 / 视频 / 语音）；一两位成员在后台评论。"""
        text = (text or "").strip()
        if len(text) > MAX_MOMENT_CHARS:
            raise ServiceError(f"内容太长（最多 {MAX_MOMENT_CHARS} 字）")
        text, meta = self._media_meta(text, media)
        if meta is None and is_bare_url(text):  # 只发了一个链接：显示成转发卡片
            hosts = self.rt.config.roundtable.chat.actions.video_hosts
            card = card_from_url(text, hosts, self.rt.repo.web_item_by_url(text))
            text, meta = chat_card_text(card), {"card": card}
        row = self.rt.repo.add_moment("user", self._moment_text(text), meta=meta)
        mode = self.perceiver.mode_for(text) if meta else None
        if self.rt.config.roundtable.chat.moments.enabled or mode:
            self._background(self._look_and_comment(row["id"], mode))
        return {"id": row["id"], "look": mode}

    async def _look_and_comment(self, moment_id: int, mode: str | None) -> None:
        """动态带图片 / 视频 / 语音时，先由感官整理成感知卡，再请成员评论。"""
        if mode is not None:
            with contextlib.suppress(Exception):  # 没看成也照常评论，成员只看到占位
                await self._perceive_moment(moment_id, mode)
        if self.rt.config.roundtable.chat.moments.enabled:
            await self.life.moments.react_to_user_post(moment_id)

    async def _perceive_moment(self, moment_id: int, mode: str) -> None:
        row = self.rt.repo.moment(moment_id)
        if row is None:
            return
        meta = dict(row["meta"] or {})
        items = self._sense_items(meta)
        if not items:
            return
        cards = await self.perceiver.perceive(ROOM, items, mode)
        if cards:
            meta["perception"] = [
                {"id": c.id, "kind": c.kind, "mode": c.mode, "text": c.text} for c in cards
            ]
            self.rt.repo.set_moment_meta(moment_id, meta)

    def moments_like(self, moment_id: int) -> dict[str, Any]:
        """用户点赞 / 取消点赞。"""
        self._moment(moment_id)
        if not self.rt.repo.like_moment(moment_id, "user"):
            self.rt.repo.unlike_moment(moment_id, "user")
            return {"liked": False}
        return {"liked": True}

    async def moments_comment(
        self, moment_id: int, text: str, reply_to: int | None = None
    ) -> dict[str, Any]:
        """用户评论（或回复某条评论）；被评论 / 被回复的成员在后台回一句。"""
        self._moment(moment_id)
        text = self._moment_text(text)
        if reply_to is not None:
            parent = self.rt.repo.moment_comment(reply_to)
            if parent is None or parent["moment_id"] != moment_id:
                raise ServiceError("要回复的评论不在这条动态下")
        row = self.rt.repo.add_moment_comment(moment_id, "user", text, reply_to=reply_to)
        if self.rt.config.roundtable.chat.moments.enabled:
            self._background(self.life.moments.reply_to_user_comment(row["id"]))
        return {"id": row["id"]}

    def moments_delete(self, moment_id: int) -> dict[str, Any]:
        """删掉一条动态（连同点赞、评论）。都是本地数据，成员的也能删。"""
        if not self.rt.repo.delete_moment(moment_id):
            raise ServiceError("没有这条动态", 404)
        return {"deleted": 1}

    def moments_delete_comment(self, comment_id: int) -> dict[str, Any]:
        if not self.rt.repo.delete_moment_comment(comment_id):
            raise ServiceError("没有这条评论", 404)
        return {"deleted": 1}

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

    # --- 上网（订阅源） ------------------------------------------------------------

    # --- 成员引擎：自我档案（docs/MEMBER_ENGINE.md） ---------------------------------

    def _profile_member(self, member: str) -> str:
        if member not in {m.id for m in self.chats.everyone()}:
            raise ServiceError(f"没有这位成员：{member}")
        return member

    def member_profile(self, member: str) -> dict[str, Any]:
        """当前档案、版本号和修改记录（新的在前）。"""
        p = self.engine.profiles.current(self._profile_member(member))
        rules = self.rt.config.roundtable.chat.engine
        card = card_text(self.rt, member)
        return {
            "member": member,
            "version": p.version,
            "data": p.data,
            "locked": p.locked,
            "sections": dict(rules.profile.sections),
            "history": self.engine.profiles.history(member),
            "card": {
                "text": card,
                "chars": len(card),
                "budget": rules.card.budget_chars,
                "custom": bool(str(p.get("identity.随身卡") or "").strip()),
            },
        }

    def member_profiles(self) -> dict[str, Any]:
        """每位成员的档案概况：版本号、随身卡字数（网页「自我档案」的成员列表）。"""
        out = []
        for m in self.chats.everyone():
            p = self.engine.profiles.current(m.id)
            card = card_text(self.rt, m.id)
            out.append(
                {
                    "id": m.id,
                    "name": m.name,
                    "version": p.version,
                    "nickname": p.get("identity.小名") or "",
                    "card_chars": len(card),
                }
            )
        return {"members": out, "budget": self.rt.config.roundtable.chat.engine.card.budget_chars}

    def member_profile_import(
        self, text: str, mapping: dict[str, str] | None = None, dry_run: bool = False
    ) -> dict[str, Any]:
        """把人物卡（Markdown，每位成员一节）读进自我档案；以用户身份写，每位成员存一个新版本。

        卡名和成员显示名一样就自动对上，mapping（卡名 → 成员 id，空串 = 跳过）可以改。
        dry_run 只解析不写。用户锁住的项不动。
        """
        cards = parse_cards(text)
        if not cards:
            raise ServiceError("没认出人物卡：每位成员一节，标题写成「## 名字 · 完整角色卡」")
        everyone = self.chats.everyone()
        ids = {m.id for m in everyone}
        matched = match_members(cards, [(m.id, m.name) for m in everyone])
        for name, mid in (mapping or {}).items():
            if name in matched:
                matched[name] = mid or None
        source = source_title(text)
        out = []
        for card in cards:
            mid = matched.get(card.name)
            if mid is not None and mid not in ids:
                raise ServiceError(f"没有这位成员：{mid}")
            row: dict[str, Any] = {
                "name": card.name,
                "member": mid,
                "chapters": list(card.chapters),
                "version": None,
            }
            if mid is not None and not dry_run:
                patch = card_patch(card, source)
                patch = without_locked(patch, self.engine.profiles.current(mid).locked)
                self._profile_do(
                    lambda mid=mid, patch=patch: self.engine.profiles.change(
                        mid, patch, author="user", reason=f"导入人物卡：{source}"
                    )
                )
                row["version"] = self.engine.profiles.current(mid).version
            out.append(row)
        return {
            "source": source,
            "cards": out,
            "members": [{"id": m.id, "name": m.name} for m in everyone],
            "imported": sum(1 for r in out if r["version"] is not None),
        }

    def _profile_do(self, fn: Any) -> None:
        try:
            fn()
        except ProfileError as e:
            raise ServiceError(str(e)) from None

    def member_profile_change(
        self, member: str, patch: dict[str, Any], reason: str = ""
    ) -> dict[str, Any]:
        """用户改档案：{分区: {键: 值或 None}}。"""
        member = self._profile_member(member)
        self._profile_do(
            lambda: self.engine.profiles.change(
                member, patch, author="user", reason=reason or "你改的"
            )
        )
        return self.member_profile(member)

    def member_profile_lock(self, member: str, path: str, locked: bool = True) -> dict[str, Any]:
        member = self._profile_member(member)
        self._profile_do(lambda: self.engine.profiles.lock(member, path, locked=locked))
        return self.member_profile(member)

    def member_profile_revert(self, member: str, version: int) -> dict[str, Any]:
        member = self._profile_member(member)
        self._profile_do(lambda: self.engine.profiles.revert(member, version))
        return self.member_profile(member)

    def member_profile_clear(self, member: str) -> dict[str, Any]:
        member = self._profile_member(member)
        n = self.engine.profiles.clear(member)
        return {"member": member, "deleted": n}

    def member_states(self) -> dict[str, Any]:
        """每位成员此刻的心情（-1～1）和精力（0～1）、基线、最近的本能反射（P3 状态，只读）。"""
        state = self.engine.state
        out = []
        for m in self.chats.everyone():
            r = state.read(m.id)
            b = r.baseline
            out.append(
                {
                    "member": m.id,
                    "name": m.name,
                    "mood": round(r.mood, 3),
                    "energy": round(r.energy, 3),
                    "line": state.line(m.id, r.at),
                    "baseline": {
                        "mood": b.mood,
                        "sensitivity": b.sensitivity,
                        "wake": b.wake,
                        "sleep": b.sleep,
                        "night_owl": b.night_owl,
                    },
                    "recent": r.recent,
                }
            )
        return {"members": out, "enabled": state.enabled()}

    def member_states_clear(self, member: str | None = None) -> dict[str, Any]:
        """清空状态（全部或某一位），回到基线。"""
        return {"deleted": self.engine.state.clear(member)}

    # --- 成员关系（P4，docs/MEMBER_ENGINE.md「P4 关系」） ------------------------------

    def member_relation(self, member: str) -> dict[str, Any]:
        """和你的阶段、亲近度，和每位同事的亲近度与看法（程序按共同经历算，只读）。"""
        member = self._profile_member(member)
        rel = self.engine.relation
        rules = self.rt.config.roundtable.chat.engine.relation
        data = rel.data(member)
        live = {f["id"] for f in self.engine.about.facts()}
        facts = {f["id"]: f["text"] for f in self.engine.about.facts()}
        views = [
            {
                **v,
                "index": i,
                "live": view_live(v, live),
                "facts_text": [facts[n] for n in v.get("facts") or [] if n in facts],
            }
            for i, v in enumerate(data.get("views") or [])
        ]
        due = self.insight.due(self._local_now().date())
        depth = rules.insight.at(rel.level(member))
        return {
            **rel.summary(member),
            "depth": {"views": depth.views, "chars": depth.chars},
            "enabled": rel.enabled(),
            "stages": [s.model_dump() for s in rules.stages],
            "views": views,
            "views_week": rel.views_week(member),
            "views_log": list(reversed(data.get("views_log") or []))[:5],
            "insight_due": week_id(due) if due else None,
            "insight_enabled": self.insight.enabled(),
        }

    async def member_relation_insight(self, member: str) -> dict[str, Any]:
        """马上让她想一次上一周对你的看法（每周一次；已想过返回 409）。"""
        member = self._profile_member(member)
        due = self.insight.due(self._local_now().date())
        if due is None or not self.insight.enabled():
            raise ServiceError("还没到每周想一想的那天", 409)
        wid = week_id(due)
        if self.engine.relation.views_week(member) == wid:
            raise ServiceError(f"{wid} 已经想过了", 409)
        m = next(m for m in self.chats.everyone() if m.id == member)
        got = await self.insight.run(m, wid, today=self._local_now().date())
        if got is None:
            raise ServiceError("今天生活的额度不够了，下次再想", 409)
        return {"result": got, **self.member_relation(member)}

    def member_relation_drop_view(self, member: str, index: int) -> dict[str, Any]:
        """删掉她的一句见解。"""
        member = self._profile_member(member)
        if not self.engine.relation.drop_view(member, index):
            raise ServiceError("没有这一句", 404)
        return self.member_relation(member)

    def member_relation_reply_view(self, member: str, index: int, text: str) -> dict[str, Any]:
        """你在她的一句看法下随手回一句（空串＝撤回）；她下周自己琢磨你的意思。"""
        member = self._profile_member(member)
        if not self.engine.relation.reply_view(member, index, text):
            raise ServiceError("没有这一句", 404)
        return self.member_relation(member)

    def member_relation_clear(self, member: str | None = None) -> dict[str, Any]:
        """清空关系（全部或某一位），从头再按经历累积。"""
        if member is not None:
            member = self._profile_member(member)
        return {"deleted": self.engine.relation.clear(member)}

    # --- 关于你（成员系统 P8：8 位共用的用户画像，只存本机） --------------------------------

    def _about(self, fn: Any) -> Any:
        try:
            return fn()
        except AboutMissing as e:
            raise ServiceError(str(e), 404) from None
        except AboutError as e:
            raise ServiceError(str(e)) from None

    def about_view(self) -> dict[str, Any]:
        """画像：每条的领域、来源、把握度、变淡 / 锁定，证据（原话、会话、时间、谁注意到的）。"""
        v = self.engine.about.view()
        names = {m.id: m.name for m in self.chats.everyone()}
        rooms = {r["id"]: r for r in self.rt.repo.chat_rooms()}
        for f in v["facts"]:
            for e in f["evidence"]:
                room = rooms.get(e["room"])
                e["room_title"] = (
                    "私聊" if room and room["kind"] == "dm" else (room or {}).get("title", "")
                )
                e["by_name"] = names.get(e.get("by"), e.get("by") or "")
            f["noticed"] = [names.get(x, x) for x in f.get("noticed", [])]
        return v

    # 你的状态的来源（每项只在开关打开、成员要回话时才问；读到的不写库）

    def _say_music(self, now: datetime) -> str | None:
        v = self._np().view(self.engine.about.switches()["music_player"])
        cur = v.get("now")
        if not v["available"] or not cur or cur["status"] != "playing" or not cur["title"]:
            return None
        who = f"（{cur['artist']}）" if cur["artist"] else ""
        return f"用户正在听《{cur['title']}》{who}"

    def _say_idle(self, now: datetime) -> str | None:
        if self._pc.available():
            return None
        idle = self._pc.read().idle_seconds
        if idle is None:
            return None
        minutes = int(idle // 60)
        if minutes >= self.engine.about.rules.idle_minutes:
            return f"用户的电脑已经 {minutes} 分钟没人动了（可能走开了）"
        return "用户正在用电脑"

    def _say_game(self, now: datetime) -> str | None:
        if self._pc.available():
            return None
        game = game_of(self._pc.read().foreground, self.engine.about.games())
        return f"用户在玩{game}" if game else None

    def _say_mail(self, now: datetime) -> str | None:
        if not self.mail.enabled():
            return None
        d = self.mail.view()["digest"]
        text = f"用户邮箱今天收到 {d['today']} 封，重要未读 {d['important_unread']} 封"
        if d["due"]:
            first = d["due"][0]
            text += f"；最近截止 {first['due'][5:10]}「{' '.join(first['snippet'].split())[:16]}」"
        return text

    def about_status(self) -> dict[str, Any]:
        return self.rt.user_status.view()

    def about_status_set(self, kind: str, on: bool) -> dict[str, Any]:
        self._about(lambda: self.engine.about.set_status(kind, on))
        return self.about_status()

    def about_music_player(self, player: str) -> dict[str, Any]:
        self._about(lambda: self.engine.about.set_music_player(player))
        return self.about_status()

    def about_game_add(self, exe: str, name: str) -> dict[str, Any]:
        self._about(lambda: self.engine.about.add_game(exe, name))
        return self.about_status()

    def about_game_del(self, exe: str) -> dict[str, Any]:
        self._about(lambda: self.engine.about.del_game(exe))
        return self.about_status()

    def about_class_add(self, weekday: int, start: str, end: str, name: str) -> dict[str, Any]:
        self._about(lambda: self.engine.about.add_class(weekday, start, end, name))
        return self.about_status()

    def about_ddl_add(self, name: str, due: str) -> dict[str, Any]:
        self._about(lambda: self.engine.about.add_ddl(name, due))
        return self.about_status()

    def about_schedule_del(self, kind: str, item_id: int) -> dict[str, Any]:
        self._about(lambda: self.engine.about.del_schedule(kind, item_id))
        return self.about_status()

    def about_schedule_clear(self) -> dict[str, Any]:
        return {"deleted": self.engine.about.clear_schedule()}

    def about_add(self, text: str, domain: str | None = None) -> dict[str, Any]:
        self._about(lambda: self.engine.about.add(text, domain))
        return self.about_view()

    def about_edit(
        self, fact_id: int, text: str | None = None, domain: str | None = None
    ) -> dict[str, Any]:
        self._about(lambda: self.engine.about.edit(fact_id, text, domain))
        return self.about_view()

    def about_delete(self, fact_id: int) -> dict[str, Any]:
        self._about(lambda: self.engine.about.delete(fact_id))
        return self.about_view()

    def about_reject(self, fact_id: int) -> dict[str, Any]:
        """「不对」：删掉，之后像它的线索不再记。"""
        self._about(lambda: self.engine.about.reject(fact_id))
        return self.about_view()

    def about_forget_rejected(self, index: int) -> dict[str, Any]:
        self._about(lambda: self.engine.about.forget_rejected(index))
        return self.about_view()

    def about_lock(self, fact_id: int, locked: bool = True) -> dict[str, Any]:
        self._about(lambda: self.engine.about.lock(fact_id, locked))
        return self.about_view()

    def about_domain(self, domain: str, on: bool) -> dict[str, Any]:
        self._about(lambda: self.engine.about.set_domain(domain, on))
        return self.about_view()

    def about_clear(self, rejected: bool = False) -> dict[str, Any]:
        return {"deleted": self.engine.about.clear(rejected)}

    def about_export(self, fmt: str = "json") -> tuple[bytes, str, str]:
        """导出画像：(内容, 类型, 文件名)；fmt = json / md。"""
        if fmt not in ("json", "md"):
            raise ServiceError("格式只能是 json 或 md")
        text = self.engine.about.export(fmt)
        media = "application/json" if fmt == "json" else "text/markdown"
        return text.encode("utf-8"), f"{media}; charset=utf-8", f"about_you.{fmt}"

    # --- 成员成长（P6，docs/MEMBER_ENGINE.md「P6 成长」） ------------------------------

    def member_growth(self, member: str) -> dict[str, Any]:
        """成长时间线（新的在前）、上次反思的是哪一周、下一次反思哪一周。"""
        member = self._profile_member(member)
        rules = self.rt.config.roundtable.chat.engine.growth
        due = self.growth.due(self._local_now().date())
        names = {m.id: m.name for m in self.chats.everyone()}
        entries = []
        for e in self.growth.log.entries(member):
            e = dict(e)
            if e.get("clash"):
                e["clash_name"] = names.get(e["clash"], e["clash"])
            entries.append(e)
        return {
            "member": member,
            "enabled": rules.enabled,
            "week": self.growth.log.done_week(member),
            "due": week_id(due) if due else None,
            "rules": {
                "max_changes": rules.max_changes,
                "core_monthly_steps": rules.core_monthly_steps,
                "user_share": rules.user_share,
                "min_days": rules.min_days,
            },
            "timeline": entries,
            "habits": self.growth.habits(member),
            "skills": self.growth.skills(member),
            "reports": [
                {**r, "lines": report_lines(r)}
                for _, r in sorted(self.growth.log.load(member)["reports"].items(), reverse=True)
            ],
        }

    async def member_growth_run(self, member: str) -> dict[str, Any]:
        """马上让这位成员反思上一周（每周只反思一次；上周已经反思过就不再调用）。"""
        member = self._profile_member(member)
        now = self._local_now()
        start = monday(now.date()) - timedelta(days=7)
        if self.growth.log.done_week(member) == week_id(start):
            raise ServiceError("上一周已经反思过了，每周只反思一次", 409)
        m = next(x for x in self.chats.everyone() if x.id == member)
        got = await self.growth.run(m, start, now.tzinfo, today=now.date())
        if got is None:
            raise ServiceError("今天生活的额度或聊天预算不够，下次推进时再试", 409)
        return {"result": got, **self.member_growth(member)}

    def member_growth_revert(self, member: str, entry_id: int) -> dict[str, Any]:
        """撤回一条成长改动（以你的身份存成档案的新版本）。"""
        member = self._profile_member(member)
        self._profile_do(lambda: self.growth.log.revert(member, entry_id))
        return self.member_growth(member)

    def member_growth_clear(self, member: str) -> dict[str, Any]:
        """清空这位成员的成长时间线（档案本身和它的修改记录不动）。"""
        member = self._profile_member(member)
        return {"member": member, "deleted": self.growth.log.clear(member)}

    def member_events_count(self) -> dict[str, Any]:
        return {"events": self.rt.repo.count_member_events()}

    # --- 日历底座（docs/CALENDAR.md；只给数据，不调模型） ------------------------------------

    def calendar_view(self, days: int | None = None, start: str | None = None) -> dict[str, Any]:
        """从 start（默认今天）起 days 天（默认 calendar.board_days）是什么日子。"""
        rules = self.rt.config.roundtable.calendar
        try:
            first = date.fromisoformat(start) if start else self._local_now().date()
        except ValueError:
            raise ServiceError("start 要写成 YYYY-MM-DD", 400) from None
        n = days or rules.board_days
        if not 1 <= n <= 366:
            raise ServiceError("days 要在 1～366 之间", 400)
        return {
            "enabled": rules.enabled,
            "days": self.calendar.span(first, n) if rules.enabled else [],
            "user_dates": self.calendar.user_dates(),
        }

    def calendar_add_date(self, name: str, when: str, lunar: bool = False) -> dict[str, Any]:
        """加一个你自己的纪念日（每年都过，只存本机）。"""
        try:
            row = self.calendar.add_user_date(name, when, lunar=lunar)
        except ValueError as exc:
            raise ServiceError(str(exc), 400) from None
        return {"date": row, "user_dates": self.calendar.user_dates()}

    def calendar_remove_date(self, date_id: str) -> dict[str, Any]:
        if not self.calendar.remove_user_date(date_id):
            raise ServiceError("没有这个纪念日", 404)
        return {"user_dates": self.calendar.user_dates()}

    def member_events_clear(self) -> dict[str, Any]:
        """一键清空成员引擎记下的全部事件（只存本机的经历数据）。"""
        return {"deleted": self.engine.bus.clear()}

    def life_reports(self) -> dict[str, Any]:
        """生活的每周自检（docs/CHAT.md G9）：已存的每周报告（新的在前）和最近 7 天的实时报告。"""
        now = self.rt.budget.now()
        names = {m.id: m.name for m in self.chats.everyone()}
        return {
            "reports": self.rt.repo.life_reports(),
            "recent": self.life.guard.report(now - timedelta(days=7), now, names),
            "halted": self.life.guard.halted(),
        }

    def chat_feeds(self) -> dict[str, Any]:
        """订阅源清单（网页改过的优先）、上次抓取时间、今天联网搜索次数与上限。"""
        rules = self.rt.config.roundtable.chat.browse
        feeds, source = feeds_of(self.rt)
        return {
            "enabled": rules.enabled,
            "feeds": [f.model_dump() for f in feeds],
            "source": source,
            "last_refresh": self.rt.repo.setting(REFRESH_KEY) or None,
            "search_used_today": self.life.browse.searches_today(),
            "search_per_day": rules.search_per_day,
            "items": len(self.rt.repo.web_items(limit=1000)),
        }

    def chat_set_feeds(self, feeds: list[dict[str, Any]] | None) -> dict[str, Any]:
        try:
            set_feeds(self.rt, feeds)
        except ValueError as exc:  # pydantic 的 ValidationError 也是 ValueError
            raise ServiceError(f"订阅源不合法：{exc}") from None
        return self.chat_feeds()

    async def chat_refresh_feeds(self) -> dict[str, Any]:
        """马上抓一遍订阅源（不调用模型、不花钱）。"""
        result = await self.life.browse.refresh(self._local_now(), force=True)
        return {**result, **self.chat_feeds()}

    def chat_clear(self, room: str = ROOM) -> dict[str, Any]:
        """一键清空这个会话的聊天记录（花费记录保留，预算照算）。"""
        chat = self._chat_room(room)
        return {"deleted": self.rt.repo.clear_chat(chat.room)}

    def perception_clear(self) -> dict[str, Any]:
        """一键清空感官缓存（data/perception/ 里的感知卡和转写稿；聊天记录里的卡不动）。"""
        return {"deleted": self.perceiver.cache.clear()}

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
