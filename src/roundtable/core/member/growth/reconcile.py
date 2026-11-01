"""对账（成长 G2）：各模块各写各的，档案和她实际的样子会慢慢对不上——口头禅说腻了老歇着、
档案里还写着；跟人学的说法早成了她常说的话、档案里没有；天天去天台、档案的「最爱」里没有。

这里只**读**（单写者规则不变：档案只有 P6 成长能以成长的身份写），把对不上的地方写成几句
「对账」经历，放进那一周的反思材料（``week.py``，带编号，可以引用）；改不改由她每周反思时
自己决定，照常过程序把关和评审。全程序、不调模型；8 位同一套规则。只用于生活模式。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from roundtable.core.member.growth import habits as hb
from roundtable.core.member.growth.timeline import GrowthLog
from roundtable.core.member.profile import ProfileStore

REST_TIMES = 2  # 同一句口头禅最近 90 天歇过这么多次，算「不怎么说了」
REST_DAYS = 90
MAX_LINES = 4


def _clean(p: Any) -> str:
    return str(p).strip("。.！!？?～~ ")


def notes(rt: Any, member: str, start: date, end: date) -> list[str]:
    """[start, end) 这一周写进反思材料的对账句（最多 MAX_LINES 句，不提到你）。"""
    from roundtable.core.member.drift import Drift

    data = ProfileStore(rt).current(member).data
    style = data.get("style") or {}
    phrases = [_clean(p) for p in style.get("口头禅") or [] if _clean(p)]
    likes = [str(x) for x in (data.get("likes") or {}).get("最爱") or []]
    drift = Drift(rt)
    out: list[str] = []
    swaps = drift.swaps(member)
    since = datetime.combine(end - timedelta(days=REST_DAYS), datetime.min.time()).isoformat()
    for p in phrases:
        rests = [s for s in swaps if s.get("kind") == "rest" and s.get("old") == p]
        rests = [s for s in rests if s.get("at", "") >= since and not s.get("undone")]
        if len(rests) >= REST_TIMES:
            out.append(
                f"对账：「{p}」最近说腻了 {len(rests)} 回，这阵子都不怎么说了，档案里还写着是口头禅"
            )
    for word, info in drift.promoted(member).items():
        if word not in phrases:
            src = (info or {}).get("from") or ""
            src = "用户" if src in ("user", "用户") else src  # 跟你学的算「受你影响」
            who = f"跟{src}学的" if src else "学来的"
            out.append(f"对账：{who}「{word}」已经成了常说的话，档案的口头禅里还没有")
    for h in hb.habits(GrowthLog(rt).load(member), rt.config.roundtable.chat.engine.growth.habits):
        name = h["label"]
        if any(x in name or name in x for x in likes):
            continue
        out.append(f"对账：已经养成了「{name}」（{h['n']} 次、跨 {h['weeks']} 周），最爱里没写")
    return out[:MAX_LINES]
