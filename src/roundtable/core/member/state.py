"""状态（P3：心情、精力、本能反射，docs/MEMBER_ENGINE.md「P3 状态」）。

- **全由程序算**：心情（-1～1）和精力（0～1）只按事件总线上的事件和规则表改，模型从不写自己的情绪。
- **快慢两层、会回到基线**：每次冲击一部分落到快层（几小时内消退），一部分落到慢层（一两天的底色）；
  两层各自按半衰期指数衰减回 0。心情 = 基线 + 慢层 + 快层；精力 = 作息曲线 + 慢层（疲劳）+ 快层。
  衰减是闭式的：读的时候按经过的时间算，不需要定时跑；事件先后乱序也算得一样（冲击按发生时刻折算）。
- **基线读自我档案**（人物卡第 5 章「情绪」、第 8 章「日常」、五项性格分，只读、不改档案）：
  心情基线来自外向 / 幽默 / 温和和情绪章里的少量关键词，情绪敏感度来自较真 / 温和和关键词，
  作息（几点起、几点睡、是不是夜猫子）从日常章里的时间读出；读不到就用公寓楼的作息。
- **本能反射**：事件 → 刺激（标签）→ ``Instinct.react`` → 反射（见 ``instinct.py``，现在是规则表）。
  每次反射发一个 ``state.reflex`` 事件（给之后的「决定与表达」和经历数据用）。
- **只有这个插件写状态**（``app_settings`` 的 ``member_state:<成员>`` 键，不加迁移）；
  别的模块只读：``mood_of`` / ``energy_of`` / ``line``。
- 只用于聊天、朋友圈、生活：状态那一行经 ``chat_persona`` 进私聊、群聊、朋友圈、生活的提示词
  （工作群、讨论、协同、办公室、个人作答都不带）。8 位成员规则、参数、字数上限完全相同。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, tzinfo
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.member.bus import PERCEIVE, Event, EventBus
from roundtable.core.member.instinct import Instinct, Reflex, RuleInstinct, Stimulus
from roundtable.core.member.mode import Mode, require_life
from roundtable.core.member.profile import ProfileStore
from roundtable.core.office.apartment import schedule
from roundtable.core.storage.repository import iso

log = logging.getLogger(__name__)

SOURCE = "state"  # 上下文组装器里的来源名
REFLEX = "state.reflex"
WATCH = ("chat.message", "life.event", "work.done", f"{PERCEIVE}*")
DM_PREFIX = "dm:"
EVERYONE = "全员"


def decay(hours: float, half_life: float) -> float:
    return 0.5 ** (max(hours, 0.0) / half_life)


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# --- 基线：读自我档案 --------------------------------------------------------------


@dataclass(frozen=True)
class Baseline:
    mood: float = 0.0  # -0.4～0.4
    sensitivity: float = 1.0  # 心情冲击乘以它，0.5～1.6
    wake: float = 7.0  # 几点起（小时，可带小数）
    sleep: float = 23.0  # 几点睡（可以大于 24，表示过了半夜）
    night_owl: bool = False


TIME = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})")
# 「晚一点」「早一点」「一点点」不是钟点
CN_TIME = re.compile(r"(?<![晚早快慢多少有差])([一二两三四五六七八九十]{1,3})\s*点(?!点)\s*(半)?")
MIDNIGHT = ("半夜", "午夜", "凌晨零点")
ONE_OCLOCK = ("凌晨", "夜里", "晚上", "半夜", "到", "于")
CN_DIGITS = {c: i for i, c in enumerate("零一二三四五六七八九")} | {"两": 2}
CLAUSE = re.compile(r"[。；;\n]")


def _cn_number(s: str) -> int | None:
    try:
        if "十" not in s:
            return CN_DIGITS[s] if len(s) == 1 else None
        tens, _, ones = s.partition("十")
        return (CN_DIGITS[tens] if tens else 1) * 10 + (CN_DIGITS[ones] if ones else 0)
    except KeyError:
        return None


def times_in(text: str) -> list[float]:
    """一句话里出现的钟点（6:30 → 6.5，十一点半 → 11.5，半夜 → 24）。"""
    out = [int(h) + int(m) / 60 for h, m in TIME.findall(text) if int(h) < 24 and int(m) < 60]
    for hit in CN_TIME.finditer(text):
        num, half = hit.group(1), hit.group(2)
        if num == "一" and not text[: hit.start()].endswith(ONE_OCLOCK):
            continue  # 单说「一点」多半是「一些」
        n = _cn_number(num)
        if n is not None and n <= 24:
            out.append(n + (0.5 if half else 0))
    if any(w in text for w in MIDNIGHT):
        out.append(24.0)
    return out


def _house_hours(member: str) -> tuple[float, float]:
    """公寓楼的作息（读不到人物卡时用）：(几点起, 几点睡)。"""
    asleep = [h for h in range(24) if schedule(member, h, 0)[1] == "sleep"]
    wake = next(h for h in range(4, 24) if h not in asleep) if asleep else 7
    sleep = next((h for h in range(wake, wake + 24) if h % 24 in asleep), 23)
    return float(wake), float(sleep)


def sleep_hours(daily: str, rules: Any, member: str) -> tuple[float, float, bool]:
    """从日常章读 (几点起, 几点睡, 是不是夜猫子)。"""
    wake, sleep = _house_hours(member)
    wakes: list[float] = []
    sleeps: list[float] = []
    for clause in CLAUSE.split(daily):
        if any(w in clause for w in rules.wake_words):
            wakes += [t for t in times_in(clause) if 4 <= t <= 12]
        if any(w in clause for w in rules.sleep_words):
            for t in times_in(clause):
                # 「十一点睡」说的是晚上：5～12 点加 12，过了半夜的加 24
                t = t + 12 if 5 <= t <= 12 else (t + 24 if t <= 4 else t)
                if 20 <= t <= 28:
                    sleeps.append(t)
    if wakes:
        wake = min(wakes)
    if sleeps:
        sleep = max(sleeps)
    if sleep - wake < 12:  # 读出来的不合理（比如把午睡当成睡觉）：用公寓楼的
        wake, sleep = _house_hours(member)
    owl = any(w in daily for w in rules.night_words)
    if owl and not sleeps:  # 只写了「深夜才睡」没写钟点：按凌晨一点算
        sleep = max(sleep, 25.0)
    return wake, sleep, owl


def _hits(text: str, words: list[str]) -> int:
    return sum(1 for w in words if w in text)


def derive_baseline(profile: dict[str, Any], member: str, rules: Any) -> Baseline:
    """从自我档案算基线（没有档案时：心情 0、敏感度 1、公寓楼作息）。"""
    personality = profile.get("personality") or {}
    scores = personality.get("五项") or {}

    def s(trait: str) -> float:
        try:
            return float(scores.get(trait, 3)) - 3
        except (TypeError, ValueError):
            return 0.0

    feel = str(personality.get("情绪") or "")
    bright, dark = _hits(feel, rules.bright_words), _hits(feel, rules.dark_words)
    calm = _hits(feel, rules.calm_words)
    mood = 0.05 * (s("外向") + s("幽默") + s("温和")) + 0.05 * (min(bright, 2) - min(dark, 2))
    sensitivity = 1 + 0.15 * s("较真") - 0.1 * s("温和") + 0.1 * min(dark, 2) - 0.1 * min(calm, 2)
    daily = str((profile.get("state") or {}).get("日常") or "")
    wake, sleep, owl = sleep_hours(daily, rules, member)
    return Baseline(
        round(_clip(mood, -0.4, 0.4), 3), round(_clip(sensitivity, 0.5, 1.6), 3), wake, sleep, owl
    )


def awake_for(hour: float, b: Baseline) -> float | None:
    """醒来几小时了；该睡觉的时候为 None。"""
    since = (hour - b.wake) % 24
    return since if since < b.sleep - b.wake else None


def circadian(hour: float, b: Baseline, c: Any) -> float:
    """作息曲线：这个钟点（本地时间，可带小数）精力的基线。"""
    since = awake_for(hour, b)
    if since is None:
        return c.asleep
    ramp = c.ramp_hours * (2 if b.night_owl else 1)
    wind = c.wind_down_hours * (0.5 if b.night_owl else 1)
    v = c.waking + (c.peak - c.waking) * min(since / ramp, 1)
    left = (b.sleep - b.wake) - since
    if left < wind:
        v = min(v, c.bedtime + (c.peak - c.bedtime) * left / wind)
    if c.dip_hours[0] <= int(hour) % 24 < c.dip_hours[1]:
        v -= c.dip
    return _clip(v, 0, 1)


# --- 快照：快慢两层 -----------------------------------------------------------------


@dataclass
class Snapshot:
    at: str  # iso
    mood_fast: float = 0.0
    mood_slow: float = 0.0
    energy_fast: float = 0.0
    energy_slow: float = 0.0
    recent: list[dict[str, Any]] = field(default_factory=list)  # 最近的反射（新的在后）

    def decayed(self, to: datetime, mood: Any, energy: Any) -> Snapshot:
        """把四层衰减到 to（to 早于快照时间时不变）。"""
        hours = (to - datetime.fromisoformat(self.at)).total_seconds() / 3600
        if hours <= 0:
            return self
        return Snapshot(
            iso(to),
            self.mood_fast * decay(hours, mood.fast_half_life_hours),
            self.mood_slow * decay(hours, mood.slow_half_life_hours),
            self.energy_fast * decay(hours, energy.fast_half_life_hours),
            self.energy_slow * decay(hours, energy.slow_half_life_hours),
            list(self.recent),
        )


@dataclass(frozen=True)
class Reading:
    """某一刻的状态（给别的模块和网页看）。"""

    member: str
    at: datetime
    mood: float
    energy: float
    baseline: Baseline
    layers: dict[str, float]
    recent: list[dict[str, Any]]


class StatePlugin:
    """P3 状态。订阅总线（传了 bus 时），只写自己的状态键。"""

    def __init__(
        self,
        rt: Any,
        bus: EventBus | None = None,
        names: Callable[[], dict[str, str]] | None = None,
        instinct: Instinct | None = None,
        tz: tzinfo | None = None,
    ) -> None:
        self.rt = rt
        self.bus = bus
        self.names = names or (lambda: {})  # 成员 id → 称呼（认 @ 用）
        self.instinct: Instinct = instinct or RuleInstinct(lambda: self.rules.reflexes)
        self.tz = tz  # 作息按这个时区的钟点算；None 为主时区（终端时钟里选的，core/hometz.py）
        self.profiles = ProfileStore(rt)
        if bus is not None:
            for kind in WATCH:
                bus.subscribe(kind, self.on_event)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.state

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return engine.enabled and engine.state.enabled

    # --- 读 ---------------------------------------------------------------------

    def baseline(self, member: str) -> Baseline:
        return derive_baseline(self.profiles.current(member).data, member, self.rules.baseline)

    def hour(self, at: datetime) -> float:
        local = at.astimezone(self.tz if self.tz is not None else home_zone(self.rt).tz())
        return local.hour + local.minute / 60

    def _snapshot(self, member: str, at: datetime) -> Snapshot:
        row = self.rt.repo.member_state(member)
        if row is None:
            return Snapshot(iso(at))
        return Snapshot(**row["data"])

    def read(self, member: str, at: datetime | None = None) -> Reading:
        at = at or self.rt.budget.now()
        r, b = self.rules, self.baseline(member)
        snap = self._snapshot(member, at).decayed(at, r.mood, r.energy)
        mood = _clip(b.mood + snap.mood_slow + snap.mood_fast, -1, 1)
        energy = circadian(self.hour(at), b, r.circadian) + snap.energy_slow + snap.energy_fast
        layers = {k: round(v, 4) for k, v in asdict(snap).items() if k not in ("at", "recent")}
        return Reading(member, at, mood, _clip(energy, 0, 1), b, layers, snap.recent)

    def mood_of(self, member: str, at: datetime | None = None) -> float:
        """此刻的心情，-1～1，0 为中性（给记忆等模块读）。"""
        return self.read(member, at).mood

    def energy_of(self, member: str, at: datetime | None = None) -> float:
        """此刻的精力，0～1。"""
        return self.read(member, at).energy

    def line(self, member: str, at: datetime | None = None, mode: Mode | str = Mode.LIFE) -> str:
        """写进提示词的状态那一行（只在生活模式用；字数 ≤ context_chars，8 位相同）。"""
        require_life(mode)
        r = self.rules
        if not self.enabled() or r.context_chars == 0 or not r.mood_labels:
            return ""
        reading = self.read(member, at)
        recent = ""
        for item in reversed(reading.recent):
            hours = (reading.at - datetime.fromisoformat(item["at"])).total_seconds() / 3600
            if item.get("note") and 0 <= hours <= r.recent_hours:
                recent = r.recent_format.format(note=item["note"])
                break
        text = r.line_format.format(
            mood=_label(reading.mood, r.mood_labels),
            energy=_label(reading.energy, r.energy_labels),
            recent=recent,
        )
        return text[: r.context_chars]

    # --- 写（只有这里写状态）-------------------------------------------

    def on_event(self, event: Event) -> None:
        if not self.enabled():
            return
        for stimulus in self.stimuli(event):
            self.feel(stimulus)

    def feel(self, s: Stimulus) -> list[Reflex]:
        """一个刺激：本能反射 → 改快慢两层 → 存下 → 发 state.reflex 事件。"""
        reading = self.read(s.member, s.at)
        b = reading.baseline
        night = awake_for(self.hour(s.at), b) is None
        s = Stimulus(s.member, s.at, s.tags, night, reading.energy, reading.mood, s.data)
        reflexes = self.instinct.react(s)
        if not reflexes:
            return []
        r = self.rules
        row = self.rt.repo.member_state(s.member)
        snap = Snapshot(**row["data"]) if row else Snapshot(iso(s.at))
        # 冲击发生在快照之前（补算时事件乱序）：按经过的时间折算后加上，结果和按顺序算一样
        late = max((datetime.fromisoformat(snap.at) - s.at).total_seconds() / 3600, 0)
        snap = snap.decayed(s.at, r.mood, r.energy)
        for x in reflexes:
            self._push(snap, "mood", x.mood * b.sensitivity, r.mood, late)
            self._push(snap, "energy", x.energy, r.energy, late)
            snap.recent.append({"at": iso(s.at), "name": x.name, "act": x.act, "note": x.note})
        snap.recent = snap.recent[-r.recent_keep :] if r.recent_keep else []
        self.rt.repo.save_member_state(s.member, asdict(snap))
        if self.bus is not None:
            for x in reflexes:
                data = {"name": x.name, "act": x.act, "tags": sorted(s.tags)}
                self.bus.publish(Event(REFLEX, s.at, s.member, data))
        return reflexes

    @staticmethod
    def _push(snap: Snapshot, dim: str, delta: float, layer: Any, late: float) -> None:
        if not delta:
            return
        fast = delta * (1 - layer.slow_share) * decay(late, layer.fast_half_life_hours)
        slow = delta * layer.slow_share * decay(late, layer.slow_half_life_hours)
        fast += getattr(snap, f"{dim}_fast")
        slow += getattr(snap, f"{dim}_slow")
        setattr(snap, f"{dim}_fast", _clip(fast, -layer.fast_cap, layer.fast_cap))
        setattr(snap, f"{dim}_slow", _clip(slow, -layer.slow_cap, layer.slow_cap))

    def clear(self, member: str | None = None) -> int:
        """清空状态（回到基线）。用户的权利。"""
        return self.rt.repo.clear_member_state(member)

    # --- 事件 → 刺激 ------------------------------------------------------------

    def stimuli(self, event: Event) -> list[Stimulus]:
        tags_of: dict[str, set[str]] = {}

        def add(member: str | None, *tags: str) -> None:
            if member:
                tags_of.setdefault(member, set()).update(tags)

        kind, data = event.kind, event.data
        if kind == "chat.message":
            self._chat(event, add)
        elif kind == "life.event":
            add(event.member, "life", f"life.{data.get('kind', '')}")
        elif kind == "work.done":
            add(event.member, "work")
        elif kind.startswith(PERCEIVE):
            add(event.member, "perceive", kind)
        return [
            Stimulus(m, event.at, frozenset(t), data=dict(data)) for m, t in sorted(tags_of.items())
        ]

    def _chat(self, event: Event, add: Callable[..., None]) -> None:
        from roundtable.core.chat import EVERYONE_WORDS  # chat 间接依赖本模块（人设）

        data, author = event.data, event.member
        msg = self.rt.repo.chat_message(int(data.get("id", 0)))
        text = (msg or {}).get("text") or ""
        room = str(data.get("room", ""))
        sender = "from_user" if data.get("from_user") else ("from_member" if author else "")
        if author:
            add(author, "spoke")
        if not sender:
            return
        r = self.rules
        criticized = any(w in text for w in r.criticism_words)
        praised = any(w in text for w in r.praise_words)
        if room.startswith(DM_PREFIX):
            targets, everyone = [room[len(DM_PREFIX) :]], False
        else:
            names = self.names()
            if f"@{EVERYONE}" in text:
                targets = list(names)
            else:
                targets = [m for m, n in names.items() if n and f"@{n}" in text]
            everyone = not targets and any(w in text for w in EVERYONE_WORDS)
            if everyone:
                targets = list(names)
        for member in targets:
            if member == author:
                continue
            tags = [sender]
            if room.startswith(DM_PREFIX):
                tags.append("dm")
            elif everyone:
                tags.append("everyone")
            else:
                tags.append("mentioned")
            if criticized:
                tags.append("group_criticized" if everyone else "criticized")
            if praised:
                tags.append("praised")
            add(member, *tags)


def _label(value: float, labels: list[tuple[float, str]]) -> str:
    for low, text in labels:
        if value >= low:
            return text
    return labels[-1][1] if labels else ""
