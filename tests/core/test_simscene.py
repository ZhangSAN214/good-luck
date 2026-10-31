"""模拟场景：认识一个月、今天你过生日——开跑前按真实规则摆好关系、日历和「关于你」。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from roundtable.core.simlab import SimError, Simulation
from roundtable.core.simscene import material_with


def test_material_with_swaps_whole_blocks_and_adds_user_kinds():
    base = {"user_mix": {"a": 1}, "user": {"a": ["x"], "b": ["y"]}, "probes": ["p"], "exam": {}}
    scene = {"user_mix": {"c": 1}, "user": {"c": ["z"], "b": ["w"]}, "probes": ["q"]}
    got = material_with(base, scene)
    assert got["user_mix"] == {"c": 1} and got["probes"] == ["q"] and got["exam"] == {}
    assert got["user"] == {"a": ["x"], "b": ["w"], "c": ["z"]}
    assert base["user"] == {"a": ["x"], "b": ["y"]}  # 原素材不动


def test_unknown_scenario(tmp_path):
    with pytest.raises(SimError, match="没有这个场景"):
        Simulation(tmp_path / "x", days=1, scenario="nope")


async def test_birthday_after_a_month(tmp_path):
    out = tmp_path / "bday"
    sim = Simulation(out, days=1, seed=2, start=datetime(2026, 11, 3, 7), scenario="birthday")
    res = await sim.run()
    assert not res.errors, res.errors
    scene = res.meta["scenario"]
    assert scene["name"] == "birthday" and scene["known_days"] == 30
    assert scene["birthday"] == "他的生日（11-03）" and scene["facts"] == 6
    assert all(n >= 15 for n in scene["exchanges"].values())  # 都过了「熟悉」的门槛
    assert {m["stage"] for m in res.members} == {"熟悉"}
    assert {e["exam"] for e in res.exams} == {"what_day", "cake", "goodnight"}
    assert {p["q"] for p in res.probes} >= {"我们认识多久了？"}
    db = sqlite3.connect(out / "sim.db")
    rows = dict(db.execute("SELECT key, value FROM app_settings").fetchall())
    db.close()
    assert [u["name"] for u in json.loads(rows["calendar.user_dates"])] == ["他的生日"]
    facts = [f["text"] for f in json.loads(rows["about_you:profile"])["facts"]]
    assert "生日是 11 月 3 日" in facts and "养了一只橘猫叫土豆，特别能吃" in facts
