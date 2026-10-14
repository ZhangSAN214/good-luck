"""用户中途插话的校验（网页接口和命令行共用）。"""

from __future__ import annotations

NOTE_MAX_CHARS = 1000  # 一条补充最多多少字
NOTE_MAX_PENDING = 5  # 还没被带上的补充最多几条（防止刷屏）
FINISHED = frozenset({"completed", "stopped", "failed"})  # 已结束的讨论不再接受补充


class NoteError(ValueError):
    """补充不能接受；message 是给用户看的原因。"""


def clean_note(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        raise NoteError("补充内容不能为空")
    if len(cleaned) > NOTE_MAX_CHARS:
        raise NoteError(f"补充太长了（最多 {NOTE_MAX_CHARS} 字）")
    return cleaned


def check_can_add(status: str, pending: int) -> None:
    if status in FINISHED:
        raise NoteError("这场讨论已经结束，不能再补充了；可以开一个新题目")
    if pending >= NOTE_MAX_PENDING:
        raise NoteError(f"还有 {pending} 条补充没被带上，等下一步开始后再发")
