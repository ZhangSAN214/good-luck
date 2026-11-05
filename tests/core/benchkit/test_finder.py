"""资源检索员（生成器第二版第 3 块）：假的取数函数，不联网。"""

from __future__ import annotations

from datetime import date

import pytest

from roundtable.core.benchkit.finder import (
    AssetFinder,
    credits_md,
    file_ok,
    found_lines,
    license_ok,
)
from roundtable.core.config.schema import BenchFinder, BenchNetService

JPG = b"\xff\xd8\xff\xe0" + b"0" * 100
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'
ALLOW = BenchFinder().licenses
K = "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models"


def commons_page(i, lic="CC BY-SA 4.0", ext="jpg"):
    return {
        "index": i,
        "title": f"File:Kyoto street {i}.{ext}",
        "imageinfo": [
            {
                "thumburl": f"https://upload.wikimedia.org/k{i}.{ext}",
                "descriptionurl": f"https://commons.wikimedia.org/wiki/File:K{i}",
                "extmetadata": {
                    "Artist": {"value": f'<a href="x">摄影师 {i}</a>'},
                    "LicenseShortName": {"value": lic},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                },
            }
        ],
    }


class Net:
    """按网址前缀回假数据；记下每次取了什么。"""

    def __init__(self, routes, files=None, bad=()):
        self.routes = routes
        self.files = files or {}
        self.bad = set(bad)
        self.got: list[str] = []

    async def fetch_json(self, url):
        self.got.append(url)
        for prefix, data in self.routes.items():
            if url.startswith(prefix):
                if isinstance(data, Exception):
                    raise data
                return data
        raise RuntimeError(f"没有假数据：{url}")

    async def fetch_bytes(self, url):
        self.got.append(url)
        return self.files.get(url, JPG)

    async def check_url(self, url):
        return not any(b in url for b in self.bad)


def finder(net, **rules):
    services = [
        BenchNetService(
            name="fx",
            use="汇率",
            url="https://api.frankfurter.dev/v1/latest?base={from}",
            source="欧洲央行",
        )
    ]
    return AssetFinder(
        fetch_json=net.fetch_json,
        fetch_bytes=net.fetch_bytes,
        check_url=net.check_url,
        rules=BenchFinder(**rules),
        net=services,
        today=lambda: date(2026, 11, 5),
    )


def test_license_rules():
    for ok in ("CC0 1.0", "CC BY-SA 4.0", "Public domain", "MIT", "OFL-1.1", "ISC"):
        assert license_ok(ok, ALLOW), ok
    for bad in ("CC BY-NC 4.0", "CC BY-ND 2.0", "CC BY-NC-SA 3.0", "", "All rights reserved"):
        assert not license_ok(bad, ALLOW), bad
    assert not license_ok("CC0 1.0；CC BY-NC 4.0", ALLOW)  # 有一条不宽松就整件不用
    assert file_ok("jpg", JPG) and not file_ok("jpg", b"<html>")
    assert file_ok("svg", SVG) and not file_ok("svg", b"<svg><script>alert(1)</script></svg>")


async def test_photos_keep_free_licenses_and_credit(tmp_path):
    pages = {str(i): commons_page(i) for i in range(1, 4)}
    pages["9"] = commons_page(9, lic="CC BY-NC 2.0")
    net = Net({"https://commons.wikimedia.org": {"query": {"pages": pages}}})
    f = finder(net, sources={"photo": ["commons"]})
    need = {"kind": "photo", "what": "京都街景", "query": "Kyoto street", "count": 2}
    res = await f.find_files(need, tmp_path, "assets/found/map", budget=10)
    assert res["status"] == "ok" and res["got"] == 2
    it = res["items"][0]
    assert it["author"] == "摄影师 1" and it["license"] == "CC BY-SA 4.0"
    assert it["date"] == "2026-11-05" and (tmp_path / it["file"]).read_bytes() == JPG
    assert it["file"].startswith("assets/found/map/kyoto-street-1")
    assert [c["title"] for c in res["spare"]] == ["Kyoto street 3.jpg"]  # 不宽松的那张没进备选
    md = credits_md([res])
    assert "摄影师 1" in md and "CC BY-SA 4.0" in md and "下载于 2026-11-05" in md


async def test_short_is_said_honestly_and_bad_files_skipped(tmp_path):
    pages = {"1": commons_page(1), "2": commons_page(2)}
    net = Net(
        {
            "https://commons.wikimedia.org": {"query": {"pages": pages}},
            "https://api.openverse.org": RuntimeError("down"),
        },
        files={"https://upload.wikimedia.org/k2.jpg": b"<html>error</html>"},
    )
    f = finder(net, sources={"photo": ["openverse", "commons"]})
    need = {"kind": "photo", "what": "京都街景", "query": "Kyoto", "count": 3}
    res = await f.find_files(need, tmp_path, "assets/found/map", budget=10)
    assert res["status"] == "short" and res["got"] == 1
    assert res["note"].startswith("只找到 1 个（要 3 个）")
    assert "openverse 连不上" in res["note"] and "没下成" in res["note"]
    lines = found_lines([res | {"window": "map"}], {"map": "地图"})
    assert "窗口「地图」要的照片「京都街景」：只找到 1 个" in lines[0]


