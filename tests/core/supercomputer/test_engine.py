from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from roundtable.core.config.schema import SupercomputerRules, SuperWorker
from roundtable.core.supercomputer import Launch, Supercomputer
from roundtable.core.supercomputer.runner import RunResult

T0 = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t


class Launcher:
    def __init__(self, blocked=None) -> None:
        self.blocked_ids = blocked or {}
        self.events: list[str] = []

    def blocked(self, w):
        return self.blocked_ids.get(w.id)

    def launch(self, w):
        return Launch([f"/fake/{w.id}"], {"PATH": ""})

    async def before(self, w):
        self.events.append(f"before:{w.id}")

    async def after(self, w):
        self.events.append(f"after:{w.id}")


def render(role, **values):
    return json.dumps({"role": role, **values}, ensure_ascii=False)


SPLIT = {
    "subtasks": [
        {"title": "写接口", "kind": "code", "brief": "写 api.py"},
        {"title": "写文档", "kind": "docs", "brief": "写 README"},
        {"title": "写测试", "kind": "test", "brief": "写 test_api.py"},
    ]
}


class Agents:
    """假的命令行助手：按提示词的角色回话；可以指定某位撞限额或失败。"""

    def __init__(self, split=SPLIT, limited=(), broken=(), delay=0.0) -> None:
        self.split, self.limited, self.broken, self.delay = split, set(limited), set(broken), delay
        self.calls: list[tuple[str, str]] = []
        self.peak = 0
        self.live = 0

    async def __call__(self, argv, *, stdin_text, cwd, env, timeout_s, secrets):
        wid = argv[0].rsplit("/", 1)[-1]
        p = json.loads(stdin_text)
        self.calls.append((wid, p["role"]))
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            await asyncio.sleep(self.delay)
            assert cwd.name == "work"
            if wid in self.limited:
                return RunResult(1, "", "You've hit your usage limit.")
            if wid in self.broken:
                return RunResult(2, "", "boom")
            if p["role"] == "super_split":
                text = self.split if isinstance(self.split, str) else json.dumps(self.split)
                return RunResult(0, text)
            if p["role"] == "super_work":
                return RunResult(0, f"{wid} 做完了 {p['title']}")
            return RunResult(0, f"合并好了（{wid}）：\n{p['results']}")
        finally:
            self.live -= 1


def rules(**kw):
    workers = [
        SuperWorker(id="d", label="D", via="dsh", skills={"docs": 0.9, "split": 0.5, "merge": 0.5}),
        SuperWorker(id="c", label="C", argv=["c"], skills={"code": 0.9, "test": 0.8, "split": 0.7}),
        SuperWorker(
            id="k", label="K", argv=["k"], skills={"code": 0.8, "merge": 0.9, "split": 0.9}
        ),
    ]
    return SupercomputerRules(workers=workers, **kw)


def build(tmp_path, agents, launcher=None, **kw):
    lau = launcher or Launcher()
    sc = Supercomputer(
        rules(**kw),
        root=tmp_path / "super",
        render=render,
        launchers={"command": lau, "dsh": lau},
        now=Clock(),
        runner=agents,
        seed=7,
    )
    return sc, lau


async def test_split_work_merge(tmp_path):
    agents = Agents(delay=0.05)
    sc, lau = build(tmp_path, agents)
    task = sc.submit("给项目加一个接口，附文档和测试")
    assert sc.busy
    with pytest.raises(RuntimeError, match="上一件活"):
        sc.submit("再来一件")
    await sc.wait()
    assert task["status"] == "done"
    # 拆分挑最擅长拆的 k；合并也是 k
    assert task["split"]["worker"] == "k" and task["merge"]["worker"] == "k"
    by_title = {s["title"]: s for s in task["subtasks"]}
    assert by_title["写文档"]["worker"] == "d"  # 文档交给最擅长文档的
    assert by_title["写接口"]["worker"] == "c"
    # 写测试：c 最擅长，但手上已有一件，分数仍最高 → 排队给 c（max_parallel=1）
    assert by_title["写测试"]["worker"] == "c"
    assert all(s["status"] == "done" for s in task["subtasks"])
    assert by_title["写接口"]["ranking"][0]["worker"] == "c"
    assert "合并好了" in task["result"] and "写文档" in task["result"]
    # 记录和结果都在本机文件夹里
    folder = tmp_path / "super" / "tasks" / task["id"]
    saved = json.loads((folder / "task.json").read_text(encoding="utf-8"))
    assert saved["status"] == "done" and (folder / "result.md").is_file()
    assert sc.recent()[0]["id"] == task["id"] and sc.get(task["id"])["status"] == "done"
    # 今天的次数：拆分 1 + 小活 3 + 合并 1
    assert sum(sc.ledger.used(w, T0) for w in ("d", "c", "k")) == 5
    assert agents.peak >= 2  # 不同的人同时干
    assert lau.events.count("before:d") == lau.events.count("after:d") == 1


