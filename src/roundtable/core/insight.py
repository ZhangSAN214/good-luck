"""P4 关系·对你的见解：每周一次，成员按自己的性格和三观读一遍「关于你」画像，写几句她对你的看法
（docs/MEMBER_ENGINE.md「P4 关系」第 2 块）。

- **8 位共用一份事实（P8 画像），见解各人各的**：同一套提示词（``relation_insight``）、同一组参数、
  同一个预算，材料是她自己的随身卡和档案短项、她和你的交情、画像里还没变淡的条目（按把握度最多
  ``facts_max`` 条）、她上周的见解。可以和别人不同，也可以不同意你（``agree: false``）。
- **程序把关**（不调模型）：每句以「我觉得」开头（「我认为」「我感觉」……换成「我觉得」，
  都没有就补上）、不超过 ``view_chars`` 字、必须引用画像里的编号、不推测敏感领域
  （P8 的 ``blocked`` 词表）、和所引事实太像（只是复述）的不要。格式不对时带着原因重试一次
  （第二次的请求和第一次不同）。
- **什么时候想**：和 P6 成长同一天（``growth.weekday``，默认周一），生活每次推进时顺带看一眼，
  上一周还没想过的成员各想一次。画像里不到 ``min_facts`` 条、或者还没和你说过话的，不调用，
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

from roundtable.core.chat import ChatMember, call_member
from roundtable.core.growth import self_text
from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.member.about import SOURCES, AboutYou
from roundtable.core.member.growth import monday, week_id
from roundtable.core.member.memory.store import similarity
from roundtable.core.member.mode import Mode, require_life
from roundtable.core.member.profile import ProfileStore
from roundtable.core.member.relation import RelationPlugin
from roundtable.core.persona import card_text

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
        stage = self._stage(member.id)
        if not stage or len(facts) < r.min_facts:
            why = "还没和你说过话" if not stage else f"画像里只有 {len(facts)} 条，还不够想"
            self.relation.set_views(member.id, week, self.relation.views(member.id), why)
            return {"views": 0, "skipped": 1}
        previous = self.relation.views(member.id, {f["id"] for f in facts})
        variables = {
            "name": member.name,
            "profile": self._self(member.id),
            "stage": stage,
            "facts": "\n".join(
                f"{f['id']}. [{SOURCES.get(f['source'], f['source'])}] {f['text']}" for f in facts
            ),
            "previous": "\n".join(v["text"] for v in previous) or NONE,
            "feedback": "",
            "max_views": str(r.max_views),
            "view_chars": str(r.view_chars),
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
                views, notes = self.check(got, facts)
            if got == [] or views:
                break  # 没想法，或者有能用的：不重试
            if attempt == 0:  # 带着原因再请求一次（请求和第一次不同，不原样重试）
                variables["feedback"] = "上一次的问题：" + "；".join(notes) + "。\n"
        note = "；".join(notes) if notes else ("" if views else "这周没什么新想法")
        self.relation.set_views(member.id, week, views, note)
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
        self, proposals: list[dict[str, Any]], facts: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """程序把关。返回 (能用的见解, 被拦下的原因)。"""
        r = self.rules
        by_id = {f["id"]: f for f in facts}
        out: list[dict[str, Any]] = []
        notes: list[str] = []
        for p in proposals:
            if len(out) >= r.max_views:
                break
            text = normalize(p.get("text", ""))
            body = text[len(PREFIX) :].strip("，, ")
            cited = []
            for x in p.get("facts") or []:
                try:
                    cited.append(int(x))
                except (TypeError, ValueError):
                    continue
            cited = [i for i in dict.fromkeys(cited) if i in by_id]
            if len(body) < 2:
                notes.append("有一句是空的")
            elif len(text) > r.view_chars:
                notes.append(f"「{text[:12]}…」超过 {r.view_chars} 字")
            elif not cited:
                notes.append(f"「{text[:12]}…」没写从画像哪几条来")
            elif self.about.blocked(text):
                notes.append(f"「{text[:12]}…」猜了不该猜的（{self.about.blocked(text)}）")
            elif any(
                similarity(body, by_id[i]["text"]) >= r.rehash_similarity for i in cited
            ) or any(body.strip("。") in by_id[i]["text"] for i in cited):
                notes.append(f"「{text[:12]}…」只是把事实又说了一遍")
            elif any(similarity(body, v["text"]) >= r.rehash_similarity for v in out):
                notes.append(f"「{text[:12]}…」和前一句差不多")
            else:
                out.append(
                    {
                        "text": text,
                        "facts": cited,
                        "agree": p.get("agree") is not False,
                    }
                )
        return out, notes
