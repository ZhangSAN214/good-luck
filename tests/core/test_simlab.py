"""成员系统加速时间模拟：临时数据库、快进时钟、假模型、报告；不碰 data/、不联网。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from roundtable.cli import run
from roundtable.core.config import load_config
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import Message
from roundtable.core.runtime import DEFAULT_DB
from roundtable.core.simlab import (
    SIM_FILE,
    FakeMind,
    SimClock,
    SimError,
    Simulation,
    tells,
    tokens_of,
)


def test_refuses_data_dir_and_real_without_cap(tmp_path):
    with pytest.raises(SimError, match="data/"):
        Simulation(DEFAULT_DB.parent / "sim_x", days=1)
    with pytest.raises(SimError, match="花费上限"):
        Simulation(tmp_path / "a", days=1, real=True)
    with pytest.raises(SimError, match="1 天"):
        Simulation(tmp_path / "b", days=0)


def test_clock_needs_timezone():
    with pytest.raises(SimError):
        SimClock(datetime(2026, 11, 2))
    c = SimClock(datetime(2026, 11, 2, tzinfo=ZoneInfo("Asia/Shanghai")))
    assert c.advance(timedelta(hours=1)).hour == 1


def test_fake_mind_identifies_prompts_and_answers_valid_json():
    import yaml

    config, prompts = load_config(), PromptLibrary()
    mind = FakeMind(config, prompts, yaml.safe_load(SIM_FILE.read_text("utf-8")), 0)
    roles = config.roundtable.prompts
    for role in ("memory_night", "growth_reflect", "life_chat", "moment_post", "chat_dm"):
        system = prompts.get(role, roles[role]).system
        assert mind.identify([Message("system", system), Message("user", "")]) == role
    night = prompts.get("memory_night", roles["memory_night"]).system
    log = "<log>\n09:00 私聊 用户：我在读计算机专业的研究生\n</log>"
    got = json.loads(mind.reply("memory_night", [Message("system", night), Message("user", log)]))
    assert got["about"][0]["quote"] in "我在读计算机专业的研究生"
    week = "<week>\n1. [11-02 聊天] 今天去天台吹风了\n</week>"
    got = json.loads(mind.reply("growth_reflect", [Message("system", ""), Message("user", week)]))
    assert got["changes"][0]["evidence"] == [1]


def test_attribution_tells_distinct_voices():
    from roundtable.core.simlab import attribution

    distinct = {
        "a": ["喵喵今天好开心喵", "喵喵想吃鱼喵", "喵喵困了喵", "喵喵晒太阳喵"],
        "b": ["本座已阅，退下吧", "本座今日心情尚可", "本座不屑一顾", "本座要去练剑"],
    }
    assert attribution(distinct) == 1.0
    assert attribution({"a": ["嗯"], "b": ["哦"]}) is None


def test_tokens_and_tells():
    assert tokens_of("你好") == 2 and tokens_of("abcdefgh") == 2
    t = tells({"a": ["嗯，好的", "嗯，好的", "首先我们来看"], "b": ["哈哈"]})
    assert t["messages"] == 4 and t["dup_ratio"] == 0.25 and t["formal_ratio"] == 0.25


async def test_two_days_fake_run_is_isolated_and_reports(tmp_path):
    out = tmp_path / "run"
    res = await Simulation(out, days=2, seed=3, user_per_day=12).run()
    assert len(res.days) == 2
    assert res.meta["mode"] == "fake" and res.meta["spent_usd"] > 0  # 按配置价格估的
    assert not res.errors, res.errors
    assert (out / "sim.db").exists() and (out / "report.md").exists()
    report = json.loads((out / "report.json").read_text("utf-8"))
    assert report["days"][0]["life_events"] > 0
    assert {m["member"] for m in report["members"]}
    import sqlite3

    db = sqlite3.connect(out / "sim.db")
    assert not db.execute("SELECT id FROM chat_rooms WHERE kind = 'wander'").fetchall()  # 不上网
    db.close()
    md = (out / "report.md").read_text("utf-8")
    assert "成员系统加速时间模拟报告" in md and "前后对比" in md
    assert {p["day"] for p in res.probes} == {1, 2} and all(p["a"] for p in res.probes)
    assert "你（模拟用户）" in (out / "chat.md").read_text("utf-8")
    with pytest.raises(SimError, match="已存在"):  # 每次用全新的数据库
        await Simulation(out, days=1).run()


async def test_cli_sim(tmp_path):
    import io

    buf = io.StringIO()
    code = await run(["sim", "--days", "1", "--out", str(tmp_path / "c")], out=buf)
    assert code in (0, 1) and "报告" in buf.getvalue()
    buf = io.StringIO()
    assert await run(["sim", "--real", "--out", str(tmp_path / "d")], out=buf) == 2
    assert "花费上限" in buf.getvalue()
