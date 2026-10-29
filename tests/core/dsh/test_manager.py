from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from roundtable.core.config.schema import DshRules
from roundtable.core.dsh.manager import DshManager
from roundtable.core.dsh.sidecar import Sidecar

REPO_CATALOG = Path(__file__).resolve().parents[3] / "config" / "dsh_plugins.yaml"
KEY = "sk-" + "dsh" * 8


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t


class Balances:
    """余额接口：按顺序返回这些美元余额。"""

    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        v = self.values.pop(0) if len(self.values) > 1 else self.values[0]
        return httpx.Response(
            200, json={"balance_infos": [{"currency": "USD", "total_balance": str(v)}]}
        )


def build(tmp_path, fake_dsh, *, balances=None, installed=True, monthly=3.0, shared=None, **rules):
    root = tmp_path / "dsh"
    mgr_rules = DshRules(
        key_env="DSH_TEST_KEY",
        balance_url="https://balance.test/user/balance",
        monthly_usd=monthly,
        shared_channels=["direct"] if shared else [],
        **rules,
    )
    seen = {}

    def factory(argv, **kw):
        seen["argv"], seen["env"] = argv, kw["env"]
        return Sidecar(fake_dsh, **{**kw, "start_timeout_s": 20})

    mgr = DshManager(
        mgr_rules,
        catalog_path=REPO_CATALOG,
        root=root,
        persona=lambda: "你是工作台助手。",
        shared_spend=shared,
        environ={
            "DSH_TEST_KEY": KEY,
            "OPENROUTER_API_KEY": "sk-or-" + "x" * 20,
            "PATH": "/usr/bin:/bin",
        },
        dotenv_path=None,
        now=Clock(),
        sidecar_factory=factory,
        node="node",
        balance_transport=httpx.MockTransport(balances or Balances(10.0)),
    )
    if installed:
        mgr.paths.bin_js.parent.mkdir(parents=True)
        mgr.paths.bin_js.write_text("// fake", encoding="utf-8")
    return mgr, seen


def test_status_before_install(tmp_path, fake_dsh):
    mgr, _ = build(tmp_path, fake_dsh, installed=False)
    st = mgr.status()
    assert st["state"] == "stopped" and not st["installed"]
    assert "setup_dsh.py" in st["unavailable"]
    assert st["key_present"] and KEY not in repr(st)
    assert [p["id"] for p in st["plugins"]] == ["worktable"]


async def test_start_refuses_when_not_installed(tmp_path, fake_dsh):
    mgr, _ = build(tmp_path, fake_dsh, installed=False)
    with pytest.raises(ValueError, match="还没装 dsh"):
        await mgr.start()


def test_memory_db_has_no_workbench():
    mgr = DshManager(DshRules(), catalog_path=REPO_CATALOG, root=None, persona=str)
    assert "内存数据库" in mgr.status()["unavailable"]


def test_disabled_in_config(tmp_path, fake_dsh):
    mgr, _ = build(tmp_path, fake_dsh, enabled=False)
    assert "dsh.enabled" in mgr.status()["unavailable"]


async def test_start_and_stop(tmp_path, fake_dsh):
    mgr, seen = build(tmp_path, fake_dsh, balances=Balances(10.0, 9.4))
    st = await mgr.start()
    try:
        assert st["state"] == "running"
        assert st["url"].startswith("http://127.0.0.1:")
        # 启动参数：只监听本机、叠加配置层、不自己开浏览器
        argv = seen["argv"]
        assert argv[argv.index("--host") + 1] == "127.0.0.1"
        assert argv[argv.index("--patch") + 1] == str(mgr.paths.overlay)
        assert "--no-open" in argv
        # 子进程环境：只给 dsh 的 key，不给圆桌的其他 key；遥测关掉、默认只读
        env = seen["env"]
        assert env["DSH_TEST_KEY"] == KEY and "OPENROUTER_API_KEY" not in env
        assert env["DSH_TELEMETRY_DISABLED"] == "1" and env["DSH_PERMISSION_MODE"] == "read-only"
        assert env["DSH_HOME"] == str(mgr.paths.home)
        # 叠加配置层写好了：关上传、中立身份
        rows = {r["id"]: r for r in yaml.safe_load(mgr.paths.overlay.read_text(encoding="utf-8"))}
        assert rows["session-log-deepseek"]["config"]["enabled"] is False
        assert rows["system-prompt"]["config"]["personaPrefix"] == "你是工作台助手。"
        # 数据都在 data/dsh/ 下
        assert mgr.paths.home.is_dir() and mgr.paths.work.is_dir()
    finally:
        st = await mgr.stop()
    assert st["state"] == "stopped" and st["url"] is None
    # 停之前记上最后一段：10.0 → 9.4
    assert st["budget"]["spent_usd"] == pytest.approx(0.6)


