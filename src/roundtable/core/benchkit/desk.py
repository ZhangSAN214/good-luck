"""资料台：工作台底部抽屉里的「资料」（2026-10-31 工作台大更新第 5 批）。

方案见 /mnt/project-files/计划/资料摘取界面_方案.md（用户批：放进工作台抽屉、
AI 摘要默认关、每周拓展、先上零成本网页库）。全部免费、不要 key、只读 GET：

- **GitHub 榜单**：配置里的每一类（3D、动画、Live2D、修图、游戏……）各取前 N，「总榜」按星数，
  「新星」只看最近 N 天新建的。简介用仓库自己的英文简介，不调模型。新进榜的标「新」。
- **动态**：Hugging Face 每日论文、arXiv 按关注主题的最新论文、配置里的 RSS / Atom 订阅。
- **能力地图**（config/bench_capabilities.yaml）：能给生成的工作台加本事的工具，每项写用途、许可证、
  花费、接入难度、适合哪类工作台。刷新时程序去 GitHub 核实星数、许可证和最近更新。
- **建议卡**：按关注主题去 GitHub 找新工具，≥ 门槛星数、许可证宽松、地图里还没有的出建议卡；
  你点「加进地图」才加，点「不要」以后不再推。
- **交给写窗口的助手**：按工作台类型挑地图里合适的工具（和榜单前几名）写进 <sources>。

GitHub 搜索接口匿名每分钟 10 次：请求之间按配置间隔（测试换成假的 sleep）。存在 data/bench/_desk/。
外部标题和简介只当资料：在页面上转义显示，写进提示词时放在标签里。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from roundtable.core.benchkit.research import parse_arxiv
from roundtable.core.benchkit.scout import PERMISSIVE
from roundtable.core.browse import FeedError, parse_feed
from roundtable.core.config.loader import (
    DEFAULT_CONFIG_DIR,
    ConfigError,
    _format_errors,
    _read_yaml,
)

FetchJSON = Callable[[str], Awaitable[Any]]
FetchText = Callable[[str], Awaitable[str]]
Sleep = Callable[[float], Awaitable[None]]

CAPS_FILE = "bench_capabilities.yaml"
DESK_DIR = "_desk"
GITHUB = "https://api.github.com"
HF_PAPERS = "https://huggingface.co/api/daily_papers"
ARXIV = "https://export.arxiv.org/api/query?search_query={q}&sortBy=submittedDate&max_results={n}"
STATUS = ("没接", "借了思路", "已接入")


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,40}$")
    direction: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=40)
    repo: str = Field(default="", pattern=r"^$|^[\w.-]+/[\w.-]+$")
    use: str = Field(min_length=1, max_length=120)
    license: str = ""
    cost: str = "$0"
    difficulty: str = Field(default="中", pattern=r"^(低|中|高)$")
    kinds: list[str] = []
    status: str = Field(default="没接", pattern=r"^(没接|借了思路|已接入)$")
    note: str = ""


class CapabilityMap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capabilities: list[Capability] = []


def load_caps(path: Path | str = DEFAULT_CONFIG_DIR / CAPS_FILE) -> CapabilityMap:
    path = Path(path)
    if not path.is_file():
        return CapabilityMap()
    try:
        got = CapabilityMap.model_validate(_read_yaml(path) or {})
    except ValidationError as exc:
        raise ConfigError(_format_errors(path.name, exc)) from None
    ids = [c.id for c in got.capabilities]
    if len(ids) != len(set(ids)):
        raise ConfigError(f"{path.name}：能力地图里有重复的 id")
    return got


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="seconds")


def _text(v: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


def repo_of(x: dict[str, Any]) -> dict[str, Any] | None:
    """GitHub 接口返回的一个仓库 → 榜单 / 地图用的几样。

    名字、星数、简介、许可证、最近更新、主题。"""
    name = str(x.get("full_name") or "")
    url = str(x.get("html_url") or "")
    if "/" not in name or not url.startswith("https://github.com/"):
        return None
    lic = (x.get("license") or {}).get("spdx_id") or ""
    return {
        "repo": name,
        "url": url,
        "stars": int(x.get("stargazers_count") or 0),
        "desc": _text(x.get("description"), 200),
        "license": "" if lic == "NOASSERTION" else str(lic),
        "pushed": str(x.get("pushed_at") or "")[:10],
        "created": str(x.get("created_at") or "")[:10],
        "topics": [str(t) for t in (x.get("topics") or [])][:5],
    }


def parse_hf(data: Any, limit: int) -> list[dict[str, Any]]:
    """Hugging Face 每日论文：标题、链接、点赞、摘要。"""
    out = []
    for x in data if isinstance(data, list) else []:
        p = (x or {}).get("paper") or {}
        pid = str(p.get("id") or "")
        title = _text(p.get("title") or x.get("title"), 200)
        if not pid or not title or not re.fullmatch(r"[\w.]+", pid):
            continue
        out.append(
            {
                "kind": "paper",
                "title": title,
                "url": f"https://huggingface.co/papers/{pid}",
                "source": "Hugging Face 每日论文",
                "date": str(x.get("publishedAt") or p.get("publishedAt") or "")[:10],
                "summary": _text(p.get("summary"), 300),
                "score": int(p.get("upvotes") or 0),
            }
        )
    out.sort(key=lambda r: -r["score"])
    return out[:limit]


class Desk:
    """资料台：刷新（联网、免费）、读缓存、处理建议卡、给写窗口的助手挑工具。"""

    def __init__(
        self,
        rules: Any,
        root: Path | None,
        *,
        fetch_json: FetchJSON,
        fetch_text: FetchText,
        caps: CapabilityMap | None = None,
        sleep: Sleep = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        min_stars: int = 100,
    ) -> None:
        self.rules = rules
        self.root = root
        self.fetch_json = fetch_json
        self.fetch_text = fetch_text
        if caps is None:
            try:
                caps = load_caps()
            except ConfigError:
                caps = CapabilityMap()
        self.caps = caps
        self.sleep = sleep
        self.now = now
        self.min_stars = min_stars
        self._task: asyncio.Task[None] | None = None

    # --- 存取 ---------------------------------------------------------------------

    @property
    def path(self) -> Path | None:
        return None if self.root is None else self.root / DESK_DIR / "desk.json"

    def load(self) -> dict[str, Any]:
        p = self.path
        if p is not None and p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return {"updated_at": None, "rank": {}, "feed": [], "caps": {}, "suggest": [], "notes": []}

    def _save(self, d: dict[str, Any]) -> None:
        p = self.path
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def view(self) -> dict[str, Any]:
        """界面用：缓存 + 能力地图（配置里的加你同意加的，带核实结果）+ 是否在刷新。"""
        d = self.load()
        caps = []
        for c in self.caps.capabilities:
            caps.append(c.model_dump() | (d.get("caps") or {}).get(c.id, {}) | {"added": False})
        for s in d.get("suggest") or []:
            if s.get("state") == "added":
                caps.append(
                    {
                        "id": s["id"],
                        "direction": s["direction"],
                        "name": s["repo"].split("/")[-1],
                        "repo": s["repo"],
                        "use": s["desc"],
                        "license": s["license"],
                        "cost": "$0",
                        "difficulty": "中",
                        "kinds": s.get("kinds") or [],
                        "status": "没接",
                        "note": "你从建议卡加的",
                        "stars": s["stars"],
                        "added": True,
                    }
                )
        cats = [
            {"id": c.id, "label": c.label, "kinds": list(c.kinds)} for c in self.rules.categories
        ]
        due = self.due()
        return {
            "enabled": self.rules.enabled,
            "updated_at": d.get("updated_at"),
            "busy": self.busy,
            "due": due,
            "categories": cats,
            "rank": d.get("rank") or {},
            "feed": d.get("feed") or [],
            "caps": caps,
            "suggest": [s for s in d.get("suggest") or [] if s.get("state") == "open"],
            "notes": d.get("notes") or [],
            "topics": list(self.rules.topics),
            "rising_days": self.rules.rising_days,
        }

    def due(self) -> bool:
        """离上次刷新超过 refresh_hours（或从没刷新过）。"""
        at = self.load().get("updated_at")
        if not at:
            return True
        return datetime.fromisoformat(at) + timedelta(hours=self.rules.refresh_hours) <= self.now()

    # --- 刷新 ---------------------------------------------------------------------

    def start_refresh(self) -> bool:
        """后台刷新一次（已在刷新就不重复）。返回是否真的开始了。"""
        if self.busy or not self.rules.enabled:
            return False
        self._task = asyncio.create_task(self.refresh())
        return True

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _gh(self, q: str, n: int, notes: list[str]) -> list[dict[str, Any]]:
        url = f"{GITHUB}/search/repositories?q={quote(q)}&sort=stars&order=desc&per_page={n}"
        try:
            data = await self.fetch_json(url)
        except Exception as exc:  # noqa: BLE001 - 一类没取到只记一笔，接着取别的
            notes.append(f"GitHub 搜索「{q}」没取到：{str(exc)[:80]}")
            return []
        finally:
            await self.sleep(self.rules.github_gap_s)
        return [r for r in (repo_of(x) for x in (data or {}).get("items") or []) if r][:n]

    async def refresh(self) -> dict[str, Any]:
        old = self.load()
        notes: list[str] = []
        now = self.now()
        rank: dict[str, Any] = {}
        since = (now - timedelta(days=self.rules.rising_days)).date().isoformat()
        for c in self.rules.categories:
            before = {r["repo"] for r in ((old.get("rank") or {}).get(c.id) or {}).get("total", [])}
            total = await self._gh(c.query, self.rules.top, notes)
            rising = await self._gh(f"{c.query} created:>{since}", self.rules.top, notes)
            for r in total:
                r["new"] = bool(before) and r["repo"] not in before
            rank[c.id] = {"label": c.label, "total": total, "rising": rising}
        caps = dict(old.get("caps") or {})
        for cap in self.caps.capabilities:
            # 只读核实：读不到不装作有数，写明「不可探测」和原因（上次核实到的数留着、标日期）
            if not cap.repo:
                caps[cap.id] = {"unprobed": "没有 GitHub 仓库，星数和许可证没法核实"}
                continue
            try:
                got = repo_of(await self.fetch_json(f"{GITHUB}/repos/{cap.repo}"))
                why = "" if got else "GitHub 返回的内容看不懂"
            except Exception as exc:  # noqa: BLE001
                got, why = None, str(exc)[:80] or "连不上"
                notes.append(f"核实 {cap.repo} 没成：{why}")
            if got:
                caps[cap.id] = {
                    "stars": got["stars"],
                    "license_seen": got["license"],
                    "pushed": got["pushed"],
                    "verified": _iso(now),
                }
            else:
                caps[cap.id] = dict(caps.get(cap.id) or {}) | {"unprobed": f"这次没读到：{why}"}
        feed = await self._feed(notes)
        suggest = (
            await self._suggest(old, notes) if self._suggest_due(old) else old.get("suggest") or []
        )
        d = {
            "updated_at": _iso(now),
            "suggested_at": _iso(now) if self._suggest_due(old) else old.get("suggested_at"),
            "rank": rank,
            "feed": feed or old.get("feed") or [],
            "caps": caps,
            "suggest": suggest,
            "notes": notes[:20],
        }
        self._save(d)
        return d

    async def _feed(self, notes: list[str]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        n = self.rules.feed_per_source
        try:
            items += parse_hf(await self.fetch_json(HF_PAPERS), n)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Hugging Face 每日论文没取到：{str(exc)[:80]}")
        for t in self.rules.topics:
            q = quote(f'all:"{t}"')
            try:
                papers = parse_arxiv(await self.fetch_text(ARXIV.format(q=q, n=n)))
            except Exception as exc:  # noqa: BLE001
                notes.append(f"arXiv「{t}」没取到：{str(exc)[:80]}")
                continue
            for p in papers[:n]:
                items.append(
                    {
                        "kind": "paper",
                        "title": p.title,
                        "url": p.url,
                        "source": f"arXiv · {t}",
                        "date": p.date,
                        "summary": p.summary,
                        "score": 0,
                    }
                )
        for f in self.rules.feeds:
            try:
                raw = await self.fetch_text(f.url)
                got = parse_feed(raw.encode("utf-8"), n)
            except (FeedError, Exception) as exc:  # noqa: BLE001
                notes.append(f"订阅「{f.name}」没取到：{str(exc)[:80]}")
                continue
            for x in got:
                items.append(
                    {
                        "kind": f.kind,
                        "title": _text(x["title"], 200),
                        "url": x["url"],
                        "source": f.name,
                        "date": str(x.get("published") or "")[:10],
                        "summary": _text(x.get("summary"), 300),
                        "score": 0,
                    }
                )
        seen: set[str] = set()
        out = []
        for x in items:
            if x["url"].startswith("https://") and x["url"] not in seen:
                seen.add(x["url"])
                out.append(x)
        out.sort(key=lambda r: r["date"] or "", reverse=True)
        return out[: self.rules.feed_max]

    def _suggest_due(self, old: dict[str, Any]) -> bool:
        at = old.get("suggested_at")
        if not at:
            return True
        return datetime.fromisoformat(at) + timedelta(days=self.rules.suggest_days) <= self.now()

    async def _suggest(self, old: dict[str, Any], notes: list[str]) -> list[dict[str, Any]]:
        """按关注主题找新工具：星数够、许可证宽松、地图和旧建议里都没有的出建议卡。"""
        keep = list(old.get("suggest") or [])
        known = {c.repo.lower() for c in self.caps.capabilities if c.repo}
        known |= {s["repo"].lower() for s in keep}
        since = (self.now() - timedelta(days=365)).date().isoformat()
        for t in self.rules.topics:
            slug = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
            q = f"topic:{slug} stars:>={self.min_stars} pushed:>{since}"
            for r in await self._gh(q, 5, notes):
                if r["repo"].lower() in known or r["license"] not in PERMISSIVE:
                    continue
                known.add(r["repo"].lower())
                keep.append(
                    r
                    | {
                        "id": "s-" + re.sub(r"[^a-z0-9]+", "-", r["repo"].lower()).strip("-")[:40],
                        "direction": t,
                        "kinds": [],
                        "state": "open",
                        "at": _iso(self.now()),
                    }
                )
        return keep[-60:]

    def decide(self, sid: str, accept: bool) -> dict[str, Any]:
        """建议卡：加进地图（added）或不要（dismissed，以后不再推）。"""
        d = self.load()
        s = next((x for x in d.get("suggest") or [] if x["id"] == sid), None)
        if s is None:
            raise LookupError("没有这张建议卡")
        s["state"] = "added" if accept else "dismissed"
        self._save(d)
        return self.view()

    # --- 交给写窗口的助手 -----------------------------------------------------------

    def text_for(self, kind: str, limit: int = 8) -> str:
        """按工作台类型挑能力地图里的工具（和对口榜单的前几名），写成给写窗口助手看的几行。"""
        v = self.view()
        caps = [c for c in v["caps"] if not c.get("kinds") or kind in c["kinds"]]
        caps.sort(key=lambda c: (kind not in (c.get("kinds") or []), c.get("difficulty") != "低"))
        lines = [
            f"- {c['name']}（{c['direction']}；{c['use']}；许可证 {c.get('license') or '未写'}；"
            f"{c.get('cost') or '$0'}；接入难度{c.get('difficulty') or '中'}"
            + (f"；GitHub {c['repo']}" if c.get("repo") else "")
            + "）"
            for c in caps[:limit]
        ]
        for cat in v["categories"]:
            if kind and kind in cat["kinds"]:
                top = ((v["rank"].get(cat["id"]) or {}).get("total") or [])[:3]
                lines += [
                    f"- 榜单·{cat['label']}：{r['repo']} ★{r['stars']}"
                    f"（{r['license'] or '许可证未写'}）"
                    f"{r['desc'][:80]}"
                    for r in top
                ]
        return "\n".join(lines)
