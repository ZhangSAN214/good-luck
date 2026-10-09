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
from roundtable.core.config import AppConfig, load_config
from roundtable.core.config.loader import DEFAULT_CONFIG_DIR
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import ChannelRouter, KeyRing, Provider, build_providers
from roundtable.core.search import SearchService
from roundtable.core.steps import check_pipelines
from roundtable.core.storage import Repository, connect
from roundtable.core.tools import Sandbox, pick_sandbox

PROJECT_ROOT = DEFAULT_CONFIG_DIR.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "roundtable.db"
DB_ENV = "ROUNDTABLE_DB_PATH"


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
        repo = Repository(connect(db_path))
        guard = BudgetGuard(config.roundtable.budget, repo, **({"now": now} if now else {}))
        return cls(
            config=config,
            router=router,
            prompts=prompts,
            repo=repo,
            scrubber=IdentityScrubber.from_config(config.models),
            budget=guard,
            files=FileStore(Path(uploads_dir) if uploads_dir is not None else None),
            search=search or SearchService({}),
            unavailable_channels=dict(unavailable or {}),
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
