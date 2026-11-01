"""爱好进度（L1，docs/LIFE_STORY.md「爱好进度」）：书读到哪、番追到哪、游戏打到哪。不调用模型。

- 每种爱好（``chat.life.story.progress.kinds``）同时只在做一样东西：优先从她档案的「最爱」里挑
  含这种爱好的词的那一项（各人不同），没有再从配置的 things 里挑（随机数由成员和日期派生）。
- 前一天生活里做了相关的事（``acts``：宿舍看书、休息室打游戏……）每回往前推几格；没有相关活动的
  爱好（追剧）每天按 ``daily`` 的机会推一次。推进那天有 ``mid_chance`` 顺嘴记一句「看到第 8 集」。
- 推满就「看完了 / 通关了」（好事，记一件，算对应擅长领域的练习），隔 ``gap_days`` 天再开始下一样。
- 进度存在她自己的生活状态里（``member_life:<成员>`` 的 ``progress``），只有本包写。
"""

from __future__ import annotations

import random
import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from roundtable.core.member.profile import ProfileStore
from roundtable.core.office.apartment import ACTIVITIES, DAYTIME_LABELS
from roundtable.core.storage.repository import iso

PREFIX = "progress_"
START, MID, DONE = 0, 1, 2
_SCENE = re.compile(r"^地点：[^\n]*（([^\n（）]+)）\s*$", re.M)
_ACT_IDS = {label: aid for aid, label in ACTIVITIES.items()} | {
    label: aid for aid, label in DAYTIME_LABELS.items()
}
_STAR = re.compile(r"[★☆⭐\s]+")


def tags_of(step: int) -> list[str]:
    return ["story.good"] if step == DONE else []


class Progress:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.life.story.progress

    def enabled(self) -> bool:
        return self.rt.config.roundtable.chat.life.story.enabled and self.rules.enabled

    # --- 前一天做了什么 -------------------------------------------------------------

    def yesterday(self, members: list[str], day: date, tz: Any) -> dict[str, Counter[str]]:
        """前一天每位成员做过的事：成员 → {「房间:活动」: 回数}（也按活动 id 记一份）。"""
        end = datetime.combine(day, datetime.min.time(), tzinfo=tz)
        out: dict[str, Counter[str]] = {m: Counter() for m in members}
        for row in self.rt.repo.life_events_between(iso(end - timedelta(days=1)), iso(end)):
            if row.get("kind") not in ("gather", "solo"):
                continue
            found = _SCENE.search(str(row.get("topic") or ""))
            act = _ACT_IDS.get(found.group(1).strip(), "") if found else ""
            if not act:
                continue
            place = str(row.get("place") or "")
            room = "dorm" if place.startswith("dorm") else place
            for m in row.get("members") or ():
                if m in out:
                    out[m][f"{room}:{act}"] += 1
                    out[m][act] += 1
        return out

    # --- 推进 ---------------------------------------------------------------------

    def _favorites(self, member: str) -> list[str]:
        fav = (ProfileStore(self.rt).current(member).data.get("likes") or {}).get("最爱") or []
        if isinstance(fav, str):
            fav = re.split(r"[、，,／/；;]", fav)
        return [_STAR.sub("", str(x)) for x in fav if _STAR.sub("", str(x))]

    def _pick(self, member: str, name: str, spec: Any, rng: random.Random, last: str) -> str:
        mine = [f for f in self._favorites(member) if any(w in f for w in spec.words)]
        pool = [t for t in mine if t != last] or [t for t in spec.things if t != last]
        return rng.choice(pool or list(spec.things))

    def step(
        self, member: str, day: date, st: dict[str, Any], did: Counter[str]
    ) -> list[tuple[str, int, str, str]]:
        """推进这位成员各爱好一天（改 st）。返回今天值得记一笔的：[(事件 id, 步, 话, 领域)]。"""
        if not self.enabled():
            return []
        rng = random.Random(f"progress:{member}:{day.isoformat()}")
        books: dict[str, dict[str, Any]] = st.setdefault("progress", {})
        out: list[tuple[str, int, str, str]] = []
        d = day.isoformat()
        for name, spec in sorted(self.rules.kinds.items()):
            cur = books.get(name)
            words = {"unit": spec.unit}
            if cur is None or cur.get("done"):
                since = (cur or {}).get("done")
                if since and (day - date.fromisoformat(since)).days < spec.gap_days:
                    continue
                thing = self._pick(member, name, spec, rng, (cur or {}).get("thing", ""))
                total = rng.randint(*sorted(spec.total))
                books[name] = cur = {"thing": thing, "n": 0, "total": total, "start": d}
                out.append((PREFIX + name, START, self._fill(spec.start, cur, words), spec.domain))
                continue
            times = sum(did.get(a, 0) for a in spec.acts)
            if not times and spec.daily and rng.random() < spec.daily:
                times = 1
            if not times:
                continue
            lo, hi = sorted(spec.per)
            cur["n"] = min(cur["total"], cur["n"] + sum(rng.randint(lo, hi) for _ in range(times)))
            if cur["n"] >= cur["total"]:
                cur["done"] = d
                out.append((PREFIX + name, DONE, self._fill(spec.done, cur, words), spec.domain))
            elif rng.random() < spec.mid_chance:
                out.append((PREFIX + name, MID, self._fill(spec.mid, cur, words), spec.domain))
        return out

    @staticmethod
    def _fill(text: str, cur: dict[str, Any], words: dict[str, str]) -> str:
        from roundtable.core.member.story.store import fill

        return fill(text, thing=cur["thing"], n=str(cur["n"]), **words)[:78]

    def view(self, st: dict[str, Any]) -> list[dict[str, Any]]:
        """在做的东西和进度（自然话，给接口和生活页看）。"""
        out = []
        for name, cur in sorted((st.get("progress") or {}).items()):
            spec = self.rules.kinds.get(name)
            if spec is None:
                continue
            tmpl = spec.done if cur.get("done") else spec.mid
            text = self._fill(tmpl, cur, {"unit": spec.unit})
            out.append(
                {"kind": name, "thing": cur["thing"], "done": bool(cur.get("done")), "text": text}
            )
        return out
