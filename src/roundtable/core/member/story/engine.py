"""生活事件库 + 长线剧情 + 每人的生活状态（P7 生活引擎 L0，docs/LIFE_STORY.md）。不调用模型。

- **挑事**：每位成员每个（主时区的）日子挑一次——先接着进行中的长线剧情，再按擅长阶段（L10），
  最后从事件库（``chat.life.story.stories``）里按时刻派生的随机数挑（成员 + 日期决定，可复现）。
  看星期、月份（季节）、档案里的爱好（``likes`` / ``need_likes``）、觉醒了的擅长（``domain``）、
  冷却（同一件事 ``cooldown_days``、同一分组 ``group_cooldown_days``：每季度最多病一次）。
  每人每天最多 ``max_per_day`` 件：大部分日子是平淡的。
- **因果和后续**：一件事的几步（``steps``）开始时就排好日子（感冒第 0 天嗓子疼 → 第 1 天躺着 →
  第 2～4 天好了；熬夜 → 第二天犯困），存在她的生活状态里，到点发生。
- **只发事件**：到点的事交给生活引擎写成 ``life_events``（kind = ``story``，场景「地点：…（事）」
  + 一行 ``生活事件：id/步/领域``），成员引擎转到总线时带上这一步的标签（``story_meta``），心情
  精力由 P3 按反射表接；夜间整理时 P2 当作没写成聊天的生活小事记进记忆；关系、档案本包都不写。
- **空闲去哪**（``prefer``）：作息表里的空闲时段不再按编号轮换，按各自档案里的爱好和觉醒了的
  擅长加权挑；生病歇着的那天不出门。之后照旧交给大脑分区再挑（驱力、习惯、目标）。
- **住户变化**：配置里加了新成员 → 「刚搬进来」+ 大家去打招呼；成员下架 → 大家记一笔「搬走了」。
- 所有成员同一套规则；生活状态只在生活模式用，工作模式不 import 本包。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from roundtable.core.member.story import store
from roundtable.core.member.story.talent import TALENT_STORIES, TALENT_TAGS, Talents

STORY = "story"  # life_events 的 kind
MARK = "生活事件："
REST = "歇着"  # 生病那天空闲时段的去处（活动名照原样显示）
FREE_ACTS = frozenset({"exercise", "chat", "stargaze", "game", "read"})
WHEN = {0: "今天", 1: "昨天", 2: "前天"}


@dataclass
class Happening:
    """到点发生的一件事（写成 life_events 的一行）。"""

    at: datetime
    story: str
    step: int
    members: list[str]
    text: str
    place: str = "dorm"
    domain: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def topic(self) -> str:
        mark = f"{MARK}{self.story}/{self.step}" + (f"/{self.domain}" if self.domain else "")
        return f"地点：{store.place_label(self.place)}（{self.text}）\n{mark}"


def parse_mark(topic: str) -> tuple[str, int, str] | None:
    """场景里的「生活事件：id/步/领域」→ (id, 步, 领域)；没有时为 None。"""
    for line in topic.splitlines():
        if line.startswith(MARK):
            parts = line.removeprefix(MARK).split("/")
            try:
                return parts[0], int(parts[1]), parts[2] if len(parts) > 2 else ""
            except (IndexError, ValueError):
                return None
    return None


def story_meta(rt: Any, topic: str) -> dict[str, Any]:
    """成员引擎转发 story 行时带上的数据：事件 id、第几步、领域、交给 P3 的标签。"""
    mark = parse_mark(topic)
    if mark is None:
        return {}
    story, step, domain = mark
    tags: list[str] = []
    if story in TALENT_STORIES:
        tags = list(TALENT_TAGS.get((story, step), []))
    else:
        stories = rt.config.roundtable.chat.life.story.stories
        spec = next((s for s in stories if s.id == story), None)
        if spec is not None and 0 <= step < len(spec.steps):
            tags = list(spec.steps[step].tags)
    return {"story": story, "step": step, "domain": domain, "tags": tags}


class StoryEngine:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self.talents = Talents(rt)
        self._texts: dict[str, str] = {}

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.life.story

    def enabled(self) -> bool:
        return self.rules.enabled

    def reset(self) -> None:
        """档案可能改了：下次重新读（每次推进开头调一次）。"""
        self._texts.clear()

    def text(self, member: str) -> str:
        if member not in self._texts:
            self._texts[member] = store.profile_text(self.rt, member)
        return self._texts[member]

    # --- 空闲去哪 -----------------------------------------------------------------

    def resting(self, member: str, day: date) -> bool:
        st = store.load(self.rt, member)
        d = day.isoformat()
        return any(p.get("rest") and p.get("date") == d for p in st["pending"])

    def prefer(self, member: str, slot: datetime, place: str, act: str) -> tuple[str, str]:
        """作息表的空闲时段：按她的爱好和擅长挑去处；生病歇着那天在宿舍。其余照原样。"""
        if not self.enabled() or act not in FREE_ACTS:
            return place, act
        if self.resting(member, slot.date()):
            return "dorm", REST
        way = self.rules.way
        if not way.enabled or not way.options or slot.hour >= 22:
            return place, act
        text = self.text(member)
        st = store.load(self.rt, member)
        awake = self.talents.awakened(st)
        boosted = {
            a for d in awake if (spec := self.talents.rules.domains.get(d)) for a in spec.acts
        }
        keys = sorted(way.options)
        weights = []
        for key in keys:
            w = 1.0 + way.like_weight * store.hits(text, way.options[key]) / 3
            if key in boosted or key.split(":", 1)[-1] in boosted:
                w += way.talent_weight
            weights.append(w)
        rng = random.Random(f"way:{member}:{slot.isoformat()}")
        room, _, chosen = rng.choices(keys, weights)[0].partition(":")
        return room, chosen

    # --- 挑事 ---------------------------------------------------------------------

    def advance(
        self,
        start: datetime,
        end: datetime,
        names: dict[str, str],
        everyone: dict[str, str] | None = None,
    ) -> list[Happening]:
        """推进 (start, end]（带时区，本地日期决定哪天）：挑好这几天的事，返回到点发生的（按时间）。

        names：现在能参加生活的成员 id → 称呼；everyone：配置里的全部成员（看住户变化用）。"""
        if not self.enabled() or start >= end:
            return []
        self.reset()
        out = self._roster(everyone if everyone is not None else names, names, start, end)
        members = sorted(names)
        tz = end.tzinfo
        first, last = start.astimezone(tz).date(), end.astimezone(tz).date()
        days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
        states = {m: store.load(self.rt, m) for m in members}
        for day in days:
            practice = None
            for m in members:
                st = states[m]
                if day.isoformat() in st["planned"]:
                    continue
                if practice is None and self.talents.enabled():
                    practice = self.talents.practice(members, day, tz)
                self._plan_day(m, day, st, names, (practice or {}).get(m) or {}, start, end)
        for m in members:
            st = states[m]
            for p in st["pending"]:
                if p.get("done"):
                    continue
                at = datetime.fromisoformat(p["at"])
                if at <= start:
                    p["done"] = p["skipped"] = True  # 太久没开机，错过了：不补
                elif at <= end:
                    p["done"] = True
                    out.append(
                        Happening(
                            at,
                            p["story"],
                            p["step"],
                            list(p["members"]),
                            p["text"],
                            p.get("place") or "dorm",
                            p.get("domain") or "",
                            list(p.get("tags") or []),
                        )
                    )
            st.pop("_member", None)
            self._prune(st, last)
            store.save(self.rt, m, st)
        out.sort(key=lambda h: (h.at, h.story, h.members))
        return out

    def _plan_day(
        self,
        member: str,
        day: date,
        st: dict[str, Any],
        names: dict[str, str],
        practice: dict[str, Any],
        start: datetime,
        end: datetime,
    ) -> None:
        r = self.rules
        d = day.isoformat()
        st["planned"].append(d)
        st["_member"] = member  # 只在这次挑事里用（看生活阶段），存之前去掉
        rng = random.Random(f"story:{member}:{d}")
        used = sum(1 for p in st["pending"] if p.get("date") == d)
        others = [m for m in sorted(names) if m != member]
        # L10：擅长的阶段（觉醒、有人来请教、卡住 / 想通、生疏）
        for story, domain, why in self.talents.step(member, day, st, self.text(member), practice):
            if used >= r.max_per_day:
                break
            spec = self._talent_spec(story, domain, why)
            if spec.with_other and not others:
                continue
            self._start(member, spec, day, rng, st, names, others, start, end, domain=domain)
            used += 1
        # 事件库：按条件和概率挑
        awake = self.talents.awakened(st)
        hits: list[Any] = []
        for spec in r.stories:
            if spec.trigger != "daily" or not self._eligible(spec, day, st, others):
                continue
            p = spec.chance
            if spec.likes:
                if store.hits(self.text(member), spec.likes):
                    p *= spec.boost
                elif spec.need_likes:
                    continue
            if spec.domain and spec.domain in awake:
                p *= self.talents.rules.story_boost
            if rng.random() < min(p, 1.0):
                hits.append(spec)
        rng.shuffle(hits)
        for spec in hits[: max(r.max_per_day - used, 0)]:
            self._start(member, spec, day, rng, st, names, others, start, end, domain=spec.domain)

    def _eligible(self, spec: Any, day: date, st: dict[str, Any], others: list[str]) -> bool:
        if spec.months and day.month not in spec.months:
            return False
        if spec.weekdays and day.weekday() not in spec.weekdays:
            return False
        if spec.with_other and not others:
            return False
        last = st["last"].get(spec.id)
        if last and (day - date.fromisoformat(last)).days < spec.cooldown_days:
            return False
        if spec.group:
            glast = st["group_last"].get(spec.group)
            cool = self.rules.group_cooldown_days.get(spec.group, 0)
            if glast and (day - date.fromisoformat(glast)).days < cool:
                return False
            if self._in_phase(st.get("_member", ""), spec.group, day):
                return False
        d = day.isoformat()
        return not any(  # 同一件事 / 同一分组还在进行（感冒没好不会又过敏）
            not p.get("done")
            and p.get("date", "") >= d
            and (p.get("story") == spec.id or (spec.group and p.get("group") == spec.group))
            for p in st["pending"]
        )

    def _in_phase(self, member: str, group: str, day: date) -> bool:
        """她这天正在生活阶段（P3 旁的 phase）里的同类事上（如小感冒）：只读，不掷骰子。"""
        names = self.rules.phase_groups.get(group) or []
        if not names or not member:
            return False
        import json

        from roundtable.core.member.phase import PHASE_KEY

        raw = self.rt.repo.setting(PHASE_KEY)
        try:
            row = (json.loads(raw) if raw else {}).get(member)
        except (ValueError, AttributeError):
            return False
        if not isinstance(row, dict) or row.get("kind") not in names:
            return False
        d = day.isoformat()
        return str(row.get("start", ""))[:10] <= d <= str(row.get("end", ""))[:10]

    def _talent_spec(self, story: str, domain: str, why: str) -> Any:
        from roundtable.core.config.schema import StorySpec, StoryStep

        texts = self.talents.rules.texts
        cut = 78
        if story == "talent_awake":
            text = store.fill(texts.awake, domain=domain, evidence=why)
            steps = [StoryStep(text=text[:cut], tags=TALENT_TAGS[(story, 0)])]
        elif story == "talent_ask":
            return StorySpec(
                id=story,
                with_other=True,
                steps=[StoryStep(text=texts.ask.replace("{domain}", domain)[:cut], place="lounge")],
            )
        elif story == "talent_stuck":
            lo, hi = self.talents.rules.stuck_days
            steps = [
                StoryStep(
                    text=store.fill(texts.stuck, domain=domain)[:cut],
                    tags=TALENT_TAGS[(story, 0)],
                ),
                StoryStep(
                    day=[lo, hi],
                    text=store.fill(texts.unstuck, domain=domain)[:cut],
                    tags=TALENT_TAGS[(story, 1)],
                ),
            ]
        else:
            steps = [StoryStep(text=store.fill(texts.rusty, domain=domain)[:cut])]
        return StorySpec(id=story, steps=steps)

    def _start(
        self,
        member: str,
        spec: Any,
        day: date,
        rng: random.Random,
        st: dict[str, Any],
        names: dict[str, str],
        others: list[str],
        start: datetime,
        end: datetime,
        *,
        domain: str = "",
        fmt: dict[str, str] | None = None,
    ) -> None:
        """开始一件事：把每一步排好日子和钟点，放进日程。"""
        tz = end.tzinfo
        crew = [member]
        if spec.with_other and others:
            crew.append(rng.choice(others))
        words = {"name": names.get(member, member), "domain": domain} | (fmt or {})
        if len(crew) > 1:
            words["other"] = names.get(crew[1], crew[1])
        offset, prev = 0, None
        for i, step in enumerate(spec.steps):
            days = step.day if isinstance(step.day, list) else [step.day, step.day]
            offset = max(offset, rng.randint(days[0], days[1]))
            when = day + timedelta(days=offset)
            h0, h1 = spec.hours
            at = datetime.combine(
                when, time(rng.randrange(h0, h1), rng.randrange(60)), tzinfo=tz
            ).astimezone(tz)
            if prev is not None and at <= prev:
                at = prev + timedelta(minutes=30)
            if at <= start:  # 今天挑的事落在上次推进以前：挪进这次推进的时间里
                at = start + (end - start) * (0.1 + 0.8 * rng.random())
            prev = at
            st["pending"].append(
                {
                    "date": at.date().isoformat(),
                    "at": at.isoformat(),
                    "story": spec.id,
                    "group": spec.group,
                    "step": i,
                    "members": crew,
                    "text": store.fill(step.text, **words),
                    "place": step.place,
                    "rest": step.rest,
                    "domain": domain,
                    "tags": list(step.tags),
                    "done": False,
                }
            )
        st["last"][spec.id] = day.isoformat()
        if spec.group:
            st["group_last"][spec.group] = day.isoformat()

    def _prune(self, st: dict[str, Any], today: date) -> None:
        keep = (today - timedelta(days=self.rules.keep_days)).isoformat()
        st["pending"] = [p for p in st["pending"] if not p.get("done") or p["date"] >= keep]
        st["planned"] = [d for d in st["planned"] if d >= keep]

    # --- 住户变化 -----------------------------------------------------------------

    def _roster(
        self, everyone: dict[str, str], names: dict[str, str], start: datetime, end: datetime
    ) -> list[Happening]:
        """配置里的成员变了：新来的「刚搬进来」+ 大家打招呼；下架的大家记一笔「搬走了」。
        第一次只记下名单，不发事件。"""
        import json

        raw = self.rt.repo.setting(store.ROSTER_KEY)
        self.rt.repo.set_setting(store.ROSTER_KEY, json.dumps(everyone, ensure_ascii=False))
        if not raw:
            return []
        try:
            before = json.loads(raw)
        except ValueError:
            return []
        specs = {s.trigger: s for s in self.rules.stories if s.trigger != "daily"}
        out: list[Happening] = []
        at = end
        for new in sorted(set(everyone) - set(before)):
            stay = [m for m in sorted(names) if m != new and m in before]
            if (spec := specs.get("arrive")) and new in names:
                s = spec.steps[0]
                text = store.fill(s.text, name=everyone[new], new=everyone[new])
                out.append(Happening(at, spec.id, 0, [new], text, s.place, "", list(s.tags)))
            if (spec := specs.get("welcome")) and stay:
                s = spec.steps[0]
                text = store.fill(s.text, new=everyone[new])
                out.append(Happening(at, spec.id, 0, stay, text, s.place, "", list(s.tags)))
        for gone in sorted(set(before) - set(everyone)):
            stay = [m for m in sorted(names) if m in everyone and m in before]  # 认识 TA 的
            if (spec := specs.get("farewell")) and stay:
                s = spec.steps[0]
                text = store.fill(s.text, gone=str(before[gone]))
                out.append(Happening(at, spec.id, 0, stay, text, s.place, "", list(s.tags)))
        return out

    # --- 读 -----------------------------------------------------------------------

    def recent(self, members: list[str], at: datetime, names: dict[str, str]) -> str:
        """场景里「最近各自的事」那一行：在场的人最近几天发生过的事（背景，不是台词）。"""
        r = self.rules
        if not self.enabled() or not r.scene_items or not r.scene_days:
            return ""
        tz = at.tzinfo
        today = at.astimezone(tz).date()
        seen: set[tuple[str, str]] = set()
        found: list[tuple[datetime, str]] = []
        for m in members:
            for p in store.load(self.rt, m)["pending"]:
                if not p.get("done") or p.get("skipped"):
                    continue
                when = datetime.fromisoformat(p["at"])
                ago = (today - when.astimezone(tz).date()).days
                if when > at or ago > r.scene_days:
                    continue
                key = (p["at"], p["text"])
                if key in seen:
                    continue
                seen.add(key)
                who = "" if len(p["members"]) > 1 else names.get(m, m)
                found.append((when, f"{who}{WHEN.get(ago, f'{ago}天前')}{p['text']}"))
            st = store.load(self.rt, m)
            for line in self.talents.known(st, names.get(m, m)):
                found.append((at - timedelta(days=30), line))
        found.sort(key=lambda x: x[0], reverse=True)
        line = "；".join(t for _, t in found[: r.scene_items])
        if not line:
            return ""
        return f"最近各自的事（背景，自然地带到就行）：{line}"[: r.scene_chars + 20]

    def state(self, member: str, names: dict[str, str] | None = None) -> dict[str, Any]:
        """一位成员的生活状态（给接口和之后的生活页看）：进行中的事、最近的事、擅长（没有分数）。"""
        st = store.load(self.rt, member)
        name = (names or {}).get(member, member)
        pending = [p for p in st["pending"] if not p.get("done")]
        done = [p for p in st["pending"] if p.get("done") and not p.get("skipped")]
        return {
            "member": member,
            "upcoming": [{"date": p["date"], "story": p["story"]} for p in pending],
            "recent": [{"at": p["at"], "text": p["text"]} for p in done[-10:]],
            "talents": [
                {"domain": d, "stage": v["stage"], "evidence": v.get("evidence", "")}
                for d, v in sorted((st.get("talents") or {}).items())
            ],
            "known": self.talents.known(st, name),
        }

    def clear(self, member: str) -> None:
        store.clear(self.rt, member)
