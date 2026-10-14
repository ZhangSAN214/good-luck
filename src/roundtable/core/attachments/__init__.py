"""附件：上传文件的识别、文字提取、存放、模型预处理（图片文字版、音频转写）和发给模型时的呈现。"""

from .detect import FileType, UploadError, detect, display_name
from .extract import Extracted, decode_text, extract, extract_docx, extract_pdf
from .ingest import ingest
from .model import (
    Attachment,
    append_to_messages,
    attach_messages,
    attachment_block,
    style_reference_media,
)
from .prepare import prepare_attachments
from .store import FileStore

__all__ = [
    "Attachment",
    "Extracted",
    "FileStore",
    "FileType",
    "UploadError",
    "append_to_messages",
    "attach_messages",
    "attachment_block",
    "decode_text",
    "detect",
    "display_name",
    "extract",
    "extract_docx",
    "extract_pdf",
    "ingest",
    "prepare_attachments",
    "style_reference_media",
]
