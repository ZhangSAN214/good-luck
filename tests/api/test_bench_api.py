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
