"""说话的样子（docs/BRAIN.md「说话的样子」）：让成员说话像发微信的真人，而不是认真作答。

- **看懂语境**（``gist``）：对方这条是分享视频、发表情包、吐槽、报喜、求安慰……意图卡里写
  「你看懂了（别说出来）」和这种场合人一般怎么回（先反应、再吐槽、不解释）。
- **万能短回复**（``quick``）：分享视频、发表情包、闲聊时，有一定概率只回一句「6」「笑死」「？？」
  或一个表情包——程序直接发，不调模型。累了、话少、和对方很熟时更常见。
- **打字习惯**（``habits``）：标点（句号党 / 不打标点 / 波浪号 / 省略号 / 感叹号）、笑法、
  个人颜文字、偏爱的表情包、每人自己的一小套短回复。按性格分和全员平均比挑
  （加一点按 id 的个人差异），档案里写了的以档案为准。
- **语气变调**（``card_line``）：累了、深夜、心情好坏、和对方熟不熟、看不惯——挑最要紧的一句；
  熟了有外号，起了就一直这么叫。
- **跟人学**（``on_event``）：在乎的人反复说的笑法、短回复、网络用语、口头禅，听多了会学来，
  一阵不用又忘。
- **发出前收尾**（``finish``）：书面词换成口语，解释句和复述能删就删，句尾按她的标点习惯，
  笑法统一成她的写法，偶尔打错一个字（较真的会补一条「*正字」）。关心时段不动。
- **口吻指纹**（``own_voice``，在收尾里）：草稿里混进了别的成员的口头禅、颜文字、自称，而她没跟人
  学过——口头禅那一小句删掉（删光了就留着）、别人的颜文字换成她自己的、别人的自称换回她的。
  8 个人共用同一类模型时最容易串味，这一步只认她自己的那套。

全程序、不调模型；只用于生活群、私聊、成员互聊（工作群不挂载）。规则 8 位相同，差别只来自
档案和性格分（中立）。只有这里写 ``member_lingo:*``。
"""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.chat_actions import STICKER_RE
from roundtable.core.hometz import home_zone
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.memes import strip_stale
from roundtable.core.member.profile import ProfileStore
from roundtable.core.member.slop import Slop, strip_phrases, strip_stage
from roundtable.core.storage.repository import iso

KEY = "member_lingo:"
USER = "user"
LIFE_KINDS = frozenset({"group", "dm", "life", "pair"})
CACHE_SECONDS = 60.0
_LAUGH = re.compile(r"(?:哈){2,}|(?<![A-Za-z])h{3,}(?![A-Za-z])|(?<!\d)2333*(?!\d)|(?:嘿){2,}")
_BARE_FIX = re.compile(r"^\s*不对[，,、]?\s*是\s*(?:…+|\.{3,})\s*")  # 开口第一句就改口
_END = re.compile(r"[。．.！!～~…]+$")
_KEEP_END = re.compile(r"(?:[？?）)」』>\]】]|[\U0001f300-\U0001faff☀-➿])$")
_CLAUSE = re.compile(r"[^，,。！!？?～~…\n]+[，,。！!？?～~…]*")
_NO = type("_NoVibe", (), {"items": ()})()  # 没有这种气质时的空组
_EMOJI = re.compile(r"[\U0001f300-\U0001faff☀-➿]")
_LAST_HAN = re.compile(r"[\u4e00-\u9fff](?=[^\u4e00-\u9fff]*$)")  # 一行里最后一个汉字
_SKIP = re.compile(r"\[表情[:：][^\]]*\]|\[引用[^\]]*\]|https?://\S+")


