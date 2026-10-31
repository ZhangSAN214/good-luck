"""口癖和说话风格自己慢慢变（docs/BRAIN.md「口癖慢慢变」；2026-10-31 用户定）。

人说话的小习惯不是一成不变的：一个颜文字、一句「笑死」用了一阵子会腻，换个新的；一句口头禅
说多了自己也烦，歇一阵再用；跟在乎的人学来的说法用得多了，就成了自己的。心情好坏时用的表情也不一样。

- **用腻了就换**：她在生活群、私聊、互聊里发出去的话，程序数一数用了几次自己的颜文字、短回复、
  口头禅。最近两周用得太勤、又已经用了一阵子，每天有一定机会腻了：颜文字和短回复换成一个新的，
  只从**她自己的风格**里挑（颜文字只在和她性格最像的几组里挑），而且**别人正在用的不挑**
  （不越聊越像）；口头禅不换，歇几周再用。好奇、外向的人更容易腻，较真的人更念旧。
- **学来的变成自己的**：跟人学的说法（``lingo`` 的「跟人学」）她自己用够几次，就成了她的口头禅，
  不会再忘。
- **心情不同用的表情不同**：心情明显好时，她分到的颜文字里挑开心那组的；心情差时挑丧气、冷淡那组的
  （写进意图卡「此刻说话」）。

全程序、不调模型；8 位同一套规则（``chat.engine.lingo.drift``），差别只来自性格分和她自己说过的话。
状态存在 ``app_settings`` 的 ``member_drift:<成员>``，只有本模块写。只用于生活模式。
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.storage.repository import iso

KEY = "member_drift:"
KINDS = {"k": "kaomoji", "s": "short", "c": "catchphrase", "w": "learned"}


class Drift:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.lingo.drift

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and engine.lingo.enabled and self.rules.enabled)

    # --- 存取（只有这里写 member_drift:*）------------------------------------------

    def _load(self, member: str) -> dict[str, Any]:
        raw = self.rt.repo.setting(KEY + member)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        for k in ("uses", "since", "rested", "promoted", "rolled"):
            if not isinstance(data.get(k), dict):
                data[k] = {}
        if not isinstance(data.get("swaps"), list):
            data["swaps"] = []
        return data

    def _save(self, member: str, data: dict[str, Any]) -> None:
        self.rt.repo.set_setting(KEY + member, json.dumps(data, ensure_ascii=False))

    def clear(self, member: str) -> None:
        self.rt.repo.set_setting(KEY + member, "")

    # --- 读：现在用的是哪些 ------------------------------------------------------------

    def swaps(self, member: str) -> list[dict[str, Any]]:
        return list(self._load(member)["swaps"])

    def mapping(self, member: str) -> dict[str, str]:
        """换过的：旧 → 现在用的（换了几次就顺着找到最新的）。"""
        out: dict[str, str] = {}
        for s in self._load(member)["swaps"]:
            if s.get("new"):
                out = {k: (s["new"] if v == s["old"] else v) for k, v in out.items()}
                out[s["old"]] = s["new"]
        return out

    def apply(self, member: str, items: tuple[str, ...]) -> tuple[str, ...]:
        """把一组颜文字 / 短回复换成她现在用的（换过的替掉，去重保持顺序）。"""
        if not self.enabled():
            return items
        m = self.mapping(member)
        return tuple(dict.fromkeys(m.get(x, x) for x in items))

    def resting(self, member: str, at: datetime) -> set[str]:
        """说腻了、正在歇着的口头禅。"""
        return {p for p, until in self._load(member)["rested"].items() if until > iso(at)}

    def catchphrases(self, member: str, phrases: list[str], at: datetime) -> list[str]:
        """她现在的口头禅：歇着的先不用，学来变成自己的加在后面。"""
        if not self.enabled():
            return phrases
        data = self._load(member)
        rest = self.resting(member, at)
        own = [p for p in phrases if p not in rest]
        return own + [w for w in data["promoted"] if w not in own]

    def promoted(self, member: str) -> dict[str, dict[str, str]]:
        return dict(self._load(member)["promoted"])

    # --- 记：她说了什么 ----------------------------------------------------------------

    def count(self, member: str, text: str, at: datetime, lingo: Any) -> list[dict[str, Any]]:
        """她发出去一句话：数一数用了自己的哪些说法；用得太勤的可能腻了。返回这次的变化。"""
        if not (self.enabled() and text and lingo.styled(member)):
            return []
        r = self.rules
        h = lingo.habits(member)
        from roundtable.core.member.voice import member_voice

        mine: dict[str, str] = {}
        for k in h.kaomoji:
            mine[f"k:{k}"] = k
        for ws in h.shorts.values():
            for w in ws:
                if len(w) >= 2:
                    mine[f"s:{w}"] = w
        for p in member_voice(self.rt, member).catchphrases:  # 已去掉歇着的、加上学来变成自己的
            mine[f"c:{p.strip('。.！!？?～~ ')}"] = p.strip("。.！!？?～~ ")
        for w, _ in lingo.learned(member, at):
            mine.setdefault(f"w:{w}", w)
        hits = [key for key, word in mine.items() if word and word in text]
        if not hits:
            return []
        data = self._load(member)
        cut = iso(at - timedelta(days=r.window_days))
        for key in hits:
            uses = [u for u in data["uses"].get(key, []) if u >= cut] + [iso(at)]
            data["uses"][key] = uses[-(r.wear_uses * 3) :]
            data["since"].setdefault(key, iso(at))
        changes = []
        for key in hits:
            change = self._maybe(member, key, mine[key], data, at, lingo)
            if change:
                changes.append(change)
        data["swaps"] = data["swaps"][-r.keep_swaps :]
        self._save(member, data)
        return changes

    def _maybe(
        self, member: str, key: str, word: str, data: dict[str, Any], at: datetime, lingo: Any
    ) -> dict[str, Any] | None:
        r = self.rules
        kind = KINDS[key[0]]
        n = len(data["uses"].get(key, []))
        if kind == "learned":  # 学来的用够几次：成了她自己的
            if n >= r.promote_uses and word not in data["promoted"]:
                src = dict(lingo.learned(member, at)).get(word, "")
                data["promoted"][word] = {"from": src, "at": iso(at)}
                return self._log(data, "promote", word, "", at, f"用了 {n} 次，成了自己的口头禅")
            return None
        held = datetime.fromisoformat(data["since"][key])
        if n < r.wear_uses or at - held < timedelta(days=r.min_days):
            return None
        day = home_zone(self.rt).local(at).date().isoformat()
        if data["rolled"].get(key) == day:
            return None  # 一天只掂量一次
        data["rolled"][key] = day
        data["rolled"] = dict(list(data["rolled"].items())[-60:])
        traits = lingo._traits(member)
        chance = r.chance + sum(c * (_num(traits.get(t, 3)) - 3) for t, c in r.traits.items())
        roll = random.Random(f"drift:{member}:{key}:{day}").random()
        if roll >= max(0.0, min(1.0, chance)):
            return None
        why = f"最近两周用了 {n} 次，用腻了"
        if kind == "catchphrase":
            until = at + timedelta(days=r.rest_days)
            data["rested"][word] = iso(until)
            data["uses"].pop(key, None)
            data["since"].pop(key, None)
            return self._log(data, "rest", word, "", at, why + f"，先歇 {r.rest_days} 天")
        new = self._pick(member, kind, word, data, at, lingo)
        if not new:
            return None
        data["uses"].pop(key, None)
        data["since"].pop(key, None)
        data["since"][f"{key[0]}:{new}"] = iso(at)
        return self._log(data, kind, word, new, at, why)

    def _pick(
        self, member: str, kind: str, old: str, data: dict[str, Any], at: datetime, lingo: Any
    ) -> str:
        """新的从她自己的风格里挑：别人正在用的不挑，最近刚换掉的不挑回来。"""
        r, lr = self.rules, lingo.rules
        h = lingo.habits(member)
        gone = {
            s["old"]
            for s in data["swaps"]
            if s.get("old") and s["at"] >= iso(at - timedelta(days=r.return_days))
        }
        crowd: dict[str, int] = {}
        for other in lingo._names():
            if other == member or not lingo.styled(other):
                continue
            oh = lingo.habits(other)
            for x in (*oh.kaomoji, *(w for ws in oh.shorts.values() for w in ws)):
                crowd[x] = crowd.get(x, 0) + 1
        if kind == "kaomoji":
            groups = lingo._rank(member, lr.kaomoji, "kaomoji")[: r.vibes_top]
            pool = [k for g in groups for k in lr.kaomoji[g].items]
            current = set(h.kaomoji)
        else:
            pool = [w for ws in lr.shorts.values() if old in ws for w in ws]
            current = {w for ws in h.shorts.values() for w in ws}
        fresh = [x for x in pool if x not in current and x not in gone and x != old]
        quiet = [x for x in fresh if crowd.get(x, 0) < r.crowd]
        if not (quiet or fresh):
            return ""
        rng = random.Random(f"drift-pick:{member}:{old}:{iso(at)}")
        return rng.choice(quiet or fresh)

    @staticmethod
    def _log(
        data: dict[str, Any], kind: str, old: str, new: str, at: datetime, why: str
    ) -> dict[str, Any]:
        entry = {"kind": kind, "old": old, "new": new, "at": iso(at), "why": why}
        data["swaps"].append(entry)
        return entry

    # --- 心情不同用的表情不同 ------------------------------------------------------------

    def mood_face(self, member: str, mood: float, lingo: Any) -> str:
        """心情明显好 / 差时，从她现在用的颜文字里挑对得上心情的那个（没有就空）。"""
        if not self.enabled():
            return ""
        r, groups = self.rules, lingo.rules.kaomoji
        if mood >= r.mood_high:
            vibes = r.up_vibes
        elif mood <= r.mood_low:
            vibes = r.down_vibes
        else:
            return ""
        mine = lingo.kaomoji(member)
        fit = [k for v in vibes if v in groups for k in groups[v].items if k in mine]
        return fit[0] if fit else ""


def _num(x: Any, default: float = 3.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default
