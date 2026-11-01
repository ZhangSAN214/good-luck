"""找插件：门槛判定、集市匹配、搜索合并、安全解压（不联网）。"""

from __future__ import annotations

import io
import json
import tarfile

import pytest

from roundtable.core.benchkit.builder import BenchBuilder
from roundtable.core.benchkit.scout import (
    DSH_INDEX,
    Candidate,
    Scout,
    judge,
    match_dsh,
    repo_of,
    safe_extract,
)
from roundtable.core.config.schema import BenchRules

from .test_builder import PLAN, FakeSC, fake_check, render

SHA = "a" * 40


def tarball(files: dict[str, bytes], extra=()) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(f"owner-repo-{SHA[:7]}/{name}")
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
        for info in extra:
            tar.addfile(info, io.BytesIO(b"x" * info.size) if info.size else None)
    return buf.getvalue()


def c(**kw):
    base = dict(
        source="github",
        repo="o/r",
        url="https://github.com/o/r",
        stars=500,
        license="MIT",
        summary="maps",
    )
    base.update(kw)
    return Candidate(**base)


@pytest.mark.parametrize(
    ("kw", "verdict"),
    [
        ({}, "adopt"),
        ({"stars": 53}, "idea"),
        ({"license": "GPL-3.0"}, "idea"),
        ({"license": ""}, "idea"),
        ({"license": "NOASSERTION"}, "idea"),
        ({"summary": "needs your API key"}, "idea"),
        ({"size_kb": 900000}, "idea"),
        ({"archived": True}, "skip"),
    ],
)
def test_judge_follows_user_rules(kw, verdict):
    got = judge(c(**kw), min_stars=100, max_kb=20000)
    assert got.verdict == verdict and got.why


def test_repo_of_and_match_dsh():
    assert repo_of("https://github.com/a/b") == "a/b"
    assert repo_of("https://github.com/a/b.git") == "a/b"
    assert repo_of("https://gitlab.com/a/b") == ""
    index = {
        "plugins": [
            {
                "name": "leaflet-maps",
                "summary": "interactive map tiles",
                "repo": "https://github.com/x/maps",
                "stars": 50,
            },
            {
                "name": "map-kit",
                "summary": "map route planner",
                "repo": "https://github.com/y/route",
                "stars": 900,
            },
            {
                "name": "notes",
                "summary": "markdown",
                "repo": "https://github.com/z/notes",
                "stars": 9999,
            },
            {"name": "bad", "summary": "map", "repo": "not a url"},
        ]
    }
    got = match_dsh(index, "map route", limit=3)
    assert [x.repo for x in got] == ["y/route"]  # 只命中一个词的 x/maps 不算
    assert [x.repo for x in match_dsh(index, "map", limit=3)] == ["y/route", "x/maps"]
    assert match_dsh(index, "", limit=3) == []


class Net:
    def __init__(self, blob=b""):
        self.urls: list[str] = []
        self.blob = blob

    async def json(self, url):
        self.urls.append(url)
        if url == DSH_INDEX:
            return {
                "plugins": [
                    {
                        "name": "leaflet route map",
                        "summary": "map route",
                        "repo": "https://github.com/d/route",
                        "stars": 10,
                    }
                ]
            }
        if "/search/repositories" in url:
            return {
                "items": [
                    {
                        "full_name": "g/leaflet",
                        "html_url": "https://github.com/g/leaflet",
                        "stargazers_count": 40000,
                        "license": {"spdx_id": "BSD-2-Clause"},
                        "description": "maps",
                        "size": 3000,
                        "default_branch": "main",
                    },
                    {"full_name": "d/route", "stargazers_count": 1, "license": None},
                ]
            }
        if url.endswith("/repos/d/route"):
            return {
                "license": {"spdx_id": "MIT"},
                "stargazers_count": 320,
                "size": 100,
                "default_branch": "dev",
            }
        if "/commits/" in url:
            return {"sha": SHA}
        raise AssertionError(url)

    async def bytes(self, url):
        self.urls.append(url)
        return self.blob


async def test_search_merges_dsh_and_github_and_fills_license():
    net = Net()
    scout = Scout(fetch_json=net.json, fetch_bytes=net.bytes)
    got = {x.repo: x for x in await scout.search("真地图", "map route")}
    assert set(got) == {"d/route", "g/leaflet"}
    assert (
        got["d/route"].source == "dsh"
        and got["d/route"].license == "MIT"
        and got["d/route"].stars == 320
    )
    assert got["d/route"].verdict == "adopt" and got["g/leaflet"].verdict == "adopt"
    assert all(x.need == "真地图" for x in got.values())


