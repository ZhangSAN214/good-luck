"""模拟场景（``roundtable sim --scenario 名字``）：开跑前把临时数据库摆成某个样子。

场景写在 ``benchmarks/members/sim.yaml`` 的 ``scenarios`` 里（不是提示词），比如「认识一个月、
今天你过生日」。只动模拟自己的临时数据库，不碰真实数据；全程序、不调模型。

一个场景可以有：

- ``known_days``：已经认识几天。按真实的关系规则（``RelationPlugin.add``）把这些天里的来往补上：
  每位和你熟的程度不一样（有的天天聊，有的隔几天聊一次），同事之间也一起住了这么久。
- ``birthday``：模拟第一天是你的哪个日子（如「他的生日」），加进日历里你的纪念日，
  成员早上会从日历看到「今天是他的生日」；``birthday_fact`` 再在「关于你」里记一句你说过的生日。
- ``facts``：「关于你」里你亲口说过的事（这一个月里聊出来的）。
- ``user_mix`` / ``user`` / ``probes`` / ``exam``：替换模拟用户说的话、晚上问的话和考题
  （``user`` 是在原来的话上加 / 换几类）。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

USER = "user"
OVERRIDE = ("user_mix", "probes", "exam")


def material_with(material: dict[str, Any], scene: dict[str, Any]) -> dict[str, Any]:
    """场景替换后的素材：user_mix、probes、exam 整个换，user 按类别加 / 换。"""
    out = dict(material)
    for key in OVERRIDE:
        if key in scene:
            out[key] = scene[key]
    if "user" in scene:
        out["user"] = {**material.get("user", {}), **scene["user"]}
    return out


def _times(day: datetime, n: int, rng: random.Random) -> list[datetime]:
    """那天里 n 个聊天的时刻（9 点到 23 点，排好序）。"""
    minutes = sorted(rng.randint(9 * 60, 23 * 60 - 1) for _ in range(n))
    return [day.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0) for m in minutes]


def known(svc: Any, days: int, start: datetime, rng: random.Random) -> dict[str, int]:
    """补上认识的这些天：和你的来往、同事之间的来往。返回每位和你来回了几次。"""
    relation = svc.engine.relation
    if days <= 0 or not relation.enabled():
        return {}
    w = relation.rules.weights
    ids = [m.id for m in svc.chats.everyone()]
    warmth = {m: rng.uniform(0.45, 0.95) for m in ids}  # 每位和你聊得多不多
    for back in range(days, 0, -1):
        day = start - timedelta(days=back)
        for m in ids:
            if back == days or rng.random() < warmth[m]:  # 同一天认识的
                for at in _times(day, rng.randint(2, 7), rng):
                    dm = rng.random() < 0.6
                    relation.add(m, USER, at, w.dm if dm else w.group, "dm" if dm else "group")
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                if rng.random() < 0.35:
                    for at in _times(day, rng.randint(1, 3), rng):
                        relation.add(a, b, at, w.chat, "chat")
                        relation.add(b, a, at, w.chat, "chat")
    return {m: int((relation.data(m)["user"] or {}).get("n", 0)) for m in ids}


def prepare(svc: Any, scene: dict[str, Any], start: datetime, seed: int) -> dict[str, Any]:
    """开跑前摆好场景，返回摆了什么（写进报告的 meta）。"""
    rng = random.Random(f"scene:{seed}")
    done: dict[str, Any] = {}
    if scene.get("known_days"):
        done["known_days"] = int(scene["known_days"])
        done["exchanges"] = known(svc, done["known_days"], start, rng)
    if scene.get("birthday"):
        svc.calendar.add_user_date(str(scene["birthday"]), start.strftime("%m-%d"))
        done["birthday"] = f"{scene['birthday']}（{start:%m-%d}）"
        if scene.get("birthday_fact"):  # 你一个月里说过自己哪天生日
            fact = str(scene["birthday_fact"]).format(month=start.month, day=start.day)
            svc.engine.about.add(fact, "其他")
    facts = scene.get("facts") or []
    for f in facts:
        text, domain = (f, None) if isinstance(f, str) else (f["text"], f.get("domain"))
        svc.engine.about.add(text, domain)
    if facts:
        done["facts"] = len(facts)
    return done