async def test_too_big_file_is_not_kept(tmp_path):
    net = Net(
        {"https://commons.wikimedia.org": {"query": {"pages": {"1": commons_page(1)}}}},
        files={"https://upload.wikimedia.org/k1.jpg": JPG + b"0" * 2_000_000},
    )
    f = finder(net, sources={"photo": ["commons"]}, max_mb_each=1)
    need = {"kind": "photo", "what": "街景", "query": "street", "count": 1}
    res = await f.find_files(need, tmp_path, "a", budget=10)
    assert res["status"] == "none" and "太大" in res["note"]
    assert not any(tmp_path.rglob("*.jpg"))


async def test_icons_textures_models_and_fonts(tmp_path):
    net = Net(
        {
            "https://api.iconify.design/search": {
                "icons": ["lucide:map", "lucide:map-pin"],
                "collections": {
                    "lucide": {
                        "name": "Lucide",
                        "license": {"spdx": "ISC"},
                        "author": {"name": "L"},
                    }
                },
            },
            "https://api.polyhaven.com/assets": {
                "rock_wall": {"name": "Rock Wall", "tags": ["rock"], "authors": {"Rob": "All"}},
                "wood": {"name": "Wood", "tags": ["wood"]},
            },
            "https://api.polyhaven.com/files/rock_wall": {
                "Diffuse": {"1k": {"jpg": {"url": "https://dl.polyhaven.org/rock.jpg"}}}
            },
            K + "/model-index.json": [
                {
                    "name": "Duck",
                    "label": "Duck",
                    "tags": ["animal"],
                    "variants": {"glTF-Binary": "Duck.glb"},
                },
                {
                    "name": "Fox",
                    "label": "Fox",
                    "tags": ["animal"],
                    "variants": {"glTF-Binary": "Fox.glb"},
                },
            ],
            K + "/Duck/metadata.json": {"legal": [{"license": "CC BY 4.0"}]},
            K + "/Fox/metadata.json": {
                "legal": [{"license": "CC0 1.0"}, {"license": "CC BY-NC 4.0"}]
            },
            "https://api.fontsource.org/v1/fonts": [
                {
                    "id": "inter",
                    "family": "Inter",
                    "subsets": ["latin"],
                    "license": "OFL-1.1",
                    "category": "sans-serif",
                },
                {
                    "id": "lobster",
                    "family": "Lobster",
                    "subsets": ["latin"],
                    "license": "OFL-1.1",
                    "category": "display",
                },
            ],
        },
        files={
            "https://api.iconify.design/lucide/map.svg": SVG,
            "https://api.iconify.design/lucide/map-pin.svg": SVG,
            K + "/Duck/glTF-Binary/Duck.glb": b"glTF" + b"0" * 20,
            "https://cdn.jsdelivr.net/fontsource/fonts/inter@latest/latin-400-normal.woff2": b"wOF2"
            + b"0" * 20,
        },
    )
    f = finder(net)
    icon = await f.find_files(
        {"kind": "icon", "what": "地图", "query": "map", "count": 2}, tmp_path, "i", 10
    )
    assert icon["got"] == 2 and icon["items"][0]["license"] == "ISC"
    assert "&prefixes=lucide,tabler,ph" in next(u for u in net.got if "search" in u)
    tex = await f.find_files(
        {"kind": "texture", "what": "石墙", "query": "rock wall", "count": 1}, tmp_path, "t", 10
    )
    assert tex["items"][0]["license"] == "CC0" and tex["items"][0]["author"] == "Rob"
    model = await f.find_files(
        {"kind": "model", "what": "动物", "query": "animal", "count": 2}, tmp_path, "m", 10
    )
    assert [it["title"] for it in model["items"]] == ["Duck"]  # 狐狸有一条 NC 许可证，不用
    assert model["status"] == "short"
    font = await f.find_files(
        {"kind": "font", "what": "标题字", "query": "inter sans-serif", "count": 1},
        tmp_path,
        "f",
        10,
    )
    assert font["items"][0]["title"] == "Inter"
    cjk = await f.find_files(
        {"kind": "font", "what": "中文", "query": "chinese serif", "count": 1}, tmp_path, "f", 10
    )
    assert cjk["status"] == "none" and "中文字体" in cjk["note"]


