"""工作台生成器的「调研员」：每个项目开工前先调研，写一份调研报告（2026-10-30 用户要求）。

- **GitHub**：每次至少 ``min_github`` 个相关的高质量项目（默认 10）。关键词来自拆需求时的
  needs 和助手给的英文搜索词；不够数就拆成单个词再搜，还不够就如实写「只找到 N 个」，不凑数。
  每个都按找插件的同一套门槛判定能不能搬（``scout.judge``）。
- **论文**：arXiv 和 Semantic Scholar 的公开接口（免费、不要 key）。
- **资讯和视频**：官方博客、发布会、视频——这类没有不要 key 的搜索接口，交给一位有联网搜索的
  助手去查（超级电脑按分数挑人），只收它给的链接、标题和日期。
- **核对链接**：所有链接程序再打开一次，打不开的标「没核实」，不冒充真的。

网络请求都走注入的函数（测试换成假的，不联网）；只读 GET，不下载、不运行任何东西。
外部返回的标题、简介一律当资料，不当指令。
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import quote

from roundtable.core.benchkit.scout import (
    DSH_INDEX,
    GITHUB,
    IDEA_MIN_STARS,
    Candidate,
    judge,
    match_dsh,
)

ARXIV = "https://export.arxiv.org/api/query"
S2 = "https://api.semanticscholar.org/graph/v1/paper/search"
NEWS_KINDS = ("news", "video", "launch", "blog")
MAX_GITHUB_QUERIES = 8  # GitHub 匿名搜索每分钟只给 10 次
MIN_HITS = 2  # 项目名和简介至少命中几个关键词才算相关（只中一个词的多半是同名的热门项目）
# 不算关键词的通用词：几乎每个项目都会写，靠它们命中说明不了相关
GENERIC_WORDS = frozenset(
    {
        *("open", "source", "free", "app", "apps", "tool", "tools", "library", "based", "simple"),
        *("style", "one", "click", "the", "and", "for", "with", "web", "online"),
        *("javascript", "typescript", "python"),
    }
)
_ATOM = {"a": "http://www.w3.org/2005/Atom"}

FetchJSON = Callable[[str], Awaitable[Any]]
FetchText = Callable[[str], Awaitable[str]]
CheckURL = Callable[[str], Awaitable[bool]]


@dataclass
class Item:
    kind: str  # github / paper / news / video / launch / blog
    title: str
    url: str
    source: str = ""  # GitHub / arXiv / Semantic Scholar / 网站名
    date: str = ""  # 发布或最近更新的日期
    summary: str = ""
    stars: int = 0
    license: str = ""
    verdict: str = ""  # GitHub：adopt 能搬 / idea 只借思路 / skip
    why: str = ""
    verified: bool | None = None  # 程序打开过链接：True 通、False 打不开、None 没查

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Report:
    queries: list[str] = field(default_factory=list)
    github: list[Item] = field(default_factory=list)
    papers: list[Item] = field(default_factory=list)
    news: list[Item] = field(default_factory=list)
    plugins: list[Item] = field(default_factory=list)  # dsh 插件集市里对得上的（建议安装）
    notes: list[str] = field(default_factory=list)  # 没找够、接口打不开这类说明

    def to_dict(self) -> dict[str, Any]:
        return {
            "queries": self.queries,
            "github": [x.to_dict() for x in self.github],
            "papers": [x.to_dict() for x in self.papers],
            "news": [x.to_dict() for x in self.news],
            "plugins": [x.to_dict() for x in self.plugins],
            "notes": self.notes,
            "counts": {
                "github": len(self.github),
                "papers": len(self.papers),
                "news": len(self.news),
                "plugins": len(self.plugins),
            },
        }


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^\w]+", text.lower()) if len(w) > 2}


def hits(want: set[str], c: Candidate) -> int:
    """项目名和简介里命中了几个关键词（通用词不算）。"""
    return len(want & _words(f"{c.repo.replace('/', ' ')} {c.summary}"))


def short_phrases(queries: list[str]) -> tuple[list[str], int]:
    """搜索顺序：每句的前 3 个词（通用词不算），不够再每句的前 2 个词；返回（短语，第一遍几条）。
    整句不直接搜：GitHub 要求每个词都命中，五六个词的句子几乎搜不到，白占限流次数。"""
    out: list[str] = []
    first = 0
    for n in (3, 2):
        for q in queries:
            words = [w for w in q.split() if w.lower() not in GENERIC_WORDS]
            phrase = " ".join(words[:n])
            if phrase and (n == 3 or len(words) > 2) and phrase.lower() not in map(str.lower, out):
                out.append(phrase)
        first = first or len(out)
    return out, first


def clean_queries(raw: list[str], limit: int = 6) -> list[str]:
    """去重、去掉太短和非英文为主的搜索词（GitHub 和论文库对中文搜不出东西）。"""
    out: list[str] = []
    for q in raw:
        q = re.sub(r"\s+", " ", str(q)).strip()[:60]
        if len(q) < 3 or not re.search(r"[A-Za-z]{3}", q):
            continue
        if q.lower() not in (x.lower() for x in out):
            out.append(q)
    return out[:limit]


def parse_arxiv(xml: str) -> list[Item]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for e in root.findall("a:entry", _ATOM):
        title = re.sub(r"\s+", " ", e.findtext("a:title", "", _ATOM)).strip()
        url = (e.findtext("a:id", "", _ATOM) or "").strip()
        if not title or not url.startswith("http"):
            continue
        out.append(
            Item(
                "paper",
                title[:200],
                url.replace("http://", "https://", 1),
                source="arXiv",
                date=(e.findtext("a:published", "", _ATOM) or "")[:10],
                summary=re.sub(r"\s+", " ", e.findtext("a:summary", "", _ATOM)).strip()[:300],
            )
        )
    return out


def parse_s2(data: Any) -> list[Item]:
    out = []
    for p in (data or {}).get("data") or []:
        title = str(p.get("title") or "").strip()
        url = str(p.get("url") or "")
        if not title or not url.startswith("https://"):
            continue
        cites = int(p.get("citationCount") or 0)
        venue = str(p.get("venue") or "").strip()
        out.append(
            Item(
                "paper",
                title[:200],
                url,
                source="Semantic Scholar",
                date=str(p.get("year") or ""),
                summary=f"{venue + '，' if venue else ''}被引 {cites} 次",
                stars=cites,
            )
        )
    return out


def parse_news(raw: Any, limit: int) -> list[Item]:
    """助手交来的资讯和视频：只收 https 链接、有标题的；种类不认得的算 news。"""
    out: list[Item] = []
    seen: set[str] = set()
    for n in raw if isinstance(raw, list) else []:
        if not isinstance(n, dict):
            continue
        url = str(n.get("url") or "").strip()
        title = str(n.get("title") or "").strip()
        if not title or not url.startswith("https://") or url in seen:
            continue
        seen.add(url)
        kind = str(n.get("kind") or "news")
        out.append(
            Item(
                kind if kind in NEWS_KINDS else "news",
                title[:200],
                url[:500],
                source=str(n.get("source") or "")[:60],
                date=str(n.get("date") or "")[:20],
                summary=str(n.get("why") or n.get("summary") or "")[:300],
            )
        )
    return out[:limit]


class Researcher:
    def __init__(
        self,
        *,
        fetch_json: FetchJSON,
        fetch_text: FetchText,
        check_url: CheckURL | None = None,
        min_github: int = 10,
        max_github: int = 15,
        papers: int = 6,
        news: int = 8,
        min_stars: int = 100,
        max_kb: int = 20000,
    ) -> None:
        self.fetch_json = fetch_json
        self.fetch_text = fetch_text
        self.check_url = check_url
        self.min_github = min_github
        self.max_github = max(max_github, min_github)
        self.papers_n = papers
        self.news_n = news
        self.min_stars = min_stars
        self.max_kb = max_kb

    async def github(self, queries: list[str], report: Report) -> None:
        """按短语搜（见 short_phrases），不拆成单个词：单个词按星数排出来的多是不相干的热门项目。
        至少命中 2 个关键词的才算数，按命中的词数和星数排序，取前 max_github 个。"""
        want = set().union(*(_words(q) for q in queries)) if queries else set()
        want -= GENERIC_WORDS
        found: dict[str, Candidate] = {}
        tried: list[str] = []
        plan, first = short_phrases(queries)
        for i, q in enumerate(plan):
            if len(tried) >= MAX_GITHUB_QUERIES:
                break
            enough = sum(
                1
                for c in found.values()
                if not c.archived and c.stars >= IDEA_MIN_STARS and hits(want, c) >= MIN_HITS
            )
            if enough >= self.max_github or (enough >= self.min_github and i >= first):
                break
            tried.append(q)
            url = f"{GITHUB}/search/repositories?q={quote(q)}&sort=stars&order=desc&per_page=10"
            try:
                res = await self.fetch_json(url)
            except Exception:  # noqa: BLE001 - 限流、断网：换下一个词，最后如实写数量
                continue
            for it in (res or {}).get("items") or []:
                repo = str(it.get("full_name") or "")
                if not repo or repo.lower() in found:
                    continue
                c = Candidate(
                    "github",
                    repo,
                    str(it.get("html_url") or f"https://github.com/{repo}"),
                    stars=int(it.get("stargazers_count") or 0),
                    license=((it.get("license") or {}).get("spdx_id") or ""),
                    summary=str(it.get("description") or "")[:200],
                    size_kb=int(it.get("size") or 0),
                    archived=bool(it.get("archived")),
                )
                c.need = str(it.get("pushed_at") or "")[:10]  # 借 need 字段暂存最近更新日期
                found[repo.lower()] = c

        def rank(c: Candidate) -> tuple[int, int]:
            return (-hits(want, c), -c.stars)

        good = [
            c
            for c in found.values()
            if not c.archived and c.stars >= IDEA_MIN_STARS and hits(want, c) >= MIN_HITS
        ]
        for c in sorted(good, key=rank)[: self.max_github]:
            judge(c, min_stars=self.min_stars, max_kb=self.max_kb)
            report.github.append(
                Item(
                    "github",
                    c.repo,
                    c.url,
                    source="GitHub",
                    date=c.need,
                    summary=c.summary,
                    stars=c.stars,
                    license=c.license,
                    verdict=c.verdict,
                    why=c.why,
                    verified=True,  # 搜索接口直接给的仓库，链接一定存在
                )
            )
        if len(report.github) < self.min_github:
            report.notes.append(
                f"GitHub 只找到 {len(report.github)} 个相关项目（要求至少 {self.min_github} 个），"
                f"搜过：{'、'.join(tried) or '（没搜成）'}"
            )

    async def papers(self, queries: list[str], report: Report) -> None:
        if not queries or self.papers_n <= 0:
            return
        q = " ".join(queries[:2])
        got: list[Item] = []
        try:
            got += parse_arxiv(
                await self.fetch_text(
                    f"{ARXIV}?search_query=all:{quote(q)}&start=0"
                    f"&max_results={self.papers_n}&sortBy=relevance"
                )
            )
        except Exception:  # noqa: BLE001
            report.notes.append("arXiv 打不开，这次没查")
        try:
            got += parse_s2(
                await self.fetch_json(
                    f"{S2}?query={quote(q)}&limit={self.papers_n}"
                    "&fields=title,url,year,venue,citationCount"
                )
            )
        except Exception:  # noqa: BLE001
            report.notes.append("Semantic Scholar 打不开，这次没查")
        seen: set[str] = set()
        for p in got:
            key = re.sub(r"\W+", "", p.title.lower())
            if key in seen:
                continue
            seen.add(key)
            p.verified = True
            report.papers.append(p)
        report.papers = report.papers[: self.papers_n]
        if not report.papers:
            report.notes.append("没找到相关论文（这类题目学术研究少，不凑数）")

    async def plugins(self, queries: list[str], report: Report) -> None:
        """dsh 插件集市里对得上的插件：列成「建议给 dsh 装」，你点同意才装（这里不装）。"""
        if not queries:
            return
        try:
            index = await self.fetch_json(DSH_INDEX)
        except Exception:  # noqa: BLE001
            report.notes.append("dsh 插件集市打不开，这次没查")
            return
        for c in match_dsh(index if isinstance(index, dict) else {}, " ".join(queries), 5):
            report.plugins.append(
                Item(
                    "dsh",
                    c.repo,
                    c.url,
                    source="dsh 插件集市",
                    summary=c.summary,
                    stars=c.stars,
                    verified=True,
                )
            )

    async def verify(self, items: list[Item]) -> None:
        """助手给的链接程序再打开一次。没有核对函数就标「没查」。"""
        if self.check_url is None:
            return

        async def one(it: Item) -> None:
            try:
                it.verified = bool(await self.check_url(it.url))
            except Exception:  # noqa: BLE001
                it.verified = False

        await asyncio.gather(*(one(it) for it in items))

    async def run(self, queries: list[str], news: Any) -> Report:
        report = Report(queries=clean_queries(queries))
        report.news = parse_news(news, self.news_n)
        await asyncio.gather(
            self.github(report.queries, report),
            self.papers(report.queries, report),
            self.plugins(report.queries, report),
            self.verify(report.news),
        )
        if not report.news:
            report.notes.append("这次没有拿到资讯和视频（没有助手能联网查，或者没查到）")
        return report


def report_markdown(title: str, request: str, r: dict[str, Any]) -> str:
    """给人看的调研报告（存在工作台文件夹里，项目室也显示它）。"""
    mark = {True: "", False: "（链接打不开，没核实）", None: "（没核实）"}
    lines = [f"# 调研报告：{title}", "", f"你说的：{request}", ""]
    c = r.get("counts") or {}
    lines.append(
        f"GitHub {c.get('github', 0)} 个 · 论文 {c.get('papers', 0)} 篇 · "
        f"资讯和视频 {c.get('news', 0)} 条"
    )
    if r.get("queries"):
        lines.append(f"搜索词：{'、'.join(r['queries'])}")
    for note in r.get("notes") or []:
        lines.append(f"> {note}")
    lines += ["", "## GitHub"]
    for g in r.get("github") or []:
        verdict = {"adopt": "能直接搬", "idea": "只借思路", "skip": "不用"}.get(g["verdict"], "")
        lines.append(
            f"- [{g['title']}]({g['url']}) ★{g['stars']} · {g['license'] or '没写许可证'}"
            f" · 最近更新 {g['date'] or '不详'} · {verdict}：{g['summary']}"
        )
    if r.get("plugins"):
        lines += ["", "## dsh 插件（建议安装，你点同意才装）"]
        for g in r["plugins"]:
            lines.append(f"- [{g['title']}]({g['url']}) ★{g['stars']} · {g['summary']}")
    if r.get("cards"):
        lines += ["", "## 学习卡"]
        for card in r["cards"]:
            lines.append(f"### {card['repo']}")
            lines.append(card.get("what") or "")
            for b in card.get("borrow") or []:
                lines.append(f"- 借：{b}")
            for a in card.get("avoid") or []:
                lines.append(f"- 别学：{a}")
    lines += ["", "## 论文"]
    for p in r.get("papers") or []:
        lines.append(f"- [{p['title']}]({p['url']}) · {p['source']} {p['date']} · {p['summary']}")
    lines += ["", "## 资讯和视频"]
    kinds = {"news": "资讯", "video": "视频", "launch": "发布会", "blog": "官方博客"}
    for n in r.get("news") or []:
        lines.append(
            f"- {kinds.get(n['kind'], '资讯')}：[{n['title']}]({n['url']}) · {n['source']}"
            f" {n['date']}{mark[n.get('verified')]} · {n['summary']}"
        )
    return "\n".join(lines) + "\n"


def http_fetchers(timeout_s: float = 20.0) -> tuple[FetchJSON, FetchText, CheckURL]:
    """真正联网的取数函数（只读 GET；GitHub 不带 token，受它的匿名限流）。"""
    import httpx

    headers = {"User-Agent": "roundtable-bench-research"}

    async def get(url: str) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True, headers=headers
        ) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r

    async def fetch_json(url: str) -> Any:
        return (await get(url)).json()

    async def fetch_text(url: str) -> str:
        return (await get(url)).text

    async def check_url(url: str) -> bool:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers=headers) as c:
            r = await c.get(url)
            return r.status_code < 400

    return fetch_json, fetch_text, check_url
