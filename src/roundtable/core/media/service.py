"""媒体生成服务：图片、语音（同步）与视频（异步：提交 → 轮询 → 下载）。

- 模型只按能力标签和档位选择；提示词发出前遮蔽身份。
- 每次生成在 media_jobs 表里一行（提示词、模型、渠道、状态、花费、第几轮）；成功的生成同时记入
  calls 表（role = media），所以进入每月 / 每日预算和按渠道花费。
- 视频任务的外部 id 存库：暂停或重启后继续轮询同一个任务，已完成的任务不会重复提交 / 计费。
- 提交失败或任务失败 / 超时时，按 submit_retries 重新提交（attempt 递增）。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from roundtable.core.allocation import IdentityScrubber
from roundtable.core.attachments import FileStore
from roundtable.core.config import AppConfig, ModelSpec, Route
from roundtable.core.providers import (
    VIDEO_FINAL,
    AllChannelsFailed,
    ChannelRouter,
    Completion,
    Media,
    Message,
    NoChannelAvailable,
    ProviderError,
    VideoJob,
    estimate_cost,
    image_cost,
)
from roundtable.core.providers.errors import ErrorKind
from roundtable.core.storage import Repository

from .pricing import KIND_LABELS, estimate_generation, unit_cost
from .select import pick_media_model

log = logging.getLogger(__name__)

EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/ogg": "ogg",
    "video/mp4": "mp4",
    "video/webm": "webm",
}
FILE_KIND = {"image": "image", "speech": "audio", "video": "video"}
ACTIVE = ("submitted", "pending", "running")
MAX_POLL_ERRORS = 5

Emit = Callable[..., None]


@dataclass(frozen=True)
class Placement:
    """一次生成在流程中的位置。同一位置重复请求时返回已有结果（恢复时不重复生成 / 计费）。"""

    table_no: int
    step: str
    round_no: int = 1
    code: str | None = None
    subtask: str | None = None


@dataclass(frozen=True)
class MediaResult:
    ok: bool
    kind: str
    job_id: int | None = None
    file_ids: tuple[str, ...] = ()
    model_id: str | None = None
    cost_usd: float = 0.0
    error: str | None = None
    attempts: int = 1
    paths: tuple[str, ...] = field(default=())

    @property
    def file_id(self) -> str | None:
        return self.file_ids[0] if self.file_ids else None


class MediaService:
    def __init__(
        self,
        *,
        session_id: str,
        config: AppConfig,
        router: ChannelRouter,
        repo: Repository,
        store: FileStore,
        scrubber: IdentityScrubber,
        seed: int | str = 0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        wall_clock: Callable[[], float] = time.time,
        emit: Emit | None = None,
    ) -> None:
        self.session_id = session_id
        self.config, self.router, self.repo = config, router, repo
        self.store, self.scrubber = store, scrubber
        self.rules = config.roundtable.media
        self.seed = seed
        self._sleep, self._wall = sleep, wall_clock
        self._emit = emit

    # --- 选择与预估 ----------------------------------------------------------------

    def model_for(self, kind: str, tier: str | None = None) -> ModelSpec | None:
        """本场某种媒体使用的模型：同一场、同一档位、同一种类总是同一个（按 seed 派生）。"""
        if not self.rules.enabled:
            return None
        tier = tier or self.rules.default_tier
        rng = random.Random(f"{self.seed}:media:{kind}:{tier}")
        return pick_media_model(self.router, kind, tier, rng)

    def unavailable_reason(self, kind: str, tier: str | None = None) -> str | None:
        if not self.rules.enabled:
            return "媒体生成已在配置中关闭"
        if self.model_for(kind, tier) is None:
            return f"没有可用的{KIND_LABELS[kind]}生成模型（需要带对应能力标签、有可用渠道的模型）"
        return None

    def estimate(self, kind: str, tier: str | None = None, *, chars: int = 0) -> float:
        model = self.model_for(kind, tier)
        return estimate_generation(model, kind, self.rules, chars=chars) if model else 0.0

    # --- 生成 ----------------------------------------------------------------------

    async def generate(
        self, kind: str, prompt: str, where: Placement, *, tier: str | None = None
    ) -> MediaResult:
        existing = self._jobs(where)
        last = existing[-1] if existing else None
        if last and last["state"] == "completed" and last["file_id"]:
            return self._result(last, len(existing))
        model = self.model_for(kind, tier)
        if model is None:
            return MediaResult(False, kind, error=self.unavailable_reason(kind, tier))
        text = self.scrubber.scrub(prompt).strip()
        if kind == "speech":
            text = text[: self.rules.speech.max_chars]
        else:
            text = text[: self.rules.prompt_max_chars]
        if not text:
            return MediaResult(False, kind, error="生成提示词为空")
        if kind == "video":
            return await self._video(model, text, where, existing)
        return await self._sync(kind, model, text, where, existing)

    def _jobs(self, where: Placement) -> list[dict[str, Any]]:
        return [
            j
            for j in self.repo.media_jobs(self.session_id)
            if (j["table_no"], j["step"], j["round"], j["code"], j["subtask"])
            == (where.table_no, where.step, where.round_no, where.code, where.subtask)
        ]

    def _result(self, job: dict[str, Any], attempts: int = 1) -> MediaResult:
        files = [job["file_id"], *job["params"].get("extra_files", [])]
        paths = tuple(self.repo.file(self.session_id, f)["path"] for f in files if f)
        return MediaResult(
            True,
            job["kind"],
            job["id"],
            tuple(f for f in files if f),
            job["model_id"],
            job["cost_usd"],
            None,
            attempts,
            paths,
        )

    def _new_job(
        self, kind: str, model: ModelSpec, text: str, where: Placement, attempt: int
    ) -> int:
        return self.repo.create_media_job(
            self.session_id,
            table_no=where.table_no,
            step=where.step,
            code=where.code,
            subtask=where.subtask,
            round=where.round_no,
            attempt=attempt,
            kind=kind,
            model_id=model.id,
            prompt=text,
        )

    def _fail(
        self,
        job_id: int,
        kind: str,
        error: str,
        state: str = "failed",
        where: Placement | None = None,
    ) -> None:
        self.repo.update_media_job(job_id, state=state, error=error)
        if where is not None:
            self._event("media_failed", where, kind=kind)

    def _event(self, type_: str, where: Placement, **data: Any) -> None:
        if self._emit:
            self._emit(type_, where.step, where.code, round=where.round_no, **data)

    # --- 图片、语音（同步）---------------------------------------------------------------

    async def _sync(
        self,
        kind: str,
        model: ModelSpec,
        text: str,
        where: Placement,
        existing: list[dict[str, Any]],
    ) -> MediaResult:
        job_id = self._new_job(kind, model, text, where, len(existing) + 1)
        self._event("media_started", where, kind=kind)
        style = {
            "image": lambda prov, route, m: prov.generate_image(
                route.model, text, dict(m.params_for(route))
            ),
            "speech": lambda prov, route, m: prov.synthesize_speech(
                route.model,
                text,
                {
                    "voice": self.rules.speech.voice,
                    "response_format": self.rules.speech.format,
                    **m.params_for(route),
                },
            ),
        }[kind]
        try:
            inv = await self.router.invoke(model.id, style)
        except (AllChannelsFailed, NoChannelAvailable) as exc:
            self._fail(job_id, kind, "生成失败（所有渠道都不可用）", where=where)
            self._record_failure(model, text, where, kind, exc)
            return MediaResult(False, kind, job_id, model_id=model.id, error="生成失败")
        out = inv.result
        if kind == "image":
            medias: list[Media] = list(out.images)[: self.rules.images_per_round]
            reported, tokens = out.cost_usd, (out.input_tokens, out.output_tokens)
            fallback = image_cost(
                model, inv.route, len(medias), out.input_tokens, out.output_tokens
            )
        else:
            medias = [out.media]
            reported, tokens = out.cost_usd, (0, 0)
            fallback = unit_cost(model, inv.route, chars=len(text))
        if not medias:
            self._fail(job_id, kind, "模型没有返回内容", where=where)
            return MediaResult(False, kind, job_id, model_id=model.id, error="模型没有返回内容")
        if reported is not None:
            cost, source = reported, "reported"
        elif fallback is not None:
            cost, source = fallback, "estimated"
        else:
            cost, source = estimate_cost(model.price_for(inv.route), *tokens), "estimated"
        file_ids = [self._store(m, kind, where, i) for i, m in enumerate(medias, 1)]
        self.repo.update_media_job(
            job_id,
            state="completed",
            channel=inv.channel,
            route_model=inv.route.model,
            cost_usd=cost,
            file_id=file_ids[0],
            params={"extra_files": file_ids[1:]},
        )
        self._record_call(model, text, where, inv, cost, source, tokens)
        self._event("media_done", where, kind=kind)
        done = next(j for j in self.repo.media_jobs(self.session_id) if j["id"] == job_id)
        return self._result(done, len(existing) + 1)

    # --- 视频（异步任务）---------------------------------------------------------------

    async def _video(
        self, model: ModelSpec, text: str, where: Placement, existing: list[dict[str, Any]]
    ) -> MediaResult:
        rules = self.rules.video
        max_attempts = rules.submit_retries + 1
        jobs = list(existing)
        error = "生成失败"
        while True:
            last = jobs[-1] if jobs else None
            if last and last["state"] == "completed" and last["file_id"]:
                return self._result(last, len(jobs))
            if last is None or last["state"] in ("failed", "timeout"):
                if last is not None:
                    error = last["error"] or error
                if len(jobs) >= max_attempts:
                    return MediaResult(
                        False, "video", last["id"] if last else None, model_id=model.id,
                        error=error, attempts=len(jobs),
                    )  # fmt: skip
                job_id = self._new_job("video", model, text, where, len(jobs) + 1)
                self._event("media_started", where, kind="video")
                if not await self._submit(model, text, job_id, where):
                    jobs = self._jobs(where)
                    continue
            elif not last["external_id"]:
                # 提交到一半中断：没有任务 id，无法找回，记为失败后重新提交
                self._fail(last["id"], "video", "提交中断，没有收到任务 id", where=where)
                jobs = self._jobs(where)
                continue
            jobs = self._jobs(where)
            row = jobs[-1]
            state = await self._poll(model, row, where)
            jobs = self._jobs(where)
            if state != "completed":
                continue
            done = await self._finish_video(model, jobs[-1], where, text)
            if done is not None:
                return done
            jobs = self._jobs(where)

    async def _submit(self, model: ModelSpec, text: str, job_id: int, where: Placement) -> bool:
        rules = self.rules.video
        base = {
            "duration": rules.duration_s,
            "resolution": rules.resolution,
            "aspect_ratio": rules.aspect_ratio,
        }
        try:
            inv = await self.router.invoke(
                model.id,
                lambda prov, route, m: prov.submit_video(
                    route.model, text, {**base, **m.params_for(route)}
                ),
            )
        except (AllChannelsFailed, NoChannelAvailable):
            self._fail(job_id, "video", "提交失败（所有渠道都不可用）", where=where)
            return False
        job: VideoJob = inv.result
        self.repo.update_media_job(
            job_id,
            channel=inv.channel,
            route_model=inv.route.model,
            external_id=job.job_id,
            polling_url=job.polling_url,
            state="pending" if job.state == "pending" else "running",
            submitted_at=self._wall(),
        )
        return True

    async def _poll(self, model: ModelSpec, row: dict[str, Any], where: Placement) -> str:
        """轮询直到任务结束；返回 completed / failed / timeout。结果写入 media_jobs。"""
        rules = self.rules.video
        provider = self.router.provider(row["channel"])
        job = VideoJob(row["external_id"], polling_url=row["polling_url"])
        errors = 0
        while True:
            if self._wall() - (row["submitted_at"] or self._wall()) > rules.timeout_s:
                self._fail(
                    row["id"],
                    "video",
                    f"等待超过 {rules.timeout_s:g} 秒仍未完成",
                    "timeout",
                    where,
                )
                return "timeout"
            try:
                job = await provider.poll_video(row["route_model"], job)
            except ProviderError as exc:
                errors += 1
                fatal = exc.kind in (ErrorKind.AUTH, ErrorKind.QUOTA, ErrorKind.NOT_FOUND)
                if fatal or errors >= MAX_POLL_ERRORS:
                    self._fail(row["id"], "video", "查询任务状态失败", where=where)
                    return "failed"
            else:
                errors = 0
                if job.state in VIDEO_FINAL:
                    if job.state == "completed":
                        self.repo.update_media_job(
                            row["id"],
                            state="completed",
                            cost_usd=job.cost_usd or 0.0,
                            params={**row["params"], "content_urls": list(job.content_urls)},
                        )
                        return "completed"
                    self._fail(row["id"], "video", job.error or f"任务{job.state}", where=where)
                    return "failed"
                state = "pending" if job.state == "pending" else "running"
                if state != row["state"]:
                    self.repo.update_media_job(row["id"], state=state)
                    row = {**row, "state": state}
                    self._event("media_progress", where, kind="video", state=state)
            await self._sleep(rules.poll_interval_s)

    async def _finish_video(
        self, model: ModelSpec, row: dict[str, Any], where: Placement, text: str
    ) -> MediaResult | None:
        """下载完成的视频并登记；下载失败时记为失败（返回 None，由外层决定是否重试）。"""
        provider = self.router.provider(row["channel"])
        route = next((r for r in model.routes if r.channel == row["channel"]), model.routes[0])
        reported = row["cost_usd"] or None
        try:
            media = await provider.fetch_video(
                row["route_model"], VideoJob(row["external_id"], "completed", row["polling_url"])
            )
        except ProviderError:
            self._fail(row["id"], "video", "下载视频失败", where=where)
            return None
        if len(media.data) > self.rules.video.max_mb * 2**20:
            self._fail(
                row["id"], "video", f"视频超过 {self.rules.video.max_mb:g} MB 上限", where=where
            )
            return None
        file_id = self._store(media, "video", where, 1)
        if reported is not None:
            cost, source = reported, "reported"
        else:
            cost = unit_cost(model, route, seconds=self.rules.video.duration_s) or 0.0
            source = "estimated"
        self.repo.update_media_job(row["id"], file_id=file_id, cost_usd=cost)
        inv = _Charged(route, row["channel"], self.config.models.channels[row["channel"]].kind)
        self._record_call(model, text, where, inv, cost, source, (0, 0))
        self._event("media_done", where, kind="video")
        return self._result({**row, "file_id": file_id, "cost_usd": cost}, row["attempt"])

    # --- 存储与记账 -----------------------------------------------------------------

    def _store(self, media: Media, kind: str, where: Placement, n: int) -> str:
        ext = EXT.get(media.mime) or media.mime.split("/")[-1][:5]
        stem = {"image": "image", "speech": "speech", "video": "video"}[kind]
        suffix = f"_{n}" if n > 1 else ""
        path = f"{stem}_r{where.round_no}{suffix}.{ext}"
        if where.subtask:
            path = f"{where.subtask}_{path}"
        key = self.store.save(media.data, ext)
        return self.repo.add_file(
            self.session_id,
            table_no=where.table_no,
            step=where.step,
            code=where.code,
            path=path,
            kind=FILE_KIND[kind],
            mime=media.mime,
            size=len(media.data),
            sha256=hashlib.sha256(media.data).hexdigest(),
            storage_key=key,
        )

    def _record_call(self, model, text, where, inv, cost, source, tokens) -> None:
        completion = Completion(
            text="",
            model_id=model.id,
            channel=inv.channel,
            channel_kind=inv.channel_kind,
            route_model=inv.route.model,
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            cached_tokens=0,
            cost_usd=cost,
            cost_source=source,  # type: ignore[arg-type]
            latency_s=getattr(inv, "latency_s", 0.0),
            attempts=getattr(inv, "attempts", ()),
        )
        self.repo.record_call(
            self.session_id,
            step=where.step,
            role="media",
            model_id=model.id,
            messages=[Message("user", text)],
            table_no=where.table_no,
            code=where.code,
            completion=completion,
        )

    def _record_failure(self, model, text, where, kind, exc) -> None:
        self.repo.record_call(
            self.session_id,
            step=where.step,
            role="media",
            model_id=model.id,
            messages=[Message("user", text)],
            table_no=where.table_no,
            code=where.code,
            failure=exc,
        )


@dataclass(frozen=True)
class _Charged:
    """视频计费记录用的渠道信息（提交时的渠道）。"""

    route: Route
    channel: str
    channel_kind: str
