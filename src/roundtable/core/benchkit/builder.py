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
import hashlib
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

from roundtable.core.benchkit.intent import (
    FIVE_KEYS,
    add_voice,
    answer_intent,
    intent_counts,
    intent_of,
    intent_text,
    link_voices,
    open_items,
    questions_of,
    stage_task,
    trace,
    voices_text,
)
from roundtable.core.benchkit.library import Library
from roundtable.core.benchkit.lint import lint_data, lint_windows
from roundtable.core.benchkit.quality import Finding, QualityError, Report, run_check, run_probes
from roundtable.core.benchkit.research import Researcher, report_markdown
from roundtable.core.benchkit.samples import SampleCatalog, install, load_catalog, samples_text
from roundtable.core.benchkit.scout import Candidate, Scout
from roundtable.core.benchkit.spec import (
    ASSET_ROLES,
    MATERIAL_EXT,
    Spec,
    SpecError,
    apply_design,
    design_brief,
    design_catalog,
    design_lines,
    load_spec,
    look_of,
    manifest,
    normalize,
    parts_brief,
    shell_html,
    write_shell,
)
from roundtable.core.config.loader import ConfigError
from roundtable.core.config.schema import BenchRules
from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.supercomputer.scoring import excluded

PROFILE_FILE = "_client/profile.json"  # 甲方档案（只存本机）
PROFILE_MAX = 40
FAKE_DONE = "说写好了，但窗口文件没写出来（假完成，不算数）"
FAKE_REDO = "说改好了，但文件一个字没动（假完成，不算数）"
RUNS_KEPT = 12
RECENT = 10
# 写完自测：只开一种屏宽，只管这几项低级错误
SELFTEST_VIEWPORTS = (("电脑", 1280, 860),)
REVERT_MARGIN = 0.3  # 一组窗口的评审均分比上一轮低这么多以上，就退回上一版
SELFTEST_CHECKS = (
    *("页面报错", "点了报错", "窗口没用零件库", "自己画插图"),
    *("数据文件看不懂", "地址为空", "占位数据", "和共用信息矛盾"),
)
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


def _who(w: Any) -> str:
    """助手的显示名（Codex、Claude Code、dsh……）。"""
    return str(getattr(w, "label", None) or w.id)


def review_groups(authors: dict[str, str]) -> dict[str, list[str]]:
    """按作者分组：{作者: [他写的窗口]}，评审时整组交给不是他的一位。"""
    groups: dict[str, list[str]] = {}
    for wid, who in authors.items():
        groups.setdefault(who, []).append(wid)
    return groups


