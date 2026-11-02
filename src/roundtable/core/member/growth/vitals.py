"""成长的防退化指标（成长 G3，docs/MEMBER_ENGINE.md「半年快进与防退化」）：
成员一周周地变，半年后会不会出问题——8 人越长越像、性格被一个方向推到头、被你带偏、
口头禅说腻了一个不剩、同一件事记了一遍又一遍。这里只读数据算数，不写任何东西、不调模型。

- ``spread``：8 人五项分两两的差（各项差的和），最小的那一对和平均。
- ``overlap``：成长能改的列表（长处、小毛病、最看重、最爱、讨厌）两两重合（交集 / 并集），
  最高的那一对。口头禅另有「不和别人重样」，不算在这里。
- ``extreme``：五项分在 1 或 5 的占比，期初 → 期末。
- ``pushed``：每一项性格分全员平均移动了多少（期末 − 期初），看是不是被一个方向推。
- ``moved``：每位成员五项分一共移动了几格。
- ``user_share``：没撤回的成长改动里受你影响的占比（整段时间，每位成员各算）。
- ``lingo_left``：每位成员现在能用的口头禅占期初几成（歇着的不算），颜文字还有几个。
- ``memory_dupes``：每位成员现在的记忆件里，和更早一件像到本该合并（P2 的 ``merge_similarity``）
  却各记一条的占比（夜间整理的合并坏了会越记越多）。

``problems()`` 按 ``chat.engine.growth.vitals`` 的阈值挑出超标的项（大白话）；半年快进
（``core/simlong.py``）和加速模拟（``core/simlab.py``）都用它，超标测试失败。只用于生活模式。
"""

from __future__ import annotations

import itertools
import statistics
from typing import Any

from roundtable.core.member.card import TRAITS
from roundtable.core.member.growth.gate import SCORE, distance
from roundtable.core.member.growth.timeline import CHANGE, GrowthLog
from roundtable.core.member.profile import ProfileStore

LOW, HIGH = 1, 5


def _traits(data: dict[str, Any]) -> dict[str, int]:
    raw = (data.get("personality") or {}).get("五项") or {}
    out = {}
    for k, v in raw.items():
        try:
            out[k] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def _items(data: dict[str, Any], lists: dict[str, list[str]], skip: set[str]) -> set[str]:
    out: set[str] = set()
    for sec, keys in lists.items():
        for key in keys:
            if f"{sec}.{key}" in skip:
                continue
            v = (data.get(sec) or {}).get(key)
            if isinstance(v, list):
                out |= {str(x).strip() for x in v if str(x).strip()}
    return out


def _share_at_bounds(profiles: dict[str, dict[str, Any]]) -> float:
    values = [v for d in profiles.values() for v in _traits(d).values()]
    return round(sum(v in (LOW, HIGH) for v in values) / len(values), 3) if values else 0.0


