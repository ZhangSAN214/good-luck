"""成长的程序把关（P6）：成员提的每条改动先过这里，过不了的不写进档案，并记下原因。

依次检查（所有成员同一套规则，与是哪个模型无关）：

1. **只能这样改**：``score`` 五项性格分的一项 ±1（结果仍在 1～5）；``add`` / ``remove``
   在 ``growth.lists`` 列出的列表里加一项 / 去掉一项（新的一项不超过 ``item_chars`` 字，
   列表不超过 ``list_max`` 项）。整段原文（人物卡各章）不让成长改。
2. **用户锁定项不动**：锁住的 ``分区.键`` 一律拦下；分区权限再经 ``ProfileStore.check``
   （成长只能写 personality / values / style / likes）。
3. **必须引用真实经历**：``evidence`` 是这一周经历的编号（至少一条），``quote`` 的两字片段
   至少 ``evidence_overlap`` 出现在所引的经历里（照抄为 1）。
4. **核心性格每月最多一格**：这个月（本地日历月）已经改过的格数（没撤回的）加上这次，
   不超过 ``core_monthly_steps``。
5. **8 人不撞型**：改完后和任何一位别的成员的五项分差（各项差的和）小于 ``min_distance``、
   并且比改之前更近，就拦下；``unique_lists`` 里的列表（口头禅）不和别人重样。
6. **受用户影响不超过三成**：引用了受用户影响的经历的改动算「受用户影响」；最近
   ``share_window_weeks`` 周没撤回的改动里，这类占比超过 ``user_share``（改动不到
   ``share_floor`` 条时按 ``share_floor`` 条算）就拦下。

几条改动按顺序叠在同一份档案副本上检查，所以同一周的改动互相也算数。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.member.growth.week import Week
from roundtable.core.member.profile import ProfileError, ProfileStore
from roundtable.core.recall import grams

SCORE, ADD, REMOVE = "score", "add", "remove"
CORE = "personality.五项"
TRAITS_PATH = ("personality", "五项")
MIN, MAX = 1, 5


@dataclass
class Proposal:
    op: str
    path: str
    value: str = ""
    delta: int = 0
    evidence: list[int] = field(default_factory=list)
    quote: str = ""
    why: str = ""

    def label(self) -> str:
        if self.op == SCORE:
            return f"{self.path} {'+' if self.delta > 0 else ''}{self.delta}"
        return f"{self.path} {'加上' if self.op == ADD else '去掉'}「{self.value}」"


@dataclass
class Verdict:
    proposal: Proposal
    ok: bool
    reason: str = ""
    patch: dict[str, dict[str, Any]] = field(default_factory=dict)
    old: Any = None
    new: Any = None
    user: bool = False  # 受用户影响
    evidence: list[str] = field(default_factory=list)  # 所引经历的原文
    score: int | None = None  # 评审打分
    clash: str = ""  # 撞型的那位成员（拦下时）


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def parse(text: str, limit: int) -> tuple[list[Proposal] | None, str]:
    """解析成员的提议；不是 JSON 时返回 (None, "")。最多取 limit 条。"""
    try:
        data = extract_json_object(text or "")
    except JSONOutputError:
        return None, ""
    out = []
    for c in data.get("changes") or []:
        if not isinstance(c, dict):
            continue
        ev = c.get("evidence")
        ev = ev if isinstance(ev, list) else [ev] if ev is not None else []
        out.append(
            Proposal(
                op=str(c.get("op") or "").strip().lower(),
                path=str(c.get("path") or "").strip(),
                value=str(c.get("value") or "").strip(),
                delta=_int(c.get("delta")),
                evidence=[n for n in (_int(x, -1) for x in ev) if n > 0],
                quote=str(c.get("quote") or "").strip(),
                why=str(c.get("why") or "").strip()[:80],
            )
        )
    return out[:limit], str(data.get("skip") or "").strip()[:120]


def overlap(quote: str, text: str) -> float:
    """quote 的两字片段有多少出现在 text 里（0～1；照抄为 1）。"""
    q = grams(quote, (2,))
    if not q:
        return 1.0 if quote.strip() and quote.strip() in text else 0.0
    return len(q & grams(text, (2,))) / len(q)


def distance(a: dict[str, Any], b: dict[str, Any]) -> int | None:
    """两份五项分的差（共有各项差的和）；没有共同项时为 None。"""
    keys = [k for k in a if k in b]
    if not keys:
        return None
    return sum(abs(_int(a[k]) - _int(b[k])) for k in keys)


def fresh_patch(data: dict[str, Any], p: Proposal) -> dict[str, dict[str, Any]]:
    """按此刻的档案重新算一条（已通过把关的）改动：同一周前面那条被评审拦下时，
    后面这条不会把它也带进去。改不了（项已不在、分数越界）时为空。"""
    sec, _, rest = p.path.partition(".")
    key, _, trait = rest.partition(".")
    if p.op == SCORE:
        scores = dict((data.get("personality") or {}).get("五项") or {})
        if trait not in scores or not MIN <= _int(scores[trait]) + p.delta <= MAX:
            return {}
        scores[trait] = _int(scores[trait]) + p.delta
        return {"personality": {"五项": scores}}
    items = (data.get(sec) or {}).get(key)
    items = list(items) if isinstance(items, list) else []
    if p.op == ADD and p.value not in items:
        return {sec: {key: [*items, p.value]}}
    if p.op == REMOVE and p.value in items:
        return {sec: {key: [x for x in items if x != p.value]}}
    return {}


@dataclass
class Ledger:
    """把关要用的历史：这个月已改的格数、最近几周的改动数和其中受用户影响的条数。"""

    core_steps: int = 0
    total: int = 0
    user: int = 0


class Gate:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self.profiles = ProfileStore(rt)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.growth

    def check(
        self,
        member: str,
        proposals: list[Proposal],
        week: Week,
        ledger: Ledger,
        others: dict[str, dict[str, Any]],
    ) -> list[Verdict]:
        """others：别的成员 → 当前档案数据（撞型检查用）。ledger 会被这次通过的改动更新。"""
        profile = self.profiles.current(member)
        data = copy.deepcopy(profile.data)
        out = []
        for p in proposals[: self.rules.max_changes]:
            v = self._one(member, p, data, profile.locked, week, ledger, others)
            if v.ok:
                for sec, items in v.patch.items():
                    data.setdefault(sec, {}).update(items)
                ledger.total += 1
                ledger.user += int(v.user)
                ledger.core_steps += int(p.op == SCORE)
            out.append(v)
        return out

    def _one(
        self,
        member: str,
        p: Proposal,
        data: dict[str, Any],
        locked: list[str],
        week: Week,
        ledger: Ledger,
        others: dict[str, dict[str, Any]],
    ) -> Verdict:
        r = self.rules
        v = Verdict(p, ok=False)
        cited = [week.line(n) for n in p.evidence]
        v.evidence = [ln.text for ln in cited if ln is not None]
        v.user = any(ln.user for ln in cited if ln is not None)
        sec, _, key = p.path.partition(".")
        key, _, trait = key.partition(".")
        # 1. 只能这样改
        if p.op == SCORE:
            scores = dict((data.get("personality") or {}).get("五项") or {})
            if (sec, key) != TRAITS_PATH or trait not in scores:
                return self._no(v, f"五项性格分里没有「{trait or p.path}」")
            if p.delta not in (1, -1):
                return self._no(v, "性格分一次只能加 1 或减 1")
            old = _int(scores[trait])
            new = old + p.delta
            if not MIN <= new <= MAX:
                return self._no(
                    v, f"「{trait}」已经是 {old} 分，不能再{'高' if p.delta > 0 else '低'}"
                )
            scores[trait] = new
            v.old, v.new = old, new
            v.patch = {"personality": {"五项": scores}}
            locked_path = CORE
        elif p.op in (ADD, REMOVE):
            if trait or key not in r.lists.get(sec, []):
                return self._no(v, f"「{p.path}」不是成长能改的列表")
            old_list = (data.get(sec) or {}).get(key)
            items = list(old_list) if isinstance(old_list, list) else []
            if not p.value:
                return self._no(v, "没写要加 / 去掉的是哪一项")
            if p.op == ADD:
                if len(p.value) > r.item_chars:
                    return self._no(v, f"新的一项超过 {r.item_chars} 字")
                if p.value in items:
                    return self._no(v, f"「{p.value}」已经在里面了")
                if len(items) >= r.list_max:
                    return self._no(v, f"这个列表已经有 {r.list_max} 项了，先去掉一项")
                new_list = [*items, p.value]
            else:
                if p.value not in items:
                    return self._no(v, f"列表里没有「{p.value}」")
                new_list = [x for x in items if x != p.value]
            v.old, v.new = items, new_list
            v.patch = {sec: {key: new_list}}
            locked_path = f"{sec}.{key}"
        else:
            return self._no(v, f"不认识的改法「{p.op}」（只能 score / add / remove）")
        # 2. 用户锁定项不动、分区权限
        if locked_path in locked:
            return self._no(v, f"「{locked_path}」被你锁住了")
        trial = copy.deepcopy(data)
        for s, items in v.patch.items():
            trial.setdefault(s, {}).update(items)
        try:
            self.profiles.check(self.profiles.current(member), trial, "growth")
        except ProfileError as e:
            return self._no(v, str(e))
        # 3. 必须引用真实经历
        if not p.evidence or len(v.evidence) != len(p.evidence):
            return self._no(v, "没引用这一周的经历（编号不对）")
        if not p.quote or max(overlap(p.quote, t) for t in v.evidence) < r.evidence_overlap:
            return self._no(v, "引用的原话在所引的经历里找不到")
        # 4. 核心性格每月最多一格
        if p.op == SCORE and ledger.core_steps + 1 > r.core_monthly_steps:
            return self._no(v, f"性格分这个月已经改过 {ledger.core_steps} 格了")
        # 5. 8 人不撞型
        if p.op == SCORE:
            before = dict((data.get("personality") or {}).get("五项") or {})
            after = v.patch["personality"]["五项"]
            for other, odata in sorted(others.items()):
                theirs = (odata.get("personality") or {}).get("五项") or {}
                d0, d1 = distance(before, theirs), distance(after, theirs)
                if d1 is not None and d0 is not None and d1 < r.min_distance and d1 < d0:
                    v.clash = other
                    return self._no(v, "改完会和另一位成员的性格太像")
        elif p.op == ADD and f"{sec}.{key}" in r.unique_lists:
            for other, odata in sorted(others.items()):
                theirs = (odata.get(sec) or {}).get(key)
                if isinstance(theirs, list) and p.value in theirs:
                    v.clash = other
                    return self._no(v, "和另一位成员的重样了")
        # 6. 受用户影响不超过三成
        if v.user:
            total = max(ledger.total + 1, r.share_floor)
            if ledger.user + 1 > r.user_share * total + 1e-9:
                return self._no(v, f"最近受你影响的改动已经到上限（{int(r.user_share * 100)}%）")
        v.ok = True
        return v

    @staticmethod
    def _no(v: Verdict, reason: str) -> Verdict:
        v.ok, v.reason, v.patch = False, reason, {}
        return v
