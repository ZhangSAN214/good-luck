"""成员的工作目录与生成的文件。

每位成员在每张桌子上有一个临时工作目录（系统临时目录下，不在项目目录里）：
- in/：本场附件的原始文件（只读材料）；
- out/：成员生成的文件。跨步骤保留（修订时可以改之前的文件）；重启后从数据库恢复。

每次工具调用后收集 out/：只接受白名单扩展名的普通文件，拒绝符号链接与硬链接，限制大小与数量，
清理文件名；通过的文件按内容哈希存放并登记（作者代号、步骤、来源工具调用）。不合格的文件被删除。
"""

from __future__ import annotations

import contextlib
import hashlib
import mimetypes
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from roundtable.core.attachments import Attachment, FileStore
from roundtable.core.config.schema import FileRules
from roundtable.core.storage import Repository

KINDS = {
    "image": {"png", "jpg", "jpeg", "gif", "webp", "svg"},
    "code": {"py", "json", "tex", "html"},
    "text": {"txt", "md"},
    "table": {"csv", "xlsx"},
    "document": {"docx", "pptx", "pdf"},
}
TEXT_EXTS = {"py", "json", "tex", "html", "txt", "md", "csv", "svg"}
_PART = re.compile(r"^[^\x00-\x1f<>:\"|?*\\/]{1,100}$")


def file_kind(ext: str) -> str:
    return next((k for k, exts in KINDS.items() if ext in exts), "other")


def file_mime(ext: str) -> str:
    return {
        "md": "text/markdown",
        "csv": "text/csv",
        "py": "text/x-python",
        "tex": "text/x-tex",
        "svg": "image/svg+xml",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }.get(ext) or mimetypes.types_map.get(f".{ext}", "application/octet-stream")


def clean_path(raw: str) -> str | None:
    """成员给出的文件路径 → out/ 下的相对路径；不合法（含 ..、绝对路径、控制字符）时返回 None。"""
    text = raw.strip().replace("\\", "/")
    if text.startswith("out/"):
        text = text[4:]
    path = PurePosixPath(text)
    if not text or path.is_absolute() or len(path.parts) > 4:
        return None
    if any(p in ("", ".", "..") or not _PART.match(p) or p.startswith(".") for p in path.parts):
        return None
    return str(path)


@dataclass
class Collected:
    new: list[dict] = field(default_factory=list)  # 新增或改动的文件（files 表的行）
    rejected: list[str] = field(default_factory=list)  # 被拒绝的原因


class Workspace:
    """一位成员在一张桌子上的工作目录。"""

    def __init__(
        self,
        *,
        session_id: str,
        table_no: int,
        code: str | None,
        repo: Repository,
        store: FileStore,
        rules: FileRules,
        attachments: tuple[Attachment, ...] = (),
        project_root: Path | None = None,
    ) -> None:
        self.session_id, self.table_no, self.code = session_id, table_no, code
        self.repo, self.store, self.rules = repo, store, rules
        self.root = Path(tempfile.mkdtemp(prefix="roundtable-")).resolve()
        if project_root and (self.root == project_root or project_root in self.root.parents):
            raise RuntimeError("工作目录不能在项目目录里")
        (self.root / "in").mkdir()
        (self.root / "out").mkdir()
        self.known: dict[str, str] = {}  # out/ 相对路径 → sha256
        for a in attachments:  # 附件原文件（同名时后者加序号）
            name = clean_path(PurePosixPath(a.name).name) or f"attachment.{a.ext}"
            target = self.root / "in" / name
            n = 1
            while target.exists():
                n += 1
                target = self.root / "in" / f"{Path(name).stem}-{n}{Path(name).suffix}"
            target.write_bytes(store.load(a.storage_key))
        for row in self._mine():  # 恢复之前生成的文件（最新版本）
            target = self.root / "out" / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(store.load(row["storage_key"]))
            self.known[row["path"]] = row["sha256"]

    def _mine(self) -> list[dict]:
        latest: dict[str, dict] = {}
        for row in self.repo.files(self.session_id):
            if row["table_no"] == self.table_no and row["code"] == self.code:
                latest[row["path"]] = row
        return list(latest.values())

    @property
    def out(self) -> Path:
        return self.root / "out"

    def files(self) -> list[dict]:
        """当前（最新版本的）文件列表。"""
        return sorted(self._mine(), key=lambda r: r["path"])

    def write(self, rel: str, data: bytes) -> None:
        target = self.out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def collect(self, *, step: str, tool_call_id: int | None, step_count: int) -> Collected:
        """检查 out/，登记新增或改动的文件；不合格的文件删除并说明原因。"""
        result = Collected()
        rules = self.rules
        used = self.repo.session_file_bytes(self.session_id)
        for dirpath, dirs, names in os.walk(self.out, followlinks=False):
            base = Path(dirpath)
            for d in list(dirs):
                if (base / d).is_symlink():
                    dirs.remove(d)
                    (base / d).unlink()
                    result.rejected.append(f"{d}：不允许符号链接")
            for name in names:
                path = base / name
                rel = path.relative_to(self.out).as_posix()
                reason = self._check(path, rel)
                if reason is None:
                    data = path.read_bytes()
                    digest = hashlib.sha256(data).hexdigest()
                    if self.known.get(rel) == digest:
                        continue
                    if len(data) > rules.max_file_mb * 2**20:
                        reason = f"超过 {rules.max_file_mb:g} MB"
                    elif used + len(data) > rules.max_session_mb * 2**20:
                        reason = f"本场生成的文件合计超过 {rules.max_session_mb:g} MB"
                    elif step_count + len(result.new) >= rules.max_files_per_step:
                        reason = f"每步最多生成 {rules.max_files_per_step} 个文件"
                if reason:
                    result.rejected.append(f"{rel}：{reason}")
                    with contextlib.suppress(OSError):
                        path.unlink()
                    continue
                ext = rel.rsplit(".", 1)[-1].lower()
                key = self.store.save(data, ext)
                row = dict(
                    table_no=self.table_no,
                    step=step,
                    code=self.code,
                    tool_call_id=tool_call_id,
                    path=rel,
                    kind=file_kind(ext),
                    mime=file_mime(ext),
                    size=len(data),
                    sha256=digest,
                    storage_key=key,
                )
                row["id"] = self.repo.add_file(self.session_id, **row)
                self.known[rel] = digest
                used += len(data)
                result.new.append(row)
        return result

    def _check(self, path: Path, rel: str) -> str | None:
        st = path.lstat()
        if path.is_symlink():
            return "不允许符号链接"
        if not path.is_file():
            return "不是普通文件"
        if st.st_nlink > 1:
            return "不允许硬链接"
        if clean_path(rel) != rel:
            return "文件名不合法"
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        if ext not in self.rules.extensions:
            return f"不允许的文件类型 .{ext or '?'}"
        return None

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
