"""记忆件：三路分流（合并计数 / 更新留旧 / 新增）、领域标签、
原文不改只改语气和重要度、挂着的事、习惯摘要。"""

from __future__ import annotations

from roundtable.core.member.memory import MemoryStore
from roundtable.core.member.memory.domains import tag
from roundtable.core.member.memory.store import MERGE, NEW, UPDATE, similarity

from ...orchestrator.conftest import Env

DOMAINS = ["学术", "日常", "工作", "娱乐", "社交", "身心"]


def store() -> tuple[Env, MemoryStore]:
    env = Env()
    return env, MemoryStore(env.rt)


def test_similarity_and_domains():
    assert similarity("和用户去爬山", "和用户去爬山") == 1.0
    assert similarity("和用户去爬山", "今天下雨了") == 0.0
    assert tag("看了场电影", "不存在的领域", DOMAINS) == "日常"  # 模型标了表外的归日常
    assert tag("随便", "身心", DOMAINS) == "身心"


def test_three_routes_merge_update_new():
    env, s = store()
    a = s.write("V1", "晚上和阿二一起打游戏", day="2026-10-20")
    assert a.route == NEW and a.item["domain"] == "日常" and a.item["hits"] == 1
    b = s.write("V1", "晚上和阿二一起打游戏了", day="2026-10-21", importance=2)
    assert b.route == MERGE and b.item["id"] == a.item["id"]
    assert b.item["hits"] == 2 and b.item["importance"] == 2  # 原文不改，合并计数
    assert b.item["text"] == "晚上和阿二一起打游戏"
    old = s.write("V1", "用户周末想去爬山", day="2026-10-20", fact=True, domain="社交")
    new = s.write("V1", "用户周末改成想去海边", day="2026-10-22", fact=True, domain="社交")
    assert new.route == UPDATE
    kept = env.rt.repo.memory_item(old.item["id"])
    assert kept["superseded_by"] == new.item["id"] and kept["text"] == "用户周末想去爬山"
    c = s.write("V1", "今天学会了做番茄炒蛋", day="2026-10-22")
    assert c.route == NEW
    # 同一个去重键只记一次
    assert s.write("V1", "x 场工作", day="2026-10-22", source_key="work:s1").route == NEW
    assert s.write("V1", "完全不同的一句话", day="2026-10-22", source_key="work:s1").route == "skip"


def test_retell_changes_only_tone_and_importance_and_keeps_old_versions():
    env, s = store()
    item = s.write("V1", "和阿三吵了一架", day="2026-10-20").item
    assert s.retell(item["id"], tone="现在想想其实是小事", importance=1, why="心情好") == 1
    assert s.retell(item["id"], tone=None, importance=3, why="心情低落") == 1
    got = env.rt.repo.memory_item(item["id"])
    assert got["text"] == "和阿三吵了一架"  # 原文一个字不改
    assert got["tone"] == "现在想想其实是小事" and got["importance"] == 3
    revs = env.rt.repo.memory_revisions(item["id"])
    assert [(r["field"], r["old"], r["new"]) for r in revs] == [
        ("tone", "", "现在想想其实是小事"),
        ("importance", "1", "3"),
    ]
    # 用户改原文也留旧版，改过的算事实
    s.user_edit(item["id"], "和阿三有点小争执")
    got = env.rt.repo.memory_item(item["id"])
    assert got["text"] == "和阿三有点小争执" and got["kind"] == "fact"
    assert ("text", "和阿三吵了一架", "和阿三有点小争执") in [
        (r["field"], r["old"], r["new"]) for r in env.rt.repo.memory_revisions(item["id"])
    ]


def test_hanging_at_most_five():
    env, s = store()
    things = ["还书", "周末爬山要带水", "给阿二回消息", "论文第三章", "买猫粮", "修自行车"]
    for i, t in enumerate(things):
        s.hang("V1", t, f"2026-10-2{i}")
    open_ = env.rt.repo.memory_hanging("V1")
    assert len(open_) == 5 and open_[0]["text"] == "周末爬山要带水"
    assert [h["text"] for h in env.rt.repo.memory_hanging("V1", "dropped")] == ["还书"]
    assert s.hang("V1", "修自行车", "2026-10-26") is None  # 已经挂着的不重复
    assert s.settle("V1", open_[0]["id"]) and len(env.rt.repo.memory_hanging("V1")) == 4
    assert not s.settle("V2", open_[1]["id"])  # 只能了结自己的


def test_digest_uses_whole_history_not_just_recent():
    env, s = store()
    old = s.write("V1", "周日早上去公园跑步", day="2024-03-03").item
    for _ in range(11):
        env.rt.repo.bump_memory_item(old["id"], "2024-06-01T00:00:00.000+00:00")
    for i in range(300):  # 最近有很多别的事
        s.write("V1", f"最近第{i}天整理文件编号{i * 7919}", day="2026-10-20")
    text = s.digest("V1")
    assert "周日早上去公园跑步（12 次）" in text  # 两年前的习惯照样在
    assert len(text) <= env.config.roundtable.chat.memory.digest_chars
    assert env.rt.repo.memory_digest("V1")["text"] == text
