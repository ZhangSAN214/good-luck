"""爱与复合情绪（docs/BRAIN.md「爱」；CLAUDE.md §5.1 第 2、6 条）。

她对每个人（每位同事、用户）各有一组感情，不是一个分数，也可以不对称：

- **在乎**（care）、**心动**（spark）、**信任**（trust）：从相处里慢慢长。
  接住对方的话、自己的话被接住、一起笑（正向共鸣，Fredrickson）、
  一起去没去过的地方（自我扩展，Aron）、安慰与被安慰、说心事都会让它长；
  每段关系每天涨幅有上限，不来往会慢慢淡。**亲密**读 P4 的亲近度（一起经历过多少）；
  **承诺**由在乎、亲密、信任、委屈算（Rusbult 投入模型）。
  三样的高低决定是哪种感情（Sternberg 爱情三角）。
- **心动**看两人的「化学反应」（由两人的 id 派生的随机数，有方向）：低于门槛就不会心动，
  有人一直只是朋友，也可能单相思。同事之间、对用户都可以，各有开关（2026-10-30 用户定）。
- **会过去的感情**：委屈（被当面反驳、主动找对方没人接）、感激（被安慰过）、
  内疚（对在乎的人说话冲了）、看不惯（三观相反、被反驳），各按半衰期消退；
  承诺越高，委屈消得越快（原谅）。
- **复合情绪 = 感情 × 发生的事**：心疼（在乎的人难过，共情更容易动）、
  思念（在乎的人好几天没说上话）、替对方高兴或揪心（对方讲了自己的好事坏事）、
  吃醋（在乎的人老和别的同事在一起）、爱恨交织（在乎又介意）。
- **各人不同**：依恋方式（安全 / 焦虑 / 回避，Hazan & Shaver）和爱的表达方式
  （说出口 / 用行动 / 互损 / 照顾 / 靠谱）按性格分；只给用户的偏心，每位按性格挑两种。
- **对用户的边界**（CLAUDE.md §5.1 第 2 条）：可以挽留，但只留一次，
  用户说要睡觉、上课、工作、陪家人朋友时痛快放人；可以怪用户没来，但一两句就消气、
  不翻旧账；从不因为用户没来、没回记委屈；不吃用户现实里朋友家人的醋（吃醋只对同事）；
  关心时段（``care.py``）里这些全停。

只写自己的键（``member_heart:<成员>``、``member_heart.tracker``，不加迁移）；
读 P4 亲近度、自我档案。只用于生活模式；8 位同一套规则（``chat.engine.brain.love``），不认人。
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.profile import ProfileStore
from roundtable.core.storage.repository import iso

KEY = "member_heart:"
TRACKER = "member_heart.tracker"
USER = "user"
FEELINGS = ("hurt", "grateful", "guilt", "dislike")
KEEP_FIRSTS = 12
KEEP_DAYS = 14
KEEP_VICARIOUS = 4
SAD_HINTS = ("落空", "泄气", "没什么进展", "卡住")
GOODBYE = {"睡觉": "晚安", "看病": "早点好起来"}
_QUESTION = re.compile(r"[？?]|(吗|呢|么)[。！!～~]*$")


def _num(x: Any, default: float = 3.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _decay(value: float, since: str, at: datetime, half_life_days: float) -> float:
    if not since or not value:
        return float(value or 0.0)
    days = max((at - datetime.fromisoformat(since)).total_seconds() / 86400, 0.0)
    return float(value) * 0.5 ** (days / max(half_life_days, 0.01))


class Heart:
    """爱与复合情绪。传了 bus 时订阅聊天和生活事件；只写自己的键。"""

    def __init__(
        self,
        rt: Any,
        bus: EventBus | None = None,
        names: Callable[[], dict[str, str]] | dict[str, str] | None = None,
    ) -> None:
        self.rt = rt
        self.names = names
        self.profiles = ProfileStore(rt)
        if bus is not None:
            bus.subscribe("chat.message", self.on_event)
            bus.subscribe("life.event", self.on_event)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.brain.love

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and engine.brain.enabled and self.rules.enabled)

    def _names(self) -> dict[str, str]:
        if callable(self.names):
            return dict(self.names())
        return dict(self.names or {})

    def name(self, other: str) -> str:
        return self.rules.user_name if other == USER else self._names().get(other, other)

    def local_day(self, at: datetime) -> str:
        return home_zone(self.rt).local(at).date().isoformat()

    # --- 存取（只有这里写 member_heart:*）------------------------------------------

    def _load(self, member: str) -> dict[str, Any]:
        raw = self.rt.repo.setting(KEY + member)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        data.setdefault("bonds", {})
        data.setdefault("vicarious", [])
        return data

    def _save(self, member: str, data: dict[str, Any]) -> None:
        self.rt.repo.set_setting(KEY + member, json.dumps(data, ensure_ascii=False))

    def clear(self, member: str) -> None:
        self.rt.repo.set_setting(KEY + member, "")

    def _new_bond(self) -> dict[str, Any]:
        bond: dict[str, Any] = {
            "care": 0.0,
            "spark": 0.0,
            "trust": self.rules.trust_start,
            "at": "",
            "seen": "",
            "moments": 0,
            "firsts": [],
            "daily": {},
            "marks": {},
        }
        for f in FEELINGS:
            bond |= {f: 0.0, f"{f}_at": "", f"{f}_why": ""}
        return bond

    def _bond(self, data: dict[str, Any], other: str) -> dict[str, Any]:
        bonds = data["bonds"]
        if other not in bonds:
            bonds[other] = self._new_bond()
        return bonds[other]

    # --- 各人的样子（按性格分，不认人）----------------------------------------------

    def _traits(self, member: str) -> dict[str, Any]:
        data = self.profiles.current(member).data
        return dict((data.get("personality") or {}).get("五项") or {})

    def _pick(self, options: dict[str, Any], member: str, salt: str) -> str:
        if not options:
            return ""
        traits = self._traits(member)
        rng = random.Random(f"heart-{salt}:{member}")
        scored = {
            name: getattr(o, "base", 0.0)
            + sum(c * (_num(traits.get(t, 3)) - 3) for t, c in o.traits.items())
            + rng.random() * 0.1
            for name, o in options.items()
        }
        return max(scored, key=lambda n: scored[n])

    def attachment(self, member: str) -> str:
        """依恋方式：secure / anxious / avoidant（配置里的名字）。"""
        return self._pick(self.rules.attachment, member, "attach")

    def style(self, member: str) -> str:
        """爱的表达方式：words / acts / tease / tending / reliable（配置里的名字）。"""
        return self._pick(self.rules.styles, member, "style")

    def privileges(self, member: str) -> list[str]:
        """只给用户的偏心：按性格挑两种（加一点个人差异）。"""
        traits = self._traits(member)
        rng = random.Random(f"heart-privilege:{member}")
        scored = [
            (
                sum(c * (_num(traits.get(t, 3)) - 3) for t, c in p.traits.items())
                + rng.random() * 0.4,
                p,
            )
            for p in self.rules.privileges
        ]
        scored.sort(key=lambda x: -x[0])
        return [p.line for _, p in scored[:2]]

    def chemistry(self, member: str, other: str) -> float:
        """两人的「化学反应」（0～1）：六成看这一对，四成看她这边（可以单相思）。"""
        a, b = sorted((member, other))
        pair = random.Random(f"heart-chem:{a}:{b}").random()
        own = random.Random(f"heart-chem:{member}>{other}").random()
        return round(0.6 * pair + 0.4 * own, 4)

    def _spark_factor(self, member: str, other: str) -> float:
        r = self.rules
        if not (r.romance_user if other == USER else r.romance):
            return 0.0
        floor = r.chemistry_floor
        if floor >= 1:
            return 0.0
        return max(0.0, (self.chemistry(member, other) - floor) / (1 - floor))

    # --- 读：此刻的感情 -------------------------------------------------------------

    def _intimacy(self, member: str, other: str, at: datetime) -> float:
        from roundtable.core.member.relation import RelationPlugin  # relation 不依赖本模块

        try:
            return float(RelationPlugin(self.rt).closeness(member, other, at))
        except Exception:  # noqa: BLE001 - 读不到亲近度就当没经历过
            return 0.0

    def _view(self, member: str, other: str, b: dict[str, Any], at: datetime) -> dict[str, Any]:
        r, hl = self.rules, self.rules.half_life_days
        v = dict(b)
        v["care"] = round(_decay(b["care"], b.get("at", ""), at, hl.get("care", 120)), 4)
        v["spark"] = round(_decay(b["spark"], b.get("at", ""), at, hl.get("spark", 45)), 4)
        v["intimacy"] = round(self._intimacy(member, other, at), 4)
        raw_hurt = _decay(b.get("hurt", 0.0), b.get("hurt_at", ""), at, hl.get("hurt", 3))
        v["commitment"] = round(
            min(
                1.0,
                max(0.0, 0.5 * v["care"] + 0.3 * v["intimacy"] + 0.2 * v["trust"] - 0.3 * raw_hurt),
            ),
            4,
        )
        for f in FEELINGS:
            life = hl.get(f, 5)
            if f == "hurt":  # 承诺越高，委屈消得越快（原谅）
                life *= max(0.3, 1 - r.forgive * v["commitment"])
            v[f] = round(_decay(b.get(f, 0.0), b.get(f"{f}_at", ""), at, life), 4)
        return v

    def view(self, member: str, other: str, at: datetime) -> dict[str, Any]:
        """她此刻对 other（成员 id 或 ``"user"``）的感情；没来往过为空。"""
        b = self._load(member)["bonds"].get(other)
        return self._view(member, other, b, at) if b else {}

    def care(self, member: str, other: str, at: datetime) -> float:
        if not (self.enabled() and other):
            return 0.0
        return float(self.view(member, other, at).get("care", 0.0))

    def kind(self, v: dict[str, Any]) -> str:
        """是哪种感情：亲密 / 心动 / 承诺三样的高低（Sternberg）。"""
        if not v:
            return ""
        r = self.rules
        key = "".join("1" if v[x] >= r.high else "0" for x in ("intimacy", "spark", "commitment"))
        return r.kinds.get(key, "")

    def longing(self, member: str, v: dict[str, Any], at: datetime) -> float:
        """思念：好几天没说上话 × 在乎 × 依恋方式。"""
        if not v or not v.get("seen"):
            return 0.0
        days = max((at - datetime.fromisoformat(v["seen"])).total_seconds() / 86400, 0.0)
        att = self.rules.attachment.get(self.attachment(member))
        factor = att.longing if att else 1.0
        return round(min(1.0, days * self.rules.longing_per_day * v["care"] * factor), 4)

    def _withdrawing(self, member: str, v: dict[str, Any], at: datetime) -> bool:
        """回避型：三天里在乎涨得太快，下意识想退半步。"""
        if self.attachment(member) != "avoidant" or not v.get("marks"):
            return False
        since = self.local_day(at - timedelta(days=3))
        old = [c for d, c in v["marks"].items() if d >= since]
        return bool(old) and v["care"] - min(old) >= self.rules.withdraw_jump

    def _members(self) -> list[str]:
        return [m for m in self._names() if m != USER]

    def rival(self, member: str, other: str, at: datetime) -> str:
        """吃醋的对象：在乎的人（同事或用户）最近和哪位同事更亲、来往更近。只会是同事。"""
        from roundtable.core.member.relation import RelationPlugin

        try:
            rel = RelationPlugin(self.rt)
            if other == USER:
                mine = rel.closeness(member, USER, at)
                best = max(
                    ((m, rel.closeness(m, USER, at)) for m in self._members() if m != member),
                    key=lambda x: x[1],
                    default=None,
                )
                return best[0] if best and best[1] > mine + 0.1 else ""
            peers = rel.data(other)["peers"]
            mine_bond = peers.get(member) or {}
            mine = rel.closeness(other, member, at)
            for c, bond in sorted(peers.items(), key=lambda kv: -kv[1].get("pts", 0)):
                if c == member:
                    continue
                if rel.closeness(other, c, at) > mine + 0.1 and bond.get(
                    "last", ""
                ) > mine_bond.get("last", ""):
                    return c
        except Exception:  # noqa: BLE001 - 读不到关系就不吃醋
            return ""
        return ""

    # --- 写：发生的事 -----------------------------------------------------------------

    def _touch(
        self,
        member: str,
        other: str,
        at: datetime,
        gain: str | None = None,
        *,
        trust: float = 0.0,
        seen: bool = True,
        repair: float = 0.0,
    ) -> None:
        """记一次和 other 的相处：涨在乎（和心动），改信任，记最近一次说上话，消一点委屈。"""
        if not other or member in (USER, other):
            return
        r, hl = self.rules, self.rules.half_life_days
        data = self._load(member)
        b = self._bond(data, other)
        last = datetime.fromisoformat(b["at"]) if b["at"] else None
        if last is None or at >= last:
            b["care"] = round(_decay(b["care"], b["at"], at, hl.get("care", 120)), 4)
            b["spark"] = round(_decay(b["spark"], b["at"], at, hl.get("spark", 45)), 4)
            b["at"] = iso(at)
        if gain:
            day = self.local_day(at)
            used = float(b["daily"].get(day, 0.0))
            room = max(0.0, r.daily_cap - used)
            g = min(r.gains.get(gain, 0.0), room)
            dc = g * (1 - b["care"])
            ds = min(g * r.spark_rate * self._spark_factor(member, other), room - g) * (
                1 - b["spark"]
            )
            ds = max(ds, 0.0)
            b["care"] = round(b["care"] + dc, 4)
            b["spark"] = round(b["spark"] + ds, 4)
            b["daily"][day] = round(used + dc + ds, 4)
            for d in sorted(b["daily"])[:-KEEP_DAYS]:
                b["daily"].pop(d)
            b["marks"][day] = b["care"]
            for d in sorted(b["marks"])[:-KEEP_DAYS]:
                b["marks"].pop(d)
            if gain == "moment":
                b["moments"] = int(b.get("moments", 0)) + 1
        if trust:
            b["trust"] = round(min(1.0, max(0.0, b["trust"] + trust)), 4)
        if repair and b.get("hurt"):
            b["hurt"] = round(b["hurt"] * (1 - repair), 4)
        if seen and (not b["seen"] or iso(at) > b["seen"]):
            b["seen"] = iso(at)
        self._save(member, data)

    def _feel(
        self, member: str, other: str, kind: str, amount: float, why: str, at: datetime
    ) -> None:
        """记一种会过去的感情（委屈 / 感激 / 内疚 / 看不惯）。why 里的 {name} 写卡时换成称呼。"""
        if not other or member in (USER, other) or amount <= 0:
            return
        data = self._load(member)
        b = self._bond(data, other)
        now = self._view(member, other, b, at).get(kind, 0.0)
        b[kind] = round(min(1.0, now + amount), 4)
        b[f"{kind}_at"] = iso(at)
        b[f"{kind}_why"] = why
        self._save(member, data)

    def _mark(self, member: str, other: str, field: str, value: str) -> None:
        data = self._load(member)
        self._bond(data, other)[field] = value
        self._save(member, data)

    def _vicarious(self, listener: str, teller: str, text: str, at: datetime) -> None:
        """对方讲了自己的好事或坏事：在乎对方的人替对方高兴或揪心。"""
        if listener == USER or self.view(listener, teller, at).get("care", 0.0) < 0.35:
            return
        data = self._load(listener)
        kind = "sad" if any(h in text for h in SAD_HINTS) else "happy"
        keep = [v for v in data["vicarious"] if v["text"] != text[:30]]
        item = {"who": teller, "text": text[:30], "kind": kind, "at": iso(at)}
        data["vicarious"] = (keep + [item])[-KEEP_VICARIOUS:]
        self._save(listener, data)

    def on_event(self, event: Event) -> None:
        if not self.enabled():
            return
        if event.kind == "chat.message":
            self._chat(event)
        elif event.kind == "life.event":
            self._life(event)

    def _tracker(self) -> dict[str, Any]:
        raw = self.rt.repo.setting(TRACKER)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def _room(self, room: str) -> dict[str, Any]:
        return self.rt.repo.chat_room(room) or {}

    def _work_room(self, room: str) -> bool:
        return room == "work" or self._room(room).get("kind") == "work"

    def _signals(self, text: str) -> dict[str, bool]:
        r = self.rules
        sad_words = self.rt.config.roundtable.chat.engine.brain.empathy.sad_words
        sad = any(w in text for w in sad_words)
        return {
            "sad": sad,
            "joy": any(w in text for w in r.joy_words),
            "bid": sad or bool(_QUESTION.search(text.strip())),
            "comfort": any(w in text for w in r.comfort_words),
        }

    def _within(self, slot: dict[str, Any] | None, at: datetime) -> bool:
        if not slot:
            return False
        since = at - datetime.fromisoformat(slot["at"])
        return timedelta(0) <= since <= timedelta(minutes=self.rules.window_minutes)

    def _chat(self, event: Event) -> None:
        """聊天里的一来一回：接话、被接住、一起笑、安慰。工作群不算。"""
        data, at = event.data, event.at
        room = str(data.get("room", ""))
        if not room or self._work_room(room):
            return
        msg = self.rt.repo.chat_message(int(data.get("id", 0) or 0))
        who = USER if data.get("from_user") else event.member
        if not msg or not who:
            return
        sig = self._signals(msg["text"] or "")
        tracker = self._tracker()
        slot = tracker.setdefault(room, {})
        prev_user, prev = slot.get("user"), slot.get("member")
        if who == USER:
            if self._within(prev, at) and not prev.get("answered"):  # 你接了她的话
                m = prev["who"]
                self._touch(m, USER, at, "attended", trust=self.rules.trust["attended"], repair=0.3)
                if prev.get("sad") and sig["comfort"]:
                    self._feel(
                        m, USER, "grateful", self.rules.grateful_comfort, "{name}安慰过你", at
                    )
                if prev.get("joy") and sig["joy"]:
                    self._touch(m, USER, at, "moment")
                prev["answered"] = True
            slot["user"] = {"at": iso(at), **sig, "answered": []}
        else:
            if self._within(prev_user, at) and who not in prev_user["answered"]:  # 她接了你的话
                self._touch(who, USER, at, "exchange")
                if prev_user.get("sad") and sig["comfort"]:
                    self._touch(who, USER, at, "comfort_given")
                if prev_user.get("joy") and sig["joy"]:
                    self._touch(who, USER, at, "moment")
                prev_user["answered"].append(who)
            if self._within(prev, at) and prev["who"] != who:  # 同事之间一来一回
                other = prev["who"]
                self._touch(who, other, at, "exchange")
                if not prev.get("answered"):
                    attended = self.rules.trust["attended"]
                    self._touch(other, who, at, "attended", trust=attended, repair=0.3)
                if prev.get("sad") and sig["comfort"]:
                    self._feel(
                        other, who, "grateful", self.rules.grateful_comfort, "{name}安慰过你", at
                    )
                    self._touch(other, who, at, None, trust=self.rules.trust["comfort"])
                    self._touch(who, other, at, "comfort_given")
                if prev.get("joy") and sig["joy"]:
                    self._touch(who, other, at, "moment")
                    self._touch(other, who, at, "moment")
                prev["answered"] = True
            slot["member"] = {"who": who, "at": iso(at), **sig, "answered": False}
        self.rt.repo.set_setting(TRACKER, json.dumps(tracker, ensure_ascii=False))

    def _life(self, event: Event) -> None:
        """生活里待在一起；一起去了没去过的地方更近一步（自我扩展）。只给这条事件的成员记。"""
        data, me = event.data, event.member
        if not me or data.get("kind") not in ("gather", "pair"):
            return
        place = str(data.get("place") or "")
        for other in data.get("members") or []:
            if other == me:
                continue
            self._touch(me, other, event.at, "together")
            if place:
                d = self._load(me)
                b = self._bond(d, other)
                if place not in b["firsts"]:
                    b["firsts"] = (b["firsts"] + [place])[-KEEP_FIRSTS:]
                    self._save(me, d)
                    self._touch(me, other, event.at, "novel")

    def tick(self, now: datetime) -> int:
        """两人私下聊天里主动找对方说话（问了句话、说了难过），对方一直没接：记一点委屈。
        只在同事之间；从不因为用户没回记委屈。返回记了几次。"""
        if not self.enabled():
            return 0
        tracker, n = self._tracker(), 0
        for room, slot in tracker.items():
            m = slot.get("member")
            if not m or m.get("answered") or not m.get("bid"):
                continue
            if now - datetime.fromisoformat(m["at"]) <= timedelta(
                minutes=self.rules.window_minutes
            ):
                continue
            m["answered"] = True  # 只算一次
            info = self._room(room)
            members = [x for x in info.get("members") or [] if x != m["who"]]
            if info.get("with_user") or len(members) != 1:
                continue
            other = members[0]
            att = self.rules.attachment.get(self.attachment(m["who"]))
            amount = self.rules.hurt["missed"] * (att.hurt if att else 1.0)
            self._feel(m["who"], other, "hurt", amount, "上次你找{name}说话，{name}没接", now)
            self._touch(m["who"], other, now, None, trust=self.rules.trust["missed"], seen=False)
            n += 1
        if n or tracker:
            self.rt.repo.set_setting(TRACKER, json.dumps(tracker, ensure_ascii=False))
        return n

    def after(self, card: Any, at: datetime) -> None:
        """真说了才回传：安慰、反驳、说心事、讲自己的事带来的感情变化；挽留和责怪只做一次。"""
        if not self.enabled() or card is None or card.silent or not getattr(card, "to", ""):
            return
        r, member, to = self.rules, card.member, card.to
        if card.intent == "care":
            return
        if card.intent == "comfort":
            self._touch(member, to, at, "comfort_given")
            if to != USER:
                self._feel(to, member, "grateful", r.grateful_comfort, "{name}安慰过你", at)
                self._touch(to, member, at, None, trust=r.trust["comfort"])
        elif card.intent == "disagree" and to != USER:
            att = r.attachment.get(self.attachment(to))
            trust = self.view(to, member, at).get("trust", r.trust_start)
            amount = r.hurt["disagree"] * (1 - 0.6 * trust) * (att.hurt if att else 1.0)
            self._feel(to, member, "hurt", amount, "{name}当面反驳过你", at)
            self._feel(to, member, "dislike", r.dislike["disagree"], "当面反驳过你", at)
            care = self.view(member, to, at).get("care", 0.0)
            if care >= 0.3:
                self._feel(
                    member, to, "guilt", r.guilt_disagree * care, "上次你对{name}说话有点冲", at
                )
        elif card.intent in ("share", "vent"):
            self._touch(member, to, at, None, trust=r.trust["confided"])
            if to != USER:
                self._touch(to, member, at, "confided")
        if "clash" in card.tags and to != USER:
            self._feel(member, to, "dislike", r.dislike["clash"], "三观和你不太合", at)
        spot = card.spotlight
        if spot is not None and spot.source == "goal" and to != USER:
            self._vicarious(to, member, spot.text, at)
        if "love:blame" in card.tags:
            self._mark(member, to, "blamed", self.view(member, to, at).get("seen", ""))
        if "love:retain" in card.tags:
            self._mark(member, to, "retained", self.local_day(at))

    # --- 写进意图卡 -------------------------------------------------------------------

    def bonus(self, member: str, other: str, at: datetime) -> float:
        """在乎的人说的话更显著（丘脑）。"""
        return round(self.rules.salience * self.care(member, other, at), 3)

    def thoughts(
        self, member: str, at: datetime, present: Iterable[str]
    ) -> list[tuple[str, float]]:
        """交给全局工作区的念头：想念不在场的人、替对方高兴或揪心。[(念头, 强度)]"""
        if not self.enabled():
            return []
        here = set(present)
        data, out = self._load(member), []
        best: tuple[str, float] | None = None
        for other, b in data["bonds"].items():
            if other in here:
                continue
            longing = self.longing(member, self._view(member, other, b, at), at)
            if longing >= self.rules.longing_show and (best is None or longing > best[1]):
                best = (other, longing)
        if best:
            text = f"好几天没和{self.name(best[0])}说上话了，有点想"
            att = self.rules.attachment.get(self.attachment(member))
            if att and att.line:
                text += f"，{att.line}"
            out.append((text, round(0.3 + 0.5 * best[1], 3)))
        hl = self.rules.half_life_days.get("vicarious", 1)
        for v in data["vicarious"]:
            left = _decay(1.0, v["at"], at, hl)
            if left < 0.3:
                continue
            verb = "揪心" if v["kind"] == "sad" else "高兴"
            out.append((f"替{self.name(v['who'])}{verb}：{v['text']}", round(0.3 + 0.4 * left, 3)))
        return out

    def _on(self, member: str, other: str, v: dict[str, Any], at: datetime) -> list[str]:
        r = self.rules
        on = []
        if v.get("care", 0) >= r.show_care:
            on.append("care")
        if v.get("spark", 0) >= r.show_spark:
            on.append("spark")
        on += [f for f in FEELINGS if v.get(f, 0) >= r.on]
        if other == USER and v.get("care", 0) >= r.privilege_above:
            on.append("privilege")
        if self._withdrawing(member, v, at):
            on.append("withdraw")
        return on

    def effects(self, member: str, other: str, at: datetime) -> dict[str, float]:
        """对说话对象的感情改各意图的权重（倍数）。"""
        if not (self.enabled() and other):
            return {}
        v = self.view(member, other, at)
        out: dict[str, float] = {}
        for name in self._on(member, other, v, at) if v else []:
            for intent, k in self.rules.effects.get(name, {}).items():
                out[intent] = out.get(intent, 1.0) * k
        return out

    def line(
        self, member: str, other: str, at: datetime, rng: random.Random, *, last: str = ""
    ) -> tuple[str, str] | None:
        """意图卡里关于说话对象的一句感情话：(标签, 句子)。
        用户说要去睡觉、上课、工作、陪家人朋友时，一定是「痛快放人」那句（压过其余）。"""
        if not (self.enabled() and other):
            return None
        r = self.rules
        v = self.view(member, other, at)
        name = self.name(other)
        style = r.styles.get(self.style(member))
        cands: list[tuple[str, str]] = []
        if other == USER and r.retain_blame and last and any(w in last for w in r.leave_words):
            reason = next((k for k, ws in r.no_retain.items() if any(w in last for w in ws)), "")
            if reason:
                bye = GOODBYE.get(reason, "加油")
                return ("let_go", f"{name}要去{reason}了，痛快放人，说句{bye}就好，别留")
            if (
                v
                and v["care"] >= r.retain_care
                and v.get("retained") != self.local_day(at)
                and style
            ):
                cands.append(
                    (
                        "retain",
                        f"{name}要走了，你有点舍不得：{style.retain}；只留这一次，对方坚持就放手",
                    )
                )
        if not v:
            return cands[0] if cands else None
        if (
            other == USER
            and r.retain_blame
            and style
            and v.get("seen")
            and v["care"] >= r.blame_care
        ):
            days = int((at - datetime.fromisoformat(v["seen"])).total_seconds() // 86400)
            if days >= r.blame_after_days and v.get("blamed") != v["seen"]:
                cands.append(
                    (
                        "blame",
                        f"有{days}天没和{name}聊了：{style.blame}；说一两句就好，不翻旧账、不越说越重",
                    )
                )

        def why(kind: str) -> str:
            return str(v.get(f"{kind}_why") or "").replace("{name}", name)

        if v["guilt"] >= r.on:
            cands.append(("guilt", f"{why('guilt')}，你心里过意不去，想找机会弥补"))
        if v["hurt"] >= r.on:
            lead = f"你在乎{name}，可还有点介意" if v["care"] >= 0.4 else "你还有点介意"
            cands.append(("hurt", f"{lead}：{why('hurt')}"))
        if v["dislike"] >= r.on and v["care"] < 0.4:
            cands.append(
                ("dislike", f"你有点看不惯{name}（{why('dislike')}），不至于翻脸，但不太想附和")
            )
        if v["grateful"] >= r.on:
            cands.append(("grateful", f"{why('grateful')}，你心里记着"))
        if self._withdrawing(member, v, at):
            cands.append(("withdraw", f"最近和{name}走得有点近，你下意识想退半步"))
        loved = v["care"] >= r.show_care or v["spark"] >= r.show_spark
        rival = self.rival(member, other, at) if loved else ""
        att = r.attachment.get(self.attachment(member))
        if rival and rng.random() < (att.jealous if att else 0.25):
            rname = self.name(rival)
            if other == USER:
                text = (
                    f"看{name}最近和{rname}聊得多，你有点吃醋，"
                    "可以小小表现一下，但别要求什么、别让对方有负担"
                )
            else:
                text = f"看{name}最近老和{rname}在一起，你心里有点酸，不至于说出来"
            cands.append(("jealous", text))
        if v["spark"] >= r.show_spark and style:
            cands.append(("crush", f"你对{name}有点心动：{style.crush}"))
        if other == USER and v["care"] >= r.privilege_above:
            perks = self.privileges(member)
            if perks:
                cands.append(("privilege", f"对{name}你有点偏心：{rng.choice(perks)}"))
        if v["care"] >= r.show_care and style:
            kind = self.kind(v)
            label = f"（{kind}）" if kind else ""
            cands.append(("care", f"你很在乎{name}{label}：{style.care}"))
        if not cands:
            return None
        weights = [r.weights.get(tag, 1.0) for tag, _ in cands]
        return rng.choices(cands, weights)[0]

    def snapshot(self, member: str, at: datetime) -> list[dict[str, Any]]:
        """她对每个人的感情（模拟报告、网页用），按在乎从高到低。"""
        out = []
        for other, b in self._load(member)["bonds"].items():
            v = self._view(member, other, b, at)
            out.append(
                {
                    "who": other,
                    "care": v["care"],
                    "spark": v["spark"],
                    "trust": v["trust"],
                    "kind": self.kind(v),
                    "longing": self.longing(member, v, at),
                    **{f: v[f] for f in FEELINGS},
                }
            )
        return sorted(out, key=lambda x: -x["care"])
