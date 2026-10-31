"""大脑分区（docs/BRAIN.md）：说话、做事之前，先由程序把各区的信号综合成一张「意图卡」。

各区（全由程序算，不调用模型）：
- **丘脑**（``salience``）：一条消息对她有多显著：点了名、说到她喜欢的、亲近的人说的、
  带情绪、是问句。
- **下丘脑**（``drives``）：想找人说话、闷了想换新鲜的、有话想说、想把手上的事往前推。驱力随时间涨
  （涨速看性格分），被事满足就降（说了话、和人待在一起、做了在练的事、去了新地方）。
- **前额叶**（``goals`` / ``intent``）：从喜好里挑一两件「在做的事」，空闲时去做，会进步、会卡住、
  过坎时有新鲜事可说；说话前按性格、心情、精力、驱力加权挑一个意图（接话 / 追问 / 讲自己的事 /
  换话题 / 开玩笑 / 不同意 / 说心情 / 累了回短的），群里不显著又不想说时就不接。
- **默认模式**（``thoughts`` 里的走神）：空闲时偶尔从喜好、挂着的事、旧记忆里冒出一个念头。
- **全局工作区**（``spotlight``）：各区交上来的念头按强度 × 一点随机挑一件，写进意图卡；
  说完回传（``after``）：驱力下降、讲过的新鲜事不再当新鲜事。

读别的区：心情精力读 P3（``StatePlugin``），亲近度读 P4（``RelationPlugin``），喜好和性格分读
自我档案，挂着的事和旧记忆读 P2 的表。**本模块只写自己的状态**（``app_settings`` 的
``member_brain:<成员>`` 键，不加迁移），别的区的数据一概不写。

只用于生活模式（生活群、私聊、成员之间的生活聊天）；工作模式不挂载（``require_life``）。
8 位同一套规则（``chat.engine.brain``），差别只来自各自的档案和经历；
随机数由（成员, 时刻）派生，可复现。
"""

from __future__ import annotations

import contextlib
import json
import random
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.member import psyche
from roundtable.core.member.bus import PERCEIVE, Event, EventBus
from roundtable.core.member.care import Care
from roundtable.core.member.circle import Circle
from roundtable.core.member.feel import Feelings
from roundtable.core.member.firsts import Firsts
from roundtable.core.member.human import Human
from roundtable.core.member.lingo import Lingo
from roundtable.core.member.love import USER, Heart
from roundtable.core.member.memes import MemeSense
from roundtable.core.member.mode import Mode, require_life
from roundtable.core.member.phrasebook import Phrasebook
from roundtable.core.member.profile import ProfileStore
from roundtable.core.storage.repository import iso

KEY = "member_brain:"
FREE_ACTS = frozenset({"exercise", "chat", "stargaze", "game", "read"})
EXPLORE = (
    ("gym", "exercise"),
    ("lounge", "chat"),
    ("rooftop", "stargaze"),
    ("lounge", "game"),
    ("canteen", "chat"),
    ("dorm", "read"),
)
_CLAIM = re.compile(r"(我觉得|我认为|应该|肯定|就是|不如|才是|根本|绝对|一定)")
_QUESTION = re.compile(r"[？?]|(吗|呢|么)[。！!～~]*$")
KEEP_NEWS = 6
KEEP_PERCEPTS = 4
CALENDAR_KINDS = frozenset({"holiday", "birthday", "joined", "user", "lunar", "observance"})
_OWN_DAY = re.compile(r"^(今天是|再过\S+?天就是)\S+?(的生日|入职)")  # 日历念头里的「某某的生日」
SENSES = {"web": "上网看到", "screen": "在屏幕上看到", "video": "看到", "weather": "看了天气"}
URGENT = frozenset({"let_go", "retain", "blame"})  # 感情话里这几种不受条数和冷却限制
FIRST = frozenset({"unknown", "consistent", "busy", "wrap", "attention"})  # 人味里先给的几种


def _cue_key(tag: str, line: str) -> str:
    return f"{tag}:{line[:16]}"


@dataclass(frozen=True)
class Thought:
    source: str  # goal / hanging / wander / mood / message
    text: str
    strength: float
    kind: str = "own"  # own：她自己的事；msg：别人刚说的；claim：别人说了个看法


QUICK_INTENTS = frozenset({"respond", "joke"})  # 这几种意图才可能只回一句短的


@dataclass(frozen=True)
class IntentCard:
    member: str
    intent: str  # respond / ask / share / … / silent
    text: str  # 写进提示词 <intent> 的那一段
    spotlight: Thought | None = None
    salience: float = 0.0
    tags: tuple[str, ...] = ()  # 这次用上的人的特质（矛盾、余波、先果后因……），模拟报告统计用
    # 真说了才结算的事（候选卡没说出口就不算）：元认知察觉到的套路、说干就干记下的盼头
    noticed: str = ""
    expect: str = ""
    to: str = ""  # 对谁说（成员 id 或 "user"；主动开口时可能为空），爱与复合情绪结算用
    cues: tuple[str, ...] = ()  # 这张卡用上的附加提示（真说了才记进冷却）
    lean: str = ""  # 对方在哪条三观轴上和她相反（"轴|方向"），真说了才结算慢慢变
    quick: str = ""  # 万能短回复：有就直接发这句，不调模型（lingo.py）

    @property
    def silent(self) -> bool:
        return self.intent == "silent"


@dataclass
class _State:
    at: str = ""
    drives: dict[str, float] = field(default_factory=dict)
    goals: list[dict[str, Any]] = field(default_factory=list)
    news: list[dict[str, Any]] = field(default_factory=list)  # 还没讲过的新鲜事
    recent: list[str] = field(default_factory=list)  # 最近几次的意图
    places: list[str] = field(default_factory=list)  # 最近几次空闲去的地方
    counts: dict[str, int] = field(default_factory=dict)  # 各种意图一共挑过几次（看各人差别）
    expects: list[dict[str, Any]] = field(default_factory=list)  # 奖赏：盼着的事（到期见分晓）
    signals: list[dict[str, Any]] = field(default_factory=list)  # 待发到总线的信号（奖赏、共情）
    noticed: str = ""  # 元认知：最近察觉到的自己的套路
    episodes: list[dict[str, Any]] = field(default_factory=list)  # 情绪余波：哪件事、什么感受、何时
    psy: dict[str, int] = field(default_factory=dict)  # 各种特质一共用上几次（看各人差别）
    used: list[str] = field(default_factory=list)  # 最近说出口的附加提示（冷却，去模板化）
    dropped: list[dict[str, Any]] = field(default_factory=list)  # 放下过的事（不再捡回来）
    shifts: dict[str, float] = field(default_factory=dict)  # 三观轴上被在乎的人慢慢带偏了多少
    percepts: list[dict[str, Any]] = field(default_factory=list)  # 感知到的事（节日、上网看到的）
    routines: dict[str, dict[str, Any]] = field(default_factory=dict)  # 习惯回路：钟点|去处 → 劲儿


def _factor(score: Any) -> float:
    try:
        return max(0.2, 1 + 0.2 * (float(score) - 3))
    except (TypeError, ValueError):
        return 1.0


