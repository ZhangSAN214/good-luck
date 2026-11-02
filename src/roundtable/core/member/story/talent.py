"""觉醒擅长领域（L10，docs/LIFE_STORY.md「擅长」）。全由程序算，不调用模型。

- **证据只有两处**：人物卡种子（档案里提到这个领域的词，最多算 ``seed_cap`` 次）和生活里的练习
  （最近 ``window_days`` 天在 ``life_events`` 里做过的相关的事：去天台看星星、宿舍看书、大脑
  琢磨的事、生活事件库里带 domain 的事）。**不看办公室表现**（办公室的模型是电脑，群聊里的成员
  是人；10-30 用户定），不读 ``work.done``、不读会话和调用记录（测试守卫）。
- **阶段**：萌芽（证据开始聚在这个领域，她自己还没说）→ 觉醒（证据分和练习都够了，发一件生活
  事件「好像我挺会这个」并带上依据；交给 P6 写进档案 ``likes.擅长``，能在成长时间线撤回）→ 深耕
  （觉醒后一直在练：空闲时更常去做、楼里有人来请教，偶尔卡住几天再想通）。长期不练会生疏：发一件
  「手生了」，交给 P6 从档案去掉，回到萌芽。最多同时 ``max_talents`` 个。
- 所有成员同一套规则和阈值；领域清单在配置里，不按厂商写死。
- 不影响派活：工作模式不 import 本包（测试守卫）。
"""

from __future__ import annotations

import random
import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from roundtable.core.office.apartment import ACTIVITIES, DAYTIME_LABELS
from roundtable.core.storage.repository import iso

SPROUT, AWAKE, DEEP = "sprout", "awake", "deep"
AWAKENED = (AWAKE, DEEP)
_SCENE = re.compile(r"^地点：[^\n]*（([^\n（）]+)）\s*$", re.M)
_ACT_IDS = {label: aid for aid, label in ACTIVITIES.items()} | {
    label: aid for aid, label in DAYTIME_LABELS.items()
}
# 擅长各阶段的生活事件（id 以 talent_ 开头，配置里的事件不能用这个前缀）→ 交给 P3 的标签
TALENT_TAGS: dict[tuple[str, int], list[str]] = {  # (事件 id, 第几步) → 标签
    ("talent_awake", 0): ["story.talent"],
    ("talent_stuck", 0): ["story.stuck"],
    ("talent_stuck", 1): ["story.unstuck"],
}
TALENT_STORIES = ("talent_awake", "talent_ask", "talent_stuck", "talent_rusty")