def _num(x: Any, default: float = 3.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


_ORPHAN = re.compile(
    r"[，,、]\s*[呢吧啊呀嘛哦啦呗]+(?=[。.！!？?～~…]|$)", re.M
)  # 「理清楚，呢。」
_LONE = re.compile(r"^\s*([\u4e00-\u9fff])[。.…，,、\s]*$")  # 只剩一个字：「让……」
_INTERJECTIONS = frozenset("嗯啊哦噢诶欸额呃哈嘿唔哇咦呀嗷喔哎唉呜好行对靠草")


def _tidy(text: str) -> str:
    """收尾后的残句：落单的语气词并回前一句；整条只剩一个不成话的字（「让……」）就不发。
    （10-31 第 3 次试跑）"""
    out = _ORPHAN.sub("", text)
    lone = _LONE.match(out)
    if lone and lone.group(1) not in _INTERJECTIONS:
        return ""
    return out


@dataclass(frozen=True)
class Gist:
    """对方这条消息是什么。"""

    kind: str
    label: str
    react: str = ""
    quick: float = 0.0
    sticker: float = 0.0
    pools: tuple[str, ...] = ()


@dataclass(frozen=True)
class Habits:
    """一位成员的打字习惯（按档案和性格分挑；用腻了的会慢慢换，见 drift.py）。"""

    punct: str = ""
    punct_end: str | None = None
    laugh: str = ""
    kaomoji: tuple[str, ...] = ()
    sticker_set: str = ""
    shorts: dict[str, tuple[str, ...]] = field(default_factory=dict)
    things: tuple[str, ...] = ()  # 打比方的取材


class Lingo:
    def __init__(
        self,
        rt: Any,
        bus: EventBus | None = None,
        names: Callable[[], dict[str, str]] | dict[str, str] | None = None,
    ) -> None:
        self.rt = rt
        self.names = names
        self.profiles = ProfileStore(rt)
        self._cache: dict[str, tuple[float, Any]] = {}  # 档案读一次用一会儿（习惯不随时间变）
        if bus is not None:
            bus.subscribe("chat.message", self.on_event)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.lingo

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and self.rules.enabled)

    def _names(self) -> dict[str, str]:
        names = self.names() if callable(self.names) else (self.names or {})
        return dict(names)

    # --- 存取（只有这里写 member_lingo:*）------------------------------------------

    def _load(self, member: str) -> dict[str, Any]:
        raw = self.rt.repo.setting(KEY + member)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        for k in ("heard", "learned", "nick"):
            if not isinstance(data.get(k), dict):
                data[k] = {}
        return data

    def _save(self, member: str, data: dict[str, Any]) -> None:
        self.rt.repo.set_setting(KEY + member, json.dumps(data, ensure_ascii=False))

    def clear(self, member: str) -> None:
        self.rt.repo.set_setting(KEY + member, "")

    # --- 档案和性格 -----------------------------------------------------------------

    def _data(self, member: str) -> dict[str, Any]:
        return dict(self.profiles.current(member).data or {})

    def styled(self, member: str) -> bool:
        """有人物卡（性格分）才有自己的打字习惯；没有档案时原样说话。"""
        return self.enabled() and bool(self._traits(member))

    def _traits(self, member: str) -> dict[str, Any]:
        return dict((self._data(member).get("personality") or {}).get("五项") or {})

    def _cached(self, key: str, make: Callable[[], Any]) -> Any:
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        value = make()
        self._cache[key] = (now, value)
        return value

    def _cast_mean(self) -> dict[str, float]:
        return self._cached("mean", self._mean)

    def _mean(self) -> dict[str, float]:
        cast = [m for m in self._names() if m != USER]
        if not cast:
            return {}
        rows = [self._traits(m) for m in cast]
        keys = {k for r in rows for k in r}
        return {k: sum(_num(r.get(k, 3)) for r in rows) / len(rows) for k in keys}

    def _rank(self, member: str, options: dict[str, Any], salt: str) -> list[str]:
        """按性格分（和全员平均比）给几种选项打分，最像的在前；加一点按 id 的个人差异。"""
        traits, mean = self._traits(member), self._cast_mean()
        rng = random.Random(f"lingo-{salt}:{member}")
        scored = []
        for name, pick in options.items():
            score = pick.base + rng.random() * 0.35
            score += sum(
                c * (_num(traits.get(t, 3)) - mean.get(t, 3)) for t, c in pick.traits.items()
            )
            scored.append((score, name))
        scored.sort(key=lambda x: -x[0])
        return [n for _, n in scored]

    def habits(self, member: str) -> Habits:
        return self._cached("habits:" + member, lambda: self._habits(member))

    def _habits(self, member: str) -> Habits:
        r = self.rules
        text = json.dumps(self._data(member).get("style") or {}, ensure_ascii=False)
        punct = next((k for k, ws in r.punct_hints.items() if any(w in text for w in ws)), "")
        if not punct and r.punct:
            punct = self._rank(member, r.punct, "punct")[0]
        laugh = self._rank(member, r.laughs, "laugh")[0] if r.laughs else ""
        kaomoji: list[str] = []
        for vibe in self._rank(member, r.kaomoji, "kaomoji")[:2]:
            items = list(r.kaomoji[vibe].items)
            random.Random(f"lingo-kao:{member}:{vibe}").shuffle(items)
            kaomoji += items
        kaomoji = kaomoji[: r.kaomoji_each]
        from roundtable.core.member.drift import Drift  # drift 只在函数里引用本模块

        drift = Drift(self.rt)
        kaomoji = list(drift.apply(member, tuple(kaomoji)))  # 用腻了换过的
        sticker_set = self._rank(member, r.sticker_sets, "sticker")[0] if r.sticker_sets else ""
        shorts: dict[str, tuple[str, ...]] = {}
        for pool, words in r.shorts.items():
            items = list(words)
            random.Random(f"lingo-short:{member}:{pool}").shuffle(items)
            shorts[pool] = drift.apply(member, tuple(items[: r.shorts_each]))
        pick = r.punct.get(punct)
        return Habits(
            punct=punct,
            punct_end=pick.end if pick else None,
            laugh=laugh,
            kaomoji=tuple(kaomoji),
            sticker_set=sticker_set,
            shorts=shorts,
            things=tuple(self._things(member)),
        )

    def _things(self, member: str) -> list[str]:
        """打比方的取材：她熟悉的领域和爱好（各取一两样）。"""
        from roundtable.core.member.human import Human  # human 不依赖本模块

        out = Human(self.rt, names=self.names).domains(member)[:2]
        likes = (self._data(member).get("preferences") or {}).get("喜欢") or []
        if isinstance(likes, list):
            out += [str(x) for x in likes[:1] if str(x).strip()]
        return out

    # --- 写进说话习惯段（不随时间变）---------------------------------------------------

    def voice_lines(self, member: str) -> list[str]:
        if not self.styled(member):
            return []
        r, h = self.rules, self.habits(member)
        lines = []
        pick = r.punct.get(h.punct)
        laugh = r.laughs.get(h.laugh)
        bits = [x for x in (pick.line if pick else "", laugh.line if laugh else "") if x]
        if bits:
            lines.append("打字：" + "，".join(bits))
        mine = [w for ws in h.shorts.values() for w in ws[:2]]
        if mine:
            lines.append(f"不想多说时就回一两个字，比如：{'、'.join(mine[:5])}")
        if h.kaomoji:
            lines.append(f"颜文字只用你自己这几个：{' '.join(h.kaomoji)}")
        pack = self.sticker_pack(member)
        if pack:
            lines.append(f"表情包最常发「{pack[0]}」那套")
        if h.things and r.metaphor:
            lines.append(r.metaphor.format(things="、".join(h.things)))
        from roundtable.core.member.drift import Drift

        mine_now = Drift(self.rt).promoted(member)
        learned = [x for x in self.learned(member, self.rt.budget.now()) if x[0] not in mine_now]
        if learned:
            lines.append(
                "最近跟人学的说法（偶尔冒出来）："
                + "、".join(f"「{w}」（跟{self._name(src)}学的）" for w, src in learned)
            )
        return lines

    def sticker_pack(self, member: str) -> tuple[str, list[str]] | None:
        """她最常发的那套表情包：有自己审过的专属表情就用自己的，没有再用按性格挑的通用那套。"""
        from roundtable.core.chat_actions import own_stickers

        name = self._names().get(member, "")
        own = own_stickers(self.rt, member, name) if name else {}
        if own:
            return next(iter(own.items()))
        stickers = self.rt.config.roundtable.chat.actions.stickers
        h = self.habits(member)
        return (h.sticker_set, list(stickers[h.sticker_set])) if h.sticker_set in stickers else None

    def kaomoji(self, member: str) -> tuple[str, ...]:
        return self.habits(member).kaomoji if self.styled(member) else ()

    def _name(self, who: str) -> str:
        return "用户" if who == USER else self._names().get(who, who)

    # --- 看懂语境 ---------------------------------------------------------------------

    def gist(self, text: str, meta: dict[str, Any] | None = None) -> Gist | None:
        if not self.enabled():
            return None
        meta = meta or {}
        has = set()
        for item in meta.get("media") or []:
            has.add("sticker" if item.get("sticker") else str(item.get("kind", "")))
        if meta.get("card"):
            has.add("card")
            if str((meta["card"] or {}).get("kind", "")) == "video":
                has.add("video")
        if STICKER_RE.search(text or ""):
            has.add("sticker")
        bare = STICKER_RE.sub("", text or "").strip()
        for g in self.rules.gist:
            hit = bool(set(g.when) & has) or any(w in (text or "") for w in g.words)
            if hit and any(w in (text or "") for w in g.unless):
                continue
            if g.kind == "sticker" and bare and "sticker" in has:
                hit = len(bare) <= 4  # 话里带个表情包不算「只发了表情包」
            if hit or (not g.when and not g.words):
                return Gist(g.kind, g.label, g.react, g.quick, g.sticker, tuple(g.pools))
        return None

    def notice(self, gist: Gist | None) -> str:
        if gist is None or gist.kind == "chat":
            return ""
        line = self.rules.notice.format(label=gist.label)
        return f"{line}；{gist.react}" if gist.react else line

    # --- 万能短回复（程序直接发）-----------------------------------------------------

    def quick(
        self,
        member: str,
        gist: Gist | None,
        *,
        energy: float,
        care: float,
        quiet: bool,
        rng: random.Random,
    ) -> str:
        """只回一句短的（或一个表情包）。不回就是空串。"""
        if gist is None or not (gist.quick or gist.sticker) or not self.styled(member):
            return ""
        r, h = self.rules, self.habits(member)
        boost = 1.0
        if energy < 0.3:
            boost *= r.quick_tired
        if care >= 0.5:
            boost *= r.quick_close
        if quiet:
            boost *= r.quick_quiet
        pack = self.sticker_pack(member)
        if pack and rng.random() < min(gist.sticker * boost, 0.9):
            return f"[表情:{pack[0]}·{rng.choice(pack[1])}]"
        if rng.random() >= min(gist.quick * boost, 0.9):
            return ""
        words = [w for p in gist.pools for w in h.shorts.get(p, ())]
        words += [w for w, _ in self.learned(member, self.rt.budget.now())]
        if not words:
            return ""
        word = rng.choice(words)
        return self._laugh(word, h, rng) if _LAUGH.fullmatch(word) else word

    # --- 语气变调 + 外号（写进意图卡）------------------------------------------------

    def card_line(
        self,
        member: str,
        to: str,
        *,
        mood: float,
        energy: float,
        care: float,
        dislike: float,
        at: datetime,
        rng: random.Random,
        battle: bool = False,
    ) -> str:
        """此刻怎么说话：最要紧的一句变调 + 对对方的叫法 + 斗图。没有就空串。"""
        if not self.enabled():
            return ""
        r, bits = self.rules, []
        hour = home_zone(self.rt).local(at).hour
        for key, ok in (
            ("night", hour >= 1 and hour < 6),
            ("tired", energy < 0.3),
            ("down", mood < -0.3),
            ("dislike", bool(to) and dislike >= 0.25 and care < 0.4),
            ("close", bool(to) and care >= 0.5),
            ("new", bool(to) and care < 0.15),
            ("up", mood > 0.4),
        ):
            if ok and r.shift.get(key):
                bits.append(r.shift[key])
                break
        from roundtable.core.member.drift import Drift

        face = Drift(self.rt).mood_face(member, mood, self) if self.styled(member) else ""
        if face:  # 心情不同用的颜文字不同
            bits.append(self.rules.drift.mood_line.format(face=face))
        nick = self.nickname(member, to, care, at, rng) if to else ""
        if nick:
            bits.append(f"你现在叫{self._name(to)}「{nick}」")
        elif to and self._invite(member, to, care, at):  # 没写叫法：够熟了偶尔提一句，她自己起
            bits.append(self.rules.nick.invite)
        if battle:
            bits.append("上一条是表情包，可以直接回个表情包斗图")
        return "；".join(bits)

    def nickname(
        self, member: str, other: str, care: float, at: datetime, rng: random.Random
    ) -> str:
        """对 other 的叫法：熟了以后从档案「熟了以后可能会叫……」里挑一个，起了就一直这么叫。"""
        data = self._load(member)
        got = data["nick"].get(other)
        if got:
            return str(got.get("name", ""))
        r = self.rules.nick
        if care < r.care or not r.hint:
            return ""
        day = home_zone(self.rt).local(at).date().isoformat()
        if random.Random(f"lingo-nick:{member}:{other}:{day}").random() >= r.chance:
            return ""
        options = self._nick_options(member, other)
        if not options:
            return ""
        name = rng.choice(options)
        data["nick"][other] = {"name": name, "at": iso(at)}
        self._save(member, data)
        return name

    def _invite(self, member: str, other: str, care: float, at: datetime) -> bool:
        r = self.rules.nick
        if not r.invite or care < r.care or self._nick_options(member, other):
            return False
        day = home_zone(self.rt).local(at).date().isoformat()
        return random.Random(f"lingo-invite:{member}:{other}:{day}").random() < r.invite_chance

    def _capture_nick(self, member: str, text: str, room: dict[str, Any], at: datetime) -> None:
        """她自己说出「以后就叫你 X」：记下来（只记对你的；已有叫法就不改）。"""
        r = self.rules.nick
        if not r.capture or room.get("kind") not in ("dm", "group"):
            return
        m = re.search(r.capture, text)
        if m is None or not m.group(1) or m.group(1)[0] in "了吧啊哦呢一个声什":
            return
        data = self._load(member)
        if data["nick"].get(USER):
            return
        data["nick"][USER] = {"name": m.group(1), "at": iso(at), "self": True}
        self._save(member, data)

    def _nick_options(self, member: str, other: str) -> list[str]:
        style = self._data(member).get("style") or {}
        text = str(style.get("说话风格") or "") + str(style.get("称呼") or "")
        out: list[str] = []
        target = self._name(other)
        for m in re.finditer(self.rules.nick.hint, text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            before = text[max(line_start, m.start() - 30) : m.start()]
            about_user = other == USER and not any(
                n and n in before for k, n in self._names().items() if k != USER
            )
            if about_user or (other != USER and target in before):
                out += [g for g in m.groups() if g]
        return list(dict.fromkeys(out))

    def nicks(self, member: str) -> dict[str, str]:
        return {k: str(v.get("name", "")) for k, v in self._load(member)["nick"].items()}

    # --- 跟人学 -----------------------------------------------------------------------

    def _own(self, who: str) -> list[str]:
        """她自己本来就会说的：短回复和口头禅（用户没有）。"""
        if who == USER:
            return []
        h = self.habits(who)
        out = [w for ws in h.shorts.values() for w in ws]
        phrases = (self._data(who).get("style") or {}).get("口头禅") or []
        out += [str(p).strip("。.！!") for p in phrases if 2 <= len(str(p)) <= 8]
        return list(dict.fromkeys(w for w in out if w))

    def _tics(self, who: str) -> list[str]:
        """这个人说话里能被学走的东西：网络用语，加上她自己的短回复和口头禅。"""
        return list(dict.fromkeys([*self.rules.learn.slang, *self._own(who)]))

    def on_event(self, event: Event) -> None:
        """有人在生活群 / 私聊 / 互聊里说了话：在乎说话人的成员听到口头禅就记一次；
        她自己用了学来的说法就记一次「用过」（不会忘）。"""
        if not self.enabled():
            return
        room = str(event.data.get("room", ""))
        info = self.rt.repo.chat_room(room) or {}
        if info.get("kind", "") not in LIFE_KINDS:
            return
        msg = self.rt.repo.chat_message(int(event.data.get("id", 0) or 0))
        if not msg:
            return
        text = str(msg["text"] or "")
        author = USER if event.data.get("from_user") else (event.member or "")
        if not author:
            return
        if author != USER:
            self._used(author, text, event.at)
            self._capture_nick(author, text, info, event.at)
            from roundtable.core.member.drift import Drift

            if Drift(self.rt).count(author, text, event.at, self):  # 口癖换了：习惯重新算
                self._cache.pop("habits:" + author, None)
        listeners = [m for m in info.get("members") or [] if m != author]
        tics = [w for w in self._tics(author) if w in text]
        if not tics:
            return
        from roundtable.core.member.love import Heart  # love 不依赖本模块

        heart, r = Heart(self.rt, names=self.names), self.rules.learn
        for m in listeners:
            if heart.care(m, author, event.at) < r.care:
                continue
            data, mine = self._load(m), set(self._own(m))
            for w in tics:
                if w in mine or w in data["learned"]:
                    continue
                seen = data["heard"].setdefault(w, {"from": author, "n": 0})
                seen["n"] = int(seen.get("n", 0)) + 1
                seen["from"] = author
                roll = random.Random(f"lingo-learn:{m}:{w}:{seen['n']}").random()
                if seen["n"] >= r.times and roll < r.chance:
                    data["learned"][w] = {"from": author, "at": iso(event.at), "used": ""}
                    data["heard"].pop(w, None)
            data["learned"] = dict(list(data["learned"].items())[-r.keep :]) if r.keep else {}
            data["heard"] = dict(list(data["heard"].items())[-40:])
            self._save(m, data)

    def _used(self, member: str, text: str, at: datetime) -> None:
        data = self._load(member)
        hit = [w for w in data["learned"] if w in text]
        if hit:
            for w in hit:
                data["learned"][w]["used"] = iso(at)
            self._save(member, data)

    def learned(self, member: str, at: datetime) -> list[tuple[str, str]]:
        """还记得的学来的说法 [(说法, 跟谁学的)]；太久没用的忘掉。"""
        days = self.rules.learn.forget_days
        from roundtable.core.member.drift import Drift

        kept = Drift(self.rt).promoted(member) if Drift(self.rt).enabled() else {}
        out = []
        for w, v in self._load(member)["learned"].items():
            last = v.get("used") or v.get("at") or ""
            if w in kept or (last and at - datetime.fromisoformat(last) <= timedelta(days=days)):
                out.append((w, str(v.get("from", ""))))
        return out

    # --- 发出前收尾 -------------------------------------------------------------------

    def finish(
        self,
        member: str,
        text: str,
        *,
        asked: str = "",
        energy: float = 0.6,
        rng: random.Random | None = None,
    ) -> list[str]:
        """发出前收尾：返回要发的几条（通常一条；打错字又改口时多一条「*正字」）。"""
        if not text.strip() or not self.enabled():
            return [text]
        rng = rng or random.Random(f"lingo-finish:{member}:{text}")
        stale = self.rt.config.roundtable.chat.memes.stale
        text = strip_stale(text, stale)  # 老梗烂梗：含老梗的那一小句不说（10-31 用户）
        text = strip_phrases(text, Slop(self.rt).active())  # AI 腔：8 位一起高频、你很少说的
        text = strip_stage(text)  # 括号里的动作描写（「（合上折扇）」）：聊天软件里没人这么打字
        if not text.strip():
            return [""]
        if not self.styled(member):  # 没有人物卡：只补她的招牌小尾巴（配置里写了的话）
            return [_tidy(self.signature_tail(member, text, rng))]
        h = self.habits(member)
        out = _BARE_FIX.sub("", text, count=1) or text  # 一开口就「不对，是……」：改口没有对象
        out = self.colloquial(out, asked)
        out = self.own_voice(member, out, rng)
        out = self.show(member, out, rng)
        out = _LAUGH.sub(lambda m: self._laugh(m.group(0), h, rng), out)
        out = "\n".join(self._end(line, h, rng) for line in out.split("\n"))
        out = self.signature_tail(member, out, rng)
        out = self._sprinkle(out, h, rng)
        out = self._cap(member, out)
        out = _tidy(out)
        if not out:
            return [""]
        fixed = self._typo(member, out, energy, rng)
        return fixed if fixed else [out]

    def _cap(self, member: str, text: str) -> str:
        """特色限量（10-31 用户反向找问题：口头禅、表情、颜文字、自称全堆一条里像在完成清单）：
        一条里「颜文字 / emoji」「招牌自称」「口头禅」超过 max_traits 样时，先删颜文字和 emoji，
        还多就把自称换成「我」。单独成条的表情包不算。"""
        cap = self.rules.max_traits
        if not cap or not text.strip():
            return text
        from roundtable.core.member.voice import signature_of  # voice 只在函数里引用本模块

        sig = signature_of(self.rt, member)
        selves = list(sig.self_names) if sig is not None else []
        faces = [*self.rules_kaomoji_all()]
        has_face = bool(_EMOJI.search(text)) or any(k in text for k in faces)
        has_self = any(n in text for n in selves)
        phrases = (self._data(member).get("style") or {}).get("口头禅") or []
        tics = [str(p).strip("。.！!") for p in phrases if 2 <= len(str(p).strip("。.！!")) <= 8]
        has_tic = any(w in text for w in tics)
        if has_face + has_self + has_tic <= cap:
            return text
        out = _EMOJI.sub("", text)
        for k in sorted(faces, key=len, reverse=True):
            out = out.replace(k, "")
        out = _tidy(out) or text
        if has_self + has_tic > cap:
            for n in sorted(selves, key=len, reverse=True):
                out = out.replace(n, "我")
        return out

    def _sprinkle(self, text: str, h: Habits, rng: random.Random) -> str:
        """一条里一个表情都没有时，按概率在最后一行末尾补一个她自己的颜文字（问句、很短的不补）。"""
        chance = self.rules.kaomoji_sprinkle
        if not chance or not h.kaomoji or STICKER_RE.search(text) or _EMOJI.search(text):
            return text
        if any(k in text for k in (*self.rules_kaomoji_all(), *h.kaomoji)):
            return text
        if any(w in text for w in self.rules.sprinkle_skip):
            return text  # 想不起来、难过、道歉时不补笑脸（10-31 第 3 次试跑：想不起来配笑脸）
        light = {k for v in self.rules.sprinkle_vibes for k in self.rules.kaomoji.get(v, _NO).items}
        faces = [k for k in h.kaomoji if k in light]
        lines = text.split("\n")
        last = lines[-1].rstrip()
        if not faces or len(last) < 4 or last.endswith(("？", "?", "…")):
            return text
        if rng.random() >= chance:
            return text
        lines[-1] = f"{last}{rng.choice(faces)}"
        return "\n".join(lines)

    def rules_kaomoji_all(self) -> tuple[str, ...]:
        return tuple(k for pick in self.rules.kaomoji.values() for k in pick.items)

    def signature_tail(self, member: str, text: str, rng: random.Random) -> str:
        """招牌的分寸（10-31 第 3 次试跑：「喵」句句都有、「本小姐」一半都有，反而像设定）：
        - 小尾巴（如「喵」）：每条按 tail_chance 掷一次——中了就只留一个
          （没有就补在最后一个汉字后面，标点、表情之前），没中就把句尾的都去掉；
        - 自称（如「本小姐」）：每条按 self_chance 掷一次，没中就换成「我」。"""
        from roundtable.core.member.voice import signature_of  # voice 只在函数里引用本模块

        sig = signature_of(self.rt, member)
        if sig is None or not text.strip():
            return text
        out = text
        if sig.self_names and rng.random() >= sig.self_chance:
            for name in sorted(sig.self_names, key=len, reverse=True):
                out = out.replace(name, "我")
        if not sig.tails:
            return out
        keep = rng.random() < sig.tail_chance
        protect = [w for w in (*sig.words, *sig.self_names) if any(t in w for t in sig.tails)]
        for k, w in enumerate(protect):
            out = out.replace(w, f"\x01{k}\x02")
        tails = "|".join(re.escape(t) for t in sorted(sig.tails, key=len, reverse=True))
        out = re.sub(rf"(?<=[\u4e00-\u9fff])(?:{tails})+(?![\u4e00-\u9fff])", "", out)
        for k, w in enumerate(protect):
            out = out.replace(f"\x01{k}\x02", w)
        if keep:
            lines = out.split("\n")
            for n in range(len(lines) - 1, -1, -1):
                plain = _SKIP.sub(lambda m: "\0" * len(m.group(0)), lines[n])
                hit = _LAST_HAN.search(plain)
                if hit is not None:
                    tail = rng.choice(sig.tails)
                    lines[n] = lines[n][: hit.end()] + tail + lines[n][hit.end() :]
                    break
            out = "\n".join(lines)
        return out

    def _marks(self, who: str) -> tuple[list[str], list[str], str]:
        """一个人说话的指纹：(口头禅, 颜文字, 自称)。"""
        style = self._data(who).get("style") or {}
        phrases = [str(p).strip("。.！!？?～~ ") for p in style.get("口头禅") or []]
        me = str(style.get("自称") or "").strip("。. ")
        return [p for p in phrases if len(p) >= 3], list(self.habits(who).kaomoji), me

    def own_voice(self, member: str, text: str, rng: random.Random) -> str:
        """把草稿里串进来的别人的口吻换回她自己的（学来的说法不算串）。"""
        if not self.rules.own_voice:
            return text
        phrases, kaomoji, me = self._marks(member)
        mine = (
            set(phrases) | set(kaomoji) | {w for w, _ in self.learned(member, self.rt.budget.now())}
        )
        others = [m for m in self._names() if m not in (member, USER)]
        out = text
        for other in others:
            their_phrases, their_kao, their_me = self._marks(other)
            for k in their_kao:
                if k in out and k not in mine:
                    out = out.replace(k, rng.choice(kaomoji) if kaomoji else "")
            for p in their_phrases:
                if p in out and p not in mine:
                    out = self._drop_clause(out, p)
            if their_me and their_me != "我" and their_me not in (me, *mine) and their_me in out:
                out = out.replace(their_me, me or "我")
        return out

    def show(self, member: str, text: str, rng: random.Random) -> str:
        """心情别直说：「好开心」「无语了」这类换成她自己的颜文字或符号（2026-10-31 用户定）。
        那一小句只剩这个词时整句换掉；还有别的话就去掉这个词，表情接在句末。"""
        r = self.rules.show
        if not r.enabled:
            return text
        mine = self.kaomoji(member)
        groups = self.rules.kaomoji
        lines = []
        for line in text.split("\n"):
            clauses = _CLAUSE.findall(line) or [line]
            out, tail = [], ""
            for c in clauses:
                hit = next(
                    ((rule, w) for rule in r.feelings.values() for w in rule.words if w in c),
                    None,
                )
                if hit is None:
                    out.append(c)
                    continue
                rule, word = hit
                fit = [
                    k
                    for v in rule.vibes
                    for k in (groups[v].items if v in groups else ())
                    if k in mine
                ]
                if fit and (not rule.marks or rng.random() < r.kaomoji):
                    face = rng.choice(fit)  # 她分到的、气质对得上的颜文字
                elif rule.marks:
                    face = rng.choice(rule.marks)
                else:
                    face = rng.choice(mine) if mine else ""
                rest = re.sub(
                    r"^[啊呀，,\s]+|[啊呀]+(?=[，,。！!？?～~…]*$)", "", c.replace(word, "")
                )
                if re.sub(r"[，,。！!？?～~…\s了的啊呀吧呢真太好超]", "", rest):
                    out.append(rest)
                    tail = tail or face
                else:
                    out.append(face)
            joined = "".join(out).strip()
            lines.append(f"{joined}{tail}" if tail else joined)
        return "\n".join(lines)

    @staticmethod
    def _drop_clause(text: str, phrase: str) -> str:
        """删掉含 phrase 的那一小句；删光了就只去掉这个词组本身，还不行就原样。"""
        lines = []
        for line in text.split("\n"):
            if phrase not in line:
                lines.append(line)
                continue
            keep = []
            for c in _CLAUSE.findall(line):  # 只去掉这个词组；那一小句只剩它时整句去掉
                left = c.replace(phrase, "").strip(" ") if phrase in c else c
                if re.sub(r"[，,。！!？?～~…\s]", "", left):
                    keep.append(left)
            rest = "".join(keep).rstrip("，,")
            residue = line.replace(phrase, "").strip(" ，,")
            meaningful = re.sub(r"[，,。！!？?～~…\s]", "", residue)
            lines.append(rest or (residue if meaningful else line))
        return "\n".join(lines)

    def colloquial(self, text: str, asked: str = "") -> str:
        """书面词换成口语；解释句、复述对方的分句能删就删（删光了就留原话）。"""
        r = self.rules
        out = text
        for k in sorted(r.formal, key=len, reverse=True):
            out = out.replace(k, r.formal[k])
        lines = []
        for line in out.split("\n"):
            if _SKIP.search(line) and not _SKIP.sub("", line).strip():
                lines.append(line)
                continue
            clauses = _CLAUSE.findall(line)
            keep = [c for c in clauses if not self._explains(c) and not self._recaps(c, asked)]
            lines.append("".join(keep).rstrip("，,") if keep else line)
        return "\n".join(lines)

    def _explains(self, clause: str) -> bool:
        return any(re.search(p, clause) for p in self.rules.explain)

    def _recaps(self, clause: str, asked: str) -> bool:
        n = self.rules.recap_chars
        core = re.sub(r"[，,。！!？?～~…\s]", "", clause)
        said = re.sub(r"[，,。！!？?～~…\s]", "", asked or "")
        if len(core) < n or len(said) < n:
            return False
        return any(core[i : i + n] in said for i in range(len(core) - n + 1))

    def _laugh(self, raw: str, h: Habits, rng: random.Random) -> str:
        pick = self.rules.laughs.get(h.laugh)
        if pick is None:
            return raw
        if pick.fixed:
            return pick.fixed
        n = rng.randint(pick.min, max(pick.min, pick.max))
        return pick.head + pick.unit * n

    def _end(self, line: str, h: Habits, rng: random.Random) -> str:
        """句尾换成她的标点习惯（问号、表情、引号结尾不动；句号党不动）。"""
        if h.punct_end is None or not line.strip() or _KEEP_END.search(line):
            return line
        if STICKER_RE.search(line) and not STICKER_RE.sub("", line).strip():
            return line
        if h.punct_end == "":
            return _END.sub("", line)
        if len(line.strip()) <= 2:  # 「6」「嗯」「啊？」这种一两个字的不加
            return line
        if rng.random() >= self.rules.punct_chance:
            return line
        return _END.sub("", line) + h.punct_end

    def _typo(self, member: str, text: str, energy: float, rng: random.Random) -> list[str]:
        r = self.rules.typo
        if not r.pairs:
            return []
        careful = _num(self._traits(member).get("较真", 3)) - self._cast_mean().get("较真", 3)
        chance = r.chance * (r.tired if energy < 0.3 else 1.0)
        chance *= max(0.2, 1 - r.careless * careful)
        if rng.random() >= chance:
            return []
        plain = _SKIP.sub(lambda m: "\0" * len(m.group(0)), text)
        options = [(a, b) for a, b in r.pairs if a in plain]
        if not options:
            return []
        right, wrong = rng.choice(options)
        at = plain.index(right)
        typo = text[:at] + wrong + text[at + len(right) :]
        fix = r.fix_base + sum(
            c * (_num(self._traits(member).get(t, 3)) - 3) for t, c in r.fix_traits.items()
        )
        return [typo, f"*{right}"] if rng.random() < fix else [typo]
