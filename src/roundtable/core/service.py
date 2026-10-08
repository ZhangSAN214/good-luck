"""对外 facade：Web 服务（以及以后的其他界面）只通过这里访问核心逻辑。

- 所有返回值都是可直接转成 JSON 的 dict / list；揭晓前不含任何模型身份。
- 讨论在后台任务中执行，提交后立即返回会话 id；进度通过事件订阅获得。
- 每当讨论完成、失败、停止或需要用户确认时，会发出一个 "state" 事件。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict
from typing import Any

from roundtable.core.orchestrator import Orchestrator, OrchestratorError, RunResult
from roundtable.core.routing import Question, RoutingError, UserChoice
from roundtable.core.runtime import Runtime
from roundtable.core.steps import Event
from roundtable.core.storage import NotFound

log = logging.getLogger(__name__)

# 讨论停下来（结束或等待用户）的状态：此时推送流会关闭
RESTING = frozenset({"completed", "stopped", "failed", "awaiting_confirmation", "paused"})
REVEALABLE = frozenset({"completed", "stopped", "failed"})
MAX_QUESTION_CHARS = 20_000

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

    async def aclose(self) -> None:
        for task in list(self._tasks.values()):
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
                }
                for m in cfg.models.models
                if m.enabled
            ],
            "plans": {name: p.label for name, p in cfg.routing.plans.items()},
            "presets": {name: p.label for name, p in cfg.routing.presets.items()},
            "confirm_threshold_usd": cfg.routing.confirm_threshold_usd,
            "max_members": cfg.roundtable.seats,
            "code_prefix": cfg.personas.code_prefix,
            "budget": self.budget(),
        }

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

    # --- 会话 ----------------------------------------------------------------------

    def create(
        self,
        question: str,
        *,
        mode: str = "auto",
        preset: str | None = None,
        members: Sequence[str] = (),
        coordinator: str | None = None,
        seed: int | None = None,
    ) -> str:
        """创建会话并在后台开始执行，立即返回会话 id。"""
        text = question.strip()
        if not text:
            raise ServiceError("题目不能为空")
        if len(text) > MAX_QUESTION_CHARS:
            raise ServiceError(f"题目过长（超过 {MAX_QUESTION_CHARS} 字）")
        try:
            choice = UserChoice(mode, preset, tuple(members), coordinator)  # type: ignore[arg-type]
        except (RoutingError, ValueError) as exc:
            raise ServiceError(str(exc)) from None
        if not self.rt.router.available_models():
            raise ServiceError("没有可用的模型：请在 .env 中填写至少一个渠道的 key", 503)
        sid = self.orc.open(Question(text), choice, seed=seed)
        self._spawn(sid, self.orc.run(sid))
        return sid

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
        self._spawn(session_id, self.orc.respond(session_id, response, note))

    def resume(self, session_id: str) -> None:
        self._require(session_id)
        if not self.running(session_id):
            self._spawn(session_id, self.orc.resume(session_id))

    def reveal_identities(self, session_id: str) -> dict[str, Any]:
        """揭晓身份：只允许在讨论结束后。"""
        row = self._require(session_id)
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
        data["can_reveal"] = view.status in REVEALABLE and not view.revealed
        return data

    def sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        return [asdict(s) for s in self.rt.repo.list_sessions(limit)]

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