class Talents:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.life.story.talent

    def enabled(self) -> bool:
        story = self.rt.config.roundtable.chat.life.story
        return story.enabled and self.rules.enabled

    # --- 证据 ---------------------------------------------------------------------

    def domains_of(self, row: dict[str, Any]) -> list[tuple[str, str]]:
        """一行生活事件算哪几个领域的练习：[(领域, 依据里怎么说)]。"""
        from roundtable.core.member.story.engine import STORY, parse_mark  # 互相引用

        kind = row.get("kind")
        topic = str(row.get("topic") or "")
        if kind == STORY:
            mark = parse_mark(topic)
            if mark is None:
                return []
            story, step, domain = mark
            if not domain or domain not in self.rules.domains:
                return []
            if story.startswith("talent_") and story != "talent_ask":
                return []  # 觉醒、卡住这些本身不算练习
            if story.startswith("progress_") and step == 0:
                return []  # 刚开始看还不算练过
            return [(domain, "做过相关的事")]
        if kind not in ("gather", "solo"):
            return []
        found = _SCENE.search(topic)
        label = found.group(1).strip() if found else ""
        place = str(row.get("place") or "")
        room = "dorm" if place.startswith("dorm") else place
        act = _ACT_IDS.get(label, "")
        out = []
        for name, d in self.rules.domains.items():
            if act and (f"{room}:{act}" in d.acts or act in d.acts):
                out.append((name, label))
            elif label and not act and any(w in label for w in d.words):
                out.append((name, label))  # 大脑琢磨的事（「琢磨「做菜」」）按词认
        return out

    def practice(
        self, members: list[str], day: date, tz: Any
    ) -> dict[str, dict[str, Counter[str]]]:
        """day 之前 window_days 天里每位成员各领域的练习：成员 → 领域 → {依据: 次数}。"""
        end = datetime.combine(day, datetime.min.time(), tzinfo=tz)
        start = end - timedelta(days=self.rules.window_days)
        out: dict[str, dict[str, Counter[str]]] = {m: {} for m in members}
        for row in self.rt.repo.life_events_between(iso(start), iso(end)):
            found = self.domains_of(row)
            if not found:
                continue
            for m in row.get("members") or ():
                if m not in out:
                    continue
                for domain, label in found:
                    out[m].setdefault(domain, Counter())[label] += 1
        return out

    def scores(self, text: str, practice: dict[str, Counter[str]]) -> dict[str, tuple[float, int]]:
        """领域 → (证据分, 练习次数)。证据分 = 种子 × 权重 + 练习次数。"""
        from roundtable.core.member.story.store import hits

        r = self.rules
        out = {}
        for name, d in r.domains.items():
            n = sum((practice.get(name) or Counter()).values())
            seed = hits(text, d.words, r.seed_cap)
            out[name] = (seed * r.seed_weight + n, n)
        return out

    def evidence(self, domain: str, practice: Counter[str], seeded: bool) -> str:
        """觉醒时说得出的依据：最近做得最多的一两样（不凭空吹）。"""
        top = [f"{label}{n}回" for label, n in practice.most_common(2)]
        text = f"最近{self.rules.window_days}天里{'、'.join(top)}" if top else ""
        if seeded:
            text += "，本来也喜欢" if text else "本来也喜欢"
        return text

    # --- 阶段 ---------------------------------------------------------------------

    def step(
        self,
        member: str,
        day: date,
        st: dict[str, Any],
        text: str,
        practice: dict[str, Counter[str]],
    ) -> list[tuple[str, str, str]]:
        """推进这位成员一天的擅长阶段（改 st）。返回今天要发生的事：[(事件 id, 领域, 依据)]。

        每天最多一件，免得一天全是「我好像挺会……」。交给 P6 的变化放进 st["p6"]。"""
        if not self.enabled():
            return []
        r = self.rules
        rng = random.Random(f"talent:{member}:{day.isoformat()}")
        talents: dict[str, dict[str, Any]] = st.setdefault("talents", {})
        scores = self.scores(text, practice)
        awake_n = sum(1 for t in talents.values() if t.get("stage") in AWAKENED)
        today: list[tuple[str, str, str]] = []
        d_iso = day.isoformat()
        for name in sorted(scores, key=lambda k: (-scores[k][0], k)):
            score, n = scores[name]
            t = talents.get(name)
            stage = (t or {}).get("stage")
            if stage in AWAKENED:
                since = date.fromisoformat(t["awake"])
                if n <= r.rusty_practice and (day - since).days >= r.window_days:
                    t.update(stage=SPROUT, since=d_iso)
                    awake_n -= 1
                    st.setdefault("p6", []).append(
                        {"op": "remove", "domain": name, "why": "好久没碰了", "date": d_iso}
                    )
                    if not today:
                        today.append(("talent_rusty", name, ""))
                    continue
                if stage == AWAKE and (day - since).days >= r.deep_days and n >= r.deep_practice:
                    t.update(stage=DEEP, deep=d_iso)
                    continue
                if stage == DEEP and not today and not self._stuck(st, name, day):
                    roll = rng.random()
                    if roll < r.stuck_chance:
                        today.append(("talent_stuck", name, ""))
                    elif roll < r.stuck_chance + r.ask_chance:
                        today.append(("talent_ask", name, ""))
                continue
            if score >= r.sprout and n >= 1:
                if t is None:
                    talents[name] = t = {"stage": SPROUT, "since": d_iso}
                if (
                    score >= r.awake
                    and n >= r.awake_practice
                    and awake_n < r.max_talents
                    and not today
                ):
                    seeded = score - n > 0
                    why = self.evidence(name, practice.get(name) or Counter(), seeded)
                    t.update(stage=AWAKE, awake=d_iso, evidence=why)
                    awake_n += 1
                    st.setdefault("p6", []).append(
                        {"op": "add", "domain": name, "why": why, "date": d_iso}
                    )
                    today.append(("talent_awake", name, why))
            elif t is not None and stage == SPROUT and score < r.sprout / 2:
                talents.pop(name)  # 证据散了：萌芽没长起来
        return today

    @staticmethod
    def _stuck(st: dict[str, Any], domain: str, day: date) -> bool:
        """这个领域正卡着（还有没发生的「想通了」）。"""
        return any(
            p.get("story") == "talent_stuck"
            and p.get("step") == 1
            and p.get("domain") == domain
            and not p.get("done")
            for p in st.get("pending") or ()
        )

    # --- 读 -----------------------------------------------------------------------

    def awakened(self, st: dict[str, Any]) -> dict[str, str]:
        """觉醒了的领域 → 阶段（awake / deep）。"""
        talents = st.get("talents") or {}
        return {k: v["stage"] for k, v in talents.items() if v.get("stage") in AWAKENED}

    def known(self, st: dict[str, Any], name: str) -> list[str]:
        """深耕了的领域，楼里的人怎么说她（自然话，没有等级和分数）。"""
        from roundtable.core.member.story.store import fill

        out = []
        for domain, stage in sorted(self.awakened(st).items()):
            spec = self.rules.domains.get(domain)
            if stage == DEEP and spec is not None and spec.known:
                out.append(fill(spec.known, name=name))
        return out

    def take(self, member: str) -> list[dict[str, Any]]:
        """取走等 P6 写进档案的擅长变化（取走就清掉）。只有成长插件调用。"""
        from roundtable.core.member.story import store

        st = store.load(self.rt, member)
        items = list(st.get("p6") or [])
        if items:
            st["p6"] = []
            store.save(self.rt, member, st)
        return items
