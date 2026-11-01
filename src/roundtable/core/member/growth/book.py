"""成长总账（docs/MEMBER_ENGINE.md「成长总账」；成长 G1）：她这阵子到底变了什么，排成一条时间线。

成员会变的东西散在好几个模块里，各写各的数据（单写者规则不变）：

- **设定**：每周反思改的性格分、长处、小毛病、口头禅、喜好、情绪理解（P6 成长时间线）；
- **口癖**：用腻了换掉的颜文字和短回复、歇着的口头禅、学来转正的说法（``drift``）；
- **和你**：和你的阶段升了（P4 关系）；
- **看法**：每周对你的见解保留 / 加深 / 改观 / 放下（P4 见解记录）；
- **收藏**：偷偷存下的表情包（``stash``）。

本模块**只读**这些数据，统一成 ``{at, source, text, why, undo}``，新的在前；月报按来源数一数，
「这阵子」挑最近的一两件写成她自己知道的一行（程序拼，不调模型）。

**撤回**也交回给原来那个模块做（``undo`` 字段是给那个模块的凭据）：设定 → 成长时间线撤回，
口癖 → ``Drift.undo``，收藏 → ``Stash.delete``。和你的阶段只升不降、看法每周自己会想，不给撤回。
只用于生活模式；工作模式不挂载。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.member.growth.gate import ADD, SCORE
from roundtable.core.member.growth.timeline import CHANGE, REVERT
from roundtable.core.member.mode import Mode, require_life
from roundtable.core.storage.repository import iso

PROFILE, LINGO, BOND, VIEW, STASH = "profile", "lingo", "bond", "view", "stash"
SOURCES = {PROFILE: "设定", LINGO: "口癖", BOND: "和你", VIEW: "看法", STASH: "收藏"}
SECTION = {
    "personality": "性格",
    "values": "三观",
    "style": "说话",
    "likes": "喜好",
    "feel": "情绪",
}


class BookError(ValueError):
    """撤回的凭据不认识或已经撤回过。"""


def where(path: str) -> str:
    """「personality.五项.好奇」→「性格分·好奇」；「likes.最爱」→「喜好·最爱」。"""
    sec, _, rest = str(path or "").partition(".")
    key, _, trait = rest.partition(".")
    if key == "五项":
        return f"性格分·{trait}"
    return f"{SECTION.get(sec, sec)}·{key}" if key else SECTION.get(sec, sec)


def profile_text(e: dict[str, Any]) -> str:
    if e["kind"] == REVERT:
        return f"你撤回了「{where(e.get('path', ''))}」的那次改动"
    if e.get("op") == SCORE:
        return f"{where(e['path'])} {e.get('old')} → {e.get('new')}"
    verb = "加上" if e.get("op") == ADD else "去掉"
    return f"{where(e['path'])} {verb}「{e.get('value')}」"


def lingo_text(s: dict[str, Any]) -> str:
    kind, old, new = s.get("kind"), s.get("old", ""), s.get("new", "")
    if kind == "rest":
        return f"口头禅「{old}」先歇一阵"
    if kind == "promote":
        return f"学来的「{old}」成了自己的口头禅"
    what = "颜文字" if kind == "kaomoji" else "短回复"
    return f"{what}「{old}」换成了「{new}」"


class GrowthBook:
    """``log``：成长时间线（GrowthLog）；``drift`` / ``relation`` / ``stash``：各模块（可空）。"""

    def __init__(
        self,
        rt: Any,
        log: Any,
        *,
        drift: Any | None = None,
        relation: Any | None = None,
        stash: Any | None = None,
    ) -> None:
        self.rt = rt
        self.log = log
        self.drift = drift
        self.relation = relation
        self.stash = stash

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.growth.book

    # ---- 汇总 ----

    def changes(self, member: str, since: str | None = None) -> list[dict[str, Any]]:
        """全部变化，新的在前；since（库里的时间串）以后才算。"""
        out = self._profile(member) + self._lingo(member) + self._bond(member)
        out += self._views(member) + self._stash(member)
        if since:
            out = [c for c in out if c["at"] >= since]
        out.sort(key=lambda c: (c["at"], c["source"], c["text"]), reverse=True)
        return out[: self.rules.keep]

    def _profile(self, member: str) -> list[dict[str, Any]]:
        out = []
        for e in self.log.entries(member):
            if e["kind"] not in (CHANGE, REVERT):
                continue
            undone = bool(e.get("reverted"))
            out.append(
                {
                    "at": e["at"],
                    "source": PROFILE,
                    "text": profile_text(e),
                    "why": e.get("why") or "",
                    "undone": undone,
                    "undo": f"profile:{e['id']}" if e["kind"] == CHANGE and not undone else None,
                }
            )
        return out

    def _lingo(self, member: str) -> list[dict[str, Any]]:
        if self.drift is None:
            return []
        out = []
        for s in self.drift.swaps(member):
            if not s.get("at") or s.get("kind") not in ("kaomoji", "short", "rest", "promote"):
                continue
            undone = bool(s.get("undone"))
            token = f"lingo:{s['at']}|{s['kind']}|{s.get('old', '')}"
            out.append(
                {
                    "at": s["at"],
                    "source": LINGO,
                    "text": lingo_text(s),
                    "why": s.get("why") or "",
                    "undone": undone,
                    "undo": None if undone else token,
                }
            )
        return out

    def _bond(self, member: str) -> list[dict[str, Any]]:
        if self.relation is None:
            return []
        out = []
        for st in self.relation.data(member).get("stages") or []:
            if st.get("at") and st.get("stage"):
                out.append(
                    {
                        "at": st["at"],
                        "source": BOND,
                        "text": f"和你成了「{st['stage']}」",
                        "why": "一起经历的事攒够了",
                        "undone": False,
                        "undo": None,
                    }
                )
        return out

    def _views(self, member: str) -> list[dict[str, Any]]:
        if self.relation is None:
            return []
        out = []
        for row in self.relation.data(member).get("views_log") or []:
            for text in (
                row.get("changes") or []
            ):  # 「改观：… → …」「加深：…」「新想到：…」「放下了：…」
                out.append(
                    {
                        "at": row.get("at") or "",
                        "source": VIEW,
                        "text": f"对你的看法·{text}",
                        "why": row.get("note") or "每周想一想",
                        "undone": False,
                        "undo": None,
                    }
                )
        return [c for c in out if c["at"]]

    def _stash(self, member: str) -> list[dict[str, Any]]:
        if self.stash is None:
            return []
        return [
            {
                "at": r["at"],
                "source": STASH,
                "text": f"偷偷存下表情包「{r.get('label') or '一张图'}」",
                "why": "觉得好玩",
                "undone": False,
                "undo": f"stash:{r['n']}",
            }
            for r in self.stash.items(member)
            if r.get("at")
        ]

    # ---- 月报、这阵子 ----

    def month_counts(self, member: str, month: str) -> dict[str, int]:
        """month（YYYY-MM，按库里的 UTC 时间串）里各来源的变化条数。
        撤回的不算；设定在月报里另有细账，这里不重复。"""
        got = Counter(
            c["source"]
            for c in self.changes(member)
            if c["at"][:7] == month and not c["undone"] and c["source"] != PROFILE
        )
        return {SOURCES[k]: v for k, v in sorted(got.items())}

    def recent_line(self, member: str, now: datetime, mode: Mode | str = Mode.LIFE) -> str:
        """给她自己的一行：「这阵子：……；……」（最近几天、最多几件、限字数）。没有时为空串。"""
        require_life(mode)
        r = self.rules
        if not r.enabled or r.recent_max == 0 or r.context_chars == 0:
            return ""
        since = iso(now - timedelta(days=r.recent_days))
        picks = [
            c
            for c in self.changes(member, since)
            if not c["undone"] and c["source"] != VIEW  # 对你的看法另有一行
        ][: r.recent_max]
        if not picks:
            return ""
        line = "这阵子的你：" + "；".join(c["text"] for c in picks)
        return line if len(line) <= r.context_chars else line[: r.context_chars - 1] + "…"

    # ---- 撤回（交回原来的模块） ----

    def undo(self, member: str, token: str) -> None:
        kind, _, rest = str(token or "").partition(":")
        if kind == PROFILE and rest.isdigit():
            self.log.revert(member, int(rest))
            return
        if kind == LINGO and self.drift is not None:
            at, _, tail = rest.partition("|")
            what, _, old = tail.partition("|")
            if self.drift.undo(member, at, what, old):
                return
        if (
            kind == STASH
            and self.stash is not None
            and rest.isdigit()
            and self.stash.delete(member, int(rest))
        ):
            return
        raise BookError("这条变化不能撤回，或者已经撤回过了")


def for_rt(rt: Any) -> GrowthBook:
    """按运行时装一本总账（各模块都用现成的类，只读）。"""
    from roundtable.core.member.drift import Drift
    from roundtable.core.member.growth.timeline import GrowthLog
    from roundtable.core.member.relation import RelationPlugin
    from roundtable.core.member.stash import Stash

    return GrowthBook(
        rt, GrowthLog(rt), drift=Drift(rt), relation=RelationPlugin(rt), stash=Stash(rt)
    )


def recent_text(rt: Any, member: str) -> str:
    """私聊、群聊提示词里「这阵子的你」那一行（引擎或成长关着时为空串）。"""
    engine = rt.config.roundtable.chat.engine
    if not (engine.enabled and engine.growth.enabled and engine.growth.book.enabled):
        return ""
    return for_rt(rt).recent_line(member, rt.budget.now())
