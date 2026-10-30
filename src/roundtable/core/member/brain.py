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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.mode import Mode, require_life
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


@dataclass(frozen=True)
class Thought:
    source: str  # goal / hanging / wander / mood / message
    text: str
    strength: float
    kind: str = "own"  # own：她自己的事；msg：别人刚说的；claim：别人说了个看法


@dataclass(frozen=True)
class IntentCard:
    member: str
    intent: str  # respond / ask / share / … / silent
    text: str  # 写进提示词 <intent> 的那一段
    spotlight: Thought | None = None
    salience: float = 0.0

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


def _factor(score: Any) -> float:
    try:
        return max(0.2, 1 + 0.2 * (float(score) - 3))
    except (TypeError, ValueError):
        return 1.0


class Brain:
    def __init__(
        self, rt: Any, bus: EventBus | None = None, buffer: dict[str, str] | None = None
    ) -> None:
        self.rt = rt
        self.profiles = ProfileStore(rt)
        # buffer：先写在内存里、``flush()`` 时才落库（生活排日程时用，排出来的事真发生了才算数，
        # 同一段时间排两次结果一样）
        self.buffer = buffer
        if bus is not None:
            bus.subscribe("chat.message", self.on_event)
            bus.subscribe("life.event", self.on_event)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.brain

    def enabled(self) -> bool:
        return bool(self.rt.config.roundtable.chat.engine.enabled and self.rules.enabled)

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
        if event.kind == "chat.message" and event.member:
            self.satisfy(event.member, "spoke", event.at)
        elif (
            event.kind == "life.event"
            and event.member
            and event.data.get("kind") in ("gather", "pair")
        ):
            self.satisfy(event.member, "gathered", event.at)

    # --- 前额叶：长线目标 ---------------------------------------------------------

    def _goals(self, member: str, s: _State) -> list[dict[str, Any]]:
        """在做的事：先从人物卡「成长」章里的小愿望和「最可能改变的地方」里取，没有再用喜好。"""
        if not s.goals and self.rules.goals.max_active:
            pool = seeds(str((self.profiles.current(member).data.get("values") or {}).get("成长")))
            pool += [f"和{x}有关的小计划" for x in self._likes(member)]
            pool = pool or list(self.rules.goals.fallback)
            rng = random.Random(f"brain-goals:{member}")
            first, rest = pool[:1], pool[1:]  # 小愿望排第一，其余随机
            rng.shuffle(rest)
            s.goals = [
                {"name": g, "xp": 0.0, "level": 0, "stuck": 0}
                for g in (first + rest)[: self.rules.goals.max_active]
            ]
        return s.goals

    def _stage(self, level: int) -> str:
        stages = self.rules.goals.stages
        return stages[min(level, len(stages) - 1)]

    def _pursue(self, s: _State, goal: dict[str, Any], rng: random.Random, at: datetime) -> str:
        """做一次在做的事：涨经验，可能卡住，可能过一个坎（记成没讲过的新鲜事）。"""
        r = self.rules.goals
        if rng.random() < r.setback_chance:
            goal["stuck"] = goal.get("stuck", 0) + 1
            note = f"「{goal['name']}」这件事最近没什么进展，有点泄气"
            self._news(s, note, at, 0.6)
            return note
        goal["xp"] = round(goal["xp"] + rng.uniform(*r.gain), 2)
        goal["stuck"] = 0
        while goal["level"] < len(r.levels) and goal["xp"] >= r.levels[goal["level"]]:
            goal["level"] += 1
            self._news(s, f"「{goal['name']}」{self._stage(goal['level'])}", at, 0.9)
        return f"在{goal['name']}（{self._stage(goal['level'])}）"

    def _news(self, s: _State, text: str, at: datetime, strength: float) -> None:
        s.news.append({"text": text, "at": iso(at), "strength": strength})
        s.news = s.news[-KEEP_NEWS:]

    def choose_activity(self, member: str, slot: datetime, place: str, act: str) -> tuple[str, str]:
        """空闲时段：按驱力和目标自己挑去哪、做什么（不再每周照一张表）。

        返回 (房间, 活动)：活动可以是在做的事的名字（场景里照原样写）。不是空闲时段照原样返回。"""
        if not (self.enabled() and self.rules.free_choice) or act not in FREE_ACTS:
            return place, act
        rng = random.Random(f"brain-act:{member}:{slot.isoformat()}")
        s = self._grown(member, self._load(member), slot)
        goals = self._goals(member, s)
        d = s.drives
        options: list[tuple[float, str]] = [(0.6, "default")]
        if goals:
            options.append((1.5 * d.get("mastery", 0.3), "pursue"))
        options.append((1.5 * d.get("social", 0.3), "company"))
        options.append((1.5 * d.get("novelty", 0.3), "explore"))
        choice = rng.choices([o for _, o in options], [max(w, 0.01) for w, _ in options])[0]
        if choice == "pursue":
            goal = min(goals, key=lambda g: g["xp"] + rng.random() * 3)
            self._pursue(s, goal, rng, slot)
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
        self._save(member, s)
        return place, act

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
        for n in s.news:
            out.append(Thought("goal", n["text"], float(n.get("strength", 0.7))))
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

    @staticmethod
    def spotlight(thoughts: list[Thought], rng: random.Random) -> Thought | None:
        if not thoughts:
            return None
        return max(thoughts, key=lambda t: t.strength * (0.75 + 0.5 * rng.random()))

    # --- 前额叶：挑意图，写成意图卡 -------------------------------------------------

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
    ) -> IntentCard | None:
        """说话前的综合：返回意图卡；没开启时返回 None（照旧说话）。群里不接时 ``silent``。"""
        require_life(mode)
        if not self.enabled():
            return None
        at = at or self.rt.budget.now()
        rng = random.Random(f"brain:{member}:{iso(at)}:{seed}")
        s = self._grown(member, self._load(member), at)
        traits = self._traits(member)
        try:
            from roundtable.core.member.state import StatePlugin

            reading = StatePlugin(self.rt).read(member, at)
            mood, energy = reading.mood, reading.energy
        except Exception:  # noqa: BLE001
            mood, energy = 0.0, 0.6
        sal = self.salience(member, last, named=named, speaker=speaker, at=at) if last else 0.0
        social = s.drives.get("social", 0.3)
        floor = self.rules.silence_below
        if (
            group
            and not named
            and sal < floor
            and social < floor + 0.3
            and s.drives.get("express", 0.3) < floor + 0.3
        ):
            self._save(member, s)
            return IntentCard(member, "silent", "", None, sal)
        mine = self.thoughts(member, s, at, rng)
        if last:
            mine.append(Thought("message", last[:40], 0.4 + sal, "msg"))
            if _CLAIM.search(last):
                mine.append(Thought("message", last[:40], 0.3 + sal, "claim"))
        has_own = any(t.kind == "own" for t in mine)
        has_claim = any(t.kind == "claim" for t in mine)
        weights: dict[str, float] = {}
        for name, rule in self.rules.intents.items():
            if rule.needs == "own" and not has_own:
                continue
            if rule.needs == "claim" and not has_claim:
                continue
            if name in ("respond", "ask", "disagree") and not last:
                continue
            w = rule.base
            w += sum(c * (_num(traits.get(t, 3)) - 3) for t, c in rule.traits.items())
            w += sum(c * s.drives.get(d, 0.3) for d, c in rule.drives.items())
            w += rule.mood * mood + rule.energy * (energy - 0.5)
            if s.recent[-2:] == [name, name]:
                w *= 0.4  # 别一直一个套路
            weights[name] = max(w, 0.0)
        if not weights or sum(weights.values()) <= 0:
            weights = {"respond": 1.0} if last else {"share": 1.0}
        chosen = rng.choices(list(weights), list(weights.values()))[0]
        pool = mine
        if chosen in ("share", "shift", "vent"):
            pool = [t for t in mine if t.kind == "own"] or mine
            if chosen == "vent":
                pool = [t for t in pool if t.source == "mood"] or pool
        elif chosen == "disagree":
            pool = [t for t in mine if t.kind == "claim"] or mine
        elif last:
            pool = [t for t in mine if t.kind != "own"] or mine
        focus = self.spotlight(pool, rng)
        text = self._card(chosen, focus, mood, energy, s, rng)
        self._save(member, s)
        return IntentCard(member, chosen, text, focus, sal)

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
        if not card.silent:
            s.recent = (s.recent + [card.intent])[-6:]
        if card.spotlight is not None and card.spotlight.source == "goal":
            s.news = [n for n in s.news if n["text"] != card.spotlight.text]
        self._save(card.member, s)


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


def _clause(text: str) -> str:
    """一句话里最像「要做的事」的那一小句（跳过「从……」「某天夜里」这类开头），去掉「我会」之类。"""
    parts = [p.strip(' “”"') for p in re.split(r"[，,。；;！!]", text.strip()) if p.strip()]
    for part in parts:
        if len(part) < 4 or re.match(r"^(从|某天|有一天|今年|明年|有时)", part):
            continue
        part = re.sub(r"^(我会|我可能会|可能会|我可能|可能|慢慢|我的|我)", "", part)
        return part.strip()[:20]
    return ""


def intent_text(card: IntentCard | None, none: str) -> str:
    return card.text if card is not None and card.text else none
