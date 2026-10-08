"""上传文件的存放：按内容哈希命名（sha256.扩展名），文件名只作显示，从不参与路径。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_NAME = re.compile(r"^[0-9a-f]{64}\.[a-z0-9]{1,5}$")


class FileStore:
    """root 为 None 时存在内存里（测试与 :memory: 数据库使用）。"""

    def __init__(self, root: Path | None) -> None:
        self.root = root
        self._memory: dict[str, bytes] = {}

    @staticmethod
    def key(data: bytes, ext: str) -> str:
        return f"{hashlib.sha256(data).hexdigest()}.{ext}"

    def save(self, data: bytes, ext: str) -> str:
        key = self.key(data, ext)
        if self.root is None:
            self._memory[key] = data
            return key
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / key
        if not path.exists():
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return key

    def load(self, key: str) -> bytes:
        if not _NAME.match(key):  # 只接受本模块生成的名字，防止路径穿越
            raise ValueError("无效的文件键")
        if self.root is None:
            return self._memory[key]
        return (self.root / key).read_bytes()
