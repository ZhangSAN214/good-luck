"""资源检索员（docs/KIT.md「生成器第二版 · 第 3 块」）：按拆需求列出的清单，自己上网找素材和数据。

- 每个窗口的 ``wants``（spec.Window）列出要什么：照片、插图、材质、3D 模型、图标、字体、
  数据、写代码要查的资料。
- 素材只去免费、不要 key 的公开来源找（SOURCES），每样记下来源、作者、许可证和下载日期；
  许可证不宽松（带 NC 非商用、ND 禁止改编，或者查不到许可证）的不用。
- 下载的文件先核对文件头（是不是真的图片 / 模型 / 字体），太大的不下。
- 数据只用配置里的公开接口（bench.net），当场试一次通不通；资料链接只留能打开的。
- 找不够就如实写「只找到 N 个」，不凑数，也不让助手自己画假图顶上。

这里只联网取数（取数函数由外面注入，测试用假的），不调模型。
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

FetchJSON = Callable[[str], Awaitable[Any]]
FetchBytes = Callable[[str], Awaitable[bytes]]
CheckURL = Callable[[str], Awaitable[bool]]

KIND_LABEL = {
    "photo": "照片",
    "illustration": "插图",
    "texture": "材质",
    "model": "3D 模型",
    "icon": "图标",
    "font": "字体",
    "data": "数据",
    "doc": "资料",
}
FILE_KINDS = ("photo", "illustration", "texture", "model", "icon", "font")

COMMONS = "https://commons.wikimedia.org/w/api.php"
OPENVERSE = "https://api.openverse.org/v1/images/"
POLYHAVEN = "https://api.polyhaven.com"
KHRONOS = "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models"
ICONIFY = "https://api.iconify.design"
FONTSOURCE = "https://api.fontsource.org/v1/fonts"
FONT_CDN = "https://cdn.jsdelivr.net/fontsource/fonts"

# 文件头：下回来的东西要真的是这种文件（防止下到一张报错网页）
MAGIC = {
    "jpg": (b"\xff\xd8\xff",),
    "png": (b"\x89PNG",),
    "webp": (b"RIFF",),
    "gif": (b"GIF8",),
    "glb": (b"glTF",),
    "woff2": (b"wOF2",),
}
NO_FAKE = "；不要自己画假图顶上，留空位写明「待补素材」"
CJK = re.compile(r"[㐀-鿿]")
NOT_FREE = re.compile(r"(^|[\s\-_/])(nc|nd)([\s\-_/.]|$)", re.I)


class FindError(Exception):
    pass


def license_ok(name: str, allow: list[str]) -> bool:
    """宽松许可证才用：在允许名单里（前缀），且不带 NC / ND。写了几条（；隔开）就每条都要宽松。"""
    parts = [p for p in str(name or "").split("；") if p.strip()]
    if len(parts) > 1:
        return all(license_ok(p, allow) for p in parts)
    n = " ".join(str(name or "").lower().replace("_", " ").split())
    if not n or NOT_FREE.search(n) or "noncommercial" in n or "noderiv" in n:
        return False
    return any(n.startswith(a.lower()) for a in allow)


def _strip(text: Any, limit: int = 120) -> str:
    """来源网站给的作者名常带 HTML 标签：去掉标签、解码实体、压成一行。"""
    t = html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))
    return " ".join(t.split())[:limit]


def _ext_of(url: str, default: str = "") -> str:
    tail = urlparse(url).path.rsplit("/", 1)[-1]
    ext = tail.rsplit(".", 1)[-1].lower() if "." in tail else default
    return {"jpeg": "jpg"}.get(ext, ext)


def file_ok(ext: str, blob: bytes) -> bool:
    """文件头和扩展名对得上；SVG 要真的是 SVG 而且不带脚本。"""
    if ext == "svg":
        head = blob[:2000].decode("utf-8", "ignore").lower()
        body = blob.decode("utf-8", "ignore").lower()
        return "<svg" in head and "<script" not in body and "javascript:" not in body
    return any(blob.startswith(m) for m in MAGIC.get(ext, ()))


def slug(text: str, fallback: str = "asset") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:40]
    return s or fallback


def _words(query: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", query.lower()) if len(w) > 1]


def _score(query: str, *fields: Any) -> int:
    """英文搜索词在名字、标签里出现了几个（Poly Haven、Khronos 这种整份清单本地筛）。"""
    hay = " ".join(
        " ".join(map(str, f)) if isinstance(f, (list, tuple, dict)) else str(f) for f in fields
    ).lower()
    return sum(1 for w in _words(query) if w in hay)


# --- 各个来源：返回候选（还没下载）--------------------------------------------------------
# 每个候选：source、title、author、license、license_url、page、file_url、ext


async def _commons(fetch: FetchJSON, query: str, limit: int, kind: str) -> list[dict]:
    url = (
        f"{COMMONS}?action=query&format=json&generator=search&gsrnamespace=6"
        f"&gsrsearch={quote(query + ' filetype:bitmap')}&gsrlimit={limit * 2}"
        "&prop=imageinfo&iiprop=url|extmetadata&iiurlwidth=1600"
    )
    data = await fetch(url)
    pages = sorted(
        ((data.get("query") or {}).get("pages") or {}).values(), key=lambda p: p.get("index", 0)
    )
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        file_url = info.get("thumburl") or info.get("url") or ""
        out.append(
            {
                "source": "Wikimedia Commons",
                "title": _strip(str(p.get("title") or "").removeprefix("File:"), 80),
                "author": _strip((meta.get("Artist") or {}).get("value")) or "（来源未写）",
                "license": _strip((meta.get("LicenseShortName") or {}).get("value"), 40),
                "license_url": str((meta.get("LicenseUrl") or {}).get("value") or ""),
                "page": str(info.get("descriptionurl") or ""),
                "file_url": file_url,
                "ext": _ext_of(file_url),
            }
        )
    return out


async def _openverse(fetch: FetchJSON, query: str, limit: int, kind: str) -> list[dict]:
    cat = "&category=illustration,digitized_artwork" if kind == "illustration" else ""
    url = (
        f"{OPENVERSE}?q={quote(query)}&license_type=commercial,modification"
        f"&page_size={limit * 2}{cat}"
    )
    data = await fetch(url)
    out = []
    for r in data.get("results") or []:
        code = str(r.get("license") or "").lower()
        ver = str(r.get("license_version") or "")
        name = {"cc0": "CC0", "pdm": "Public Domain Mark"}.get(code, f"CC {code.upper()}")
        file_url = str(r.get("url") or "")
        out.append(
            {
                "source": f"Openverse（{_strip(r.get('source'), 30) or '来源未写'}）",
                "title": _strip(r.get("title"), 80),
                "author": _strip(r.get("creator")) or "（来源未写）",
                "license": f"{name} {ver}".strip(),
                "license_url": str(r.get("license_url") or ""),
                "page": str(r.get("foreign_landing_url") or ""),
                "file_url": file_url,
                "ext": _ext_of(file_url, str(r.get("filetype") or "")),
            }
        )
    return out


async def _polyhaven(fetch: FetchJSON, query: str, limit: int, kind: str) -> list[dict]:
    index = await fetch(f"{POLYHAVEN}/assets?t=textures")
    ranked = sorted(
        (
            (_score(query, aid, a.get("name"), a.get("tags"), a.get("categories")), aid, a)
            for aid, a in (index or {}).items()
            if isinstance(a, dict)
        ),
        key=lambda x: (-x[0], x[1]),
    )
    out = []
    for score, aid, a in ranked:
        if score <= 0 or len(out) >= limit:
            break
        files = await fetch(f"{POLYHAVEN}/files/{quote(aid)}")
        diffuse = (((files or {}).get("Diffuse") or {}).get("1k") or {}).get("jpg") or {}
        if not diffuse.get("url"):
            continue
        out.append(
            {
                "source": "Poly Haven",
                "title": _strip(a.get("name"), 80) or aid,
                "author": "、".join(map(str, (a.get("authors") or {}).keys())) or "Poly Haven",
                "license": "CC0",
                "license_url": "https://polyhaven.com/license",
                "page": f"https://polyhaven.com/a/{aid}",
                "file_url": str(diffuse["url"]),
                "ext": "jpg",
            }
        )
    return out


def _licenses_in(meta: Any) -> list[str]:
    """Khronos 每个模型的 metadata.json：把里面写的许可证都挑出来（结构各版不一，递归找）。"""
    found: list[str] = []
    if isinstance(meta, dict):
        for k, v in meta.items():
            if k.lower() in ("license", "licence", "spdx") and isinstance(v, str):
                found.append(v)
            else:
                found += _licenses_in(v)
    elif isinstance(meta, list):
        for v in meta:
            found += _licenses_in(v)
    return found


async def _khronos(fetch: FetchJSON, query: str, limit: int, kind: str) -> list[dict]:
    index = await fetch(f"{KHRONOS}/model-index.json")
    ranked = sorted(
        (
            (_score(query, m.get("name"), m.get("label"), m.get("tags")), m)
            for m in index or []
            if isinstance(m, dict) and (m.get("variants") or {}).get("glTF-Binary")
        ),
        key=lambda x: (-x[0], str(x[1].get("name"))),
    )
    out = []
    for score, m in ranked:
        if score <= 0 or len(out) >= limit:
            break
        name = str(m["name"])
        try:
            meta = await fetch(f"{KHRONOS}/{quote(name)}/metadata.json")
        except Exception:  # noqa: BLE001 - 查不到许可证就当不明，下面会筛掉
            meta = {}
        licenses = _licenses_in(meta)
        out.append(
            {
                "source": "Khronos 示例模型",
                "title": _strip(m.get("label") or name, 80),
                "author": "Khronos 示例模型库（见原页）",
                # 有几条许可证时取最严的一条判断：任何一条不宽松就整件不用
                "license": licenses[0] if len(set(licenses)) == 1 else "；".join(licenses),
                "license_url": "",
                "page": f"https://github.com/KhronosGroup/glTF-Sample-Assets/tree/main/Models/{name}",
                "file_url": f"{KHRONOS}/{quote(name)}/glTF-Binary/"
                + quote(str(m["variants"]["glTF-Binary"])),
                "ext": "glb",
            }
        )
    return out


async def _iconify(
    fetch: FetchJSON, query: str, limit: int, kind: str, sets: list[str] | None = None
) -> list[dict]:
    pre = f"&prefixes={','.join(sets)}" if sets else ""
    data = await fetch(f"{ICONIFY}/search?query={quote(query)}&limit={max(limit * 3, 32)}{pre}")
    cols = data.get("collections") or {}
    out = []
    for icon in data.get("icons") or []:
        prefix, _, name = str(icon).partition(":")
        col = cols.get(prefix) or {}
        lic = col.get("license") or {}
        out.append(
            {
                "source": f"Iconify（{_strip(col.get('name'), 30) or prefix}）",
                "title": icon,
                "author": _strip((col.get("author") or {}).get("name")) or prefix,
                "license": str(lic.get("spdx") or lic.get("title") or ""),
                "license_url": str(lic.get("url") or ""),
                "page": f"https://icon-sets.iconify.design/{prefix}/?icon-filter={quote(name)}",
                "file_url": f"{ICONIFY}/{quote(prefix)}/{quote(name)}.svg",
                "ext": "svg",
            }
        )
    return out


async def _fontsource(fetch: FetchJSON, query: str, limit: int, kind: str) -> list[dict]:
    if CJK.search(query) or "chinese" in query.lower() or "cjk" in query.lower():
        raise FindError("中文字体一套就有几 MB 到十几 MB，这次不下载，成品用系统里的中文字体")
    fonts = await fetch(FONTSOURCE)
    cats = {"serif", "sans-serif", "monospace", "display", "handwriting"}
    want_cat = next((c for c in cats if c in query.lower()), "")
    ranked = sorted(
        (
            (
                _score(query, f.get("family"), f.get("id")) * 2
                + (want_cat and f.get("category") == want_cat),
                f,
            )
            for f in fonts or []
            if isinstance(f, dict) and "latin" in (f.get("subsets") or [])
        ),
        key=lambda x: (-x[0], str(x[1].get("id"))),
    )
    out = []
    for score, f in ranked:
        if score <= 0 or len(out) >= limit:
            break
        fid = str(f["id"])
        out.append(
            {
                "source": "Fontsource",
                "title": _strip(f.get("family"), 60),
                "author": "见字体原页",
                "license": str(f.get("license") or ""),
                "license_url": "",
                "page": f"https://fontsource.org/fonts/{fid}",
                "file_url": f"{FONT_CDN}/{quote(fid)}@latest/latin-400-normal.woff2",
                "ext": "woff2",
            }
        )
    return out


SOURCES: dict[str, Callable[..., Awaitable[list[dict]]]] = {
    "commons": _commons,
    "openverse": _openverse,
    "polyhaven": _polyhaven,
    "khronos": _khronos,
    "iconify": _iconify,
    "fontsource": _fontsource,
}


class AssetFinder:
    """按一项需求去各来源找候选、筛许可证、下载、核对文件；数据和资料链接当场试通不通。"""

    def __init__(
        self,
        *,
        fetch_json: FetchJSON,
        fetch_bytes: FetchBytes,
        check_url: CheckURL,
        rules: Any,
        net: list[Any] | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.fetch_json = fetch_json
        self.fetch_bytes = fetch_bytes
        self.check_url = check_url
        self.rules = rules
        self.net = {s.name: s for s in net or []}
        self.today = today

    async def candidates(self, kind: str, query: str) -> tuple[list[dict], list[str]]:
        """各来源按顺序找，许可证不宽松、不是 https、扩展名不对的筛掉。返回候选和沿路的说明。"""
        notes: list[str] = []
        want = self.rules.max_per_need + self.rules.alternates
        out: list[dict] = []
        seen: set[str] = set()
        for name in self.rules.sources.get(kind) or []:
            search = SOURCES.get(name)
            if search is None:
                notes.append(f"不认得的来源 {name}")
                continue
            try:
                if name == "iconify":
                    found = await search(
                        self.fetch_json, query, want, kind, list(self.rules.icon_sets)
                    )
                else:
                    found = await search(self.fetch_json, query, want, kind)
            except FindError as exc:
                notes.append(str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 - 一个来源连不上就换下一个
                notes.append(f"{name} 连不上：{type(exc).__name__}")
                continue
            dropped = 0
            for c in found:
                ok = (
                    c["file_url"].startswith("https://")
                    and (c["ext"] in MAGIC or c["ext"] == "svg")
                    and license_ok(c["license"], self.rules.licenses)
                    and c["file_url"] not in seen
                )
                if not ok:
                    dropped += 1
                    continue
                seen.add(c["file_url"])
                out.append(c)
            if dropped:
                notes.append(f"{name} 有 {dropped} 个许可证不宽松或格式不对，没用")
            if len(out) >= want:
                break
        return out[:want], notes

    async def download(self, cand: dict, folder: Path, rel: str) -> dict:
        """下载一个候选到 folder/rel：太大、文件头不对都不要。返回带署名的记录。"""
        blob = await self.fetch_bytes(cand["file_url"])
        limit = int(self.rules.max_mb_each * 1024 * 1024)
        if len(blob) > limit:
            raise FindError(f"太大（{len(blob) / 1024 / 1024:.1f} MB）")
        if not file_ok(cand["ext"], blob):
            raise FindError("下回来的不是这种文件")
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        return cand | {
            "file": rel,
            "bytes": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "date": self.today().isoformat(),
        }

    async def find_files(self, need: dict, folder: Path, base: str, budget: int) -> dict[str, Any]:
        """一项要文件的需求：找候选 → 下前 N 个 → 剩下的留作「换一个」的备选。"""
        want = min(int(need.get("count") or 1), self.rules.max_per_need, budget)
        query = need.get("query") or need.get("what") or ""
        cands, notes = await self.candidates(need["kind"], query)
        items, spare = [], []
        for c in cands:
            if len(items) >= want:
                spare.append(c)
                continue
            rel = f"{base}/{slug(c['title'])}-{len(items) + 1}.{c['ext']}"
            try:
                items.append(await self.download(c, folder, rel))
            except Exception as exc:  # noqa: BLE001 - 这一个下不了就试下一个
                notes.append(f"「{c['title'][:30]}」没下成：{str(exc)[:60] or type(exc).__name__}")
        return self.result(need, want, items, spare[: self.rules.alternates], notes)

    def result(self, need, want, items, spare, notes) -> dict[str, Any]:
        got = len(items)
        status = "ok" if got >= want else "short" if got else "none"
        say = {
            "ok": f"找到 {got} 个",
            "short": f"只找到 {got} 个（要 {want} 个）",
            "none": "一个都没找到",
        }[status]
        return {
            "kind": need["kind"],
            "what": need.get("what") or "",
            "query": need.get("query") or "",
            "want": want,
            "got": got,
            "status": status,
            "note": say + ("：" + "；".join(notes[:4]) if notes and status != "ok" else ""),
            "items": items,
            "spare": spare,
        }

    async def find_data(self, need: dict, status: Callable[[str], Awaitable[int]]) -> dict:
        """要数据：只用配置里的公开接口（bench.net），当场试一次；名单里没有就写明原因。"""
        name = str(need.get("service") or "")
        base = {"kind": "data", "what": need.get("what") or "", "service": name, "items": []}
        svc = self.net.get(name)
        if svc is None:
            why = need.get("why") or "名单里没有免费、不要 key 的公开接口"
            return base | {"status": "none", "ok": False, "note": f"不能联网取：{why}，用参考数据"}
        url = re.sub(r"\{[a-z_]+\}", "1", svc.url)  # 参数随便填一个，只看接口通不通
        try:
            code = await status(url)
        except Exception as exc:  # noqa: BLE001
            code, err = 0, type(exc).__name__
        else:
            err = ""
        ok = 0 < code < 400 or code in (400, 422)  # 参数不对也说明接口在
        note = (
            f"用 {svc.source} 的「{name}」（刚试过能连上）"
            if ok
            else f"「{name}」现在连不上（{err or code}），写窗口时要有参考数据兜底"
        )
        return base | {
            "status": "ok" if ok else "none",
            "ok": ok,
            "note": note,
            "source": svc.source,
        }

    async def find_docs(self, need: dict) -> dict:
        """写代码要查的资料：助手给的链接只留 https 而且能打开的。"""
        items, bad = [], 0
        for url in need.get("urls") or []:
            url = str(url).strip()
            ok = url.startswith("https://") and len(url) < 300
            if ok:
                try:
                    ok = await self.check_url(url)
                except Exception:  # noqa: BLE001
                    ok = False
            if ok:
                items.append(
                    {"url": url, "title": need.get("what") or url, "source": urlparse(url).netloc}
                )
            else:
                bad += 1
        status = "ok" if items else "none"
        note = f"{len(items)} 个能打开" + (f"，{bad} 个打不开没用" if bad else "")
        return {
            "kind": "doc",
            "what": need.get("what") or "",
            "status": status,
            "note": note,
            "items": items,
            "want": len(items) + bad,
            "got": len(items),
        }


def credits_md(found: list[dict]) -> str:
    """assets/found/CREDITS.md：每个文件的来源、作者、许可证和下载日期。"""
    lines = ["# 素材署名", "", "资源检索员找来的素材，按许可证要求署名。", ""]
    for need in found:
        for it in need.get("items") or []:
            if not it.get("file"):
                continue
            lic = it["license"] + (f"（{it['license_url']}）" if it.get("license_url") else "")
            lines.append(
                f"- `{it['file']}`：{it['title']}，作者 {it['author']}，{lic}，"
                f"来源 {it['source']} {it['page']}，下载于 {it['date']}"
            )
    return "\n".join(lines) + "\n"


def found_lines(found: list[dict], titles: dict[str, str]) -> list[str]:
    """给写窗口的助手看：每个窗口找到了什么、在哪、怎么署名；找不够的如实说。"""
    out = []
    for need in found:
        who = titles.get(need.get("window", ""), need.get("window", ""))
        label = KIND_LABEL.get(need["kind"], need["kind"])
        head = f"  · 窗口「{who}」要的{label}「{need['what']}」：{need['note']}"
        if need["kind"] in FILE_KINDS:
            files = [
                f"{it['file']}（{it['title']}，{it['author']}，{it['license']}）"
                for it in need["items"]
            ]
            out.append(
                head
                + (
                    "；文件：" + "、".join(files)
                    if files
                    else "；不要自己画假图顶上，留空位写明「待补素材」"
                )
            )
        elif need["kind"] == "doc":
            out.append(head + "".join(f"；{it['url']}" for it in need["items"]))
        else:
            out.append(head)
    return out
