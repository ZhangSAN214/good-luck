"""成员系统加速时间模拟（docs/SIM.md）：在一个**临时数据库**里把时间快进几天到几个月，
看成员的生活、心情精力、记忆、关系、关于你、成长、朋友圈是否正常运转、越来越像真人。

- 隔离：数据库、上传、备份都放在 ``out`` 目录（默认不在 data/ 下，放在 data/ 下直接报错）；
  订阅源不联网；从不打开用户的真实数据库。
- 快进：一个虚拟时钟同时管预算（``rt.budget.now``）、数据库时间戳（``repo.clock``）
  和服务的本地时间；
  每一步按生活的 ``tick_minutes`` 往前拨，依次推进生活 → 日历 → 成员引擎 → 成长（同服务的
  ``life_tick``，但每个部件单独捕获异常并记进报告，而不是悄悄吞掉）。
- 模型：默认假模型（$0，按提示词种类回合规的 JSON 或口语短句，token 按中文字数估，
  路由按配置价格算「如果是真模型大约多少钱」）；``real=True`` 时用 .env 里的真实 key，并受
  ``max_usd`` 硬上限约束（超过立刻停）。
- 模拟用户：按 ``benchmarks/members/sim.yaml`` 的比例在醒着的时段给成员私聊或在生活群说话。
- 报告：每天一行快照 + 期末每位成员一行 + 运行检查（通过 / 不通过）+ 「AI 味」程序指标，
  写成 ``report.md`` 和 ``report.json``。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import statistics
import time
import traceback
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

import roundtable.core.browse as browse
from roundtable.core.chat import ROOM, dm_room
from roundtable.core.config import AppConfig, load_config
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import FakeProvider, Message
from roundtable.core.providers.base import RawCompletion
from roundtable.core.runtime import DEFAULT_DB, PROJECT_ROOT, Runtime
from roundtable.core.storage.repository import iso

log = logging.getLogger(__name__)

SIM_FILE = PROJECT_ROOT / "benchmarks" / "members" / "sim.yaml"
USER_HOURS = range(9, 24)  # 模拟用户醒着的钟点（本地时间）
_CJK = re.compile(r"[　-鿿＀-￯]")
_TAG = re.compile(r"<(\w+)>\n?(.*?)\n?</\1>", re.S)
_PLACEHOLDER = re.compile(r"\{\{\s*\w+\s*\}\}")


class SimError(ValueError):
    pass


def tokens_of(text: str) -> int:
    """粗估 token：中文每字约 1 个，其余每 4 个字符约 1 个。"""
    cjk = len(_CJK.findall(text))
    return max(1, cjk + (len(text) - cjk) // 4)


# --- 虚拟时钟 ---------------------------------------------------------------------


class SimClock:
    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise SimError("模拟开始时间必须带时区")
        self.t = start

    def now(self) -> datetime:
        return self.t

    def advance(self, delta: timedelta) -> datetime:
        self.t += delta
        return self.t


# --- 假模型 -----------------------------------------------------------------------


def signatures(config: AppConfig, prompts: PromptLibrary) -> list[tuple[str, list[str]]]:
    """每种提示词系统提示里的固定文字片段当「指纹」；片段越多越长的排在前面（先比更具体的）。"""
    out = []
    for role, version in config.roundtable.prompts.items():
        try:
            system = prompts.get(role, version).system
        except Exception:  # noqa: BLE001 - 缺文件的提示词不参与识别
            continue
        pieces = [p.strip() for p in _PLACEHOLDER.split(system) for p in p.split("\n")]
        pieces = [p for p in pieces if len(p) >= 6]
        if pieces:
            out.append((role, pieces))
    return sorted(out, key=lambda x: -sum(map(len, x[1])))


def tags(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _TAG.finditer(text)}


def _items(voice: str, label: str) -> list[str]:
    """说话习惯里某一行（「语气词：嗯、诶」）的各项。"""
    m = re.search(rf"^{re.escape(label)}：(.+)$", voice, re.M)
    return [x.strip() for x in re.split(r"[、，,]", m.group(1)) if x.strip()] if m else []


class FakeMind:
    """假模型模式下替所有成员回话：认出是哪种提示词，回一个结构合规的结果。"""

    def __init__(self, config: AppConfig, prompts: PromptLibrary, material: dict, seed: int):
        self.sigs = signatures(config, prompts)
        self.fake = material["fake"]
        self.rng = random.Random(seed)
        self.roles: Counter[str] = Counter()

    def identify(self, messages: Sequence[Message]) -> str:
        system = messages[0].content if messages else ""
        return next((role for role, sig in self.sigs if all(p in system for p in sig)), "unknown")

    def say(self, user: str = "") -> str:
        """聊天类的回话。有意图卡（<intent>）和说话习惯（<voice>）时照它们拼：想说的事 / 注意到的话
        决定内容，意图决定句式，说话习惯里的语气词、口头禅、句子长短决定口吻——这样大脑分区和说话风格
        有没有起作用，假模型下也看得出来。都没有时照旧从短语表随机拼。"""
        f, r = self.fake, self.rng
        t = tags(user)
        intent, voice = t.get("intent", ""), t.get("voice", "")
        want = re.search(r"你想：(.+)", intent)
        focus = re.search(r"(?:想说的事|你注意到)：(.+)", intent)
        if not want:
            return f"{r.choice(f['open'])}，{r.choice(f['middle'])}{r.choice(f['close'])}"
        particles = _items(voice, "语气词") or list(f["open"][:3])
        catch = _items(voice, "口头禅（隔好几条才用一次）")
        short = "偏短" in voice
        head = r.choice(particles)
        what = (focus.group(1) if focus else r.choice(f["middle"])).strip()
        what = re.sub(r"^(心里挂着：|最近在|突然想到|突然想起)", "", what)[: 14 if short else 24]
        w = want.group(1)
        if "回得很短" in w:
            text = r.choice([head, f"{head}…", "嗯嗯", "好"])
        elif "追问" in w:
            text = f"{head}，{what[:8]}然后呢？"
        elif "不同意" in w:
            text = f"{head}，{what[:8]}这个我不太同意"
        elif "玩笑" in w:
            text = f"笑死，{what[:10]}"
        elif "心情" in w:
            text = f"{head}…{what}"
        elif "自己" in w or "换到" in w:
            text = f"{head}，{what}"
        else:
            text = f"{head}，{what[:10]}{r.choice(f['close'])}"
        if catch and r.random() < 0.15:
            text += f" {r.choice(catch)}"
        return text

    def __call__(self, model: str, messages: Sequence[Message]) -> RawCompletion:
        role = self.identify(messages)
        self.roles[role] += 1
        text = self.reply(role, messages)
        prompt = sum(tokens_of(m.content) for m in messages)
        return RawCompletion(text=text, input_tokens=prompt, output_tokens=tokens_of(text))

    def reply(self, role: str, messages: Sequence[Message]) -> str:
        user = messages[-1].content if messages else ""
        system = messages[0].content if messages else ""
        if role == "memory_night":
            return self.night(tags(user), system)
        if role == "growth_reflect":
            return self.reflect(tags(user))
        if role == "growth_review":
            block = tags(user).get("changes", "")
            n = len(re.findall(r"^\d+\. ", block, re.M))
            return json.dumps({"scores": [{"id": i, "score": 4} for i in range(1, n + 1)]})
        if role.startswith(("life_", "chat")):
            return self.say(user)
        if role.startswith(("moment_", "voice_", "plain_")) or role == "unknown":
            return self.say()
        return "{}"

    def night(self, t: dict[str, str], system: str) -> str:
        lines = [ln.split(" ", 1)[-1] for ln in t.get("log", "").splitlines() if ln.strip()]
        items = [
            {"text": ln[:40], "domain": "日常", "importance": self.rng.randint(1, 3), "fact": False}
            for ln in lines[:3]
        ]
        about = []
        m = re.search(r"领域只能是：([^。\n]+)", system)
        domain = re.split(r"[、，, ]", m.group(1))[0] if m else "日常"
        for ln in lines:
            if "用户：" in ln:
                said = ln.split("用户：", 1)[1].strip()
                if len(said) >= 6:
                    about.append(
                        {"text": f"用户说过{said[:20]}", "domain": domain, "quote": said[:12]}
                        | {"said": True}
                    )
        diary = "今天" + ("；".join(x[:20] for x in lines[:2]) or "没什么特别的事")
        hanging = {"add": [lines[0][:20]] if lines and self.rng.random() < 0.3 else [], "done": []}
        return json.dumps(
            {"diary": diary, "items": items, "hanging": hanging, "retell": [], "about": about[:2]},
            ensure_ascii=False,
        )

    def reflect(self, t: dict[str, str]) -> str:
        week = re.findall(r"^(\d+)\. \[[^\]]*\] (.+)$", t.get("week", ""), re.M)
        if not week:
            return json.dumps({"changes": [], "skip": "这周没什么变化"}, ensure_ascii=False)
        n, text = self.rng.choice(week)
        change = {
            "op": "add",
            "path": "likes.最爱",
            "value": self.rng.choice(self.fake["likes"]),
            "evidence": [int(n)],
            "quote": text[:20],
            "why": "这周老在做这件事",
        }
        return json.dumps({"changes": [change], "skip": ""}, ensure_ascii=False)


# --- 记录 -------------------------------------------------------------------------


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(f"{record.name}: {record.getMessage()[:200]}")


@dataclass
class SimResult:
    out: Path
    days: list[dict[str, Any]] = field(default_factory=list)
    members: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    tells: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    probes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(c["ok"] for c in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "meta": self.meta,
            "checks": self.checks,
            "tells": self.tells,
            "days": self.days,
            "members": self.members,
            "probes": self.probes,
            "errors": self.errors,
            "warnings": self.warnings[:200],
        }


# --- 模拟 -------------------------------------------------------------------------


class Simulation:
    def __init__(
        self,
        out: Path,
        *,
        days: int = 30,
        seed: int = 0,
        start: datetime | None = None,
        tz: str = "Asia/Shanghai",
        real: bool = False,
        max_usd: float = 0.0,
        user_per_day: float = 6.0,
        cards: str | None = None,
        config: AppConfig | None = None,
        material: dict | None = None,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        out = Path(out).resolve()
        if out.is_relative_to(DEFAULT_DB.parent.resolve()):
            raise SimError("模拟数据不能放在 data/ 下（要和真实数据分开）")
        if real and max_usd <= 0:
            raise SimError("真模型模拟必须给花费上限（max_usd > 0）")
        if days < 1:
            raise SimError("至少模拟 1 天")
        self.out, self.days, self.seed, self.real = out, days, seed, real
        self.max_usd, self.user_per_day, self.cards = max_usd, user_per_day, cards
        self.zone = ZoneInfo(tz)
        first = start or datetime(2026, 11, 2, 7, 0)  # 默认从一个星期一早上开始
        self.clock = SimClock(first.replace(tzinfo=self.zone) if first.tzinfo is None else first)
        self.config = config or load_config()
        self.material = material or yaml.safe_load(SIM_FILE.read_text(encoding="utf-8"))
        self.rng = random.Random(seed)
        self.result = SimResult(out)
        self._sleep = sleep or asyncio.sleep
        self.transcript = ""

    # --- 组装 ---

    def build(self) -> Any:
        from roundtable.core.service import RoundtableService  # 推迟导入：服务很重

        self.out.mkdir(parents=True, exist_ok=True)
        db = self.out / "sim.db"
        if db.exists():
            raise SimError(f"{db} 已存在：换一个输出目录（每次模拟用全新的数据库）")
        prompts = PromptLibrary()
        self.mind: FakeMind | None = None
        providers = None
        if not self.real:
            self.mind = FakeMind(self.config, prompts, self.material, self.seed)
            providers = {
                name: FakeProvider(name, default=self.mind) for name in self.config.models.channels
            }
        rt = Runtime.build(
            config=self.config,
            providers=providers,
            db_path=db,
            dotenv_path=PROJECT_ROOT / ".env" if self.real else None,
            environ=None if self.real else {},
            prompts=prompts,
            now=self.clock.now,
            members_dir=self.out / "no_assets",
            standee_file=self.out / "no_assets" / "standee.json",
        )
        rt.repo.clock = lambda: iso(self.clock.now())
        rt.repo.set_setting("home_tz", self.zone.key)
        svc = RoundtableService(rt)

        async def no_wait(_: float) -> None:
            return None

        svc.chats.sleep = no_wait  # 拆条之间不真的等
        svc.engine.heartbeat.unregister("wander")  # 模拟里成员不开真浏览器上网
        if self.cards:
            svc.member_profile_import(self.cards)
        return svc

    # --- 推进 ---

    async def _step(self, name: str, fn: Callable[[], Any]) -> Any:
        try:
            got = fn()
            return await got if asyncio.iscoroutine(got) else got
        except Exception as exc:  # noqa: BLE001 - 每个部件出错都记进报告，继续跑
            tb = traceback.format_exception_only(type(exc), exc)[-1].strip()
            at = self.clock.now().isoformat()
            self.result.errors.append({"at": at, "part": name, "error": tb})
            return None

    async def _user_turn(self, svc: Any, members: list[Any]) -> int:
        now = self.clock.now()
        tick_hours = self.config.roundtable.chat.life.tick_minutes / 60
        if now.hour not in USER_HOURS:
            return 0
        p = self.user_per_day * tick_hours / len(USER_HOURS)
        if self.rng.random() >= p:
            return 0
        mix = self.material["user_mix"]
        kind = self.rng.choices(list(mix), list(mix.values()))[0]
        text = self.rng.choice(self.material["user"][kind])
        room = ROOM if self.rng.random() < 0.3 else dm_room(self.rng.choice(members).id)
        sent = await self._step("chat_post", lambda: svc.chat_post(text, room=room))
        pending = list(svc._chat_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return int(sent is not None)

    async def probe(self, svc: Any, members: list[Any], day: int) -> None:
        """前后对比：在私聊里问每位成员同样的几句（走真实聊天流程），记下她的回答。"""
        for m in members:
            room = dm_room(m.id)
            for q in self.material.get("probes", []):
                sent = await self._step("probe", lambda q=q, room=room: svc.chat_post(q, room=room))
                pending = list(svc._chat_tasks)
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                after = sent["id"] if sent else 0
                rows = svc.rt.repo.chat_messages(room, after=after)
                said = [x["text"] for x in rows if x["author"] == m.id]
                self.result.probes.append(
                    {"day": day, "member": m.id, "name": m.name, "q": q, "a": " / ".join(said)}
                )

    def _spent(self, rt: Any, start: datetime, end: datetime) -> float:
        return rt.repo.spent_between(start, end)

    async def run(self) -> SimResult:
        handler = _Collect()
        logging.getLogger("roundtable").addHandler(handler)
        saved_fetch = browse.http_fetch

        async def offline(*_a: Any, **_k: Any) -> Any:
            raise browse.FeedError("模拟不联网")

        browse.http_fetch = offline
        began = time.monotonic()
        try:
            svc = self.build()
            try:
                await self._loop(svc)
            finally:
                self.finish(svc, time.monotonic() - began)
                await svc.aclose()
        finally:
            browse.http_fetch = saved_fetch
            logging.getLogger("roundtable").removeHandler(handler)
            self.result.warnings = handler.records
        self.write()
        return self.result

    async def _loop(self, svc: Any) -> None:
        rt = self.rt = svc.rt
        members = svc.chats.everyone()
        tick = timedelta(minutes=self.config.roundtable.chat.life.tick_minutes)
        begin = self.clock.now()
        end = begin + timedelta(days=self.days)
        self.result.meta["stopped"] = None
        day_start, user_msgs = begin, 0
        while self.clock.now() < end:
            now = self.clock.advance(tick)
            user_msgs += await self._user_turn(svc, members)
            local = svc._local_now()
            await self._step("life", lambda t=local: svc.life.tick(t))
            await self._step(
                "calendar", lambda t=local: svc.calendar.tick(t, svc.engine.bus.publish)
            )
            await self._step("engine", lambda t=local: svc.engine.tick(t))
            await self._step("growth", lambda t=local: svc.growth.tick(t))
            if now - day_start >= timedelta(days=1):
                self.result.days.append(self.snapshot(svc, members, day_start, now, user_msgs))
                day_start, user_msgs = now, 0
            first_or_last = now.date() == begin.date() or end - now < timedelta(days=1)
            if (local.hour, local.minute) == (21, 0) and first_or_last:  # 晚上 9 点问一遍
                await self.probe(svc, members, len(self.result.days) + 1)
            if self.real and self._spent(rt, begin, now + tick) >= self.max_usd:
                self.result.meta["stopped"] = f"真模型花费到了上限 ${self.max_usd:.2f}，提前停"
                break
            await self._sleep(0)

    # --- 快照与报告 ---

    def snapshot(
        self, svc: Any, members: list[Any], start: datetime, end: datetime, user_msgs: int
    ) -> dict[str, Any]:
        rt = svc.rt
        a, b = iso(start), iso(end)
        events = rt.repo.life_events_between(a, b)
        msgs = rt.repo.chat_messages_between(a, b)
        moods = {}
        for m in members:
            r = svc.engine.state.read(m.id, end) if svc.engine.state.enabled else None
            moods[m.id] = (round(r.mood, 2), round(r.energy, 2)) if r else None
        return {
            "day": len(self.result.days) + 1,
            "date": start.astimezone(self.zone).date().isoformat(),
            "life_events": len(events),
            "life_written": sum(1 for e in events if e["written"]),
            "member_msgs": sum(1 for m in msgs if m["author"] not in ("user", "system")),
            "user_msgs": user_msgs,
            "moments": len(rt.repo.moments(since=a, limit=500)),
            "memory_items": rt.repo.count_memory_items(),
            "about_facts": len(svc.engine.about.facts()),
            "spent_usd": round(self._spent(rt, start, end), 4),
            "errors": len(self.result.errors),
            "moods": moods,
        }

    def finish(self, svc: Any, seconds: float) -> None:
        rt, res = svc.rt, self.result
        members = svc.chats.everyone()
        begin = self.clock.now() - timedelta(days=len(res.days) or 1)
        texts: dict[str, list[str]] = {m.id: [] for m in members}
        since = iso(begin - timedelta(days=1))
        all_msgs = rt.repo.chat_messages_between(since, iso(self.clock.now()))
        for msg in all_msgs:
            if msg["author"] in texts:
                texts[msg["author"]].append(msg["text"])
        for m in members:
            rel = svc.engine.relation.summary(m.id) if svc.engine.relation.enabled else {}
            growth = [
                e for e in svc.growth.log.entries(m.id) if e.get("kind") not in ("skip", None)
            ]
            res.members.append(
                {
                    "member": m.id,
                    "name": m.name,
                    "stage": (rel.get("user") or {}).get("stage"),
                    "closeness": (rel.get("user") or {}).get("closeness"),
                    "closest_peer": (rel.get("peers") or [{}])[0].get("name"),
                    "memory_items": rt.repo.count_memory_items(m.id),
                    "growth_changes": len(growth),
                    "messages": len(texts[m.id]),
                    "moments": len(rt.repo.moments(author=m.id, limit=1000)),
                    "brain": svc.engine.brain.snapshot(m.id, self.clock.now()),
                }
            )
        res.tells = tells(texts)
        res.tells["change"] = change(rt, begin, self.clock.now())
        self.transcript = transcript(rt, {m.id: m.name for m in members}, self.zone)
        total = sum(d["spent_usd"] for d in res.days)
        res.meta |= {
            "mode": "real" if self.real else "fake",
            "days": len(res.days),
            "seed": self.seed,
            "start": (self.clock.now() - timedelta(days=len(res.days))).isoformat(),
            "wall_seconds": round(seconds, 1),
            "spent_usd": round(total, 4),
            "usd_per_day": round(total / max(1, len(res.days)), 4),
            "calls": dict(self.mind.roles) if self.mind else None,
        }
        res.checks = checks(self.config, res)

    def write(self) -> None:
        res = self.result
        (self.out / "report.json").write_text(
            json.dumps(res.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (self.out / "report.md").write_text(render(res), encoding="utf-8")
        (self.out / "chat.md").write_text(self.transcript, encoding="utf-8")


# --- 检查与指标（程序算，不调模型）---------------------------------------------------


def _similar(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(1, len(sa | sb))


def _grams(text: str) -> Counter[str]:
    return Counter(text[i : i + 2] for i in range(len(text) - 1))


def attribution(texts: dict[str, list[str]]) -> float | None:
    """盲猜是谁说的：每条消息拿掉后，和每位成员其余消息的两字片段最像的是谁（留一法）。
    猜中率接近 1/人数 = 说话没特点；越高越有各自的味道。"""
    pools = {m: sum((_grams(t) for t in ts), Counter()) for m, ts in texts.items() if len(ts) >= 3}
    if len(pools) < 2:
        return None
    hit = n = 0
    for member, ts in texts.items():
        if member not in pools:
            continue
        for t in ts:
            g = _grams(t)
            if not g:
                continue

            def score(other: str, g: Counter[str] = g, member: str = member) -> float:
                pool = pools[other] - g if other == member else pools[other]
                total = sum(pool.values()) or 1
                return sum(min(c, pool[k]) for k, c in g.items()) / total**0.5

            hit += max(pools, key=score) == member
            n += 1
    return round(hit / n, 3) if n else None


def tells(texts: dict[str, list[str]]) -> dict[str, Any]:
    """「AI 味」程序指标：一眼能看出是 AI 的迹象（越低越像真人）。"""
    all_msgs = [t for ts in texts.values() for t in ts]
    if not all_msgs:
        return {"messages": 0}
    lengths = [len(t) for t in all_msgs]
    dup = sum(c - 1 for c in Counter(all_msgs).values() if c > 1)
    openings = Counter(t[:3] for t in all_msgs if len(t) >= 3)
    formal = re.compile(r"首先|其次|总之|综上|作为一个|希望对你有帮助|您")
    per_member = {}
    for member, ts in texts.items():
        near = sum(1 for i, t in enumerate(ts[1:], 1) if _similar(t, ts[i - 1]) >= 0.8)
        per_member[member] = round(near / max(1, len(ts) - 1), 3)
    return {
        "messages": len(all_msgs),
        "dup_ratio": round(dup / len(all_msgs), 3),
        "top_opening_ratio": round(openings.most_common(1)[0][1] / len(all_msgs), 3)
        if openings
        else 0.0,
        "formal_ratio": round(sum(1 for t in all_msgs if formal.search(t)) / len(all_msgs), 3),
        "len_mean": round(statistics.mean(lengths), 1),
        "len_cv": round(statistics.pstdev(lengths) / max(1, statistics.mean(lengths)), 3),
        "self_repeat": per_member,
        "attribution": attribution(texts),
        "chance": round(1 / max(1, sum(1 for ts in texts.values() if len(ts) >= 3)), 3),
    }


def change(rt: Any, begin: datetime, end: datetime) -> dict[str, Any]:
    """第一周和最后一周比：说的话（两字片段）重合多少、去过的地方 / 做的事有几种。"""
    if end - begin < timedelta(days=8):
        return {}

    def week(a: datetime) -> tuple[set[str], set[str]]:
        b = a + timedelta(days=7)
        grams: set[str] = set()
        for msg in rt.repo.chat_messages_between(iso(a), iso(b)):
            if msg["author"] not in ("user", "system"):
                grams |= set(_grams(msg["text"]))
        places = set()
        for e in rt.repo.life_events_between(iso(a), iso(b)):
            hit = re.search(r"地点：(.+)", e.get("topic") or "")
            if hit:
                places.add(hit.group(1))
        return grams, places

    g1, p1 = week(begin)
    g2, p2 = week(end - timedelta(days=7))
    overlap = len(g1 & g2) / max(1, len(g1 | g2))
    return {
        "topic_overlap": round(overlap, 3),
        "places_first": len(p1),
        "places_last": len(p2),
        "places_new": len(p2 - p1),
    }


def checks(config: AppConfig, res: SimResult) -> list[dict[str, Any]]:
    life_cap = config.roundtable.chat.life.daily_usd
    total_cap = config.roundtable.budget.daily_usd
    out = []

    def add(name: str, ok: bool, detail: str) -> None:
        out.append({"name": name, "ok": bool(ok), "detail": detail})

    add("各部件不报错", not res.errors, f"{len(res.errors)} 次出错")
    worst = max((d["spent_usd"] for d in res.days), default=0.0)
    if total_cap:
        add("每天花费不超总日上限", worst <= total_cap + 1e-6, f"最多一天 ${worst:.3f}")
    chat = config.roundtable.chat.engine.scheduler.daily_usd
    add(
        "后台（生活 + 主动 + 群聊）不明显超额",
        worst <= life_cap + chat + 0.05,
        f"最多一天 ${worst:.3f}，生活上限 ${life_cap} + 聊天日额度 ${chat}",
    )
    quiet = [d["date"] for d in res.days if d["life_events"] == 0]
    add("每天都有生活", not quiet, f"没生活的日子：{quiet[:5]}" if quiet else "每天都有")
    if res.days and res.days[-1]["memory_items"] is not None:
        n = res.days[-1]["memory_items"]
        add("记忆在积累", n > 0, f"期末 {n} 件")
    no_mem = [m["name"] for m in res.members if not m["memory_items"]]
    add("每位成员都有记忆", not no_mem, f"没有记忆的：{no_mem}" if no_mem else "都有")
    if len(res.days) >= 7:
        moments = sum(d["moments"] for d in res.days)
        add("朋友圈有人发", moments > 0, f"共 {moments} 条")
        if any(d["user_msgs"] for d in res.days):
            n = res.days[-1]["about_facts"]
            add("关于你记住了事", n > 0, f"{n} 条")
    if len(res.days) >= 8:
        grown = sum(m["growth_changes"] for m in res.members)
        add("每周反思有记录", grown > 0, f"共 {grown} 条成长记录")
    moving = [
        d for d in res.days for v in d["moods"].values() if v is not None and abs(v[0]) > 0.01
    ]
    add("心情会变", bool(moving) or not res.days, "有起伏" if moving else "一直不变")
    return out


def transcript(rt: Any, names: dict[str, str], zone: Any) -> str:
    """模拟世界里的全部聊天，按天分好（给人翻看）。"""
    rooms = {r["id"]: r for r in rt.repo.chat_rooms()}
    names = names | {"user": "你（模拟用户）"}
    out, day = ["# 模拟里的聊天记录", ""], None
    for m in rt.repo.chat_messages_between("0", "9"):
        if m["author"] == "system":
            continue
        at = datetime.fromisoformat(m["created_at"]).astimezone(zone)
        if at.date() != day:
            day = at.date()
            out += ["", f"## {day}", ""]
        room = rooms.get(m["room"]) or {}
        title = room.get("title") or m["room"]
        where = {"dm": f"私聊·{title}", "pair": f"成员私聊·{title}"}.get(room.get("kind"), title)
        out.append(f"- {at:%H:%M} [{where}] {names.get(m['author'], m['author'])}：{m['text']}")
    return "\n".join(out) + "\n"


def _spark(values: list[float]) -> str:
    bars = "▁▂▃▄▅▆▇█"
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    return "".join(bars[int((v - lo) / span * (len(bars) - 1))] for v in values)


def render(res: SimResult) -> str:
    m = res.meta
    passed = sum(c["ok"] for c in res.checks)
    lines = [
        "# 成员系统加速时间模拟报告",
        "",
        f"- 模式：{'真模型' if m.get('mode') == 'real' else '假模型（花费是按配置价格估的）'}"
        f"；模拟 {m.get('days')} 天，用时 {m.get('wall_seconds')} 秒；种子 {m.get('seed')}",
        f"- 花费：共 ${m.get('spent_usd')}，平均每天 ${m.get('usd_per_day')}",
        f"- 检查：{passed}/{len(res.checks)} 通过"
        + (f"；{m['stopped']}" if m.get("stopped") else ""),
        "",
        "## 检查",
        "",
        *[f"- {'✅' if c['ok'] else '❌'} {c['name']}：{c['detail']}" for c in res.checks],
        "",
        "## AI 味指标（越低越像真人）",
        "",
    ]
    t = res.tells
    if t.get("messages"):
        lines += [
            f"- 成员消息 {t['messages']} 条，平均 {t['len_mean']} 字，长短变化系数 {t['len_cv']}",
            f"- 一字不差的重复：{t['dup_ratio']:.1%}；最常见开头占比：{t['top_opening_ratio']:.1%}",
            f"- 客服腔 / 书面腔（首先、总之、您……）：{t['formal_ratio']:.1%}",
        ]
        if t.get("attribution") is not None:
            lines.append(
                f"- 盲猜是谁说的（越高越有各自特点）：猜中 {t['attribution']:.0%}，"
                f"瞎猜也有 {t['chance']:.0%}"
            )
    else:
        lines.append("- 没有成员消息")
    lines += ["", "## 每位成员（期末）", ""]
    head = ["成员", "和你的阶段", "亲近度", "最亲近的同事", "记忆件", "成长记录", "消息"]
    lines.append("| " + " | ".join([*head, "朋友圈", "心情走势"]) + " |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for row in res.members:
        moods = [d["moods"].get(row["member"]) for d in res.days]
        spark = _spark([v[0] for v in moods if v])
        lines.append(
            f"| {row['name']} | {row['stage'] or '-'} | {row['closeness'] or 0} | "
            f"{row['closest_peer'] or '-'} | {row['memory_items']} | {row['growth_changes']} | "
            f"{row['messages']} | {row['moments']} | {spark} |"
        )
    ch = t.get("change") or {}
    if ch or any(row.get("brain") for row in res.members):
        lines += ["", "## 大脑分区（主动性与变化）", ""]
        if ch:
            lines += [
                f"- 第一周和最后一周说的话重合 {ch['topic_overlap']:.0%}（越低变化越大）；"
                f"去处和做的事：第一周 {ch['places_first']} 种，最后一周 {ch['places_last']} 种，"
                f"其中新出现 {ch['places_new']} 种",
                "",
            ]
        lines.append("| 成员 | 在做的事（进展） | 还没讲的新鲜事 | 最常挑的意图 | 此刻最强的驱力 |")
        lines.append("|---|---|---|---|---|")
        for row in res.members:
            b = row.get("brain") or {}
            goals = "；".join(f"{k}（{v}）" for k, v in (b.get("stages") or {}).items()) or "-"
            counts = sorted((b.get("counts") or {}).items(), key=lambda kv: -kv[1])[:3]
            top = "、".join(f"{k} {v}" for k, v in counts) or "-"
            drives = b.get("drives") or {}
            strong = max(drives.items(), key=lambda kv: kv[1], default=("-", 0))
            lines.append(
                f"| {row['name']} | {goals} | {len(b.get('news') or [])} | {top} | "
                f"{strong[0]} {strong[1]} |"
            )
    lines += ["", "## 每天", ""]
    head = ["天", "日期", "生活事件", "写成聊天", "成员消息", "你的消息", "朋友圈", "记忆件"]
    lines.append("| " + " | ".join([*head, "关于你", "花费"]) + " |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for d in res.days:
        lines.append(
            f"| {d['day']} | {d['date']} | {d['life_events']} | {d['life_written']} | "
            f"{d['member_msgs']} | {d['user_msgs']} | {d['moments']} | {d['memory_items']} | "
            f"{d['about_facts']} | ${d['spent_usd']} |"
        )
    days = sorted({p["day"] for p in res.probes})
    if len(days) >= 2:
        first, last = days[0], days[-1]
        lines += ["", f"## 前后对比（第 {first} 天 vs 第 {last} 天，同样的问题）", ""]
        got = {(p["day"], p["member"], p["q"]): p for p in res.probes}
        for row in res.members:
            lines += [f"### {row['name']}", ""]
            for q in dict.fromkeys(p["q"] for p in res.probes):
                a = got.get((first, row["member"], q), {}).get("a") or "（没回）"
                b = got.get((last, row["member"], q), {}).get("a") or "（没回）"
                lines += [f"- 问：{q}", f"  - 第 {first} 天：{a}", f"  - 第 {last} 天：{b}"]
            lines.append("")
    if res.errors:
        lines += ["", "## 出错（前 20 条）", ""]
        lines += [f"- {e['at'][:16]} {e['part']}：{e['error']}" for e in res.errors[:20]]
    return "\n".join(lines) + "\n"
