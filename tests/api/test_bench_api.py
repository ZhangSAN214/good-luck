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
        r = c.post(f"/api/bench/{bid}/sign", json={"requirements": [{"text": "能换城市"}]})
        assert r.status_code == 200
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
