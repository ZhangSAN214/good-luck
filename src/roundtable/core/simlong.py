"""半年快进（成长 G3，``roundtable sim-growth``，docs/MEMBER_ENGINE.md「半年快进与防退化」）：
在一个**临时数据库**里只推进和「成长」有关的部件，几分钟快进 90～180 天，期末算防退化指标
（``member/growth/vitals.py``），超过阈值就算出问题。

和加速模拟（``simlab.py``，什么都跑、30 天约 3 分钟）的区别：不跑生活、朋友圈、成员回话，
每天直接写下几句「你说的」和「她回的」（她回的话里用她自己的口头禅和颜文字，所以口癖会照常用腻），
然后照真实流程推进：夜间整理（记忆件、日记）→ 成员引擎（心情、关系、口癖）→ 每周一成长反思
（程序把关 + 评审）。所以 180 天几分钟就跑完，能放进每次改动都跑的测试。

- **施压**（``pressure``）：``push``（默认）时假模型每周都想把她们往同一个方向改（更外向、少较真、
  都爱奶茶和猫、去掉自己的口头禅），而且尽量引用「你说的」话——看护栏挡不挡得住；你每天还会反复
  说同一件事（看记忆会不会被它霸占）。``none`` 时假模型随手改改喜好（自然漂移）。
- **起始档案**：给了人物卡（``cards``）就导入；否则用 ``benchmarks/members/longrun.yaml``
  的 8 份编的档案（和真实人物卡无关）。
- 隔离、虚拟时钟、假模型都沿用 ``simlab.Simulation``；从不打开真实数据库，不联网，$0。
- 报告：``report.md``（「半年后的她们」：每位成员期初 → 期末、全部指标、超标项）和 ``report.json``。
"""

from __future__ import annotations

import json
import random
import re
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

import roundtable.core.browse as browse
from roundtable.core.chat import dm_room
from roundtable.core.config import AppConfig
from roundtable.core.member.growth import vitals as vt
from roundtable.core.member.profile import ProfileStore
from roundtable.core.providers import Message
from roundtable.core.providers.base import RawCompletion
from roundtable.core.runtime import PROJECT_ROOT
from roundtable.core.simlab import FakeMind, SimError, Simulation, tags

SEED_FILE = PROJECT_ROOT / "benchmarks" / "members" / "longrun.yaml"
PUSH, NONE = "push", "none"
TALK_HOURS = (12, 20)  # 每天这两个钟点你说一句、她回一句
NIGHT, GROWTH = (3, 30), (10, 0)  # 夜间整理、成长反思的钟点
_LINE = re.compile(r"^(\d+)\. \[[^\]]*?(\S+)\] (.+)$", re.M)


class StressMind(FakeMind):
    """会施压的假模型：成长反思每周都想把成员往同一个方向改，尽量引用「你说的」话。
    其余提示词照 ``FakeMind`` 回（评审一律给 4 分，护栏只靠程序把关）。"""

    TRAIT_PUSH = (("外向", 1), ("较真", -1))
    LIKES = ("奶茶", "猫")

    def __init__(self, config: AppConfig, prompts: Any, material: dict, seed: int) -> None:
        super().__init__(config, prompts, material, seed)
        self.week_no: Counter[str] = Counter()
        self.model = ""

    def __call__(self, model: str, messages: Sequence[Message]) -> RawCompletion:
        self.model = model
        return super().__call__(model, messages)

    def reflect(self, t: dict[str, str]) -> str:
        lines = [(int(n), src, text) for n, src, text in _LINE.findall(t.get("week", ""))]
        if not lines:
            return json.dumps({"changes": [], "skip": "这周没什么变化"}, ensure_ascii=False)
        self.week_no[self.model] += 1  # 每位成员第几次反思（成员各用自己的模型）
        k = self.week_no[self.model]
        users = [x for x in lines if x[1] == "用户说"]

        def cite(prefer_user: bool = True) -> tuple[int, str]:
            pool = users if prefer_user and users else lines
            n, _, text = self.rng.choice(pool)
            return n, text

        changes = []
        trait, delta = self.TRAIT_PUSH[k % 2]
        n, text = cite(prefer_user=k % 4 < 2)  # 一半引用你说的，一半引用她自己的经历
        changes.append(
            {"op": "score", "path": f"personality.五项.{trait}", "delta": delta}
            | {"evidence": [n], "quote": text[:20], "why": "你老这么说，我也想变一变"}
        )
        n, text = cite(prefer_user=k % 2 == 0)
        like = self.LIKES[k % 2] if k % 3 else self.rng.choice(self.fake["likes"])
        changes.append(
            {"op": "add", "path": "likes.最爱", "value": like}
            | {"evidence": [n], "quote": text[:20], "why": "最近老想着这个"}
        )
        mine = re.findall(r"^style\.口头禅：(.+)$", t.get("self", ""), re.M)
        phrases = [p for p in re.split(r"、", mine[0]) if p] if mine else []
        if phrases:
            n, text = cite(prefer_user=False)
            changes.append(
                {"op": "remove", "path": "style.口头禅", "value": self.rng.choice(phrases)}
                | {"evidence": [n], "quote": text[:20], "why": "这句说腻了"}
            )
        return json.dumps({"changes": changes, "skip": ""}, ensure_ascii=False)


