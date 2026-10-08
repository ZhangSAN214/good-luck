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
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import (
    AllChannelsFailed,
    ChannelRouter,
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
            completion = await router.complete(model.id, messages, {"max_tokens": max_tokens})
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
