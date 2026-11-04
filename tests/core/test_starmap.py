"""星图（纯函数）：圆桌星图按部门挂更新、标缺口；人生星图十个星域、缺口、相关线、可复现。"""

from __future__ import annotations

from datetime import date

from roundtable.core.starmap import (
    GAP,
    LIFE_DOMAINS,
    MEMBER,
    PEER,
    RELATE,
    department_of,
    life_map,
    roundtable_map,
)

TODAY = date(2026, 11, 4)


def _check(m):
    ids = [n["id"] for n in m["nodes"]]
    assert len(ids) == len(set(ids))
    assert all(link["a"] in ids and link["b"] in ids for link in m["links"])
    assert m["center"] in ids
    assert all(g["node"] in ids for g in m["gaps"])
    assert all(c["node"] in ids for c in m["changes"])


def test_department_by_keywords():
    assert department_of("朋友圈配图更有摄影感") == "home"
    assert department_of("工作台资料：榜单有中文说明") == "bench"
    assert department_of("群聊里成员会记得你说过的话") == "chat"
    assert department_of("首页大立绘会眨眼") == "hub"
    assert department_of("没有任何关键词") == "work"


def _changelog():
    return {
        "latest": "3.2.0",
        "releases": [
            {
                "version": "3.2.0",
                "id": "#2",
                "level": "minor",
                "date": "2026-11-03",
                "title": "朋友圈配图",
                "added": ["生活照"],
                "fixed": [],
            },
            {
                "version": "3.1.0",
                "id": "#1",
                "level": "patch",
                "date": "2026-09-01",
                "title": "群聊表情",
                "added": [],
                "fixed": ["表情包"],
            },
        ],
    }


def test_roundtable_map():
    m = roundtable_map(
        changelog=_changelog(),
        members=[
            {"id": "a", "name": "甲", "score": 1.0, "missing": []},
            {"id": "b", "name": "乙", "score": 0.2, "missing": ["记忆", "关系"]},
        ],
        findings=[
            {"id": "db", "level": "ok", "title": "数据库正常"},
            {"id": "sandbox", "level": "warn", "title": "沙箱不可用", "fix": "装一下"},
        ],
        peers={"a": [{"member": "b", "closeness": 0.6}], "b": [{"member": "a", "closeness": 0.6}]},
        today=TODAY,
    )
    _check(m)
    by = {n["id"]: n for n in m["nodes"]}
    assert by["rel:#2"]["group"] == "home" and by["rel:#2"]["fresh"]
    assert by["rel:#1"]["group"] == "chat" and not by["rel:#1"]["fresh"]
    assert by["check:sandbox"]["kind"] == GAP and by["check:db"]["kind"] != GAP
    assert by["member:b"]["kind"] == MEMBER and by["member:b"]["missing"]
    assert sum(link["kind"] == PEER for link in m["links"]) == 1  # 双向只连一条
    gaps = {g["text"] for g in m["gaps"]}
    assert "沙箱不可用" in gaps and "乙：缺记忆、关系" in gaps
    assert any(g["text"] == "终端最近没有更新" for g in m["gaps"])  # 没轮到的部门也是缺口
    assert [c["t"] for c in m["changes"]] == ["2026-11-03", "2026-09-01"]


def test_empty_life_map_is_all_gaps():
    m = life_map(
        member={"id": "a", "name": "甲"},
        profile={"data": {}},
        growth={},
        relation={},
        memory={},
        state=None,
        today=TODAY,
    )
    _check(m)
    assert m["score"] == 0
    assert len(m["gaps"]) == len(LIFE_DOMAINS) == 10
    assert m["missing"] == [d[1] for d in LIFE_DOMAINS]
    assert all(g["fix"] for g in m["gaps"])


def _life(**over):
    kw = dict(
        member={"id": "a", "name": "甲"},
        profile={
            "data": {
                "identity": {"名字": "甲", "住处": "01 号房"},
                "personality": {
                    "五项": {"外向": 3, "好奇": 5},
                    "长处": ["认真"],
                    "小毛病": ["嘴硬"],
                },
                "values": {"最看重": ["尊重选择"], "底线要点": ["不拿隐私开玩笑"]},
                "likes": {"最爱": ["旧书店", "热茶"], "讨厌": ["起哄"]},
                "style": {"口头禅": ["让我想想。"], "自称": "我"},
            }
        },
        growth={
            "skills": [{"domain": "日常", "xp": 5, "level": 2, "name": "熟练", "next": 3}],
            "timeline": [
                {
                    "id": 1,
                    "kind": "change",
                    "week": "2026-W44",
                    "at": "2026-11-02T08:00:00",
                    "path": "personality.五项.外向",
                    "old": 3,
                    "new": 4,
                    "why": "多和人聊了",
                },
            ],
            "habits": [{"key": "walk", "label": "散步", "n": 4, "weeks": 2}],
        },
        relation={
            "user": {"stage": "熟悉", "closeness": 0.4, "n": 20, "days": 5},
            "peers": [{"member": "b", "name": "乙", "closeness": 0.5, "label": "亲近"}],
        },
        memory={
            "count": 2,
            "items": [
                {
                    "id": 7,
                    "text": "在旧书店泡了一下午",
                    "domain": "日常",
                    "importance": 2,
                    "day": "2026-11-03",
                    "last_at": "x",
                },
                {
                    "id": 8,
                    "text": "和乙去看海",
                    "domain": "社交",
                    "importance": 1,
                    "day": "2026-10-01",
                    "last_at": "x",
                },
            ],
        },
        state={"mood": 0.2, "energy": 0.7, "line": "还行", "baseline": {"wake": 7, "sleep": 23}},
        today=TODAY,
    )
    kw.update(over)
    return life_map(**kw)


def test_life_map_domains_and_relations():
    m = _life()
    _check(m)
    assert m["score"] == 1 and m["gaps"] == []
    by = {n["id"]: n for n in m["nodes"]}
    rel = {(link["a"], link["b"]) for link in m["links"] if link["kind"] == RELATE}
    rel |= {(b, a) for a, b in rel}
    # 记忆里提到最爱的旧书店 → 相关
    assert ("likes:最爱.旧书店", "memory:7") in rel
    # 同领域的能力和记忆相关
    assert ("ability:技能.日常", "memory:7") in rel
    # 提到同事的记忆连到关系
    assert ("relation:b", "memory:8") in rel
    # 成长改动连到它改的那一项性格
    assert ("growth:1", "personality:五项.外向") in rel
    assert by["growth:1"]["fresh"] and by["memory:7"]["fresh"] and not by["memory:8"]["fresh"]
    assert by["relation:b"]["peer"] == "b"
    assert any(c["node"] == "growth:1" for c in m["changes"])


def test_life_map_partial_gaps_and_repeatable():
    m = _life(memory={}, relation={})
    assert {g["node"] for g in m["gaps"]} == {"gap:memory", "gap:relation"}
    assert m["score"] == 0.8 and m["missing"] == ["记忆", "关系"]
    assert _life() == _life()
