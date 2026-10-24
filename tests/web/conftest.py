"""端到端测试：用 Fake 模型的服务起一个真实的 uvicorn，再用 Playwright（Chromium）操作页面。

没有安装 playwright 或浏览器时整个目录跳过。不联网：外部字体请求直接中止。
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import uvicorn  # noqa: E402

from roundtable.api.app import create_app  # noqa: E402
from roundtable.core.service import RoundtableService  # noqa: E402

from ..core.orchestrator.conftest import Env  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """在后台线程里运行服务。Env（含 SQLite 连接）在服务线程中创建。"""

    def __init__(self, delay: float = 0.0, **env_kw) -> None:
        self.delay = delay
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.env: Env | None = None
        self._ready = threading.Event()
        self._server: uvicorn.Server | None = None
        self._thread = threading.Thread(target=self._run, args=(env_kw,), daemon=True)

    def _run(self, env_kw) -> None:
        async def main() -> None:
            self.env = Env(**env_kw)
            self.env.rt.catalog.fetch = self._catalog  # 人才市场：不联网
            if self.delay:
                self._slow_down(self.env.fake)
            app = create_app(RoundtableService(self.env.rt))
            config = uvicorn.Config(app, port=self.port, log_level="warning", lifespan="on")
            self._server = uvicorn.Server(config)
            serve = asyncio.create_task(self._server.serve())
            while not self._server.started:
                await asyncio.sleep(0.01)
            self._ready.set()
            await serve

        asyncio.run(main())

    async def _catalog(self, url: str) -> dict:
        """聚合渠道的模型列表：b1 的价格和配置不一样，f5 找不到。"""
        self.catalog_calls = getattr(self, "catalog_calls", 0) + 1
        data = []
        for m in self.env.config.models.models:
            if m.id == "f5":
                continue
            out = m.price.output * (2 if m.id == "b1" else 1)
            data.append(
                {
                    "id": m.routes[0].model,
                    "created": 1,
                    "pricing": {"prompt": str(m.price.input / 1e6), "completion": str(out / 1e6)},
                }
            )
        return {"data": data}

    def _slow_down(self, fake) -> None:
        """每次模型调用前等一会儿，便于观察实时进度（发言高亮、当前步骤）。"""
        original = fake.complete

        async def complete(*args, **kwargs):
            await asyncio.sleep(self.delay)
            return await original(*args, **kwargs)

        fake.complete = complete

    def start(self) -> Server:
        self._thread.start()
        if not self._ready.wait(10):
            raise RuntimeError("服务没有启动")
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        self._thread.join(5)

    def identity_terms(self) -> set[str]:
        assert self.env is not None
        terms = set(self.env.config.models.channels)
        for m in self.env.config.models.models:
            terms |= {m.id, m.vendor, *m.aliases}
        return terms

    def anonymous_terms(self) -> set[str]:
        """匿名进行中界面上不能出现的名称：模型 id、厂商、别称、渠道，以及厂商昵称。"""
        assert self.env is not None
        return self.identity_terms() | set(self.env.config.personas.nicknames.values())


@pytest.fixture(scope="session")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            # ROUNDTABLE_CHROMIUM：指定已安装的 Chromium 可执行文件（与 playwright 版本不匹配时用）
            path = os.environ.get("ROUNDTABLE_CHROMIUM") or None
            b = p.chromium.launch(executable_path=path)
        except Exception as exc:  # noqa: BLE001 - 没有浏览器时跳过
            pytest.skip(f"无法启动 Chromium：{exc}")
        yield b
        b.close()


@pytest.fixture
def serve():
    servers: list[Server] = []

    def start(**env_kw) -> Server:
        s = Server(**env_kw).start()
        servers.append(s)
        return s

    yield start
    for s in servers:
        s.stop()


def quiet_boot(ctx) -> None:
    """不放开机动画（它会盖住首页）；测片头本身的测试先把模式设成别的再刷新。"""
    ctx.add_init_script(
        "try { if (!localStorage.getItem('roundtable.boot.mode'))"
        " localStorage.setItem('roundtable.boot.mode', 'off') } catch {}"
    )


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    # 这些端到端测试多数针对讨论模式：预先记住"讨论模式"（办公室模式的测试会清掉它）
    ctx.add_init_script(
        "try { if (!localStorage.getItem('roundtable.workflow'))"
        " localStorage.setItem('roundtable.workflow', 'discussion') } catch {}"
    )
    quiet_boot(ctx)
    pg = ctx.new_page()
    pg.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    pg.route("https://fonts.gstatic.com/**", lambda r: r.abort())
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.js_errors = errors
    yield pg
    ctx.close()
    assert errors == [], errors


def wait_until(cond, timeout=10.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("等待超时")
