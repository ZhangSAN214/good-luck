"""人味（docs/BRAIN.md「人味」）：倒过来想「一个月后十分钟就认出她是 AI，会是因为什么」补上的几样。

- **知识边界**：每位只熟悉自己的几个领域（自我档案里提到的 + 按 id 派生的一两个），被问到不熟的就说
  不知道或说明是猜的；熟的偶尔也记不准细节。
- **有限的注意力和自己的时间**：一条话里说了好几件事，只挑一点回；这会儿按作息在睡觉、洗澡、健身，
  可以回得短或说待会再聊；聊了很久又累了，会自己找个理由先撤。
- **自我一致**：记下她自己说过的关于自己的话（喜欢什么、从不做什么），话题碰到时提醒别说反；
  答应过的事（下次、改天、明天我……）记一阵，提醒兑现。
- **八卦和小圈子**：会想聊不在场的人（在乎的、看不惯的）；群里更爱接亲近的人的话（丘脑已有）。
- **时间纵深**：偶尔想起自我档案里的从前、遗憾、梦想、害怕。
  （大事前紧张、过后松口气在 ``brain.py``。）
- **小毛病**：每位按性格有两样（拖延、嘴硬、生闷气、记仇、爱面子、三分钟热度、善意的谎……），
  碰到对应的场合才冒出来。
- **主动做决定**：想找人、又有亲近的人时，主动约人做点什么；约了、答应了就记着。
  （做不下去就放下、换件事做在 ``brain.py`` 的目标里。）

全是程序计算，不调模型；只写自己的键（``member_human:<成员>``）。8 位同一套规则
（``chat.engine.brain.human``），差别只来自各自的档案、经历和由 id 派生的随机数。只用于生活模式。
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

KEY = "member_human:"
USER = "user"
_CJK2 = re.compile("(?=([一-鿿]{2}))")
_SPLIT = re.compile(r"[。！？!?\n；;，,~～…]+")
_QUESTION = re.compile(r"[？?]|(吗|呢|么)[。！!～~]*$")


def _num(x: Any, default: float = 3.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(text or "") if s.strip()]


class Human:
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

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.brain.human

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and engine.brain.enabled and self.rules.enabled)

    def name(self, other: str) -> str:
        if other == USER:
            return self.rt.config.roundtable.chat.engine.brain.love.user_name
        names = self.names() if callable(self.names) else (self.names or {})
        return dict(names).get(other, other)

    # --- 存取（只有这里写 member_human:*）------------------------------------------

    def _load(self, member: str) -> dict[str, Any]:
        raw = self.rt.repo.setting(KEY + member)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        data.setdefault("claims", [])
        data.setdefault("promises", [])
        return data

    def _save(self, member: str, data: dict[str, Any]) -> None:
        self.rt.repo.set_setting(KEY + member, json.dumps(data, ensure_ascii=False))

    # --- 档案 ---------------------------------------------------------------------

    def _profile_text(self, member: str) -> str:
        return json.dumps(self.profiles.current(member).data, ensure_ascii=False)

    def _traits(self, member: str) -> dict[str, Any]:
        data = self.profiles.current(member).data
        return dict((data.get("personality") or {}).get("五项") or {})

    def domains(self, member: str) -> list[str]:
        """她熟悉的领域：自我档案里提到过的，加上按 id 派生的一两个（人总有点杂学）。"""
        text, topics = self._profile_text(member), self.rules.topics
        known = [d for d, words in topics.items() if any(w in text for w in words)]
        rest = [d for d in topics if d not in known]
        rng = random.Random(f"human-domains:{member}")
        rng.shuffle(rest)
        return known + rest[: self.rules.extra_domains]

    def topic(self, text: str) -> str:
        """这句话说的是哪个领域（第一个命中的）；没有就空串。"""
        return next((d for d, ws in self.rules.topics.items() if any(w in text for w in ws)), "")

    def flaws(self, member: str) -> list[str]:
        """她的小毛病：按性格分挑最像的几样（加一点个人差异）。"""
        traits, rng = self._traits(member), random.Random(f"human-flaws:{member}")
        scored = [
            (
                f.base
                + sum(c * (_num(traits.get(t, 3)) - 3) for t, c in f.traits.items())
                + rng.random() * 0.3,
                name,
            )
            for name, f in self.rules.flaws.items()
        ]
        scored.sort(key=lambda x: -x[0])
        return [n for _, n in scored[: self.rules.flaw_count]]

    def _depth(self, member: str) -> list[str]:
        """自我档案里关于从前、遗憾、梦想、害怕的句子。"""
        out = []
        for s in _sentences(self._profile_text(member).replace('"', "，")):
            if 4 <= len(s) <= 40 and any(
                w in s for ws in self.rules.depth_words.values() for w in ws
            ):
                out.append(s)
        return out

    # --- 记下她说过的话 ---------------------------------------------------------------

    def on_event(self, event: Event) -> None:
        """她自己在生活里说的话：关于自己的说法和答应的事记下来。工作群不算。"""
        if not self.enabled() or not event.member or event.data.get("from_user"):
            return
        room = str(event.data.get("room", ""))
        info = self.rt.repo.chat_room(room) or {}
        if room == "work" or info.get("kind") == "work":
            return
        msg = self.rt.repo.chat_message(int(event.data.get("id", 0) or 0))
        if msg:
            to = USER if info.get("with_user", True) else ""
            others = [m for m in info.get("members") or [] if m != event.member]
            if not to and len(others) == 1:
                to = others[0]
            self.note(event.member, msg["text"] or "", event.at, to=to)

    def note(self, member: str, text: str, at: datetime, *, to: str = "") -> None:
        r = self.rules
        claims, promises = [], []
        for s in _sentences(text):
            if "我" in s and any(w in s for w in r.claim_words) and len(s) <= 30:
                claims.append(s)
            if any(w in s for w in r.promise_words) and len(s) <= 30:
                promises.append(s)
        if not (claims or promises):
            return
        data = self._load(member)
        old = {c["text"] for c in data["claims"]}
        data["claims"] += [{"text": c, "at": iso(at)} for c in claims if c not in old]
        data["claims"] = data["claims"][-r.keep_claims :]
        data["promises"] += [{"text": p, "to": to, "at": iso(at)} for p in promises]
        data["promises"] = data["promises"][-r.keep_promises :]
        self._save(member, data)

    def _claim_about(self, member: str, text: str) -> str:
        """话题碰到她以前关于自己的哪句说法（共享两个字）；没有就空串。"""
        stop = self.rt.config.roundtable.chat.engine.brain.psyche.assoc.stop
        for w in stop:
            text = text.replace(w, " ")
        words = set(_CJK2.findall(text)) - {"我们", "你们", "他们", "什么", "怎么"}
        if not words:
            return ""
        for c in reversed(self._load(member)["claims"]):
            if any(w in c["text"] for w in words):
                return str(c["text"])
        return ""

    def _promises(self, member: str, at: datetime) -> list[dict[str, Any]]:
        days = self.rules.promise_days
        return [
            p
            for p in self._load(member)["promises"]
            if timedelta(0) <= at - datetime.fromisoformat(p["at"]) <= timedelta(days=days)
        ]

    # --- 交给大脑：念头和卡里的话 -------------------------------------------------------

    def thoughts(
        self, member: str, at: datetime, present: Iterable[str], rng: random.Random
    ) -> list[tuple[str, float]]:
        """念头：答应过的事、想聊不在场的人、想起从前或梦想。[(念头, 强度)]"""
        if not self.enabled():
            return []
        r, here, out = self.rules, set(present), []
        for p in self._promises(member, at)[-1:]:
            said = str(p["text"])
            who = f"跟{self.name(p['to'])}" if p.get("to") else ""
            text = (
                f"{said}，记着这事" if said.startswith("约了") else f"{who}说过「{said}」，记着这事"
            )
            out.append((text, 0.5))
        names = self.names() if callable(self.names) else (self.names or {})
        absent = [m for m in names if m not in here and m not in (member, USER)]
        if absent and rng.random() < r.gossip_chance:
            from roundtable.core.member.love import Heart  # love 不依赖本模块

            heart, who = Heart(self.rt, names=self.names), rng.choice(absent)
            v = heart.view(member, who, at)
            if v.get("dislike", 0) >= heart.rules.on and v.get("care", 0) < 0.4:
                out.append((f"有点想吐槽{self.name(who)}", 0.45))
            else:
                out.append((f"想跟人聊聊{self.name(who)}最近怎么样", 0.4))
        if rng.random() < r.depth_chance:
            pool = self._depth(member)
            if pool:
                out.append((f"想起{rng.choice(pool)}", 0.45))
        return out

    def lines(
        self,
        member: str,
        *,
        to: str,
        last: str,
        chosen: str,
        room: str,
        energy: float,
        social: float,
        spotlight: Any,
        hurt: bool,
        at: datetime,
        rng: random.Random,
    ) -> list[tuple[str, str]]:
        """卡里的话（先给要紧的：知识边界、别说反、自己的时间；再给小毛病、约人）。"""
        if not self.enabled():
            return []
        r, out = self.rules, []
        local = home_zone(self.rt).local(at)
        if last:
            dom = self.topic(last)
            asked = bool(_QUESTION.search(last.strip())) and any(w in last for w in r.ask_words)
            if dom and asked and dom not in self.domains(member):
                out.append(
                    ("unknown", f"这是{dom}方面的事，你不太懂：不知道就直说，想猜就说明是猜的")
                )
            elif dom and rng.random() < r.misremember:
                out.append(("fuzzy", "细节你可能记不太准，说个大概就好"))
            claim = self._claim_about(member, last)
            if claim:
                out.append(("consistent", f"你以前说过「{claim}」，别说反了（真变了就说为什么变）"))
            if len(last) >= r.long_message and rng.random() < 0.6:
                out.append(("attention", "对方说了好几件事，你只挑最在意的一点回，其余的先不管"))
        if room:
            from roundtable.core.office.apartment import schedule

            _, act = schedule(member, local.hour, local.weekday())
            busy = r.busy_acts.get(act, "")
            if busy and rng.random() < r.busy_chance:
                out.append(("busy", f"你这会儿{busy}，可以回得短点，或者说一声待会再聊"))
            elif (
                energy < 0.4
                and self._talked(member, room, at) >= r.wrap_after
                and rng.random() < r.wrap_chance
            ):
                why = rng.choice(r.wrap_reasons) if r.wrap_reasons else "有点累了"
                out.append(
                    ("wrap", f"聊了挺久，你有点乏了，可以找个理由先撤（比如{why}），说一声就行")
                )
        mine = self.flaws(member)
        cond = {
            "hurt": hurt,
            "goal": spotlight is not None and getattr(spotlight, "source", "") == "goal",
            "disagree": chosen == "disagree",
            "asked": bool(last and _QUESTION.search(last.strip())),
            "tired": energy < 0.3,
            "praised": bool(last and any(w in last for w in r.praise_words)),
        }
        for name in mine:
            f = r.flaws[name]
            if cond.get(f.when) and rng.random() < f.chance:
                out.append((f"flaw:{name}", f.line))
        if (
            (not last or chosen in ("share", "shift"))
            and social >= r.propose_social
            and rng.random() < r.propose_chance
        ):
            who = to or self._closest(member, at)
            if who and r.activities:
                act = rng.choice(r.activities)
                out.append(("propose", f"你想约{self.name(who)}{act}，可以直接提"))
        return out

    def _talked(self, member: str, room: str, at: datetime) -> int:
        start = iso(at - timedelta(minutes=self.rules.wrap_minutes))
        rows = self.rt.repo.chat_messages_between(start, iso(at + timedelta(seconds=1)))
        return sum(1 for m in rows if m["room"] == room and m["author"] == member)

    def _closest(self, member: str, at: datetime) -> str:
        from roundtable.core.member.love import Heart

        snap = [x for x in Heart(self.rt, names=self.names).snapshot(member, at) if x["care"] > 0.2]
        return snap[0]["who"] if snap else ""

    def after(self, card: Any, at: datetime) -> None:
        """真说了才回传：约了人，就当答应了一件事记着。"""
        if not self.enabled() or card is None or card.silent:
            return
        for line in (card.text or "").splitlines():
            if line.startswith("你想约") and "propose" in card.tags:
                what = line.removeprefix("你想约").split("，", 1)[0]
                data = self._load(card.member)
                item = {"text": f"约了{what}", "to": card.to, "at": iso(at)}
                data["promises"] = (data["promises"] + [item])[-self.rules.keep_promises :]
                self._save(card.member, data)
