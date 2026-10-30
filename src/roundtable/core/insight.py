"""P4 关系·对你的见解：每周一次，成员按自己的性格和三观，读「关于你」画像和最近和你相处的片段，
在上周的看法上接着想（docs/MEMBER_ENGINE.md「P4 关系」第 2 块）。

- **8 位共用一份事实，见解各人各的**：同一套提示词（``relation_insight``）、同一组参数、同一个预算。
  材料是她自己的随身卡和档案短项、她和你的交情、画像里还没变淡的条目（按把握度最多 ``facts_max``
  条，编号 F）、最近 ``moments_days`` 天她和你私聊里的原话（你说的那句 + 她接的那句，最多
  ``moments_max`` 段，编号 M）、她上周的看法（编号 P，带着你在网页上回她的话）。
- **会变、越来越默契**：每句写明是从上周哪句来的（``from``）以及怎么变的（保留 / 加深 / 改观 /
  新的）；上周的没接着写就算放下。每句留着以前的说法（``history``，最多 ``history_max`` 版），
  网页上能看到她的看法是怎么一步步变的。和你越熟写得越多越深（``depth``，按阶段一张表，8 位相同）。
- **你的反应由她自己理解**：没有打分按钮。她把看法顺口说出来时你怎么接（私聊原话），和你在网页上
  随手回的一句（``RelationPlugin.reply_view``），下周都在材料里，由她自己琢磨你的意思。
- **程序把关**（不调模型）：每句以「我觉得」开头（「我认为」「我感觉」……换成「我觉得」，
  都没有就补上）、不超过这一阶段的字数、新的/加深/改观的必须引用画像或片段（保留的沿用上周的依据）、
  不推测敏感领域（P8 的 ``blocked`` 词表）、和所引依据太像（只是复述）的不要。格式不对时带着原因
  重试一次（第二次的请求和第一次不同）。
- **什么时候想**：和 P6 成长同一天（``growth.weekday``，默认周一），生活每次推进时顺带看一眼，
  上一周还没想过的成员各想一次。还没和你说过话、或画像条目加片段不到 ``min_facts`` 条的，不调用，
  记一条原因。
- **花费**：每次调用的预估相同（``estimate_usd``，8 位相同），步骤 ``relation``，
  计入生活每天的上限、聊天月预算和总预算；额度不够时这周先不想，下次推进再试。
- **写哪**：结果交给关系插件存（``RelationPlugin.set_views``，只有关系插件写关系数据；不写成长、
  不写画像）。只用于生活模式：``run(..., mode="work")`` 报错；工作模式的代码不 import 本模块。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from roundtable.core.chat import USER, ChatMember, call_member, dm_room
from roundtable.core.chat_actions import is_recalled
from roundtable.core.growth import self_text
from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.member.about import SOURCES, AboutYou
from roundtable.core.member.growth import monday, week_id
from roundtable.core.member.memory.store import similarity
from roundtable.core.member.mode import Mode, require_life
from roundtable.core.member.profile import ProfileStore
from roundtable.core.member.relation import RelationPlugin
from roundtable.core.persona import card_text
from roundtable.core.storage.repository import iso

ROOM = "relation"
STEP = "relation"
NONE = "（没有）"
PREFIX = "我觉得"
OTHER_PREFIXES = ("我认为", "我感觉", "我觉着", "我猜", "我想", "我看")


def normalize(text: str) -> str:
    """统一成「我觉得……」开头。"""
    text = " ".join(str(text or "").split()).strip("「」\"' ")
    if text.startswith(PREFIX):
        return text
    for p in OTHER_PREFIXES:
        if text.startswith(p):
            return PREFIX + text[len(p) :].lstrip("，, ")
    return PREFIX + text


class InsightKeeper:
    def __init__(self, rt: Any, members: Callable[[], list[ChatMember]]) -> None:
        self.rt = rt
        self.members = members
        self.allow: Callable[[float], bool] = lambda estimate_usd: True  # 生活每天的上限
        self.relation = RelationPlugin(rt, names=lambda: {m.id: m.name for m in self.members()})
        self.about = AboutYou(rt)
        self.profiles = ProfileStore(rt)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.relation.insight

    def enabled(self) -> bool:
        return self.relation.enabled() and self.rules.enabled and self.about.enabled()

    def due(self, today: date) -> date | None:
        """今天该想哪一周（那一周的周一）；和成长同一天，还没到时为 None。"""
        if today.isoweekday() < self.rt.config.roundtable.chat.engine.growth.weekday:
            return None
        return monday(today) - timedelta(days=7)

    async def tick(self, now: datetime) -> dict[str, int]:
        out = {"members": 0, "views": 0, "skipped": 0}
        if not self.enabled():
            return out
        start = self.due(now.date())
        if start is None:
            return out
        wid = week_id(start)
        for m in self.members():
            if self.relation.views_week(m.id) == wid:
                continue
            got = await self.run(m, wid, today=now.date())
            if got is None:
                continue  # 额度不够：下次推进再试
            out["members"] += 1
            out["views"] += got["views"]
            out["skipped"] += got["skipped"]
        return out

    # --- 一位成员想一次 ----------------------------------------------------------------

    def facts(self, today: date) -> list[dict[str, Any]]:
        """画像里还在用的条目（没变淡、领域没关），按把握度从高到低。"""
        off = set(self.about.switches()["domains_off"])
        live = [
            f
            for f in self.about.facts()
            if f["domain"] not in off and not self.about.faded(f, today)
        ]
        live.sort(key=lambda f: (-self.about.confidence(f, today), f["id"]))
        return live[: self.rules.facts_max]

    def moments(self, member: str) -> list[dict[str, str]]:
        """最近私聊里的相处片段：你说的一句 + 她接的那句（按时间先后，最近 ``moments_max`` 段）。"""
        r = self.rules
        if r.moments_max == 0:
            return []
        since = iso(self.rt.budget.now() - timedelta(days=r.moments_days))
        rows = [
            m
            for m in self.rt.repo.chat_messages(dm_room(member), limit=400)
            if m["created_at"] >= since and not is_recalled(m) and (m.get("text") or "").strip()
        ]
        out: list[dict[str, str]] = []
        for i, m in enumerate(rows):
            if m["author"] != USER:
                continue
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            answer = nxt["text"] if nxt and nxt["author"] == member else ""
            out.append(
                {
                    "day": self.relation.local_day(datetime.fromisoformat(m["created_at"])),
                    "you": _clip(m["text"], r.moment_chars),
                    "me": _clip(answer, r.moment_chars),
                }
            )
        return out[-r.moments_max :]

    def _self(self, member: str) -> str:
        card = card_text(self.rt, member)
        short = self_text(self.profiles.current(member).data)
        parts = [p for p in (card, short if short != NONE else "") if p]
        return "\n".join(parts) or "（没写：按你自己的样子想）"

    def _stage(self, member: str) -> str:
        bond = self.relation.data(member)["user"]
        if not bond:
            return ""
        now = self.rt.budget.now()
        quiet = "，有阵子没聊了" if self.relation.quiet(bond, now) else ""
        return (
            f"{self.relation.stage(member)}：认识第 {self.relation.known_days(bond, now)} 天，"
            f"一起聊过 {bond['days']} 天、{bond['n']} 次来回{quiet}"
        )

    async def run(
        self,
        member: ChatMember,
        week: str,
        *,
        today: date | None = None,
        mode: Mode | str = Mode.LIFE,
    ) -> dict[str, int] | None:
        """她想一次这一周对你的看法。额度不够时返回 None（这周还算没想过）。"""
        require_life(mode)
        r = self.rules
        today = today or self.about.today()
        facts = self.facts(today)
        moments = self.moments(member.id)
        stage = self._stage(member.id)
        if not stage or len(facts) + len(moments) < r.min_facts:
            why = (
                "还没和你说过话"
                if not stage
                else f"画像和最近的聊天加起来只有 {len(facts) + len(moments)} 条，还不够想"
            )
            self.relation.set_views(member.id, week, self.relation.views(member.id), why)
            return {"views": 0, "skipped": 1}
        depth = r.at(self.relation.level(member.id))
        previous = self.relation.views(member.id, {f["id"] for f in self.about.facts()})
        variables = {
            "name": member.name,
            "profile": self._self(member.id),
            "stage": stage,
            "depth": depth.hint or "交情浅时说得保守些，熟了可以说得直一点。",
            "facts": "\n".join(
                f"F{f['id']}. [{SOURCES.get(f['source'], f['source'])}] {f['text']}" for f in facts
            )
            or NONE,
            "moments": "\n".join(
                f"M{i}. [{m['day']}] TA：「{m['you']}」" + (f" 我：「{m['me']}」" * bool(m["me"]))
                for i, m in enumerate(moments, 1)
            )
            or NONE,
            "previous": "\n".join(_previous_line(i, v) for i, v in enumerate(previous, 1)) or NONE,
            "feedback": "",
            "max_views": str(depth.views),
            "view_chars": str(depth.chars),
        }
        views: list[dict[str, Any]] = []
        notes: list[str] = []
        for attempt in range(2):
            text = await self._call(member, variables)
            if text is None:
                return None
            got = self.parse(text)
            if got is None:
                notes = ["没有按格式只输出一个 JSON 对象"]
            else:
                views, notes = self.check(got, facts, moments, previous, depth, week)
            if got == [] or views:
                break  # 没想法，或者有能用的：不重试
            if attempt == 0:  # 带着原因再请求一次（请求和第一次不同，不原样重试）
                variables["feedback"] = "上一次的问题：" + "；".join(notes) + "。\n"
        changes = _changes(views, previous)
        note = "；".join(notes) if notes else ("" if views else "这周没什么新想法")
        self.relation.set_views(member.id, week, views, note, changes)
        return {"views": len(views), "skipped": 0 if views else 1}

    async def _call(self, member: ChatMember, variables: dict[str, str]) -> str | None:
        rt, r = self.rt, self.rules
        if not self.allow(r.estimate_usd):
            return None
        role = "relation_insight"
        prompt = rt.prompts.render(role, rt.config.roundtable.prompts[role], **variables)
        params = dict(rt.config.roundtable.step_params.get("chat", {}))
        params["max_tokens"] = r.max_tokens
        completion = await call_member(
            rt,
            room=ROOM,
            model_id=member.model_id,
            prompt=prompt,
            step=STEP,
            params=params,
            estimate_usd=r.estimate_usd,
        )
        return None if completion is None else completion.text

    @staticmethod
    def parse(text: str) -> list[dict[str, Any]] | None:
        try:
            views = extract_json_object(text).get("views")
        except JSONOutputError:
            return None
        if not isinstance(views, list):
            return None
        return [v for v in views if isinstance(v, dict)]

    def check(
        self,
        proposals: list[dict[str, Any]],
        facts: list[dict[str, Any]],
        moments: list[dict[str, str]],
        previous: list[dict[str, Any]],
        depth: Any,
        week: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """程序把关，并把每句接到上周那句上（留以前的说法）。返回 (能用的见解, 被拦下的原因)。"""
        r = self.rules
        by_id = {f["id"]: f for f in facts}
        out: list[dict[str, Any]] = []
        notes: list[str] = []
        used_prev: set[int] = set()
        for p in proposals:
            if len(out) >= depth.views:
                break
            text = normalize(p.get("text", ""))
            body = text[len(PREFIX) :].strip("，, ")
            cited = [i for i in dict.fromkeys(_ids(p.get("facts"), "F")) if i in by_id]
            seen = [i for i in dict.fromkeys(_ids(p.get("moments"), "M")) if 1 <= i <= len(moments)]
            k = next(iter(_ids([p.get("from")], "P")), 0)
            prev = previous[k - 1] if 1 <= k <= len(previous) and k not in used_prev else None
            change = str(p.get("change") or "").strip()
            if prev is None:
                change = "新的"
            elif text == prev["text"]:
                change = "保留"
            elif change not in ("加深", "改观"):
                change = "加深"
            sources = [by_id[i]["text"] for i in cited] + [moments[i - 1]["you"] for i in seen]
            if change == "保留" and prev is not None:
                cited = cited or [i for i in prev.get("facts") or []]
            if len(body) < 2:
                notes.append("有一句是空的")
            elif len(text) > depth.chars:
                notes.append(f"「{text[:12]}…」超过 {depth.chars} 字")
            elif not sources and change != "保留":
                notes.append(f"「{text[:12]}…」没写从哪来（画像 F 编号或聊天 M 编号）")
            elif self.about.blocked(text):
                notes.append(f"「{text[:12]}…」猜了不该猜的（{self.about.blocked(text)}）")
            elif any(similarity(body, x) >= r.rehash_similarity for x in sources) or any(
                body.strip("。") in x for x in sources
            ):
                notes.append(f"「{text[:12]}…」只是把事实又说了一遍")
            elif any(similarity(body, v["text"]) >= r.rehash_similarity for v in out):
                notes.append(f"「{text[:12]}…」和前一句差不多")
            else:
                view: dict[str, Any] = {
                    "text": text,
                    "facts": cited,
                    "quotes": [moments[i - 1]["you"] for i in seen]
                    or (list(prev.get("quotes") or []) if prev and change == "保留" else []),
                    "agree": p.get("agree") is not False,
                    "change": change,
                    "week": week if change != "保留" or prev is None else prev.get("week", week),
                    "since": (prev or {}).get("since") or week,
                    "history": [],
                }
                if prev is not None:
                    used_prev.add(k)
                    history = list(prev.get("history") or [])
                    if change != "保留" or prev.get("reply"):
                        entry = {"week": prev.get("week", ""), "text": prev["text"]}
                        if prev.get("reply"):
                            entry["reply"] = prev["reply"]["text"]
                        history.append(entry)
                    view["history"] = history[-r.history_max :] if r.history_max else []
                    if change == "改观":
                        view["was"] = _clip(str(p.get("was") or ""), depth.chars)
                out.append(view)
        return out, notes


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _ids(values: Any, prefix: str) -> list[int]:
    """["F2", 3, "M1"] → 只要这个前缀（或不带前缀的数字）的编号。"""
    out: list[int] = []
    for x in values if isinstance(values, list) else []:
        s = str(x or "").strip().upper()
        if s.startswith(prefix):
            s = s[len(prefix) :]
        elif s[:1].isalpha():
            continue
        if s.isdigit():
            out.append(int(s))
    return out


def _previous_line(i: int, v: dict[str, Any]) -> str:
    line = f"P{i}. {v['text']}"
    if v.get("since"):
        line += f"（从 {v['since']} 起这么想）"
    if (v.get("reply") or {}).get("text"):
        line += f"（TA 在网页上回你：「{v['reply']['text']}」）"
    return line


def _changes(views: list[dict[str, Any]], previous: list[dict[str, Any]]) -> list[str]:
    """这周看法怎么变的，写给网页看的大白话。"""
    out: list[str] = []
    kept = {h["text"] for v in views for h in v.get("history") or []} | {v["text"] for v in views}
    for v in views:
        if v["change"] == "改观":
            was = v.get("was") or (v["history"][-1]["text"] if v.get("history") else "")
            out.append(f"改观：{was} → {v['text']}" if was else f"改观：{v['text']}")
        elif v["change"] == "加深":
            out.append(f"加深：{v['text']}")
        elif v["change"] == "新的":
            out.append(f"新想到：{v['text']}")
    for p in previous:
        if p["text"] not in kept:
            out.append(f"放下了：{p['text']}")
    return out
