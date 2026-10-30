"""工作台生成器（docs/KIT.md）：一句话 → 拆需求 → 按零件库分头写窗口 → 质检 + 截图评审 → 返工，
直到过线。

- 每一步都交给超级电脑按分数挑人（``Supercomputer.step``），花的是订阅次数，撞限额照样冷却换人。
- 外壳由程序按规格生成（spec.py），助手只写窗口内容；几个窗口同时写。
- 每轮先跑硬指标质检（quality.run_check，三种屏宽 × 浅 / 深色，截图存在 ``report/round-N/``），
  再把窗口按作者分组，每组交给**不是作者的**一位评审（不能自评）；
  评审按 ``rubric`` 逐项打分、写出要改的地方。
- 过线：质检通过，且评审各项平均分 ≥ ``pass_avg``、每项 ≥ ``pass_min``。没过线就把问题交回作者返工。
- 做满 ``max_rounds`` 轮还不过线：停下来，状态「等你决定」，成果和每轮的分数、截图都留着。
- 记录在 ``data/bench/<编号>/build.json``；一次只做一个工作台，可以随时叫停。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import re
import secrets
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from roundtable.core.benchkit.library import Library
from roundtable.core.benchkit.lint import lint_windows
from roundtable.core.benchkit.quality import QualityError, Report, run_check
from roundtable.core.benchkit.research import Researcher, report_markdown
from roundtable.core.benchkit.scout import Candidate, Scout
from roundtable.core.benchkit.spec import (
    Spec,
    SpecError,
    load_spec,
    manifest,
    normalize,
    parts_brief,
    write_shell,
)
from roundtable.core.config.schema import BenchRules
from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.supercomputer.scoring import excluded

RECENT = 10
# 写完自测：只开一种屏宽，只管这几项低级错误
SELFTEST_VIEWPORTS = (("电脑", 1280, 860),)
REVERT_MARGIN = 0.3  # 一组窗口的评审均分比上一轮低这么多以上，就退回上一版
SELFTEST_CHECKS = ("页面报错", "窗口没用零件库", "自己画插图")
Check = Callable[..., Report]


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="seconds")


def passes(report_ok: bool, scores: dict[str, float], rules: BenchRules) -> bool:
    """过线：质检通过，评审各项平均分和最低分都达标（没有评分算不过）。"""
    if not report_ok or not scores:
        return False
    vals = list(scores.values())
    return mean(vals) >= rules.pass_avg and min(vals) >= rules.pass_min


def merge_scores(reviews: list[dict[str, float]], rubric: list[str]) -> dict[str, float]:
    """几位评审各评一部分窗口：同一项取平均；缺的项按 0 分算（评审漏项不能白送分）。"""
    if not reviews:
        return {}
    out: dict[str, float] = {}
    for item in rubric:
        vals = [float(r.get(item, 0) or 0) for r in reviews]
        out[item] = round(mean(vals), 2)
    return out


def requirements_text(spec: Spec) -> str:
    """验收清单给评审看：编号、来源、要求、怎么检查、归哪几个窗口。"""
    lines = []
    for r in spec.requirements:
        line = f"- {r['id']}（{r['source']}）{r['text']}"
        if r["check"]:
            line += f"｜检查：{r['check']}"
        if r["windows"]:
            line += f"｜负责窗口：{'、'.join(r['windows'])}"
        lines.append(line)
    return "\n".join(lines) or "（没有验收清单）"


def merge_checks(
    requirements: list[dict[str, Any]], reviews: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """几位评审各判一遍：至少一位判过、且判过的都说做到了才算做到（漏判不能白送）。"""
    out = []
    for r in requirements:
        got = [rec["checks"][r["id"]] for rec in reviews if r["id"] in (rec.get("checks") or {})]
        out.append(
            {
                "id": r["id"],
                "source": r["source"],
                "text": r["text"],
                "windows": r["windows"],
                "met": bool(got) and all(g["met"] for g in got),
                "why": list(dict.fromkeys(g["why"] for g in got if not g["met"] and g["why"]))[:3],
            }
        )
    return out


def review_groups(authors: dict[str, str]) -> dict[str, list[str]]:
    """按作者分组：{作者: [他写的窗口]}，评审时整组交给不是他的一位。"""
    groups: dict[str, list[str]] = {}
    for wid, who in authors.items():
        groups.setdefault(who, []).append(wid)
    return groups


class BenchBuilder:
    def __init__(
        self,
        rules: BenchRules,
        *,
        root: Path | None,
        supercomputer: Any,
        render: Callable[..., str],
        check: Check = run_check,
        scout: Scout | None = None,
        researcher: Researcher | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        seed: int | None = None,
    ) -> None:
        self.rules = rules
        self.root = root
        self.sc = supercomputer
        self.render = render
        self.check = check
        self.scout = scout
        self.researcher = researcher
        self.library = Library(root) if root is not None else None
        self.now = now
        self.seed = seed
        self.build: dict[str, Any] | None = None
        self._job: asyncio.Task[None] | None = None

    # --- 状态 -------------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._job is not None and not self._job.done()

    def folder(self, bid: str) -> Path:
        assert self.root is not None
        return self.root / bid

    def unavailable_reason(self) -> str | None:
        if not self.rules.enabled:
            return "工作台生成器已关闭（roundtable.yaml 的 bench.enabled）"
        if self.root is None:
            return "没有本机数据目录"
        return None

    def recent(self) -> list[dict[str, Any]]:
        if self.root is None or not self.root.is_dir():
            return []
        out = []
        for p in sorted(self.root.glob("*/build.json"), reverse=True)[:RECENT]:
            with contextlib.suppress(OSError, ValueError):
                b = json.loads(p.read_text(encoding="utf-8"))
                keys = ("id", "request", "status", "title", "created_at", "score")
                out.append({k: b.get(k) for k in keys} | {"rounds": len(b.get("rounds") or [])})
        return out

    def get(self, bid: str) -> dict[str, Any] | None:
        if self.build and self.build["id"] == bid:
            return self.build
        if self.root is None or not bid.replace("-", "").isalnum():
            return None
        p = self.root / bid / "build.json"
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def status(self) -> dict[str, Any]:
        with contextlib.suppress(RuntimeError):  # 没有事件循环（同步测试）就不自动推进
            asyncio.get_running_loop()
            self._auto_gates()
        return {
            "enabled": self.rules.enabled,
            "unavailable": self.unavailable_reason(),
            "busy": self.busy,
            "max_rounds": self.rules.max_rounds,
            "project": self.rules.project.enabled,
            "current": self.build,
            "recent": self.recent(),
            "library": {
                "parts": self.library.parts() if self.library else [],
                "recipes": self.library.recipes()[:RECENT] if self.library else [],
            },
        }

    # --- 交活 -------------------------------------------------------------------

    def submit(self, request: str) -> dict[str, Any]:
        request = request.strip()
        if not request:
            raise ValueError("想要什么样的工作台？写一句话就行")
        reason = self.unavailable_reason()
        if reason:
            raise ValueError(reason)
        if self.busy:
            raise RuntimeError("正在生成上一个工作台，等它做完或先叫停")
        now = self.now()
        bid = f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        seed = self.seed if self.seed is not None else random.SystemRandom().randrange(2**31)
        self.build = {
            "id": bid,
            "request": request[:2000],
            "status": "planning",
            "created_at": _iso(now),
            "finished_at": None,
            "seed": seed,
            "folder": str(self.folder(bid)),
            "title": None,
            "plan": None,
            "windows": [],
            "sources": [],
            "research": None,
            "gate": None,
            "timeline": [],
            "changes": [],
            "rounds": [],
            "score": None,
            "result": None,
            "log": [],
        }
        self._save()
        self._start_job(self.build, lambda: self._run(self.build))
        return self.build

    async def cancel(self) -> dict[str, Any] | None:
        job = self._job
        if job is not None and not job.done():
            job.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await job
            b = self.build
            if b is not None and b["status"] not in (
                "done",
                "needs_you",
                "failed",
                "cancelled",
                "waiting",
                "delivered",
            ):
                b["status"] = "cancelled"  # 还没开始跑就叫停了
                b["finished_at"] = _iso(self.now())
                self._save()
        elif self.build is not None and self.build.get("gate"):
            b = self.build  # 停在关口等你时叫停：项目结束，不再往下做
            b["gate"] = None
            b["status"] = "cancelled"
            b["finished_at"] = _iso(self.now())
            self._log(b, "你结束了这个项目")
        return self.build

    async def aclose(self) -> None:
        await self.cancel()

    async def wait(self) -> None:
        """测试用：等当前这个工作台做完。"""
        if self._job is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._job

    # --- 流程 -------------------------------------------------------------------

    def _log(self, b: dict[str, Any], text: str) -> None:
        b["log"].append({"at": _iso(self.now()), "text": text})
        self._save()

    def _save(self) -> None:
        b = self.build
        if b is None or self.root is None:
            return
        path = self.folder(b["id"]) / "build.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    async def _step(self, b, kind, prompt, rng, record, **kw):
        return await self.sc.step(
            kind,
            prompt,
            folder=self.folder(b["id"]),
            rng=rng,
            record=record,
            log=lambda t: self._log(b, t),
            **kw,
        )

    def _brief(self, b: dict[str, Any]) -> str:
        """发给助手的「用户要什么」：原话，加上访谈里的回答和签字时的补充。"""
        return b.get("brief") or b["request"]

    def _mark(self, b: dict[str, Any], stage: str, title: str, detail: str = "") -> None:
        """项目室时间线：每个阶段交了什么、你做了什么决定。"""
        b.setdefault("timeline", []).append(
            {"at": _iso(self.now()), "stage": stage, "title": title, "detail": detail[:600]}
        )

    def _gate(self, b: dict[str, Any], kind: str, title: str) -> None:
        """停在关口等你：不占任何次数；配置了自动通过的里程碑到点自己往下走。"""
        hours = self.rules.project.gate_auto_hours if kind == "m1" else 0
        now = self.now()
        b["gate"] = {
            "kind": kind,
            "title": title,
            "since": _iso(now),
            "auto_at": _iso(now + timedelta(hours=hours)) if hours > 0 else None,
        }
        b["status"] = "waiting"
        self._log(b, f"停下来等你：{title}")

    def _start_job(self, b: dict[str, Any], work: Callable[[], Any]) -> None:
        loop = asyncio.get_running_loop()
        self.build = b
        self._job = loop.create_task(self._job_wrap(b, work))

    async def _job_wrap(self, b: dict[str, Any], work: Callable[[], Any]) -> None:
        try:
            await work()
        except asyncio.CancelledError:
            b["status"] = "cancelled"
            b["gate"] = None
            self._log(b, "你叫停了")
            raise
        except Exception as exc:  # noqa: BLE001 - 记下来给界面看，不让后台任务悄悄死掉
            b["status"] = "failed"
            b["gate"] = None
            self._log(b, f"出错了：{type(exc).__name__}: {exc}")
        finally:
            if b.get("gate") is None:
                b["finished_at"] = _iso(self.now())
            self._save()

    def _rng(self, b: dict[str, Any], stage: str) -> random.Random:
        n = len(b.get("timeline") or [])
        return random.Random(f"{b['seed']}-{stage}-{n}")

    async def _run(self, b: dict[str, Any]) -> None:
        if self.rules.project.enabled and self.rules.project.interview:
            await self._interview(b)
            if b["gate"]:
                return
        await self._prepare(b)

    async def _prepare(self, b: dict[str, Any]) -> None:
        """拆需求 → 调研 → 找插件 → 方案书；项目室开着就停下来等你签字。"""
        rng = random.Random(b["seed"])
        b["status"] = "planning"
        spec = await self._plan(b, rng)
        write_shell(
            self.folder(b["id"]),
            spec,
            b["id"],
            net=[
                x.model_dump(exclude={"also", "ttl_min", "probe"}) | {"ttl": x.ttl_min}
                for x in self.rules.net
            ],
        )
        if self.library is not None:
            self.library.seed(self.folder(b["id"]))
        if self.researcher is not None and self.rules.research.enabled:
            b["status"] = "researching"
            await self._research(b, spec, rng)
            c = (b.get("research") or {}).get("counts") or {}
            self._mark(
                b,
                "立项",
                "调研报告",
                f"GitHub {c.get('github', 0)} 个、论文 {c.get('papers', 0)} 篇、"
                f"资讯和视频 {c.get('news', 0)} 条、学习卡 {len(b['research']['cards'])} 张",
            )
        if spec.needs and self.scout is not None and self.rules.scout:
            b["status"] = "scouting"
            await self._scout(b, spec)
        if self.rules.project.enabled:
            self._proposal(b, spec)
            self._gate(b, "sign", "看方案书，改好了签字开工")
            return
        await self._build_all(b, spec, rng)

    async def _build_all(self, b: dict[str, Any], spec: Spec, rng: random.Random) -> None:
        """不走项目室时：一口气写完所有窗口，再按轮质检评审。"""
        b["status"] = "building"
        self._log(b, f"规格：{spec.title}，{len(spec.windows)} 个窗口")
        await asyncio.gather(*(self._write(b, spec, w, rng) for w in b["windows"]))
        await self._selftest(b, spec, rng)
        await self._rounds(b, spec, rng)

    async def _rounds(self, b: dict[str, Any], spec: Spec, rng: random.Random) -> None:
        start = len(b["rounds"])
        for k in range(1, self.rules.max_rounds + 1):
            n = start + k
            b["status"] = "checking"
            ok = await self._round(b, spec, n, rng)
            if ok:
                b["status"] = "done"
                self._log(b, f"第 {n} 轮过线")
                self._grow(b, spec)
                break
            self._revert(b, n)
            if k == self.rules.max_rounds:
                b["status"] = "needs_you"
                self._log(b, f"做满 {k} 轮还没过线，停下来等你决定")
                break
            b["status"] = "reworking"
            await self._rework(b, spec, n, rng)
        if self.rules.project.enabled:
            last = b["rounds"][-1] if b["rounds"] else {}
            reqs = last.get("requirements") or []
            met = sum(1 for r in reqs if r["met"])
            self._mark(
                b,
                "交付",
                "成品与验收单",
                f"评审平均 {b.get('score')} 分，验收 {met}/{len(reqs)}"
                + ("，过线" if b["status"] == "done" else "，没过线"),
            )
            self._gate(b, "accept", "试用成品，逐条验收")

    # --- 项目室：访谈、方案书、签字、里程碑、变更单、交付、革新 --------------------------

    async def _interview(self, b: dict[str, Any]) -> None:
        """开工前先问你几个关键问题（给谁用、在哪用、对标哪个产品、不要什么……）。"""
        b["status"] = "interviewing"
        record: dict[str, Any] = {}
        prompt = self.render(
            "bench_interview",
            request=b["request"],
            max_questions=str(self.rules.project.max_questions),
        )
        chosen, result = await self._step(
            b, self.rules.plan_kind, prompt, self._rng(b, "interview"), record
        )
        qs: list[dict[str, Any]] = []
        if chosen is not None and result is not None and result.ok:
            try:
                raw = extract_json_object(result.output).get("questions") or []
            except JSONOutputError:
                raw = []
            for i, q in enumerate(raw if isinstance(raw, list) else [], 1):
                if not isinstance(q, dict) or not str(q.get("q") or "").strip():
                    continue
                opts = q.get("options") if isinstance(q.get("options"), list) else []
                qs.append(
                    {
                        "id": f"q{i}",
                        "q": str(q["q"]).strip()[:200],
                        "why": str(q.get("why") or "")[:200],
                        "options": [str(o)[:60] for o in opts][:6],
                    }
                )
        qs = qs[: self.rules.project.max_questions]
        b["interview"] = {"questions": qs, "answers": {}, "worker": record.get("worker")}
        if not qs:
            self._log(b, "没问成问题（没有助手或看不懂），直接按你这句话往下做")
            return
        self._mark(b, "立项", "需求访谈", f"{len(qs)} 个问题")
        self._gate(b, "interview", "回答几个问题（可以跳过）")

    def answer(self, bid: str, answers: dict[str, str]) -> dict[str, Any]:
        b = self._at_gate(bid, "interview")
        qs = b["interview"]["questions"]
        got = {q["id"]: str(answers.get(q["id"]) or "").strip()[:1000] for q in qs}
        b["interview"]["answers"] = got
        lines = [f"- {q['q']}\n  {got[q['id']]}" for q in qs if got[q["id"]]]
        if lines:
            b["brief"] = f"{b['request']}\n\n访谈时补充的情况：\n" + "\n".join(lines)
        self._mark(
            b, "立项", "你回答了访谈", f"答了 {len(lines)}/{len(qs)} 题" if lines else "全部跳过"
        )
        b["gate"] = None
        self._start_job(b, lambda: self._prepare(b))
        return b

    def _proposal(self, b: dict[str, Any], spec: Spec) -> None:
        """方案书：程序按规格和调研整理（验收清单、主视觉、窗口、里程碑、用到的项目），给你看、改、签字。"""
        hero = [spec.hero] if spec.hero and spec.window(spec.hero) else []
        rest = [w.id for w in spec.windows if w.id not in hero]
        title = {w.id: w.title for w in spec.windows}
        milestones = []
        if hero:
            milestones.append({"n": 1, "title": "主视觉演示", "windows": hero})
        milestones.append({"n": len(milestones) + 1, "title": "全部窗口", "windows": rest})
        r = b.get("research") or {}
        tech = [
            f"{s['repo']}（{s['why']}）" for s in b.get("sources") or [] if s["verdict"] == "adopt"
        ] + [f"学习卡 {c['repo']}：{c['what'][:80]}" for c in r.get("cards") or []]
        b["proposal"] = {
            "title": spec.title,
            "subtitle": spec.subtitle,
            "requirements": spec.requirements,
            "hero": spec.hero,
            "windows": [{"id": w.id, "title": w.title, "purpose": w.purpose} for w in spec.windows],
            "milestones": [m | {"titles": [title[x] for x in m["windows"]]} for m in milestones],
            "tech": tech[:12],
            "plugins": [p["title"] for p in r.get("plugins") or []],
        }
        b["milestones"] = []
        folder = self.folder(b["id"])
        lines = [f"# 方案书：{spec.title}", "", f"你说的：{b['request']}", "", "## 验收清单"]
        lines += [f"- {x['id']} {x['text']}" for x in spec.requirements]
        lines += ["", "## 窗口"]
        lines += [
            f"- {w.title}{'（主视觉）' if w.id == spec.hero else ''}：{w.purpose}"
            for w in spec.windows
        ]
        lines += ["", "## 里程碑"]
        lines += [
            f"{m['n']}. {m['title']}：{'、'.join(m['titles'])}" for m in b["proposal"]["milestones"]
        ]
        if tech:
            lines += ["", "## 借鉴的项目"] + [f"- {t}" for t in tech]
        (folder / "方案书.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._mark(
            b,
            "立项",
            "方案书",
            f"验收清单 {len(spec.requirements)} 条、{len(spec.windows)} 个窗口、"
            f"{len(milestones)} 个里程碑",
        )

    def sign(
        self, bid: str, requirements: list[dict[str, str]] | None = None, note: str = ""
    ) -> dict[str, Any]:
        """签字开工：可以改、删、加验收清单里你的要求（底线 b… 不能删），写一句补充。"""
        b = self._at_gate(bid, "sign")
        folder = self.folder(bid)
        spec = load_spec(folder)
        if requirements is not None:
            old = {r["id"]: r for r in spec.requirements}
            keep = [r for r in spec.requirements if str(r["id"]).startswith("b")]
            mine: list[dict[str, Any]] = []
            for x in requirements:
                text = str(x.get("text") or "").strip()[:300]
                rid = str(x.get("id") or "")
                if not text or rid.startswith("b"):
                    continue
                base = old.get(rid) if rid.startswith("r") else None
                mine.append(
                    (base or {"check": "", "windows": [], "source": "你的要求"}) | {"text": text}
                )
            mine = mine[: self.rules.max_requirements]
            for i, r in enumerate(mine, 1):
                r["id"] = f"r{i}"
            spec.requirements = mine + keep
            (folder / "spec.json").write_text(
                json.dumps(spec.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            b["proposal"]["requirements"] = spec.requirements
        note = note.strip()[:1000]
        if note:
            b["brief"] = f"{self._brief(b)}\n\n签字时补充：{note}"
        self._mark(
            b,
            "立项",
            "你签字开工",
            (note or "没有补充") + f"；验收清单 {len(spec.requirements)} 条",
        )
        b["gate"] = None
        self._start_job(b, lambda: self._milestone(b, 1))
        return b

    async def _milestone(self, b: dict[str, Any], n: int) -> None:
        """按方案书的里程碑做：第 1 个通常是主视觉，演示给你看；
        最后一个写完其余窗口，进入质检评审。"""
        spec = load_spec(self.folder(b["id"]))
        ms = b["proposal"]["milestones"]
        m = ms[n - 1]
        rng = self._rng(b, f"m{n}")
        b["status"] = "building"
        recs = [w for w in b["windows"] if w["id"] in m["windows"]]
        self._log(b, f"里程碑 {n}「{m['title']}」：写 {len(recs)} 个窗口")
        await asyncio.gather(*(self._write(b, spec, w, rng) for w in recs))
        if n < len(ms):
            shots = await self._selftest(b, spec, rng, only=m["windows"], out=f"m{n}-1")
            b["milestones"].append({"n": n, "title": m["title"], "demos": [shots]})
            self._mark(b, "执行", f"里程碑 {n}：{m['title']}", "、".join(m["titles"]))
            self._gate(b, f"m{n}", f"试用里程碑 {n}「{m['title']}」，通过或提意见")
            return
        await self._selftest(b, spec, rng)
        await self._rounds(b, spec, rng)

    def review(
        self, bid: str, decision: str, comments: list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        """里程碑演示或交付成品：通过，或提意见（变更单，可指定窗口）。"""
        b = self.get(bid)
        if b is None:
            raise LookupError("没有这个工作台")
        kind = (b.get("gate") or {}).get("kind") or ""
        if not (kind.startswith("m") or kind == "accept"):
            raise ValueError("现在不在演示或验收的关口")
        if self.busy:
            raise RuntimeError("正在做另一个工作台，等它停下来再继续")
        if decision == "pass":
            b["gate"] = None
            if kind == "accept":
                b["status"] = "delivered"
                self._mark(b, "交付", "你验收通过", "")
                self._log(b, "你验收通过，项目交付")
                self.build = b
                b["finished_at"] = _iso(self.now())
                self._save()
                return b
            n = int(kind[1:])
            self._mark(b, "执行", f"里程碑 {n} 你点了通过", "")
            self._start_job(b, lambda: self._milestone(b, n + 1))
            return b
        wins = {w["id"] for w in b["windows"]}
        items = []
        for c in comments or []:
            text = str(c.get("text") or "").strip()[:1000]
            if not text:
                continue
            wid = str(c.get("window") or "")
            items.append(
                {
                    "id": f"c{len(b.setdefault('changes', [])) + len(items) + 1}",
                    "at": _iso(self.now()),
                    "stage": kind,
                    "text": text,
                    "window": wid if wid in wins else "",
                    "status": "open",
                    "reply": "",
                }
            )
        if not items:
            raise ValueError("写一下哪里要改")
        b["changes"].extend(items)
        self._mark(
            b,
            "执行" if kind != "accept" else "交付",
            "你提了变更单",
            "；".join(i["text"] for i in items),
        )
        b["gate"] = None
        self._start_job(b, lambda: self._apply_changes(b, kind, items))
        return b

    async def _apply_changes(
        self, b: dict[str, Any], kind: str, items: list[dict[str, Any]]
    ) -> None:
        """按变更单返工：指定了窗口的只交给那个窗口，没指定的交给这一阶段的所有窗口。"""
        spec = load_spec(self.folder(b["id"]))
        rng = self._rng(b, f"change-{kind}")
        if kind == "accept":
            scope = [w["id"] for w in b["windows"]]
        else:
            scope = b["proposal"]["milestones"][int(kind[1:]) - 1]["windows"]
        findings = [
            {
                "check": f"你的意见（变更单 {c['id']}）",
                "severity": "block",
                "where": "甲方",
                "detail": c["text"],
                "windows": [c["window"]] if c["window"] else scope,
            }
            for c in items
        ]
        b["status"] = "reworking"
        before = {w["id"]: len(w.get("rework") or []) for w in b["windows"]}
        await self._rework(
            b, spec, 0, rng, rnd={"check": {"findings": findings}}, only_flagged=True
        )
        for c in items:
            targets = [c["window"]] if c["window"] else scope
            done = [
                w
                for w in b["windows"]
                if w["id"] in targets and len(w.get("rework") or []) > before[w["id"]]
            ]
            ok = done and all(w["status"] == "done" for w in done)
            c["status"] = "done" if ok else "failed"
            c["reply"] = (done[0]["summary"] or "")[-300:] if ok else "这次没改成（没有助手能接）"
        self._mark(
            b,
            "执行" if kind != "accept" else "交付",
            "变更单处理完",
            "、".join(f"{c['id']}{'改好了' if c['status'] == 'done' else '没改成'}" for c in items),
        )
        if kind == "accept":
            await self._selftest(b, spec, rng)
            await self._rounds(b, spec, rng)
            return
        n = int(kind[1:])
        m = b["proposal"]["milestones"][n - 1]
        demos = next(x for x in b["milestones"] if x["n"] == n)["demos"]
        shots = await self._selftest(b, spec, rng, only=m["windows"], out=f"m{n}-{len(demos) + 1}")
        demos.append(shots)
        self._gate(b, kind, f"再试用里程碑 {n}「{m['title']}」，通过或接着提意见")

    async def evolve(self, bid: str) -> dict[str, Any]:
        """交付以后出下一版提案：汇总你的意见和变更单，对照调研里的新资料，提出下一版改什么。"""
        b = self.get(bid)
        if b is None:
            raise LookupError("没有这个工作台")
        if b.get("status") != "delivered":
            raise ValueError("验收通过以后才能出下一版提案")
        if self.busy:
            raise RuntimeError("正在做另一个工作台，等它停下来再继续")
        self.build = b
        b["status"] = "evolving"
        self._save()

        async def go() -> None:
            report: dict[str, Any] = {}
            spec = load_spec(self.folder(bid))
            if self.researcher is not None and self.rules.research.enabled:
                queries = (b.get("research") or {}).get("queries") or [
                    str(n.get("keywords") or "") for n in spec.needs
                ]
                report = (await self.researcher.run(queries, [])).to_dict()
            prompt = self.render(
                "bench_evolve",
                request=self._brief(b),
                spec=json.dumps(spec.to_dict(), ensure_ascii=False)[:6000],
                feedback="\n".join(
                    f"- {c['text']}（{'改好了' if c['status'] == 'done' else '没改成'}）"
                    for c in b.get("changes") or []
                )
                or "（没有提过意见）",
                research="\n".join(
                    f"- {g['title']} ★{g['stars']}：{g['summary']}"
                    for g in (report.get("github") or [])[:10]
                )
                + "\n"
                + "\n".join(f"- 论文：{p['title']}" for p in (report.get("papers") or [])[:5])
                or "（这次没调研）",
            )
            record: dict[str, Any] = {}
            chosen, result = await self._step(
                b, self.rules.plan_kind, prompt, self._rng(b, "evolve"), record
            )
            items: list[dict[str, str]] = []
            if chosen is not None and result is not None and result.ok:
                with contextlib.suppress(JSONOutputError):
                    raw = extract_json_object(result.output).get("items") or []
                    items = [
                        {
                            "title": str(x.get("title") or "")[:80],
                            "why": str(x.get("why") or "")[:300],
                            "basis": str(x.get("basis") or "")[:200],
                        }
                        for x in raw
                        if isinstance(x, dict) and x.get("title")
                    ][:8]
            b["evolve"] = {"items": items, "at": _iso(self.now()), "worker": record.get("worker")}
            b["status"] = "delivered"
            self._mark(b, "革新", "下一版提案", f"{len(items)} 条" if items else "没出成")

        self._start_job(b, go)
        return b

    def _at_gate(self, bid: str, kind: str) -> dict[str, Any]:
        b = self.get(bid)
        if b is None:
            raise LookupError("没有这个工作台")
        if (b.get("gate") or {}).get("kind") != kind:
            raise ValueError("现在不在这个关口")
        if self.busy:
            raise RuntimeError("正在做另一个工作台，等它停下来再继续")
        return b

    def _auto_gates(self) -> None:
        """配置了自动通过的里程碑：到点没回就算通过（立项签字和交付验收永远等你）。"""
        b = self.build
        gate = (b or {}).get("gate") or {}
        if not b or not gate.get("auto_at") or self.busy:
            return
        if datetime.fromisoformat(gate["auto_at"]) <= self.now():
            self._mark(b, "执行", f"{gate['title']}：到点没回，按通过处理", "")
            with contextlib.suppress(Exception):
                self.review(b["id"], "pass")

    async def _plan(self, b: dict[str, Any], rng: random.Random) -> Spec:
        m = manifest()
        record: dict[str, Any] = {}
        b["plan"] = record
        prompt = self.render(
            "bench_plan",
            request=self._brief(b),
            parts=parts_brief(m) + ("\n" + self.library.brief() if self.library else ""),
            themes="、".join(m["themes"]),
            max_windows=str(self.rules.max_windows),
            baseline="\n".join(f"b{i}. {t}" for i, t in enumerate(self.rules.baseline, 1))
            or "（没有底线）",
            max_requirements=str(self.rules.max_requirements),
            net=self._net_text(),
        )
        chosen, result = await self._step(b, self.rules.plan_kind, prompt, rng, record)
        if chosen is None or result is None or not result.ok:
            raise RuntimeError("没有人能拆需求（都没装、在冷却或今天次数用完了）")
        try:
            spec = normalize(
                extract_json_object(result.output),
                max_windows=self.rules.max_windows,
                baseline=self.rules.baseline,
                max_requirements=self.rules.max_requirements,
            )
        except (JSONOutputError, SpecError) as exc:
            raise RuntimeError(f"拆需求的结果看不懂：{exc}") from None
        b["title"] = spec.title
        b["windows"] = [
            {"id": w.id, "title": w.title, "author": None, "status": "waiting", "summary": None}
            for w in spec.windows
        ]
        return spec

    async def _scout(self, b: dict[str, Any], spec: Spec) -> None:
        """按 needs 找插件：达标的每项需求搬一个（总数不超过 max_adopt），其余只借思路；
        都记进清单。"""
        assert self.scout is not None
        vendor = self.folder(b["id"]) / "vendor"
        adopted = 0
        for need in spec.needs:
            try:
                found = await self.scout.search(need["need"], need["keywords"] or need["need"])
            except Exception as exc:  # noqa: BLE001 - 找不到就自己写
                self._log(b, f"找「{need['need']}」时出错：{exc}")
                continue
            took = False
            for c in sorted(found, key=lambda x: (x.verdict != "adopt", -x.stars)):
                item = c.to_dict()
                if c.verdict == "adopt" and not took and adopted < self.rules.max_adopt:
                    slug = re.sub(r"[^\w.-]+", "-", c.repo.replace("/", "__"))
                    try:
                        item["record"] = await self.scout.fetch(c, vendor / slug)
                        item["folder"] = f"vendor/{slug}"
                        took, adopted = True, adopted + 1
                    except Exception as exc:  # noqa: BLE001 - 下载或解压不了就改成只借思路
                        item["verdict"], item["why"] = (
                            "idea",
                            f"没搬成（{str(exc)[:80]}），只借思路",
                        )
                elif c.verdict == "adopt":
                    item["verdict"], item["why"] = "idea", "同一项需求已经搬了一个，这个只借思路"
                b["sources"].append(item)
            self._log(
                b,
                f"找「{need['need']}」：{len(found)} 个候选，{'搬了 1 个' if took else '只借思路'}",
            )
        (self.folder(b["id"]) / "sources.json").write_text(
            json.dumps(b["sources"], ensure_ascii=False, indent=1), encoding="utf-8"
        )

    async def _research(self, b: dict[str, Any], spec: Spec, rng: random.Random) -> None:
        """开工前调研：一位能联网的助手给英文搜索词和资讯、视频 → 程序搜 GitHub（≥ min_github）、
        论文、dsh 插件集市并核对链接 → 高分项目源码只读下载，交给助手写学习卡。"""
        assert self.researcher is not None
        rules = self.rules.research
        record: dict[str, Any] = {}
        prompt = self.render(
            "bench_research",
            request=self._brief(b),
            spec=json.dumps(
                {
                    "title": spec.title,
                    "windows": [w.title for w in spec.windows],
                    "needs": spec.needs,
                },
                ensure_ascii=False,
            ),
            news=str(rules.news),
        )
        queries = [str(n.get("keywords") or "") for n in spec.needs]
        news: Any = []
        chosen, result = await self._step(b, rules.kind, prompt, rng, record)
        if chosen is not None and result is not None and result.ok:
            try:
                got = extract_json_object(result.output)
                queries = [*(got.get("queries") or []), *queries]
                news = got.get("news") or []
            except JSONOutputError:
                self._log(b, "调研助手交来的东西看不懂，只用拆需求时的关键词搜")
        else:
            self._log(b, "没有助手能联网查资讯，只搜 GitHub 和论文")
        report = (await self.researcher.run(queries, news)).to_dict()
        report["worker"] = record.get("worker")
        report["cards"] = await self._learn(b, report["github"], rng)
        b["research"] = report
        folder = self.folder(b["id"])
        (folder / "research.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (folder / "调研报告.md").write_text(
            report_markdown(spec.title, b["request"], report), encoding="utf-8"
        )
        c = report["counts"]
        self._log(
            b,
            f"调研：GitHub {c['github']} 个、论文 {c['papers']} 篇、资讯和视频 {c['news']} 条、"
            f"dsh 插件 {c['plugins']} 个、学习卡 {len(report['cards'])} 张",
        )

    async def _learn(
        self, b: dict[str, Any], github: list[dict[str, Any]], rng: random.Random
    ) -> list[dict[str, Any]]:
        """挑分最高的几个项目，源码只读下载到 research/learn/，各交给一位助手读完写学习卡。"""
        n = self.rules.research.learn
        if n <= 0 or self.scout is None:
            return []
        picks = sorted(
            (g for g in github if g["verdict"] in ("adopt", "idea")),
            key=lambda g: -g["stars"],
        )[:n]

        async def one(g: dict[str, Any]) -> dict[str, Any] | None:
            assert self.scout is not None
            slug = re.sub(r"[^\w.-]+", "-", g["title"].replace("/", "__"))
            rel = f"research/learn/{slug}"
            c = Candidate("github", g["title"], g["url"], stars=g["stars"], license=g["license"])
            try:
                info = await self.scout.fetch_json(f"https://api.github.com/repos/{c.repo}")
                c.branch = str(info.get("default_branch") or "main")
                await self.scout.fetch(c, self.folder(b["id"]) / rel)
            except Exception as exc:  # noqa: BLE001 - 下载不了就只看说明写
                self._log(b, f"{g['title']} 的源码没下载成（{str(exc)[:60]}），只按简介写学习卡")
                rel = ""
            record: dict[str, Any] = {}
            prompt = self.render(
                "bench_learn",
                request=self._brief(b),
                repo=json.dumps(
                    {k: g[k] for k in ("title", "url", "stars", "license", "summary", "verdict")},
                    ensure_ascii=False,
                ),
                folder=rel or "（没有源码，只按简介写）",
            )
            chosen, result = await self._step(b, self.rules.research.kind, prompt, rng, record)
            if chosen is None or result is None or not result.ok:
                return None
            try:
                card = extract_json_object(result.output)
            except JSONOutputError:
                return None
            return {
                "repo": g["title"],
                "url": g["url"],
                "verdict": g["verdict"],
                "folder": rel,
                "worker": chosen.id,
                "what": str(card.get("what") or "")[:400],
                "borrow": [str(x)[:300] for x in (card.get("borrow") or [])][:6],
                "avoid": [str(x)[:300] for x in (card.get("avoid") or [])][:4],
                "files": [str(x)[:200] for x in (card.get("files") or [])][:8],
            }

        cards = await asyncio.gather(*(one(g) for g in picks))
        return [c for c in cards if c]

    def _sources_text(self, b: dict[str, Any]) -> str:
        lines = []
        for s in b.get("sources") or []:
            if s["verdict"] == "adopt" and s.get("folder"):
                lines.append(
                    f"- 可以直接用：{s['folder']}（{s['repo']}，{s['why']}）"
                    f"为「{s['need']}」：{s['summary']}"
                )
            elif s["verdict"] == "idea":
                lines.append(
                    f"- 只借思路：{s['url']}（{s['why']}）为「{s['need']}」：{s['summary']}"
                )
        r = b.get("research") or {}
        for c in r.get("cards") or []:
            where = (
                f"，源码在 {c['folder']}（只能读、借思路"
                + ("，许可证允许直接搬）" if c["verdict"] == "adopt" else "，不能照抄）")
                if c["folder"]
                else ""
            )
            lines.append(
                f"- 学习卡 {c['repo']}{where}：{c['what']}"
                + "".join(f"\n  · 借：{x}" for x in c["borrow"])
                + "".join(f"\n  · 别学：{x}" for x in c["avoid"])
            )
        for p in (r.get("papers") or [])[:3]:
            lines.append(f"- 论文：{p['title']}（{p['source']} {p['date']}）")
        return "\n".join(lines) or "（这次没找插件，全部用零件库自己写）"

    async def ask_json(self, bid: str, question: str) -> dict[str, Any]:
        """成品按用户自己的输入现场生成内容（行程、推荐……）：助手只交一个 JSON 对象。
        拿不到或看不懂时 data 为 None，窗口退回示例数据并告诉用户。"""
        text = await self.ask(bid, question, role="bench_ask_json", limit=60000)
        try:
            return {"text": "", "data": extract_json_object(text)}
        except JSONOutputError:
            return {"text": text[:400], "data": None}

    async def ask(
        self, bid: str, question: str, *, role: str = "bench_ask", limit: int = 4000
    ) -> str:
        """成品里的对话窗：用户的问题连同这个台的规格和示例数据，交给一位助手回答。
        在 ``ask/`` 子文件夹里跑，不碰台本身的文件。"""
        q = question.strip()
        if not q:
            raise ValueError("问题是空的")
        if len(q) > self.rules.ask_max_chars:
            raise ValueError(f"问题太长了（最多 {self.rules.ask_max_chars} 字）")
        b = self.get(bid)
        if b is None or self.root is None:
            raise LookupError("没有这个工作台")
        folder = self.folder(bid)
        spec = (
            (folder / "spec.json").read_text(encoding="utf-8")
            if (folder / "spec.json").is_file()
            else "{}"
        )
        data = []
        for p in sorted((folder / "data").glob("*.json"))[: self.rules.max_windows]:
            with contextlib.suppress(OSError, UnicodeDecodeError):
                data.append(f"## {p.stem}\n{p.read_text(encoding='utf-8')[:3000]}")
        prompt = self.render(
            role,
            request=self._brief(b),
            spec=spec[:6000],
            data="\n\n".join(data) or "（没有数据）",
            question=q,
        )
        chosen, result = await self.sc.step(
            self.rules.ask_kind,
            prompt,
            folder=folder / "ask",
            rng=random.Random(),
            record={},
        )
        if chosen is None or result is None:
            return "现在没有助手能回答（都没装、在冷却或今天次数用完了）。"
        if not result.ok:
            return "这次没答上来，稍后再试。"
        return result.output.strip()[:limit] or "（没有回答）"

    def _grow(self, b: dict[str, Any], spec: Spec) -> None:
        """过线以后：新零件收进本机零件库，整个台存成配方（自行拓展）。"""
        if self.library is None:
            return
        b["new_parts"] = self.library.promote(self.folder(b["id"]), b["id"])
        b["recipe"] = str(self.library.save_recipe(b, spec.to_dict()).name)
        if b["new_parts"]:
            self._log(b, "收进零件库：" + "、".join(b["new_parts"]))

    def _allow(self) -> tuple[str, ...]:
        """质检截图时放行的网址：配置里的公开数据（评审要看到真实地图和照片）。"""
        return tuple(dict.fromkeys(o for x in self.rules.net for o in x.origins))

    def _net_text(self) -> str:
        """能联网取的公开数据，给助手看：名字、用途（含参数）、来源。"""
        lines = []
        for x in self.rules.net:
            how = (
                "地图零件 mapFrame 自动使用"
                if x.kind == "tiles"
                else f"ctx.net.get('{x.name}', 参数)"
            )
            lines.append(f"- {x.name}：{x.use}（{how}；来源：{x.source}）")
        return "\n".join(lines) or "（成品不能联网：需要实时的信息用可信的参考数据，标日期和来源）"

    def _kit_text(self) -> str:
        return json.dumps(manifest(), ensure_ascii=False, indent=1)

    async def _write(
        self, b: dict[str, Any], spec: Spec, rec: dict[str, Any], rng: random.Random
    ) -> None:
        w = spec.window(rec["id"])
        assert w is not None
        rec["status"] = "running"
        self._save()
        prompt = self.render(
            "bench_window",
            request=self._brief(b),
            spec=json.dumps(spec.to_dict(), ensure_ascii=False, indent=1),
            window=json.dumps(w.__dict__, ensure_ascii=False, indent=1),
            file=w.file,
            data_file=w.data_file,
            kit=self._kit_text(),
            sources=self._sources_text(b),
            net=self._net_text(),
        )
        chosen, result = await self._step(b, self.rules.window_kind, prompt, rng, rec)
        rec["author"] = chosen.id if chosen else None
        if chosen is None or result is None or not result.ok:
            rec["status"] = "failed"
            rec["summary"] = "没有人能写这个窗口" if result is None else "写的时候出错了"
        else:
            rec["status"] = "done"
            rec["summary"] = result.output.strip()[-2000:] or "（没有说明）"
        self._log(b, f"窗口「{w.title}」{'写好了' if rec['status'] == 'done' else '没写成'}")

    async def _round(self, b: dict[str, Any], spec: Spec, n: int, rng: random.Random) -> bool:
        folder = self.folder(b["id"])
        out = folder / "report" / f"round-{n}"
        rnd: dict[str, Any] = {"n": n, "check": None, "reviews": [], "scores": {}, "passed": False}
        b["rounds"].append(rnd)
        try:
            report = await asyncio.to_thread(
                self.check, str(folder / "index.html"), out, allow=self._allow()
            )
        except QualityError as exc:
            raise RuntimeError(f"自动质检没法进行：{exc}") from None
        # 读代码就能看出来的问题（没用零件库、写死颜色、代码太大）一起算进质检
        report.findings += lint_windows(folder, [w["id"] for w in b["windows"]])
        rnd["check"] = report.to_dict()
        self._log(b, f"第 {n} 轮质检：{'通过' if report.passed else '不通过'}，{report.score} 分")
        shots = "\n".join(os.path.relpath(s, folder) for s in report.shots)
        authors = {w["id"]: w["author"] for w in b["windows"] if w["author"]}
        last = b["rounds"][-2] if len(b["rounds"]) > 1 else None
        reviews: list[dict[str, float]] = []
        for author, wids in review_groups(authors).items():
            rec: dict[str, Any] = {"author": author, "windows": wids}
            rnd["reviews"].append(rec)
            prompt = self.render(
                "bench_review",
                request=self._brief(b),
                spec=json.dumps(spec.to_dict(), ensure_ascii=False, indent=1),
                windows="\n".join(f"- {wid}（windows/{wid}.js）" for wid in wids),
                rubric="、".join(self.rules.rubric),
                shots=shots or "（这轮没有截图）",
                check=report.summary(),
                previous=self._previous(last, wids),
                requirements=requirements_text(spec),
            )
            chosen, result = await self._step(
                b, self.rules.review_kind, prompt, rng, rec, exclude=(author,)
            )
            if chosen is None or result is None or not result.ok:
                rec["error"] = "没有能评审的人（作者不能评自己写的窗口）"
                continue
            try:
                data = extract_json_object(result.output)
            except JSONOutputError:
                rec["error"] = "评审结果看不懂"
                continue
            raw = data.get("scores") if isinstance(data.get("scores"), dict) else {}
            scores = {}
            for k in self.rules.rubric:
                try:
                    scores[k] = max(0.0, min(10.0, float(raw.get(k, 0))))
                except (TypeError, ValueError):
                    scores[k] = 0.0
            rec["scores"] = scores
            rec["summary"] = str(data.get("summary") or "")[:400]
            rec["checks"] = {
                str(x.get("id")): {
                    "met": x.get("met") is True,
                    "why": str(x.get("why") or "")[:300],
                }
                for x in (data.get("checks") or [])
                if isinstance(x, dict) and x.get("id")
            }
            rec["problems"] = {
                str(x.get("id")): {
                    "problems": [str(p)[:300] for p in (x.get("problems") or [])][:10],
                    "fixes": [str(p)[:300] for p in (x.get("fixes") or [])][:10],
                }
                for x in (data.get("windows") or [])
                if isinstance(x, dict) and str(x.get("id")) in wids
            }
            reviews.append(scores)
        rnd["scores"] = merge_scores(reviews, self.rules.rubric)
        # 每组窗口都要有人评过才算数：缺了哪组，就当那组没评（不能靠少评过线）
        complete = bool(rnd["reviews"]) and all("scores" in r for r in rnd["reviews"])
        rnd["window_scores"] = {
            wid: round(mean(r["scores"].values()), 2)
            for r in rnd["reviews"]
            if r.get("scores")
            for wid in r["windows"]
        }
        rnd["requirements"] = merge_checks(spec.requirements, rnd["reviews"])
        met = all(r["met"] for r in rnd["requirements"])
        rnd["passed"] = complete and met and passes(report.passed, rnd["scores"], self.rules)
        if rnd["scores"]:
            b["score"] = round(mean(rnd["scores"].values()), 2)
        if rnd["requirements"]:
            miss = [r["id"] for r in rnd["requirements"] if not r["met"]]
            self._log(
                b,
                f"第 {n} 轮验收：{len(rnd['requirements']) - len(miss)}/{len(rnd['requirements'])}"
                + (f" 条做到，没做到：{'、'.join(miss)}" if miss else " 条全部做到"),
            )
        self._log(
            b,
            f"第 {n} 轮评审："
            + ("、".join(f"{k} {v:g}" for k, v in rnd["scores"].items()) or "没评成"),
        )
        return rnd["passed"]

    @staticmethod
    def _previous(last: dict[str, Any] | None, wids: list[str]) -> str:
        """上一轮评审对这几个窗口提的问题：这一轮先核对改了没有，分数才不会每轮换一把尺子。"""
        lines: list[str] = []
        for rec in (last or {}).get("reviews", []):
            for wid in wids:
                got = (rec.get("problems") or {}).get(wid)
                if got:
                    lines += [f"- {wid}：{p}" for p in got["problems"]]
        return "\n".join(lines) or "（第一轮，没有上一轮的问题）"

    async def _selftest(
        self,
        b: dict[str, Any],
        spec: Spec,
        rng: random.Random,
        only: list[str] | None = None,
        out: str = "selftest",
    ) -> list[str]:
        """写完先自测一遍（只开电脑·浅色，几秒钟）：脚本报错、没用零件库这种低级问题当场交回作者改，
        不占评审轮次，也不让评审拿着坏页面打分。``only``：只管这几个窗口（里程碑只写了一部分）。
        返回截图路径（里程碑演示给你看）。"""
        folder = self.folder(b["id"])
        try:
            report = await asyncio.to_thread(
                self.check,
                str(folder / "index.html"),
                folder / "report" / out,
                viewports=SELFTEST_VIEWPORTS,
                modes=("light",),
                allow=self._allow(),
            )
        except QualityError as exc:
            raise RuntimeError(f"自动质检没法进行：{exc}") from None
        scope = only if only is not None else [w["id"] for w in b["windows"]]
        report.findings += lint_windows(folder, scope)
        bad = [
            f.__dict__
            for f in report.findings
            if f.severity == "block"
            and f.check in SELFTEST_CHECKS
            and f.windows
            and set(f.windows) & set(scope)
        ]
        b["selftest"] = {"findings": bad}
        shots = [os.path.relpath(x, folder) for x in report.shots]
        if not bad:
            self._log(b, "写完自测：没有低级错误")
            return shots
        names = "、".join(sorted({w for f in bad for w in f["windows"]}))
        self._log(b, f"写完自测：{names} 有低级错误，交回作者先改")
        b["status"] = "reworking"
        await self._rework(b, spec, 0, rng, rnd={"check": {"findings": bad}}, only_flagged=True)
        return shots

    def _files(self, b: dict[str, Any], wid: str) -> list[str]:
        return [f"windows/{wid}.js", f"data/{wid}.json"]

    def _snapshot(self, b: dict[str, Any], n: int) -> None:
        """返工前把每个窗口现在的样子（第 n 轮评审看到的）存一份，改坏了能退回。"""
        folder = self.folder(b["id"])
        snap = folder / "report" / f"round-{n}" / "snap"
        for rec in b["windows"]:
            for rel in self._files(b, rec["id"]):
                if (folder / rel).is_file():
                    (snap / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(folder / rel, snap / rel)

    def _revert(self, b: dict[str, Any], n: int) -> None:
        """第 n 轮比上一轮分数低的窗口：退回上一轮评审看到的版本（返工前的快照），
        下次返工照上一轮的意见、换个改法（不让分数越改越低）。"""
        if n < 2 or len(b["rounds"]) < 2:
            return
        cur, prev = b["rounds"][-1], b["rounds"][-2]
        folder = self.folder(b["id"])
        snap = folder / "report" / f"round-{prev['n']}" / "snap"
        back: dict[str, list[float]] = {}
        for wid, now in (cur.get("window_scores") or {}).items():
            before = (prev.get("window_scores") or {}).get(wid)
            files = [rel for rel in self._files(b, wid) if (snap / rel).is_file()]
            if before is None or now >= before - REVERT_MARGIN or not files:
                continue
            for rel in files:
                shutil.copy2(snap / rel, folder / rel)
            back[wid] = [before, now]
            title = next((w["title"] for w in b["windows"] if w["id"] == wid), wid)
            self._log(b, f"「{title}」第 {n} 轮分数从 {before:g} 降到 {now:g}，退回上一版")
        cur["reverted"] = back

    def _problems_for(self, rnd: dict[str, Any], wid: str) -> str:
        lines: list[str] = []
        # 验收清单没做到的放最前面：归这个窗口负责的（没写归谁的大家都看）
        for r in rnd.get("requirements") or []:
            if r["met"] or (r["windows"] and wid not in r["windows"]):
                continue
            why = "；".join(r["why"]) or "评审判为没做到"
            lines.append(f"- [验收·必须做到·{r['id']}] {r['text']}：{why}")
        check = rnd.get("check") or {}
        for f in check.get("findings", []):
            # 知道出在哪几个窗口的，只交给那几个窗口的作者；不知道的大家都看
            if f.get("windows") and wid not in f["windows"]:
                continue
            level = "必须修" if f["severity"] == "block" else "建议修"
            lines.append(f"- [自动质检·{level}·{f['where']}] {f['check']}：{f['detail']}")
        for rec in rnd.get("reviews", []):
            got = (rec.get("problems") or {}).get(wid)
            if got:
                lines += [f"- [评审] {p}" for p in got["problems"]]
                lines += [f"  建议：{p}" for p in got["fixes"]]
            if wid in rec.get("windows", []) and rec.get("summary"):
                lines.append(f"- [评审总评] {rec['summary']}")
        return "\n".join(lines)

    async def _rework(
        self,
        b: dict[str, Any],
        spec: Spec,
        n: int,
        rng: random.Random,
        rnd: dict[str, Any] | None = None,
        only_flagged: bool = False,
    ) -> None:
        rnd = rnd if rnd is not None else b["rounds"][-1]
        if n:
            self._snapshot(b, n)
        reverted = rnd.get("reverted") or {}
        jobs = []
        for rec in b["windows"]:
            if rec["id"] in reverted and len(b["rounds"]) > 1:
                # 退回了上一版：照上一轮的意见改，并提醒上次的改法让分数降了
                before, now = reverted[rec["id"]]
                problems = (
                    f"- [退回] 上一次的改法让分数从 {before:g} 降到 {now:g}，已退回改之前的版本；"
                    "这次换个改法，别把做好的地方改坏\n"
                    + self._problems_for(b["rounds"][-2], rec["id"])
                )
            else:
                problems = self._problems_for(rnd, rec["id"])
            if not problems and (rec["status"] == "done" or only_flagged):
                continue
            w = spec.window(rec["id"])
            assert w is not None
            prompt = self.render(
                "bench_rework",
                request=self._brief(b),
                spec=json.dumps(spec.to_dict(), ensure_ascii=False, indent=1),
                window=json.dumps(w.__dict__, ensure_ascii=False, indent=1),
                file=w.file,
                data_file=w.data_file,
                problems=problems or "- 上一次没写成，请从头写这个窗口",
                kit=self._kit_text(),
                sources=self._sources_text(b),
                net=self._net_text(),
            )
            jobs.append(self._redo(b, rec, prompt, rng, n))
        await asyncio.gather(*jobs)

    async def _redo(
        self, b: dict[str, Any], rec: dict[str, Any], prompt: str, rng: random.Random, n: int
    ) -> None:
        record: dict[str, Any] = {}
        rec.setdefault("rework", []).append(record)
        # 返工优先交回作者本人（他最熟）；作者这会儿干不了（冷却、次数用完）就按分数换人，
        # 换了以后新的人就是作者
        author = rec["author"]
        w = self.sc.worker(author) if author else None
        preset = author if w is not None and not excluded(self.sc.state(w)) else None
        chosen, result = await self._step(
            b, self.rules.window_kind, prompt, rng, record, preset=preset
        )
        if chosen is not None and result is not None and result.ok:
            rec["author"], rec["status"] = chosen.id, "done"
            rec["summary"] = result.output.strip()[-2000:] or rec["summary"]
        done = "完成" if rec["status"] == "done" else "没成"
        stage = f"第 {n} 轮返工" if n else "自测后返工"
        self._log(b, f"{stage}「{rec['title']}」{done}")
