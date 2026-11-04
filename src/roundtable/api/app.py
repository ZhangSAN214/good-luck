"""FastAPI 应用：HTTP 接口 + SSE 实时推送。只调用 core.service。

启动：uvicorn roundtable.api.app:app --reload   （默认只监听本机）
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from roundtable.api.common import WEB_DIR, LanGate, WebFiles
from roundtable.api.routes import ROUTERS
from roundtable.core.service import LanAccess, RoundtableService, ServiceError, data_dir


def create_app(service: RoundtableService | None = None, lan: LanAccess | None = None) -> FastAPI:
    """service 为空时在启动时根据 .env 与配置构建（测试中传入使用 Fake 模型的服务）。

    lan 为空时：正式启动按环境变量 ROUNDTABLE_LAN 决定是否对局域网开放；测试中默认不开放。
    """
    if lan is None:
        lan = LanAccess.from_env(data_dir()) if service is None else LanAccess(None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if service is not None:
            app.state.service = service
            yield
            return
        from roundtable.core.runtime import Runtime

        app.state.service = RoundtableService(Runtime.build())
        app.state.service.reconcile()
        app.state.service.start_backup()
        app.state.service.start_life()
        app.state.service.start_evolution()
        app.state.service.start_dsh()
        app.state.service.start_butler()
        app.state.service.start_desk_weekly()
        app.state.service.start_gamemode()
        try:
            yield
        finally:
            await app.state.service.aclose()

    app = FastAPI(title="Roundtable", lifespan=lifespan)
    app.state.lan = lan
    app.add_middleware(LanGate, lan=lan)

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    for router in ROUTERS:  # 各部门的接口（api/routes/），按 ROUTERS 的顺序注册
        app.include_router(router)

    if WEB_DIR.is_dir():  # 静态前端（web/）；挂在最后，前面的接口优先匹配
        app.mount("/", WebFiles(directory=WEB_DIR, html=True), name="web")

    return app


app = create_app()