def stuck_report(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    """做满轮数没过线：哪几条没做到、卡在哪、建议怎么办（给你选再跑一轮 / 改要求 / 就这样）。"""
    last = rounds[-1] if rounds else {}
    missing = []
    for r in last.get("requirements") or []:
        if r["met"]:
            continue
        missed = sum(
            1
            for rnd in rounds
            for x in rnd.get("requirements") or []
            if x["id"] == r["id"] and not x["met"]
        )
        missing.append({"id": r["id"], "text": r["text"], "why": r["why"], "missed": missed})
    blocks = [
        f"{f['check']}（{f['where']}）"
        for f in (last.get("check") or {}).get("findings", [])
        if f.get("severity") == "block"
    ]
    high = [
        x["problem"] for x in (last.get("attack") or {}).get("bugs", []) if x["severity"] == "high"
    ]
    trend = [round(mean(r["scores"].values()), 2) for r in rounds if r.get("scores")]
    advice: list[str] = []
    for m in missing:
        if m["missed"] >= 2 and m["missed"] == len(rounds):
            advice.append(
                f"「{m['id']}」连续 {m['missed']} 轮都没做到：可能说得太笼统或这一版做不到，"
                "建议改得更具体，或者先删掉"
            )
    if blocks:
        advice.append(f"自动质检还有 {len(blocks)} 处必须修：{'、'.join(blocks[:3])}")
    if high:
        advice.append(f"对抗手找到的严重问题还有 {len(high)} 个没修：{high[0]}")
    if len(trend) >= 2 and trend[-1] - trend[0] >= 0.5:
        advice.append(f"评审分在涨（{trend[0]:g} → {trend[-1]:g}），再跑一轮有希望过线")
    elif len(trend) >= 2:
        advice.append(
            f"评审分没怎么涨（{trend[0]:g} → {trend[-1]:g}），再跑多半也一样，"
            "建议改要求或就这样收下"
        )
    return {"missing": missing, "blocks": blocks, "high": high, "trend": trend, "advice": advice}


def _short(v: Any, limit: int = 80) -> str:
    text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def desk_shot(shots: list[str]) -> str | None:
    """一组截图里挑电脑·浅色那张（没有就第一张）。"""
    desk = [x for x in shots if Path(x).name.startswith("电脑")] or shots
    return next((x for x in desk if "light" in Path(x).name), desk[0] if desk else None)


def thumb_of(b: dict[str, Any]) -> str | None:
    """项目墙上的缩略图：最近一轮质检的电脑截图（优先浅色），相对成品文件夹的路径；没有就 None。"""
    for r in reversed(b.get("rounds") or []):
        shots = [Path(str(p)).name for p in (r.get("check") or {}).get("shots") or []]
        desk = [x for x in shots if x.startswith("电脑")] or shots
        if desk:
            pick = next((x for x in desk if "light" in x), desk[0])
            return f"report/round-{r.get('n', len(b['rounds']))}/{pick}"
    return None


INTENT_HEAD = "理想画像（访谈时整理，括号里是把握）："
ONE_GO = "（这个项目不分段：一次把这个窗口完整做好。）"
ALL_DONE = "（各段都做完了，现在是整体质检后的返工：按 <problems> 改，别把做好的地方改坏。）"


def fingerprint(folder: Path, files: list[str]) -> str:
    """窗口文件的指纹：识别「说写好了 / 改好了，文件其实没动」的假完成。没有文件就是空串。"""
    h = hashlib.sha256()
    seen = False
    for f in files:
        p = folder / f
        if f and p.is_file():
            seen = True
            h.update(f.encode("utf-8") + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest() if seen else ""


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
        whitelist: Callable[[], dict[str, str]] | None = None,
        probe: Callable[..., list[dict[str, Any]]] | None = run_probes,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        seed: int | None = None,
        samples: SampleCatalog | None = None,
        skill: Callable[[str], tuple[str, str]] | None = None,
        desk: Any = None,
    ) -> None:
        self.rules = rules
        self.desk = desk  # 资料台（能力地图 + 榜单），写窗口时按工作台类型挑工具附上
        if samples is None:
            try:
                samples = load_catalog()
            except ConfigError:
                samples = SampleCatalog()
        self.samples = samples  # 本地示例素材清单（config/bench_samples.yaml）
        self.skill = skill  # 地基技能：名字 → (短版, 长版)（测试换成假的）
        self.root = root
        self.sc = supercomputer
        self.render = render
        self.check = check
        self.scout = scout
        self.researcher = researcher
        # dsh 插件白名单 {仓库 owner/name: 白名单 id}：调研列出的插件标出「已审过，可以装」
        self.whitelist = whitelist
        self.probe = probe  # 可执行的验收（测试换成假的）
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
                rounds = b.get("rounds") or []
                out.append(
                    {k: b.get(k) for k in keys} | {"rounds": len(rounds), "thumb": thumb_of(b)}
                )
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
            "profile": self.profile(),
            "library": {
                "parts": self.library.parts() if self.library else [],
                "recipes": self.library.recipes()[:RECENT] if self.library else [],
            },
        }

    # --- 交活 -------------------------------------------------------------------

    def submit(
        self, request: str, materials: list[str] | None = None, *, from_product: bool = False
    ) -> dict[str, Any]:
        """开工：一句话，可以带拖进来的素材（先用 stage_material 收下，这里给编号）。

        from_product：拖进来的是一个成品（图片、动图、视频），要的是能做出类似成品的台
        （第 6 批「从成品反推」）：每一步都加上反推技能，先拆出哪些固定、哪些可以换。"""
        request = request.strip()
        if not request:
            raise ValueError("想要什么样的工作台？写一句话就行")
        if from_product and not materials:
            raise ValueError("从成品反推要先把成品（图片、动图或视频）拖进来")
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
            "meeting": None,
            "team": {"cards": []},
            "plugin_requests": [],
            "materials": [],
            "look": None,
            "gate": None,
            "timeline": [],
            "changes": [],
            "voices": [],
            "intent": None,
            "from_product": bool(from_product),
            "rounds": [],
            "score": None,
            "result": None,
            "log": [],
        }
        add_voice(self.build, "开工", request, _iso(now))
        for item in self.profile():  # 甲方档案：以前定下的偏好，每个新项目都带上
            add_voice(self.build, "档案", item["text"], _iso(now), item["id"])
        self._take_materials(self.build, materials or [])
        self._save()
        self._start_job(self.build, lambda: self._run(self.build))
        return self.build

    # --- 甲方档案（第 6 批）：你的长期偏好，只存本机，可看可改可删 ---------------------------

    def _profile_path(self) -> Path | None:
        return None if self.root is None else self.root / PROFILE_FILE

    def profile(self) -> list[dict[str, Any]]:
        p = self._profile_path()
        if p is None or not p.is_file():
            return []
        try:
            items = json.loads(p.read_text(encoding="utf-8")).get("items") or []
        except (OSError, ValueError, AttributeError):
            return []
        return [x for x in items if isinstance(x, dict) and x.get("id") and x.get("text")]

    def _save_profile(self, items: list[dict[str, Any]]) -> None:
        p = self._profile_path()
        if p is None:
            raise RuntimeError("没有存放档案的地方")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, p)

    def profile_add(self, text: str, src: str = "") -> list[dict[str, Any]]:
        """记下一条偏好（你写的，或从某个项目的原话点「记住」）。重复的不再加。"""
        text = " ".join(str(text).split())[:200]
        if not text:
            raise ValueError("写一句你的偏好")
        items = self.profile()
        if any(x["text"] == text for x in items):
            return items
        if len(items) >= PROFILE_MAX:
            raise ValueError(f"档案最多 {PROFILE_MAX} 条，先删掉几条不用的")
        n = max([int(x["id"][1:]) for x in items if x["id"][1:].isdigit()] or [0]) + 1
        items.append({"id": f"p{n}", "text": text, "src": str(src)[:40], "at": _iso(self.now())})
        self._save_profile(items)
        return items

    def profile_remove(self, pid: str) -> list[dict[str, Any]]:
        items = self.profile()
        left = [x for x in items if x["id"] != pid]
        if len(left) == len(items):
            raise LookupError("档案里没有这一条")
        self._save_profile(left)
        return left

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

    def _save(self, b: dict[str, Any] | None = None) -> None:
        b = b or self.build
        if b is None or self.root is None:
            return
        if b.get("voices"):
            b["trace"] = trace(b)
        path = self.folder(b["id"]) / "build.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    async def _step(self, b, kind, prompt, rng, record, *, role: str = "", task: str = "", **kw):
        chosen, result = await self.sc.step(
            kind,
            prompt,
            folder=self.folder(b["id"]),
            rng=rng,
            record=record,
            log=lambda t: self._log(b, t),
            **kw,
        )
        if role and chosen is not None:
            self._card(b, chosen, role, task, bool(result is not None and result.ok))
        return chosen, result

    def _card(self, b: dict[str, Any], who: Any, role: str, task: str, ok: bool) -> None:
        """协作看板：谁、当什么角色、干了哪件事、成没成。界面按人分栏显示。"""
        team = b.setdefault("team", {"cards": []})
        team["cards"].append(
            {
                "at": _iso(self.now()),
                "who": who.id,
                "label": _who(who),
                "role": role,
                "task": task[:120],
                "ok": ok,
            }
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

    # --- 素材：拖进来的图片和视频（视频由浏览器截几帧），看一遍出分析，签字时标用处 --------------

    def _staging(self) -> Path:
        if self.root is None:
            raise ValueError("没有存放素材的地方")
        return self.root / "_materials"

    def stage_material(self, name: str, data: bytes, of: str = "") -> dict[str, Any]:
        """先收下一份素材（开工前或签字前拖进来的），返回编号；开工 / 加进工作台时再拷过去。
        of 是视频的编号：浏览器从那个视频里截的帧。"""
        rules = self.rules.materials
        if not rules.enabled:
            raise ValueError("素材功能关着")
        name = Path(str(name or "")).name[:80] or "素材"
        ext = Path(name).suffix.lower()
        kind = MATERIAL_EXT.get(ext)
        if kind is None:
            raise ValueError("只收图片（png、jpg、webp、gif）和视频（mp4、webm、mov）")
        limit = rules.max_video_mb if kind == "video" else rules.max_image_mb
        if not data or len(data) > limit * 1024 * 1024:
            raise ValueError(f"文件是空的或超过 {limit} MB")
        folder = self._staging()
        folder.mkdir(parents=True, exist_ok=True)
        if of:
            if not re.fullmatch(r"[0-9a-f]{12}", of) or not (folder / f"{of}.json").is_file():
                raise ValueError("找不到这个视频")
            if kind != "image":
                raise ValueError("视频截帧要是图片")
            kind = "frame"
        mid = secrets.token_hex(6)
        meta = {"id": mid, "name": name, "kind": kind, "file": f"{mid}{ext}", "of": of}
        (folder / meta["file"]).write_bytes(data)
        (folder / f"{mid}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return meta

    def _take_materials(self, b: dict[str, Any], ids: list[str]) -> list[dict[str, Any]]:
        """把收下的素材拷进这个工作台的 materials/（视频的截帧跟着视频一起来）。"""
        if not ids:
            return []
        folder = self._staging()
        metas = {}
        for p in folder.glob("*.json") if folder.is_dir() else []:
            with contextlib.suppress(ValueError, OSError):
                m = json.loads(p.read_text(encoding="utf-8"))
                metas[m["id"]] = m
        want = [i for i in dict.fromkeys(ids) if i in metas]
        want += [m["id"] for m in metas.values() if m["of"] in want and m["id"] not in want]
        have = {m["id"] for m in b["materials"]}
        dest = self.folder(b["id"]) / "materials"
        added = []
        for mid in want:
            if mid in have or len(b["materials"]) >= self.rules.materials.max_files:
                continue
            m = metas[mid]
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(folder / m["file"], dest / m["file"])
            b["materials"].append(m | {"seen": False})
            added.append(m)
        if added:
            self._log(b, f"收到 {len(added)} 份素材")
        return added

    def _unseen(self, b: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            m for m in b.get("materials") or [] if m["kind"] in ("image", "frame") and not m["seen"]
        ][: self.rules.materials.max_look]

    def _material_label(self, b: dict[str, Any], m: dict[str, Any]) -> str:
        if m["kind"] == "frame":
            video = next((x for x in b["materials"] if x["id"] == m["of"]), None)
            return f"视频「{video['name'] if video else '?'}」的截帧 {m['name']}"
        return f"图片「{m['name']}」"

    async def _look(self, b: dict[str, Any]) -> None:
        """请能看图的助手看一遍素材：版型、配色、材质、风格、值得借鉴的地方。结果写进方案和选型推荐。"""
        todo = self._unseen(b)
        if not todo:
            return
        b["status"] = "looking"
        kind = self.rules.materials.look_kind
        workers = getattr(getattr(self.sc, "rules", None), "workers", None) or []
        blind = tuple(w.id for w in workers if not w.skills.get(kind))
        prompt = self.render(
            "bench_look",
            request=b["request"],
            files="\n".join(
                f"- materials/{m['file']}（{self._material_label(b, m)}）" for m in todo
            ),
            design=design_brief(),
        )
        record: dict[str, Any] = {}
        chosen, result = await self._step(
            b,
            kind,
            prompt,
            self._rng(b, "look"),
            record,
            role="看素材",
            task=f"看 {len(todo)} 张图",
            exclude=blind,
        )
        if chosen is None or result is None or not result.ok:
            self._log(b, "没有能看图的助手（或没看成），素材只当文件交给写窗口的助手参考")
            return
        try:
            got = look_of(extract_json_object(result.output), [m["file"] for m in todo])
        except JSONOutputError:
            self._log(b, "看素材的结果看不懂，素材只当文件参考")
            return
        for m in todo:
            m["seen"] = True
        old = b.get("look") or {"items": [], "palette": []}
        got["items"] = old["items"] + got["items"]
        got["palette"] = list(dict.fromkeys(old["palette"] + got["palette"]))[:10]
        got["worker"] = record.get("worker")
        b["look"] = got
        lines = [
            f"- {x['file']}：{x['what']}；借鉴：{x['borrow']}" for x in got["items"] if x["what"]
        ]
        b["brief"] = (
            f"{self._brief(b)}\n\n你拖进来的参考素材（在 materials/ 里，看过的分析）："
            f"{got['summary']}\n配色：{'、'.join(got['palette']) or '没提取到'}\n"
            + "\n".join(lines)
        )
        self._mark(b, "立项", "看素材", f"看了 {len(todo)} 张：{got['summary'][:200]}")

    def attach(self, bid: str, ids: list[str]) -> dict[str, Any]:
        """签字前再拖进来的素材：拷进工作台，选型台上可以标用处（不花次数）；要分析再点「看一遍」。"""
        b = self._at_gate(bid, "sign")
        added = self._take_materials(b, ids)
        if not added:
            raise ValueError("没有收到新的素材")
        b["proposal"]["design"]["materials"] = b["materials"]
        self._save()
        return b

    def look(self, bid: str) -> dict[str, Any]:
        """签字前：把还没看过的素材请助手看一遍（占 1 次），看完回到选型台。"""
        b = self._at_gate(bid, "sign")
        if not self._unseen(b):
            raise ValueError("没有还没看过的图")
        if self.busy:
            raise RuntimeError("正在做另一个工作台，等它停下来再继续")
        gate = b["gate"]
        b["gate"] = None

        async def run() -> None:
            await self._look(b)
            d = b["proposal"]["design"]
            d["look"] = b.get("look")
            d["materials"] = b["materials"]
            b["gate"] = gate
            b["status"] = "waiting"

        self._start_job(b, run)
        return b

    async def _run(self, b: dict[str, Any]) -> None:
        if self.rules.materials.look and self._unseen(b):
            await self._look(b)
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
        if self.root is not None:
            b["samples"] = install(self.root, self.folder(b["id"]), self.samples)
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
        if self.rules.team.enabled:
            b["status"] = "meeting"
            await self._meeting(b, spec, rng)
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

    async def _rounds(
        self, b: dict[str, Any], spec: Spec, rng: random.Random, limit: int | None = None
    ) -> None:
        start = len(b["rounds"])
        limit = limit or self.rules.max_rounds
        b["stuck"] = None
        for k in range(1, limit + 1):
            n = start + k
            b["status"] = "checking"
            ok = await self._round(b, spec, n, rng)
            if ok:
                b["status"] = "done"
                self._log(b, f"第 {n} 轮过线")
                self._grow(b, spec)
                break
            self._revert(b, n)
            if k == limit:
                b["status"] = "needs_you"
                b["stuck"] = stuck_report(b["rounds"])
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
            left = open_items(trace(b)) if b.get("voices") else []
            self._gate(
                b,
                "accept",
                "试用成品，逐条验收"
                + (f"（你说的话里有 {len(left)} 句还没做到，见追踪表）" if left else ""),
            )

    # --- 三位助手协作：方案会、对抗手、dsh 插件申请 ------------------------------------

    async def _meeting(self, b: dict[str, Any], spec: Spec, rng: random.Random) -> None:
        """方案会：几位助手各提一份做法（每人一份，不重复找同一个人），再互相投票（看不到自己的，
        顺序各自打乱）。得票最多的当主案，平票抽签；只有一份就是它。主案的做法交给写窗口的人。"""
        team = self.rules.team
        m: dict[str, Any] = {"pitches": [], "votes": [], "lead": None, "borrow": []}
        b["meeting"] = m
        spec_json = json.dumps(spec.to_dict(), ensure_ascii=False, indent=1)
        prompt = self.render(
            "bench_pitch", request=self._brief(b), spec=spec_json, sources=self._sources_text(b)
        )
        asked: list[str] = []
        for _ in range(team.pitches):
            record: dict[str, Any] = {}
            chosen, result = await self._step(
                b,
                team.pitch_kind,
                prompt,
                rng,
                record,
                exclude=tuple(asked),
                role="主案候选",
                task="方案会：提一份做法",
            )
            if chosen is None:
                break
            asked.append(chosen.id)
            if result is None or not result.ok:
                continue
            try:
                d = extract_json_object(result.output)
            except JSONOutputError:
                self._log(b, f"{_who(chosen)} 的方案看不懂，不算")
                continue
            m["pitches"].append(
                {
                    "worker": chosen.id,
                    "author": _who(chosen),
                    "label": "",
                    "idea": str(d.get("idea") or "")[:600],
                    "hero": str(d.get("hero") or "")[:300],
                    "plan": [str(x)[:300] for x in (d.get("plan") or [])][:8],
                    "risks": [str(x)[:300] for x in (d.get("risks") or [])][:5],
                    "cut": [str(x)[:200] for x in (d.get("cut") or [])][:3],
                    "votes": 0,
                }
            )
        pitches = m["pitches"]
        if not pitches:
            self._log(b, "方案会没收到能用的方案，按规格直接做")
            return
        rng.shuffle(pitches)
        for i, p in enumerate(pitches):
            p["label"] = "ABCDE"[i]
        if team.vote and len(pitches) > 1:
            await self._votes(b, spec_json, rng)
        best = max(p["votes"] for p in pitches)
        top = [p for p in pitches if p["votes"] == best]
        lead = top[0] if len(top) == 1 else rng.choice(top)
        m["lead"] = lead["worker"]
        tally = "、".join(f"{p['label']} {p['votes']} 票" for p in pitches)
        self._log(b, f"方案会：{len(pitches)} 份方案（{tally}），主案是 {lead['author']}")
        self._mark(b, "立项", "方案会", f"主案：方案 {lead['label']}（{lead['author']}），{tally}")

    async def _votes(self, b: dict[str, Any], spec_json: str, rng: random.Random) -> None:
        m = b["meeting"]
        by_label = {p["label"]: p for p in m["pitches"]}
        for voter in [p["worker"] for p in m["pitches"]]:
            others = [p for p in m["pitches"] if p["worker"] != voter]
            rng.shuffle(others)
            text = "\n\n".join(
                f"方案 {p['label']}：{p['idea']}\n主视觉：{p['hero']}\n"
                + "\n".join(f"- {x}" for x in p["plan"])
                + ("\n风险：" + "；".join(p["risks"]) if p["risks"] else "")
                for p in others
            )
            prompt = self.render("bench_vote", request=self._brief(b), spec=spec_json, pitches=text)
            record: dict[str, Any] = {}
            chosen, result = await self._step(
                b,
                self.rules.team.pitch_kind,
                prompt,
                rng,
                record,
                preset=voter,
                role="投票",
                task="方案会：给别人的方案投票",
            )
            # 本人干不了时调度会换人；换来的人可能正是某份方案的作者，这票不算
            if chosen is None or chosen.id != voter or result is None or not result.ok:
                continue
            try:
                d = extract_json_object(result.output)
            except JSONOutputError:
                continue
            pick = str(d.get("pick") or "").strip().upper().removeprefix("方案").strip()
            if pick not in by_label or by_label[pick]["worker"] == voter:
                continue
            by_label[pick]["votes"] += 1
            borrow = [str(x)[:300] for x in (d.get("borrow") or [])][:3]
            m["votes"].append(
                {
                    "voter": voter,
                    "label": _who(chosen),
                    "pick": pick,
                    "why": str(d.get("why") or "")[:300],
                    "borrow": borrow,
                }
            )
            m["borrow"] = list(dict.fromkeys([*m["borrow"], *borrow]))[:6]

    async def _attack(
        self,
        b: dict[str, Any],
        spec: Spec,
        rnd: dict[str, Any],
        check: str,
        shots: str,
        authors: dict[str, str],
        rng: random.Random,
    ) -> None:
        """对抗手：专门想办法把成品弄坏。先找没写过窗口的人；人手不够才让作者自己来
        （只报毛病不打分，不算自评）。"""
        team = self.rules.team
        rec: dict[str, Any] = {"bugs": []}
        rnd["attack"] = rec
        wids = [w["id"] for w in b["windows"]]
        prompt = self.render(
            "bench_attack",
            request=self._brief(b),
            spec=json.dumps(spec.to_dict(), ensure_ascii=False, indent=1),
            windows="\n".join(f"- {wid}（windows/{wid}.js）" for wid in wids),
            shots=shots or "（这轮没有截图）",
            check=check,
            requirements=requirements_text(spec),
        )
        kw: dict[str, Any] = {"role": "对抗", "task": f"第 {rnd['n']} 轮：找毛病"}
        writers = tuple(sorted(set(authors.values())))
        chosen, result = await self._step(
            b, team.attack_kind, prompt, rng, rec, exclude=writers, **kw
        )
        if chosen is None and writers:
            chosen, result = await self._step(b, team.attack_kind, prompt, rng, rec, **kw)
        if chosen is None or result is None or not result.ok:
            rec["error"] = "这轮没有人能当对抗手"
            return
        try:
            d = extract_json_object(result.output)
        except JSONOutputError:
            rec["error"] = "对抗手交来的东西看不懂"
            return
        for x in d.get("bugs") or []:
            if not isinstance(x, dict) or not str(x.get("problem") or "").strip():
                continue
            sev = str(x.get("severity") or "").lower()
            win = str(x.get("window") or "")
            rec["bugs"].append(
                {
                    "window": win if win in wids else "",
                    "severity": sev if sev in ("high", "mid", "low") else "mid",
                    "problem": str(x["problem"])[:300],
                    "steps": str(x.get("steps") or "")[:300],
                    "fix": str(x.get("fix") or "")[:300],
                }
            )
        rec["bugs"] = rec["bugs"][:12]
        rec["summary"] = str(d.get("summary") or "")[:300]
        rec["worker"] = chosen.id
        counts = {
            k: sum(1 for x in rec["bugs"] if x["severity"] == k) for k in ("high", "mid", "low")
        }
        self._log(
            b,
            f"第 {rnd['n']} 轮对抗：{_who(chosen)} 找出严重 {counts['high']}、"
            f"一般 {counts['mid']}、小 {counts['low']} 个问题",
        )

    def request_plugin(self, bid: str, name: str) -> dict[str, Any]:
        """你点「申请装上」：记进这个项目和全局申请单。装不装由审过源码的白名单决定
        （config/dsh_plugins.yaml 钉版本和哈希，合并后在电脑上运行 setup_dsh.py 装上）。"""
        b = self.get(bid)
        if b is None:
            raise KeyError(bid)
        plugins = (b.get("research") or {}).get("plugins") or []
        item = next((p for p in plugins if p["title"] == name), None)
        if item is None:
            raise ValueError("调研报告里没有这个插件")
        reqs = b.setdefault("plugin_requests", [])
        if name not in [r["name"] for r in reqs]:
            entry = {"name": name, "url": item["url"], "at": _iso(self.now()), "bench": bid}
            reqs.append(entry)
            self._mark(b, "立项", "申请给 dsh 装插件", name)
            if self.root is not None:
                path = self.root / "plugin_requests.json"
                try:
                    allreq = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    allreq = []
                if name not in [r.get("name") for r in allreq]:
                    allreq.append(entry)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(
                        json.dumps(allreq, ensure_ascii=False, indent=1), encoding="utf-8"
                    )
            self._save(b)
        return b

    def decide(
        self, bid: str, action: str, requirements: list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        """做满轮数没过线时你来定：再跑一轮（more）、改要求再跑一轮（edit）、就这样（accept）。"""
        b = self.get(bid)
        if b is None:
            raise LookupError("没有这个工作台")
        if b.get("status") != "needs_you":
            raise ValueError("现在不需要你决定")
        if self.busy:
            raise RuntimeError("正在做另一个工作台，等它停下来再继续")
        if action == "accept":
            b["status"], b["accepted_as_is"], b["stuck"] = "done", True, None
            self._log(b, "你选了「就这样」，按现在的样子收下")
            self.build = b
            self._save()
            return b
        if action not in ("more", "edit"):
            raise ValueError("不认得这个操作")
        spec = load_spec(self.folder(bid))
        if action == "edit":
            if not requirements:
                raise ValueError("改要求要给出新的验收清单")
            self._edit_requirements(b, spec, requirements)
            self._log(b, f"你改了要求（{len(spec.requirements)} 条），再跑一轮")
        else:
            self._log(b, "你选了再跑一轮")
        self._start_job(b, lambda: self._more(b, spec))
        return b

    async def _more(self, b: dict[str, Any], spec: Spec) -> None:
        rng = random.Random(f"{b['seed']}-more-{len(b['rounds'])}")
        b["status"] = "reworking"
        await self._rework(b, spec, len(b["rounds"]), rng)
        await self._rounds(b, spec, rng, limit=1)

    def _edit_requirements(
        self, b: dict[str, Any], spec: Spec, requirements: list[dict[str, str]]
    ) -> None:
        """改验收清单里你的要求（r…，可改、删、加）；底线（b…）原样保留。写回 spec.json。"""
        old = {r["id"]: r for r in spec.requirements}
        keep = [r for r in spec.requirements if str(r["id"]).startswith("b")]
        mine: list[dict[str, Any]] = []
        for x in requirements:
            text = str(x.get("text") or "").strip()[:300]
            rid = str(x.get("id") or "")
            if not text or rid.startswith("b"):
                continue
            base = old.get(rid) if rid.startswith("r") else None
            item = (base or {"check": "", "windows": [], "source": "你的要求"}) | {"text": text}
            if base is None or base["text"] != text:
                # 你在签字时加的、改过的要求：记一句原话，追踪表能指回来
                v = add_voice(b, "签字", f"验收清单：{text}", _iso(self.now()))
                item["voices"] = [v["id"]] if v else []
            mine.append(item)
        mine = mine[: self.rules.max_requirements]
        renum: dict[str, str] = {}
        for i, r in enumerate(mine, 1):
            if r.get("id"):
                renum[r["id"]] = f"r{i}"
            r["id"] = f"r{i}"
        spec.requirements = mine + keep
        # 分段里的验收编号跟着改：删掉的拿掉，新加的放进最后一段
        placed: set[str] = set()
        for st in spec.stages:
            st["accept"] = [
                renum.get(a, a) for a in st["accept"] if a.startswith("b") or renum.get(a)
            ]
            placed |= set(st["accept"])
        if spec.stages:
            spec.stages[-1]["accept"][:0] = [r["id"] for r in mine if r["id"] not in placed]
            if b.get("proposal"):
                b["proposal"]["milestones"] = self._stages(spec)
        (self.folder(b["id"]) / "spec.json").write_text(
            json.dumps(spec.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if b.get("proposal"):
            b["proposal"]["requirements"] = spec.requirements

    # --- 项目室：访谈、方案书、签字、里程碑、变更单、交付、革新 --------------------------

    async def _interview(self, b: dict[str, Any]) -> None:
        """意图理解：先整理理想画像（五要素、隐含期待、假设，每条标把握），只问会改结构的取舍
        （最多 3 问，每问带推荐项和后果）。看过的素材先于结构（<look>）。"""
        b["status"] = "interviewing"
        record: dict[str, Any] = {}
        look = b.get("look") or {}
        prompt = self.render(
            "bench_interview",
            request=b["request"],
            look=(
                f"{look.get('summary') or ''}\n配色：{'、'.join(look.get('palette') or [])}\n"
                + "\n".join(
                    f"- {x['file']}：{x['what']}；借鉴：{x['borrow']}"
                    for x in look.get("items") or []
                    if x.get("what")
                )
            ).strip()
            or "（没有参考素材）",
            max_questions=str(self.rules.project.max_questions),
            skills=self._skills("interview", b=b),
        )
        chosen, result = await self._step(
            b,
            self.rules.plan_kind,
            prompt,
            self._rng(b, "interview"),
            record,
            role="访谈",
            task="整理理想画像、想该问你的取舍",
        )
        raw: dict[str, Any] = {}
        if chosen is not None and result is not None and result.ok:
            with contextlib.suppress(JSONOutputError):
                raw = extract_json_object(result.output)
        intent = intent_of(raw.get("intent"))
        qs = questions_of(raw.get("questions"), self.rules.project.max_questions)
        useful = intent.get("ideal") or any(intent.get(k) for k in FIVE_KEYS)
        b["intent"] = intent if useful else None
        b["interview"] = {"questions": qs, "answers": {}, "worker": record.get("worker")}
        b["brief_pre"] = self._brief(b)
        if b["intent"]:
            c = intent_counts(b["intent"])
            self._mark(
                b,
                "立项",
                "理想画像",
                f"{b['intent'].get('ideal') or ''}（确定 {c['确定']}、推断 {c['推断']}、"
                f"假设 {c['假设']}）",
            )
            b["brief"] = f"{b['brief_pre']}\n\n{INTENT_HEAD}\n" + intent_text(b["intent"])
        if not qs:
            self._log(b, "没有需要你取舍的地方，直接按理想画像往下做")
            return
        self._mark(b, "立项", "要你取舍", f"{len(qs)} 个问题")
        self._gate(b, "interview", "回答几个取舍（可以跳过，跳过按推荐）")

    def answer(self, bid: str, answers: dict[str, str]) -> dict[str, Any]:
        b = self._at_gate(bid, "interview")
        qs = b["interview"]["questions"]
        got = {q["id"]: str(answers.get(q["id"]) or "").strip()[:1000] for q in qs}
        b["interview"]["answers"] = got
        at = _iso(self.now())
        for q in qs:
            if got[q["id"]]:
                add_voice(b, "访谈", f"{q['q']} —— {got[q['id']]}", at, q["id"])
        if b.get("intent"):
            answer_intent(b["intent"], qs, got)
        lines = [f"- {q['q']}\n  {got[q['id']]}" for q in qs if got[q["id"]]]
        skipped = [
            f"- {q['q']}\n  （没答，按推荐：{q['recommend']}）"
            for q in qs
            if not got[q["id"]] and q.get("recommend")
        ]
        base = b.get("brief_pre") or b["request"]
        if b.get("intent"):
            base += f"\n\n{INTENT_HEAD}\n" + intent_text(b["intent"])
        if lines or skipped:
            base += "\n\n访谈时的取舍：\n" + "\n".join(lines + skipped)
        b["brief"] = base
        self._mark(
            b, "立项", "你回答了取舍", f"答了 {len(lines)}/{len(qs)} 题" if lines else "全部按推荐"
        )
        b["gate"] = None
        self._start_job(b, lambda: self._prepare(b))
        return b

    def _proposal(self, b: dict[str, Any], spec: Spec) -> None:
        """方案书：程序按规格和调研整理（验收清单、主视觉、窗口、里程碑、用到的项目），给你看、改、签字。"""
        milestones = self._stages(spec)
        r = b.get("research") or {}
        tech = [
            f"{s['repo']}（{s['why']}）" for s in b.get("sources") or [] if s["verdict"] == "adopt"
        ] + [f"学习卡 {c['repo']}：{c['what'][:80]}" for c in r.get("cards") or []]
        b["proposal"] = {
            "title": spec.title,
            "subtitle": spec.subtitle,
            "icon": spec.icon,
            "requirements": spec.requirements,
            "hero": spec.hero,
            "windows": [
                {
                    "id": w.id,
                    "title": w.title,
                    "sub": w.sub,
                    "icon": w.icon,
                    "span": w.span,
                    "purpose": w.purpose,
                    "data": w.data,
                    "actions": w.actions,
                    "parts": w.parts,
                }
                for w in spec.windows
            ],
            "links": self._links(spec),
            "shared": [f"{k}：{_short(v)}" for k, v in list(spec.shared.items())[:12]],
            "state": [f"{k}：{v}" for k, v in spec.state.items()],
            "design": self._design_board(b, spec),
            "milestones": milestones,
            "pool": spec.pool,
            "kind": spec.kind,
            "kind_label": (
                self.rules.skills.kinds[spec.kind].label
                if spec.kind in self.rules.skills.kinds
                else ""
            ),
            "tech": tech[:12],
            "plugins": [p["title"] for p in r.get("plugins") or []],
            "meeting": self._meeting_lines(b),
        }
        b["milestones"] = []
        folder = self.folder(b["id"])
        lines = [f"# 方案书：{spec.title}", "", f"你说的：{b['request']}", "", "## 验收清单"]
        lines += [f"- {x['id']} {x['text']}" for x in spec.requirements]
        lines += ["", "## 设计选型（推荐，签字时可改）"] + [f"- {t}" for t in design_lines(spec)]
        lines += ["", "## 窗口"]
        for w in spec.windows:
            lines.append(f"### {w.title}{'（主视觉）' if w.id == spec.hero else ''}")
            lines.append(w.purpose)
            if w.data:
                lines.append(f"- 数据：{w.data}")
            if w.actions:
                lines.append(f"- 能做的事：{'、'.join(w.actions)}")
            if w.parts:
                lines.append(f"- 零件：{'、'.join(w.parts)}")
        if b["proposal"]["links"]:
            lines += ["", "## 窗口之间怎么联动"] + [f"- {t}" for t in b["proposal"]["links"]]
        if b["proposal"]["shared"]:
            lines += ["", "## 共用信息（只放一份）"] + [f"- {t}" for t in b["proposal"]["shared"]]
        lines += ["", "## 分段计划（每段都能从头到尾走通）"]
        for m in milestones:
            lines.append(f"### 第 {m['n']} 段 · {m['title']}：{m['goal']}")
            if m["try"]:
                lines.append(f"- 你能试：{' → '.join(m['try'])}")
            lines.append(f"- 窗口：{'、'.join(m['titles'])}")
            if m["accept"]:
                lines.append(f"- 验收：{'、'.join(m['accept'])}")
            if m["skip"]:
                lines.append(f"- 这段不做：{m['skip']}")
            if m["risk"]:
                lines.append(f"- 风险：{m['risk']}")
        if spec.pool:
            lines += ["", "## 建议池（你没要，这次不做）"] + [f"- {t}" for t in spec.pool]
        if b["proposal"]["meeting"]:
            lines += ["", "## 方案会"] + [f"- {t}" for t in b["proposal"]["meeting"]]
        if tech:
            lines += ["", "## 借鉴的项目"] + [f"- {t}" for t in tech]
        (folder / "方案书.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._mark(
            b,
            "立项",
            "方案书",
            f"验收清单 {len(spec.requirements)} 条、{len(spec.windows)} 个窗口、"
            f"分 {len(milestones)} 段",
        )

    @staticmethod
    def _stages(spec: Spec) -> list[dict[str, Any]]:
        """方案书和界面用的分段：规格里的 stages 加上窗口标题（旧规格没有分段就整个算一段）。"""
        title = {w.id: w.title for w in spec.windows}
        stages = spec.stages or [
            {
                "n": 1,
                "title": "全部窗口",
                "goal": "写完全部窗口",
                "try": [],
                "windows": [w.id for w in spec.windows],
                "accept": [r["id"] for r in spec.requirements],
                "skip": "",
                "risk": "",
            }
        ]
        return [
            dict(m) | {"titles": [title[x] for x in m["windows"] if x in title]} for m in stages
        ]

    @staticmethod
    def _links(spec: Spec) -> list[str]:
        """窗口之间的联动，写成大白话：谁做了什么 → 谁跟着变。"""
        title = {w.id: w.title for w in spec.windows}
        out = []
        for e in spec.events:
            to = "、".join(title[x] for x in e["to"] if x in title)
            if not to:
                continue
            who = title.get(e["from"], "某个窗口")
            out.append(f"{who}{('：' + e['when']) if e['when'] else ''} → {to}跟着变")
        return out[:12]

    def _design_board(self, b: dict[str, Any], spec: Spec) -> dict[str, Any]:
        """选型台的材料：每项的选项（带推荐和理由）、每个版型下窗口怎么排、参考作品
        （方案里提的 + 调研里采纳的项目和学习卡）。预览在浏览器里按这些现场画，不再问助手。"""
        d = spec.design or {}
        refs = list(d.get("refs") or [])
        names = {x["name"] for x in refs}
        for s in b.get("sources") or []:
            if s["verdict"] == "adopt" and s["repo"] not in names:
                refs.append({"name": s["repo"], "borrow": s["why"][:120], "from": "调研"})
                names.add(s["repo"])
        for c in (b.get("research") or {}).get("cards") or []:
            if c["repo"] not in names:
                refs.append({"name": c["repo"], "borrow": c["what"][:120], "from": "学习卡"})
                names.add(c["repo"])
        spec.design = d | {"refs": refs[:12]}
        return {
            "dims": design_catalog(),
            "picks": d.get("picks") or {},
            "recommend": d.get("recommend") or d.get("picks") or {},
            "why": d.get("why") or {},
            "refs": refs[:12],
            "tagline": d.get("tagline") or "",
            "note": d.get("note") or "",
            "materials": b.get("materials") or [],
            "roles": ASSET_ROLES,
            "look": b.get("look"),
        }

    def _meeting_lines(self, b: dict[str, Any]) -> list[str]:
        """方案书里写清方案会上谁提了什么、为什么采纳或没采纳。"""
        m = b.get("meeting") or {}
        pitches = sorted(m.get("pitches") or [], key=lambda p: p["label"])
        if not pitches:
            return []
        why = {v["pick"]: v["why"] for v in m.get("votes") or [] if v["why"]}
        out = []
        for p in pitches:
            took = p["worker"] == m.get("lead")
            verdict = (
                f"采纳为主案（{p['votes']} 票）"
                + (f"：{why[p['label']]}" if p["label"] in why else "")
                if took
                else f"没采纳（{p['votes']} 票）"
            )
            out.append(f"方案 {p['label']}（{p['author']}）{p['idea']}——{verdict}")
        out += [f"从其他方案补进主案：{x}" for x in m.get("borrow") or []]
        return out

    def sign(
        self,
        bid: str,
        requirements: list[dict[str, str]] | None = None,
        note: str = "",
        design: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """签字开工：可以改、删、加验收清单里你的要求（底线 b… 不能删），写一句补充；
        选型台上点的版型、配色、材质……和每个窗口的宽窄、去掉哪些，一起写回规格。"""
        b = self._at_gate(bid, "sign")
        spec = load_spec(self.folder(bid))
        if design is not None:
            self._choose_design(b, spec, design)
        if requirements is not None:
            self._edit_requirements(b, spec, requirements)
        note = note.strip()[:1000]
        at = _iso(self.now())
        if note:
            b["brief"] = f"{self._brief(b)}\n\n签字时补充：{note}"
            add_voice(b, "签字", note, at)
        style = str(((design or {}).get("note")) or "").strip()
        if style:
            add_voice(b, "风格", style, at)
        self._mark(
            b,
            "立项",
            "你签字开工",
            (note or "没有补充") + f"；验收清单 {len(spec.requirements)} 条",
        )
        b["gate"] = None
        self._start_job(b, lambda: self._milestone(b, 1))
        return b

    def _choose_design(self, b: dict[str, Any], spec: Spec, choice: dict[str, Any]) -> None:
        drop = apply_design(spec, choice, b.get("materials") or [])
        folder = self.folder(b["id"])
        for a in spec.design["assets"]:
            (folder / "assets").mkdir(exist_ok=True)
            shutil.copyfile(folder / "materials" / Path(a["file"]).name, folder / a["file"])
        (folder / "spec.json").write_text(
            json.dumps(spec.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (folder / "index.html").write_text(shell_html(spec, b["id"]), encoding="utf-8")
        if drop:
            b["windows"] = [w for w in b["windows"] if w["id"] not in drop]
            b["proposal"]["milestones"] = self._stages(spec)
        b["proposal"]["requirements"] = spec.requirements
        b["proposal"]["design"]["picks"] = spec.design["picks"]
        lines = design_lines(spec)
        b["brief"] = f"{self._brief(b)}\n\n设计选型（你在选型台上定的）：" + "；".join(lines)
        self._mark(
            b,
            "立项",
            "你定了设计",
            "；".join(lines[:6]) + (f"；去掉了 {len(drop)} 个窗口" if drop else ""),
        )

    async def _milestone(self, b: dict[str, Any], n: int) -> None:
        """按分段做：这一段没写过的窗口新写，写过的往里加这一段的功能（交回作者，带「本段任务」）。
        不是最后一段就演示给你试；最后一段做完进入质检评审。"""
        spec = load_spec(self.folder(b["id"]))
        ms = b["proposal"]["milestones"]
        m = ms[n - 1]
        rng = self._rng(b, f"m{n}")
        b["status"] = "building"
        b["stage"] = n
        task = stage_task(m, spec.requirements, len(ms))
        b["stage_task"] = task  # 这一段里的自测返工、变更单返工也带上
        wids = m["windows"] or [w["id"] for w in b["windows"]]
        recs = [w for w in b["windows"] if w["id"] in wids]
        new = [w for w in recs if w["status"] == "waiting"]
        old = [w for w in recs if w["status"] != "waiting"]
        self._log(
            b,
            f"第 {n} 段「{m['title']}」：新写 {len(new)} 个窗口"
            + (f"，往 {len(old)} 个写好的窗口里加功能" if old else ""),
        )
        jobs = [self._write(b, spec, w, rng, stage=task) for w in new]
        if old:
            jobs.append(self._stage_rework(b, spec, old, task, rng))
        await asyncio.gather(*jobs)
        if n < len(ms):
            shots = await self._selftest(b, spec, rng, only=wids, out=f"m{n}-1")
            checks = await self._stage_checks(b, spec, ms[:n], rng)
            if checks.get("fixed"):
                shots = await self._selftest(b, spec, rng, only=wids, out=f"m{n}-2")
            b["milestones"].append(
                {"n": n, "title": m["title"], "demos": [shots], "checks": checks["results"]}
            )
            self._mark(b, "执行", f"第 {n} 段：{m['title']}", m["goal"])
            self._gate(b, f"m{n}", f"试用第 {n} 段「{m['title']}」，通过或提意见")
            return
        await self._selftest(b, spec, rng)
        b["stage_task"] = None
        await self._rounds(b, spec, rng)

    async def _stage_checks(
        self,
        b: dict[str, Any],
        spec: Spec,
        done: list[dict[str, Any]],
        rng: random.Random,
    ) -> dict[str, Any]:
        """分段演示前：把做过的各段里能自动检查的验收跑一遍。没做到或倒退的先交回负责的窗口改一次，
        改完再查（最多一次，不没完没了），结果随演示给你看。"""
        ids = {a for st in done for a in st["accept"]}
        report = Report(target=str(self.folder(b["id"]) / "index.html"))
        results = await self._probes(b, spec, report, ids)
        if not report.findings:
            return {"results": results, "fixed": False}
        await self._rework(
            b,
            spec,
            0,
            rng,
            rnd={"check": {"findings": [f.__dict__ for f in report.findings]}},
            only_flagged=True,
        )
        again = await self._probes(b, spec, Report(target=report.target), ids)
        return {"results": again, "fixed": True}

    async def _stage_rework(
        self,
        b: dict[str, Any],
        spec: Spec,
        recs: list[dict[str, Any]],
        task: str,
        rng: random.Random,
    ) -> None:
        """往写好的窗口里加这一段的功能：交回作者，问题清单里写「本段任务」。"""
        rnd = {
            "check": {
                "findings": [
                    {
                        "check": "本段任务",
                        "severity": "block",
                        "where": "分段",
                        "detail": task.replace("\n", "；"),
                        "windows": [w["id"] for w in recs],
                    }
                ]
            }
        }
        await self._rework(b, spec, 0, rng, rnd=rnd, only_flagged=True, stage=task)

    def review(
        self,
        bid: str,
        decision: str,
        comments: list[dict[str, str]] | None = None,
        *,
        auto: bool = False,
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
            if not auto:  # 你点了通过：改完待你看的变更单才算真的改好了
                for c in b.get("changes") or []:
                    if c["status"] == "done":
                        c["status"] = "accepted"
            if kind == "accept":
                b["status"] = "delivered"
                self._mark(b, "交付", "你验收通过", "")
                self._log(b, "你验收通过，项目交付")
                self.build = b
                b["finished_at"] = _iso(self.now())
                self._save()
                return b
            n = int(kind[1:])
            # 防倒退：这一段（和之前各段）自动检查做到的验收记成基线，后面的段不许让它们变红
            m = next((x for x in b.get("milestones") or [] if x["n"] == n), {})
            ok = {x["id"] for x in m.get("checks") or [] if x.get("ok")}
            b["baseline"] = sorted(set(b.get("baseline") or []) | ok)
            self._mark(
                b, "执行", f"第 {n} 段你点了通过", f"基线 {len(b['baseline'])} 条" if ok else ""
            )
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
        for c in items:
            add_voice(b, "变更单", c["text"], c["at"], c["id"])
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
            # 真改了才算：这次返工有记录、助手说成了，而且至少一个窗口文件真的变了
            real = [w for w in done if (w["rework"][-1] or {}).get("changed")]
            ok = bool(real) and all(w["status"] == "done" for w in done)
            fake = bool(done) and not real
            c["status"] = "done" if ok else "failed"
            if ok:
                c["reply"] = (real[0]["summary"] or "")[-300:]
            elif fake:
                c["reply"] = FAKE_REDO
            else:
                c["reply"] = "这次没改成（没有助手能接）"
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
        self._gate(b, kind, f"再试用第 {n} 段「{m['title']}」，通过或接着提意见")

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
                b,
                self.rules.plan_kind,
                prompt,
                self._rng(b, "evolve"),
                record,
                role="革新",
                task="写下一版提案",
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
                self.review(b["id"], "pass", auto=True)

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
            design=design_brief(m["themes"]),
            voices=voices_text(b) or f"- v1（开工）{b['request']}",
            max_stages=str(self.rules.project.max_stages),
            skills=self._skills("plan", b=b),
            kinds="\n".join(f"- {k}：{v.label}" for k, v in self.rules.skills.kinds.items())
            or "- other：通用",
        )
        chosen, result = await self._step(
            b, self.rules.plan_kind, prompt, rng, record, role="拆需求", task="把一句话拆成规格"
        )
        if chosen is None or result is None or not result.ok:
            raise RuntimeError("没有人能拆需求（都没装、在冷却或今天次数用完了）")
        try:
            raw = extract_json_object(result.output)
            spec = normalize(
                raw,
                max_windows=self.rules.max_windows,
                baseline=self.rules.baseline,
                max_requirements=self.rules.max_requirements,
                max_stages=self.rules.project.max_stages,
                kinds=set(self.rules.skills.kinds),
            )
        except (JSONOutputError, SpecError) as exc:
            raise RuntimeError(f"拆需求的结果看不懂：{exc}") from None
        link_voices(spec.requirements, raw.get("requirements"), b.get("voices") or [])
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
        chosen, result = await self._step(
            b, rules.kind, prompt, rng, record, role="调研", task="给搜索词、查资讯和视频"
        )
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
        known = self.whitelist() if self.whitelist else {}
        for p in report["plugins"]:
            p["whitelisted"] = known.get(p["title"], "")
        report["cards"] = await self._learn(b, report["github"], rng, report["plugins"])
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
        self,
        b: dict[str, Any],
        github: list[dict[str, Any]],
        rng: random.Random,
        plugins: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """挑分最高的几个项目，源码只读下载到 research/learn/，各交给一位助手读完写学习卡；
        dsh 插件集市里最对口的插件也读一读（给 dsh 和别的助手学它的做法，装不装另说）。"""
        n, np_ = self.rules.research.learn, self.rules.research.learn_plugins
        if self.scout is None:
            return []
        # 调研报告里已经按「命中的关键词多、星多」排好，照这个顺序挑，不再只看星数
        picks = [g for g in github if g["verdict"] in ("adopt", "idea")][:n]
        seen = {g["title"] for g in picks}
        for p in sorted(plugins or [], key=lambda p: -p["stars"]):
            if np_ <= 0:
                break
            # 只读 GitHub 上的（owner/name）；许可证没核对过，一律只借思路
            if p["title"] in seen or not re.fullmatch(r"[\w.-]+/[\w.-]+", p["title"]):
                continue
            picks.append(p | {"verdict": "idea", "license": p.get("license") or ""})
            seen.add(p["title"])
            np_ -= 1
        if not picks:
            return []

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
            chosen, result = await self._step(
                b,
                self.rules.research.kind,
                prompt,
                rng,
                record,
                role="调研",
                task=f"读 {g['title']} 写学习卡",
            )
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
                "plugin": g.get("kind") == "dsh",
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
        m = b.get("meeting") or {}
        lead = next((p for p in m.get("pitches") or [] if p["worker"] == m.get("lead")), None)
        if lead:
            lines.append(
                f"- 方案会定下的主案（方案 {lead['label']}，大家照这个做）：{lead['idea']}"
                + (f"\n  · 主视觉：{lead['hero']}" if lead["hero"] else "")
                + "".join(f"\n  · {x}" for x in lead["plan"])
                + "".join(f"\n  · 防：{x}" for x in lead["risks"])
                + "".join(f"\n  · 这一版先不做：{x}" for x in lead["cut"])
                + "".join(f"\n  · 补进来：{x}" for x in m.get("borrow") or [])
            )
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
        if self.desk is not None:
            kind = ((b.get("proposal") or {}).get("kind")) or "other"
            try:
                tools = self.desk.text_for(kind)
            except Exception:  # noqa: BLE001 - 资料台坏了不影响写窗口
                tools = ""
            if tools:
                lines.append(
                    "- 能力地图里适合这类工作台的现成工具（要用就按许可证接，用不上别硬塞）："
                )
                lines += ["  " + x for x in tools.splitlines()]
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

    def _skills(self, step: str, kind: str = "", b: dict[str, Any] | None = None) -> str:
        """这一步要带的地基技能：短版常驻、长版只在这一步给；写窗口、返工、评审再加上工作台类型的
        品类技能（长版）。技能文字从注入的 skill 函数取（短版, 长版），没有就空。"""
        cfg = self.rules.skills
        if not cfg.enabled or self.skill is None:
            return "（这一步没有额外的技能说明）"
        st = cfg.steps.get(step)
        long = list(st.long) if st else []
        if step in ("window", "rework", "review") and kind in cfg.kinds:
            long += cfg.kinds[kind].skills
        if b is not None and b.get("from_product"):
            long += cfg.product
        short = [n for n in (st.short if st else []) if n not in long]
        parts: list[str] = []
        for name in dict.fromkeys(short + long):
            try:
                brief, full = self.skill(name)
            except Exception:  # noqa: BLE001 - 某个技能读不到不挡流程，只少一段说明
                continue
            parts.append(full if name in long else brief)
        return "\n\n".join(parts) or "（这一步没有额外的技能说明）"

    def _samples_text(self, b: dict[str, Any]) -> str:
        return samples_text(b.get("samples") or [])

    def _kit_text(self) -> str:
        return json.dumps(manifest(), ensure_ascii=False, indent=1)

    async def _write(
        self,
        b: dict[str, Any],
        spec: Spec,
        rec: dict[str, Any],
        rng: random.Random,
        stage: str = "",
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
            samples=self._samples_text(b),
            stage=stage or ONE_GO,
            skills=self._skills("window", spec.kind, b),
        )
        folder = self.folder(b["id"])
        before = fingerprint(folder, [w.file])
        chosen, result = await self._step(
            b, self.rules.window_kind, prompt, rng, rec, role="实施", task=f"写窗口「{w.title}」"
        )
        rec["author"] = chosen.id if chosen else None
        changed = fingerprint(folder, [w.file]) not in ("", before)
        if chosen is None or result is None or not result.ok:
            rec["status"] = "failed"
            rec["summary"] = "没有人能写这个窗口" if result is None else "写的时候出错了"
        elif not changed:
            # 假完成：助手说写好了，窗口文件却没写出来（或一个字没动）
            rec["status"] = "failed"
            rec["summary"] = FAKE_DONE
        else:
            rec["status"] = "done"
            rec["summary"] = result.output.strip()[-2000:] or "（没有说明）"
        self._run_card(rec, "写", chosen, result, changed)
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
        report.findings += lint_data(folder, [w["id"] for w in b["windows"]], spec.shared)
        rnd["probes"] = await self._probes(b, spec, report)
        rnd["check"] = report.to_dict()
        b["last_shot"] = desk_shot([os.path.relpath(x, folder) for x in report.shots])
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
                skills=self._skills("review", spec.kind, b),
            )
            chosen, result = await self._step(
                b,
                self.rules.review_kind,
                prompt,
                rng,
                rec,
                exclude=(author,),
                role="评审",
                task=f"评 {'、'.join(wids)}",
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
        # 程序真的去填、去点没做到的：评审说做到了也不算
        failed = {x["id"]: x["why"] for x in rnd["probes"] if x["ok"] is False}
        for r in rnd["requirements"]:
            if r["id"] in failed:
                r["met"] = False
                r["why"] = [f"自动检查：{failed[r['id']]}", *r["why"]][:3]
        met = all(r["met"] for r in rnd["requirements"])
        rnd["passed"] = complete and met and passes(report.passed, rnd["scores"], self.rules)
        team = self.rules.team
        if team.enabled and team.attack:
            await self._attack(b, spec, rnd, report.summary(), shots, authors, rng)
            high = [x for x in rnd["attack"].get("bugs", []) if x["severity"] == "high"]
            if high and team.block_high:
                rnd["passed"] = False
                self._log(b, f"第 {n} 轮对抗：找出 {len(high)} 个严重问题，修完才算过线")
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

    async def _probes(
        self,
        b: dict[str, Any],
        spec: Spec,
        report: Report,
        ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """可执行的验收：拆需求时写了 probe 的要求，程序打开成品照着填、选、点，
        看该出现的字出现没有。没做到的记成必须修的质检问题，交给负责的窗口。
        ``ids``：只查这几条（分段演示时只查做过的段）。之前的段已经做到、现在又坏了的记成「倒退」。"""
        probes = [
            {"id": r["id"], **r["probe"]}
            for r in spec.requirements
            if r.get("probe") and (ids is None or r["id"] in ids)
        ]
        if not probes or self.probe is None:
            return []
        try:
            results = await asyncio.to_thread(
                self.probe, str(self.folder(b["id"]) / "index.html"), probes, allow=self._allow()
            )
        except QualityError as exc:
            self._log(b, f"验收自动检查没法进行：{exc}")
            return []
        by = {r["id"]: r for r in spec.requirements}
        kept = set(b.get("baseline") or [])
        for x in results:
            if x["ok"] is False:
                r = by[x["id"]]
                back = x["id"] in kept
                report.findings.append(
                    Finding(
                        "倒退" if back else "验收自动检查没过",
                        "block",
                        "自动检查",
                        f"{r['id']} {r['text']}："
                        + ("前面的段已经做到了，现在又坏了（别把做好的改坏）；" if back else "")
                        + x["why"],
                        list(r["windows"]),
                    )
                )
        back = [x["id"] for x in results if x["ok"] is False and x["id"] in kept]
        if back:
            self._log(b, f"防倒退：{'、'.join(back)} 前面做到了，现在坏了，交回负责的窗口")
        ok = sum(1 for x in results if x["ok"])
        skipped = sum(1 for x in results if x["ok"] is None)
        self._log(
            b,
            f"验收自动检查：{ok}/{len(results)} 条做到"
            + (f"，{skipped} 条要现场生成、交给评审看" if skipped else ""),
        )
        return results

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
        report.findings += lint_windows(folder, scope) + lint_data(folder, scope, spec.shared)
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
        b["last_shot"] = desk_shot(shots)
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
            if f.get("where") == "分段":
                lines.insert(0, f"- [本段任务] {f['detail']}")
                continue
            if f.get("where") == "甲方":
                # 用户自己提的变更单：标「用户意见」，返工提示词要求优先照办
                lines.insert(0, f"- [用户意见·必须照办] {f['check']}：{f['detail']}")
                continue
            lines.append(f"- [自动质检·{level}·{f['where']}] {f['check']}：{f['detail']}")
        for x in (rnd.get("attack") or {}).get("bugs", []):
            if x["window"] and x["window"] != wid:
                continue
            level = {"high": "必须修", "mid": "该修", "low": "顺手修"}[x["severity"]]
            lines.append(
                f"- [对抗·{level}] {x['problem']}"
                + (f"（复现：{x['steps']}）" if x["steps"] else "")
                + (f"\n  建议：{x['fix']}" if x["fix"] else "")
            )
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
        stage: str = "",
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
            if problems and b.get("last_shot"):
                # 写手自看：改之前整页的样子（电脑·浅色），打开看一眼自己的窗口，改完再对照
                problems += (
                    f"\n- [自看] 改之前的截图：{b['last_shot']}（你的窗口是 "
                    f'data-window="{rec["id"]}" 那一格；改完对照着看有没有变好、有没有改坏别处）'
                )
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
                samples=self._samples_text(b),
                stage=stage or b.get("stage_task") or ALL_DONE,
                skills=self._skills("rework", spec.kind, b),
            )
            jobs.append(self._redo(b, rec, prompt, rng, n))
        await asyncio.gather(*jobs)

    def _run_card(
        self, rec: dict[str, Any], kind: str, chosen: Any, result: Any, changed: bool
    ) -> None:
        """一张卡记每一次真实会话：谁做的、助手说成没成、文件有没有真的改动。"""
        ok = result is not None and result.ok
        rec.setdefault("runs", []).append(
            {
                "at": _iso(self.now()),
                "kind": kind,
                "worker": chosen.id if chosen is not None else None,
                "ok": ok,
                "changed": changed,
                "fake": ok and not changed,
            }
        )
        del rec["runs"][:-RUNS_KEPT]

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
        folder = self.folder(b["id"])
        files = [f"windows/{rec['id']}.js", f"data/{rec['id']}.json"]  # 改代码或改数据都算动了
        before = fingerprint(folder, files)
        chosen, result = await self._step(
            b,
            self.rules.window_kind,
            prompt,
            rng,
            record,
            preset=preset,
            role="实施",
            task=f"返工「{rec['title']}」",
        )
        changed = fingerprint(folder, files) not in ("", before)
        record["changed"] = changed
        ok = chosen is not None and result is not None and result.ok
        if ok and changed:
            rec["author"], rec["status"] = chosen.id, "done"
            rec["summary"] = result.output.strip()[-2000:] or rec["summary"]
        elif ok:
            # 假完成：说改好了，文件一个字没动。不算数，也不顶掉原来的说明
            record["fake"] = True
            self._log(b, f"「{rec['title']}」{FAKE_REDO}")
        self._run_card(rec, "返工", chosen, result, changed)
        done = "完成" if rec["status"] == "done" and changed else "没成"
        stage = f"第 {n} 轮返工" if n else "自测后返工"
        self._log(b, f"{stage}「{rec['title']}」{done}")
