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
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from roundtable.core.benchkit.library import Library
from roundtable.core.benchkit.lint import lint_windows
from roundtable.core.benchkit.quality import QualityError, Report, run_check
from roundtable.core.benchkit.scout import Scout
from roundtable.core.benchkit.spec import (
    Spec,
    SpecError,
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
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        seed: int | None = None,
    ) -> None:
        self.rules = rules
        self.root = root
        self.sc = supercomputer
        self.render = render
        self.check = check
        self.scout = scout
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
        return {
            "enabled": self.rules.enabled,
            "unavailable": self.unavailable_reason(),
            "busy": self.busy,
            "max_rounds": self.rules.max_rounds,
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
            "rounds": [],
            "score": None,
            "result": None,
            "log": [],
        }
        self._save()
        self._job = asyncio.get_running_loop().create_task(self._run(self.build))
        return self.build

    async def cancel(self) -> dict[str, Any] | None:
        job = self._job
        if job is not None and not job.done():
            job.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await job
            b = self.build
            if b is not None and b["status"] not in ("done", "needs_you", "failed", "cancelled"):
                b["status"] = "cancelled"  # 还没开始跑就叫停了
                b["finished_at"] = _iso(self.now())
                self._save()
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

    async def _run(self, b: dict[str, Any]) -> None:
        rng = random.Random(b["seed"])
        try:
            spec = await self._plan(b, rng)
            write_shell(
                self.folder(b["id"]),
                spec,
                b["id"],
                net=[
                    x.model_dump(exclude={"also", "ttl_min"}) | {"ttl": x.ttl_min}
                    for x in self.rules.net
                ],
            )
            if self.library is not None:
                self.library.seed(self.folder(b["id"]))
            if spec.needs and self.scout is not None and self.rules.scout:
                b["status"] = "scouting"
                await self._scout(b, spec)
            b["status"] = "building"
            self._log(b, f"规格：{spec.title}，{len(spec.windows)} 个窗口")
            await asyncio.gather(*(self._write(b, spec, w, rng) for w in b["windows"]))
            await self._selftest(b, spec, rng)
            for n in range(1, self.rules.max_rounds + 1):
                b["status"] = "checking"
                ok = await self._round(b, spec, n, rng)
                if ok:
                    b["status"] = "done"
                    self._log(b, f"第 {n} 轮过线")
                    self._grow(b, spec)
                    break
                self._revert(b, n)
                if n == self.rules.max_rounds:
                    b["status"] = "needs_you"
                    self._log(b, f"做满 {n} 轮还没过线，停下来等你决定")
                    break
                b["status"] = "reworking"
                await self._rework(b, spec, n, rng)
        except asyncio.CancelledError:
            b["status"] = "cancelled"
            self._log(b, "你叫停了")
            raise
        except Exception as exc:  # noqa: BLE001 - 记下来给界面看，不让后台任务悄悄死掉
            b["status"] = "failed"
            self._log(b, f"出错了：{type(exc).__name__}: {exc}")
        finally:
            b["finished_at"] = _iso(self.now())
            self._save()

    async def _plan(self, b: dict[str, Any], rng: random.Random) -> Spec:
        m = manifest()
        record: dict[str, Any] = {}
        b["plan"] = record
        prompt = self.render(
            "bench_plan",
            request=b["request"],
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
            request=b["request"],
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
            request=b["request"],
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
                request=b["request"],
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

    async def _selftest(self, b: dict[str, Any], spec: Spec, rng: random.Random) -> None:
        """写完先自测一遍（只开电脑·浅色，几秒钟）：脚本报错、没用零件库这种低级问题当场交回作者改，
        不占评审轮次，也不让评审拿着坏页面打分。"""
        folder = self.folder(b["id"])
        try:
            report = await asyncio.to_thread(
                self.check,
                str(folder / "index.html"),
                folder / "report" / "selftest",
                viewports=SELFTEST_VIEWPORTS,
                modes=("light",),
                allow=self._allow(),
            )
        except QualityError as exc:
            raise RuntimeError(f"自动质检没法进行：{exc}") from None
        report.findings += lint_windows(folder, [w["id"] for w in b["windows"]])
        bad = [
            f.__dict__
            for f in report.findings
            if f.severity == "block" and f.check in SELFTEST_CHECKS and f.windows
        ]
        b["selftest"] = {"findings": bad}
        if not bad:
            self._log(b, "写完自测：没有低级错误")
            return
        names = "、".join(sorted({w for f in bad for w in f["windows"]}))
        self._log(b, f"写完自测：{names} 有低级错误，交回作者先改")
        b["status"] = "reworking"
        await self._rework(b, spec, 0, rng, rnd={"check": {"findings": bad}}, only_flagged=True)

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
                request=b["request"],
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
