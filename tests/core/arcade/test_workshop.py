"""游戏工坊：主做写游戏 → 辅助依次试玩修错（不审自己写的）→ 机器检查 → 返工 → 放进游戏区。

超级电脑用真的（派活、换人、记次数），命令行助手换成假的：按提示词的角色往工作目录里写文件。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from roundtable.core.arcade.workshop import Workshop, WorkshopError
from roundtable.core.arcade.workshop_check import static_issues
from roundtable.core.config.schema import SupercomputerRules, SuperWorker
from roundtable.core.config.sections.arcade import WorkshopRules
from roundtable.core.supercomputer import Supercomputer
from roundtable.core.supercomputer.runner import RunResult

from ..supercomputer.test_engine import Clock, Launcher, render

GOOD = (
    '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width"></head>'
    "<body><canvas></canvas><script>let s = 0;</script></body></html>"
)


class Agents:
    def __init__(self, limited=(), broken=(), first_html=GOOD) -> None:
        self.calls: list[tuple[str, str]] = []
        self.limited = set(limited)
        self.broken = set(broken)
        self.first_html = first_html

    async def __call__(self, argv, *, stdin_text, cwd, env, timeout_s, secrets):
        wid = argv[0].rsplit("/", 1)[-1]
        p = json.loads(stdin_text)
        role = p["role"]
        self.calls.append((wid, role))
        await asyncio.sleep(0.01)
        if wid in self.limited:
            return RunResult(1, "", "You've hit your usage limit.")
        if wid in self.broken:
            return RunResult(2, "", "boom")
        if role == "arcade_workshop_build":
            (cwd / "game.html").write_text(self.first_html, encoding="utf-8")
            meta = {"title": "星尘收集者", "summary": "原创小游戏", "controls": "方向键"}
            (cwd / "game.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            return RunResult(0, f"{wid} 做好了")
        if role == "arcade_workshop_review":
            return RunResult(0, f"{wid} 试玩过了：{p['issues']}")
        (cwd / "game.html").write_text(GOOD, encoding="utf-8")  # 返工：修好
        return RunResult(0, f"{wid} 修好了")


def make(tmp_path, agents, *, blocked=None, check=None, **kw):
    workers = [
        SuperWorker(id="lead", label="主", argv=["lead"], skills={"frontend": 0.5}),
        SuperWorker(id="a1", label="甲", argv=["a1"], skills={"frontend": 0.6, "review": 0.6}),
        SuperWorker(id="a2", label="乙", argv=["a2"], skills={"frontend": 0.4, "review": 0.9}),
        # 不在名单里的（比如按 API 额度花钱的）：分数再高也不派
        SuperWorker(id="out", label="外", argv=["out"], skills={"frontend": 1, "review": 1}),
    ]
    sc = Supercomputer(
        SupercomputerRules(workers=workers),
        root=tmp_path / "super",
        render=render,
        launchers={"command": Launcher(blocked)},
        now=Clock(),
        runner=agents,
        seed=3,
    )
    rules = WorkshopRules(lead="lead", assist=["a1", "a2"], **kw)
    holder = SimpleNamespace(rules=rules)
    ws = Workshop(
        lambda: holder.rules,
        root=tmp_path / "ws",
        supercomputer=sc,
        render=render,
        check=check or (lambda folder: []),
        now=Clock(),
        seed=5,
    )
    return ws, holder


async def test_lead_builds_assists_review_and_game_is_listed(tmp_path):
    agents = Agents()
    ws, _ = make(tmp_path, agents)
    job = ws.submit("做一个收集星星的小游戏")
    assert ws.busy and job["status"] == "building"
    with pytest.raises(WorkshopError, match="还在做"):
        ws.submit("再来一个")
    await ws.wait()
    assert agents.calls == [
        ("lead", "arcade_workshop_build"),
        ("a1", "arcade_workshop_review"),
        ("a2", "arcade_workshop_review"),
    ]
    job = ws.job
    assert job["status"] == "done" and job["author"] == "lead"
    assert [s["role"] for s in job["steps"]] == ["build", "review", "review"]
    assert job["title"] == "星尘收集者" and job["issues"] == []
    games = ws.games()
    assert [g["id"] for g in games] == [job["id"]]
    assert ws.file(job["id"], "").name == "game.html"
    with pytest.raises(WorkshopError):
        ws.file(job["id"], "../job.json")  # 跳不出工作目录
    st = ws.status()
    assert [m["role"] for m in st["team"]] == ["主做", "辅助", "辅助"]
    assert "seed" not in st["job"]


async def test_lead_limited_falls_back_inside_team_and_never_self_reviews(tmp_path):
    agents = Agents(limited={"lead"})
    ws, _ = make(tmp_path, agents)
    ws.submit("贪吃蛇那种玩法")
    await ws.wait()
    who = [c for c in agents.calls if c[1] == "arcade_workshop_build"]
    assert who[0][0] == "lead" and who[-1][0] in {"a1", "a2"}
    assert all(c[0] != "out" for c in agents.calls)  # 名单外的从不派
    author = ws.job["author"]
    reviewers = [c[0] for c in agents.calls if c[1] == "arcade_workshop_review"]
    assert author not in reviewers and reviewers  # 不审自己写的
    assert ws.job["status"] == "done"


async def test_check_issues_go_back_to_author_once(tmp_path):
    checks = iter([["电脑宽度：页面报错 x is not defined"], []])
    agents = Agents()
    ws, _ = make(tmp_path, agents, check=lambda folder: next(checks))
    ws.submit("打砖块")
    await ws.wait()
    assert agents.calls[-1] == ("lead", "arcade_workshop_fix")
    assert ws.job["status"] == "done" and ws.job["issues"] == []


async def test_remaining_issues_are_kept_on_the_card(tmp_path):
    agents = Agents()
    ws, _ = make(tmp_path, agents, check=lambda folder: ["手机宽度：页面比屏幕宽"], fix_rounds=1)
    ws.submit("跳一跳")
    await ws.wait()
    assert ws.job["status"] == "done"
    assert ws.games()[0]["issues"] == ["手机宽度：页面比屏幕宽"]


async def test_nobody_builds_then_failed_and_not_listed(tmp_path):
    agents = Agents(broken={"lead", "a1", "a2"})
    ws, _ = make(tmp_path, agents)
    ws.submit("迷宫")
    await ws.wait()
    assert ws.job["status"] == "failed"
    assert ws.games() == []


async def test_unavailable_and_limits(tmp_path):
    agents = Agents()
    ws, holder = make(tmp_path, agents, blocked={"lead": "没装", "a1": "没装", "a2": "没装"})
    with pytest.raises(WorkshopError, match="都用不了"):
        ws.submit("随便")
    ws2, holder2 = make(tmp_path / "b", agents, max_request_chars=50)
    with pytest.raises(WorkshopError, match="太长"):
        ws2.submit("长" * 51)
    with pytest.raises(WorkshopError, match="先写"):
        ws2.submit("   ")
    holder2.rules = WorkshopRules(lead="lead", assist=["a1"], enabled=False)
    with pytest.raises(WorkshopError, match="关掉"):
        ws2.submit("x")


async def test_cancel_and_delete(tmp_path):
    class Slow(Agents):
        async def __call__(self, argv, **kw):
            await asyncio.sleep(5)
            return RunResult(0, "")

    ws, _ = make(tmp_path, Slow())
    job = ws.submit("慢慢做")
    await asyncio.sleep(0.05)
    with pytest.raises(WorkshopError, match="先叫停"):
        ws.delete(job["id"])
    await ws.cancel()
    assert ws.job["status"] == "cancelled"
    ws.delete(job["id"])
    assert not (tmp_path / "ws" / job["id"]).exists()
    with pytest.raises(WorkshopError):
        ws.delete("../etc")


def test_static_issues(tmp_path):
    assert static_issues(tmp_path, 10_000) == ["没有做出 game.html"]
    (tmp_path / "game.html").write_text(GOOD, encoding="utf-8")
    assert static_issues(tmp_path, 10_000) == []
    bad = GOOD.replace(
        "<script>", '<script src="https://cdn.example.com/x.js"></script><script>alert(1);'
    )
    (tmp_path / "game.html").write_text(bad, encoding="utf-8")
    issues = static_issues(tmp_path, 10_000)
    assert any("外部" in i for i in issues) and any("弹窗" in i for i in issues)
    assert static_issues(tmp_path, 100)[0].startswith("game.html 太大")


def test_browser_check_finds_errors_and_overflow(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from roundtable.core.arcade.workshop_check import browser_issues

    ok = tmp_path / "ok"
    ok.mkdir()
    (ok / "game.html").write_text(GOOD.replace("<canvas>", '<canvas width="300" height="200">'))
    found = browser_issues(ok, settle_ms=200)
    if found is None:
        pytest.skip("浏览器起不来")
    assert found == []
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "game.html").write_text(
        '<meta charset="utf-8"><div style="width:2000px">x</div>'
        "<script>addEventListener('keydown', () => nope());</script>"
    )
    found = browser_issues(bad, settle_ms=200)
    assert any("nope is not defined" in i for i in found)
    assert any("横向滚动条" in i for i in found)
