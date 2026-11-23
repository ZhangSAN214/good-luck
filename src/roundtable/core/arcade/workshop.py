"""游戏工坊（docs/ARCADE.md）：你写要求 → 工作台的编程助手做成网页小游戏 → 放进游戏区。

走超级电脑同一条通道（``Supercomputer.step``：撞限额冷却换人、别的失败换一位再试一次、记次数），
只派给 ``arcade.workshop`` 里点名的几位：

1. 主做（``lead``）写 ``game.html`` 和 ``game.json``；主做用不了时在这几位里按分数换人。
2. 辅助（``assist``）按顺序试玩、找错、直接修；永远不审自己写的。
3. 机器检查（``workshop_check``：文件内容 + 本机浏览器里试一下），还有问题就交回作者返工
   ``fix_rounds`` 次。还剩的问题如实写在游戏卡片上。

每个游戏一个文件夹 ``<root>/<编号>/``：``job.json`` 是记录，``work/`` 是助手们的工作目录，
也是网页上 ``/arcade-games/<编号>/`` 打开的地方（沙箱，碰不到圆桌的接口和登录状态）。
一次只做一个；可以随时叫停。不调圆桌自己的模型，只用订阅的编程助手。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import secrets
import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from roundtable.core.arcade.workshop_check import (
    GAME_FILE,
    META_FILE,
    browser_issues,
    static_issues,
)
from roundtable.core.supercomputer.scoring import excluded
from roundtable.core.textutil import iso_seconds as _iso
from roundtable.core.textutil import utcnow as _utcnow

log = logging.getLogger(__name__)

ROLE_ZH = {"build": "主做", "review": "试玩修错", "fix": "返工"}
LIVE = ("building", "reviewing", "checking", "fixing")


class WorkshopError(Exception):
    """给用户看的原因（服务层转成 4xx）。"""


class Workshop:
    def __init__(
        self,
        rules_of: Callable[[], Any],
        *,
        root: Path | None,
        supercomputer: Any,
        render: Callable[..., str],
        check: Callable[[Path], list[str] | None] | None = None,
        now: Callable[[], datetime] = _utcnow,
        seed: int | None = None,
    ) -> None:
        self._rules_of = rules_of  # 每次现取配置（改了 roundtable.yaml 重新载入后马上生效）
        self.root = root
        self.sc = supercomputer
        self.render = render
        self.check = check if check is not None else browser_issues
        self.now = now
        self.seed = seed
        self.job: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def rules(self) -> Any:
        return self._rules_of()

    # --- 状态 -------------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def team(self) -> list[dict[str, Any]]:
        r = self.rules
        out = []
        for role, ids in (("主做", [r.lead]), ("辅助", list(r.assist))):
            for wid in ids:
                w = self.sc.worker(wid)
                if w is None:
                    blocked = "工作台里没有这位"
                    out.append({"id": wid, "label": wid, "role": role, "blocked": blocked})
                    continue
                out.append(
                    {
                        "id": wid,
                        "label": w.label,
                        "role": role,
                        "blocked": excluded(self.sc.state(w)),
                    }
                )
        return out

    def unavailable(self) -> str | None:
        r = self.rules
        if not r.enabled:
            return "游戏工坊在配置里关掉了（roundtable.yaml 的 arcade.workshop.enabled）"
        if self.root is None:
            return "当前用的是内存数据库，没有本机目录，游戏工坊不可用"
        reason = self.sc.unavailable_reason()
        if reason:
            return reason
        if all(m["blocked"] for m in self.team()):
            return "主做和辅助的助手现在都用不了（没装、在冷却或今天次数用完了）"
        return None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.rules.enabled,
            "unavailable": self.unavailable(),
            "team": self.team(),
            "busy": self.busy,
            "job": _public(self.job) if self.job else None,
            "games": self.games(),
            "max_games": self.rules.max_games,
            "max_request_chars": self.rules.max_request_chars,
        }

    def games(self) -> list[dict[str, Any]]:
        if self.root is None or not self.root.is_dir():
            return []
        items = []
        for d in sorted(self.root.iterdir(), reverse=True):
            job = _read(d / "job.json")
            if not job or job.get("status") != "done":
                continue
            items.append(
                {
                    k: job.get(k)
                    for k in ("id", "title", "summary", "controls", "issues", "created_at", "text")
                }
            )
        return items

    def get(self, gid: str) -> dict[str, Any] | None:
        if self.job and self.job["id"] == gid:
            return self.job
        if self.root is None or not _safe_id(gid):
            return None
        return _read(self.root / gid / "job.json")

    def file(self, gid: str, path: str) -> Path:
        """做好的游戏里的一个文件（只读，不许跳出它的工作目录）。"""
        job = self.get(gid)
        if job is None or self.root is None:
            raise WorkshopError("没有这个游戏")
        base = (self.root / gid / "work").resolve()
        full = (base / (path or GAME_FILE)).resolve()
        if not full.is_relative_to(base) or not full.is_file():
            raise WorkshopError("没有这个文件")
        return full

    # --- 开做、叫停、删除 ------------------------------------------------------------

    def submit(self, text: str) -> dict[str, Any]:
        text = text.strip()
        r = self.rules
        if not text:
            raise WorkshopError("先写一下想要什么游戏")
        if len(text) > r.max_request_chars:
            raise WorkshopError(f"要求太长了，最多 {r.max_request_chars} 字")
        if self.busy:
            raise WorkshopError("上一个游戏还在做，等它做完或先叫停")
        reason = self.unavailable()
        if reason:
            raise WorkshopError(reason)
        if len(self.games()) >= r.max_games:
            raise WorkshopError(f"已经存了 {r.max_games} 个游戏，先删掉几个不要的")
        assert self.root is not None
        now = self.now()
        gid = f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        seed = self.seed if self.seed is not None else random.SystemRandom().randrange(2**31)
        self.job = {
            "id": gid,
            "text": text,
            "status": "building",
            "created_at": _iso(now),
            "finished_at": None,
            "seed": seed,
            "steps": [],
            "issues": [],
            "title": None,
            "summary": None,
            "controls": None,
            "author": None,
            "log": [],
        }
        (self.root / gid / "work").mkdir(parents=True, exist_ok=True)
        self._save()
        self._task = asyncio.get_running_loop().create_task(self._run(self.job))
        return _public(self.job)

    async def cancel(self) -> dict[str, Any] | None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        return _public(self.job) if self.job else None

    async def aclose(self) -> None:
        await self.cancel()

    async def wait(self) -> None:
        """测试用：等当前这个游戏做完。"""
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def delete(self, gid: str) -> None:
        if self.busy and self.job and self.job["id"] == gid:
            raise WorkshopError("这个游戏还在做，先叫停")
        if self.root is None or not _safe_id(gid) or not (self.root / gid).is_dir():
            raise WorkshopError("没有这个游戏")
        shutil.rmtree(self.root / gid)
        if self.job and self.job["id"] == gid:
            self.job = None

    # --- 流程 -------------------------------------------------------------------

    def _folder(self, job: dict[str, Any]) -> Path:
        assert self.root is not None
        return self.root / job["id"] / "work"

    def _log(self, job: dict[str, Any], text: str) -> None:
        job["log"].append({"at": _iso(self.now()), "text": text[:300]})
        job["log"] = job["log"][-60:]
        self._save()

    def _save(self) -> None:
        job = self.job
        if job is None or self.root is None:
            return
        path = self.root / job["id"] / "job.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def _outsiders(self) -> tuple[str, ...]:
        """不在这次名单里的助手一律不派（包括按 API 额度花钱的）。"""
        r = self.rules
        team = {r.lead, *r.assist}
        return tuple(w.id for w in self.sc.rules.workers if w.id not in team)

    def _usable(self, wid: str) -> bool:
        w = self.sc.worker(wid)
        return w is not None and not excluded(self.sc.state(w))

    async def _step(
        self,
        job: dict[str, Any],
        role: str,
        kind: str,
        prompt: str,
        rng: random.Random,
        *,
        preset: str | None,
        exclude: tuple[str, ...] = (),
    ) -> tuple[Any, Any]:
        rec: dict[str, Any] = {}
        step = {"role": role, "worker": None, "label": None, "ok": None, "summary": None}
        step["started_at"] = _iso(self.now())
        planned = self.sc.worker(preset) if preset and self._usable(preset) else None
        if planned is not None:  # 先写上点名的那位（撞限额换了人，做完再改成实际干活的）
            step["worker"], step["label"] = planned.id, planned.label
        job["steps"].append(step)
        self._save()
        chosen, result = await self.sc.step(
            kind,
            prompt,
            folder=self._folder(job),
            rng=rng,
            record=rec,
            log=lambda t: self._log(job, t),
            preset=preset if preset and self._usable(preset) else None,
            exclude=tuple(dict.fromkeys((*self._outsiders(), *exclude))),
        )
        ok = bool(chosen is not None and result is not None and result.ok)
        step["worker"] = chosen.id if chosen is not None else None
        step["label"] = chosen.label if chosen is not None else None
        step["ok"] = ok
        out = (result.output if result is not None else "") or ""
        step["summary"] = out.strip()[-1200:] or ("没有能干这一步的助手" if chosen is None else "")
        step["finished_at"] = _iso(self.now())
        who = step["label"] or "没人"
        self._log(job, f"{who}·{ROLE_ZH[role]}：{'完成' if ok else '没成功'}")
        self._save()
        return (chosen if ok else None), result

    async def _check(self, job: dict[str, Any]) -> list[str]:
        folder = self._folder(job)
        issues = static_issues(folder, self.rules.max_bytes)
        if not issues and self.rules.browser_check:
            found = await asyncio.to_thread(self.check, folder)
            if found is None:
                self._log(job, "这台电脑没装 Playwright，跳过浏览器试玩，只查了文件")
            else:
                issues = found
        return issues

    async def _run(self, job: dict[str, Any]) -> None:
        rng = random.Random(job["seed"])
        r = self.rules
        try:
            prompt = self.render(
                "arcade_workshop_build", request=job["text"], max_kb=str(r.max_bytes // 1024)
            )
            author, built = await self._step(job, "build", "frontend", prompt, rng, preset=r.lead)
            folder = self._folder(job)
            if author is None or not (folder / GAME_FILE).is_file():
                raise WorkshopError("主做的助手没做出游戏（看下面的记录）")
            job["author"] = author.id
            notes = (built.output or "").strip()[-2000:]
            job["status"] = "reviewing"
            reviewed: list[str] = []
            for wid in r.assist:
                if wid == author.id or wid in reviewed:
                    continue  # 不审自己写的
                issues = static_issues(folder, r.max_bytes)
                prompt = self.render(
                    "arcade_workshop_review",
                    request=job["text"],
                    notes=notes,
                    issues="\n".join(f"- {i}" for i in issues) or "（没有）",
                )
                who, _ = await self._step(
                    job,
                    "review",
                    "review",
                    prompt,
                    rng,
                    preset=wid,
                    exclude=(author.id, *reviewed),
                )
                if who is not None:
                    reviewed.append(who.id)
            if not reviewed:
                self._log(job, "没有别的助手能试玩把关，只做了机器检查")
            job["status"] = "checking"
            self._save()
            issues = await self._check(job)
            for _ in range(r.fix_rounds):
                if not issues or not (folder / GAME_FILE).is_file():
                    break
                job["status"] = "fixing"
                prompt = self.render(
                    "arcade_workshop_fix",
                    request=job["text"],
                    issues="\n".join(f"- {i}" for i in issues),
                )
                await self._step(job, "fix", "frontend", prompt, rng, preset=author.id)
                job["status"] = "checking"
                self._save()
                issues = await self._check(job)
            if not (folder / GAME_FILE).is_file():
                raise WorkshopError("游戏文件不见了")
            job["issues"] = issues
            meta = _read(folder / META_FILE) or {}
            job["title"] = _clip(meta.get("title"), 20) or _clip(job["text"], 12)
            job["summary"] = _clip(meta.get("summary"), 120)
            job["controls"] = _clip(meta.get("controls"), 160)
            job["status"] = "done"
            self._log(job, "做好了" + (f"，还有 {len(issues)} 个小问题" if issues else ""))
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            self._log(job, "你叫停了")
            raise
        except WorkshopError as exc:
            job["status"] = "failed"
            self._log(job, str(exc))
        except Exception as exc:  # noqa: BLE001 - 记下来给界面看，不让后台任务悄悄死掉
            job["status"] = "failed"
            self._log(job, f"出错了：{type(exc).__name__}: {exc}")
            log.exception("游戏工坊出错")
        finally:
            job["finished_at"] = _iso(self.now())
            self._save()


def _public(job: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in job.items() if k != "seed"}


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _clip(value: Any, n: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = " ".join(value.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _safe_id(gid: str) -> bool:
    return bool(gid) and gid.replace("-", "").isalnum() and len(gid) <= 40