async def test_long_keywords_retry_as_short_phrase_not_single_words():
    urls: list[str] = []

    async def json_(url):
        urls.append(url)
        if url == DSH_INDEX:
            return {"plugins": []}
        if "/search/repositories" in url:
            if "q=image%20crop%20stars" in url:
                return {
                    "items": [
                        {"full_name": "g/cropper", "stargazers_count": 7000, "description": "crop"}
                    ]
                }
            return {"items": []}
        if url.endswith("/repos/g/cropper"):
            return {"license": {"spdx_id": "MIT"}, "stargazers_count": 7000, "size": 10}
        raise AssertionError(url)

    scout = Scout(fetch_json=json_, fetch_bytes=json_)
    got = await scout.search("裁剪", "image crop perspective transform")
    assert [x.repo for x in got] == ["g/cropper"]
    searched = [u.split("q=")[1].split("%20stars")[0] for u in urls if "/search/" in u]
    assert searched == [
        "image%20crop%20perspective%20transform",
        "image%20crop%20perspective",
        "image%20crop",
    ]


async def test_scout_survives_network_errors():
    async def boom(url):
        raise OSError("断网")

    scout = Scout(fetch_json=boom, fetch_bytes=boom)
    assert await scout.search("x", "map") == []


def test_safe_extract_blocks_escapes_links_and_exec_bits(tmp_path):
    link = tarfile.TarInfo("owner-repo/evil-link")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
    up = tarfile.TarInfo("owner-repo/../../outside.txt")
    up.size = 1
    blob = tarball({"LICENSE": b"MIT", "src/map.js": b"export {}"}, extra=[link, up])
    n = safe_extract(blob, tmp_path / "v", limit=10_000)
    assert n == 2
    assert (tmp_path / "v" / "src" / "map.js").read_bytes() == b"export {}"
    assert not (tmp_path / "v" / "evil-link").exists()
    assert not (tmp_path / "outside.txt").exists()
    assert ((tmp_path / "v" / "LICENSE").stat().st_mode & 0o111) == 0
    with pytest.raises(ValueError):
        safe_extract(tarball({"big": b"x" * 5000}), tmp_path / "w", limit=1000)


async def test_fetch_pins_commit_and_records_provenance(tmp_path):
    net = Net(tarball({"LICENSE": b"MIT", "a.js": b"1"}))
    scout = Scout(fetch_json=net.json, fetch_bytes=net.bytes)
    rec = await scout.fetch(c(repo="d/route", branch="dev", need="真地图"), tmp_path / "d__route")
    assert rec["commit"] == SHA and len(rec["sha256"]) == 64 and rec["files"] == 2
    assert f"codeload.github.com/d/route/tar.gz/{SHA}" in net.urls[-1]
    saved = json.loads((tmp_path / "d__route" / "SOURCE.json").read_text(encoding="utf-8"))
    assert saved["license"] == "MIT" and saved["need"] == "真地图"


async def test_builder_adopts_one_per_need_and_tells_writers(tmp_path):
    net = Net(tarball({"LICENSE": b"MIT", "a.js": b"1"}))
    scout = Scout(fetch_json=net.json, fetch_bytes=net.bytes)
    sc = FakeSC()
    b = BenchBuilder(
        BenchRules(),
        root=tmp_path,
        supercomputer=sc,
        render=render,
        check=fake_check(),
        scout=scout,
        seed=2,
    )
    build = b.submit("旅行台")
    await b.wait()
    assert build["status"] == "done", build["log"]
    adopted = [s for s in build["sources"] if s["verdict"] == "adopt"]
    assert len(adopted) == 1  # 一项需求只搬一个
    assert any(s["verdict"] == "idea" and "已经搬了一个" in s["why"] for s in build["sources"])
    folder = tmp_path / build["id"]
    assert (folder / adopted[0]["folder"] / "SOURCE.json").is_file()
    assert json.loads((folder / "sources.json").read_text(encoding="utf-8"))
    window_prompts = [x for x in sc.prompts if '"bench_window"' in x]
    assert window_prompts and all("可以直接用：vendor/" in x for x in window_prompts)
    assert PLAN["needs"][0]["need"] in window_prompts[0]


async def test_scout_off_skips_search(tmp_path):
    net = Net()
    scout = Scout(fetch_json=net.json, fetch_bytes=net.bytes)
    b = BenchBuilder(
        BenchRules(scout=False),
        root=tmp_path,
        supercomputer=FakeSC(),
        render=render,
        check=fake_check(),
        scout=scout,
    )
    build = b.submit("旅行台")
    await b.wait()
    assert build["sources"] == [] and net.urls == []