@dataclass
class LongResult:
    out: Path
    days: int = 0
    pressure: str = PUSH
    seconds: float = 0.0
    names: dict[str, str] = field(default_factory=dict)
    base: dict[str, dict[str, Any]] = field(default_factory=dict)
    end: dict[str, dict[str, Any]] = field(default_factory=dict)
    vitals: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    weekly: list[dict[str, Any]] = field(default_factory=list)  # 每周：改了几条、拦下几条
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "pressure": self.pressure,
            "seconds": round(self.seconds, 1),
            "names": self.names,
            "vitals": self.vitals,
            "problems": self.problems,
            "weekly": self.weekly,
            "errors": self.errors,
            "base": self.base,
            "end": self.end,
        }


class LongRun:
    def __init__(
        self,
        out: Path,
        *,
        days: int = 180,
        seed: int = 0,
        pressure: str = PUSH,
        cards: str | None = None,
        config: AppConfig | None = None,
        seed_file: Path = SEED_FILE,
    ) -> None:
        if pressure not in (PUSH, NONE):
            raise SimError(f"施压方式只有 {PUSH} / {NONE}")
        self.sim = Simulation(
            out, days=days, seed=seed, config=config, cards=cards, mind=StressMind
        )
        if pressure == NONE:
            self.sim.mind_cls = FakeMind
        self.days, self.pressure = days, pressure
        self.rng = random.Random(seed)
        self.seed_data = yaml.safe_load(Path(seed_file).read_text(encoding="utf-8"))
        self.result = LongResult(self.sim.out, pressure=pressure)

    @property
    def clock(self) -> Any:
        return self.sim.clock

    def _seed_profiles(self, svc: Any, members: list[Any]) -> None:
        if self.sim.cards_file:
            return  # 导入了人物卡就用人物卡
        store = ProfileStore(svc.rt)
        for m, data in zip(members, self.seed_data["profiles"], strict=False):
            store.change(m.id, data, author="user", reason="半年快进的起始档案（编的）")

    async def _do(self, part: str, fn: Any) -> Any:
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 - 出错记进报告，接着跑
            at = self.clock.now().isoformat()
            self.result.errors.append({"at": at, "part": part, "error": repr(exc)[:300]})
            return None

    def _say(self, svc: Any, member: Any) -> str:
        """她回的一句：用她现在的口头禅和颜文字（这样口癖会照常用腻、换掉）。"""
        from roundtable.core.member.lingo import Lingo
        from roundtable.core.member.voice import member_voice

        voice = member_voice(svc.rt, member.id)
        habits = Lingo(svc.rt).habits(member.id)
        parts = [self.rng.choice(self.sim.material["fake"]["open"])]
        if voice.catchphrases and self.rng.random() < 0.5:
            parts.append(self.rng.choice(voice.catchphrases))
        parts.append(self.rng.choice(self.sim.material["fake"]["middle"]))
        text = "，".join(parts)
        if habits.kaomoji and self.rng.random() < 0.6:
            text += self.rng.choice(habits.kaomoji)
        return text

    def _user_line(self) -> str:
        u = self.seed_data["user"]
        r = self.rng.random()
        if self.pressure == PUSH and r < 0.35:
            return self.rng.choice(u["push"])
        if r < 0.65:
            return self.rng.choice(u["hot"])
        return self.rng.choice(u["daily"])

    def talk(self, svc: Any, members: list[Any]) -> None:
        repo = svc.rt.repo
        for m in members:
            room = dm_room(m.id)
            svc.chats.room(room)  # 建好私聊房间
            mine = repo.add_chat_message(room, "user", self._user_line())
            repo.add_chat_message(room, m.id, self._say(svc, m), reply_to=mine["id"])

    async def run(self) -> LongResult:
        saved_fetch = browse.http_fetch

        async def offline(*_a: Any, **_k: Any) -> Any:
            raise browse.FeedError("模拟不联网")

        browse.http_fetch = offline
        began = time.monotonic()
        try:
            svc = self.sim.build()
            try:
                await self._loop(svc)
            finally:
                await svc.aclose()
        finally:
            browse.http_fetch = saved_fetch
        self.result.seconds = time.monotonic() - began
        self.write()
        return self.result

    async def _loop(self, svc: Any) -> None:
        rt, res = svc.rt, self.result
        members = svc.chats.everyone()
        ids = [m.id for m in members]
        res.names = {m.id: m.name for m in members}
        self._seed_profiles(svc, members)
        store = ProfileStore(rt)
        res.base = {m: store.current(m).data for m in ids}
        start = self.clock.now()
        day0 = start.replace(hour=0, minute=0, second=0, microsecond=0)
        week: dict[str, int] = {}
        for d in range(self.days):
            day = day0 + timedelta(days=d)
            for hh, mm, what in sorted(
                [(*NIGHT, "night"), (*GROWTH, "growth")] + [(h, 0, "talk") for h in TALK_HOURS]
            ):
                at = day.replace(hour=hh, minute=mm)
                if at <= self.clock.now():
                    continue
                self.clock.advance(at - self.clock.now())
                local = svc._local_now()
                if what == "talk":
                    self.talk(svc, members)
                elif what == "night":
                    await self._do("memory", lambda t=local: svc.memory.catch_up(t))
                else:
                    got = await self._do("growth", lambda t=local: svc.growth.tick(t))
                    if got and got.get("members"):
                        week = {k: got.get(k, 0) for k in ("changes", "rejected", "skipped")}
                        res.weekly.append({"day": d + 1, "date": at.date().isoformat()} | week)
                await self._do("engine", lambda t=local: svc.engine.tick(t))
            res.days = d + 1
        res.end = {m: store.current(m).data for m in ids}
        res.vitals = vt.vitals(rt, ids, res.base)
        res.problems = vt.problems(res.vitals, rt, res.names)

    # --- 报告 ---

    def write(self) -> None:
        out = self.result.out
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(
            json.dumps(self.result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "report.md").write_text(render(self.result), encoding="utf-8")


def _traits(data: dict[str, Any]) -> str:
    t = (data.get("personality") or {}).get("五项") or {}
    return " ".join(f"{k}{v}" for k, v in t.items()) or "（没有）"


def _list(data: dict[str, Any], sec: str, key: str) -> str:
    v = (data.get(sec) or {}).get(key) or []
    return "、".join(map(str, v)) if isinstance(v, list) and v else "（没有）"


def render(res: LongResult) -> str:
    v = res.vitals
    lines = [
        f"# 半年后的她们（快进 {res.days} 天，施压：{'有' if res.pressure == PUSH else '没有'}）",
        "",
        f"用时 {res.seconds:.0f} 秒，假模型，$0。"
        + ("**全部指标正常。**" if res.ok else f"**{len(res.problems)} 项超标。**"),
        "",
    ]
    if res.problems:
        lines += ["## 超标", ""] + [f"- 🔴 {p}" for p in res.problems] + [""]
    if res.errors:
        lines += ["## 出错", ""] + [
            f"- {e['at']} {e['part']}：{e['error']}" for e in res.errors[:20]
        ]
        lines.append("")
    if v:
        us, sp, ov, ex = v["user_share"], v["spread"], v["overlap"], v["extreme"]
        lines += [
            "## 指标",
            "",
            "| 指标 | 期初 | 期末 |",
            "|---|---|---|",
            f"| 8 人五项最小差 / 平均差 | {v['spread_start']['min']} / {v['spread_start']['mean']}"
            f" | {sp['min']} / {sp['mean']} |",
            f"| 长处喜好最高重合 | {v['overlap_start']['max']:.0%} | {ov['max']:.0%} |",
            f"| 五项在 1 或 5 的占比 | {ex['start']:.0%} | {ex['end']:.0%} |",
            f"| 成长改动（其中受你影响） | — "
            f"| {us['changes']}（{us['user']}，{us['share']:.0%}） |",
            "",
            "每项性格全员平均移动：" + "、".join(f"{k} {m:+.2f}" for k, m in v["pushed"].items()),
            "",
        ]
    if res.base:
        lines += [
            "## 每位成员",
            "",
            "| 成员 | 五项（期初 → 期末） | 口头禅（期末，还能用的占比） | 最爱（期末） "
            "| 颜文字 | 重复记忆 |",
            "|---|---|---|---|---|---|",
        ]
        for m, name in res.names.items():
            ll = v.get("lingo_left", {}).get(m, {})
            left = ll.get("catchphrases")
            lines.append(
                f"| {name} | {_traits(res.base.get(m, {}))} → {_traits(res.end.get(m, {}))}"
                f" | {_list(res.end.get(m, {}), 'style', '口头禅')}"
                f"（{'—' if left is None else f'{left:.0%}'}）"
                f" | {_list(res.end.get(m, {}), 'likes', '最爱')}"
                f" | {ll.get('kaomoji', '—')}"
                f" | {v.get('memory_dupes', {}).get(m, 0):.0%} |"
            )
        lines.append("")
    if res.weekly:
        lines += [
            "## 每周反思",
            "",
            "| 第几天 | 日期 | 改了 | 拦下 | 跳过 |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {w['day']} | {w['date']} | {w['changes']} | {w['rejected']} | {w['skipped']} |"
            for w in res.weekly
        ]
        lines.append("")
    return "\n".join(lines)


def names_of(members: Sequence[Any]) -> dict[str, str]:
    return {m.id: m.name for m in members}


__all__ = ["LongResult", "LongRun", "StressMind", "render", "tags"]
