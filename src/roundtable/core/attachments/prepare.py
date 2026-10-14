"""需要模型处理的附件：图片 → 文字版（最便宜的 vision 模型），
音频 → 转写稿（最便宜的 transcribe 模型）。

每个文件只处理一次，结果入库，费用记在本场（calls 表中 step="attachments"、role="preprocess"）。
模型生成的文字转给其他模型前做身份遮蔽。
"""

from __future__ import annotations

import logging
import random

from roundtable.core.allocation import IdentityScrubber, cheapest
from roundtable.core.config import AppConfig
from roundtable.core.media import pick_stt_model, unit_cost
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import (
    AllChannelsFailed,
    ChannelRouter,
    Completion,
    Media,
    Message,
    NoChannelAvailable,
)
from roundtable.core.storage import Repository

from .ingest import MODEL_TAGS, capable
from .model import Attachment
from .store import FileStore

log = logging.getLogger(__name__)

STEP = "attachments"
ROLES = {"image": ("describe_image", "vision"), "audio": ("transcribe", "transcribe")}


async def prepare_attachments(
    session_id: str,
    *,
    config: AppConfig,
    router: ChannelRouter,
    prompts: PromptLibrary,
    repo: Repository,
    store: FileStore,
    scrubber: IdentityScrubber,
    question: str,
    rng: random.Random,
) -> list[Attachment]:
    """处理本场所有待处理的附件，返回处理后的全部附件（失败的带 error）。"""
    rules = config.roundtable.uploads
    for row in repo.session_attachments(session_id):
        a = Attachment.from_row(row)
        if a.status != "pending" or a.kind not in MODEL_TAGS:
            continue
        role, source = ROLES[a.kind]
        if a.kind == "audio" and await _transcribe_stt(
            session_id, a, config, router, repo, store, scrubber, question, rng
        ):
            continue
        pool = capable(router, MODEL_TAGS[a.kind])
        if not pool:
            repo.update_attachment(a.id, status="failed", error="没有可用的模型处理这个附件")
            continue
        max_tokens = rules.describe_max_tokens if a.kind == "image" else rules.transcribe_max_tokens
        model = cheapest(pool, rng, input_tokens=1500, output_tokens=max_tokens // 3)
        rendered = prompts.render(role, config.roundtable.prompts[role], name=a.name)
        media = Media(a.kind, a.mime, store.load(a.storage_key), a.name)  # type: ignore[arg-type]
        messages = tuple(
            Message(m.role, m.content, (media,)) if m.role == "user" else m
            for m in rendered.messages
        )
        try:
            completion = await router.complete(
                model.id, messages, {"max_tokens": max_tokens, "reasoning": {"effort": "low"}}
            )
        except (AllChannelsFailed, NoChannelAvailable) as exc:
            repo.record_call(
                session_id,
                step=STEP,
                role="preprocess",
                model_id=model.id,
                messages=messages,
                prompt=rendered,
                failure=exc,
            )
            repo.update_attachment(a.id, status="failed", error="处理附件的模型调用失败")
            log.warning("附件预处理失败（%s）", a.kind)
            continue
        repo.record_call(
            session_id,
            step=STEP,
            role="preprocess",
            model_id=model.id,
            messages=messages,
            prompt=rendered,
            completion=completion,
        )
        text = completion.text.strip()
        if not text:
            repo.update_attachment(a.id, status="failed", error="模型没有给出内容")
            continue
        if completion.truncated:
            text += "\n……（输出达到长度上限，以下内容缺失）"
        repo.update_attachment(
            a.id, status="ready", text=scrubber.scrub(text, question), text_source=source
        )
    return [Attachment.from_row(r) for r in repo.session_attachments(session_id)]


# 没有时长信息时按 128 kbps 的 MP3 估计（字节 / 秒）
BYTES_PER_SECOND = 16000


async def _transcribe_stt(
    session_id, a: Attachment, config, router, repo, store, scrubber, question, rng
) -> bool:
    """用 stt 模型（Whisper 系列）转写音频。成功返回 True；没有可用的 stt 模型或调用失败返回 False
    （由旧的 transcribe 标签对话模型接着处理）。费用按渠道返回的实际费用，否则按每分钟单价。"""
    model = pick_stt_model(router, rng)
    if model is None:
        return False
    media = Media("audio", a.mime, store.load(a.storage_key), a.name)
    try:
        inv = await router.invoke(
            model.id,
            lambda prov, route, m: prov.transcribe(route.model, media, m.params_for(route)),
        )
    except (AllChannelsFailed, NoChannelAvailable) as exc:
        repo.record_call(
            session_id,
            step=STEP,
            role="preprocess",
            model_id=model.id,
            messages=[Message("user", a.name)],
            failure=exc,
        )
        log.warning("音频转写（stt）失败，改用对话模型")
        return False
    out = inv.result
    seconds = out.seconds if out.seconds is not None else len(media.data) / BYTES_PER_SECOND
    if out.cost_usd is not None:
        cost, source = out.cost_usd, "reported"
    else:
        cost, source = unit_cost(model, inv.route, seconds=seconds) or 0.0, "estimated"
    completion = Completion(
        text=out.text,
        model_id=model.id,
        channel=inv.channel,
        channel_kind=inv.channel_kind,
        route_model=inv.route.model,
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        cost_usd=cost,
        cost_source=source,  # type: ignore[arg-type]
        latency_s=inv.latency_s,
        attempts=inv.attempts,
    )
    repo.record_call(
        session_id,
        step=STEP,
        role="preprocess",
        model_id=model.id,
        messages=[Message("user", a.name, (media,))],
        completion=completion,
    )
    text = out.text.strip()
    if not text:
        repo.update_attachment(a.id, status="failed", error="模型没有给出内容")
        return True
    repo.update_attachment(
        a.id, status="ready", text=scrubber.scrub(text, question), text_source="stt"
    )
    return True
