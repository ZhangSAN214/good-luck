"""身份遮蔽：组员输出在转给其他模型前，去掉可能暴露模型身份的名称。

名称从配置中提取（厂商名、模型 id、各渠道上的模型 ID、别称），不在代码里写死。
题目本身出现的名称不遮蔽 —— 那是讨论内容，不是自报身份。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from roundtable.core.config import ModelsConfig

MASK = "[已隐去]"
_ASCII = re.compile(r"^[\x00-\x7f]+$")
_MIN_LEN = 2


def identity_terms(models: ModelsConfig) -> set[str]:
    terms: set[str] = set()
    for m in models.models:
        terms |= {m.id, m.vendor, *m.aliases}
        for route in m.routes:
            terms.add(route.model)
            terms.add(route.model.rsplit("/", 1)[-1])
    terms |= set(models.channels)
    return {t for t in terms if len(t) >= _MIN_LEN}


class IdentityScrubber:
    def __init__(self, terms: Iterable[str]) -> None:
        # 长的先匹配，避免 "Gemini" 先于 "gemini-3.8-flash" 被替换造成残留
        self._terms = sorted(set(terms), key=len, reverse=True)

    @classmethod
    def from_config(cls, models: ModelsConfig, extra: Iterable[str] = ()) -> IdentityScrubber:
        """extra：额外要遮蔽的名称（厂商昵称：匿名时它们同样会暴露身份）。"""
        return cls(identity_terms(models) | {t for t in extra if len(t) >= _MIN_LEN})

    def _pattern(self, terms: list[str]) -> re.Pattern[str] | None:
        if not terms:
            return None
        parts = []
        for term in terms:
            escaped = re.escape(term)
            # 英文名称要求两侧不是字母数字，避免误伤（如 "grok" 出现在 "grokking" 里）
            parts.append(
                rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])" if _ASCII.match(term) else escaped
            )
        return re.compile("|".join(parts), re.IGNORECASE)

    def scrub(self, text: str, context: str = "") -> str:
        """遮蔽 text 中的身份名称；context（通常是题目）里出现过的名称保留。"""
        lowered = context.lower()
        active = [t for t in self._terms if t.lower() not in lowered]
        pattern = self._pattern(active)
        return pattern.sub(MASK, text) if pattern else text

    def bound(self, context: str = "", *, enabled: bool = True) -> IdentityScrubber:
        """绑定了固定上下文的遮蔽器：题目 / 附件里本来就有的名称保留。enabled=False 时完全不遮蔽
        （匿名关闭：成员之间、成员与工具之间都不做身份遮蔽）。"""
        return _Bound(self, context, enabled)

    def found(self, text: str) -> list[str]:
        """text 中出现的身份名称（用于测试与审计）。"""
        pattern = self._pattern(self._terms)
        return sorted({m.group(0) for m in pattern.finditer(text)}) if pattern else []


class _Bound(IdentityScrubber):
    def __init__(self, base: IdentityScrubber, context: str, enabled: bool) -> None:
        super().__init__(())
        self._base, self._context, self._enabled = base, context, enabled

    def scrub(self, text: str, context: str = "") -> str:
        if not self._enabled:
            return text
        return self._base.scrub(text, f"{self._context}\n{context}" if context else self._context)

    def found(self, text: str) -> list[str]:
        return self._base.found(text)
