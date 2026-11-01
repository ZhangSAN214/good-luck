"""外部表情包（dsh-meme 格式的压缩包）：装到本机 data/sticker_packs/，给某位成员专用或大家通用。

压缩包的样子（dsh-meme-packs 仓库的 release）::

    manifest.json   {"id", "name", "version", "license", ...}
    index.db        SQLite，表 memes(path, tag, file_name, caption, keywords, ...)
    memes/<分类>/<文件>.png|.gif|.webp|.jpg

圆桌只认这个格式，不依赖 dsh 本身。装进来后每张图有一个短名字（聊天记号「[表情:套·名]」里用，
≤6 字）、用法（提示词里告诉成员什么时候发）和意思词（发出前去重用）：

- 名字：文件名像词（如「害羞失语」）就用文件名，否则用第一个关键词；重名加数字。
- 用法：说明（caption）够短就用它，否则用关键词里最有区分度的几个（整包大半都有的词不算）。
- 意思词：同上的关键词，1～8 字，最多 8 个。

图片和索引只存在本机，不进仓库；许可证照原样记下（很多包只许非商业使用，个人用没问题）。
下载用目录（catalog.json）里给的 sha256 核对；压缩包里的路径、大小、图片格式都检查过才落盘。
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import re
import shutil
import sqlite3
import tempfile
import zipfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

PACK_DIR = "sticker_packs"  # 在数据库同级目录下
PACK_FILE = "pack.json"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
LABEL_RE = re.compile(r"^[^\]·\s:：\[]{1,6}$")  # 和聊天记号里的套装名同样的限制
IMAGE_EXT = {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
IMAGE_EXT |= {".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
MAX_FILE = 8 * 1024 * 1024  # 单张图最大
MAX_ITEMS = 400
NAME_MAX = 6
USE_MAX = 24
MEANS_MAX = 8
COMMON = ""  # owner 为空 = 大家通用

Fetch = Callable[[str, int], "bytes | None"]


class PackError(Exception):
    """装不上、找不到、参数不对；文字可以直接给用户看。"""


def _http_fetch(url: str, limit: int) -> bytes | None:
    import httpx

    try:
        with httpx.stream("GET", url, timeout=60.0, follow_redirects=True) as r:
            if r.status_code != 200:
                return None
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > limit:
                    return None
            return bytes(buf)
    except httpx.HTTPError:
        return None


def mime_of(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return None


def _letters(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())


def _wordlike(stem: str) -> bool:
    """文件名像个词（不是一串数字、时间戳）。"""
    s = _letters(stem)
    return bool(s) and sum(ch.isdigit() for ch in s) * 2 < len(s)


def _short(word: str, n: int = NAME_MAX) -> str:
    return _letters(word)[:n]


def _unique(base: str, taken: set[str]) -> str:
    base = base or "表情"
    if base not in taken:
        return base
    for i in range(2, 1000):
        cand = f"{base[: NAME_MAX - len(str(i))]}{i}"
        if cand not in taken:
            return cand
    raise PackError("表情名字重复太多")


def describe(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """index.db 的行（path / tag / caption / keywords）→ [{name, file, use, means, tag}]。"""
    words = [[w for w in (r.get("keywords") or "").split() if w] for r in rows]
    # 整包一半以上都有的词（包名、角色名、「梗图」之类）没有区分度
    seen = Counter(w for ws in words for w in set(ws))
    common = {w for w, c in seen.items() if len(rows) >= 4 and c * 2 > len(rows)}
    # 同一分类里一半以上都有的词（如「生气」类都带「愤怒」）排到后面，先说这张自己的
    by_tag: dict[str, list[list[str]]] = {}
    for r, ws in zip(rows, words, strict=True):
        by_tag.setdefault(r.get("tag") or "", []).append(ws)
    tag_common: dict[str, set[str]] = {}
    for tag, group in by_tag.items():
        c = Counter(w for ws in group for w in set(ws))
        tag_common[tag] = {w for w, n in c.items() if len(group) >= 4 and n * 2 > len(group)}
    taken: set[str] = set()
    out = []
    for r, ws in zip(rows, words, strict=True):
        shared = tag_common[r.get("tag") or ""]
        ws = [w for w in ws if w not in shared] + [w for w in ws if w in shared]
        stem = PurePosixPath(r["path"]).stem
        name = _short(stem) if _wordlike(stem) else ""
        if not name:
            first = next((w for w in ws if w not in common), ws[0] if ws else "")
            name = _short(first) or _short(r.get("caption") or "")
        name = _unique(name, taken)
        taken.add(name)
        means = []
        for w in ws:
            w = _letters(w)
            if w in common or not 1 <= len(w) <= 8 or w in means or w == name:
                continue
            means.append(w)
        means = means[:MEANS_MAX]
        caption = (r.get("caption") or "").strip()
        use = caption if 0 < len(caption) <= USE_MAX else "、".join(means[:4])
        out.append(
            {
                "name": name,
                "file": r["path"],
                "use": use[:USE_MAX],
                "means": means,
                "tag": (r.get("tag") or "")[:20],
            }
        )
    return out


def _safe_member(name: str) -> str | None:
    """压缩包里的路径：只收 memes/ 下的图片，拒绝绝对路径、..、反斜杠。"""
    if "\\" in name or name.startswith("/") or "\x00" in name:
        return None
    p = PurePosixPath(name)
    if any(part in ("", ".", "..") for part in p.parts) or p.parts[:1] != ("memes",):
        return None
    if p.suffix.lower() not in IMAGE_EXT:
        return None
    return str(p)


def _read_index(zf: zipfile.ZipFile, files: set[str]) -> list[dict[str, str]]:
    """index.db 里的条目；没有 index.db 时按文件名和分类目录生成。只留压缩包里真有的图。"""
    rows: list[dict[str, str]] = []
    if "index.db" in zf.namelist():
        info = zf.getinfo("index.db")
        if info.file_size > 32 * 1024 * 1024:
            raise PackError("index.db 太大")
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "index.db"
            db.write_bytes(zf.read(info))
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                cur = con.execute("select path, tag, caption, keywords from memes order by path")
                for path, tag, caption, keywords in cur:
                    if path in files:
                        rows.append(
                            {
                                "path": path,
                                "tag": str(tag or ""),
                                "caption": str(caption or ""),
                                "keywords": str(keywords or ""),
                            }
                        )
            except sqlite3.Error:
                raise PackError("index.db 读不出来（不是 dsh-meme 格式？）") from None
            finally:
                con.close()
    listed = {r["path"] for r in rows}
    for f in sorted(files - listed):
        p = PurePosixPath(f)
        tag = p.parts[1] if len(p.parts) > 2 else ""
        rows.append({"path": f, "tag": tag, "caption": "", "keywords": p.stem})
    return rows[:MAX_ITEMS]


class StickerPacks:
    """本机装的外部表情包。root 为空（内存数据库）时什么都没装。"""

    def __init__(self, rt: Any, root: Path | None, fetch: Fetch | None = None) -> None:
        self.rt = rt
        self.root = root
        self._fetch = fetch or _http_fetch

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.actions.sticker_packs

    # --- 查看 ------------------------------------------------------------------------

    def installed(self) -> list[dict[str, Any]]:
        """装好的包（pack.json 的内容），按 id 排序；坏掉的跳过。"""
        if self.root is None or not self.root.is_dir():
            return []
        out = []
        for d in sorted(self.root.iterdir()):
            if not d.is_dir() or not ID_RE.fullmatch(d.name):
                continue
            try:
                data = json.loads((d / PACK_FILE).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("id") == d.name and data.get("items"):
                out.append(data)
        return out

    def pack(self, pack_id: str) -> dict[str, Any]:
        for p in self.installed():
            if p["id"] == pack_id:
                return p
        raise PackError(f"没有装 {pack_id}")

    def enabled(self) -> bool:
        acts = self.rt.config.roundtable.chat.actions
        return bool(acts.enabled and self.rules.enabled)

    def for_member(self, member: str) -> list[dict[str, Any]]:
        """这位成员能发的外部包：她专用的在前，再是通用的。"""
        if not self.enabled():
            return []
        mine = [p for p in self.installed() if p.get("owner") == member]
        common = [p for p in self.installed() if not p.get("owner")]
        return mine + common

    def stickers(self, member: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """({套装名: [表情名]} 她专用的, 通用的)。"""
        own: dict[str, list[str]] = {}
        common: dict[str, list[str]] = {}
        for p in self.for_member(member):
            (own if p.get("owner") else common)[p["label"]] = [i["name"] for i in p["items"]]
        return own, common

    def menu(self, member: str, when: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """提示词里列的：每套最多 menu_size 张，按 when（如到小时的时间）和成员轮换，可复现。"""
        k = self.rules.menu_size
        own, common = self.stickers(member)

        def pick(packs: dict[str, list[str]]) -> dict[str, list[str]]:
            out = {}
            for label, names in packs.items():
                if len(names) > k:
                    rng = random.Random(f"{when}:{member}:{label}")
                    names = sorted(rng.sample(names, k), key=names.index)
                out[label] = names
            return out

        return pick(own), pick(common)

    def uses(self, member: str) -> dict[str, str]:
        """{「套·名」: 用法}。"""
        return {
            f"{p['label']}·{i['name']}": i["use"]
            for p in self.for_member(member)
            for i in p["items"]
            if i.get("use")
        }

    def means(self, member: str) -> dict[str, list[str]]:
        """{「套·名」: [意思词]}（发出前去重用）。"""
        return {
            f"{p['label']}·{i['name']}": list(i.get("means") or [])
            for p in self.for_member(member)
            for i in p["items"]
        }

    def image(self, pack_id: str, n: int) -> tuple[bytes, str]:
        """第 n 张（从 0 数）的图和类型。"""
        if not ID_RE.fullmatch(pack_id) or self.root is None:
            raise PackError("没有这张表情")
        p = self.pack(pack_id)
        if not 0 <= n < len(p["items"]):
            raise PackError("没有这张表情")
        path = self.root / pack_id / p["items"][n]["file"]
        try:
            data = path.read_bytes()
        except OSError:
            raise PackError("图片文件不见了") from None
        mime = mime_of(data)
        if mime is None:
            raise PackError("不是图片")
        return data, mime

    def chat_packs(self, member_names: dict[str, str]) -> list[dict[str, Any]]:
        """网页显示用：[{pack, member, items: [{name, caption, url}]}]。"""
        if not self.enabled():
            return []
        out = []
        for p in self.installed():
            owner = p.get("owner") or COMMON
            if owner and owner not in member_names:
                continue
            items = [
                {
                    "name": it["name"],
                    "caption": it.get("use", ""),
                    "url": f"/api/sticker-packs/{p['id']}/{n}?v={p.get('version', '')}",
                }
                for n, it in enumerate(p["items"])
            ]
            out.append({"pack": p["label"], "member": owner or "*", "items": items})
        return out

    # --- 目录 ------------------------------------------------------------------------

    def catalog(self) -> list[dict[str, Any]]:
        """在线目录（dsh-meme-packs 的 catalog.json）里能直接装的包。"""
        raw = (
            self._fetch(self.rules.catalog_url, 4 * 1024 * 1024) if self.rules.catalog_url else None
        )
        if not raw:
            raise PackError("目录下载不到（没联网？）")
        try:
            data = json.loads(raw)
        except ValueError:
            raise PackError("目录格式不对") from None
        mine = {p["id"]: p for p in self.installed()}
        out = []
        for p in data.get("packs") or []:
            if not isinstance(p, dict) or not ID_RE.fullmatch(str(p.get("id", ""))):
                continue
            out.append(
                {
                    "id": p["id"],
                    "name": str(p.get("name", ""))[:40],
                    "license": str(p.get("license", ""))[:40],
                    "count": p.get("count"),
                    "bytes": p.get("bytes"),
                    "url": p.get("archiveUrl") or "",
                    "sha256": str(p.get("sha256") or ""),
                    "installable": bool(p.get("archiveUrl")),
                    "installed": p["id"] in mine,
                }
            )
        return out

    # --- 装、改、删 --------------------------------------------------------------------

    def _check_label(self, label: str, pack_id: str) -> None:
        if not LABEL_RE.fullmatch(label):
            raise PackError("套装名 1～6 个字，不能有空格、·、冒号和方括号")
        if label in self._labels_taken(pack_id):
            raise PackError(f"套装名「{label}」已经有了，换一个")

    def _labels_taken(self, pack_id: str) -> set[str]:
        """已经用掉的套装名：通用的几套、成员昵称（专属那套就叫昵称）、别的外部包。"""
        acts = self.rt.config.roundtable.chat.actions
        taken = set(acts.stickers) | {p["label"] for p in self.installed() if p["id"] != pack_id}
        return taken | set(self.rt.config.personas.nicknames.values())

    def _check_owner(self, owner: str) -> None:
        vendors = {m.vendor for m in self.rt.config.models.enabled if m.seat}
        if owner and owner not in vendors:
            raise PackError(f"没有成员 {owner}")

    def install(
        self, source: str, owner: str | None = None, label: str = "", data: bytes | None = None
    ) -> dict[str, Any]:
        """装一个包：source 是目录里的 id（会下载、核对 sha256），或给 data（压缩包本身）。
        owner 是专用的成员（空串 = 通用）。已经装过的会整个换掉，owner、套装名不传时沿用原来的。"""
        if self.root is None:
            raise PackError("内存数据库没有地方存表情包")
        if owner:
            self._check_owner(owner)
        limit = self.rules.max_mb * 1024 * 1024
        expect = ""
        if data is None:
            entry = next((p for p in self.catalog() if p["id"] == source), None)
            if entry is None:
                raise PackError(f"目录里没有 {source}")
            if not entry["installable"]:
                raise PackError(f"{source} 没有打包下载，装不了")
            if entry["bytes"] and entry["bytes"] > limit:
                raise PackError(
                    f"{source} 有 {entry['bytes'] // 1048576} MB，超过上限 {self.rules.max_mb} MB"
                )
            data = self._fetch(entry["url"], limit)
            if not data:
                raise PackError("压缩包下载不到")
            expect = entry["sha256"]
        if len(data) > limit:
            raise PackError(f"压缩包超过上限 {self.rules.max_mb} MB")
        if expect and hashlib.sha256(data).hexdigest() != expect.lower():
            raise PackError("压缩包校验不对（下载坏了或被改过），没装")
        return self._unpack(data, owner, label)

    def _unpack(self, data: bytes, owner: str | None, label: str) -> dict[str, Any]:
        assert self.root is not None
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise PackError("不是 zip 压缩包") from None
        with zf:
            try:
                manifest = json.loads(zf.read("manifest.json"))
            except (KeyError, ValueError):
                raise PackError("压缩包里没有 manifest.json") from None
            pack_id = str(manifest.get("id", ""))
            if not ID_RE.fullmatch(pack_id):
                raise PackError("manifest.json 里的 id 不对")
            old = next((p for p in self.installed() if p["id"] == pack_id), None)
            if old and not label:
                label = old["label"]
            if owner is None:
                owner = old.get("owner", COMMON) if old else COMMON
            name = str(manifest.get("name") or pack_id)
            if not label:  # 没给套装名：用包名（去掉「表情包」），重了就加「包」或数字
                base = _short(re.sub(r"(动态)?表情包?$", "", name)) or _short(pack_id)
                taken = self._labels_taken(pack_id)
                label = next(
                    c
                    for c in [base, f"{base[:5]}包", *(f"{base[:5]}{i}" for i in range(2, 10))]
                    if c not in taken
                )
            self._check_label(label, pack_id)
            files: dict[str, zipfile.ZipInfo] = {}
            total = 0
            for info in zf.infolist():
                path = _safe_member(info.filename)
                if path is None or info.is_dir():
                    continue
                if info.file_size > MAX_FILE:
                    continue
                total += info.file_size
                if total > self.rules.max_mb * 1024 * 1024:
                    raise PackError(f"解压后超过上限 {self.rules.max_mb} MB")
                files[path] = info
            rows = _read_index(zf, set(files))
            tmp = self.root / f".{pack_id}.tmp"
            shutil.rmtree(tmp, ignore_errors=True)
            kept = []
            for r in rows:
                blob = zf.read(files[r["path"]])
                if len(blob) > MAX_FILE or mime_of(blob) is None:
                    continue
                dest = tmp / r["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blob)
                kept.append(r)
        if not kept:
            shutil.rmtree(tmp, ignore_errors=True)
            raise PackError("压缩包里没有能用的图片")
        pack = {
            "id": pack_id,
            "label": label,
            "owner": owner,
            "name": name[:40],
            "version": str(manifest.get("version", ""))[:20],
            "license": str(manifest.get("license", ""))[:40],
            "source": str(manifest.get("homepage") or manifest.get("source") or "")[:300],
            "items": describe(kept),
        }
        (tmp / PACK_FILE).write_text(json.dumps(pack, ensure_ascii=False, indent=1), "utf-8")
        final = self.root / pack_id
        shutil.rmtree(final, ignore_errors=True)
        tmp.replace(final)
        return self.summary(pack)

    def assign(
        self, pack_id: str, owner: str | None = None, label: str | None = None
    ) -> dict[str, Any]:
        """改给谁用（owner 为空串 = 通用）或套装名。"""
        p = self.pack(pack_id)
        if owner is not None:
            self._check_owner(owner)
            p["owner"] = owner
        if label is not None:
            self._check_label(label, pack_id)
            p["label"] = label
        assert self.root is not None
        path = self.root / pack_id / PACK_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(p, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(path)
        return self.summary(p)

    def remove(self, pack_id: str) -> dict[str, Any]:
        p = self.pack(pack_id)
        assert self.root is not None
        shutil.rmtree(self.root / pack_id)
        return {"id": p["id"], "removed": True}

    @staticmethod
    def summary(p: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": p["id"],
            "label": p["label"],
            "owner": p.get("owner") or COMMON,
            "name": p.get("name", ""),
            "license": p.get("license", ""),
            "count": len(p["items"]),
            "sample": [i["name"] for i in p["items"][:8]],
        }
