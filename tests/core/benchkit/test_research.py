"""调研员：GitHub 至少 10 个（不够就拆词再搜，还不够如实写）、论文、dsh 插件、资讯链接核对。

网络全换成假的。
"""

from __future__ import annotations

import json

import pytest

from roundtable.core.benchkit.research import (
    ARXIV,
    S2,
    Researcher,
    clean_queries,
    parse_arxiv,
    parse_news,
    parse_s2,
    report_markdown,
)
from roundtable.core.benchkit.scout import DSH_INDEX, GITHUB

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2401.00001v1</id><title>Layered  Image
 Editing</title><published>2024-01-02T00:00:00Z</published><summary>We edit.</summary></entry>
<entry><id>http://arxiv.org/abs/2401.00002v1</id><title>Mahjong AI</title>
<published>2024-02-03T00:00:00Z</published><summary>Play.</summary></entry>
</feed>"""


def repo(name, stars, lic="MIT", desc="photo editor canvas", archived=False):
    return {
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "stargazers_count": stars,
        "license": {"spdx_id": lic},
        "description": desc,
        "size": 100,
        "archived": archived,
        "pushed_at": "2026-09-01T00:00:00Z",
    }


class Net:
    def __init__(self, pages):
        self.pages = pages  # 搜索词（url 里 q= 后面的部分）→ 仓库列表
        self.urls: list[str] = []

    async def fetch_json(self, url):
        self.urls.append(url)
        if url.startswith(f"{GITHUB}/search"):
            for key, items in self.pages.items():
                if f"q={key}&" in url:
                    return {"items": items}
            return {"items": []}
        if url.startswith(S2):
            return {
                "data": [
                    {
                        "title": "Mahjong AI",
                        "url": "https://www.semanticscholar.org/p/1",
                        "year": 2024,
                        "venue": "AAAI",
                        "citationCount": 30,
                    },
                    {"title": "No link", "url": ""},
                ]
            }
        if url == DSH_INDEX:
            return {
                "plugins": [
                    {
                        "name": "dsh-canvas",
                        "summary": "photo canvas editor",
                        "repo": "https://github.com/x/dsh-canvas",
                        "stars": 40,
                    },
                ]
            }
        raise AssertionError(url)

    async def fetch_text(self, url):
        self.urls.append(url)
        assert url.startswith(ARXIV)
        return ATOM


def test_clean_queries_drops_chinese_short_and_duplicates():
    assert clean_queries(["修图", "ps", "Photo Editor", "photo editor", "  canvas   layers "]) == [
        "Photo Editor",
        "canvas layers",
    ]


def test_parsers():
    papers = parse_arxiv(ATOM)
    assert papers[0].title == "Layered Image Editing"
    assert papers[0].url.startswith("https://arxiv.org/")
    assert papers[0].date == "2024-01-02"
    assert parse_arxiv("not xml") == []
    s2 = parse_s2(
        {"data": [{"title": "T", "url": "https://s2/1", "year": 2023, "citationCount": 5}]}
    )
    assert s2[0].summary == "被引 5 次" and s2[0].stars == 5
    news = parse_news(
        [
            {"title": "发布会", "url": "https://a/1", "kind": "launch", "date": "2026-10-01"},
            {"title": "重复", "url": "https://a/1"},
            {"title": "没链接", "url": "http://insecure"},
            {"title": "怪种类", "url": "https://a/2", "kind": "podcast"},
            "不是对象",
        ],
        limit=8,
    )
    assert [(n.kind, n.url) for n in news] == [("launch", "https://a/1"), ("news", "https://a/2")]


@pytest.mark.asyncio
async def test_github_reaches_ten_by_shorter_phrases_and_ranks_by_relevance():
    big = [repo(f"a/r{i}", 1000 + i) for i in range(6)]
    more = [repo(f"b/r{i}", 500 + i, desc="photo editor thing") for i in range(6)]
    tiny = [repo("c/tiny", 3)]  # 星太少：不列
    old = [repo("c/old", 9000, archived=True)]  # 归档：不列
    net = Net({"photo%20editor%20canvas": big + tiny + old, "photo%20editor": more})
    r = Researcher(fetch_json=net.fetch_json, fetch_text=net.fetch_text, min_github=10)
    rep = await r.run(["photo editor canvas layers"], [])
    names = [g.title for g in rep.github]
    assert len(names) == 12 and "c/tiny" not in names and "c/old" not in names
    assert names[0].startswith("a/")  # 命中词多的排前面
    assert all(g.verdict == "adopt" for g in rep.github)
    assert rep.github[0].date == "2026-09-01"
    assert not [n for n in rep.notes if "GitHub" in n]


@pytest.mark.asyncio
async def test_github_never_falls_back_to_single_words_and_drops_one_word_matches():
    """2026-10-31 修图台真跑：长句搜不到，退到单个词 perspective 按星数排，
    拿回一堆不相干的热门项目。"""
    queries = [
        "photo editor canvas layers curves HSL javascript",
        "javascript perspective correction crop image editor",
        "film emulation LUT VSCO style open source",
    ]
    junk = [
        repo("p/perspective", 11000, desc="A data visualization and analytics component"),
        repo(
            "c/council", 4700, desc="Structured multi-perspective deliberation for hard decisions"
        ),
    ]
    good = [
        repo(
            "j/jpegview",
            3000,
            desc="fast viewer/editor for JPEG images with on-the-fly image processing",
        ),
        repo("f/filmlut", 800, desc="Film emulation LUT pack"),
    ]
    net = Net(
        {
            "perspective": junk,
            "photo%20editor%20canvas": good[:1] + junk,
            "film%20emulation": good[1:],
        }
    )
    r = Researcher(fetch_json=net.fetch_json, fetch_text=net.fetch_text, min_github=10)
    rep = await r.run(queries, [])
    assert [g.title for g in rep.github] == ["f/filmlut", "j/jpegview"]  # 命中 3 个词的在前
    searched = [
        u.split("q=")[1].split("&")[0] for u in net.urls if u.startswith(f"{GITHUB}/search")
    ]
    assert "perspective" not in searched and all("%20" in q for q in searched)
    assert any("只找到 2 个" in n for n in rep.notes)


@pytest.mark.asyncio
async def test_github_short_is_reported_honestly_and_queries_are_capped():
    net = Net({"rare%20thing": [repo("a/one", 300, desc="a rare thing")]})
    r = Researcher(fetch_json=net.fetch_json, fetch_text=net.fetch_text, min_github=10)
    rep = await r.run(["rare thing", "alpha beta gamma delta", "epsilon zeta eta"], [])
    assert len(rep.github) == 1
    assert any("只找到 1 个" in n for n in rep.notes)
    searches = [u for u in net.urls if u.startswith(f"{GITHUB}/search")]
    assert len(searches) <= 8  # GitHub 匿名搜索有限流


@pytest.mark.asyncio
async def test_papers_plugins_and_news_verification():
    checked = []

    async def check(url):
        checked.append(url)
        return url.endswith("ok")

    net = Net({})
    r = Researcher(fetch_json=net.fetch_json, fetch_text=net.fetch_text, check_url=check)
    news = [
        {"title": "真的", "url": "https://x/ok", "kind": "video"},
        {"title": "假的", "url": "https://x/bad"},
    ]
    rep = await r.run(["photo canvas editor"], news)
    titles = [p.title for p in rep.papers]
    assert titles.count("Mahjong AI") == 1  # arXiv 和 Semantic Scholar 重复的只留一篇
    assert "Layered Image Editing" in titles
    assert [p.title for p in rep.plugins] == ["x/dsh-canvas"]
    assert {n.url: n.verified for n in rep.news} == {"https://x/ok": True, "https://x/bad": False}
    d = rep.to_dict()
    assert d["counts"] == {"github": 0, "papers": 2, "news": 2, "plugins": 1}
    md = report_markdown(
        "修图台",
        "做个修图台",
        d | {"cards": [{"repo": "a/b", "what": "画布", "borrow": ["图层"], "avoid": []}]},
    )
    assert "链接打不开" in md and "学习卡" in md and "借：图层" in md
    json.dumps(d, ensure_ascii=False)


@pytest.mark.asyncio
async def test_broken_network_still_gives_a_report_with_notes():
    async def boom(url):
        raise OSError("offline")

    r = Researcher(fetch_json=boom, fetch_text=boom)
    rep = await r.run(["photo editor"], [])
    assert rep.github == [] and rep.papers == []
    joined = "".join(rep.notes)
    assert "arXiv 打不开" in joined and "dsh 插件集市打不开" in joined and "资讯" in joined
