"""附件的数据结构与呈现方式（发给模型时的标签块与随附图片）。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from roundtable.core.prompts import PromptTemplate
from roundtable.core.providers import Media, Message

# 附件内容中出现这些结束标签时打断，防止提前闭合外层标签（提示注入）
_TAGS = ("attachment", "question", "answer", "review", "work", "subtask")
IMAGE_HINT = "见随附图片"  # 与 prompts/attachments 的说明对应


@dataclass(frozen=True)
class Attachment:
    id: str
    name: str
    kind: str  # image / pdf / docx / text / audio
    mime: str
    ext: str
    size: int
    storage_key: str
    status: str  # ready / pending / failed
    text: str | None = None
    text_source: str | None = None  # extract / vision / transcribe
    pages: int | None = None
    error: str | None = None
    warnings: tuple[str, ...] = ()
    data: bytes | None = field(default=None, repr=False)  # 图片原图（发给 vision 成员）

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Attachment:
        return cls(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            mime=row["mime"],
            ext=row["ext"],
            size=row["size"],
            storage_key=row["storage_key"],
            status=row["status"],
            text=row["text"],
            text_source=row["text_source"],
            pages=row["pages"],
            error=row["error"],
            warnings=tuple(row["warnings"]),
        )

    def with_data(self, load: Callable[[str], bytes]) -> Attachment:
        return replace(self, data=load(self.storage_key)) if self.kind == "image" else self

    def public(self) -> dict[str, Any]:
        """对外展示（界面、API）：不含文件内容和存储位置。"""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "size": self.size,
            "pages": self.pages,
            "status": self.status,
            "text_source": self.text_source,
            "text_chars": len(self.text or ""),
            "error": self.error,
            "warnings": list(self.warnings),
        }


def _neutralize(text: str) -> str:
    for tag in _TAGS:
        text = text.replace(f"</{tag}", f"<​/{tag}")
    return text


def attachment_block(files: Sequence[Attachment], *, vision: bool) -> tuple[str, tuple[Media, ...]]:
    """所有附件的标签块与随附媒体。vision 成员收到原图，其他成员收到图片的文字版。"""
    blocks: list[str] = []
    media: list[Media] = []
    for a in files:
        attrs = f'name="{a.name}" type="{a.kind}"'
        if a.kind == "image" and vision and a.data:
            media.append(Media("image", a.mime, a.data, a.name))
            blocks.append(f"<attachment {attrs}>{IMAGE_HINT} {len(media)}</attachment>")
            continue
        if a.kind == "image":
            attrs += ' presentation="文字版"'
        body = a.text if a.text else "（这个附件的内容不可用）"
        blocks.append(f"<attachment {attrs}>\n{_neutralize(body)}\n</attachment>")
    return "\n\n".join(blocks), tuple(media)


def attach_messages(
    messages: Sequence[Message],
    files: Sequence[Attachment],
    template: PromptTemplate,
    *,
    vision: bool,
) -> tuple[Message, ...]:
    """把附件接到一次调用的消息里：说明接在系统提示之后，附件接在第一条用户消息之后。"""
    if not files:
        return tuple(messages)
    text, media = attachment_block(files, vision=vision)
    filled = template.render(files=text)
    extra_system = next(m.content for m in filled.messages if m.role == "system")
    extra_user = next(m.content for m in filled.messages if m.role == "user")
    out: list[Message] = []
    first_user = True
    for m in messages:
        if m.role == "system":
            m = Message("system", f"{m.content}\n\n{extra_system}")
        elif m.role == "user" and first_user:
            m = Message("user", f"{m.content}\n\n{extra_user}", (*m.media, *media))
            first_user = False
        out.append(m)
    return tuple(out)