async def test_data_and_docs():
    net = Net({}, bad=("broken",))
    f = finder(net)

    async def up(url):
        return 200

    async def down(url):
        return 503

    ok = await f.find_data({"kind": "data", "what": "汇率", "service": "fx"}, up)
    assert ok["ok"] and "欧洲央行" in ok["note"]
    no = await f.find_data({"kind": "data", "what": "汇率", "service": "fx"}, down)
    assert not no["ok"] and "参考数据" in no["note"]
    none = await f.find_data(
        {"kind": "data", "what": "酒店价格", "service": "", "why": "要 key"}, up
    )
    assert none["status"] == "none" and "要 key" in none["note"]
    docs = await f.find_docs(
        {
            "kind": "doc",
            "what": "拖拽",
            "urls": ["https://x.dev/ok", "https://x.dev/broken", "http://plain"],
        }
    )
    assert [it["url"] for it in docs["items"]] == ["https://x.dev/ok"] and "2 个打不开" in docs[
        "note"
    ]


def test_unknown_kind_in_config_is_rejected():
    with pytest.raises(ValueError, match="不认得的素材种类"):
        BenchFinder(sources={"video": ["x"]})


# --- 接进生成器：拆需求列清单 → 找 → 交给写窗口的助手；换一个、不要、重新找 ----------------


def plan_with_wants():
    import copy

    from tests.core.benchkit.test_builder import PLAN

    plan = copy.deepcopy(PLAN)
    plan["windows"][0]["wants"] = [
        {"kind": "photo", "what": "京都街景", "query": "Kyoto street", "count": 2},
        {"kind": "data", "what": "汇率", "service": "fx"},
        {"kind": "video", "what": "不认得的种类会被丢掉"},
    ]
    return plan


def builder_with_finder(tmp_path, net, **rules):
    from roundtable.core.benchkit.builder import BenchBuilder
    from roundtable.core.config.schema import BenchRules
    from tests.core.benchkit.test_builder import FakeSC, fake_check, render

    sc = FakeSC(plan=plan_with_wants())
    bench = BenchBuilder(
        BenchRules(**rules),
        root=tmp_path,
        supercomputer=sc,
        render=render,
        check=fake_check(),
        seed=7,
        finder=finder(net, sources={"photo": ["commons"]}),
    )
    return sc, bench


def commons_net(n=4):
    pages = {str(i): commons_page(i) for i in range(1, n + 1)}
    return Net({"https://commons.wikimedia.org": {"query": {"pages": pages}}})


async def test_builder_finds_and_hands_files_to_writers(tmp_path):
    sc, bench = builder_with_finder(tmp_path, commons_net())
    b = bench.submit("帮我做个东京三天旅行的工作台")
    await bench.wait()
    assert b["status"] == "done"
    photo, data = b["found"]
    assert photo["window"] == "map-view" and photo["got"] == 2 and photo["id"] == "f1"
    assert [it["id"] for it in photo["items"]] == ["f1-1", "f1-2"]
    assert data["kind"] == "data" and data["ok"]
    folder = tmp_path / b["id"]
    assert (folder / photo["items"][0]["file"]).is_file()
    assert "摄影师 1" in (folder / "assets/found/CREDITS.md").read_text(encoding="utf-8")
    writer = next(
        p for c, p in zip(sc.calls, sc.prompts, strict=True) if c["role"] == "bench_window"
    )
    assert "资源检索员找来的素材" in writer and photo["items"][0]["file"] in writer
    thread = next(t for t in b["threads"] if t["role"] == "找资源")
    assert (thread["phase"], thread["project"], thread["status"]) == ("立项", "找素材", "done")
    assert "下了 2 个文件" in thread["summary"]


async def test_swap_drop_and_refind(tmp_path):
    net = commons_net(5)
    sc, bench = builder_with_finder(tmp_path, net)
    b = bench.submit("帮我做个东京三天旅行的工作台")
    await bench.wait()
    folder = tmp_path / b["id"]
    first = b["found"][0]["items"][0]
    path = first["file"]
    bench.swap_found(b["id"], "f1-1")
    await bench.wait()
    now = b["found"][0]["items"][0]
    assert now["id"] == "f1-1" and now["file"] == path and now["title"] == "Kyoto street 3.jpg"
    assert "map-view" in b["review_stale"]  # 同一个位置换了图：代码不用改，只要重新评审
    assert "Kyoto street 3" in (folder / "assets/found/CREDITS.md").read_text(encoding="utf-8")
    bench.drop_found(b["id"], "f1-2")
    assert len(b["found"][0]["items"]) == 1 and b["found"][0]["status"] == "short"
    assert "map-view" in b["stale"]
    with pytest.raises(LookupError):
        bench.drop_found(b["id"], "f9-9")
    b["found"][0]["spare"] = []
    with pytest.raises(ValueError, match="没有别的同类备选"):
        bench.swap_found(b["id"], "f1-1")
    bench.refind(b["id"])
    await bench.wait()
    assert b["found"][0]["got"] == 2 and b["status"] == "done"


async def test_finder_off_or_nothing_wanted(tmp_path):
    from roundtable.core.config.schema import BenchFinder

    net = commons_net()
    sc, bench = builder_with_finder(tmp_path, net, finder=BenchFinder(enabled=False))
    b = bench.submit("帮我做个东京三天旅行的工作台")
    await bench.wait()
    assert b["found"] == [] and not net.got
    with pytest.raises(ValueError, match="没开"):
        bench.refind(b["id"])
