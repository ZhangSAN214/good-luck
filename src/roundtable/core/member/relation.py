"""关系（P4，docs/MEMBER_ENGINE.md「P4 关系」）：和你的阶段、同事之间的亲近度，按真实经历累积。

- **和你**：你说话后她在 ``reply_window_minutes`` 内接了（私聊记 ``dm`` 分、群里记 ``group`` 分，
  你一句话她只算一次）才算一来一回。阶段（新邻居 → 熟悉 → 老朋友）看一起聊过的天数、来回次数、
  从认识到现在跨的周数，**只升不降**；亲近度（0～1）不来往会慢慢变淡（半衰期 ``half_life_days``）。
  每天最多记 ``user_daily_cap`` 分：一天刷几百句也成不了老朋友。工作群不算（工作与生活分开）。
- **同事之间**：生活里碰到一起（``gather``）、两人私下聊天（``pair``）、生活群和私聊里一来一回
  （``chat``）、一起在办公室干过同一件活（``work``，只读工作记录，从不反写）。两人的分数对称，
  各存在自己的那份里；每对每天最多 ``peer_daily_cap`` 分。看法（「常在食堂碰到」
  「一起干过 3 次活」）由程序按记下的次数写，不调模型。
- **用处**：生活里挑谁和谁聊时按亲近度加权（``weight``，权重 = 1 + ``pair_bias`` × 亲近度，
  生人也有机会碰到）；私聊、群聊、朋友圈、生活的 ``<persona>`` 里加一行「和你」；
  生活场景里加一行交情。
  **不影响工作派活**：工作模式不挂载（``line(..., mode="work")`` 报错，工作代码不 import 本模块）。
- **只有这个插件写关系**（``app_settings`` 的 ``member_relation:<成员>`` 和 ``relation.tracker``，
  不加迁移）；别的模块只读 ``closeness`` / ``stage`` / ``line`` / ``weight``。
- 8 位成员规则、分值、字数上限完全相同；不调用模型、不花钱。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.mode import Mode, require_life
from roundtable.core.office.apartment import PUBLIC_ROOMS
from roundtable.core.storage.repository import iso

log = logging.getLogger(__name__)

SOURCE = "relation"  # 上下文组装器里的来源名
WATCH = ("chat.message", "life.event", "work.done")
USER = "user"
DM_PREFIX = "dm:"
PAIR_PREFIX = "pair:"
WORK_KINDS = frozenset({"work"})  # 工作群（会话种类，也是它的房间名）：不算关系
KINDS = ("gather", "pair", "chat", "work")
KEEP_SESSIONS = 200  # 记住最近多少场工作（认谁和谁一起干过）
KEEP_DAYS = 60  # 每段关系记住最近多少天各记了几分（认「新的一天」、每天上限）


def _decay(days: float, half_life: float) -> float:
    return 0.5 ** (max(days, 0.0) / half_life)


def _new_bond(at: str) -> dict[str, Any]:
    return {
        "pts": 0.0,  # 亲近分（截至 at，之后按半衰期变淡）
        "at": at,
        "n": 0,  # 一共几次
        "days": 0,  # 一起有经历的天数
        "daily": {},  # 最近几天（本地日期）→ 那天已经记了几分（每天有上限）
        "first": at,
        "last": at,
        "kinds": {},  # 种类 → 次数
        "places": {},  # 碰到的地方 → 次数
    }


class RelationPlugin:
    """P4 关系。订阅总线（传了 bus 时），只写自己的关系键。"""

    def __init__(
        self,
        rt: Any,
        bus: EventBus | None = None,
        names: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self.rt = rt
        self.bus = bus
        self.names = names or (lambda: {})
        if bus is not None:
            for kind in WATCH:
                bus.subscribe(kind, self.on_event)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.relation

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return engine.enabled and engine.relation.enabled

    def local_day(self, at: datetime) -> str:
        tz = home_zone(self.rt).tz()
        return at.astimezone(tz).date().isoformat()

    # --- 读 ---------------------------------------------------------------------

    def data(self, member: str) -> dict[str, Any]:
        d = self.rt.repo.relation_data(member) or {}
        d.setdefault("user", None)
        d.setdefault("peers", {})
        d.setdefault("stages", [])  # 和你的阶段变化：[{at, stage}]
        return d

    def _bond(self, member: str, other: str) -> dict[str, Any] | None:
        d = self.data(member)
        return d["user"] if other == USER else d["peers"].get(other)

    def points(self, bond: dict[str, Any] | None, at: datetime | None = None) -> float:
        if not bond:
            return 0.0
        at = at or self.rt.budget.now()
        days = (at - datetime.fromisoformat(bond["at"])).total_seconds() / 86400
        return bond["pts"] * _decay(days, self.rules.half_life_days)

    def closeness(self, member: str, other: str, at: datetime | None = None) -> float:
        """亲近度 0～1（other 是另一位成员的 id，或 ``"user"``）。"""
        if not self.enabled():
            return 0.0
        pts = self.points(self._bond(member, other), at)
        scale = self.rules.user_scale if other == USER else self.rules.scale
        return 1 - math.exp(-pts / scale)

    def weight(self, member: str, other: str, at: datetime | None = None) -> float:
        """生活里挑谁和谁聊的权重（≥1；没来往过的也是 1，生人照样能碰到）。"""
        return 1 + self.rules.pair_bias * self.closeness(member, other, at)

    def computed_stage(self, bond: dict[str, Any] | None) -> int:
        """按累积的经历算到第几阶段（stages 的下标）。"""
        if not bond:
            return 0
        weeks = (
            datetime.fromisoformat(bond["last"]) - datetime.fromisoformat(bond["first"])
        ).days // 7
        got = 0
        for i, s in enumerate(self.rules.stages):
            if bond["days"] >= s.min_days and bond["n"] >= s.min_exchanges and weeks >= s.min_weeks:
                got = i
        return got

    def stage(self, member: str) -> str:
        """和你此刻的阶段（只升不降）。"""
        stages = self.rules.stages
        level = int((self.data(member)["user"] or {}).get("level", 0))
        return stages[min(level, len(stages) - 1)].name

    def known_days(self, bond: dict[str, Any] | None, at: datetime) -> int:
        if not bond:
            return 0
        first = datetime.fromisoformat(bond["first"]).astimezone(home_zone(self.rt).tz()).date()
        return (at.astimezone(home_zone(self.rt).tz()).date() - first).days + 1

    def quiet(self, bond: dict[str, Any] | None, at: datetime) -> bool:
        if not bond:
            return False
        return at - datetime.fromisoformat(bond["last"]) > timedelta(days=self.rules.quiet_days)

    def line(self, member: str, at: datetime | None = None, mode: Mode | str = Mode.LIFE) -> str:
        """写进提示词的「和你」那一行（只在生活模式用；≤ context_chars，8 位相同）。
        还没和你说过话时为空串。"""
        require_life(mode)
        r = self.rules
        if not self.enabled() or r.context_chars == 0:
            return ""
        at = at or self.rt.budget.now()
        bond = self.data(member)["user"]
        if not bond:
            return ""
        text = r.line_format.format(
            stage=self.stage(member),
            known=self.known_days(bond, at),
            quiet=r.quiet_format if self.quiet(bond, at) else "",
        )
        return text[: r.context_chars]

    def label(self, closeness: float) -> str:
        for low, text in self.rules.peer_labels:
            if closeness >= low:
                return text
        return self.rules.peer_labels[-1][1] if self.rules.peer_labels else ""

    def view(self, member: str, other: str, at: datetime | None = None) -> str:
        """她对另一位同事的看法（程序按记下的次数写，不调模型）。没来往过为空串。"""
        at = at or self.rt.budget.now()
        bond = self.data(member)["peers"].get(other)
        if not bond or not bond["n"]:
            return ""
        reasons = self.reasons(bond)
        text = self.label(self.closeness(member, other, at))
        if reasons:
            text += "：" + "、".join(reasons[:2])
        if self.quiet(bond, at):
            text += "；最近没怎么来往"
        return text

    @staticmethod
    def reasons(bond: dict[str, Any]) -> list[str]:
        """按次数从多到少：常在哪碰到、私下聊过几次、群里搭过几回话、一起干过几次活。"""
        kinds = bond.get("kinds") or {}
        out: list[tuple[int, str]] = []
        if n := kinds.get("gather"):
            places = bond.get("places") or {}
            top = max(places, key=lambda p: (places[p], p)) if places else ""
            where = PUBLIC_ROOMS.get(top, "")
            out.append((n, f"常在{where}碰到（{n} 次）" if where else f"一起待过 {n} 次"))
        if kinds.get("pair"):
            out.append((kinds["pair"], f"私下聊过 {kinds['pair']} 次"))
        if kinds.get("chat"):
            out.append((kinds["chat"], f"群里搭过 {kinds['chat']} 回话"))
        if kinds.get("work"):
            out.append((kinds["work"], f"一起干过 {kinds['work']} 次活"))
        return [text for _, text in sorted(out, key=lambda x: -x[0])]

    def scene_line(self, members: list[str], at: datetime | None = None) -> str:
        """生活场景里的「交情」：在场几位两两之间的亲近度（没来往过的不写）。"""
        r = self.rules
        if not self.enabled() or r.scene_chars == 0 or len(members) < 2:
            return ""
        names = self.names()
        parts = []
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                bond = self.data(a)["peers"].get(b)
                if not bond or not bond["n"]:
                    continue
                label = self.label(self.closeness(a, b, at))
                parts.append(f"{names.get(a, a)}和{names.get(b, b)}{label}")
        if not parts:
            return ""
        return ("交情：" + "；".join(parts))[: r.scene_chars]

    # --- 写（只有这里写关系）-------------------------------------------

    def add(
        self,
        member: str,
        other: str,
        at: datetime,
        points: float,
        kind: str = "",
        place: str = "",
    ) -> None:
        """给 member 记一次和 other（成员 id 或 ``"user"``）的共同经历。"""
        r = self.rules
        d = self.data(member)
        stamp = iso(at)
        if other == USER:
            bond = d["user"] or _new_bond(stamp)
            cap = r.user_daily_cap
        else:
            bond = d["peers"].get(other) or _new_bond(stamp)
            cap = r.peer_daily_cap
        day = self.local_day(at)
        daily: dict[str, float] = bond["daily"]
        if day not in daily:
            if len(daily) >= KEEP_DAYS and day < min(daily):
                gain = 0.0  # 太早的补算（记得的天数之前）：天数不确定，不加分也不加天
            else:
                bond["days"] += 1
                daily[day] = 0.0
                while len(daily) > KEEP_DAYS:
                    daily.pop(min(daily))
        if day in daily:
            gain = max(0.0, min(points, cap - daily[day]))
            daily[day] += gain
        # 亲近分：先把旧分折到较晚的时刻，再加上这次（早于记下时刻的按经过的时间折算）
        last_at = datetime.fromisoformat(bond["at"])
        if at >= last_at:
            days = (at - last_at).total_seconds() / 86400
            bond["pts"] = bond["pts"] * _decay(days, r.half_life_days) + gain
            bond["at"] = stamp
        else:
            days = (last_at - at).total_seconds() / 86400
            bond["pts"] += gain * _decay(days, r.half_life_days)
        bond["n"] += 1
        bond["first"] = min(bond["first"], stamp)
        bond["last"] = max(bond["last"], stamp)
        if kind:
            bond["kinds"][kind] = bond["kinds"].get(kind, 0) + 1
        if place:
            bond["places"][place] = bond["places"].get(place, 0) + 1
        if other == USER:
            level = max(int(bond.get("level", 0)), self.computed_stage(bond))
            if level > int(bond.get("level", 0)):
                d["stages"].append({"at": stamp, "stage": r.stages[level].name})
            bond["level"] = level
            d["user"] = bond
        else:
            d["peers"][other] = bond
        self.rt.repo.save_relation_data(member, d)

    def on_event(self, event: Event) -> None:
        if not self.enabled():
            return
        if event.kind == "life.event":
            self._life(event)
        elif event.kind == "chat.message":
            self._chat(event)
        elif event.kind == "work.done":
            self._work(event)

    def _life(self, event: Event) -> None:
        """生活里碰到一起 / 两人私下聊天：只给这条事件的成员记（每位各有一条事件）。"""
        data, me = event.data, event.member
        kind = data.get("kind")
        if not me or kind not in ("gather", "pair"):
            return
        place = str(data.get("place") or "")
        points = getattr(self.rules.weights, kind)
        for other in data.get("members") or []:
            if other != me:
                self.add(me, other, event.at, points, kind, place if kind == "gather" else "")

    def _room_kind(self, room: str) -> str:
        if room.startswith(DM_PREFIX):
            return "dm"
        if room.startswith(PAIR_PREFIX):
            return "pair"
        row = self.rt.repo.chat_room(room)
        if row:
            return str(row["kind"])
        return room if room in WORK_KINDS else "group"

    def _chat(self, event: Event) -> None:
        data, author = event.data, event.member
        room = str(data.get("room", ""))
        kind = self._room_kind(room)
        if kind in WORK_KINDS:  # 工作群不算关系
            return
        r = self.rules
        window = timedelta(minutes=r.reply_window_minutes)
        tracker = self.rt.repo.relation_tracker()
        rooms = tracker.setdefault("rooms", {})
        slot = rooms.setdefault(room, {})
        if data.get("from_user"):
            slot["user"] = {"at": iso(event.at), "answered": []}
        elif author:
            asked = slot.get("user")
            if (
                asked
                and author not in asked["answered"]
                and timedelta(0) <= event.at - datetime.fromisoformat(asked["at"]) <= window
            ):
                asked["answered"].append(author)
                points = r.weights.dm if kind == "dm" else r.weights.group
                self.add(author, USER, event.at, points, kind)
            prev = slot.get("member")
            if (
                prev
                and prev["who"] != author
                and timedelta(0) <= event.at - datetime.fromisoformat(prev["at"]) <= window
            ):
                for a, b in ((author, prev["who"]), (prev["who"], author)):
                    self.add(a, b, event.at, r.weights.chat, "chat")
            slot["member"] = {"who": author, "at": iso(event.at)}
        else:
            return
        self.rt.repo.save_relation_tracker(tracker)

    def _work(self, event: Event) -> None:
        """一起在办公室干过同一件活：同一场的成员两两记一次（只读工作记录）。"""
        me, sid = event.member, str(event.data.get("session_id") or "")
        if not me or not sid:
            return
        tracker = self.rt.repo.relation_tracker()
        sessions: dict[str, list[str]] = tracker.setdefault("work", {})
        crew = sessions.get(sid, [])
        if me in crew:
            return
        for other in crew:
            for a, b in ((me, other), (other, me)):
                self.add(a, b, event.at, self.rules.weights.work, "work")
        sessions.pop(sid, None)
        sessions[sid] = [*crew, me]  # 新的放最后，超了丢最旧的
        while len(sessions) > KEEP_SESSIONS:
            sessions.pop(next(iter(sessions)))
        self.rt.repo.save_relation_tracker(tracker)

    def clear(self, member: str | None = None) -> int:
        """清空关系（全部或某一位）。用户的权利。"""
        return self.rt.repo.clear_relation_data(member)

    # --- 给网页看 ---------------------------------------------------------------

    def summary(self, member: str, at: datetime | None = None) -> dict[str, Any]:
        at = at or self.rt.budget.now()
        r, d, names = self.rules, self.data(member), self.names()
        user = d["user"]
        level = int((user or {}).get("level", 0))
        nxt = r.stages[level + 1] if level + 1 < len(r.stages) else None
        peers = []
        for other, bond in d["peers"].items():
            c = self.closeness(member, other, at)
            peers.append(
                {
                    "member": other,
                    "name": names.get(other, other),
                    "closeness": round(c, 3),
                    "label": self.label(c),
                    "view": self.view(member, other, at),
                    "n": bond["n"],
                    "days": bond["days"],
                    "kinds": bond["kinds"],
                    "last": bond["last"],
                }
            )
        peers.sort(key=lambda p: (-p["closeness"], p["member"]))
        return {
            "member": member,
            "user": {
                "stage": self.stage(member),
                "closeness": round(self.closeness(member, USER, at), 3),
                "n": (user or {}).get("n", 0),
                "days": (user or {}).get("days", 0),
                "known": self.known_days(user, at),
                "quiet": self.quiet(user, at),
                "first": (user or {}).get("first"),
                "last": (user or {}).get("last"),
                "line": self.line(member, at),
                "next": (
                    {
                        "stage": nxt.name,
                        "days": nxt.min_days,
                        "exchanges": nxt.min_exchanges,
                        "weeks": nxt.min_weeks,
                    }
                    if nxt
                    else None
                ),
                "history": d["stages"],
            },
            "peers": peers,
        }