async def test_limit_hit_cools_down_and_hands_over(tmp_path):
    agents = Agents(limited={"k"})
    sc, _ = build(tmp_path, agents)
    task = sc.submit("活")
    await sc.wait()
    assert task["status"] == "done"
    assert task["split"]["worker"] == "c"  # k 撞限额，拆分换给下一位
    assert task["split"]["attempts"][0] == {
        **task["split"]["attempts"][0],
        "worker": "k",
        "ok": False,
    }
    assert sc.ledger.cooling("k", T0) is not None
    st = {w["id"]: w for w in sc.status()["workers"]}
    assert "歇到" in st["k"]["blocked"] and st["k"]["cooling_until"]
    assert task["merge"]["worker"] != "k"
    sc.clear_cooldown("k")
    assert sc.ledger.cooling("k", T0) is None


async def test_broken_worker_retries_once_then_fails(tmp_path):
    agents = Agents(
        split={"subtasks": [{"title": "文档", "kind": "docs", "brief": "b"}]}, broken={"d", "c"}
    )
    sc, _ = build(tmp_path, agents)
    task = sc.submit("活")
    await sc.wait()
    sub = task["subtasks"][0]
    assert sub["status"] == "failed" and "退出码 2" in sub["summary"]
    assert [a["worker"] for a in sub["attempts"]] == ["d", "c"]
    assert task["status"] == "done" and "失败" in task["result"]


async def test_unreadable_split_falls_back_to_one_task(tmp_path):
    sc, _ = build(tmp_path, Agents(split="我觉得可以这样拆……"))
    task = sc.submit("整件活的原文")
    await sc.wait()
    assert len(task["subtasks"]) == 1 and task["subtasks"][0]["brief"] == "整件活的原文"
    assert any("看不懂" in line["text"] for line in task["log"])


async def test_unknown_kind_and_too_many_subtasks(tmp_path):
    many = {"subtasks": [{"title": f"t{i}", "kind": "magic", "brief": "b"} for i in range(9)]}
    sc, _ = build(tmp_path, Agents(split=many), max_subtasks=3)
    task = sc.submit("活")
    await sc.wait()
    assert len(task["subtasks"]) == 3 and {s["kind"] for s in task["subtasks"]} == {"code"}


async def test_cancel(tmp_path):
    sc, _ = build(tmp_path, Agents(delay=5))
    task = sc.submit("慢活")
    await asyncio.sleep(0.05)
    await sc.cancel()
    assert task["status"] == "cancelled" and not sc.busy
    assert sc.running == {"d": 0, "c": 0, "k": 0}


async def test_nobody_available(tmp_path):
    lau = Launcher(blocked={"d": "没装", "c": "没装", "k": "没装"})
    sc, _ = build(tmp_path, Agents(), launcher=lau)
    with pytest.raises(ValueError, match="没有能干活的助手"):
        sc.submit("活")
    with pytest.raises(ValueError, match="空的"):
        sc.submit("  ")


async def test_daily_limit_adjustable(tmp_path):
    sc, _ = build(tmp_path, Agents())
    sc.set_limit("k", 0)
    st = {w["id"]: w for w in sc.status()["workers"]}
    assert st["k"]["limit"] == 0 and "用完" in st["k"]["blocked"]
    task = sc.submit("活")
    await sc.wait()
    assert task["split"]["worker"] == "c"
    with pytest.raises(ValueError):
        sc.set_limit("nobody", 3)


def test_memory_db_unavailable():
    sc = Supercomputer(rules(), root=None, render=render, launchers={})
    st = sc.status()
    assert "内存数据库" in st["unavailable"]
    assert all("启动方式" in w["blocked"] for w in st["workers"])
    with pytest.raises(ValueError, match="内存数据库"):
        sc.submit("活")


async def test_step_excludes_and_logs_to_caller(tmp_path):
    import random as _random

    agents = Agents()
    sc = Supercomputer(
        rules(),
        root=tmp_path,
        render=render,
        launchers={"command": Launcher(), "dsh": Launcher()},
        runner=agents,
        now=Clock(),
    )
    logs: list[str] = []
    record: dict = {}
    folder = tmp_path / "bench" / "work"
    first = sc.rules.workers[0].id
    chosen, result = await sc.step(
        "split",
        render("super_split"),
        folder=folder,
        rng=_random.Random(1),
        record=record,
        log=logs.append,
        exclude=(first,),
    )
    assert chosen is not None and chosen.id != first
    assert result.ok and folder.is_dir()
    assert record["worker"] == chosen.id
