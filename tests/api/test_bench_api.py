"""工作台生成器接口：交一句话、看进度、取生成的页面（沙箱头）、零件库允许沙箱载入。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.benchkit.builder import BenchBuilder
from roundtable.core.config.schema import BenchRules
from roundtable.core.service import RoundtableService

from ..core.benchkit.test_builder import FakeSC, fake_check, render
from ..core.orchestrator.conftest import Env


def service_with(tmp_path, **rules) -> RoundtableService:
    service = RoundtableService(Env().rt)
    service.bench = BenchBuilder(
        BenchRules(**rules),
        root=tmp_path / "bench",
        supercomputer=FakeSC(),
        render=render,
        check=fake_check(),
        seed=1,
    )
    return service


def wait_done(c, deadline=5.0):
    end = time.monotonic() + deadline
    while c.get("/api/bench").json()["busy"] and time.monotonic() < end:
        time.sleep(0.02)


def test_round_trip_and_sandboxed_files(tmp_path):
    with TestClient(create_app(service_with(tmp_path))) as c:
        assert c.get("/api/bench").json()["unavailable"] is None
        assert c.post("/api/bench", json={"request": ""}).status_code == 422
        assert c.post("/api/bench", json={"request": "东京旅行台"}).status_code == 200
        wait_done(c)
        st = c.get("/api/bench").json()
        b = st["current"]
        assert b["status"] == "done" and st["recent"][0]["id"] == b["id"]
        assert c.get(f"/api/bench/{b['id']}").json()["title"] == "旅行台"
        assert c.get("/api/bench/nope").status_code == 404

        page = c.get(f"/bench/{b['id']}/")
        assert page.status_code == 200 and "text/html" in page.headers["content-type"]
        assert page.headers["content-security-policy"].startswith("sandbox allow-scripts")
        assert "allow-same-origin" not in page.headers["content-security-policy"]
        js = c.get(f"/bench/{b['id']}/windows/{b['windows'][0]['id']}.js")
        assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript")
        # 不许跳出自己的文件夹，也不给看生成记录
        assert c.get(f"/bench/{b['id']}/../../etc/passwd").status_code == 404
        assert c.get(f"/bench/{b['id']}/build.json").status_code == 404

        kit = c.get("/kit/kit.js")
        assert kit.status_code == 200 and kit.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-origin" not in c.get("/index.html").headers


def test_ask_inside_bench(tmp_path):
    with TestClient(create_app(service_with(tmp_path))) as c:
        c.post("/api/bench", json={"request": "东京旅行台"})
        wait_done(c)
        bid = c.get("/api/bench").json()["current"]["id"]
        r = c.post(f"/api/bench/{bid}/ask", json={"question": "第一天去哪"})
        assert r.status_code == 200 and r.json()["text"] == "回答：第一天去哪"
        assert c.post(f"/api/bench/{bid}/ask", json={"question": "   "}).status_code == 400
        assert c.post("/api/bench/nope/ask", json={"question": "在吗"}).status_code == 404
        assert c.post(f"/api/bench/{bid}/ask", json={}).status_code == 422


def test_sandbox_lets_through_only_configured_public_data(tmp_path):
    net = [
        {
            "name": "tiles",
            "use": "底图",
            "url": "https://t.example/{z}/{x}/{y}.png",
            "source": "示例",
            "kind": "tiles",
        },
        {
            "name": "wiki",
            "use": "简介",
            "url": "https://w.example/s/{q}",
            "source": "示例",
            "also": ["https://img.example/"],
        },
    ]
    with TestClient(create_app(service_with(tmp_path, net=net))) as c:
        c.post("/api/bench", json={"request": "东京旅行台"})
        wait_done(c)
        bid = c.get("/api/bench").json()["current"]["id"]
        csp = c.get(f"/bench/{bid}/").headers["content-security-policy"]
        origins = "https://t.example https://w.example https://img.example"
        assert f"connect-src 'self' {origins};" in csp
        assert f"img-src 'self' data: blob: {origins};" in csp
        assert c.get(f"/bench/{bid}/data/_net.json").json()[1]["name"] == "wiki"
        # 成品按用户的条件现场生成内容：拿到的是对象
        r = c.post(f"/api/bench/{bid}/ask", json={"question": "杭州 3 天", "as_json": True})
        assert r.status_code == 200 and r.json()["data"]["days"][0]["q"] == "杭州 3 天"
    with TestClient(create_app(service_with(tmp_path / "off"))) as c:
        c.post("/api/bench", json={"request": "东京旅行台"})
        wait_done(c)
        bid = c.get("/api/bench").json()["current"]["id"]
        assert "connect-src 'self';" in c.get(f"/bench/{bid}/").headers["content-security-policy"]


def test_net_check_lists_every_service_without_real_network(tmp_path):
    service = service_with(
        tmp_path,
        net=[{"name": "fx", "use": "汇率", "url": "https://fx.example/?b={from}", "source": "ECB"}],
    )
    seen = []

    async def fetch(url):
        seen.append(url)
        return 503 if "fx.example" in url else 200

    service.bench_status_fetch = fetch
    with TestClient(create_app(service)) as c:
        r = c.get("/api/bench/net-check").json()
    names = [x["name"] for x in r["items"]]
    assert names[0] == "fx" and "github" in names
    assert r["total"] == len(names) and r["ok"] == r["total"] - 1
    assert seen[0] == "https://fx.example/?b=EUR"


def test_project_room_gates_over_http(tmp_path):
    from ..core.benchkit.test_builder import ProjectSC

    service = RoundtableService(Env().rt)
    service.bench = BenchBuilder(
        BenchRules(project={"enabled": True}),
        root=tmp_path / "bench",
        supercomputer=ProjectSC(),
        render=render,
        check=fake_check(),
        seed=1,
    )
    with TestClient(create_app(service)) as c:
        c.post("/api/bench", json={"request": "旅行台"})
        wait_done(c)
        b = c.get("/api/bench").json()["current"]
        assert b["gate"]["kind"] == "interview"
        bid = b["id"]
        assert c.post(f"/api/bench/{bid}/sign", json={}).status_code == 400  # 不在这一关
        assert c.post("/api/bench/nope/answer", json={}).status_code == 404
        c.post(f"/api/bench/{bid}/answer", json={"answers": {"q1": "自己"}})
        wait_done(c)
        assert c.get("/api/bench").json()["current"]["gate"]["kind"] == "sign"
        d = c.get("/api/bench").json()["current"]["proposal"]["design"]
        assert d["picks"]["layout"] and d["dims"]
        extra = {"design": {"layout": "stream", "colour": "红"}}
        assert c.post(f"/api/bench/{bid}/sign", json=extra).status_code == 422
        r = c.post(
            f"/api/bench/{bid}/sign",
            json={
                "requirements": [{"text": "能换城市"}],
                "design": {"layout": "stream", "material": "glass", "refs": []},
            },
        )
        assert r.status_code == 200
        wait_done(c)
        got = c.get(f"/api/bench/{bid}").json()["proposal"]["design"]["picks"]
        assert got["material"] == "glass" and got["layout"] == "stream"
        wait_done(c)
        assert c.get("/api/bench").json()["current"]["gate"]["kind"] == "m1"
        bad = c.post(f"/api/bench/{bid}/review", json={"decision": "maybe"})
        assert bad.status_code == 422
        c.post(f"/api/bench/{bid}/review", json={"decision": "pass"})
        wait_done(c)
        assert c.get("/api/bench").json()["current"]["gate"]["kind"] == "accept"
        c.post(f"/api/bench/{bid}/review", json={"decision": "pass"})
        assert c.get("/api/bench").json()["current"]["status"] == "delivered"
        assert c.post(f"/api/bench/{bid}/evolve").status_code == 200
        wait_done(c)
        assert c.get(f"/api/bench/{bid}").json()["evolve"]["items"]


def test_plugin_request_over_http(tmp_path):
    from ..core.benchkit.test_builder import LearnScout, ResearchSC
    from ..core.benchkit.test_team import PluginResearcher

    service = RoundtableService(Env().rt)
    service.bench = BenchBuilder(
        BenchRules(),
        root=tmp_path / "bench",
        supercomputer=ResearchSC(),
        render=render,
        check=fake_check(),
        scout=LearnScout(),
        researcher=PluginResearcher(),
        seed=1,
    )
    with TestClient(create_app(service)) as c:
        c.post("/api/bench", json={"request": "修图台"})
        wait_done(c)
        bid = c.get("/api/bench").json()["current"]["id"]
        r = c.post(f"/api/bench/{bid}/plugin", json={"name": "x/dsh-canvas"})
        assert r.status_code == 200
        assert r.json()["current"]["plugin_requests"][0]["name"] == "x/dsh-canvas"
        assert c.post(f"/api/bench/{bid}/plugin", json={"name": "没有"}).status_code == 400
        assert c.post("/api/bench/nope/plugin", json={"name": "x"}).status_code == 404
        assert c.post(f"/api/bench/{bid}/plugin", json={"name": ""}).status_code == 422


def test_dsh_whitelist_maps_reviewed_repos_to_ids():
    service = RoundtableService(Env().rt)
    known = service._dsh_whitelist()
    if service.dsh is None or service.dsh.catalog is None:
        assert known == {}
    else:
        assert known["Aisland-SJL/dsh-worktable"] == "worktable"
    service.dsh = None
    assert service._dsh_whitelist() == {}


def test_decide_when_stuck_over_http(tmp_path):
    from ..core.benchkit.test_builder import CheckSC, plan_with_requirements

    service = RoundtableService(Env().rt)
    service.bench = BenchBuilder(
        BenchRules(max_rounds=1),
        root=tmp_path / "bench",
        supercomputer=CheckSC(unmet={"r1"}, plan=plan_with_requirements()),
        render=render,
        check=fake_check(),
        seed=1,
    )
    with TestClient(create_app(service)) as c:
        c.post("/api/bench", json={"request": "旅行台"})
        wait_done(c)
        b = c.get("/api/bench").json()["current"]
        assert b["status"] == "needs_you" and b["stuck"]["missing"]
        assert c.post(f"/api/bench/{b['id']}/decide", json={"action": "x"}).status_code == 422
        assert c.post(f"/api/bench/{b['id']}/decide", json={"action": "edit"}).status_code == 400
        assert c.post("/api/bench/nope/decide", json={"action": "more"}).status_code == 404
        c.post(f"/api/bench/{b['id']}/decide", json={"action": "more"})
        wait_done(c)
        assert len(c.get("/api/bench").json()["current"]["rounds"]) == 2
        r = c.post(f"/api/bench/{b['id']}/decide", json={"action": "accept"})
        assert r.json()["current"]["status"] == "done"


def test_materials_over_http(tmp_path):
    from ..core.benchkit.test_materials import LookSC

    service = RoundtableService(Env().rt)
    service.bench = BenchBuilder(
        BenchRules(project={"enabled": True, "interview": False}),
        root=tmp_path / "bench",
        supercomputer=LookSC(),
        render=render,
        check=fake_check(),
        seed=1,
    )
    with TestClient(create_app(service)) as c:
        assert c.post("/api/bench/materials?name=a.exe", content=b"x").status_code == 400
        img = c.post("/api/bench/materials?name=参考.png", content=b"png")
        assert img.status_code == 201 and img.json()["kind"] == "image"
        mid = img.json()["id"]
        c.post("/api/bench", json={"request": "修图台", "materials": [mid]})
        wait_done(c)
        b = c.get("/api/bench").json()["current"]
        assert b["gate"]["kind"] == "sign" and b["look"]["summary"]
        bid = b["id"]
        assert c.get(f"/bench/{bid}/materials/{mid}.png").content == b"png"
        logo = c.post("/api/bench/materials?name=标.png", content=b"logo").json()["id"]
        assert c.post(f"/api/bench/{bid}/materials", json={"ids": []}).status_code == 422
        assert c.post(f"/api/bench/{bid}/materials", json={"ids": [logo]}).status_code == 200
        assert c.post(f"/api/bench/{bid}/look").status_code == 200
        wait_done(c)
        design = {"assets": {logo: "logo"}, "accent": "#ff7a00", "note": "大按钮", "button": "pill"}
        assert c.post(f"/api/bench/{bid}/sign", json={"design": design}).status_code == 200
        wait_done(c)
        html = c.get(f"/bench/{bid}/index.html").text
        assert f"assets/{logo}.png" in html and "--k-accent: #ff7a00" in html


def test_desk_endpoints_read_without_network_and_refresh_on_request(tmp_path):
    from ..core.benchkit.test_desk import CAPS, RULES, Net, gh, make

    service = service_with(tmp_path)
    net = Net(rank={"topic%3Alive2d": [gh("y/good", 500, "MIT")]})
    service.desk = make(tmp_path, net)
    with TestClient(create_app(service)) as c:
        v = c.get("/api/bench/desk").json()
        assert v["enabled"] and v["due"] and v["updated_at"] is None and not net.urls
        assert [x["id"] for x in v["categories"]] == [x.id for x in RULES.categories]
        assert len(v["caps"]) == len(CAPS.capabilities)
        assert c.post("/api/bench/desk/refresh").status_code == 200
        end = time.monotonic() + 5
        while c.get("/api/bench/desk").json()["busy"] and time.monotonic() < end:
            time.sleep(0.02)
        v = c.get("/api/bench/desk").json()
        assert v["updated_at"] and v["suggest"]
        sid = v["suggest"][0]["id"]
        assert c.post("/api/bench/desk/suggest/nope").status_code == 404
        v = c.post(f"/api/bench/desk/suggest/{sid}?accept=false").json()
        assert v["suggest"] == []


def test_weekly_endpoints_build_list_and_read(tmp_path):
    from roundtable.core.benchkit.weekly import Weekly

    from ..core.benchkit.test_desk import Net, gh, make

    service = service_with(tmp_path)
    net = Net(rank={"topic%3A3d": [gh("a/one", 900)]})
    service.desk = make(tmp_path, net)
    service.weekly = Weekly(service.desk, now=service._local_now)
    with TestClient(create_app(service)) as c:
        v = c.get("/api/bench/desk/weekly").json()
        assert v["enabled"] and v["latest"] is None and v["weeks"] == [] and not net.urls
        assert c.post("/api/bench/desk/weekly/build").status_code == 200
        end = time.monotonic() + 5
        while not c.get("/api/bench/desk/weekly").json()["weeks"] and time.monotonic() < end:
            time.sleep(0.02)
        v = c.get("/api/bench/desk/weekly").json()
        week = v["weeks"][0]
        assert v["latest"]["week"] == week and v["latest"]["sections"]
        assert c.get(f"/api/bench/desk/weekly/{week}").json()["week"] == week
        assert c.get("/api/bench/desk/weekly/2001-W01").status_code == 404
        assert c.get("/api/bench/desk/weekly/..%2F..").status_code == 404


def test_profile_endpoints_and_from_product_flag(tmp_path):
    with TestClient(create_app(service_with(tmp_path))) as c:
        st = c.post("/api/bench/profile", json={"text": "给小白用", "src": "修图台"}).json()
        assert [x["text"] for x in st["profile"]] == ["给小白用"]
        assert c.post("/api/bench/profile", json={"text": ""}).status_code == 422
        pid = st["profile"][0]["id"]
        assert c.delete("/api/bench/profile/nope").status_code == 404
        assert c.delete(f"/api/bench/profile/{pid}").json()["profile"] == []
        r = c.post("/api/bench", json={"request": "照着做", "from_product": True})
        assert r.status_code == 400 and "成品" in r.json()["detail"]


def test_submit_with_structured_form(tmp_path):
    with TestClient(create_app(service_with(tmp_path))) as c:
        st = c.get("/api/bench").json()
        assert {f["group"] for f in st["form_fields"]} >= {"基础信息", "输出与兜底"}
        r = c.post("/api/bench", json={"request": "旅行台", "form": {"who": "爸妈"}})
        assert r.status_code == 200
        assert r.json()["current"]["form"] == {"who": "爸妈"}
        wait_done(c)


def test_tree_pause_resume_and_rerun_over_http(tmp_path):
    """生成器第二版 · 第 1 块：分工树随状态下发；暂停 / 继续；写窗口的线程单独重跑、换人。"""
    with TestClient(create_app(service_with(tmp_path))) as c:
        c.post("/api/bench", json={"request": "东京旅行台"})
        wait_done(c)
        b = c.get("/api/bench").json()["current"]
        tree = b["tree"]
        assert [p["name"] for p in tree["phases"]] == ["立项", "执行", "交付", "革新"]
        assert c.get(f"/api/bench/{b['id']}").json()["tree"]["phases"]
        st = c.post(f"/api/bench/{b['id']}/pause", json={"key": "交付/质检与评审"}).json()
        assert st["current"]["paused"] == ["交付/质检与评审"]
        assert c.post(f"/api/bench/{b['id']}/pause", json={"key": "乱写"}).status_code == 400
        st = c.post(f"/api/bench/{b['id']}/resume", json={}).json()
        assert st["current"]["paused"] == []
        write = next(t for t in b["threads"] if t["window"])
        bad = c.post(f"/api/bench/{b['id']}/rerun", json={"thread": write["id"], "reason": ""})
        assert bad.status_code == 400 and "哪里不对" in bad.json()["detail"]
        ok = c.post(
            f"/api/bench/{b['id']}/rerun",
            json={"thread": write["id"], "reason": "标题太小", "worker": "b"},
        )
        assert ok.status_code == 200
        wait_done(c)
        last = c.get(f"/api/bench/{b['id']}").json()["threads"][-1]
        assert last["window"] == write["window"] and last["worker"] == "b"
        missing = c.post(f"/api/bench/{b['id']}/rerun", json={"thread": "t999", "reason": "x"})
        assert missing.status_code == 404


def test_edits_versions_and_redo_stale_over_http(tmp_path):
    """生成器第二版 · 第 2 块：改窗口说明 → 只这一个过期；看版本、对比、退回；只重做受影响的。"""
    with TestClient(create_app(service_with(tmp_path))) as c:
        c.post("/api/bench", json={"request": "东京旅行台"})
        wait_done(c)
        b = c.get("/api/bench").json()["current"]
        wid = b["windows"][0]["id"]
        base = f"/api/bench/{b['id']}"
        view = c.get(f"{base}/edit", params={"key": f"code:{wid}"}).json()
        assert view["value"] and view["versions"]
        r = c.post(f"{base}/edit", json={"key": f"window:{wid}", "value": {"purpose": "改了用途"}})
        assert r.status_code == 200
        tree = c.get(base).json()["tree"]
        assert [s["window"] for s in tree["stale"]] == [wid]
        vs = c.get(f"{base}/edit", params={"key": f"window:{wid}"}).json()["versions"]
        diff = c.get(f"{base}/edit", params={"key": f"window:{wid}", "n": vs[-1]["n"]}).json()
        assert "改了用途" in diff["diff"]
        assert c.post(f"{base}/revert", json={"key": f"window:{wid}", "n": vs[0]["n"]}).is_success
        assert c.post(f"{base}/edit", json={"key": "../x", "value": 1}).status_code == 400
        assert c.get(f"{base}/edit", params={"key": "secrets"}).status_code == 400
        assert c.post(f"{base}/redo-stale").status_code == 200
        wait_done(c)
        assert c.get(base).json()["tree"]["stale"] == []
        assert c.post(f"{base}/redo-stale").status_code == 400
        r = c.post(f"{base}/instruct", json={"window": wid, "text": "字大一点"})
        assert r.status_code == 200
        wait_done(c)
        assert c.post(f"{base}/conflict", json={"choice": "later"}).status_code == 400


def test_found_actions_over_http(tmp_path):
    """生成器第二版 · 第 3 块：找到的素材换一个 / 不要 / 重新找（这里没有清单，只测报错）。"""
    with TestClient(create_app(service_with(tmp_path))) as c:
        c.post("/api/bench", json={"request": "东京旅行台"})
        wait_done(c)
        b = c.get("/api/bench").json()["current"]
        base = f"/api/bench/{b['id']}"
        assert b["found"] == []
        assert c.post(f"{base}/found/swap", json={"item": "f1-1"}).status_code == 404
        assert c.post(f"{base}/found/drop", json={"item": "f1-1"}).status_code == 404
        assert c.post(f"{base}/found/drop", json={}).status_code == 422
        assert c.post(f"{base}/refind").status_code == 400
