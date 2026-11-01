"""聊天里 emoji 显示成图片（如苹果样式）：用到哪个才下载哪个，存在本机 data/emoji/，不进仓库。

图的来源由配置 ``chat.actions.emoji_images.url`` 给（``{code}`` 换成码点文件名）。只在网页显示时用：
存进库的消息文字、发给模型的文字里仍然是原来的 emoji 字符。下载不到时返回 None，网页退回系统字体。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

EMOJI_DIR = "emoji"  # 在数据库同级目录下
CODE_RE = re.compile(r"^[0-9a-f]{2,6}(-[0-9a-f]{2,6}){0,9}$")
MAX_BYTES = 512 * 1024
MISS = b""  # 这次运行里下载过但没有：只记在内存里（断网时下次启动还会再试）

Fetch = Callable[[str], "bytes | None"]


def _http_fetch(url: str) -> bytes | None:
    import httpx

    try:
        r = httpx.get(url, timeout=8.0, follow_redirects=True)
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or len(r.content) > MAX_BYTES or r.content[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return r.content


def candidates(code: str) -> list[str]:
    """同一个 emoji 可能的文件名：原样一个；带不带 fe0f（变体选择符）各试一次。"""
    out = [code]
    bare = "-".join(p for p in code.split("-") if p != "fe0f")
    if bare and bare != code:
        out.append(bare)
    elif "-" not in code:
        out.append(f"{code}-fe0f")
    return out


class EmojiImages:
    def __init__(self, rt: Any, root: Path | None, fetch: Fetch | None = None) -> None:
        self.rt = rt
        self.root = root
        self._fetch = fetch or _http_fetch
        self._mem: dict[str, bytes] = {}

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.actions.emoji_images

    def enabled(self) -> bool:
        return bool(self.rules.enabled and self.rules.url)

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled(), "style": self.rules.style if self.enabled() else ""}

    def _path(self, code: str) -> Path | None:
        return self.root / f"{code}.png" if self.root else None

    def _cached(self, code: str) -> bytes | None:
        if code in self._mem:
            return self._mem[code]
        p = self._path(code)
        if p and p.is_file():
            data = p.read_bytes()
            self._mem[code] = data
            return data
        return None

    def _keep(self, code: str, data: bytes) -> None:
        self._mem[code] = data
        p = self._path(code)
        if p:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def image(self, code: str) -> bytes | None:
        """码点（如 1f602、2764-fe0f）对应的图；关着、码点不对、下载不到都是 None。"""
        code = code.lower()
        if not self.enabled() or not CODE_RE.fullmatch(code):
            return None
        hit = self._cached(code)
        if hit is not None:
            return hit or None
        for name in candidates(code):
            data = self._fetch(self.rules.url.format(code=name))
            if data:
                self._keep(code, data)
                return data
        self._mem[code] = MISS
        return None
