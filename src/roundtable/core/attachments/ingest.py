"""接收上传：限大小 → 识别类型（扩展名 + 文件头）→ 提取文字 → 按哈希存放 → 登记。"""

from __future__ import annotations

import hashlib

from roundtable.core.allocation import eligible
from roundtable.core.config import AppConfig
from roundtable.core.providers import ChannelRouter
from roundtable.core.storage import Repository

from .detect import UploadError, detect, display_name
from .extract import extract
from .model import Attachment
from .store import FileStore

# 需要模型处理的类型 → 所需能力标签与说明
MODEL_TAGS = {"image": "vision", "audio": "transcribe"}
MISSING_MODEL = {
    "image": "没有可用的识图模型（需要带 vision 标签且有可用渠道的模型），无法上传图片",
    "audio": "没有可用的转写模型（需要带 transcribe 标签且有可用渠道的模型），无法上传音频",
}


def capable(router: ChannelRouter, tag: str) -> list:
    return eligible(router.available_models(), required_tags=[tag])


def ingest(
    name: str,
    data: bytes,
    *,
    config: AppConfig,
    router: ChannelRouter,
    repo: Repository,
    store: FileStore,
) -> Attachment:
    rules = config.roundtable.uploads
    name = display_name(name)
    limit = int(rules.max_file_mb * 1024 * 1024)
    if len(data) > limit:
        raise UploadError(f"文件「{name}」超过 {rules.max_file_mb:g} MB 上限")
    file_type = detect(name, data)
    tag = MODEL_TAGS.get(file_type.kind)
    if tag and not capable(router, tag):
        raise UploadError(MISSING_MODEL[file_type.kind])
    extracted = extract(file_type, data, rules)
    key = store.save(data, file_type.ext)
    attachment_id = repo.add_attachment(
        name=name,
        kind=file_type.kind,
        mime=file_type.mime,
        ext=file_type.ext,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_key=key,
        pages=extracted.pages if extracted else None,
        text=extracted.text if extracted else None,
        text_source="extract" if extracted else None,
        status="ready" if extracted else "pending",
        warnings=extracted.warnings if extracted else (),
    )
    return Attachment.from_row(repo.attachment(attachment_id))
