"""生活引擎：成员在用户不在时按作息过日子、彼此聊天（docs/CHAT.md G4）。

- 事件由代码生成，不调用模型：公寓楼作息（``office.apartment.schedule``）决定谁在哪；
  同一时间在同一个公共房间的几位「碰到一起」，傍晚偶尔有两位私下聊天，其余是一个人的日常。
  办公室里真实办过的活（匿名未揭晓的会话除外）写进场景，成为话题。随机数由时刻派生，可复现。
- 只有少数事件交给模型写成聊天（每次推进最多 ``max_written_per_tick`` 件，开机补算最多
  ``max_written_catch_up`` 件），其余只记一行，整理记忆时用。
- 花费：每句话前检查生活每日上限（``life.daily_usd``，含记忆整理）、聊天月预算和总预算，
  不够就停。调用记在对应会话的隐藏聊天会话上（step = life）。
- 开机补算：记下上次推进的时刻，启动后补上中间的时间（最多往回 ``catch_up_hours`` 小时），
  补出来的消息标「你不在时」（meta.offline），时间按事件发生的时刻。
- 成员不主动找用户：生活只发生在生活群和成员之间的私聊里，不进用户的私聊，不推送。
- 收得住（``core/life_guard.py``）：没新内容就收尾、成员之间每天的条数上限、连续出错或预算告急
  当天停掉、每周自检。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.browse import SEARCH_STEP, SHARE_STEP, Browser
from roundtable.core.budget.guard import day_window as utc_day_window
from roundtable.core.chat import ROOM, ChatHub, ChatMember, call_member, clean_reply, member_memory
from roundtable.core.chat_work import short_title
from roundtable.core.life_guard import LifeGuard
from roundtable.core.member.brain import Brain, intent_text
from roundtable.core.member.relation import RelationPlugin
from roundtable.core.member.voice import Said, before_voice, recent_words, voice_for
from roundtable.core.memory import MemoryKeeper
from roundtable.core.moments import MOMENT_STEP, NOTHING, Moments
from roundtable.core.office.apartment import ACTIVITIES, PUBLIC_ROOMS, schedule
from roundtable.core.persona import chat_persona
from roundtable.core.plain_talk import before_speaking, plain_prompt
from roundtable.core.storage.repository import iso

LAST_TICK_KEY = "life.last_tick"
PAIR_PREFIX = "pair:"
GATHER = frozenset({"eat", "exercise", "chat", "game", "stargaze"})
WEEKDAYS = "一二三四五六日"
LIFE_STEPS = ("life", "memory", SHARE_STEP, SEARCH_STEP, MOMENT_STEP, "wander", "growth")


def pair_room(a: str, b: str) -> str:
    return PAIR_PREFIX + "+".join(sorted((a, b)))


@dataclass
class Plan:
    at: datetime
    kind: str  # gather / pair / solo
    place: str
    members: list[str]
    activity: str
    topic: str = ""
    room: str | None = None
    score: float = 0.0
    work: list[str] = field(default_factory=list)


class LifeEngine:
    def __init__(self, rt: Any, hub: ChatHub, memory: MemoryKeeper) -> None:
        self.rt = rt
        self.hub = hub
        self.memory = memory
        self.guard = LifeGuard(rt, LIFE_STEPS)
        memory.allow = self.allowed  # 记忆整理也算进生活的每日上限
        self.browse = Browser(rt, hub, self.allowed)
        self.moments = Moments(rt, hub, self.allowed)
        self._planned: Brain | None = None

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.life

    # --- 预算 --------------------------------------------------------------------

    def spent_today(self) -> float:
        start, end = utc_day_window(self.rt.budget.now())
        return self.rt.repo.spent_in_steps("chat", LIFE_STEPS, start, end)

    def allowed(self, estimate_usd: float) -> bool:
        if self.guard.halted():
            return False
        return self.spent_today() + estimate_usd <= self.rules.daily_usd + 1e-9

    # --- 事件 --------------------------------------------------------------------

    def _work(self, start: datetime, end: datetime) -> dict[str, list[str]]:
        """成员（厂商）→ 这段时间里办过的活（题目）。匿名未揭晓的会话不算。"""
        vendor = {m.id: m.vendor for m in self.rt.config.models.models}
        out: dict[str, list[str]] = {}
        for row in self.rt.repo.office_work_between(start, end):
            if row["anonymous"] and not row["revealed_at"]:
                continue
            v = vendor.get(row["model_id"])
            title = f"「{short_title(row['question'])}」"
            if v and title not in out.setdefault(v, []):
                out[v].append(title)
        return out

    def plan(self, start: datetime, end: datetime) -> list[Plan]:
        """(start, end] 内每个整点的事件。start / end 带时区（本地时间决定作息）。"""
        members = [m for m in self.hub.everyone() if m.available]
        if not members:
            return []
        names = {m.id: m.name for m in members}
        relation = RelationPlugin(self.rt, names=lambda: names)
        weights = self._weights(relation, list(names), end)
        brain = Brain(self.rt, buffer={})  # 排日程只写内存；tick 里真发生了才落库
        self._planned = brain
        slot = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        plans: list[Plan] = []
        while slot <= end:
            rng = random.Random(f"life:{slot.isoformat()}")
            day_start = slot.replace(hour=0)
            work = self._work(day_start, slot)
            where: dict[str, list[str]] = {}
            acts: dict[str, str] = {}
            for m in members:
                place, act = schedule(m.id, slot.hour, slot.weekday())
                # 大脑分区：空闲时段按驱力和在做的事自己挑去哪、做什么（不是空闲时段照作息表）
                place, act = brain.choose_activity(m.id, slot, place, act)
                acts[m.id] = act
                if act == "sleep":
                    continue
                key = f"dorm:{m.id}" if place == "dorm" else place
                where.setdefault(key, []).append(m.id)
            for place, ids in sorted(where.items()):
                act = acts[ids[0]]
                at = slot + timedelta(minutes=rng.randrange(5, 50))
                if len(ids) >= 2 and all(acts[i] == act for i in ids) and act in GATHER:
                    rng.shuffle(ids)
                    group = self._closest(ids, 3, weights, rng)
                    done = [f"{names[i]} 今天办了{'、'.join(work[i])}" for i in group if i in work]
                    plans.append(
                        Plan(
                            at,
                            "gather",
                            place,
                            group,
                            act,
                            room=ROOM,
                            work=done,
                            score=1.0 + len(done) + rng.random(),
                        )
                    )
                else:
                    for i in ids:
                        plans.append(Plan(at, "solo", place, [i], acts[i]))
            awake = [m.id for m in members if acts[m.id] != "sleep"]
            if 18 <= slot.hour < 23 and len(awake) >= 2 and rng.random() < self.rules.pair_chance:
                a, b = self._pair(awake, weights, rng)
                done = [f"{names[i]} 今天办了{'、'.join(work[i])}" for i in (a, b) if i in work]
                plans.append(
                    Plan(
                        slot + timedelta(minutes=rng.randrange(5, 55)),
                        "pair",
                        "",
                        [a, b],
                        "chat",
                        room=pair_room(a, b),
                        work=done,
                        score=1.5 + len(done) + rng.random(),
                    )
                )
            slot += timedelta(hours=1)
        for p in plans:
            p.topic = self.scene(p, names)
            if weights and p.kind != "solo":
                bond = relation.scene_line(p.members, end)
                if bond:
                    p.topic += f"\n{bond}"
        return plans

    # --- 关系（P4）：谁和谁更常聊 ------------------------------------------------------

    @staticmethod
    def _weights(
        relation: RelationPlugin, ids: list[str], at: datetime
    ) -> dict[tuple[str, str], float]:
        """两两之间挑人的权重（1 + 偏向 × 亲近度）；谁和谁都没来往过时为空（照旧均匀随机）。"""
        out = {(a, b): relation.weight(a, b, at) for a in ids for b in ids if a != b}
        return out if any(w > 1 for w in out.values()) else {}

    @staticmethod
    def _closest(
        ids: list[str], k: int, weights: dict[tuple[str, str], float], rng: random.Random
    ) -> list[str]:
        """从打乱后的 ids 里挑 k 位：第一位随机，其余按和已选的人的亲近度加权抽（不放回）。"""
        if len(ids) <= k or not weights:
            return ids[:k]
        group, pool = ids[:1], ids[1:]
        while len(group) < k:
            w = [sum(weights[(g, c)] for g in group) / len(group) for c in pool]
            pick = rng.choices(pool, w)[0]
            group.append(pick)
            pool.remove(pick)
        return group

    @staticmethod
    def _pair(
        awake: list[str], weights: dict[tuple[str, str], float], rng: random.Random
    ) -> tuple[str, str]:
        if not weights:
            a, b = rng.sample(awake, 2)
            return a, b
        a = rng.choice(awake)
        others = [x for x in awake if x != a]
        return a, rng.choices(others, [weights[(a, x)] for x in others])[0]

    def scene(self, p: Plan, names: dict[str, str]) -> str:
        place = "私聊" if p.kind == "pair" else self.place_name(p.place)
        lines = [
            f"时间：星期{WEEKDAYS[p.at.weekday()]} {p.at.hour} 点 {p.at.minute} 分",
            f"地点：{place}（{ACTIVITIES.get(p.activity, p.activity)}）",
            f"在场：{'、'.join(names.get(i, i) for i in p.members)}",
            f"今天的事：{'；'.join(p.work) if p.work else NOTHING}",
        ]
        return "\n".join(lines)

    @staticmethod
    def place_name(place: str) -> str:
        if place.startswith("dorm"):
            return "自己的宿舍"
        return f"公寓楼{PUBLIC_ROOMS.get(place, place)}"

    # --- 推进 --------------------------------------------------------------------

    async def tick(self, now: datetime) -> dict[str, int]:
        """推进到 now（带时区）。返回各项数量（事件、聊天、消息、记忆、分享、朋友圈、自检）。"""
        out = {"events": 0, "written": 0, "messages": 0, "memories": 0, "shares": 0, "moments": 0}
        out["reports"] = 0
        if not self.rules.enabled:
            return out
        repo = self.rt.repo
        last_text = repo.setting(LAST_TICK_KEY)
        tick = timedelta(minutes=self.rules.tick_minutes)
        if last_text:
            last = datetime.fromisoformat(last_text).astimezone(now.tzinfo)
            start = max(last, now - timedelta(hours=self.rules.catch_up_hours))
        else:
            start = now - tick  # 第一次：不往回补
        if start >= now:
            return out
        offline = now - start > tick * 2
        plans = self.plan(start, now)
        if self._planned is not None:
            self._planned.flush()  # 这段时间里她们自己挑的去处、练的事算数了
        limit = self.rules.max_written_catch_up if offline else self.rules.max_written_per_tick
        if self.guard.check(self.spent_today()):
            limit = 0  # 熔断：当天只记一行
        elif self.guard.room_left() == 0:
            self.guard.note_cap()
            limit = 0
        talk = sorted((p for p in plans if p.kind != "solo"), key=lambda p: -p.score)[:limit]
        chosen = {id(p) for p in talk}
        for p in sorted(plans, key=lambda p: p.at):
            written = 0
            if id(p) in chosen and not self.guard.check(self.spent_today()):
                written = await self.write(p, offline=offline, now=now)
            repo.add_life_event(
                iso(p.at),
                p.kind,
                p.members,
                place=p.place,
                topic=p.topic,
                room=p.room if written else None,
                written=bool(written),
                offline=offline,
            )
            out["events"] += 1
            out["written"] += int(bool(written))
            out["messages"] += written
        repo.set_setting(LAST_TICK_KEY, now.isoformat())
        out["memories"] = await self.memory.catch_up(now, offline=offline)
        if self.guard.room_left() > 0:
            out["shares"] = await self.browse.tick(now)
        m = await self.moments.tick(now, offline=offline)
        out["moments"] = m["posts"] + m["comments"]
        names = {m.id: m.name for m in self.hub.everyone()}
        out["reports"] = int(self.guard.weekly(self.rt.budget.now(), names) is not None)
        return out

    async def write(self, p: Plan, *, offline: bool, now: datetime) -> int:
        """把一件事写成几句聊天。返回写出的句数（预算不够或调用失败时停在中途）。"""
        rt, rules = self.rt, self.rules
        everyone = {m.id: m for m in self.hub.everyone()}
        speakers = [everyone[i] for i in p.members if i in everyone]
        if len(speakers) < 2:
            return 0
        if p.kind == "pair":
            a, b = speakers[:2]
            rt.repo.ensure_chat_room(
                p.room,
                kind="pair",
                title=f"{a.name} 和 {b.name}",
                members=[a.id, b.id],
                with_user=False,
            )
        chat = self.hub.room(p.room or ROOM)
        names = {m.id: m.name for m in everyone.values()}
        params = dict(rt.config.roundtable.step_params.get("chat", {}))
        params["max_tokens"] = rules.max_tokens
        said = 0
        earlier = self.guard.recent_lines(chat.room)
        spoken: list[tuple[str, str]] = []  # 这一段里谁说了什么（自查比对用）
        brain = Brain(self.rt, names=names)
        last_by: str | None = None
        for turn in range(rules.turns):
            # 补算：按事件发生的时刻，且不晚于现在（整点那件事的分钟数可能落到现在之后）
            at = min(p.at, now - timedelta(minutes=rules.turns)) + timedelta(minutes=turn)
            at = at if offline else now
            pick = self.next_speaker(brain, speakers, spoken, last_by, at, f"{p.room}:{turn}")
            if pick is None:
                break  # 大家都没什么想说的：这段自然收尾
            member, card = pick
            if not self.allowed(rules.estimate_usd):
                break
            if self.guard.room_left() == 0:
                self.guard.note_cap()
                break
            others = "、".join(s.name for s in speakers if s.id != member.id)
            prompt = rt.prompts.render(
                "life_chat",
                rt.config.roundtable.prompts["life_chat"],
                name=member.name,
                others=others,
                scene=p.topic,
                persona=chat_persona(rt, member.id),
                voice=voice_for(rt, member.id, "life", seed=turn + len(earlier)),
                memory=member_memory(
                    rt, member.id, f"{p.topic}\n{chat.history(names)}", p.room or ROOM
                ),
                history=chat.history(names),
                **self.intent_vars(card),
            )
            prompt = plain_prompt(rt.prompts, rt.config, prompt)
            completion = await call_member(
                rt,
                room=chat.room,
                model_id=member.model_id,
                prompt=prompt,
                step="life",
                params=params,
                estimate_usd=rules.estimate_usd,
            )
            if completion is None:
                break
            text = clean_reply(completion.text, member.name, rt.config.roundtable.chat.max_chars)
            text = self.guard.clip(text)
            if not text:
                break
            if self.guard.is_stale(text, earlier):
                self.guard.note_stale(member.id, text)  # 没有新内容：这句不发，这段收尾
                break
            text = await before_voice(
                rt,
                text,
                Said(
                    own=tuple(t for who, t in spoken if who == member.id),
                    others=tuple((names.get(w, w), t) for w, t in spoken if w != member.id),
                    long=recent_words(rt, member.id, now),
                ),
                member=member.id,
                room=chat.room,
                room_kind="life",
                named=False,
                name=member.name,
                model_id=member.model_id,
            )
            if not text:
                self.guard.note_stale(member.id, "（自查：跟风或重复，不发）")
                break  # 说出口前自查：跟风、附和接龙、反复提同一件事——这句不发，这段收尾
            text = await before_speaking(
                rt, text, room=chat.room, name=member.name, model_id=member.model_id
            )
            spoken.append((member.id, text))
            earlier.append(text)
            brain.after(card, at)
            last_by = member.id
            when = at
            rt.repo.add_chat_message(
                chat.room,
                member.id,
                text,
                model_id=member.model_id,
                cost_usd=completion.cost_usd or 0.0,
                meta={"life": True, "offline": offline},
                created_at=iso(when),
            )
            said += 1
        return said

    # --- 大脑分区：谁接话、带上此刻的想法 -------------------------------------------------

    def intent_vars(self, card: Any) -> dict[str, str]:
        """life_chat v4 起带 <intent>；旧版本提示词没有这个变量就不传。"""
        version = self.rt.config.roundtable.prompts["life_chat"]
        if "intent" not in self.rt.prompts.get("life_chat", version).variables:
            return {}
        return {"intent": intent_text(card, "（没有特别的念头：照平时的你接话）")}

    @staticmethod
    def next_speaker(
        brain: Brain,
        speakers: list[ChatMember],
        spoken: list[tuple[str, str]],
        last_by: str | None,
        at: datetime,
        seed: str,
    ) -> tuple[ChatMember, Any] | None:
        """下一句谁说：除了刚说完的人，每位算一张意图卡，不接的跳过，按显著性 + 想说的劲儿
        挑最想说的；没人想说就返回 None。大脑分区没开时照旧轮流。"""
        others = [m for m in speakers if m.id != last_by] or speakers
        if not brain.enabled():
            return others[len(spoken) % len(others)], None
        last = spoken[-1][1] if spoken else ""
        best: tuple[float, ChatMember, Any] | None = None
        for m in others:
            card = brain.intent(
                m.id,
                last=last,
                speaker=last_by,
                named=not spoken or len(speakers) == 2,
                group=len(speakers) > 2,
                at=at,
                seed=seed,
                present={s.id for s in speakers},
            )
            if card is None:
                return m, None
            if card.silent:
                continue
            drives = brain.snapshot(m.id, at)["drives"]
            score = card.salience + drives.get("social", 0.3) + drives.get("express", 0.3)
            if best is None or score > best[0]:
                best = (score, m, card)
        return (best[1], best[2]) if best else None
