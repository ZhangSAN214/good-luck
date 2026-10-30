"""工作台生成器的「找插件」：按拆需求时写下的 needs，去 dsh 插件集市和 GitHub 找现成的，
判定能不能搬。

用户的规则（2026-10-29 拍板「全自动，事后看清单」）：
- GitHub 星数 ≥ ``min_stars`` 且许可证宽松（``PERMISSIVE``）：**直接搬**，不用每次问；
  做完在清单里列出来。
- 不满足（星数不够、许可证不宽松、没写许可证）：**只借思路**，代码不搬，
  交给写窗口的助手参考它的做法自己写。
- 底线：只下载源码压缩包，**不在电脑上运行任何东西**（不 npm install、不跑脚本）；
  解压时挡掉绝对路径、``..``、
  链接文件和超大文件；每次搬用都记下来源、许可证、星数、
  提交号和压缩包的 sha256（``sources.json``）。
  搬来的代码只在工作台页面里用，而页面本身在浏览器沙箱里运行（碰不到圆桌的接口和登录状态）。
- 不碰带 key、要登录、会上传数据的东西：这类仓库按说明里的关键词挡掉，只借思路。

网络请求都走注入的 ``fetch``（测试里换成假的，不联网）。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

PERMISSIVE = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "0BSD",
        "Unlicense",
        "CC0-1.0",
        "Zlib",
        "BSL-1.0",
    }
)
# 说明里出现这些词的仓库可能要 key、要登录或会上传数据：只借思路，不搬
RISKY = re.compile(r"api[ _-]?key|token|login|oauth|telemetry|upload|cloud sync|credentials", re.I)
DSH_INDEX = "https://www.mydsh.xyz/market/index.json"
GITHUB = "https://api.github.com"
IDEA_MIN_STARS = 20  # 只借思路的候选至少几星才列进清单

FetchJSON = Callable[[str], Awaitable[Any]]
FetchBytes = Callable[[str], Awaitable[bytes]]


@dataclass
class Candidate:
    source: str  # github / dsh
    repo: str  # owner/name
    url: str
    stars: int = 0
    license: str = ""
    summary: str = ""
    size_kb: int = 0
    branch: str = "main"
    archived: bool = False
    verdict: str = ""  # adopt 搬 / idea 只借思路 / skip 不用
    why: str = ""
    need: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def judge(c: Candidate, *, min_stars: int, max_kb: int) -> Candidate:
    """按用户定的门槛判定：搬、只借思路、还是不用。"""
    if c.archived:
        c.verdict, c.why = "skip", "仓库已归档，不再维护"
    elif RISKY.search(c.summary):
        c.verdict, c.why = "idea", "说明里提到 key、登录或上传，只借思路"
    elif not c.license or c.license in ("NOASSERTION", "other"):
        c.verdict, c.why = "idea", "没写明许可证，只借思路"
    elif c.license not in PERMISSIVE:
        c.verdict, c.why = "idea", f"许可证 {c.license} 不宽松，只借思路"
    elif c.stars < min_stars:
        c.verdict, c.why = "idea", f"{c.stars} 星，不到 {min_stars} 星，只借思路"
    elif c.size_kb > max_kb:
        c.verdict, c.why = "idea", f"仓库太大（{c.size_kb // 1024} MB），只借思路"
    else:
        c.verdict, c.why = "adopt", f"{c.license}，{c.stars} 星"
    return c


def repo_of(url: str) -> str:
    m = re.match(r"https?://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url.strip())
    return m.group(1) if m else ""


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^\w]+", text.lower()) if len(w) > 1}


def match_dsh(index: dict[str, Any], keywords: str, limit: int = 3) -> list[Candidate]:
    """在 dsh 插件集市的索引里按关键词找：名字和简介里命中的词越多越靠前，同分看星数。"""
    want = _words(keywords)
    if not want:
        return []
    # 只命中一个常见词（比如 map）的大多不相关：关键词有两个以上时至少命中两个
    need = min(2, len(want))
    scored = []
    for p in index.get("plugins") or []:
        repo = repo_of(str(p.get("repo") or ""))
        if not repo:
            continue
        hit = len(want & _words(f"{p.get('name', '')} {p.get('summary', '')} {repo}"))
        if hit >= need:
            scored.append((hit, int(p.get("stars") or 0), p, repo))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [
        Candidate(
            "dsh",
            repo,
            f"https://github.com/{repo}",
            stars=s,
            summary=str(p.get("summary") or "")[:200],
        )
        for _, s, p, repo in scored[:limit]
    ]


class Scout:
    def __init__(
        self,
        *,
        fetch_json: FetchJSON,
        fetch_bytes: FetchBytes,
        min_stars: int = 100,
        max_kb: int = 20000,
        max_extract_mb: int = 30,
        per_need: int = 3,
    ) -> None:
        self.fetch_json = fetch_json
        self.fetch_bytes = fetch_bytes
        self.min_stars = min_stars
        self.max_kb = max_kb
        self.max_extract = max_extract_mb * 1024 * 1024
        self.per_need = per_need
        self._dsh: dict[str, Any] | None = None

    async def dsh_index(self) -> dict[str, Any]:
        if self._dsh is None:
            try:
                data = await self.fetch_json(DSH_INDEX)
                self._dsh = data if isinstance(data, dict) else {}
            except Exception:  # noqa: BLE001 - 集市打不开就只搜 GitHub
                self._dsh = {}
        return self._dsh

    async def _repo(self, c: Candidate) -> Candidate:
        """补全仓库信息：许可证、星数、大小、默认分支、是否归档。"""
        info = await self.fetch_json(f"{GITHUB}/repos/{c.repo}")
        lic = (info.get("license") or {}).get("spdx_id") or ""
        c.license = lic
        c.stars = int(info.get("stargazers_count") or c.stars)
        c.size_kb = int(info.get("size") or 0)
        c.branch = str(info.get("default_branch") or "main")
        c.archived = bool(info.get("archived"))
        c.summary = c.summary or str(info.get("description") or "")[:200]
        return c

    async def search(self, need: str, keywords: str) -> list[Candidate]:
        """一项需求：dsh 集市 + GitHub 各找几个，去重，补全信息，按门槛判定。"""
        found: dict[str, Candidate] = {}
        for c in match_dsh(await self.dsh_index(), keywords, self.per_need):
            found.setdefault(c.repo.lower(), c)
        q = quote(f"{keywords} stars:>={max(1, self.min_stars // 4)}")
        try:
            res = await self.fetch_json(
                f"{GITHUB}/search/repositories?q={q}&sort=stars&per_page={self.per_need}"
            )
            items = res.get("items") or []
        except Exception:  # noqa: BLE001 - 搜索失败（限流、断网）就用集市里找到的
            items = []
        for it in items[: self.per_need]:
            repo = str(it.get("full_name") or "")
            if repo and repo.lower() not in found:
                found[repo.lower()] = Candidate(
                    "github",
                    repo,
                    str(it.get("html_url") or f"https://github.com/{repo}"),
                    stars=int(it.get("stargazers_count") or 0),
                    license=((it.get("license") or {}).get("spdx_id") or ""),
                    summary=str(it.get("description") or "")[:200],
                    size_kb=int(it.get("size") or 0),
                    branch=str(it.get("default_branch") or "main"),
                    archived=bool(it.get("archived")),
                )
        out = []
        for c in found.values():
            if c.source == "dsh":
                try:
                    await self._repo(c)
                except Exception:  # noqa: BLE001 - 查不到仓库信息：当作没写许可证
                    c.license = ""
            c.need = need
            judge(c, min_stars=self.min_stars, max_kb=self.max_kb)
            # 星太少的只借思路也没什么可借：不列进清单，免得清单里一堆不相关的小仓库
            if c.verdict == "adopt" or c.stars >= IDEA_MIN_STARS:
                out.append(c)
        return out

    async def fetch(self, c: Candidate, dest: Path) -> dict[str, Any]:
        """把判定为「搬」的仓库源码钉在某个提交上，下载解压到 dest（只解压，不运行）
        。返回来源记录。"""
        commit = await self.fetch_json(f"{GITHUB}/repos/{c.repo}/commits/{quote(c.branch)}")
        sha = str(commit.get("sha") or "")
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("查不到提交号")
        blob = await self.fetch_bytes(f"https://codeload.github.com/{c.repo}/tar.gz/{sha}")
        digest = hashlib.sha256(blob).hexdigest()
        files = safe_extract(blob, dest, limit=self.max_extract)
        record = {
            "repo": c.repo,
            "url": c.url,
            "license": c.license,
            "stars": c.stars,
            "commit": sha,
            "sha256": digest,
            "files": files,
            "need": c.need,
            "folder": dest.name,
        }
        (dest / "SOURCE.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return record


def safe_extract(blob: bytes, dest: Path, *, limit: int) -> int:
    """解压 GitHub 的源码包：去掉最外层目录；挡掉绝对路径、``..``、链接和设备文件；
    总大小超过 limit 就停。"""
    dest.mkdir(parents=True, exist_ok=True)
    total = count = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for m in tar:
            parts = PurePosixPath(m.name).parts[1:]  # 去掉 owner-repo-sha/ 这一层
            if not parts or m.name.startswith("/") or ".." in parts:
                continue
            if m.isdir():
                (dest.joinpath(*parts)).mkdir(parents=True, exist_ok=True)
                continue
            if not m.isfile():  # 链接、设备文件一律不要
                continue
            total += m.size
            if total > limit:
                raise ValueError(f"解压后超过 {limit // 1024 // 1024} MB，只借思路")
            target = dest.joinpath(*parts)
            if not target.resolve().is_relative_to(dest.resolve()):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(m)
            if src is None:
                continue
            target.write_bytes(src.read())
            target.chmod(0o644)  # 不保留可执行位
            count += 1
    return count


def http_fetchers(timeout_s: float = 20.0) -> tuple[FetchJSON, FetchBytes]:
    """真正联网的取数函数（只读 GET；GitHub 不带 token，受它的匿名限流）。"""
    import httpx

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "roundtable-bench"}

    async def get(url: str) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True, headers=headers
        ) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r

    async def fetch_json(url: str) -> Any:
        return (await get(url)).json()

    async def fetch_bytes(url: str) -> bytes:
        return (await get(url)).content

    return fetch_json, fetch_bytes
