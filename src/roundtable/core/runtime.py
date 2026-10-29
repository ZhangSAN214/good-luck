"""把配置、密钥、渠道、数据库、预算组装在一起。命令行和 Web 服务共用。"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from roundtable.core.allocation import IdentityScrubber
from roundtable.core.attachments import FileStore
from roundtable.core.budget import BudgetGuard
from roundtable.core.catalog import Fetch, ModelCatalog
from roundtable.core.changelog import CHANGELOG_FILE
from roundtable.core.config import AppConfig, load_config
from roundtable.core.config.loader import DEFAULT_CONFIG_DIR
from roundtable.core.model_directory import ModelDirectory
from roundtable.core.plugin_board import BOARD_FILE
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import ChannelRouter, KeyRing, Provider, build_providers
from roundtable.core.search import SearchService
from roundtable.core.steps import check_pipelines
from roundtable.core.storage import Repository, connect
from roundtable.core.tools import Sandbox, pick_sandbox

PROJECT_ROOT = DEFAULT_CONFIG_DIR.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "roundtable.db"
DB_ENV = "ROUNDTABLE_DB_PATH"
# 成员形象素材（头像、小人）目录：在用户本机，不进 git
DEFAULT_MEMBERS_DIR = PROJECT_ROOT / "data" / "avatars" / "roundtable_assets"
MEMBERS_ENV = "ROUNDTABLE_ASSETS_DIR"
# 终端首页大立绘的清单（图片路径相对它所在目录）：在用户本机，不进 git
DEFAULT_STANDEE_FILE = PROJECT_ROOT / "data" / "avatars" / "standee.json"
STANDEE_ENV = "ROUNDTABLE_STANDEE_FILE"
# 人才市场的最近结果（放在数据库旁边，不进 git）
CATALOG_FILE = "model_candidates.json"
# 模型图鉴（聚合渠道上的全部模型，分类展示；放在数据库旁边，不进 git）
DIRECTORY_FILE = "model_directory.json"
# 革新例会的资料卡与采纳单（放在数据库旁边，不进 git）
EVOLUTION_DIR = "evolution"
# 邮件栏专用浏览器的配置目录（登录状态在里面；放在数据库旁边，不进 git）
MAIL_DIR = "mail_browser"
# 聊天 / 朋友圈里用户发的图片、视频、语音（放在数据库旁边，不进 git）
CHAT_MEDIA_DIR = "chat_media"


def data_dir(environ: Mapping[str, str] | None = None) -> Path:
    """数据库所在目录（本机数据都放这里，不进 git）。"""
    configured = (os.environ if environ is None else environ).get(DB_ENV)
    if not configured or configured == ":memory:":
        return DEFAULT_DB.parent
    return (PROJECT_ROOT / configured).parent


@dataclass
class Runtime:
    config: AppConfig
    router: ChannelRouter
    prompts: PromptLibrary
    repo: Repository
    scrubber: IdentityScrubber
    budget: BudgetGuard
    files: FileStore = field(default_factory=lambda: FileStore(None))
    search: SearchService = field(default_factory=lambda: SearchService({}))
    unavailable_channels: dict[str, str] = field(default_factory=dict)
    # 代码运行沙箱 (后端, 不可用原因)；为空时第一次使用才检测，测试中可直接注入
    tools_sandbox: tuple[Sandbox | None, str | None] | None = None
    # 媒体生成的时钟 / 等待函数（sleep、wall_clock），测试中注入以免真的等待
    media_hooks: dict[str, Any] = field(default_factory=dict)
    # 视频截帧函数（默认用 PyAV）；测试中注入
    frame_extractor: Callable[..., list[Any]] | None = None
    # 成员形象素材目录（members.json 所在目录）；为空表示没有素材
    members_dir: Path | None = None
    # 终端首页大立绘清单（standee.json）；为空表示没有立绘
    standee_file: Path | None = None
    # 模型人才市场（只提示，不改配置）；为空时不提供
    catalog: ModelCatalog | None = None
    # 模型图鉴（只浏览，不上桌、不改配置）；为空时不提供
    directory: ModelDirectory | None = None
    # 革新例会的资料卡收件箱与采纳单所在目录（数据库旁边的 evolution/）；为空表示不可用
    evolution_dir: Path | None = None
    # 插件榜数据文件（自我学习革新调研过的插件，只读展示）；测试中可换成临时文件
    plugin_board: Path = DEFAULT_CONFIG_DIR / BOARD_FILE
    # 公告栏的版本更新说明（只读展示）；测试中可换成临时文件
    changelog: Path = DEFAULT_CONFIG_DIR / CHANGELOG_FILE
    # 邮件栏专用浏览器的配置目录；为空表示不可用（内存数据库）
    mail_dir: Path | None = None
    # 邮件栏的数据来源（测试中注入假的；为空时用 mail_dir 上的专用浏览器）
    mail_source: Any | None = None
    # 音乐卡片的「电脑上正在放的歌」（测试中注入假的；为空时用 Windows 系统媒体控制）
    nowplaying_source: Any | None = None
    # 投屏陪伴的画面来源 {"pc": ..., "android": ...}（测试中注入假的；为空时用电脑截屏和 adb）
    screen_sources: dict[str, Any] | None = None
    # 聊天 / 朋友圈的图片、视频、语音所在目录；为空表示不能存（内存数据库）
    chat_media_dir: Path | None = None

    @classmethod
    def build(
        cls,
        *,
        config: AppConfig | None = None,
        providers: Mapping[str, Provider] | None = None,
        unavailable: Mapping[str, str] | None = None,
        db_path: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
        dotenv_path: str | Path | None = PROJECT_ROOT / ".env",
        prompts: PromptLibrary | None = None,
        now: Callable[[], datetime] | None = None,
        uploads_dir: str | Path | None = None,
        search: SearchService | None = None,
        members_dir: str | Path | None = None,
        standee_file: str | Path | None = None,
        catalog_fetch: Fetch | None = None,
        evolution_dir: str | Path | None = None,
    ) -> Runtime:
        """providers 为空时从 .env / 环境变量读取 key 并构建真实渠道（测试中传入 Fake）。"""
        config = config or load_config()
        prompts = prompts or PromptLibrary()
        prompts.check_config(config.roundtable)
        check_pipelines(config)

        if providers is None:
            names = [c.key_env for c in config.models.channels.values() if c.key_env]
            names += [p.key_env for p in config.models.search_providers.values() if p.key_env]
            keys = KeyRing.from_env(names, environ=environ, dotenv_path=dotenv_path)
            built, missing = build_providers(
                config.models, keys, config.roundtable.request.timeout_s
            )
            providers, unavailable = built, missing
            if search is None:
                version = config.roundtable.prompts["web_search"]

                def render(query: str) -> list[dict[str, str]]:
                    rendered = prompts.render("web_search", version, query=query)
                    return [{"role": m.role, "content": m.content} for m in rendered.messages]

                fetch_version = config.roundtable.prompts["web_fetch"]

                def render_fetch(url: str) -> list[dict[str, str]]:
                    rendered = prompts.render("web_fetch", fetch_version, url=url)
                    return [{"role": m.role, "content": m.content} for m in rendered.messages]

                search = SearchService.build(
                    config.models, keys, config.roundtable.request.timeout_s, render, render_fetch
                )
        router = ChannelRouter(
            config.models,
            providers,
            mode=config.roundtable.channel_mode,
            policy=config.roundtable.request,
            unavailable=unavailable,
        )

        env = os.environ if environ is None else environ
        if db_path is None:
            configured = env.get(DB_ENV)
            db_path = PROJECT_ROOT / configured if configured else DEFAULT_DB
        if uploads_dir is None and str(db_path) != ":memory:":
            # 默认放在配置的目录；自定义数据库位置时放在数据库旁边
            default = Path(db_path).resolve() == DEFAULT_DB.resolve()
            storage = config.roundtable.uploads.storage_dir
            uploads_dir = PROJECT_ROOT / storage if default else Path(db_path).parent / "uploads"
        if members_dir is None:
            configured = env.get(MEMBERS_ENV)
            members_dir = PROJECT_ROOT / configured if configured else DEFAULT_MEMBERS_DIR
        if standee_file is None:
            configured = env.get(STANDEE_ENV)
            standee_file = PROJECT_ROOT / configured if configured else DEFAULT_STANDEE_FILE
        if evolution_dir is None and str(db_path) != ":memory:":
            evolution_dir = Path(db_path).parent / EVOLUTION_DIR
        repo = Repository(connect(db_path))
        guard = BudgetGuard(config.roundtable.budget, repo, **({"now": now} if now else {}))
        return cls(
            config=config,
            router=router,
            prompts=prompts,
            repo=repo,
            scrubber=IdentityScrubber.from_config(
                config.models, config.personas.nicknames.values()
            ),
            budget=guard,
            files=FileStore(Path(uploads_dir) if uploads_dir is not None else None),
            search=search or SearchService({}),
            unavailable_channels=dict(unavailable or {}),
            members_dir=Path(members_dir),
            standee_file=Path(standee_file),
            catalog=ModelCatalog(
                config,
                None if str(db_path) == ":memory:" else Path(db_path).parent / CATALOG_FILE,
                catalog_fetch,
            ),
            directory=ModelDirectory(
                config,
                None if str(db_path) == ":memory:" else Path(db_path).parent / DIRECTORY_FILE,
                catalog_fetch,
            ),
            evolution_dir=Path(evolution_dir) if evolution_dir is not None else None,
            mail_dir=None if str(db_path) == ":memory:" else Path(db_path).parent / MAIL_DIR,
            chat_media_dir=None
            if str(db_path) == ":memory:"
            else Path(db_path).parent / CHAT_MEDIA_DIR,
        )

    def sandbox(self) -> tuple[Sandbox | None, str | None]:
        """代码运行沙箱（第一次使用时按配置选择后端并缓存）。返回 (沙箱, 不可用原因)。"""
        if self.tools_sandbox is None:
            self.tools_sandbox = pick_sandbox(self.config.roundtable.tools.python)
        return self.tools_sandbox

    async def aclose(self) -> None:
        await self.router.aclose()
        await self.search.aclose()
        self.repo.conn.close()
