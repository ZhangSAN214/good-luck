"""各自的情感（docs/BRAIN.md「各自的情感」；2026-10-30 用户定）。

开心、难过、想念、生气、害羞、委屈、内疚、感激、吃醋、失落、满足、惊喜、心疼、紧张、期待、无聊
（``chat.engine.feel.names``），8 位各有各的理解和表达，不贴统一的标签，随经历慢慢变，可以五味杂陈：

- **起点按人物卡**：她对每种情绪的理解，最初从自我档案里人物卡的「情绪」章抽出来——那一段写着
  什么事让她开心 / 生气、开心时她什么样。卡里没写的（多半是想念）就去整张卡里找相关的句子；
  再找不到就空着，等经历来告诉她。
- **记下每次真有的情绪**：P3 状态每次反射（被夸、被说了几句、盼着的事成了 / 落空、替人难受……）
  心情真动了一下，就记成一段经历：哪种情绪、为了什么、什么时候。同一件事算哪种（被夸是害羞
  还是开心，被说是生气、委屈还是难过），**按她自己的理解来分**：和她理解里的哪种像就算哪种；
  性格也有影响（较真的更容易生气、温和的更容易难过或委屈、内向的被夸更容易害羞）。
  感情里的委屈、内疚、感激也记成经历；想念：好几天没和你说话、你终于来了，或者她主动找你。
- **五味杂陈**：同时在场的情绪（刚有过的、那句感情话、快见分晓的紧张、闷）最多写两种。
- **说话时带上**：意图卡里写一句「你心里的难过：……（她自己的理解）；上回：……（真发生过的事）」，
  只在这种情绪正在场时写（刚有过、心情明显高 / 低、对方说想她）。
- **随时间变**：每周成长反思（P6）可以用 ``feel`` 改写其中一种情绪的理解，必须引用这一周真实的
  情绪经历和原话；程序把关：不照抄别的成员对同一种情绪的理解、和原来不能一模一样、同一种情绪
  隔几周才能再改（``growth/gate.py``）。

全程序、不调模型。情绪经历存在 ``app_settings`` 的 ``member_feel:<成员>``，只有本模块写；
改写后的理解在自我档案 ``personality.情感``（只经 P6 成长写）。
8 位同一套规则（``chat.engine.feel``），差别只来自各自的人物卡、性格分和经历。只用于生活模式。
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.profile import ProfileStore
from roundtable.core.recall import grams
from roundtable.core.storage.repository import iso

KEY = "member_feel:"
PATH = ("personality", "情感")  # 成长改写后的理解：{情绪: 一句话}
USER = "user"
_LABEL = re.compile(r"^[\s>*\-•·#]*(?:\*\*)?([^：:\n]{1,14}?)(?:\*\*)?\s*[：:]\s*(?:\*\*)?\s*")
_HEAD = re.compile(r"^\s*#{2,6}\s*(.+?)\s*$")
_SENT = re.compile(r"[^。！？!?\n]+[。！？!?]?")
_MD = re.compile(r"\*\*|__|^\s*[-•·>]\s*", re.M)


def _flat(text: str) -> str:
    return _MD.sub("", re.sub(r"\s+", " ", text or "")).strip()


def _cut(text: str, limit: int) -> str:
    """按句子截到 limit 字以内（第一句就超长时硬截）。"""
    text = _flat(text)
    if len(text) <= limit:
        return text
    out = ""
    for s in _SENT.findall(text):
        if len(out) + len(s) > limit:
            break
        out += s
    return out.strip() or text[: limit - 1].rstrip() + "…"


def overlap(a: str, b: str) -> float:
    """a 的两字片段有多少出现在 b 里（0～1）。"""
    ga = grams(a, (2,))
    return len(ga & grams(b, (2,))) / len(ga) if ga else 0.0


def _label(line: str) -> tuple[str, str] | None:
    """一行是不是「标签：内容」或小标题（### 开心）：返回 (标签, 同一行后面的内容)。"""
    head = _HEAD.match(line)
    if head:
        return head.group(1).strip(), ""
    m = _LABEL.match(line)
    return (m.group(1), line[m.end() :].strip()) if m else None


