"""健康与低谷（L4，docs/LIFE_STORY.md「健康与低谷」）。不调用模型，只看她自己的生活。

- **病了有人照顾**：她不舒服的那几天——生活阶段（``chat.engine.phase``）的小感冒，或事件库里
  带 ``story.sick`` 的那一步（过敏、扭脚、牙疼）——常一起玩的朋友照顾一下（带碗粥、买药），
  一场病一次。朋友 = 最近两周生活里和她一起待得最多的人（读生活事件，不读关系插件的表）。
- **低谷有前因后果**：最近几天接连有几件不顺（``story.bad`` / ``sick`` / ``stuck`` / ``farewell`` /
  ``homesick``），可能低落几天：说得出是哪几件事 → 第 1～2 天有朋友陪 → 第 3～5 天缓过来。
- **累久了想歇**：最近熬夜、犯困（``story.late`` / ``tired``），加上生活阶段压精力的那几天，攒够了
  就在最近的周末歇一天（空闲时段在宿舍「歇着」）。
- 生活阶段只读（``member_phase`` 的格式不动，成长总账照旧读）；心情精力照旧由 P3 按标签接。
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from roundtable.core.storage.repository import iso

PREFIX = "low_"
CARE, MOOD, REST = "low_care", "low_mood", "low_rest"
TAGS: dict[tuple[str, int], list[str]] = {
    (CARE, 0): ["story.cared"],
    (MOOD, 0): ["story.low"],
    (MOOD, 1): ["story.comforted"],
    (MOOD, 2): ["story.better"],
    (REST, 0): ["story.rest"],
}
BAD = frozenset({"story.bad", "story.sick", "story.stuck", "story.farewell", "story.homesick"})
TIRED = frozenset({"story.late", "story.tired"})
SICK_PHASES_GROUP = "sick"
_CLAUSE = re.compile(r"[，,。；;！!]")


def tags_of(story: str, step: int) -> list[str]:
    return list(TAGS.get((story, step), []))


class Lows:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self._friends: dict[tuple[str, str], Counter[str]] = {}

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.life.story.lows

    def enabled(self) -> bool:
        return self.rt.config.roundtable.chat.life.story.enabled and self.rules.enabled

    # --- 读 -------------------------------------------------------------------------

    def phase_row(self, member: str) -> dict[str, Any] | None:
        """她的生活阶段（只读）。"""
        from roundtable.core.member.phase import PHASE_KEY

        raw = self.rt.repo.setting(PHASE_KEY)
        try:
            row = (json.loads(raw) if raw else {}).get(member)
        except (ValueError, AttributeError):
            return None
        return row if isinstance(row, dict) else None

    def _phase_on(self, member: str, day: date) -> Any:
        """这天她在的生活阶段（阶段表里那一种），没有返回 None。"""
        row = self.phase_row(member)
        if not row:
            return None
        d = day.isoformat()
        if not str(row.get("start", ""))[:10] <= d <= str(row.get("end", ""))[:10]:
            return None
        kinds = self.rt.config.roundtable.chat.engine.phase.kinds
        return next((k for k in kinds if k.name == row.get("kind")), None)

    def phase_sick(self, member: str, day: date) -> bool:
        names = self.rt.config.roundtable.chat.life.story.phase_groups.get(SICK_PHASES_GROUP) or []
        kind = self._phase_on(member, day)
        return kind is not None and kind.name in names

    def sick(self, member: str, day: date, st: dict[str, Any]) -> bool:
        if self.phase_sick(member, day):
            return True
        d, y = day.isoformat(), (day - timedelta(days=1)).isoformat()
        return any(
            "story.sick" in (p.get("tags") or []) and p.get("date") in (d, y) for p in st["pending"]
        )

    def _recent(self, st: dict[str, Any], day: date, tags: frozenset[str]) -> list[dict[str, Any]]:
        lo = (day - timedelta(days=self.rules.window_days)).isoformat()
        d = day.isoformat()
        return [
            p
            for p in st["pending"]
            if lo <= p.get("date", "") < d
            and not p.get("skipped")
            and tags & set(p.get("tags") or [])
        ]

    def friend(
        self, member: str, day: date, others: list[str], rng: random.Random, tz: Any = None
    ) -> str:
        """最近两周生活里和她一起待得最多的人；都没有就随便一位。"""
        if not others:
            return ""
        key = (member, day.isoformat())
        if key not in self._friends:
            end = datetime.combine(day, datetime.min.time(), tzinfo=tz)
            seen: Counter[str] = Counter()
            for row in self.rt.repo.life_events_between(iso(end - timedelta(days=14)), iso(end)):
                crew = row.get("members") or []
                if row.get("kind") in ("gather", "pair") and member in crew:
                    seen.update(m for m in crew if m != member)
            self._friends[key] = seen
        seen = self._friends[key]
        best = [m for m in others if seen.get(m)]
        if best:
            top = max(seen[m] for m in best)
            return rng.choice(sorted(m for m in best if seen[m] == top))
        return rng.choice(others)

    # --- 推进 -----------------------------------------------------------------------

    def step(
        self,
        member: str,
        day: date,
        st: dict[str, Any],
        others: list[str],
        rng: random.Random,
        tz: Any = None,
    ) -> list[tuple[Any, str, dict[str, str]]]:
        """今天要开始的事：[(StorySpec, 同伴, 填空)]。改 st["lows"]。"""
        if not self.enabled():
            return []
        from roundtable.core.config.schema import StorySpec, StoryStep

        r = self.rules
        lows: dict[str, str] = st.setdefault("lows", {})
        out: list[tuple[Any, str, dict[str, str]]] = []
        d = day.isoformat()

        def since(key: str) -> int:
            last = lows.get(key)
            return (day - date.fromisoformat(last)).days if last else 10**6

        busy = {p.get("story") for p in st["pending"] if not p.get("done")}
        # 病了：一场病照顾一次（隔 5 天以上才算新的一场）
        if others and self.sick(member, day, st) and since("care") > 5:
            lows["care"] = d
            if rng.random() < r.care_chance:
                other = self.friend(member, day, others, rng, tz)
                text = rng.choice(list(r.care_texts))
                spec = StorySpec(
                    id=CARE, hours=[11, 21], steps=[StoryStep(text=text, tags=TAGS[(CARE, 0)])]
                )
                out.append((spec, other, {}))
        # 低谷：接连不顺 → 低落 → 有人陪 → 缓过来
        bad = self._recent(st, day, BAD)
        stories = {p.get("story") for p in bad}
        if (
            others
            and len(stories) >= r.need_bad
            and MOOD not in busy
            and since("low") >= r.low_cooldown_days
            and rng.random() < r.low_chance
        ):
            lows["low"] = d
            last = sorted(bad, key=lambda p: p.get("at", ""))[-2:]
            cause = "、又".join(dict.fromkeys(_clause(p.get("text", "")) for p in reversed(last)))
            other = self.friend(member, day, others, rng, tz)
            spec = StorySpec(
                id=MOOD,
                hours=[16, 22],
                steps=[
                    StoryStep(text=r.low_text[:60], tags=TAGS[(MOOD, 0)]),
                    StoryStep(
                        day=[1, 2],
                        text=rng.choice(list(r.comfort_texts)),
                        tags=TAGS[(MOOD, 1)],
                        place="out",
                    ),
                    StoryStep(day=[3, 5], text=r.better_text, tags=TAGS[(MOOD, 2)]),
                ],
            )
            out.append((spec, other, {"cause": cause}))
        # 累久了：最近的周末歇一天
        tired = len(self._recent(st, day, TIRED))
        for k in range(1, r.window_days + 1):
            kind = self._phase_on(member, day - timedelta(days=k))
            if kind is not None and kind.energy < 0:
                tired += 1
        if (
            tired >= r.need_tired
            and REST not in busy
            and since("rest") >= r.rest_cooldown_days
            and rng.random() < r.rest_chance
        ):
            lows["rest"] = d
            wait = (5 - day.weekday()) % 7 if day.weekday() < 5 else 0
            spec = StorySpec(
                id=REST,
                hours=[10, 14],
                steps=[StoryStep(day=wait, text=r.rest_text, tags=TAGS[(REST, 0)], rest=True)],
            )
            out.append((spec, "", {}))
        return out


def _clause(text: str) -> str:
    """一件事的第一小句（「跑步的时候崴了一下脚，在房间歇着」→「跑步的时候崴了一下脚」）。"""
    return _CLAUSE.split(text, maxsplit=1)[0][:16]
