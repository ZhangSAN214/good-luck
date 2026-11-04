"""对外 facade：Web 服务（以及以后的其他界面）只通过这里访问核心逻辑。

- 所有返回值都是可直接转成 JSON 的 dict / list；揭晓前不含任何模型身份。
- 讨论在后台任务中执行，提交后立即返回会话 id；进度通过事件订阅获得。
- 每当讨论完成、失败、停止或需要用户确认时，会发出一个 "state" 事件。
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from roundtable.core import chat_handoff as handoff
from roundtable.core.allocation import display_name
from roundtable.core.almanac import Almanac
from roundtable.core.attachments.detect import TYPES as UPLOAD_TYPES
from roundtable.core.backup import DbBackup
from roundtable.core.benchkit.builder import BenchBuilder
from roundtable.core.benchkit.desk import Desk
from roundtable.core.benchkit.netcheck import http_status, net_check
from roundtable.core.benchkit.research import Researcher
from roundtable.core.benchkit.research import http_fetchers as research_fetchers
from roundtable.core.benchkit.scout import Scout as PluginScout
from roundtable.core.benchkit.scout import http_fetchers
from roundtable.core.benchkit.tree import tree_view
from roundtable.core.benchkit.weekly import Weekly
from roundtable.core.budget import report as usage_report
from roundtable.core.budget.channels import MonthSpend, save_priority
from roundtable.core.budget.guard import day_window, month_window
from roundtable.core.chat import ChatHub
from roundtable.core.chat_actions import sticker_packs
from roundtable.core.chat_media import MAX_BYTES as CHAT_MEDIA_MAX
from roundtable.core.chat_work import short_title
from roundtable.core.config.loader import DEFAULT_CONFIG_DIR
from roundtable.core.emoji_images import EMOJI_DIR, EmojiImages
from roundtable.core.evolution_dev import Run as DevRun
from roundtable.core.evolution_dev import run_process
from roundtable.core.growth import GrowthKeeper
from roundtable.core.insight import InsightKeeper
from roundtable.core.lan import COOKIE as LAN_COOKIE
from roundtable.core.lan import LanAccess, is_local, is_remote, qr_svg
from roundtable.core.life import LIFE_STEPS, LifeEngine
from roundtable.core.mail import MailAI, MailBox, OwaBrowser
from roundtable.core.member import MemberEngine
from roundtable.core.member.voice import VoicePolicy
from roundtable.core.member.yourstate import UserStatus
from roundtable.core.member_stickers import (
    MemberStickers,
)
from roundtable.core.member_voice import (
    MemberVoices,
)
from roundtable.core.members_art import MEMBERS_FILE, load_members, mime_of, resolve_file
from roundtable.core.memory import MemoryKeeper
from roundtable.core.moments import PHOTO_JOB
from roundtable.core.nowplaying import COVERS_DIR, NowPlaying, list_covers, read_cover
from roundtable.core.orchestrator import Orchestrator, OrchestratorError, RunResult
from roundtable.core.perception import Perceiver, PerceptionCache
from roundtable.core.persona import persona_of
from roundtable.core.providers import (
    KeyRing,
    ProviderError,
    UnsupportedCapability,
    redact,
)
from roundtable.core.routing import (
    RoutingError,
)
from roundtable.core.runtime import PROJECT_ROOT, WANDER_DIR, Runtime, data_dir
from roundtable.core.screen import (
    AdbSource,
    PcSource,
    ScreenWatch,
)
from roundtable.core.services.apartment import ApartmentService
from roundtable.core.services.chat import ChatService
from roundtable.core.services.common import (
    MAX_QUESTION_CHARS,
    ServiceError,
    log,
)
from roundtable.core.services.office import OfficeService
from roundtable.core.services.terminal import TerminalService
from roundtable.core.standee import load_standee
from roundtable.core.standee import standee_file as read_standee_file
from roundtable.core.steps import Event, restore_state
from roundtable.core.storage import NotFound
from roundtable.core.supercomputer import Butler, Supercomputer
from roundtable.core.supercomputer.credit import CreditLauncher
from roundtable.core.supercomputer.launch import CommandLauncher, HookLauncher
from roundtable.core.wander import Wanderer
from roundtable.core.yourpc import WindowsPc

try:  # dsh 工作台是可拔掉的外设：删掉整个 core/dsh/ 圆桌照常运行（docs/DSH.md，CI「拔掉 dsh」任务）
    from roundtable.core.dsh import DshManager
except ImportError:  # pragma: no cover - 只在拔掉 dsh 时走到
    DshManager = None  # type: ignore[assignment,misc]


ORPHANED_ERROR = "服务上次意外退出，做到一半中断了，可以点「继续」接着做"
BENCH_HANDOFF_KEY = "office_bench:"  # 设置表：哪些办公室会话已经交给了工作台（只交一次）

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


class RoundtableService(TerminalService, ApartmentService, ChatService, OfficeService):
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
        emoji_dir = runtime.sticker_dir.parent / EMOJI_DIR if runtime.sticker_dir else None
        self.emoji = EmojiImages(runtime, emoji_dir)
        self.packs = sticker_packs(runtime)
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
        self.growth.allow = self.life.weekly_allowed
        # 整库备份（docs/BACKUP.md）：每天一份 + 存档点，不花钱
        rules = runtime.config.roundtable.backup
        self.backup = DbBackup(
            runtime.repo.conn,
            runtime.backup_dir,
            enabled=rules.enabled,
            keep_daily=rules.keep_daily,
            keep_checkpoints=rules.keep_checkpoints,
        )
        self._backup_task: asyncio.Task[None] | None = None
        self._butler: Butler | None = None
        self._butler_task: asyncio.Task[None] | None = None
        # 游戏模式（docs/GAMEMODE.md）：游戏表用「关于你」的；排队补跑朋友圈配图
        self._gamemode_task: asyncio.Task[None] | None = None
        if runtime.gamemode is not None:
            if about is not None:
                runtime.gamemode.set_games(about.games)
            runtime.gamemode.register(PHOTO_JOB, self._deferred_moment_photo)
        # P4 关系·对你的见解：和成长同一天，每位按自己的性格读一遍画像；花费计入生活每天的上限
        self.insight = InsightKeeper(runtime, self.chats.everyone)
        self.insight.allow = self.life.weekly_allowed
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
        self.desk = self._build_desk(
            runtime.config.roundtable.bench,
            (base / "bench") if runtime.mail_dir is not None else None,
        )
        self.weekly = Weekly(self.desk, now=self._local_now)
        self._weekly_task: asyncio.Task[None] | None = None
        self._weekly_force: asyncio.Task[Any] | None = None
        self.bench = BenchBuilder(
            runtime.config.roundtable.bench,
            root=(base / "bench") if runtime.mail_dir is not None else None,
            supercomputer=self.supercomputer,
            render=self._super_render,
            scout=self._build_scout(runtime.config.roundtable.bench),
            researcher=self._build_researcher(runtime.config.roundtable.bench),
            whitelist=self._dsh_whitelist,
            skill=self._bench_skill,
            desk=self.desk,
            report=self._bench_report,
            metered_spent=self._dsh_total_spent,
        )
        # 办公室交接（M）：工作台开着、项目室开着、有本机数据目录、没在做别的台时，大项目才问老板
        self.orc.bench_ready = lambda: (
            self.bench.unavailable_reason() is None
            and self.bench.rules.project.enabled
            and not self.bench.busy
        )
        self.bench_status_fetch = http_status()  # 联网自检用（测试换成假的）
        self._chat_tasks: set[asyncio.Task[Any]] = set()
        self._life_task: asyncio.Task[None] | None = None
        self._evolution_task: asyncio.Task[None] | None = None
        self._dev_tasks: dict[str, asyncio.Task[None]] = {}  # 「做这条」后正在实现的建议
        self.dev_run: DevRun = run_process  # 叫外部命令（测试换成假的）

    def _dsh_total_spent(self) -> float | None:
        """dsh 开始记账以来一共花了多少美元（余额差值估算）；没有 dsh 或不记账时为 None。"""
        if self.dsh is None or not self.dsh.budget()["tracking"]:
            return None
        return self.dsh.ledger.total()

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

        wrap_version = runtime.config.roundtable.prompts.get("dsh_wrapup")

        def wrapup(max_steps: int) -> str:
            assert wrap_version is not None
            rendered = runtime.prompts.render("dsh_wrapup", wrap_version, max_steps=str(max_steps))
            return "\n\n".join(m.content for m in rendered.messages)

        dsh = DshManager(
            runtime.config.roundtable.dsh,
            catalog_path=DEFAULT_CONFIG_DIR / "dsh_plugins.yaml",
            root=base / "dsh" if base is not None else None,
            persona=persona,
            wrapup=wrapup if wrap_version is not None else None,
            shared_spend=runtime.repo.spent_on_channels,
            dotenv_path=PROJECT_ROOT / ".env",
        )
        # 和渠道共用每周上限（2026-11-04 用户：聊天的 DeepSeek 加工作台每周最多 ¥10）
        weekly, ch = runtime.weekly, runtime.config.roundtable.dsh.cap_channel
        if weekly is not None and ch and ch in weekly.caps:
            weekly.extra[ch] = lambda: dsh.ledger.week_spent(dsh.now())
            dsh.weekly_gate = lambda: weekly.blocked(ch)
        return dsh

    @staticmethod
    def _build_scout(rules: Any) -> PluginScout:
        fetch_json, fetch_bytes = http_fetchers()
        return PluginScout(
            fetch_json=fetch_json,
            fetch_bytes=fetch_bytes,
            min_stars=rules.min_stars,
            max_kb=rules.max_repo_mb * 1024,
        )

    @staticmethod
    def _build_researcher(rules: Any) -> Researcher:
        fetch_json, fetch_text, check_url = research_fetchers()
        r = rules.research
        return Researcher(
            fetch_json=fetch_json,
            fetch_text=fetch_text,
            check_url=check_url,
            min_github=r.min_github,
            max_github=r.max_github,
            papers=r.papers,
            news=r.news,
            min_stars=rules.min_stars,
            max_kb=rules.max_repo_mb * 1024,
        )

    @staticmethod
    def _build_desk(rules: Any, root: Path | None) -> Desk:
        """资料台（工作台抽屉「资料」）：GitHub 榜单、论文动态、能力地图、建议卡；免费只读。"""
        fetch_json, fetch_text, _ = research_fetchers()
        return Desk(
            rules.desk,
            root,
            fetch_json=fetch_json,
            fetch_text=fetch_text,
            min_stars=rules.min_stars,
        )

    def bench_desk(self, *, refresh: bool = False) -> dict[str, Any]:
        """资料台的内容。refresh=True 时在后台刷新（联网、免费），先给旧的；
        读不联网，过期了由界面决定要不要刷（打开抽屉时 due 就刷一次）。"""
        if refresh:
            self.desk.start_refresh()
        return self.desk.view()

    # --- 资料台周报（core/benchkit/weekly.py） -------------------------------------------

    def start_desk_weekly(self) -> None:
        """后台每半小时看一次表，到点出周报（只在正式启动的服务里调用）。"""
        if self._weekly_task is None and self.weekly.rules.weekly:
            self._weekly_task = asyncio.get_running_loop().create_task(self._weekly_loop())

    async def _weekly_loop(self) -> None:
        while True:
            try:
                if not self._background_paused():
                    await self.weekly.tick()
            except Exception:  # noqa: BLE001 - 周报出错不影响其他功能，下次再看
                log.exception("资料台周报：生成出错")
            await asyncio.sleep(1800)

    def bench_weekly(self, week: str | None = None) -> dict[str, Any]:
        """周报概况（历史周、最新一份、是否到点、是否在生成）；给了 week 就只要那一份。"""
        if week is None:
            return self.weekly.view() | {"building": self._weekly_building}
        rep = self.weekly.get(week)
        if rep is None:
            raise ServiceError("没有这一周的周报", 404)
        return rep

    @property
    def _weekly_building(self) -> bool:
        return self._weekly_force is not None and not self._weekly_force.done()

    def bench_weekly_build(self) -> dict[str, Any]:
        """现在就出本周的周报（后台：资料过期先刷新，约 3～5 分钟；不调模型、不花钱）。"""
        if not self.weekly.view()["enabled"]:
            raise ServiceError("周报在配置里关着（bench.desk.weekly）", 409)
        if not self._weekly_building:
            self._weekly_force = asyncio.get_running_loop().create_task(
                self.weekly.tick(force=True)
            )
        return self.bench_weekly()

    def bench_desk_suggest(self, sid: str, accept: bool) -> dict[str, Any]:
        try:
            return self.desk.decide(sid, accept)
        except LookupError as exc:
            raise ServiceError(str(exc), 404) from None

    def _bench_skill(self, name: str) -> tuple[str, str]:
        """工作台地基技能：prompts/bench_skill_<名字>/ 的 system 段是短版、user 段是长版。"""
        role = f"bench_skill_{name}"
        tpl = self.rt.prompts.get(role, self.rt.config.roundtable.prompts[role])
        return tpl.system, tpl.user

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
        command = CommandLauncher(drop_env=keys)
        launchers: dict[str, Any] = {"command": command}
        if self.dsh is not None:
            dsh = self.dsh
            launchers["dsh"] = HookLauncher(
                dsh.job_blocked,
                dsh.job_launch,
                dsh.job_started,
                dsh.job_finished,
                getattr(dsh, "job_watch", None),  # 没有就不数步数（如测试里的假 dsh）
            )
        rules = config.roundtable.supercomputer
        credit = {w.credit_channel for w in rules.workers if w.via == "credit" and w.credit_channel}
        weekly = runtime.weekly
        if credit:
            # 用 API 额度的助手：key 只从 .env 读（内存数据库 / 测试里不读，这几位显示没有 key）
            names = {
                ch: config.models.channels[ch].key_env or ""
                for ch in credit
                if ch in config.models.channels
            }
            ring = (
                KeyRing.from_env(
                    [n for n in names.values() if n], dotenv_path=PROJECT_ROOT / ".env"
                )
                if base is not None
                else KeyRing({})
            )
            launchers["credit"] = CreditLauncher(
                command,
                {ch: (names.get(ch, ""), ring.get(names.get(ch))) for ch in credit},
                weekly.blocked if weekly is not None and weekly.active else None,
            )
        sc = Supercomputer(
            rules,
            root=base / "super" if base is not None else None,
            render=self._super_render,
            launchers=launchers,
        )
        if weekly is not None:
            for ch in credit:  # 这几位自己报的花费算进渠道的每月上限
                ids = [w.id for w in rules.workers if w.credit_channel == ch]
                weekly.monthly_extra[ch] = lambda ids=ids: sum(
                    sc.ledger.month_spent(i, sc.now()) for i in ids
                )
        return sc

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

    @property
    def butler(self) -> Butler:
        """超级电脑的管家（定时活、排队、自治闸门、日报、审计），跟着当前的超级电脑走。"""
        sc = self.supercomputer
        if self._butler is None or self._butler.sc is not sc:
            self._butler = Butler(sc, sc.rules.butler, root=sc.root, now=self._local_now)
        return self._butler

    def start_butler(self) -> None:
        """后台每隔一会儿看一次表（只在正式启动的服务里调用；测试直接调 super_tick）。"""
        if self._butler_task is None and self.butler.rules.enabled:
            self._butler_task = asyncio.get_running_loop().create_task(self._butler_loop())

    async def _butler_loop(self) -> None:
        while True:
            try:
                if not self._background_paused():
                    self.super_tick()
            except Exception:  # noqa: BLE001 - 管家出错不影响其他功能，下次再看
                log.exception("超级电脑管家：看表出错")
            await asyncio.sleep(self.butler.rules.tick_seconds)

    def super_tick(self) -> dict[str, int]:
        if self.supercomputer.unavailable_reason():
            return {}
        return self.butler.tick()

    def super_status(self) -> dict[str, Any]:
        return {**self.supercomputer.status(), "butler": self.butler.status()}

    def _butler_call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except LookupError as exc:
            raise ServiceError(str(exc), 404) from None
        except RuntimeError as exc:
            raise ServiceError(str(exc), 409) from None
        except ValueError as exc:
            raise ServiceError(str(exc), 400) from None

    def super_submit(self, text: str, queue: bool = False) -> dict[str, Any]:
        self._butler_call(self.butler.submit, text, queue=queue)
        return self.super_status()

    async def super_cancel(self) -> dict[str, Any]:
        await self.butler.cancel()
        return self.super_status()

    def super_add_schedule(
        self, text: str, at: str, days: list[int] | None = None, once: str | None = None
    ) -> dict[str, Any]:
        self._butler_call(self.butler.add_schedule, text, at, days=days, once=once)
        return self.super_status()

    def super_remove_schedule(self, item_id: str) -> dict[str, Any]:
        self._butler_call(self.butler.remove_schedule, item_id)
        return self.super_status()

    def super_toggle_schedule(self, item_id: str, enabled: bool) -> dict[str, Any]:
        self._butler_call(self.butler.toggle_schedule, item_id, enabled)
        return self.super_status()

    def super_run_schedule(self, item_id: str) -> dict[str, Any]:
        if self.supercomputer.unavailable_reason():
            raise ServiceError(self.supercomputer.unavailable_reason() or "", 400)
        self._butler_call(self.butler.run_schedule, item_id)
        return self.super_status()

    def super_approve(self, item_id: str) -> dict[str, Any]:
        self._butler_call(self.butler.approve, item_id)
        return self.super_status()

    def super_reject(self, item_id: str) -> dict[str, Any]:
        self._butler_call(self.butler.reject, item_id)
        return self.super_status()

    def super_unqueue(self, item_id: str) -> dict[str, Any]:
        self._butler_call(self.butler.unqueue, item_id)
        return self.super_status()

    def super_report(self, day: str | None = None) -> dict[str, Any]:
        rep = self._butler_call(self.butler.report, day)
        return {**rep, "days": self.butler.reports()}

    def super_audit(self) -> dict[str, Any]:
        return self.butler.audit_view()

    def super_task(self, task_id: str) -> dict[str, Any]:
        task = self.supercomputer.get(task_id)
        if task is None:
            raise ServiceError("没有这件活", 404)
        return task

    def super_set_limit(self, worker: str, limit: int) -> dict[str, Any]:
        self._butler_call(self.butler.set_limit, worker, limit)
        return self.super_status()

    def super_clear_cooldown(self, worker: str) -> dict[str, Any]:
        self._butler_call(self.butler.wake, worker)
        return self.super_status()

    # --- 工作台生成器（docs/KIT.md） --------------------------------------------------

    def bench_status(self) -> dict[str, Any]:
        st = self.bench.status()
        cur = st.get("current")
        if cur:  # 右栏的三层分工树（大阶段 → 小项目 → 小线程）
            st = st | {"current": cur | {"tree": tree_view(cur)}}
        return st

    def bench_profile_add(self, text: str, src: str = "") -> dict[str, Any]:
        """甲方档案：记下一条长期偏好（只存本机），以后每个新项目都带上。"""
        try:
            self.bench.profile_add(text, src)
        except ValueError as exc:
            raise ServiceError(str(exc), 400) from None
        except RuntimeError as exc:
            raise ServiceError(str(exc), 409) from None
        return self.bench_status()

    def bench_profile_remove(self, pid: str) -> dict[str, Any]:
        try:
            self.bench.profile_remove(pid)
        except LookupError as exc:
            raise ServiceError(str(exc), 404) from None
        return self.bench_status()

    def bench_material(self, name: str, data: bytes, of: str = "") -> dict[str, Any]:
        """拖进来的素材先收下（开工或加进工作台时再给编号）。"""
        try:
            return self.bench.stage_material(name, data, of)
        except ValueError as exc:
            raise ServiceError(str(exc), 400) from None

    @property
    def bench_material_limit(self) -> int:
        return self.bench.rules.materials.max_video_mb * 1024 * 1024

    def bench_submit(
        self,
        request: str,
        materials: list[str] | None = None,
        *,
        from_product: bool = False,
        form: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            self.bench.submit(request, materials, from_product=from_product, form=form)
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
        return b | {"tree": tree_view(b)}

    def bench_tree(self, bench_id: str) -> dict[str, Any]:
        """三层分工树：大阶段 → 小项目 → 小线程（docs/KIT.md「生成器第二版」）。"""
        tree = self.bench.tree(bench_id)
        if tree is None:
            raise ServiceError("没有这个工作台", 404)
        workers = [{"id": w.id, "label": w.label} for w in self.supercomputer.rules.workers]
        return tree | {"workers": workers, "busy": self.bench.busy}

    async def bench_ask(self, bench_id: str, question: str, json: bool = False) -> dict[str, Any]:
        try:
            if json:
                return await self.bench.ask_json(bench_id, question)
            return {"text": await self.bench.ask(bench_id, question)}
        except LookupError as exc:
            raise ServiceError(str(exc), 404) from None
        except ValueError as exc:
            raise ServiceError(str(exc), 400) from None

    async def bench_project(self, bench_id: str, action: str, **kw: Any) -> dict[str, Any]:
        """项目室的关口：回答访谈、签字开工、里程碑 / 交付通过或提变更单、出下一版提案。"""
        try:
            if action == "answer":
                self.bench.answer(bench_id, kw.get("answers") or {})
            elif action == "sign":
                self.bench.sign(
                    bench_id, kw.get("requirements"), kw.get("note") or "", kw.get("design")
                )
            elif action == "review":
                self.bench.review(bench_id, kw.get("decision") or "", kw.get("comments"))
            elif action == "evolve":
                await self.bench.evolve(bench_id)
            elif action == "attach":
                self.bench.attach(bench_id, kw.get("ids") or [])
            elif action == "look":
                self.bench.look(bench_id)
            elif action == "decide":
                self.bench.decide(bench_id, kw.get("choice") or "", kw.get("requirements"))
            elif action == "plugin":
                self.bench.request_plugin(bench_id, kw.get("name") or "")
            elif action == "pause":
                self.bench.pause(bench_id, kw.get("key") or "*")
            elif action == "resume":
                self.bench.resume(bench_id, kw.get("key"))
            elif action == "rerun":
                self.bench.rerun_thread(
                    bench_id, kw.get("thread") or "", kw.get("reason") or "", kw.get("worker")
                )
            else:
                raise ValueError("不认得这个操作")
        except LookupError as exc:
            raise ServiceError(str(exc), 404) from None
        except RuntimeError as exc:
            raise ServiceError(str(exc), 409) from None
        except ValueError as exc:
            raise ServiceError(str(exc), 400) from None
        return self.bench_status()

    def _dsh_whitelist(self) -> dict[str, str]:
        """dsh 插件白名单里审过的仓库（没带 dsh 或名单读不了就是空的）。"""
        catalog = getattr(self.dsh, "catalog", None) if self.dsh is not None else None
        return {p.repo: p.id for p in catalog.plugins if p.repo} if catalog else {}

    async def bench_net_check(self) -> dict[str, Any]:
        """联网自检：成品要用的公开数据和调研接口各试一次（只读 GET）。"""
        items = await net_check(list(self.bench.rules.net), self.bench_status_fetch)
        return {"items": items, "ok": sum(1 for x in items if x["ok"]), "total": len(items)}

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

    # --- 办公室交给工作台（M，docs/KIT.md「M 办公室交接」）------------------------------

    def _bench_handoff(self, sid: str, type_: str) -> None:
        """办公室那场对话做完、老板选了「交给工作台」：用这场对话里说清楚的内容开一个项目室
        （访谈里答过的不再问），工作台做完把成品和验收结果回报到这里。只交一次。"""
        if type_ != "completed" or self.rt.repo.setting(BENCH_HANDOFF_KEY + sid):
            return
        row = self.rt.repo.session_row(sid)
        if row["workflow"] != "office":
            return
        o = restore_state(self.rt.repo, sid, 0).office
        if not o.bench:
            return
        self.rt.repo.set_setting(BENCH_HANDOFF_KEY + sid, "1")
        question = str(row["question"] or "").strip()
        intake = o.intake or {}
        said = [f"用户的要求：{question}"]
        if intake.get("brief"):
            said.append(f"经理整理的工作说明：{intake['brief']}")
        if intake.get("reason"):
            said.append(f"经理判断的理由：{intake['reason']}")
        notes = [n["text"] for n in self.rt.repo.notes(sid) if n.get("text")]
        if notes:
            said.append("用户在办公室里补充的：" + "；".join(notes))
        files = [a["name"] for a in self.rt.repo.session_attachments(sid) if a.get("name")]
        if files:
            said.append("用户给的附件：" + "、".join(files))
        title = short_title(question.split("\n\n")[0])
        try:
            b = self.bench.submit(
                question,
                handoff={"session_id": sid, "title": title, "answered": "\n".join(said)},
            )
        except (ValueError, RuntimeError) as exc:
            content = {"started": False, "error": str(exc)[:300], "title": title}
        else:
            content = {"started": True, "bench_id": b["id"], "title": title}
        self.rt.repo.save_output(
            sid, table_no=0, step="bench", kind="office_bench", code=None, content=content
        )
        self._publish(sid, Event("office_bench", "bench", 0, None, content))

    def _bench_report(self, rep: dict[str, Any]) -> None:
        """工作台做完办公室交过来的项目：成品、交付没有、验收结果写进那场对话。"""
        sid = str(rep.get("session_id") or "")
        try:
            self.rt.repo.session_row(sid)
        except NotFound:
            return
        content = {k: v for k, v in rep.items() if k != "session_id"}
        self.rt.repo.save_output(
            sid, table_no=0, step="bench", kind="office_bench_report", code=None, content=content
        )
        self._publish(sid, Event("office_bench_report", "bench", 0, None, content))

    def _handoff_back(self, sid: str, type_: str) -> None:
        """办公室做完（或没办成）：以交办的那位成员的名义，把结果卡片发回原来的会话。"""
        where = handoff.origin(self.rt, sid)
        if where is None or type_ not in ("completed", "failed"):
            return
        handoff.forget(self.rt, sid)
        row = self.rt.repo.session_row(sid)
        ok = type_ == "completed"
        text = (self.orc.final_answer(sid) or "") if ok else ""
        files: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            files = [
                {
                    "id": f["id"],
                    "name": f["path"].rsplit("/", 1)[-1],
                    "kind": f["kind"],
                    "mime": f["mime"],
                }
                for f in self._files(sid)
                if f["latest"]
            ]
        card = handoff.result_card(
            sid=sid,
            title=short_title(row["question"].split("\n\n")[0]),
            ok=ok,
            text=text,
            files=files,
            cost=self.rt.repo.session_cost(sid),
        )
        handoff.mark(self.rt, where["message_id"], state=handoff.DONE if ok else handoff.FAILED)
        try:
            chat = self._chat_room(where["room"])
            chat.send_as(
                where["member"],
                "[办公室做好了]" if ok else "[办公室没办成]",
                meta={"work_card": card, "auto_text": True},
            )
        except (ServiceError, ValueError):
            log.warning("会话 %s：结果没能发回聊天 %s", sid, where["room"])

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