def seed_from(data: dict[str, Any], names: dict[str, list[str]], limit: int) -> dict[str, str]:
    """从人物卡（自我档案里存的各章原文）抽出她对每种情绪最初的理解。

    先找情绪章里以这种情绪开头的那一条（「开心：……」「**难过时会**：……」「### 生气」
    「让我生气的事：」下一段）；没有就在情绪、人际关系、成长几章里找第一句提到它的话；都没有就不写。"""
    chapter = str((data.get("personality") or {}).get("情绪") or "")
    lines = [ln for ln in chapter.splitlines() if ln.strip()]
    nearby = "\n".join(
        str((data.get(sec) or {}).get(key) or "")
        for sec, key in (("personality", "情绪"), ("identity", "人际关系"), ("values", "成长"))
    )
    out: dict[str, str] = {}
    for emo, words in names.items():
        found = ""
        for i, ln in enumerate(lines):
            got = _label(ln)
            if not got or not any(w in got[0] for w in words):
                continue
            found = got[1]
            if not found and i + 1 < len(lines) and not _HEAD.match(lines[i + 1]):
                found = lines[i + 1]  # 「让我开心的事：」换行写
            break
        if not found:
            for sent in _SENT.findall(nearby):
                if any(w in sent for w in words) and len(sent.strip()) >= 6:
                    found = sent
                    break
        if found:
            short = _cut(found, limit)
            out[emo] = short if len(short) >= 15 else _flat(found)[: limit - 1] + "…"
    return out


