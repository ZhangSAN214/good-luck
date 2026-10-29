"""把 dsh 工作台组装起来给服务层用：状态、启动、停止、余额检查与每月软上限。

服务层只通过这个类和 dsh 打交道；它不读写圆桌的数据库（只读一个「共用渠道花了多少」的回调）。
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from roundtable.core.config.schema import DshRules
from roundtable.core.dsh.catalog import CatalogError, DshCatalog, load_catalog
from roundtable.core.dsh.channel import BalanceError, ModelKey
from roundtable.core.dsh.overlay import write_overlay
from roundtable.core.dsh.paths import DshPaths
from roundtable.core.dsh.sidecar import Sidecar
from roundtable.core.dsh.usage import UsageLedger

EPS = 1e-9
HOST = "127.0.0.1"

SharedSpend = Callable[[Sequence[str], datetime, datetime], float]
SidecarFactory = Callable[..., Sidecar]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DshManager:
    def __init__(
        self,
        rules: DshRules,
        *,
        catalog_path: Path,
        root: Path | None,
        persona: Callable[[], str],
        shared_spend: SharedSpend | None = None,
        environ: Mapping[str, str] | None = None,
        dotenv_path: Path | str | None = None,
        now: Callable[[], datetime] = _utcnow,
        sidecar_factory: SidecarFactory = Sidecar,
        node: str | None = None,
        balance_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.rules = rules
        self.persona = persona
        self.shared_spend = shared_spend
        self.environ = environ
        self.now = now
        self.sidecar_factory = sidecar_factory
        self.node = node
        self.balance_transport = balance_transport
        self.catalog: DshCatalog | None = None
        self.catalog_error: str | None = None
        try:
            self.catalog = load_catalog(catalog_path)
        except CatalogError as exc:
            self.catalog_error = str(exc)
        package = self.catalog.runtime.package if self.catalog else ""
        # 内存数据库（测试）没有本机目录：工作台显示「不可用」
        self.paths = DshPaths(root, package) if root is not None else None
        self.key = ModelKey.load(rules.key_env, environ=environ, dotenv_path=dotenv_path)
        self.ledger = UsageLedger(self.paths.usage if self.paths else None)
        self.sidecar: Sidecar | None = None
        self.balance_error: str | None = None
        self.stopped_for_budget: str | None = None
        self._watch: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._jobs = 0  # 超级电脑派来的一次性活（headless），正在跑几件

    # --- 状态 -------------------------------------------------------------------

    @property
    def installed(self) -> bool:
        return bool(self.paths and self.catalog and self.paths.installed)

    @property
    def running(self) -> bool:
        return self.sidecar is not None and self.sidecar.state == "running"

    def budget(self) -> dict[str, Any]:
        now = self.now()
        st = self.ledger.state
        return {
            "spent_usd": round(self.ledger.spent(now), 4),
            "limit_usd": self.rules.monthly_usd,
            "balance": st.last_raw,
            "currency": st.currency,
            "last_check": st.last_check,
            "tracking": bool(self.rules.balance_url and self.key.present),
            "error": self.balance_error,
        }

    def unavailable_reason(self) -> str | None:
        if not self.rules.enabled:
            return "工作台在配置里关掉了（roundtable.yaml 的 dsh.enabled）"
        if self.catalog_error:
            return f"白名单配置有误：{self.catalog_error}"
        if self.paths is None:
            return "当前用的是内存数据库，没有本机目录，工作台不可用"
        if not self.paths.installed:
            return "还没装 dsh：在电脑上运行 python scripts/setup_dsh.py（需要 Node 和 pnpm）"
        if self._node() is None:
            return "找不到 Node：装好 Node 后重启圆桌"
        return None

    def status(self) -> dict[str, Any]:
        sc = self.sidecar
        state = sc.state if sc else "stopped"
        last = sc.last_exit if sc else None
        return {
            "enabled": self.rules.enabled,
            "installed": self.installed,
            "unavailable": self.unavailable_reason(),
            "runtime": self.catalog.runtime.version if self.catalog else None,
            "state": state,
            "url": sc.url if sc and state == "running" else None,
            "started_at": sc.started_at if sc and state != "stopped" else None,
            "last_exit": last.why if last and state == "stopped" else None,
            "stopped_for_budget": self.stopped_for_budget,
            "log": list(sc.log)[-30:] if sc else [],
            "key_env": self.rules.key_env,
            "key_present": self.key.present,
            "permission_mode": self.rules.permission_mode,
            "plugins": self.catalog.summary() if self.catalog else [],
            "budget": self.budget(),
        }

    # --- 启动与停止 ---------------------------------------------------------------

    def _node(self) -> str | None:
        return self.node or shutil.which("node")

    def argv(self) -> list[str]:
        assert self.paths is not None
        return [
            self._node() or "node",
            str(self.paths.bin_js),
            "--profile",
            "web",
            "--patch",
            str(self.paths.overlay),
            "--no-open",
            "--host",
            HOST,
            "--port",
            str(self.rules.port),
        ]

    def child_env(self) -> dict[str, str]:
        assert self.paths is not None
        extra = {
            "DSH_HOME": str(self.paths.home),
            "DSH_TELEMETRY_DISABLED": "1",
            "DSH_PERMISSION_MODE": self.rules.permission_mode,
        }
        return self.key.child_env(extra, self.environ)

    def over_budget(self) -> bool:
        return self.ledger.spent(self.now()) >= self.rules.monthly_usd - EPS

    async def start(self) -> dict[str, Any]:
        async with self._lock:
            reason = self.unavailable_reason()
            if reason:
                raise ValueError(reason)
            if self.running:
                return self.status()
            if self.over_budget():
                raise ValueError(
                    f"本月工作台已花约 ${self.ledger.spent(self.now()):.2f}，到了上限"
                    f" ${self.rules.monthly_usd:.2f}；下个月 1 号恢复，"
                    "或在 roundtable.yaml 调高 dsh.monthly_usd"
                )
            assert self.paths is not None
            self.paths.ensure()
            write_overlay(self.paths.overlay, self.persona())
            env = self.child_env()
            secrets = [env[self.rules.key_env]] if self.rules.key_env in env else []
            self.sidecar = self.sidecar_factory(
                self.argv(),
                cwd=self.paths.work,
                env=env,
                start_timeout_s=self.rules.start_timeout_s,
                secrets=secrets,
                clock=time.time,
            )
            self.stopped_for_budget = None
            try:
                await self.sidecar.start()
            except RuntimeError as exc:
                raise ValueError(str(exc)) from None
            await self._check_balance(rebase=self._jobs == 0)  # 有活在跑时接着记，不重置
            self._watch = asyncio.get_running_loop().create_task(self._watch_loop())
            return self.status()

    async def stop(self, why: str = "你停止了 dsh") -> dict[str, Any]:
        async with self._lock:
            await self._stop(why)
            return self.status()

    async def _stop(self, why: str) -> None:
        task, self._watch = self._watch, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self.sidecar is not None and self.sidecar.state != "stopped":
            await self._check_balance()  # 停之前把最后一段花费记上
            await self.sidecar.stop(why)

    async def aclose(self) -> None:
        await self.stop("圆桌关闭，dsh 也停了")

    # --- 超级电脑派来的一次性活（dsh headless） --------------------------------------

    def job_blocked(self) -> str | None:
        """不能接活的原因：没装、没 key、本月到了上限。"""
        reason = self.unavailable_reason()
        if reason:
            return reason
        if not self.key.present:
            return f"没有配置 {self.rules.key_env}，dsh 调不了模型"
        if self.over_budget():
            return f"本月 dsh 已花约 ${self.ledger.spent(self.now()):.2f}，到了上限"
        return None

    def job_launch(self) -> tuple[list[str], dict[str, str], list[str]]:
        """一次性活的启动参数：headless 方式，任务正文从标准输入给（最后的 ``-``）。"""
        assert self.paths is not None
        self.paths.ensure()
        write_overlay(self.paths.overlay, self.persona())
        argv = [
            self._node() or "node",
            str(self.paths.bin_js),
            "--profile",
            "headless",
            "--patch",
            str(self.paths.overlay),
            "-",
        ]
        extra = {
            "DSH_HOME": str(self.paths.home),
            "DSH_TELEMETRY_DISABLED": "1",
            "DSH_PERMISSION_MODE": self.rules.job_permission_mode,
        }
        env = self.key.child_env(extra, self.environ)
        secrets = [env[self.rules.key_env]] if self.rules.key_env in env else []
        return argv, env, secrets

    async def job_started(self) -> None:
        """活开始前：没有别的活、网页也没开时，先记一次余额当起点。"""
        if self._jobs == 0 and not self.running:
            await self._check_balance(rebase=True)
        self._jobs += 1

    async def job_finished(self) -> None:
        """活干完：记上这段花费；超了上限就停掉网页（之后的活也不再派给 dsh）。"""
        self._jobs = max(0, self._jobs - 1)
        await self._check_balance()
        async with self._lock:
            await self._enforce()

    # --- 余额与软上限 ---------------------------------------------------------------

    async def _check_balance(self, *, rebase: bool = False) -> None:
        if not (self.rules.balance_url and self.key.present):
            return
        try:
            bal = await self.key.balance(
                self.rules.balance_url, self.rules.usd_rates, transport=self.balance_transport
            )
        except BalanceError as exc:
            self.balance_error = str(exc)
            return
        self.balance_error = None
        now = self.now()
        if rebase:
            self.ledger.rebase(bal.amount, now, bal.currency, bal.raw)
            return
        shared = 0.0
        since = self.ledger.last_check()
        if self.shared_spend and self.rules.shared_channels and since is not None:
            shared = self.shared_spend(self.rules.shared_channels, since, now)
        self.ledger.observe(bal.amount, now, bal.currency, bal.raw, shared)

    async def check_now(self) -> dict[str, Any]:
        """手动刷新一次余额（只在 dsh 运行时记账）。"""
        if self.running:
            await self._check_balance()
            await self._enforce()
        return self.status()

    async def _enforce(self) -> None:
        if self.running and self.over_budget():
            why = (
                f"本月工作台已花约 ${self.ledger.spent(self.now()):.2f}，"
                f"超过上限 ${self.rules.monthly_usd:.2f}，已停止 dsh"
            )
            self.stopped_for_budget = why
            await self._stop(why)

    async def _watch_loop(self) -> None:
        interval = self.rules.balance_check_minutes * 60
        while self.running:
            await asyncio.sleep(interval)
            if not self.running:
                break
            await self._check_balance()
            async with self._lock:
                await self._enforce()
