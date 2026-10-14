"""统一的调用错误。消息由本项目生成，外部文本必须先脱敏再放进来。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class ErrorKind(StrEnum):
    RATE_LIMIT = "rate_limit"  # 429 限流
    QUOTA = "quota"  # 额度 / 余额用尽
    AUTH = "auth"  # key 无效或无权限
    NOT_FOUND = "not_found"  # 该渠道没有这个模型
    NETWORK = "network"  # 连接失败、代理错误等
    TIMEOUT = "timeout"
    SERVER = "server"  # 5xx、过载
    BAD_REQUEST = "bad_request"  # 请求本身有问题，换渠道也没用
    REFUSAL = "refusal"  # 模型拒答，换渠道是同一个模型，也没用
    INVALID_RESPONSE = "invalid_response"  # 返回格式无法解析


# 换到下一个渠道可能成功的错误
FAILOVER_KINDS = frozenset(
    {
        ErrorKind.RATE_LIMIT,
        ErrorKind.QUOTA,
        ErrorKind.AUTH,
        ErrorKind.NOT_FOUND,
        ErrorKind.NETWORK,
        ErrorKind.TIMEOUT,
        ErrorKind.SERVER,
        ErrorKind.INVALID_RESPONSE,
    }
)
# 本次调用内不再重试该渠道的错误
PERMANENT_KINDS = frozenset({ErrorKind.QUOTA, ErrorKind.AUTH, ErrorKind.NOT_FOUND})


class ProviderError(Exception):
    def __init__(
        self,
        kind: ErrorKind,
        channel: str,
        detail: str = "",
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.kind = kind
        self.channel = channel
        self.detail = detail
        self.status = status
        self.retry_after = retry_after
        status_part = f" HTTP {status}" if status else ""
        detail_part = f"：{detail}" if detail else ""
        super().__init__(f"[{channel}] {kind.value}{status_part}{detail_part}")

    @property
    def failover(self) -> bool:
        return self.kind in FAILOVER_KINDS


class UnsupportedCapability(NotImplementedError):
    """该渠道 / 适配器不支持请求的能力（如图像生成）。"""


@dataclass(frozen=True)
class Attempt:
    """一次渠道尝试的记录。"""

    channel: str
    ok: bool
    latency_s: float
    error: ErrorKind | None = None


class NoChannelAvailable(Exception):
    """模型在当前渠道模式下没有任何可用渠道（模式不匹配或缺少 key）。"""

    def __init__(self, model_id: str, skipped: dict[str, str]) -> None:
        self.model_id = model_id
        self.skipped = dict(skipped)
        reasons = "，".join(f"{c}: {r}" for c, r in skipped.items()) or "未配置渠道"
        super().__init__(f"模型 {model_id} 没有可用渠道（{reasons}）")


class AllChannelsFailed(Exception):
    """所有可用渠道都失败，或遇到不可切换的错误。"""

    def __init__(self, model_id: str, attempts: Sequence[Attempt], last: ProviderError) -> None:
        self.model_id = model_id
        self.attempts = tuple(attempts)
        self.last = last
        trail = " → ".join(f"{a.channel}({a.error.value if a.error else 'ok'})" for a in attempts)
        super().__init__(f"模型 {model_id} 调用失败：{trail}；最后错误 {last}")
