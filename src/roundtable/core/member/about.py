"""关于你（P8，docs/MEMBER_ENGINE.md「P8 关于你」）：8 位成员共用的一份用户画像。

- **只放事实**：每条是一句关于用户的事，带领域、来源（你在画像页写的 / 你亲口说的 / 成员推测的）、
  证据（你的原话：哪条消息、哪个会话、什么时候、哪位成员注意到的）和把握度。每位成员对你的
  **看法**不在这里（归 P4 关系）。
- **线索从哪来**：夜间整理时每位成员顺带交几条线索（``memory_night`` v2 的 ``about``，不多调用），
  每条必须引你的原话；程序在那一天你在私聊、生活群里发的消息里找这句原话，**找不到就不记**
  （防编造）。工作群里的话不读。
- **把握度**：你写的 1；你亲口说的 ``said_confidence``，不变淡；推测从 ``inferred_confidence``
  起步，每多一条不同的证据加一点，``half_life_days`` 天没有新证据就淡一半，淡到 ``faded_below``
  以下聊天就不带了（画像页还看得到）。你锁住的不变淡、不被程序改。
- **不推测**：健康、宗教、政治、性取向、经济、行动轨迹（``blocked`` 词表），线索里出现就不记；
  你自己在画像页写的不拦。按领域关掉的不记也不带。点过「不对」的，之后像它的线索都不再记。
- **聊天只带相关的**：私聊和生活群里按和当前话题的相关度 × 把握度挑最多 ``inject_max`` 条、
  ``context_chars`` 字内，8 位相同。工作模式不挂载（``lines(..., mode="work")`` 报错，有测试）。
- **每周整合一次**（程序，不调模型）：你撤回或删掉的消息不再当证据，没有证据的推测删掉；
  同领域很像的几条合成一条；超过上限时先删最淡的推测。
- **只有本插件写**：存在 ``app_settings`` 的 ``about_you:<名字>`` 键（不加迁移，测试守卫别的代码
  不写）。数据只存本机，可看、可改、可删、可清空、可导出。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.member.memory.store import similarity
from roundtable.core.member.mode import Mode, require_life
from roundtable.core.recall import _NOISE, grams

PROFILE = "profile"
SWITCHES = "switches"
SCHEDULE = "schedule"
# 第二块：用户状态，每项默认关（只有你在画像页打开的才进聊天）
STATUS_KINDS = {
    "music": "正在放的歌",
    "idle": "电脑空闲",
    "game": "在玩游戏",
    "timetable": "课表和 DDL",
    "mail": "邮件栏",
}
MUSIC_PLAYERS = ("qqmusic", "cloudmusic", "spotify", "any")
WEEKDAYS = "一二三四五六日"
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
USER, SAID, INFERRED = "user", "said", "inferred"
SOURCES = {USER: "你写的", SAID: "你说过", INFERRED: "推测"}
USER_AUTHOR = "user"
LIFE_ROOMS = frozenset({"group", "dm"})  # 线索只认私聊和生活群里你的原话（工作群不读）
MIN_QUOTE = 4  # 原话至少几个字（整条消息更短时要一字不差）
QUERY_CHARS = 300  # 相关度只看当前对话最后这么多字
REJECTED_MAX = 500
HISTORY_MAX = 10


class AboutError(ValueError):
    """用户操作不合法（领域不对、内容为空）。"""


class AboutMissing(AboutError):
    """找不到这一条。"""


def norm(text: str) -> str:
    return _NOISE.sub("", (text or "").lower())


def one_line(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def relevance(fact: str, query: str) -> float:
    """这条事实的两字片段有多少出现在当前对话里（0～1）。"""
    f = grams(fact, (2,))
    if not f:
        return 0.0
    return len(f & grams(query, (2,))) / len(f)


class AboutYou:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.about

    def enabled(self) -> bool:
        return bool(self.rt.config.roundtable.chat.engine.enabled and self.rules.enabled)

    def today(self) -> date:
        return self.rt.budget.now().astimezone(home_zone(self.rt).tz()).date()

    # --- 存取（只有这里写） ---------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        data = self.rt.repo.about_you(PROFILE) or {}
        data.setdefault("next", 1)
        data.setdefault("facts", [])
        data.setdefault("rejected", [])
        data.setdefault("week", "")
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.rt.repo.save_about_you(PROFILE, data)

    def switches(self) -> dict[str, Any]:
        data = self.rt.repo.about_you(SWITCHES) or {}
        data.setdefault("domains_off", [])
        status = data.setdefault("status", {})
        for k in STATUS_KINDS:
            status.setdefault(k, False)  # 默认全关
        data.setdefault("music_player", "qqmusic")
        data.setdefault("games", {})
        return data

    def _save_switches(self, data: dict[str, Any]) -> None:
        self.rt.repo.save_about_you(SWITCHES, data)

    def facts(self) -> list[dict[str, Any]]:
        return self._load()["facts"]

    # --- 把握度 ---------------------------------------------------------------------

    def confidence(self, fact: dict[str, Any], today: date | None = None) -> float:
        r = self.rules
        if fact["source"] == USER:
            return 1.0
        if fact["source"] == SAID:
            return r.said_confidence
        n = len({e["msg"] for e in fact["evidence"]}) or 1
        base = min(r.inferred_cap, r.inferred_confidence + r.evidence_bonus * (n - 1))
        if fact.get("locked"):
            return base
        today = today or self.today()
        age = max((today - date.fromisoformat(fact["seen"])).days, 0)
        return base * 0.5 ** (age / r.half_life_days)

    def faded(self, fact: dict[str, Any], today: date | None = None) -> bool:
        return not fact.get("locked") and self.confidence(fact, today) < self.rules.faded_below

    def blocked(self, text: str) -> str | None:
        """命中不推测的领域时返回领域名（健康、宗教……）。"""
        t = (text or "").lower()
        for name, words in self.rules.blocked.items():
            if any(w.lower() in t for w in words):
                return name
        return None

    def domain_of(self, domain: str) -> str:
        domains = self.rules.domains
        return domain if domain in domains else domains[-1]

    # --- 聊天时带几条（只在生活模式） ---------------------------------------------------

    def lines(self, query: str, mode: Mode | str = Mode.LIFE, today: date | None = None) -> str:
        """和当前对话相关的几条（8 位相同的条数和字数）；没有相关的返回空串。"""
        require_life(mode)
        r = self.rules
        if not self.enabled() or r.inject_max == 0 or r.context_chars == 0:
            return ""
        today = today or self.today()
        off = set(self.switches()["domains_off"])
        query = (query or "")[-QUERY_CHARS:]
        scored = []
        for f in self.facts():
            if f["domain"] in off or self.faded(f, today):
                continue
            rel = relevance(f["text"], query)
            if rel < r.min_relevance:
                continue
            conf = self.confidence(f, today)
            scored.append((rel * conf, f["id"], f, conf))
        scored.sort(key=lambda x: (-x[0], x[1]))
        out: list[str] = []
        used = 0
        for _, _, f, conf in scored[: r.inject_max]:
            maybe = "（可能）" if f["source"] == INFERRED and conf < r.maybe_below else ""
            line = f"- {f['text']}{maybe}"
            need = len(line) + (1 if out else 0)
            if used + need > r.context_chars:
                continue
            out.append(line)
            used += need
        return f"{r.header}\n" + "\n".join(out) if out else ""

    # --- 夜间整理交来的线索 -----------------------------------------------------------

    def _evidence(self, quote: str, start: str, end: str) -> dict[str, Any] | None:
        """在 [start, end) 你在私聊、生活群里发的消息里找这句原话。"""
        q = norm(quote)
        if not q:
            return None
        kinds = {room["id"]: room["kind"] for room in self.rt.repo.chat_rooms()}
        for msg in self.rt.repo.chat_messages_between(start, end):
            if msg["author"] != USER_AUTHOR or kinds.get(msg["room"]) not in LIFE_ROOMS:
                continue
            if (msg.get("meta") or {}).get("recalled"):
                continue
            text = norm(msg["text"])
            if (len(q) >= MIN_QUOTE and q in text) or (q == text and q):
                return {
                    "full": msg["text"],
                    "msg": msg["id"],
                    "room": msg["room"],
                    "at": msg["created_at"],
                    "quote": one_line(msg["text"], 60),
                }
        return None

    def _match(self, facts: list[dict[str, Any]], text: str, domain: str) -> dict[str, Any] | None:
        best, score = None, 0.0
        for f in facts:
            s = similarity(text, f["text"])
            if f["domain"] != domain and s < 0.8:
                continue
            if s > score:
                best, score = f, s
        return best if score >= self.rules.merge_similarity else None

    def observe(
        self, member: str, day: str, window: tuple[str, str], clues: list[dict[str, Any]]
    ) -> dict[str, int]:
        """成员夜间整理交来的线索 → 画像。window：这一天的 [开始, 结束)（库里的时间）。"""
        out = {"new": 0, "merge": 0, "skip": 0, "blocked": 0, "unverified": 0, "rejected": 0}
        r = self.rules
        if not self.enabled() or r.clues_per_night == 0:
            return out
        data = self._load()
        off = set(self.switches()["domains_off"])
        for clue in clues[: r.clues_per_night]:
            text = one_line(str(clue.get("text") or ""), r.fact_chars)
            quote = str(clue.get("quote") or "")
            domain = self.domain_of(str(clue.get("domain") or ""))
            if not text or domain in off:
                out["skip"] += 1
                continue
            if self.blocked(f"{text} {quote}"):
                out["blocked"] += 1
                continue
            if any(similarity(text, x["text"]) >= r.reject_similarity for x in data["rejected"]):
                out["rejected"] += 1
                continue
            ev = self._evidence(quote, *window)
            if ev is None:
                out["unverified"] += 1
                continue
            if self.blocked(ev.pop("full")):  # 原话整句里有敏感的事也不记
                out["blocked"] += 1
                continue
            ev["by"] = member
            source = SAID if clue.get("said") else INFERRED
            old = self._match(data["facts"], text, domain)
            if old is not None:
                if not old.get("locked"):
                    self._add_evidence(old, ev, day)
                    if source == SAID and old["source"] == INFERRED:
                        old["source"] = SAID
                out["merge"] += 1
                continue
            if not self._room(data):
                out["skip"] += 1
                continue
            data["facts"].append(
                {
                    "id": data["next"],
                    "text": text,
                    "domain": domain,
                    "source": source,
                    "locked": False,
                    "created": day,
                    "seen": day,
                    "evidence": [ev],
                    "history": [],
                }
            )
            data["next"] += 1
            out["new"] += 1
        self._save(data)
        return out

    def _add_evidence(self, fact: dict[str, Any], ev: dict[str, Any], day: str) -> None:
        if all(e["msg"] != ev["msg"] for e in fact["evidence"]):
            fact["evidence"] = (fact["evidence"] + [ev])[-self.rules.max_evidence :]
        elif all(e.get("by") != ev["by"] for e in fact["evidence"] if e["msg"] == ev["msg"]):
            fact.setdefault("noticed", [])
            if ev["by"] not in fact["noticed"]:
                fact["noticed"].append(ev["by"])
        fact["seen"] = max(fact["seen"], day)

    def _room(self, data: dict[str, Any]) -> bool:
        """满了时先删最淡的推测；删不了返回 False。"""
        facts = data["facts"]
        if len(facts) < self.rules.max_facts:
            return True
        today = self.today()
        loose = [f for f in facts if f["source"] == INFERRED and not f.get("locked")]
        if not loose:
            return False
        worst = min(loose, key=lambda f: (self.confidence(f, today), f["id"]))
        facts.remove(worst)
        return True

    # --- 每周整合一次（程序，不调模型） -----------------------------------------------

    def weekly(self, now: datetime) -> dict[str, int] | None:
        """这一周还没整合过就整合一次；返回各项数量，已整合过返回 None。"""
        if not self.enabled():
            return None
        y, w, _ = now.date().isocalendar()
        week = f"{y}-W{w:02d}"
        data = self._load()
        if data["week"] == week:
            return None
        out = self.consolidate(data, now.date())
        data["week"] = week
        self._save(data)
        return out

    def consolidate(self, data: dict[str, Any], today: date) -> dict[str, int]:
        out = {"dropped_evidence": 0, "dropped": 0, "merged": 0, "trimmed": 0}
        repo = self.rt.repo
        kept = []
        for f in data["facts"]:
            if f["source"] != USER:
                live = []
                for e in f["evidence"]:
                    msg = repo.chat_message(e["msg"])
                    ok = msg is not None and not (msg.get("meta") or {}).get("recalled")
                    if ok and norm(e["quote"].rstrip("…")) in norm(msg["text"]):
                        live.append(e)
                    else:
                        out["dropped_evidence"] += 1
                f["evidence"] = live
                if not live and not f.get("locked"):
                    out["dropped"] += 1
                    continue
            kept.append(f)
        merged: list[dict[str, Any]] = []
        for f in sorted(kept, key=lambda x: x["id"]):
            same = None
            if f["source"] != USER and not f.get("locked"):
                same = self._match(
                    [m for m in merged if m["domain"] == f["domain"]], f["text"], f["domain"]
                )
            if same is None or same.get("locked"):
                merged.append(f)
                continue
            for e in f["evidence"]:
                self._add_evidence(same, e, f["seen"])
            if f["source"] == SAID and same["source"] == INFERRED:
                same["source"] = SAID
            out["merged"] += 1
        while len(merged) > self.rules.max_facts:
            loose = [f for f in merged if f["source"] == INFERRED and not f.get("locked")]
            if not loose:
                break
            merged.remove(min(loose, key=lambda f: (self.confidence(f, today), f["id"])))
            out["trimmed"] += 1
        data["facts"] = merged
        return out

    # --- 你在画像页的操作 --------------------------------------------------------------

    def _find(self, data: dict[str, Any], fact_id: int) -> dict[str, Any]:
        for f in data["facts"]:
            if f["id"] == fact_id:
                return f
        raise AboutMissing("没有这一条（可能已经删了）")

    def _domain(self, domain: str | None) -> str:
        if domain is None or domain == "":
            return self.rules.domains[-1]
        if domain not in self.rules.domains:
            raise AboutError(f"领域只能是：{'、'.join(self.rules.domains)}")
        return domain

    def add(self, text: str, domain: str | None = None) -> dict[str, Any]:
        text = one_line(text, self.rules.fact_chars * 3)
        if not text:
            raise AboutError("内容不能为空")
        data = self._load()
        day = self.today().isoformat()
        fact = {
            "id": data["next"],
            "text": text,
            "domain": self._domain(domain),
            "source": USER,
            "locked": False,
            "created": day,
            "seen": day,
            "evidence": [],
            "history": [],
        }
        data["facts"].append(fact)
        data["next"] += 1
        self._save(data)
        return fact

    def edit(self, fact_id: int, text: str | None = None, domain: str | None = None) -> dict:
        """你改一条：改过的算你写的（把握度 1，不变淡），旧的留在修改记录里。"""
        data = self._load()
        f = self._find(data, fact_id)
        if text is not None:
            text = one_line(text, self.rules.fact_chars * 3)
            if not text:
                raise AboutError("内容不能为空")
            if text != f["text"]:
                f["history"] = (
                    f["history"] + [{"text": f["text"], "at": self.today().isoformat()}]
                )[-HISTORY_MAX:]
                f["text"] = text
                f["source"] = USER
        if domain is not None:
            f["domain"] = self._domain(domain)
        self._save(data)
        return f

    def delete(self, fact_id: int) -> None:
        data = self._load()
        data["facts"].remove(self._find(data, fact_id))
        self._save(data)

    def reject(self, fact_id: int) -> None:
        """点「不对」：删掉这一条，之后像它的线索都不再记。"""
        data = self._load()
        f = self._find(data, fact_id)
        data["facts"].remove(f)
        data["rejected"] = (
            data["rejected"]
            + [{"text": f["text"], "domain": f["domain"], "at": self.today().isoformat()}]
        )[-REJECTED_MAX:]
        self._save(data)

    def forget_rejected(self, index: int) -> None:
        data = self._load()
        if not 0 <= index < len(data["rejected"]):
            raise AboutMissing("没有这一条")
        data["rejected"].pop(index)
        self._save(data)

    def lock(self, fact_id: int, locked: bool = True) -> dict[str, Any]:
        """锁住：不变淡，程序不再改它（证据也不再加）。"""
        data = self._load()
        f = self._find(data, fact_id)
        f["locked"] = bool(locked)
        if not locked:
            f["seen"] = self.today().isoformat()  # 解锁从今天重新算变淡
        self._save(data)
        return f

    def set_domain(self, domain: str, on: bool) -> list[str]:
        """按领域关掉：这个领域不再记，聊天也不带（已有的留着给你看、你可以删）。"""
        self._domain(domain)
        sw = self.switches()
        off = [d for d in sw["domains_off"] if d != domain]
        if not on:
            off.append(domain)
        sw["domains_off"] = off
        self._save_switches(sw)
        return off

    def clear(self, rejected: bool = False) -> int:
        """清空画像；rejected=True 时连「不对」名单一起清。开关不动。返回清掉几条。"""
        data = self._load()
        n = len(data["facts"])
        data["facts"] = []
        if rejected:
            n += len(data["rejected"])
            data["rejected"] = []
        self._save(data)
        return n

    # --- 第二块：你的状态的开关、课表和 DDL -------------------------------------------------

    def set_status(self, kind: str, on: bool) -> dict[str, bool]:
        if kind not in STATUS_KINDS:
            raise AboutError(f"只能是：{'、'.join(STATUS_KINDS.values())}")
        sw = self.switches()
        sw["status"][kind] = bool(on)
        self._save_switches(sw)
        return sw["status"]

    def status_on(self, kind: str) -> bool:
        return bool(self.switches()["status"].get(kind))

    def set_music_player(self, player: str) -> str:
        if player not in MUSIC_PLAYERS:
            raise AboutError("播放器只能是 QQ 音乐、网易云音乐、Spotify 或任意播放器")
        sw = self.switches()
        sw["music_player"] = player
        self._save_switches(sw)
        return player

    def games(self) -> dict[str, str]:
        """游戏表：配置里的 + 你自己加的（你加的优先）。"""
        return {**self.rules.games, **self.switches()["games"]}

    def add_game(self, exe: str, name: str) -> dict[str, str]:
        exe, name = one_line(exe, 80), one_line(name, 30)
        if not exe or not name or not exe.lower().endswith(".exe") or re.search(r"[\\/]", exe):
            raise AboutError("写程序文件名（比如 Game.exe）和游戏名")
        sw = self.switches()
        sw["games"][exe] = name
        self._save_switches(sw)
        return sw["games"]

    def del_game(self, exe: str) -> dict[str, str]:
        sw = self.switches()
        if sw["games"].pop(exe, None) is None:
            raise AboutMissing("没有这个游戏（配置文件里的要在配置里删）")
        self._save_switches(sw)
        return sw["games"]

    def schedule(self) -> dict[str, Any]:
        data = self.rt.repo.about_you(SCHEDULE) or {}
        data.setdefault("next", 1)
        data.setdefault("classes", [])
        data.setdefault("ddls", [])
        return data

    def add_class(self, weekday: int, start: str, end: str, name: str) -> dict[str, Any]:
        """加一节课（每周固定）：weekday 0 = 周一。"""
        name = one_line(name, 30)
        if not 0 <= int(weekday) <= 6:
            raise AboutError("星期要在周一到周日之间")
        if not HHMM.match(start or "") or not HHMM.match(end or ""):
            raise AboutError("时间写成 08:30 这样")
        start, end = (f"{int(t.split(':')[0]):02d}:{t.split(':')[1]}" for t in (start, end))
        if end <= start or not name:
            raise AboutError("要有课名，下课时间要晚于上课时间")
        data = self.schedule()
        row = {"id": data["next"], "weekday": int(weekday), "start": start, "end": end}
        data["classes"].append(row | {"name": name})
        data["classes"].sort(key=lambda c: (c["weekday"], c["start"]))
        data["next"] += 1
        self.rt.repo.save_about_you(SCHEDULE, data)
        return data

    def add_ddl(self, name: str, due: str) -> dict[str, Any]:
        """加一个 DDL：due 写 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM。"""
        name = one_line(name, 30)
        try:
            when = datetime.fromisoformat(due)
        except (TypeError, ValueError):
            raise AboutError("截止时间写成 2026-11-03 或 2026-11-03T23:59") from None
        if not name:
            raise AboutError("要写是什么 DDL")
        data = self.schedule()
        fmt = "%Y-%m-%dT%H:%M" if "T" in due else "%Y-%m-%d"
        data["ddls"].append({"id": data["next"], "name": name, "due": when.strftime(fmt)})
        data["ddls"].sort(key=lambda d: d["due"])
        data["next"] += 1
        self.rt.repo.save_about_you(SCHEDULE, data)
        return data

    def del_schedule(self, kind: str, item_id: int) -> dict[str, Any]:
        data = self.schedule()
        key = {"class": "classes", "ddl": "ddls"}.get(kind)
        if key is None:
            raise AboutError("只能删课或 DDL")
        rows = [r for r in data[key] if r["id"] != item_id]
        if len(rows) == len(data[key]):
            raise AboutMissing("没有这一条")
        data[key] = rows
        self.rt.repo.save_about_you(SCHEDULE, data)
        return data

    def clear_schedule(self) -> int:
        data = self.schedule()
        n = len(data["classes"]) + len(data["ddls"])
        self.rt.repo.clear_about_you(SCHEDULE)
        return n

    def timetable_line(self, now: datetime) -> str:
        """此刻的课和最近的 DDL（now 是你的本地时间）。没有就空串。程序算，不调模型。"""
        data = self.schedule()
        parts = []
        hm = now.strftime("%H:%M")
        today = [c for c in data["classes"] if c["weekday"] == now.weekday()]
        cur = next((c for c in today if c["start"] <= hm < c["end"]), None)
        nxt = next((c for c in today if c["start"] > hm), None)
        if cur:
            parts.append(f"用户正在上「{cur['name']}」（{cur['end']} 下课）")
        elif nxt:
            parts.append(f"用户今天 {nxt['start']} 还有「{nxt['name']}」")
        horizon = now + timedelta(days=self.rules.ddl_days)
        for d in data["ddls"]:
            due = datetime.fromisoformat(d["due"])
            if "T" not in d["due"]:
                due = due.replace(hour=23, minute=59)
            if not now.replace(tzinfo=None) <= due <= horizon.replace(tzinfo=None):
                continue
            days = (due.date() - now.date()).days
            when = "今天" if days == 0 else "明天" if days == 1 else f"还有 {days} 天"
            parts.append(f"「{d['name']}」DDL {when}")
            break
        return "；".join(parts)

    # --- 看和导出 ---------------------------------------------------------------------

    def view(self, today: date | None = None) -> dict[str, Any]:
        today = today or self.today()
        data = self._load()
        off = self.switches()["domains_off"]
        facts = []
        for f in data["facts"]:
            facts.append(
                {
                    **f,
                    "confidence": round(self.confidence(f, today), 2),
                    "faded": self.faded(f, today),
                    "off": f["domain"] in off,
                    "source_label": SOURCES[f["source"]],
                }
            )
        facts.sort(
            key=lambda f: (
                self.rules.domains.index(f["domain"]) if f["domain"] in self.rules.domains else 99,
                f["faded"],
                -f["confidence"],
                f["id"],
            )
        )
        return {
            "enabled": self.enabled(),
            "domains": [{"name": d, "on": d not in off} for d in self.rules.domains],
            "blocked": list(self.rules.blocked),
            "facts": facts,
            "rejected": data["rejected"],
            "week": data["week"],
        }

    def export(self, fmt: str = "json") -> str:
        v = self.view()
        if fmt == "json":
            keep = (
                "id",
                "text",
                "domain",
                "source_label",
                "confidence",
                "faded",
                "locked",
                "created",
                "seen",
                "evidence",
                "history",
            )
            return json.dumps(
                {
                    "facts": [{k: f.get(k) for k in keep} for f in v["facts"]],
                    "rejected": v["rejected"],
                    "domains": v["domains"],
                },
                ensure_ascii=False,
                indent=2,
            )
        lines = ["# 关于你（成员共用的画像）", ""]
        for d in v["domains"]:
            rows = [f for f in v["facts"] if f["domain"] == d["name"]]
            if not rows:
                continue
            lines.append(f"## {d['name']}{'' if d['on'] else '（已关）'}")
            for f in rows:
                tags = [f["source_label"], f"把握 {f['confidence']:.2f}"]
                if f["locked"]:
                    tags.append("锁定")
                if f["faded"]:
                    tags.append("变淡")
                lines.append(f"- {f['text']}（{'，'.join(tags)}）")
                for e in f["evidence"]:
                    lines.append(f"  - 证据：{e['at'][:16].replace('T', ' ')} 「{e['quote']}」")
            lines.append("")
        if v["rejected"]:
            lines.append("## 你说「不对」的")
            lines += [f"- {x['text']}" for x in v["rejected"]]
        return "\n".join(lines).rstrip() + "\n"
