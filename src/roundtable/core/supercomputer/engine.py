"""超级电脑的流程：拆分 → 按分数分给几位助手并行干 → 合并。

- 每一步都按分数挑人（scoring.pick），派活记录里留着当时每位的分数，界面上能看到为什么是他。
- 撞到订阅限额（输出里有 limit_patterns）：这位冷却 cooldown_minutes 分钟，活换给下一位；
  别的失败（超时、退出码不为 0）换一位再试一次，还不行就记为失败，合并时如实写出来。
- 大家在同一个工作文件夹 ``data/super/tasks/<编号>/work/`` 里干；记录在同目录 ``task.json``。
- 一次只干一件用户交的活；用户可以随时叫停（连同子进程一起结束）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from roundtable.core.config.schema import SupercomputerRules, SuperWorker
from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.supercomputer.ledger import Ledger
from roundtable.core.supercomputer.runner import RunResult, hit_limit, run
from roundtable.core.supercomputer.scoring import WorkerState, excluded, pick

RECENT = 10


@dataclass(frozen=True)
class Launch:
    argv: list[str]
    env: dict[str, str]
    secrets: list[str] = field(default_factory=list)


class Launcher(Protocol):
    """一种启动方式（配置里的 via）：服务层交进来，引擎不关心具体是哪家。"""

    def blocked(self, worker: SuperWorker) -> str | None: ...

    def launch(self, worker: SuperWorker) -> Launch: ...

    async def before(self, worker: SuperWorker) -> None: ...

    async def after(self, worker: SuperWorker) -> None: ...


Render = Callable[..., str]  # render(角色, **变量) → 发给助手的全文
Runner = Callable[..., Awaitable[RunResult]]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="seconds")


class Supercomputer:
    def __init__(
        self,
        rules: SupercomputerRules,
        *,
        root: Path | None,
        render: Render,
        launchers: Mapping[str, Launcher],
        now: Callable[[], datetime] = _utcnow,
        runner: Runner = run,
        seed: int | None = None,
    ) -> None:
        self.rules = rules
        self.root = root
        self.render = render
        self.launchers = launchers
        self.now = now
        self.runner = runner
        self.seed = seed
        self.ledger = Ledger(root / "ledger.json" if root else None)
        self.running: dict[str, int] = {w.id: 0 for w in rules.workers}
        self.slots = {w.id: asyncio.Semaphore(w.max_parallel) for w in rules.workers}
        self.task: dict[str, Any] | None = None
        self._job: asyncio.Task[None] | None = None
        self._recent: list[dict[str, Any]] = []

    # --- 状态 -------------------------------------------------------------------

    def worker(self, wid: str) -> SuperWorker | None:
        return next((w for w in self.rules.workers if w.id == wid), None)

    def state(self, w: SuperWorker) -> WorkerState:
        now = self.now()
        launcher = self.launchers.get(w.via)
        blocked = launcher.blocked(w) if launcher else f"没有「{w.via}」启动方式"
        cooling = self.ledger.cooling(w.id, now)
        if not blocked and cooling:
            blocked = f"撞到额度上限，歇到 {cooling.astimezone(UTC):%H:%M} UTC"
        return WorkerState(
            used=self.ledger.used(w.id, now),
            limit=self.ledger.limit(w.id, w.daily_limit),
            running=self.running[w.id],
            blocked=blocked,
        )

    def states(self) -> dict[str, WorkerState]:
        return {w.id: self.state(w) for w in self.rules.workers}

    @property
    def busy(self) -> bool:
        return self._job is not None and not self._job.done()

    def status(self) -> dict[str, Any]:
        states = self.states()
        workers = []
        for w in self.rules.workers:
            st = states[w.id]
            cooling = self.ledger.cooling(w.id, self.now())
            workers.append(
                {
                    "id": w.id,
                    "label": w.label,
                    "skills": dict(w.skills),
                    "used": st.used,
                    "limit": st.limit,
                    "default_limit": w.daily_limit,
                    "running": st.running,
                    "blocked": excluded(st),
                    "cooling_until": _iso(cooling) if cooling else None,
                }
            )
        return {
            "enabled": self.rules.enabled,
            "unavailable": self.unavailable_reason(),
            "kinds": list(self.rules.kinds),
            "weights": self.rules.weights.model_dump(),
            "workers": workers,
            "busy": self.busy,
            "task": self.task,
            "recent": self.recent(),
        }

    def unavailable_reason(self) -> str | None:
        if not self.rules.enabled:
            return "超级电脑在配置里关掉了（roundtable.yaml 的 supercomputer.enabled）"
        if self.root is None:
            return "当前用的是内存数据库，没有本机目录，超级电脑不可用"
        if not self.rules.workers:
            return "配置里没有助手（supercomputer.workers）"
        return None

    def recent(self) -> list[dict[str, Any]]:
        if self.root is None:
            return self._recent
        items = []
        base = self.root / "tasks"
        if base.is_dir():
            for d in sorted(base.iterdir(), reverse=True)[:RECENT]:
                try:
                    t = json.loads((d / "task.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                items.append({k: t.get(k) for k in ("id", "text", "status", "created_at")})
        return items

    def get(self, task_id: str) -> dict[str, Any] | None:
        if self.task and self.task["id"] == task_id:
            return self.task
        if self.root is None or not task_id.replace("-", "").isalnum():
            return None
        path = self.root / "tasks" / task_id / "task.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def set_limit(self, wid: str, value: int) -> None:
        if self.worker(wid) is None:
            raise ValueError(f"没有这位助手：{wid}")
        if not 0 <= value <= 1000:
            raise ValueError("每天次数要在 0～1000 之间")
        self.ledger.set_limit(wid, value)

    def clear_cooldown(self, wid: str) -> None:
        if self.worker(wid) is None:
            raise ValueError(f"没有这位助手：{wid}")
        self.ledger.clear_cooldown(wid)

    # --- 交活与叫停 -----------------------------------------------------------------

    def submit(self, text: str) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise ValueError("要交的活是空的")
        reason = self.unavailable_reason()
        if reason:
            raise ValueError(reason)
        if self.busy:
            raise RuntimeError("超级电脑正在干上一件活，等它干完或先叫停")
        if not any(not excluded(s) for s in self.states().values()):
            raise ValueError("现在没有能干活的助手（都没装、在冷却或今天次数用完了）")
        now = self.now()
        task_id = f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        seed = self.seed if self.seed is not None else random.SystemRandom().randrange(2**31)
        self.task = {
            "id": task_id,
            "text": text,
            "status": "splitting",
            "created_at": _iso(now),
            "finished_at": None,
            "seed": seed,
            "folder": str(self.folder(task_id)),
            "split": None,
            "subtasks": [],
            "merge": None,
            "result": None,
            "log": [],
        }
        self._save()
        self._job = asyncio.get_running_loop().create_task(self._run(self.task))
        return self.task

    async def cancel(self) -> dict[str, Any] | None:
        job = self._job
        if job is not None and not job.done():
            job.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await job
        return self.task

    async def aclose(self) -> None:
        await self.cancel()

    async def wait(self) -> None:
        """测试用：等当前这件活干完。"""
        if self._job is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._job

    async def step(
        self,
        kind: str,
        prompt: str,
        *,
        folder: Path,
        rng: random.Random,
        record: dict[str, Any],
        log: Callable[[str], None] | None = None,
        preset: str | None = None,
        exclude: tuple[str, ...] = (),
    ) -> tuple[SuperWorker | None, RunResult | None]:
        """给别的流程（如工作台生成器）用：按分数挑一位干一步，规则和自己的流程完全一样
        （撞限额冷却换人、别的失败换一位再试一次、记次数）。
        exclude 里的人不参加（比如不评自己的活）。"""
        job: dict[str, Any] = {"folder": str(folder), "log": [], "_log": log}
        folder.mkdir(parents=True, exist_ok=True)
        return await self._attempt(job, kind, prompt, rng, record, preset=preset, exclude=exclude)

    # --- 流程 -------------------------------------------------------------------

    def folder(self, task_id: str) -> Path:
        assert self.root is not None  # submit 已经挡掉了没有本机目录的情况
        return self.root / "tasks" / task_id / "work"

    def _log(self, task: dict[str, Any], text: str) -> None:
        if task.get("_log"):  # step() 交进来的活：记到调用方那里
            task["_log"](text)
            return
        task["log"].append({"at": _iso(self.now()), "text": text})
        self._save()

    def _save(self) -> None:
        task = self.task
        if task is None or self.root is None:
            return
        path = self.root / "tasks" / task["id"] / "task.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(task, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    async def _run(self, task: dict[str, Any]) -> None:
        rng = random.Random(task["seed"])
        try:
            subtasks = await self._split(task, rng)
            task["subtasks"] = subtasks
            task["status"] = "working"
            self._assign(task, rng)
            await asyncio.gather(*(self._work(task, st, rng) for st in subtasks))
            task["status"] = "merging"
            self._save()
            task["result"] = await self._merge(task, rng)
            task["status"] = "done"
            self._log(task, "完成")
        except asyncio.CancelledError:
            task["status"] = "cancelled"
            self._log(task, "你叫停了这件活")
            raise
        except Exception as exc:  # noqa: BLE001 - 记下来给界面看，不让后台任务悄悄死掉
            task["status"] = "failed"
            self._log(task, f"出错了：{type(exc).__name__}: {exc}")
        finally:
            task["finished_at"] = _iso(self.now())
            if task.get("result") and self.root is not None:
                (self.root / "tasks" / task["id"] / "result.md").write_text(
                    task["result"], encoding="utf-8"
                )
            self._save()

    async def _attempt(
        self,
        task: dict[str, Any],
        kind: str,
        prompt: str,
        rng: random.Random,
        record: dict[str, Any],
        *,
        preset: str | None = None,
        exclude: tuple[str, ...] = (),
    ) -> tuple[SuperWorker | None, RunResult | None]:
        """按分数挑人干一步；撞限额换人，别的失败换一位再试一次。返回（干成的人，结果）。"""
        tried: list[str] = list(exclude)
        failures = 0
        first = True
        record.setdefault("attempts", [])
        while True:
            chosen: SuperWorker | None
            if preset and first:
                chosen, ranking = self.worker(preset), record.get("ranking", [])
            else:
                chosen, scores = pick(
                    self.rules.workers, kind, self.states(), self.rules.weights, rng, tried
                )
                ranking = [s.as_dict() for s in scores]
            first = False
            if chosen is None:
                return None, None
            tried.append(chosen.id)
            record["worker"], record["ranking"] = chosen.id, ranking
            self._save()
            result = await self._run_one(chosen, prompt, task)
            attempt = {"worker": chosen.id, "ok": result.ok, "at": _iso(self.now())}
            if result.ok:
                record["attempts"].append(attempt)
                return chosen, result
            limited = hit_limit(result.output + "\n" + result.diagnostics, chosen.limit_patterns)
            if limited:
                until = self.now() + timedelta(minutes=chosen.cooldown_minutes)
                self.ledger.cool(chosen.id, until)
                attempt["why"] = "撞到额度上限，换人"
            else:
                failures += 1
                attempt["why"] = _why(result)
            record["attempts"].append(attempt)
            self._log(task, f"{chosen.label}：{attempt['why']}")
            if not limited and failures >= 2:
                return None, result

    async def _run_one(self, w: SuperWorker, prompt: str, task: dict[str, Any]) -> RunResult:
        launcher = self.launchers[w.via]
        self.running[w.id] += 1
        try:
            async with self.slots[w.id]:
                self.ledger.add(w.id, self.now())
                launch = launcher.launch(w)
                await launcher.before(w)
                try:
                    return await self.runner(
                        launch.argv,
                        stdin_text=prompt,
                        cwd=Path(task["folder"]),
                        env=launch.env,
                        timeout_s=w.timeout_s,
                        secrets=launch.secrets,
                    )
                finally:
                    await launcher.after(w)
        finally:
            self.running[w.id] -= 1

    async def _split(self, task: dict[str, Any], rng: random.Random) -> list[dict[str, Any]]:
        record: dict[str, Any] = {}
        task["split"] = record
        prompt = self.render(
            "super_split",
            task=task["text"],
            kinds="、".join(self.rules.kinds),
            max_subtasks=str(self.rules.max_subtasks),
        )
        chosen, result = await self._attempt(task, "split", prompt, rng, record)
        items: list[dict[str, Any]] = []
        if chosen is not None and result is not None:
            try:
                data = extract_json_object(result.output)
                raw = data.get("subtasks") or data.get("_list") or []
                items = [x for x in raw if isinstance(x, dict)]
            except JSONOutputError as exc:
                self._log(task, f"拆分结果看不懂（{exc}），整件活当一件干")
        if not items:
            items = [{"title": "整件活", "kind": self.rules.kinds[0], "brief": task["text"]}]
        subtasks = []
        for n, item in enumerate(items[: self.rules.max_subtasks], 1):
            kind = str(item.get("kind", ""))
            subtasks.append(
                {
                    "n": n,
                    "title": str(item.get("title") or f"小活 {n}")[:40],
                    "kind": kind if kind in self.rules.kinds else self.rules.kinds[0],
                    "brief": str(item.get("brief") or task["text"])[:2000],
                    "status": "waiting",
                    "worker": None,
                    "ranking": [],
                    "summary": None,
                }
            )
        who = chosen.label if chosen else "没人能拆，"
        self._log(task, f"{who}拆成了 {len(subtasks)} 件小活")
        return subtasks

    def _assign(self, task: dict[str, Any], rng: random.Random) -> None:
        """先把所有小活按分数分好（每分一件，那位手上的活和今天的次数都算上），再一起开干。"""
        states = self.states()
        for st in task["subtasks"]:
            chosen, scores = pick(self.rules.workers, st["kind"], states, self.rules.weights, rng)
            st["ranking"] = [s.as_dict() for s in scores]
            if chosen is None:
                st["status"], st["worker"] = "failed", None
                st["summary"] = "没有能干这件活的助手"
                continue
            st["worker"] = chosen.id
            states[chosen.id].running += 1
            states[chosen.id].used += 1
        self._save()

    async def _work(self, task: dict[str, Any], st: dict[str, Any], rng: random.Random) -> None:
        if st["status"] == "failed":
            return
        st["status"] = "running"
        self._save()
        others = "\n".join(f"- {o['title']}：{o['brief']}" for o in task["subtasks"] if o is not st)
        prompt = self.render(
            "super_work", task=task["text"], title=st["title"], brief=st["brief"], others=others
        )
        chosen, result = await self._attempt(task, st["kind"], prompt, rng, st, preset=st["worker"])
        if chosen is None or result is None or not result.ok:
            st["status"] = "failed"
            st["summary"] = _why(result) if result else "没有能干这件活的助手"
        else:
            st["status"] = "done"
            st["summary"] = result.output.strip()[-4000:] or "（没有说明）"
        self._log(task, f"「{st['title']}」{'完成' if st['status'] == 'done' else '失败'}")

    async def _merge(self, task: dict[str, Any], rng: random.Random) -> str:
        results = "\n\n".join(
            f"## {st['title']}（{'完成' if st['status'] == 'done' else '失败'}）\n{st['summary']}"
            for st in task["subtasks"]
        )
        record: dict[str, Any] = {}
        task["merge"] = record
        prompt = self.render("super_merge", task=task["text"], results=results)
        chosen, result = await self._attempt(task, "merge", prompt, rng, record)
        if chosen is None or result is None or not result.ok:
            self._log(task, "合并没成功，直接把各件小活的说明放在一起")
            return f"（合并没成功，下面是各件小活的说明）\n\n{results}"
        return result.output.strip()


def _why(result: RunResult | None) -> str:
    if result is None:
        return "没有能干的助手"
    if result.error:
        return result.error
    if result.timed_out:
        return "超时了"
    tail = (result.diagnostics or result.output).strip().splitlines()[-1:] or [""]
    return f"退出码 {result.code}" + (f"：{tail[0][:200]}" if tail[0] else "")