def spread(profiles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pairs = []
    for a, b in itertools.combinations(sorted(profiles), 2):
        d = distance(_traits(profiles[a]), _traits(profiles[b]))
        if d is not None:
            pairs.append((d, a, b))
    if not pairs:
        return {"min": None, "mean": None, "pair": []}
    low = min(pairs)
    return {
        "min": low[0],
        "mean": round(statistics.mean(p[0] for p in pairs), 2),
        "pair": [low[1], low[2]],
    }


def overlap(profiles: dict[str, dict[str, Any]], rules: Any) -> dict[str, Any]:
    skip = set(rules.unique_lists)
    sets = {m: _items(d, rules.lists, skip) for m, d in profiles.items()}
    best, pair, scores = 0.0, [], []
    for a, b in itertools.combinations(sorted(sets), 2):
        union = sets[a] | sets[b]
        if not union:
            continue
        j = len(sets[a] & sets[b]) / len(union)
        scores.append(j)
        if j > best:
            best, pair = j, [a, b]
    return {
        "max": round(best, 3),
        "mean": round(statistics.mean(scores), 3) if scores else 0.0,
        "pair": pair,
    }


def pushed(base: dict[str, dict[str, Any]], now: dict[str, dict[str, Any]]) -> dict[str, float]:
    """每一项性格分全员平均移动多少（期末 − 期初；只算两头都有的成员）。"""
    out = {}
    keys = [k for k in TRAITS] + sorted(
        {k for d in base.values() for k in _traits(d)} - set(TRAITS)
    )
    for k in keys:
        moves = [
            _traits(now[m])[k] - _traits(base[m])[k]
            for m in base
            if m in now and k in _traits(base[m]) and k in _traits(now[m])
        ]
        if moves:
            out[k] = round(statistics.mean(moves), 2)
    return out


def moved(base: dict[str, dict[str, Any]], now: dict[str, dict[str, Any]]) -> dict[str, int]:
    out = {}
    for m in base:
        a, b = _traits(base[m]), _traits(now.get(m, {}))
        out[m] = sum(abs(b[k] - a[k]) for k in a if k in b)
    return out


def user_share(rt: Any, members: list[str]) -> dict[str, Any]:
    """没撤回的成长改动里受你影响的占比：全员合计，以及每位成员各自的（规则按每位成员算）。"""
    log = GrowthLog(rt)
    total = user = score = 0
    each: dict[str, dict[str, int]] = {}
    for m in members:
        mine = {"changes": 0, "user": 0}
        for e in log.entries(m):
            if e.get("kind") != CHANGE or e.get("reverted"):
                continue
            mine["changes"] += 1
            mine["user"] += int(bool(e.get("user")))
            score += int(e.get("op") == SCORE)
        each[m] = mine
        total += mine["changes"]
        user += mine["user"]
    return {
        "changes": total,
        "user": user,
        "score": score,
        "share": round(user / total, 3) if total else 0.0,
        "members": each,
    }


def lingo_left(rt: Any, members: list[str], base: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """口头禅现在还能用几句，占期初几句的比例（歇着的不算；成长换成新的也算，最多算满），
    颜文字还有几个。"""
    from roundtable.core.member.drift import Drift
    from roundtable.core.member.lingo import Lingo

    drift, lingo = Drift(rt), Lingo(rt)
    now = rt.budget.now()
    out: dict[str, Any] = {}
    for m in members:
        start = [
            str(p).strip()
            for p in ((base.get(m) or {}).get("style") or {}).get("口头禅") or []
            if str(p).strip()
        ]
        current = (ProfileStore(rt).current(m).data.get("style") or {}).get("口头禅") or []
        usable = drift.catchphrases(m, [str(p).strip() for p in current], now)
        left = min(len(start), len(usable))
        kao = len(lingo.habits(m).kaomoji) if lingo.styled(m) else None
        out[m] = {
            "catchphrases": round(left / len(start), 2) if start else None,
            "kaomoji": kao,
        }
    return out


def memory_dupes(rt: Any, members: list[str]) -> dict[str, float]:
    """现在的记忆件里，和更早一件像到本该合并（P2 的 ``merge_similarity``）却各记一条的占比。
    正常应接近 0；高了说明夜间整理的合并坏了，同一件事会越记越多、把记忆挤满。"""
    from roundtable.core.member.memory.store import similarity

    same = rt.config.roundtable.chat.memory.merge_similarity
    out = {}
    for m in members:
        items = sorted(rt.repo.memory_items(m, current=True, limit=400), key=lambda r: r["id"])
        seen: list[str] = []
        dupes = 0
        for it in items:
            dupes += int(any(similarity(it["text"], t) >= same for t in seen))
            seen.append(it["text"])
        out[m] = round(dupes / len(items), 3) if items else 0.0
    return out


def vitals(rt: Any, members: list[str], base: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """期末的全部指标。base：期初每位成员的档案数据（快进开始时记下的）。"""
    growth = rt.config.roundtable.chat.engine.growth
    store = ProfileStore(rt)
    now = {m: store.current(m).data for m in members}
    return {
        "spread": spread(now),
        "spread_start": spread({m: base.get(m, {}) for m in members}),
        "overlap": overlap(now, growth),
        "overlap_start": overlap({m: base.get(m, {}) for m in members}, growth),
        "extreme": {
            "start": _share_at_bounds({m: base.get(m, {}) for m in members}),
            "end": _share_at_bounds(now),
        },
        "pushed": pushed(base, now),
        "moved": moved(base, now),
        "user_share": user_share(rt, members),
        "lingo_left": lingo_left(rt, members, base),
        "memory_dupes": memory_dupes(rt, members),
    }


def problems(v: dict[str, Any], rt: Any, names: dict[str, str] | None = None) -> list[str]:
    """超过阈值的项（大白话，一项一句）。没有超标返回空列表。"""
    growth = rt.config.roundtable.chat.engine.growth
    r = growth.vitals
    name = (names or {}).get
    out = []
    low = v["spread"]["min"]
    start = v["spread_start"]["min"]
    floor = growth.min_distance if start is None else min(growth.min_distance, start)
    if low is not None and low < floor:
        a, b = v["spread"]["pair"]
        out.append(f"性格撞型：{name(a, a)} 和 {name(b, b)} 的五项分只差 {low}（至少 {floor}）")
    gain = v["overlap"]["max"] - v["overlap_start"]["max"]
    if v["overlap"]["max"] > r.overlap_max and gain > 0:
        a, b = v["overlap"]["pair"]
        out.append(
            f"越长越像：{name(a, a)} 和 {name(b, b)} 的长处 / 喜好重合 "
            f"{v['overlap']['max']:.0%}（上限 {r.overlap_max:.0%}）"
        )
    ex = v["extreme"]
    if ex["end"] - ex["start"] > r.extreme_gain:
        out.append(
            f"性格走极端：五项分在 1 或 5 的从 {ex['start']:.0%} 变成 {ex['end']:.0%}"
            f"（最多多 {r.extreme_gain:.0%}）"
        )
    for trait, mean in v["pushed"].items():
        if abs(mean) > r.push_max:
            out.append(
                f"被一个方向推：全员「{trait}」平均变了 {mean:+.2f} 格（最多 ±{r.push_max}）"
            )
    for m, n in v["moved"].items():
        if n > r.moved_max:
            out.append(f"变太多：{name(m, m)} 五项分一共移动了 {n} 格（最多 {r.moved_max}）")
    cap = growth.user_share + r.share_slack
    for m, got in v["user_share"]["members"].items():
        n = got["changes"]
        if n and got["user"] > cap * max(n, growth.share_floor) + 1e-9:
            out.append(
                f"被你带偏：{name(m, m)} 的 {n} 条成长改动里 {got['user']} 条受你影响"
                f"（上限 {growth.user_share:.0%}，不到 {growth.share_floor} 条时按 "
                f"{growth.share_floor} 条算）"
            )
    for m, got in v["lingo_left"].items():
        left = got["catchphrases"]
        if left is not None and left < r.catchphrase_left:
            out.append(
                f"口头禅快用光：{name(m, m)} 能用的口头禅只剩期初的 {left:.0%}"
                f"（至少 {r.catchphrase_left:.0%}）"
            )
        if got["kaomoji"] is not None and got["kaomoji"] < r.kaomoji_left:
            out.append(
                f"颜文字快用光：{name(m, m)} 只剩 {got['kaomoji']} 个（至少 {r.kaomoji_left}）"
            )
    for m, share in v["memory_dupes"].items():
        if share > r.dupes_max:
            out.append(
                f"同一件事记了好几遍：{name(m, m)} 的记忆件里 {share:.0%} 和更早的一件几乎一样"
                f"（最多 {r.dupes_max:.0%}）"
            )
    return out