async def test_budget_cap_stops_dsh(tmp_path, fake_dsh):
    mgr, _ = build(tmp_path, fake_dsh, balances=Balances(10.0, 8.0), monthly=1.0)
    await mgr.start()
    st = await mgr.check_now()
    assert st["state"] == "stopped"
    assert "超过上限" in st["stopped_for_budget"]
    # 到了上限就不让再启动
    with pytest.raises(ValueError, match="到了上限"):
        await mgr.start()


async def test_shared_channel_spend_is_subtracted(tmp_path, fake_dsh):
    calls = []

    def shared(channels, start, end):
        calls.append((tuple(channels), start <= end))
        return 0.5

    mgr, _ = build(tmp_path, fake_dsh, balances=Balances(10.0, 9.0), shared=shared)
    await mgr.start()
    try:
        st = await mgr.check_now()
        assert st["budget"]["spent_usd"] == pytest.approx(0.5)
        assert calls == [(("direct",), True)]
    finally:
        await mgr.stop()


async def test_balance_failure_does_not_block(tmp_path, fake_dsh):
    def down(request):
        return httpx.Response(503, text="busy")

    mgr, _ = build(tmp_path, fake_dsh, balances=down)
    st = await mgr.start()
    try:
        assert st["state"] == "running"
        assert "503" in st["budget"]["error"]
    finally:
        await mgr.stop()


async def test_bad_catalog_makes_workbench_unavailable(tmp_path, fake_dsh):
    bad = tmp_path / "dsh_plugins.yaml"
    bad.write_text("runtime: {}\n", encoding="utf-8")
    mgr = DshManager(DshRules(), catalog_path=bad, root=tmp_path / "dsh", persona=str)
    assert "白名单配置有误" in mgr.status()["unavailable"]


async def test_headless_jobs_for_supercomputer(tmp_path, fake_dsh):
    """超级电脑派来的一次性活：headless 启动参数、写共享文件夹的权限、花费计入月上限。"""
    mgr, _ = build(tmp_path, fake_dsh, balances=Balances(10.0, 9.0, 9.0), monthly=0.5)
    assert mgr.job_blocked() is None
    argv, env, secrets = mgr.job_launch()
    assert argv[2:4] == ["--profile", "headless"] and argv[-1] == "-"
    assert argv[argv.index("--patch") + 1] == str(mgr.paths.overlay)
    assert env["DSH_PERMISSION_MODE"] == "workspace-write" and env["DSH_TEST_KEY"] == KEY
    assert "OPENROUTER_API_KEY" not in env and secrets == [KEY]
    await mgr.job_started()  # 记起点 10.0
    await mgr.job_finished()  # 9.0：花了 1.0，超过 0.5
    assert mgr.budget()["spent_usd"] == pytest.approx(1.0)
    assert "到了上限" in mgr.job_blocked()


def test_job_blocked_without_key(tmp_path, fake_dsh):
    mgr, _ = build(tmp_path, fake_dsh)
    mgr.key = type(mgr.key)("DSH_TEST_KEY", None)
    assert "DSH_TEST_KEY" in mgr.job_blocked()