class Brain:
    def __init__(
        self,
        rt: Any,
        bus: EventBus | None = None,
        buffer: dict[str, str] | None = None,
        members: Callable[[], list[str]] | None = None,
        names: Callable[[], dict[str, str]] | dict[str, str] | None = None,
    ) -> None:
        self.rt = rt
        self.bus = bus
        self.members = members
        self.profiles = ProfileStore(rt)
        self.memes = MemeSense(rt)
        self.phrases = Phrasebook(rt, bus)
        self.circle = Circle(rt, bus)  # 圈外熟人名册：她提过的室友、老同学，免得说反
        self.firsts = Firsts(rt, bus)  # 「第一次」纪念：你亲口说的第一次，整月 / 整年那天轻轻提
        # 爱与复合情绪（love.py）：传了 bus 时订阅聊天和生活事件；names：成员 id → 称呼
        self.heart = Heart(rt, bus, names)
        # 人味（human.py）：知识边界、自己的时间、自我一致、八卦、时间纵深、小毛病、主动约人
        self.human = Human(rt, bus, names)
        # 说话的样子（lingo.py）：看懂语境、万能短回复、语气变调、外号、跟人学、发出前收尾
        self.lingo = Lingo(rt, bus, names)
        # 各自的情感（feel.py）：开心 / 难过 / 想念 / 生气按她自己的理解记经历、写进意图卡
        self.feel = Feelings(rt, bus, names)
        # buffer：先写在内存里、``flush()`` 时才落库（生活排日程时用，排出来的事真发生了才算数，
        # 同一段时间排两次结果一样）
        self.buffer = buffer
        if bus is not None:
            bus.subscribe("chat.message", self.on_event)
            bus.subscribe("life.event", self.on_event)
            for kind in ("calendar.day", "calendar.upcoming", f"{PERCEIVE}*"):
                bus.subscribe(kind, self.on_sense)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.brain

    def enabled(self) -> bool:
        return bool(self.rt.config.roundtable.chat.engine.enabled and self.rules.enabled)

    def local(self, at: datetime) -> datetime:
        """换算到主时区（按哪天、星期几过日子都看这个，不看 UTC）。"""
        return home_zone(self.rt).local(at)

    # --- 存取（只有这里写 member_brain:*）------------------------------------------

    def _load(self, member: str) -> _State:
        raw = self.buffer.get(member) if self.buffer is not None else None
        raw = raw if raw is not None else self.rt.repo.setting(KEY + member)
        if not raw:
            return _State()
        try:
            data = json.loads(raw)
            return _State(**{k: v for k, v in data.items() if k in _State.__dataclass_fields__})
        except (ValueError, TypeError):
            return _State()

    def _save(self, member: str, s: _State) -> None:
        raw = json.dumps(s.__dict__, ensure_ascii=False)
        if self.buffer is not None:
            self.buffer[member] = raw
        else:
            self.rt.repo.set_setting(KEY + member, raw)

    def flush(self) -> int:
        """把内存里排好的状态落库。返回落了几位。"""
        if not self.buffer:
            return 0
        for member, raw in self.buffer.items():
            self.rt.repo.set_setting(KEY + member, raw)
        n = len(self.buffer)
        self.buffer.clear()
        return n

    def clear(self, member: str) -> None:
        self.rt.repo.set_setting(KEY + member, "")

    def snapshot(self, member: str, at: datetime | None = None) -> dict[str, Any]:
        """看她此刻的驱力、目标、没讲的新鲜事（网页 / 模拟报告用）。"""
        at = at or self.rt.budget.now()
        s = self._grown(member, self._load(member), at)
        return {
            "drives": s.drives,
            "goals": s.goals,
            "news": s.news,
            "recent": s.recent,
            "counts": s.counts,
            "episodes": s.episodes,
            "psy": s.psy,
            "stages": {g["name"]: self._stage(g["level"]) for g in s.goals},
        }

    def _traits(self, member: str) -> dict[str, Any]:
        data = self.profiles.current(member).data
        return dict((data.get("personality") or {}).get("五项") or {})

    def _likes(self, member: str) -> list[str]:
        fav = (self.profiles.current(member).data.get("likes") or {}).get("最爱") or []
        if isinstance(fav, str):
            fav = re.split(r"[、，,／/；;]", fav)
        return [str(x).strip() for x in fav if str(x).strip()]

    # --- 下丘脑：驱力 -------------------------------------------------------------

    def _grown(self, member: str, s: _State, at: datetime) -> _State:
        """按经过的时间让驱力自己涨（线性、封顶 1）。"""
        traits = self._traits(member)
        last = datetime.fromisoformat(s.at) if s.at else None
        hours = max((at - last).total_seconds() / 3600, 0) if last else 0.0
        for name, rule in self.rules.drives.items():
            base = s.drives.get(name, 0.3)
            rate = rule.rate * (_factor(traits.get(rule.trait, 3)) if rule.trait else 1.0)
            s.drives[name] = round(min(1.0, base + rate * min(hours, 72)), 4)
        s.at = iso(at)
        return s

    def _satisfy(self, s: _State, what: str) -> None:
        for name, rule in self.rules.drives.items():
            drop = rule.satisfy.get(what, 0.0)
            if drop:
                s.drives[name] = round(max(0.0, s.drives.get(name, 0.3) - drop), 4)

    def satisfy(self, member: str, what: str, at: datetime) -> None:
        s = self._grown(member, self._load(member), at)
        self._satisfy(s, what)
        self._save(member, s)

    def on_event(self, event: Event) -> None:
        """总线：说了话 → spoke；生活里和人碰到一起 / 私聊 → gathered。"""
        if not self.enabled():
            return
        if event.kind == "chat.message":
            if event.member:
                self.satisfy(event.member, "spoke", event.at)
            self._empathy(event)
        elif (
            event.kind == "life.event"
            and event.member
            and event.data.get("kind") in ("gather", "pair")
        ):
            self.satisfy(event.member, "gathered", event.at)

    def on_sense(self, event: Event) -> None:
        """输入层进念头：今天和快到的节日、纪念日（日历），上网、看屏幕看到的（感知卡）。"""
        if not self.enabled():
            return
        data, kind = event.data, event.kind
        # (谁，没有 = 全员；念头；种类；生日 / 入职纪念日是谁的)
        found: list[tuple[str | None, str, str, str | None]] = []
        if kind == "calendar.day":
            for it in data.get("items") or []:
                if it.get("kind") in CALENDAR_KINDS:
                    text = f"今天是{it.get('name', '')}"
                    found.append((None, text, str(it.get("kind")), it.get("member")))
        elif kind == "calendar.upcoming" and data.get("kind") in CALENDAR_KINDS:
            text = f"再过{data.get('in_days', '几')}天就是{data.get('name', '')}"
            found.append((None, text, "upcoming", data.get("member")))
        elif kind.startswith(PERCEIVE) and event.member:
            card = str(data.get("card") or "").strip().replace("\n", " ")
            if card:
                verb = SENSES.get(kind.removeprefix(PERCEIVE), "看到")
                found.append((event.member, f"{verb}：{card[:30]}", "sense", None))
        everyone = self.members() if self.members is not None else []
        for who, said, what, owner in found:
            for m in [who] if who else everyone:
                text = self._own(said, owner) if owner == m else said
                s = self._grown(m, self._load(m), event.at)
                if any(p["text"] == text for p in s.percepts):
                    continue
                item = {"text": text, "at": iso(event.at), "kind": what}
                s.percepts = (s.percepts + [item])[-KEEP_PERCEPTS:]
                self._save(m, s)

    def _own(self, text: str, member: str) -> str:
        """自己的生日 / 入职纪念日：念头里写「你」，不把自己说成第三个人（10-31 真模型试跑）。"""
        return _OWN_DAY.sub(r"\1你\2", text, count=1)

    def _festive(self, s: _State, at: datetime) -> bool:
        """今天是节假日或纪念日（日历进来的）：更想约人出去。"""
        day = self.local(at).date()
        return any(
            p["kind"] in CALENDAR_KINDS
            and self.local(datetime.fromisoformat(p["at"])).date() == day
            for p in s.percepts
        )

    def _empathy(self, event: Event) -> None:
        """共情：有人（你或同事）说了难过 / 高兴的话，在场的成员按温和分和亲近度，
        有一定概率心情跟着动一下（发 brain.signal，由 P3 的反射表改心情）。"""
        if self.bus is None:
            return
        msg = self.rt.repo.chat_message(int(event.data.get("id", 0) or 0))
        if not msg:
            return
        r = self.rules.empathy
        text = msg["text"] or ""
        tag = (
            "empathy_sad"
            if any(w in text for w in r.sad_words)
            else "empathy_joy"
            if any(w in text for w in r.joy_words)
            else ""
        )
        if not tag:
            return
        from roundtable.core.member.relation import RelationPlugin

        room = self.rt.repo.chat_room(msg["room"]) or {}
        present = list(room.get("members") or [])
        if msg["room"].startswith("dm:"):
            present = [msg["room"][3:]]
        elif not present and self.members is not None:
            present = self.members()  # 大家庭群：全员都在
        speaker = event.member
        for m in present:
            if m == speaker:
                continue
            close = 0.0
            if speaker:
                with contextlib.suppress(Exception):
                    close = RelationPlugin(self.rt).closeness(m, speaker, event.at)
            else:
                close = 0.5  # 你说的：大家都在意
            gentle = _num(self._traits(m).get("温和", 3)) - 3
            care = self.heart.care(m, speaker or USER, event.at)  # 在乎的人难过更容易跟着动
            p = r.base + r.gentle * gentle + r.close * close + self.heart.rules.empathy * care
            rng = random.Random(f"brain-empathy:{m}:{msg['id']}")
            if rng.random() < p:
                why = f"听到「{text[:16]}」"
                self.bus.publish(Event("brain.signal", event.at, m, {"tags": [tag], "why": why}))
                feel = self.rules.psyche.episode.feels.get(tag, "")
                if feel and tag == "empathy_sad" and care >= 0.5:
                    feel = "心疼"
                if feel:
                    s = self._grown(m, self._load(m), event.at)
                    self._episode(s, feel, why, event.at)
                    self._save(m, s)

    # --- 前额叶：长线目标 ---------------------------------------------------------

    def _goals(self, member: str, s: _State) -> list[dict[str, Any]]:
        """在做的事：先从人物卡「成长」章里的小愿望和「最可能改变的地方」里取，没有再用喜好。"""
        if not s.goals and self.rules.goals.max_active:
            pool = seeds(str((self.profiles.current(member).data.get("values") or {}).get("成长")))
            pool += list(self._likes(member))  # 就是喜好本身（别带「有关的小计划」这种程序腔）
            pool = pool or list(self.rules.goals.fallback)
            rng = random.Random(f"brain-goals:{member}")
            first, rest = pool[:1], pool[1:]  # 小愿望排第一，其余随机
            rng.shuffle(rest)
            chosen = (first + rest)[: self.rules.goals.max_active]
            s.goals = [self._new_goal(member, g) for g in chosen]
        return s.goals

    def _new_goal(self, member: str, name: str) -> dict[str, Any]:
        """新开一件在做的事。和 P6 成长打通：这件事所属领域她已经熟（熟练度等级高），
        起步就快一些，不会一边说「已经很熟练」一边说「刚开始」。"""
        from roundtable.core.member.growth import habits as hb
        from roundtable.core.member.memory.domains import classify

        domains = list(self.rt.config.roundtable.chat.memory.domains)
        domain = classify(name, domains)
        try:
            skills = hb.skills(self.rt.repo.memory_item_stats(member), domains)
            lv = next((x["level"] for x in skills if x["domain"] == domain), 0)
        except Exception:  # noqa: BLE001 - 读不到熟练度就从头来
            lv = 0
        r = self.rules.goals
        xp = round(r.levels[0] * min(1.0, lv / 5), 2) if r.levels else 0.0
        level = sum(1 for need in r.levels if xp >= need)
        return {"name": name, "xp": xp, "level": level, "stuck": 0, "domain": domain}

    def _stage(self, level: int) -> str:
        stages = self.rules.goals.stages
        return stages[min(level, len(stages) - 1)]

    def _pursue(
        self, s: _State, goal: dict[str, Any], rng: random.Random, at: datetime, member: str = ""
    ) -> str:
        """做一次在做的事：涨经验，可能卡住，可能过一个坎（记成没讲过的新鲜事）。"""
        r = self.rules.goals
        if rng.random() < r.setback_chance:
            goal["stuck"] = goal.get("stuck", 0) + 1
            goal["fails"] = goal.get("fails", 0) + 1
            tired_of = goal["fails"] >= r.give_up_after and goal["level"] <= 1
            if tired_of and rng.random() < r.give_up_chance:  # 主动做决定：做不下去就放下、换一件
                return self._give_up(s, goal, rng, at, member)
            note = f"「{goal['name']}」这件事最近没什么进展，有点泄气"
            self._news(s, note, at, 0.6, neg=True)
            return note
        goal["xp"] = round(goal["xp"] + rng.uniform(*r.gain), 2)
        goal["stuck"] = 0
        while goal["level"] < len(r.levels) and goal["xp"] >= r.levels[goal["level"]]:
            goal["level"] += 1
            self._news(s, f"「{goal['name']}」{self._stage(goal['level'])}", at, 0.9)
            expected = any(e.get("goal") == goal["name"] for e in s.expects)
            if not expected:  # 没盼着却成了：惊喜（盼着的那种到期时算「兑现」）
                s.signals.append({"tags": ["surprise"], "at": iso(at), "why": goal["name"]})
        self._anticipate_goal(s, goal, at)
        return f"在{goal['name']}（{self._stage(goal['level'])}）"

    def _give_up(
        self, s: _State, goal: dict[str, Any], rng: random.Random, at: datetime, member: str
    ) -> str:
        names = {g["name"] for g in s.goals} | {d["name"] for d in s.dropped}
        pool = [x for x in self.rules.goals.fallback if x not in names]
        s.dropped = (s.dropped + [{"name": goal["name"], "at": iso(at)}])[-6:]
        s.expects = [e for e in s.expects if e.get("goal") != goal["name"]]
        if pool:
            new = rng.choice(pool)
            s.goals[s.goals.index(goal)] = self._new_goal(member, new)
            note = f"决定先放下「{goal['name']}」，改去{new}"
        else:
            s.goals.remove(goal)
            note = f"决定先放下「{goal['name']}」，歇一阵再说"
        self._news(s, note, at, 0.7)
        return note

    def _news(
        self, s: _State, text: str, at: datetime, strength: float, *, neg: bool = False
    ) -> None:
        s.news.append({"text": text, "at": iso(at), "strength": strength, "neg": neg})
        s.news = s.news[-KEEP_NEWS:]

    # --- 情绪余波：一件事的情绪拖一阵，按各人习惯的方式消化 ---------------------------

    def _episode(self, s: _State, feel: str, why: str, at: datetime) -> None:
        s.episodes = [e for e in s.episodes if e["why"] != why]
        s.episodes = (s.episodes + [{"feel": feel, "why": why, "at": iso(at)}])[
            -self.rules.psyche.episode.keep :
        ]

    def _lingering(self, member: str, s: _State, at: datetime) -> tuple[dict, float, str] | None:
        """还没消化完的、最强的那段余波：(余波, 剩下的强度, 她的消化方式)。"""
        ps = self.rules.psyche
        if not (ps.enabled and s.episodes):
            return None
        how = psyche.strategy(ps.episode, self._traits(member), member)
        scale = ps.episode.strategies[how].half_life if how else 1.0
        best = None
        for e in s.episodes:
            since = datetime.fromisoformat(e["at"])
            if since > at:
                continue
            left = psyche.decayed(1.0, since, at, ps.episode.half_life_hours * scale)
            if best is None or left > best[1]:
                best = (e, round(left, 3), how)
        return best if best and best[1] >= 0.25 else None

    # --- 奖赏：盼着 → 兑现 / 落空 / 惊喜 -------------------------------------------

    def _expect(self, s: _State, item: dict[str, Any], at: datetime) -> None:
        if any(e["text"] == item["text"] for e in s.expects):
            return
        item |= {"at": iso(at), "due": iso(at + timedelta(days=self.rules.reward.ahead_days))}
        s.expects = (s.expects + [item])[-4:]
        s.signals.append({"tags": ["anticipate"], "at": iso(at), "why": item["text"]})

    def _anticipate_goal(self, s: _State, goal: dict[str, Any], at: datetime) -> None:
        levels = self.rules.goals.levels
        if goal["level"] >= len(levels):
            return
        nxt = levels[goal["level"]]
        if goal["xp"] >= nxt * self.rules.reward.near:
            stage = self._stage(goal["level"] + 1)
            text = f"「{goal['name']}」眼看就要{stage}了"
            self._expect(s, {"text": text, "goal": goal["name"], "level": goal["level"]}, at)

    def _anticipate_plan(self, member: str, s: _State, at: datetime, rng: random.Random) -> None:
        r = self.rules.reward
        if self.local(at).weekday() not in (3, 4, 5) or rng.random() >= r.plan_chance:
            return
        pool = list(r.plans) + [f"弄点和{x}有关的事" for x in self._likes(member)[:3]]
        self._expect(s, {"text": f"盼着周末{rng.choice(pool)}", "plan": True}, at)

    def _settle(self, member: str, s: _State, at: datetime) -> None:
        """到期的盼头见分晓：在做的事过了坎 → 兑现；没过 / 小计划没成行 → 落空。"""
        keep = []
        for e in s.expects:
            if datetime.fromisoformat(e["due"]) > at:
                keep.append(e)
                continue
            if e.get("goal"):
                goal = next((g for g in s.goals if g["name"] == e["goal"]), None)
                met = goal is not None and goal["level"] > e.get("level", 0)
            else:
                rng = random.Random(f"brain-plan:{member}:{e['text']}:{e['due']}")
                met = rng.random() < self.rules.reward.met_chance
            tag = "reward" if met else "letdown"
            s.signals.append({"tags": [tag], "at": iso(at), "why": e["text"]})
            what = e["text"].replace("盼着", "")
            if met:
                self._news(s, f"{what}，成了", at, 0.8)
            else:
                self._news(s, f"本来{e['text']}，结果落空了", at, 0.8, neg=True)
        s.expects = keep

    def flush_signals(self, member: str, bus: EventBus, at: datetime) -> int:
        """把攒下的信号发到总线（P3 状态按反射表改心情）。返回发了几个。"""
        s = self._grown(member, self._load(member), at)
        self._settle(member, s, at)
        sent = s.signals
        s.signals = []
        feels = self.rules.psyche.episode.feels
        for sig in sent:
            feel = next((feels[t] for t in sig["tags"] if t in feels), "")
            if feel and sig.get("why"):
                self._episode(s, feel, sig["why"], datetime.fromisoformat(sig["at"]))
        self._save(member, s)
        for sig in sent:
            when = datetime.fromisoformat(sig["at"]) if sig.get("at") else at
            data = {"tags": sig["tags"], "why": sig.get("why", "")}
            bus.publish(Event("brain.signal", when, member, data))
        return len(sent)

    def choose_activity(self, member: str, slot: datetime, place: str, act: str) -> tuple[str, str]:
        """空闲时段：按驱力和目标自己挑去哪、做什么（不再每周照一张表）。

        返回 (房间, 活动)：活动可以是在做的事的名字（场景里照原样写）。不是空闲时段照原样返回。"""
        if not (self.enabled() and self.rules.free_choice) or act not in FREE_ACTS:
            return place, act
        rng = random.Random(f"brain-act:{member}:{slot.isoformat()}")
        s = self._grown(member, self._load(member), slot)
        goals = self._goals(member, s)
        self._anticipate_plan(member, s, slot, rng)
        d = s.drives
        cue = self._cue(slot)
        habit = self._habit(s, cue, slot)
        options: list[tuple[float, str]] = [(0.6, "default")]
        if goals:
            options.append((1.5 * d.get("mastery", 0.3), "pursue"))
        options.append((1.5 * d.get("social", 0.3), "company"))
        options.append((1.5 * d.get("novelty", 0.3), "explore"))
        choice = rng.choices([o for _, o in options], [max(w, 0.01) for w, _ in options])[0]
        # 没什么特别想做的（没想练在做的事、没想换新鲜）时，到点了照老样子
        if habit is not None and choice in ("default", "company") and rng.random() < habit[1]:
            choice = "habit"
            place, act = habit[0].split(":", 1)
        if choice == "pursue":
            goal = min(goals, key=lambda g: g["xp"] + rng.random() * 3)
            self._pursue(s, goal, rng, slot, member)
            self._satisfy(s, "pursued")
            place, act = "dorm", f"琢磨「{goal['name']}」"
        elif choice == "company":
            place, act = rng.choice((("lounge", "chat"), ("canteen", "chat")))
        elif choice == "explore":
            fresh = [x for x in EXPLORE if f"{x[0]}:{x[1]}" not in s.places[-4:]] or list(EXPLORE)
            place, act = rng.choice(fresh)
            self._satisfy(s, "explored")
        key = f"{place}:{act}"
        if key in s.places[-3:]:  # 老待在一个地方：闷
            s.drives["novelty"] = round(min(1.0, s.drives.get("novelty", 0.3) + 0.05), 4)
        s.places = (s.places + [key])[-6:]
        self._routine(s, cue, place, act, slot)
        self._save(member, s)
        return place, act

    # --- 习惯回路：同一个钟点反复做同一件事，慢慢成了习惯 --------------------------------

    def _cue(self, at: datetime) -> str:
        """钟点线索：工作日 / 周末 × 早上 / 中午 / 下午 / 晚上 / 深夜（主时区）。"""
        from roundtable.core.member.growth.habits import slot as day_slot

        local = self.local(at)
        return ("周末" if local.weekday() >= 5 else "工作日") + day_slot(local.hour)

    def _strength(self, item: dict[str, Any], at: datetime) -> float:
        r = self.rules.routine
        return psyche.decayed(
            float(item.get("n", 0)), datetime.fromisoformat(item["at"]), at, r.half_life_days * 24
        )

    def _habit(self, s: _State, cue: str, at: datetime) -> tuple[str, float] | None:
        """这个钟点最强的习惯（去处:做什么, 照老样子的概率）；还没成习惯时为 None。"""
        r = self.rules.routine
        if not r.enabled:
            return None
        best = None
        for key, item in s.routines.items():
            when, _, what = key.partition("|")
            if when != cue:
                continue
            n = self._strength(item, at)
            if n >= r.min_strength and (best is None or n > best[1]):
                best = (what, n)
        if best is None:
            return None
        return best[0], min(r.cap, r.per * best[1])

    def _routine(self, s: _State, cue: str, place: str, act: str, at: datetime) -> None:
        """这个钟点去了这里做这件事：习惯的劲儿 +1（先按过了多久淡一点）；
        刚成习惯时记一件新鲜事。"""
        r = self.rules.routine
        if not r.enabled or act not in FREE_ACTS:
            return
        key = f"{cue}|{place}:{act}"
        item = s.routines.get(key)
        before = self._strength(item, at) if item else 0.0
        s.routines.pop(key, None)
        s.routines[key] = {"n": round(before + 1, 3), "at": iso(at)}
        if len(s.routines) > r.keep:
            weakest = min(s.routines, key=lambda k: self._strength(s.routines[k], at))
            s.routines.pop(weakest)
        if before < r.min_strength <= before + 1:
            from roundtable.core.office.apartment import ACTIVITIES, PUBLIC_ROOMS

            where = "在宿舍" if place.startswith("dorm") else f"去{PUBLIC_ROOMS.get(place, place)}"
            what = f"{where}{ACTIVITIES.get(act, act)}"
            self._news(s, r.news.format(when=cue.removeprefix("工作日"), what=what), at, 0.5)

    def habits_of(self, member: str, at: datetime | None = None) -> list[tuple[str, float]]:
        """她现在的习惯（钟点|去处:做什么, 劲儿），强的在前（网页 / 模拟报告用）。"""
        at = at or self.rt.budget.now()
        s = self._load(member)
        r = self.rules.routine
        got = [(k, round(self._strength(v, at), 2)) for k, v in s.routines.items()]
        return sorted([g for g in got if g[1] >= r.min_strength], key=lambda g: -g[1])

    # --- 丘脑：显著性 -------------------------------------------------------------

    def salience(
        self, member: str, text: str, *, named: bool, speaker: str | None = None, at: datetime
    ) -> float:
        w = self.rules.salience
        score = w.get("named", 1.0) if named else 0.0
        if speaker is None:  # 用户说的（群里你说话，大家至少会留意）
            score += w.get("user", 0.0)
        if any(like and like[:2] in text for like in self._likes(member)):
            score += w.get("likes", 0.0)
        if speaker and speaker != member:
            from roundtable.core.member.relation import RelationPlugin  # relation 不依赖本模块

            with contextlib.suppress(Exception):  # 读不到亲近度就不算这一项
                score += w.get("close", 0.0) * RelationPlugin(self.rt).closeness(
                    member, speaker, at
                )
        if any(word in text for word in self.rules.emotion_words):
            score += w.get("emotion", 0.0)
        if _QUESTION.search(text.strip()):
            score += w.get("question", 0.0)
        return round(score, 3)

    # --- 全局工作区：各区交念头，挑一件 ---------------------------------------------

    def thoughts(self, member: str, s: _State, at: datetime, rng: random.Random) -> list[Thought]:
        out: list[Thought] = []
        ps = self.rules.psyche
        for n in s.news:
            strength = float(n.get("strength", 0.7))
            if ps.enabled and n.get("at"):  # 新鲜事慢慢不新鲜，坏事记得更久
                days = ps.news_half_life_days * (ps.negative_slow if n.get("neg") else 1.0)
                since = datetime.fromisoformat(n["at"])
                strength = psyche.decayed(strength, since, at, days * 24)
            out.append(Thought("goal", n["text"], round(strength, 3)))
        from roundtable.core.member.phase import Phase  # 生活阶段：这阵子在忙 / 感冒 / 迷上……

        for text, strength in Phase(self.rt).thoughts(member, at):
            out.append(Thought("goal", text, round(strength, 3)))
        linger = self._lingering(member, s, at)
        if linger:
            e, left, _ = linger
            out.append(Thought("episode", f"还在为「{e['why']}」{e['feel']}", 0.3 + 0.5 * left))
        for p in s.percepts:  # 输入层：节日当天、快到的日子、刚看到的东西（看到的半天就淡了）
            hours = (at - datetime.fromisoformat(p["at"])).total_seconds() / 3600
            if p["kind"] == "sense":
                left = psyche.decayed(1.0, datetime.fromisoformat(p["at"]), at, 6)
            else:
                same_day = (
                    self.local(at).date() == self.local(datetime.fromisoformat(p["at"])).date()
                )
                left = 1.0 if same_day else 0.0
            if left >= 0.3 and hours >= 0:
                out.append(Thought("sense", p["text"], round(0.3 + 0.35 * left, 3)))
        for d in s.dropped[-1:]:  # 认知失调：放下了的事，会跟自己说其实也没那么想做
            if at - datetime.fromisoformat(d["at"]) <= timedelta(days=3):
                why = f"放下「{d['name']}」以后，你跟自己说其实也没那么想做（心里还有点不甘）"
                out.append(Thought("dissonance", why, 0.4))
        for e in s.expects:  # 大事前提心吊胆：快见分晓时有点紧张（过后的松口气、失落由 _settle 给）
            if datetime.fromisoformat(e["due"]) - at <= timedelta(days=1):
                out.append(Thought("expect", f"{e['text']}，快见分晓了，有点紧张", 0.55))
            else:
                out.append(Thought("expect", e["text"], 0.5))
        for g in self._goals(member, s):
            out.append(
                Thought("goal", f"最近在琢磨「{g['name']}」，{self._stage(g['level'])}", 0.35)
            )
        try:
            for h in self.rt.repo.memory_hanging(member)[:3]:
                out.append(Thought("hanging", f"心里挂着：{h['text']}", 0.5))
        except Exception:  # noqa: BLE001 - 记忆表不可用时不算这一区
            pass
        if rng.random() < self.rules.wander_chance:  # 默认模式：走神冒出的念头
            pool = [f"突然想到{x}" for x in self._likes(member)]
            try:
                old = self.rt.repo.memory_items(member, min_importance=2)
                pool += [f"突然想起{x['text'][:30]}" for x in old[-30:]]
            except Exception:  # noqa: BLE001
                pass
            if pool:
                out.append(Thought("wander", rng.choice(pool), 0.45))
        try:
            from roundtable.core.member.state import StatePlugin  # state 不依赖本模块

            mood = StatePlugin(self.rt).mood_of(member, at)
        except Exception:  # noqa: BLE001
            mood = 0.0
        if abs(mood) > 0.35:
            word = "心情挺好" if mood > 0 else "心情不太好"
            out.append(Thought("mood", word, 0.3 + abs(mood)))
        return out

    def musing(self, member: str, at: datetime) -> str:
        """她这阵子心里最挂着的一件自己的事（朋友圈、日记读这个，和聊天是一个人）：
        新鲜事、情绪余波、盼头、想念、节日、刚看到的……只读不写。没开启或没有时为空串。"""
        if not self.enabled():
            return ""
        rng = random.Random(f"brain-musing:{member}:{self.local(at).date().isoformat()}")
        s = self._grown(member, self._load(member), at)
        mine = self.thoughts(member, s, at, rng)
        mine += [Thought("love", t, w) for t, w in self.heart.thoughts(member, at, {member})]
        mine = [t for t in mine if t.source not in ("mood", "wander", "hanging")]
        focus = self.spotlight(mine, rng)
        return focus.text if focus else ""

    @staticmethod
    def spotlight(thoughts: list[Thought], rng: random.Random) -> Thought | None:
        if not thoughts:
            return None
        return max(thoughts, key=lambda t: t.strength * (0.75 + 0.5 * rng.random()))

    # --- 前额叶：挑意图，写成意图卡 -------------------------------------------------

    def _asked(self, last: str, gist: Any, *, named: bool, group: bool) -> str:
        """对方是不是在直接问她："self"（问她自己的事）/ "judge"（让她评价对方）/
        "other"（问别的）/ ""（不是）。"""
        r = self.rules.asked
        if not (r.enabled and last and gist is not None and gist.kind == "question"):
            return ""
        if group and not named:
            return ""
        if any(w in last for w in r.judge_me):
            return "judge"
        return "self" if any(w in last for w in r.about_me) else "other"

    def intent(
        self,
        member: str,
        *,
        last: str = "",
        speaker: str | None = None,
        named: bool = False,
        group: bool = True,
        at: datetime | None = None,
        seed: str = "",
        mode: Mode | str = Mode.LIFE,
        room: str = "",
        present: Iterable[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> IntentCard | None:
        """说话前的综合：返回意图卡；没开启时返回 None（照旧说话）。群里不接时 ``silent``。

        ``room`` 是会话名：用户在这个会话里说出让人担心的话时，进入关心时段，返回关心卡
        （压过平时的一切，大脑分区关着也照样生效，见 ``care.py``）。
        ``present`` 是在场的人（成员 id，用户为 ``"user"``）：不在场的才会想念。
        ``meta`` 是对方那条消息的附件信息（视频、图片、表情包、链接卡片），看懂语境用。"""
        require_life(mode)
        at = at or self.rt.budget.now()
        if room:
            care = Care(self.rt)
            if speaker is None and last:
                care.note(room, last, at)
            level = care.active(room, at)
            if level:
                return IntentCard(member, "care", care.card(level, at), None, 2.0)
        if not self.enabled():
            return None
        to = speaker or (USER if last or room.startswith("dm:") else "")
        rng = random.Random(f"brain:{member}:{iso(at)}:{seed}")
        s = self._grown(member, self._load(member), at)
        traits = self._traits(member)
        try:
            from roundtable.core.member.state import StatePlugin

            reading = StatePlugin(self.rt).read(member, at)
            mood, energy = reading.mood, reading.energy
        except Exception:  # noqa: BLE001
            mood, energy = 0.0, 0.6
        ps = self.rules.psyche
        base_traits = traits
        if ps.enabled:  # 性格天平①：今天、此刻、对这个人
            close = 0.5 if speaker is None else self._closeness(member, speaker, at)
            traits = psyche.sway(
                ps.sway, traits, member, self.local(at), close=close, energy=energy, mood=mood
            )
        sal = self.salience(member, last, named=named, speaker=speaker, at=at) if last else 0.0
        if last and to:
            sal = round(sal + self.heart.bonus(member, to, at), 3)  # 在乎的人说的话更显著
        social = s.drives.get("social", 0.3)
        floor = self.rules.silence_below
        quiet = sal
        if group and not named and room:  # 群聊分寸：没在场、今天说超了、嘴累的更容易潜水
            from roundtable.core.member.floor import Floor

            quiet = sal - Floor(self.rt).penalty(
                member, room, at, self.lingo._names().get(member, "")
            )
        if (
            group
            and not named
            and quiet < floor
            and social < floor + 0.3
            and s.drives.get("express", 0.3) < floor + 0.3
        ):
            self._save(member, s)
            return IntentCard(member, "silent", "", None, sal)
        mine = self.thoughts(member, s, at, rng)
        here = set(present) if present is not None else {to}
        for text, strength in self.heart.thoughts(member, at, here | {member}):
            mine.append(Thought("love", text, strength))
        for text, strength in self.human.thoughts(member, at, here | {member}, rng):
            mine.append(Thought("human", text, strength))
        if last:
            mine.append(Thought("message", last[:40], 0.4 + sal, "msg"))
            if _CLAIM.search(last):
                mine.append(Thought("message", last[:40], 0.3 + sal, "claim"))
            if any(w in last for w in self.rules.empathy.sad_words):
                mine.append(Thought("message", last[:40], 0.6 + sal, "sad"))
        tags: list[str] = []
        opposed, expect = None, ""
        if ps.enabled:
            opposed, expect = self._psyche_thoughts(
                member, s, mine, tags, last, traits, base_traits, at, rng
            )
        has_sad = any(t.kind == "sad" for t in mine)
        has_whim = any(t.kind == "whim" for t in mine)
        noticed = self._noticed(s)
        love = self.heart.effects(member, to, at)
        has_own = any(t.kind == "own" for t in mine)
        has_claim = any(t.kind == "claim" for t in mine)
        weights: dict[str, float] = {}
        for name, rule in self.rules.intents.items():
            if rule.needs == "own" and not has_own:
                continue
            if rule.needs == "claim" and not has_claim:
                continue
            if rule.needs == "sad" and not has_sad:
                continue
            if rule.needs == "whim" and not has_whim:
                continue
            if rule.needs == "last" and not last:
                continue
            if name in ("respond", "ask", "disagree") and not last:
                continue
            w = rule.base
            w += sum(c * (_num(traits.get(t, 3)) - 3) for t, c in rule.traits.items())
            w += sum(c * s.drives.get(d, 0.3) for d, c in rule.drives.items())
            w += rule.mood * mood + rule.energy * (energy - 0.5)
            if name == "disagree" and opposed is not None:  # 三观：和她的立场相反更想反驳
                if opposed[0]:
                    w = (max(w, 0.0) + ps.values.push) * (1 + ps.values.oppose)
                else:
                    w *= ps.values.agree
            w *= love.get(name, 1.0)  # 对说话对象的感情（在乎、心动、委屈、内疚……）
            if s.recent[-2:] == [name, name]:
                w *= 0.4  # 别一直一个套路
            if noticed == name:  # 元认知：察觉到自己最近老这样，压一压
                w *= self.rules.meta.damp
            elif noticed and self.rules.meta.swap.get(noticed) == name:
                w *= 1.5
            weights[name] = max(w, 0.0)
        if not weights or sum(weights.values()) <= 0:
            weights = {"respond": 1.0} if last else {"share": 1.0}
        gist = self.lingo.gist(last, meta) if last else None
        if gist is not None and (gist.quick or gist.sticker):  # 别人分享东西：人一般先给反应
            for name in QUICK_INTENTS & set(weights):
                weights[name] *= self.lingo.rules.react_boost
        asked = self._asked(last, gist, named=named, group=group)
        if asked:  # 被直接问到：先回答，不岔开（问的是她自己的事时也可以讲自己的事）
            ra = self.rules.asked
            keep = {"self": ra.self_intents, "judge": ra.judge_intents}.get(asked, ra.intents)
            weights = {k: v for k, v in weights.items() if k in keep and v > 0} or {"respond": 1.0}
            tags.append(f"asked:{asked}")
        chosen = rng.choices(list(weights), list(weights.values()))[0]
        pool = mine
        if chosen in ("share", "shift", "vent"):
            pool = [t for t in mine if t.kind == "own"] or mine
            if chosen == "vent":
                pool = [t for t in pool if t.source == "mood"] or pool
        elif chosen == "disagree":
            pool = [t for t in mine if t.kind == "claim"] or mine
        elif chosen == "comfort":
            pool = [t for t in mine if t.kind == "sad"] or mine
        elif chosen == "blurt":
            pool = [t for t in mine if t.kind == "whim"] or mine
        elif last:
            pool = [t for t in mine if t.kind != "own"] or mine
        focus = self.spotlight(pool, rng)
        text = self._card(chosen, focus, mood, energy, s, rng)
        if asked:
            ra = self.rules.asked
            line = {"self": ra.self_line, "judge": ra.judge_line}.get(asked, ra.line)
            text = f"{text}\n{line}"
        # 说话的样子：看懂了什么（别说出来）、此刻怎么说话（变调、叫法、斗图）
        if gist is not None and gist.kind != "chat":
            tags.append(f"gist:{gist.kind}")
        notice = self.lingo.notice(gist)
        if notice:
            text = f"{text}\n{notice}"
        to_view = self.heart.view(member, to, at) if to else {}
        battle = (
            group
            and gist is not None
            and gist.kind == "sticker"
            and _num(base_traits.get("幽默", 3)) >= 4
            and rng.random() < self.lingo.rules.battle
        )
        how = self.lingo.card_line(
            member,
            to,
            mood=mood,
            energy=energy,
            care=float(to_view.get("care", 0.0)),
            dislike=float(to_view.get("dislike", 0.0)),
            at=at,
            rng=rng,
            battle=battle,
        )
        if how:
            text = f"{text}\n此刻说话：{how}"
        # 附加提示（察觉、感情、特质）：最多 max_cues 句；说出口过的隔 cooldown_cards 张卡才再用
        budget, cues = self.rules.max_cues, []
        cool = set(s.used[-self.rules.cooldown_cards :]) if self.rules.cooldown_cards else set()
        if (
            noticed and noticed != s.noticed and budget > 0
        ):  # 刚察觉到就写一句（真说了才算，见 after）
            note = self.rules.meta.notes.get(noticed, "")
            text = f"{text}\n你察觉到自己{note}，想换个方式"[: self.rules.card_chars]
            budget -= 1
        heart = self.heart.line(member, to, at, rng, last=last if speaker is None else "")
        if heart and (
            heart[0] in URGENT or (budget > 0 and _cue_key(heart[0], heart[1]) not in cool)
        ):  # 关于说话对象的一句感情话（放人那句一定写上）
            text = f"{text}\n{heart[1]}"
            tags.append(f"love:{heart[0]}")
            cues.append(_cue_key(heart[0], heart[1]))
            budget = max(budget - 1, 0)
        lean = ""
        if opposed is not None and opposed[0] and to:
            lean = f"{opposed[2]}|{opposed[3]}"  # 三观慢慢变：说出口后，在乎对方就往那边挪一点
            if to != USER:
                tags.append("clash")
        lines = []
        if ps.enabled:
            lines = self._psyche_lines(
                member, s, chosen, last, mood, energy, traits, opposed, at, rng
            )[: ps.extra_lines]
        hurt = bool(to) and self.heart.view(member, to, at).get("hurt", 0) >= self.heart.rules.on
        meme = self.memes.line(  # 用自己的随机数，不打乱别的区
            member,
            last=last,
            chosen=chosen,
            hurt=hurt,
            at=at,
            rng=random.Random(f"meme:{member}:{iso(at)}:{seed}"),
        )
        if meme is not None:  # 网络梗：对方玩梗就接住（不懂就问），平时偶尔提一句最近在玩的梗
            text = f"{text}\n{meme[1]}"
            tags.append(meme[0])
        else:  # 情境说法本：按场合和性格提一条说话招式（只写逻辑），或你常说的短句
            phrase = self.phrases.line(
                member,
                last=last,
                gist=gist.kind if gist is not None else "",
                chosen=chosen,
                traits=traits,
                care=self.heart.care(member, USER, at),
                hurt=hurt,
                at=at,
                rng=random.Random(f"phrase:{member}:{iso(at)}:{seed}"),
                first=to == USER and self._exchanges(member) < self.phrases.rules.first_exchanges,
            )
            if phrase is not None:
                text = f"{text}\n{phrase[1]}"
                tags.append(phrase[0])
        circle = self.circle.line(member, last)
        if circle:  # 对方提到她说过的圈外人（室友、老同学……）：照之前说的来
            text = f"{text}\n{circle}"
            tags.append("circle")
        first = (
            self.firsts.line(member, at, random.Random(f"firsts:{member}:{iso(at)}:{seed}"))
            if to == USER
            else None
        )
        if first is not None:
            text = f"{text}\n{first[1]}"
            tags.append(first[0])
        extra = self.human.lines(
            member,
            to=to,
            last=last,
            chosen=chosen,
            room=room,
            energy=energy,
            social=s.drives.get("social", 0.3) + (0.2 if self._festive(s, at) else 0.0),
            spotlight=focus,
            hurt=hurt,
            at=at,
            rng=rng,
        )
        first = [x for x in extra if x[0] in FIRST]
        if self.feel.enabled():  # 此刻在场的情绪：她自己的理解 + 上回真有这种情绪是为了什么
            hints = self.feel.hints(
                heart=heart[0] if heart else "",
                tense=any(t.source == "expect" and t.strength >= 0.55 for t in mine),
                novelty=s.drives.get("novelty", 0.3),
            )
            if heart and heart[0] == "jealous":  # 吃醋没有别处记：写进卡时就算有过一次
                self.feel.record(
                    member, self.feel.rules.love["jealous"], heart[1].split("，")[0], at
                )
            emos = self.feel.active(member, mood=mood, last=last, at=at, hints=hints)
            felt = self.feel.line(member, emos, at)
            if felt:
                first.insert(0, ("feel:mix" if "\n" in felt else "feel", felt))
        rest = lines + [x for x in extra if x[0] not in FIRST]
        rng.shuffle(rest)
        for tag, line in first + rest:  # 人味里要紧的先给，其余和特质句一起随机
            if budget <= 0:
                break
            key = _cue_key(tag, line)
            if key in cool or len(text) + 1 + len(line) > self.rules.card_chars:
                continue
            text += "\n" + line
            tags.append(tag)
            cues.append(key)
            budget -= 1
        quick = ""
        if chosen in QUICK_INTENTS and not hurt:  # 万能短回复：只回一句，程序直接发
            quick = self.lingo.quick(
                member,
                gist,
                energy=energy,
                care=float(to_view.get("care", 0.0)),
                quiet=_num(base_traits.get("外向", 3)) <= 2,
                rng=rng,
            )
            if quick:
                tags.append("quick")
        if meme is not None and meme[2] and not asked:  # 上句 → 接法：懂的人直接接，不调模型
            quick = meme[2]
            tags.append("quick")
        self._save(member, s)  # 只存随时间长的驱力；候选卡的其余影响等 after 再结算
        return IntentCard(
            member,
            chosen,
            text,
            focus,
            sal,
            tuple(tags),
            noticed,
            expect,
            to,
            tuple(cues),
            lean,
            quick,
        )

    def _closeness(self, member: str, other: str, at: datetime) -> float:
        from roundtable.core.member.relation import RelationPlugin  # relation 不依赖本模块

        try:
            return float(RelationPlugin(self.rt).closeness(member, other, at))
        except Exception:  # noqa: BLE001 - 读不到亲近度就当普通关系
            return 0.0

    def _exchanges(self, member: str) -> int:
        """她和你一来一回了几次（关系插件记的）；读不到当聊过很多（不算第一次见）。"""
        from roundtable.core.member.relation import RelationPlugin  # relation 不依赖本模块

        try:
            return int((RelationPlugin(self.rt).data(member)["user"] or {}).get("n", 0))
        except Exception:  # noqa: BLE001
            return 10**6

    def _psyche_thoughts(
        self,
        member: str,
        s: _State,
        mine: list[Thought],
        tags: list[str],
        last: str,
        traits: dict[str, float],
        base_traits: dict[str, Any],
        at: datetime,
        rng: random.Random,
    ) -> tuple[tuple[bool, str, str, int] | None, str]:
        """人的特质交上来的念头：突发念头、发散联想、被勾起的旧记忆。

        返回（三观判断，说干就干的盼头）。三观判断是 (和她立场相反?, 她偏的那端)，没表态时 None；
        盼头只在这张卡真说出口后才记（``after``）。"""
        ps = self.rules.psyche
        w = ps.whim
        expect = ""
        chance = w.chance + sum(c * (traits.get(t, 3) - 3) for t, c in w.traits.items())
        pool = psyche.personal(w.pool, member, "whim", w.share)  # 每位分到的不一样
        pool += [f"突然想{x}" for x in self._likes(member) if x]
        if pool and rng.random() < chance:
            whim = rng.choice(pool)
            mine.append(Thought("whim", whim, 0.7, "whim"))
            tags.append("whim")
            doable = whim.startswith("突然想") and not whim.startswith("突然想起")
            if doable and rng.random() < w.act_chance:  # 说干就干：说出口后记成一个小盼头
                expect = "盼着" + whim.removeprefix("突然想")
        if not last:
            return None, expect
        personal = self._likes(member) + [g["name"] for g in s.goals]
        hop = psyche.associate(ps.assoc, last, personal, traits.get("好奇", 3.0), rng)
        if hop:
            mine.append(Thought("assoc", f"由「{hop[0]}」想到{hop[1]}", 0.45))
            tags.append("assoc")
        try:
            old = self.rt.repo.memory_items(member, min_importance=2)[-30:]
            memories = [str(m["text"]) for m in old]
        except Exception:  # noqa: BLE001
            old, memories = [], []
        cue = psyche.cue_memory(ps.assoc, last, memories, rng)
        if cue:
            text = f"被「{cue[0]}」勾起：{cue[1][:30]}"
            day = next((str(m.get("day") or "") for m in old if m["text"] == cue[1]), "")
            if day and rng.random() < ps.assoc.drift_chance:  # 记忆走样：太久的事细节会记岔
                age = (self.local(at).date() - datetime.fromisoformat(day[:10]).date()).days
                if age > ps.assoc.drift_days:
                    text += "（记得不太清了，细节可能有出入）"
                    tags.append("drift")
            mine.append(Thought("cue", text, 0.55))
            tags.append("cue")
        if not _CLAIM.search(last):
            return None, expect
        values = json.dumps(
            self.profiles.current(member).data.get("values") or {}, ensure_ascii=False
        )
        for axis in ps.values.axes:
            side = psyche.lean(axis, last)
            if not side:
                continue
            st = psyche.stance(axis, base_traits, values, member) + s.shifts.get(axis.a, 0.0)
            if abs(st) >= ps.values.firm:
                return (st * side < 0, axis.a if st > 0 else axis.b, axis.a, side), expect
        return None, expect

    def _psyche_lines(
        self,
        member: str,
        s: _State,
        chosen: str,
        last: str,
        mood: float,
        energy: float,
        traits: dict[str, float],
        opposed: tuple[bool, str, str, int] | None,
        at: datetime,
        rng: random.Random,
    ) -> list[tuple[str, str]]:
        """写进意图卡的特质句：矛盾、情绪余波（或心情归错因）、三观、先果后因；随机挑几句。"""
        ps = self.rules.psyche
        lines: list[tuple[str, str]] = []
        ten = psyche.tensions(ps.ambivalence, s.drives, energy, mood, traits)
        if ten:
            lines.append(("ambivalence", f"心里有点矛盾：{ten[0][0]}，{ten[0][1]}"))
        linger = self._lingering(member, s, at)
        if linger:
            e, _, how = linger
            line = f"还在为「{e['why']}」{e['feel']}"
            if how:
                line += "；" + ps.episode.strategies[how].line
            lines.append(("episode", line))
        elif last and (energy < 0.3 or mood < -0.3) and rng.random() < ps.confab.misattribute:
            cause = "累了" if energy < 0.3 else "心情本来就不好"
            lines.append(
                ("misattr", f"你有点烦躁，会下意识觉得是眼前这事烦人（其实是{cause}，你没意识到）")
            )
        if opposed is not None:
            fit = "不一样" if opposed[0] else "挺合"
            lines.append(("values", f"这话和你的看法{fit}（你更偏「{opposed[1]}」）"))
        reasons = ps.confab.reasons.get(chosen)
        if reasons and rng.random() < ps.confab.chance:
            why = rng.choice(reasons)
            lines.append(("confab", f"你没细想就想这么做；被问起会说「{why}」，不一定是真原因"))
        rng.shuffle(lines)
        return lines  # 条数和冷却由 intent 把关

    def _noticed(self, s: _State) -> str:
        """元认知：最近 window 次里同一个套路（有记的那几种）出现 ≥ threshold 次。"""
        r = self.rules.meta
        recent = s.recent[-r.window :]
        for name in r.notes:
            if recent.count(name) >= r.threshold:
                return name
        return ""

    def _card(
        self,
        intent: str,
        focus: Thought | None,
        mood: float,
        energy: float,
        s: _State,
        rng: random.Random,
    ) -> str:
        feel = "心情不错" if mood > 0.3 else "有点低落" if mood < -0.3 else "心情平常"
        tired = "有点累" if energy < 0.3 else "精神挺好" if energy > 0.7 else "精力一般"
        lines = [f"此刻：{feel}，{tired}"]
        top = max(s.drives.items(), key=lambda kv: kv[1], default=None)
        if top and top[1] >= 0.6 and top[0] in self.rules.drives:
            lines[0] += f"；{self.rules.drives[top[0]].label}"
        rule = self.rules.intents.get(intent)
        lines.append(f"你想：{rule.label if rule else intent}")
        if focus is not None:
            what = "你注意到" if focus.kind != "own" else "想说的事"
            lines.append(f"{what}：{focus.text}")
            if focus.source == "goal":  # 目标是从人物卡里摘的原句：说出口要换成眼下具体的一小步
                lines.append(self.rules.goals.say_line)
        if rng.random() < self.rules.imperfect:
            lines.append(
                rng.choice(
                    ("有记不清的地方就直说记不清，不用硬圆", "可以只回一两个字，不用每句都说圆")
                )
            )
        return "\n".join(lines)[: self.rules.card_chars]

    def after(self, card: IntentCard | None, at: datetime) -> None:
        """真说了（或真决定不接）才回传：记下这次的意图；讲过的新鲜事不再新鲜。
        说了话的驱力下降由总线上的 chat.message 结算。"""
        if card is None:
            return
        s = self._grown(card.member, self._load(card.member), at)
        s.counts[card.intent] = s.counts.get(card.intent, 0) + 1
        for tag in card.tags:
            s.psy[tag] = s.psy.get(tag, 0) + 1
        if not card.silent:
            s.recent = (s.recent + [card.intent])[-max(6, self.rules.meta.window) :]
            s.noticed = card.noticed
            keep = max(12, 2 * self.rules.cooldown_cards)
            s.used = (s.used + list(card.cues))[-keep:]
            vr = self.rules.psyche.values
            if card.lean and self.heart.care(card.member, card.to, at) >= vr.shift_care:
                axis, side = card.lean.rsplit("|", 1)
                moved = s.shifts.get(axis, 0.0) + int(side) * vr.shift_step
                s.shifts[axis] = round(max(-vr.shift_cap, min(vr.shift_cap, moved)), 3)
            if card.expect:
                self._expect(s, {"text": card.expect, "plan": True}, at)
        if card.spotlight is not None and card.spotlight.source == "goal":
            s.news = [n for n in s.news if n["text"] != card.spotlight.text]
        self._save(card.member, s)
        self.heart.after(card, at)
        self.human.after(card, at)
        if not card.silent:
            self.memes.after(card.member, card.tags, at)
            self.phrases.after(card.member, card.tags, at)
            self.firsts.after(card.member, card.tags)


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 3.0


def seeds(growth: str) -> list[str]:
    """从人物卡「成长」章取在做的事：小愿望一件 + 「最可能改变」的几条。

    人物卡是各位自己写的，格式不一（粗体、编号、「第一，」写在一行里），这里尽量宽松地认。"""
    text = growth.replace("**", "")
    out: list[str] = []
    wish = re.search(r"小愿望[^：:，,\n]*[：:，,]\s*(?:是)?\s*\n?(.+)", text)
    if wish:
        out.append(_clause(re.sub(r"^(希望有一天|希望|我希望|想要|我想)[，,]?", "", wish.group(1))))
    block = re.search(r"最可能[^\n]*?改变(.*?)(?:不会变|$)", text, re.S)
    items = re.findall(
        r"(?:第[一二三四五][，,、]|\n\s*\d+[.、]|\n\s*[-•])\s*(.+?)(?=第[一二三四五][，,、]|\n|$)",
        block.group(1) if block else "",
    )
    for item in items:
        head = re.match(r"^([^：:]{2,10})[：:]", item)
        out.append(_clause(head.group(1) if head else item))
    keep = (x for x in out if len(x) >= 4 and not re.search(r"愿望|无论|不会变", x))
    return list(dict.fromkeys(keep))


_SETTING = re.compile(r"(的时候|时|旁边|之前|之后|以前|以后|那天|夜里)$")
_JOIN = re.compile(r"^(慢慢变|变成|攒到)")


def _clause(text: str) -> str:
    """一句话里最像「要做的事」的那一小句（跳过「从……」「某天夜里」「……的时候」这类场景），
    去掉「我会」之类；话被逗号拆开时（「把那本子，攒到……」「从……，慢慢变成……」）接上后半句。"""
    parts = [p.strip(' “”"') for p in re.split(r"[，,。；;！!]", text.strip()) if p.strip()]
    for i, part in enumerate(parts):
        if len(part) < 4 or re.match(r"^(从|某天|有一天|今年|明年|有时)", part):
            continue
        if _SETTING.search(part) and i + 1 < len(parts):
            continue
        nxt = parts[i + 1] if i + 1 < len(parts) else ""
        if nxt and (part.startswith("把") or "从" in part or _JOIN.match(nxt)):
            part = re.sub(r"^慢慢", "", part + nxt) if _JOIN.match(nxt) else part + nxt
            part = re.sub(r"从(.+?)慢慢变成", r"从\1变成", part)
        part = re.sub(r"^(我会|我可能会|可能会|我可能|可能|慢慢|我的|我)", "", part)
        return part.strip()[:28]
    return ""


def intent_text(card: IntentCard | None, none: str) -> str:
    return card.text if card is not None and card.text else none