class Feelings:
    def __init__(
        self,
        rt: Any,
        bus: EventBus | None = None,
        names: Callable[[], dict[str, str]] | dict[str, str] | None = None,
    ) -> None:
        self.rt = rt
        self.profiles = ProfileStore(rt)
        self._names = names
        if bus is not None:
            bus.subscribe("state.reflex", self.on_event)
            bus.subscribe("chat.message", self.on_event)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.feel

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and self.rules.enabled)

    def local(self, at: datetime) -> datetime:
        return home_zone(self.rt).local(at)

    # --- 她的理解 ----------------------------------------------------------------------

    def seed(self, member: str) -> dict[str, str]:
        data = self.profiles.current(member).data
        return seed_from(data, self.rules.names, self.rules.seed_chars)

    def grown(self, member: str) -> dict[str, str]:
        """成长改写过的（personality.情感）。"""
        got = (self.profiles.current(member).data.get(PATH[0]) or {}).get(PATH[1]) or {}
        return {str(k): str(v) for k, v in got.items() if v} if isinstance(got, dict) else {}

    def understanding(self, member: str) -> dict[str, str]:
        """她此刻对每种情绪的理解：成长改写过的优先，其余用人物卡里的。"""
        return self.seed(member) | self.grown(member)

    # --- 情绪经历（只有这里写 member_feel:*）--------------------------------------------

    def _load(self, member: str) -> dict[str, Any]:
        raw = self.rt.repo.setting(KEY + member)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        data.setdefault("episodes", [])
        return data

    def _save(self, member: str, data: dict[str, Any]) -> None:
        data["episodes"] = data["episodes"][-self.rules.keep :]
        self.rt.repo.set_setting(KEY + member, json.dumps(data, ensure_ascii=False))

    def episodes(
        self, member: str, emo: str = "", since: datetime | None = None
    ) -> list[dict[str, Any]]:
        out = self._load(member)["episodes"]
        if emo:
            out = [e for e in out if e["emo"] == emo]
        if since is not None:
            out = [e for e in out if datetime.fromisoformat(e["at"]) >= since]
        return out

    def record(self, member: str, emo: str, why: str, at: datetime) -> None:
        if not (self.enabled() and emo in self.rules.names and why):
            return
        why = why[: self.rules.why_chars]
        data = self._load(member)
        recent = data["episodes"][-3:]
        if any(e["emo"] == emo and e["why"] == why for e in recent):
            return  # 同一件事连着来几下，算一次
        day = self.local(at).date()
        same = [e for e in data["episodes"] if e["emo"] == emo]
        if sum(self.local(datetime.fromisoformat(e["at"])).date() == day for e in same) >= (
            self.rules.daily_max
        ):
            return  # 同一种一天记够了几回，别把别的挤掉
        data["episodes"].append({"emo": emo, "why": why, "at": iso(at)})
        self._save(member, data)

    def clear(self, member: str) -> None:
        self.rt.repo.set_setting(KEY + member, "")

    # --- 哪种情绪：按她自己的理解分 ------------------------------------------------------

    def classify(self, member: str, mood: float, why: str, reflex: str = "") -> str:
        """心情动了 mood 这么多，为了 why：她会觉得这是哪种情绪（不够明显时为空）。

        反射表里列了的事在那几种里分（被夸：害羞还是开心；被说：生气、委屈还是难过……），
        没列的按心情往上 / 往下在 positive / negative 里分；分法看她自己的理解和性格。"""
        r = self.rules
        listed = [e for e in r.reflexes.get(reflex, []) if e in r.names]
        if abs(mood) < (r.min_mood_listed if listed else r.min_mood) or not mood:
            return ""
        pool = listed or (r.positive if mood > 0 else r.negative)
        if len(pool) == 1:
            return pool[0]
        mine = self.understanding(member)
        traits = (self.profiles.current(member).data.get("personality") or {}).get("五项") or {}
        best, score = pool[0], float("-inf")
        for i, emo in enumerate(pool):
            s = r.lean_weight * (1 - i / len(pool)) if listed else 0.0
            s += r.match * overlap(why, mine.get(emo, ""))
            for trait, c in r.traits.get(emo, {}).items():
                with contextlib.suppress(TypeError, ValueError):
                    s += c * (float(traits.get(trait, 3)) - 3)
            if s > score:
                best, score = emo, s
        return best

    def _who(self, author: str) -> str:
        if author == USER:
            return self.rules.user_label
        names = self._names() if callable(self._names) else (self._names or {})
        return names.get(author) or self.rules.someone

    def _why(self, data: dict[str, Any]) -> str:
        if data.get("why"):
            return str(data["why"])
        msg = self.rt.repo.chat_message(int(data.get("msg") or 0)) if data.get("msg") else None
        if msg and msg.get("text"):
            text = re.sub(r"\s+", " ", str(msg["text"])).strip()[:18]
            return f"{self._who(str(msg['author']))}说「{text}」"
        return str(data.get("note") or data.get("name") or "")

    def on_event(self, event: Event) -> None:
        if not self.enabled():
            return
        if event.kind == "state.reflex" and event.member:
            mood = float(event.data.get("mood") or 0.0)
            why = self._why(event.data)
            emo = self.classify(event.member, mood, why, str(event.data.get("name") or ""))
            if emo:
                self.record(event.member, emo, why, event.at)
        elif event.kind == "chat.message" and event.data.get("from_user"):
            room = str(event.data.get("room") or "")
            if room.startswith("dm:"):
                self._missed(room[3:], room, int(event.data.get("id") or 0), event.at)

    def _missed(self, member: str, room: str, msg_id: int, at: datetime) -> None:
        """好几天没聊，你终于来找她了：想念。"""
        mine = [m for m in self.rt.repo.chat_messages(room, limit=40) if m["author"] == USER]
        before = [m for m in mine if m["id"] < msg_id]
        if not before:
            return
        gap = at - datetime.fromisoformat(before[-1]["created_at"])
        if gap >= timedelta(days=self.rules.miss_days):
            self.record(member, self.rules.missing, self.rules.miss_back, at)

    # --- 说话时：此刻在场的那种情绪 -----------------------------------------------------

    def hints(self, *, heart: str = "", tense: bool = False, novelty: float = 0.0) -> list[str]:
        """大脑交来的此刻情绪：那句感情话（委屈 / 内疚 / 感激 / 吃醋）、快见分晓的事（紧张）、
        新鲜感攒得太满（无聊）。"""
        r = self.rules
        out = [r.love[heart]] if heart in r.love else []
        if tense:
            out.append(r.tense)
        if novelty >= r.bored_above:
            out.append(r.bored)
        return out

    def active(
        self, member: str, *, mood: float, last: str, at: datetime, hints: list[str] | None = None
    ) -> list[str]:
        """此刻正在场的情绪（最多 mix_max 种，五味杂陈）：对方说想她 → 想念；大脑交来的
        （那句感情话、快见分晓的紧张、闷）；刚有过的；心情明显好 / 不好 → 开心 / 最近那种负面的。"""
        r = self.rules
        out: list[str] = []

        def add(emo: str) -> None:
            if emo and emo in r.names and emo not in out:
                out.append(emo)

        if last and any(w in last for w in r.miss_words):
            add(r.missing)
        for emo in hints or []:
            add(emo)
        fresh = self.episodes(member, since=at - timedelta(hours=r.linger_hours))
        for e in reversed(fresh):
            add(str(e["emo"]))
        if not out and mood >= r.mood_high:
            add(r.positive[0])
        if not out and mood <= r.mood_low:
            neg = [e for e in self.episodes(member) if e["emo"] in r.low]
            add(str(neg[-1]["emo"]) if neg else r.negative[0])
        return out[: r.mix_max]

    def _when(self, at: datetime, now: datetime) -> str:
        if now - at < timedelta(hours=self.rules.linger_hours):
            return "刚才"
        a, b = self.local(at).date(), self.local(now).date()
        days = (b - a).days
        return "昨天" if days == 1 else "前天" if days == 2 else f"{a.month}月{a.day}日"

    def _one(self, member: str, emo: str, at: datetime) -> tuple[str, str] | None:
        """(她的理解, 上回那件事)；卡里没写过、也还没经历过时为 None（不写空话）。"""
        r = self.rules
        mine = self.understanding(member).get(emo, "")
        past = [e for e in self.episodes(member, emo) if datetime.fromisoformat(e["at"]) <= at]
        if not (mine or past):
            return None
        text = _cut(mine or r.unknown, r.mine_chars)
        if past:
            e = past[-1]
            when = self._when(datetime.fromisoformat(e["at"]), at)
            text += r.last.format(when=when, why=e["why"])
        return emo, text

    def line(self, member: str, emo: str | list[str], at: datetime) -> str:
        """意图卡里的那一句：她自己对这种情绪的理解 + 上回真有这种情绪是为了什么；
        同时有两种时写成五味杂陈。"""
        r = self.rules
        emos = [emo] if isinstance(emo, str) else list(emo)
        parts = [p for p in (self._one(member, e, at) for e in emos if e) if p]
        if not parts:
            return ""
        first = r.line.format(emo=parts[0][0], mine=parts[0][1])[: r.line_chars]
        if len(parts) == 1:
            return first
        emo2, mine2 = parts[1]
        return r.mix.format(first=first, emo=emo2, mine=mine2)[: r.mix_chars]

    # --- 成长材料 ----------------------------------------------------------------------

    def week_lines(self, member: str, start: date, end: date) -> list[tuple[str, str]]:
        """[start, end) 这几天的情绪经历：(哪天, 「难过：为了什么」)，给每周成长反思当材料。"""
        out = []
        for e in self.episodes(member):
            day = self.local(datetime.fromisoformat(e["at"])).date()
            if start <= day < end:
                out.append((day.isoformat(), f"{e['emo']}：{e['why']}"))
        return out
