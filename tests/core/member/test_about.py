"""P8 关于你：8 位共用的用户画像。线索要对得上用户原话；推测会变淡、亲口说的不变淡；
不推测敏感领域；「不对」之后不再推回；按领域关；聊天只带相关几条；工作模式不挂载；只有本插件写。"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.chat import ROOM, WORK, ChatHub, dm_room, ensure_default_rooms
from roundtable.core.member import WorkModeError
from roundtable.core.member.about import SAID, USER, AboutError, AboutMissing, AboutYou
from roundtable.core.memory import LAST_DAY_KEY, MemoryKeeper
from roundtable.core.service import RoundtableService
from roundtable.core.storage.repository import iso

from ..orchestrator.conftest import MEDIUM, Env

DAY = date(2026, 10, 28)
START = datetime(2026, 10, 28, tzinfo=UTC)
WINDOW = (iso(START), iso(START + timedelta(days=1)))
SRC = Path(__file__).resolve().parents[3] / "src" / "roundtable"


def setup(env: Env | None = None) -> tuple[Env, AboutYou]:
    env = env or Env()
    ensure_default_rooms(env.rt)
    env.rt.repo.ensure_chat_room(dm_room("V1"), kind="dm", title="V1", members=["V1"])
    return env, AboutYou(env.rt)


def say(env: Env, text: str, room: str = ROOM, hour: int = 10, author: str = "user") -> dict:
    at = (START + timedelta(hours=hour)).isoformat()
    return env.rt.repo.add_chat_message(room, author, text, created_at=at)


def clue(text, quote, domain="兴趣爱好", said=True):
    return {"text": text, "quote": quote, "domain": domain, "said": said}


# --- 线索：对得上原话才记 -------------------------------------------------------------


def test_clue_needs_users_own_words_in_life_rooms():
    env, about = setup()
    say(env, "我周末一般去爬山，雷打不动")
    say(env, "工作群里我说我爱吃榴莲", room=WORK)
    say(env, "我是 V1，我爱看星星", author="V1")
    out = about.observe(
        "V1",
        DAY.isoformat(),
        WINDOW,
        [
            clue("用户周末去爬山", "周末一般去爬山"),
            clue("用户爱吃榴莲", "我爱吃榴莲", domain="口味"),  # 工作群的话不读
            clue("用户爱看星星", "我爱看星星"),  # 不是用户说的
        ],
    )
    assert out["new"] == 1 and out["unverified"] == 2
    out = about.observe("V1", DAY.isoformat(), WINDOW, [clue("用户会弹钢琴", "我会弹钢琴")])
    assert out["unverified"] == 1  # 原话里没有：编的
    (f,) = about.facts()
    assert f["text"] == "用户周末去爬山" and f["source"] == SAID
    assert f["evidence"][0]["quote"] == "我周末一般去爬山，雷打不动"
    assert f["evidence"][0]["room"] == ROOM and f["evidence"][0]["by"] == "V1"


def test_recalled_message_is_no_evidence():
    env, about = setup()
    msg = say(env, "我最近在学日语")
    env.rt.repo.recall_chat_message(msg["id"])
    out = about.observe("V1", "2026-10-28", WINDOW, [clue("用户在学日语", "在学日语", "学业")])
    assert out["unverified"] == 1 and not about.facts()


def test_sensitive_domains_are_never_inferred():
    env, about = setup()
    say(env, "我这两天在吃药，工资也还没发")
    say(env, "我住址是幸福路 3 号")
    out = about.observe(
        "V1",
        "2026-10-28",
        WINDOW,
        [
            clue("用户在吃药", "在吃药", "其他"),
            clue("用户工资没发", "工资也还没发", "其他"),
            clue("用户住幸福路", "幸福路 3 号", "其他"),
        ],
    )
    assert out["blocked"] == 3 and not about.facts()
    assert {"健康", "宗教", "政治", "性取向", "经济", "行动轨迹"} <= set(about.rules.blocked)


def test_same_fact_from_two_members_merges_and_more_evidence_raises_confidence():
    env, about = setup()
    say(env, "我好像挺喜欢下雨天的", hour=9)
    say(env, "下雨天真舒服，喜欢下雨天", hour=11)
    c = clue("用户喜欢下雨天", "下雨天真舒服", said=False)
    about.observe("V1", "2026-10-28", WINDOW, [c])
    one = about.confidence(about.facts()[0], DAY)
    about.observe(
        "V2", "2026-10-28", WINDOW, [clue("用户喜欢下雨天", "挺喜欢下雨天的", said=False)]
    )
    (f,) = about.facts()
    assert len(f["evidence"]) == 2
    assert about.confidence(f, DAY) > one
    about.observe(
        "V3", "2026-10-28", WINDOW, [clue("用户喜欢下雨天", "挺喜欢下雨天的", said=False)]
    )
    (f,) = about.facts()
    assert len(f["evidence"]) == 2 and f["noticed"] == ["V3"]  # 同一句原话不重复算


def test_inferred_fades_said_and_locked_do_not():
    env, about = setup()
    say(env, "我喜欢猫")
    say(env, "又熬夜了")
    about.observe(
        "V1",
        "2026-10-28",
        WINDOW,
        [clue("用户喜欢猫", "我喜欢猫"), clue("用户常熬夜", "又熬夜了", "作息", said=False)],
    )
    said, guess = sorted(about.facts(), key=lambda f: f["source"] != SAID)
    later = DAY + timedelta(days=60)
    assert about.confidence(said, later) == about.confidence(said, DAY) == 0.9
    assert about.confidence(guess, later) == pytest.approx(0.5 * 0.25)
    assert about.faded(guess, later) and not about.faded(said, later)
    about.lock(guess["id"])
    guess = next(f for f in about.facts() if f["id"] == guess["id"])
    assert about.confidence(guess, later) == 0.5 and not about.faded(guess, later)


def test_rejected_is_never_pushed_back():
    env, about = setup()
    say(env, "今天又去了咖啡馆写作业")
    c = clue("用户爱喝咖啡", "去了咖啡馆写作业", "口味", said=False)
    about.observe("V1", "2026-10-28", WINDOW, [c])
    about.reject(about.facts()[0]["id"])
    out = about.observe("V2", "2026-10-28", WINDOW, [clue("用户很爱喝咖啡", "去了咖啡馆", "口味")])
    assert out["rejected"] == 1 and not about.facts()
    about.forget_rejected(0)
    about.observe("V2", "2026-10-28", WINDOW, [c])
    assert about.facts()


def test_domain_off_stops_collecting_and_injecting():
    env, about = setup()
    say(env, "我超爱吃火锅")
    about.add("用户叫小张", "称呼与身份")
    about.set_domain("口味", False)
    out = about.observe("V1", "2026-10-28", WINDOW, [clue("用户爱吃火锅", "超爱吃火锅", "口味")])
    assert out["skip"] == 1 and len(about.facts()) == 1
    about.set_domain("口味", True)
    about.observe("V1", "2026-10-28", WINDOW, [clue("用户爱吃火锅", "超爱吃火锅", "口味")])
    assert "火锅" in about.lines("晚上吃火锅吗")
    about.set_domain("口味", False)
    assert "火锅" not in about.lines("晚上吃火锅吗")
    with pytest.raises(AboutError):
        about.set_domain("不存在", False)


# --- 聊天时只带相关的几条 ---------------------------------------------------------------


def test_lines_only_relevant_within_budget_and_refuse_work_mode():
    env, about = setup()
    for t in ("用户在读计算机专业", "用户喜欢周末爬山", "用户爱吃火锅", "用户养了一只橘猫"):
        about.add(t, "其他")
    text = about.lines("周末要不要一起去爬山？")
    assert "爬山" in text and "火锅" not in text and "橘猫" not in text
    assert text.startswith(about.rules.header)
    assert about.lines("今天天气") == ""
    for i in range(10):
        about.add(f"用户周末第{i}件事是爬山看日出看晚霞再吃一顿火锅", "其他")
    body = about.lines("周末爬山看日出吃火锅").split("\n")[1:]
    assert 0 < len(body) <= about.rules.inject_max
    assert len("\n".join(body)) <= about.rules.context_chars
    with pytest.raises(WorkModeError):
        about.lines("周末爬山", mode="work")


def test_low_confidence_guess_is_marked_maybe():
    env, about = setup()
    say(env, "又熬夜了")
    about.observe("V1", "2026-10-28", WINDOW, [clue("用户常熬夜", "又熬夜了", "作息", said=False)])
    assert "用户常熬夜（可能）" in about.lines("昨晚熬夜了吗", today=DAY)


async def test_dm_and_group_prompts_carry_it_work_room_does_not():
    env = Env()
    hub = ChatHub(env.rt)
    ensure_default_rooms(env.rt)
    AboutYou(env.rt).add("用户周末喜欢爬山", "兴趣爱好")
    dm = hub.room(dm_room("V1"))
    msg, speakers = dm.post("周末去爬山吗")
    await dm.replies(msg["id"], speakers)
    assert "用户周末喜欢爬山" in env.fake.calls[-1].messages[-1].content
    work = hub.room(WORK)
    n = len(env.fake.calls)
    msg, speakers = work.post("周末去爬山吗")
    await work.replies(msg["id"], speakers)
    sent = [c.messages[-1].content for c in env.fake.calls[n:]]
    assert sent and all("用户周末喜欢爬山" not in s for s in sent)


# --- 夜间整理顺带交线索 -----------------------------------------------------------------


async def test_night_call_feeds_clues_and_checks_quotes():
    env = Env()
    svc = RoundtableService(env.rt)
    setup(env)
    env.rt.repo.add_chat_message(
        dm_room("V1"),
        "user",
        "周末只想去爬山，不想逛街",
        created_at=(START + timedelta(hours=9)).isoformat(),
    )
    env.rt.repo.set_setting(LAST_DAY_KEY, (DAY - timedelta(days=1)).isoformat())
    await svc.memory.catch_up(datetime(2026, 10, 29, 12, tzinfo=UTC))
    facts = svc.engine.about.facts()
    assert [f["text"] for f in facts] == ["用户周末喜欢爬山"]  # 吃药被拦、吃辣对不上原话
    night = next(c for c in env.fake.calls if "今晚整理" in c.messages[0].content)
    assert "关于用户的线索" in night.messages[0].content


async def test_night_without_wiring_writes_no_profile():
    env, _ = setup()
    env.rt.repo.add_chat_message(
        dm_room("V1"), "user", "周末只想去爬山", created_at=(START + timedelta(hours=9)).isoformat()
    )
    keeper = MemoryKeeper(env.rt, ChatHub(env.rt).everyone)
    env.rt.repo.set_setting(LAST_DAY_KEY, (DAY - timedelta(days=1)).isoformat())
    await keeper.catch_up(datetime(2026, 10, 29, 12, tzinfo=UTC))
    assert AboutYou(env.rt).facts() == []  # 记忆插件只交出线索，不自己写画像


# --- 每周整合 ---------------------------------------------------------------------------


def test_weekly_drops_dead_evidence_merges_and_runs_once_a_week():
    env, about = setup()
    gone = say(env, "我在学吉他", hour=9)
    say(env, "我喜欢看科幻电影", hour=10)
    say(env, "科幻电影我最爱", hour=11)
    about.observe("V1", "2026-10-28", WINDOW, [clue("用户在学吉他", "在学吉他")])
    about.observe("V1", "2026-10-28", WINDOW, [clue("用户喜欢看科幻电影", "喜欢看科幻电影")])
    data = about._load()  # 模拟两条后来才发现是同一件事
    data["facts"].append(
        {
            **data["facts"][-1],
            "id": 99,
            "text": "用户喜欢科幻电影",
            "evidence": [
                {
                    **data["facts"][-1]["evidence"][0],
                    "msg": gone["id"] + 2,
                    "quote": "科幻电影我最爱",
                }
            ],
        }
    )
    about._save(data)
    about.add("用户叫小张", "称呼与身份")
    env.rt.repo.recall_chat_message(gone["id"])
    out = about.weekly(datetime(2026, 10, 29, 9, tzinfo=UTC))
    assert out["dropped"] == 1 and out["merged"] == 1
    texts = sorted(f["text"] for f in about.facts())
    assert texts == ["用户叫小张", "用户喜欢看科幻电影"]
    sci = next(f for f in about.facts() if "科幻" in f["text"])
    assert len(sci["evidence"]) == 2
    assert about.weekly(datetime(2026, 10, 30, 9, tzinfo=UTC)) is None


async def test_engine_tick_consolidates_weekly():
    env = Env()
    svc = RoundtableService(env.rt)
    out = await svc.engine.tick(datetime(2026, 10, 29, 9, tzinfo=UTC))
    assert out.get("about_week") == 1
    out = await svc.engine.tick(datetime(2026, 10, 29, 10, tzinfo=UTC))
    assert "about_week" not in out


# --- 你在画像页的操作 ------------------------------------------------------------------


def test_user_edit_add_lock_clear_export():
    env, about = setup()
    say(env, "又熬夜了")
    about.observe("V1", "2026-10-28", WINDOW, [clue("用户常熬夜", "又熬夜了", "作息", said=False)])
    f = about.facts()[0]
    about.edit(f["id"], "用户考试周常熬夜")
    f = about.facts()[0]
    assert f["source"] == USER and f["history"][0]["text"] == "用户常熬夜"
    assert about.confidence(f) == 1.0
    with pytest.raises(AboutMissing):
        about.delete(12345)
    with pytest.raises(AboutError):
        about.add("  ")
    with pytest.raises(AboutError):
        about.add("用户叫小张", "不存在的领域")
    about.add("用户吃素，偶尔吃鱼", "口味")  # 你自己写的不拦
    exported = json.loads(about.export("json"))
    assert {x["text"] for x in exported["facts"]} == {"用户考试周常熬夜", "用户吃素，偶尔吃鱼"}
    md = about.export("md")
    assert "## 作息" in md and "证据" in md and "「又熬夜了」" in md
    about.reject(about.facts()[0]["id"])
    assert about.clear() == 1 and about.facts() == [] and about._load()["rejected"]
    assert about.clear(rejected=True) == 1 and about._load()["rejected"] == []


def test_api_endpoints():
    env, about = setup()
    say(env, "我是计算机专业的")
    about.observe("V1", "2026-10-28", WINDOW, [clue("用户读计算机专业", "计算机专业", "学业")])
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        v = c.get("/api/chat/about").json()
        (f,) = v["facts"]
        assert f["evidence"][0]["room_title"] and f["evidence"][0]["by_name"]
        assert any(d["name"] == "学业" and d["on"] for d in v["domains"])
        r = c.post("/api/chat/about/facts", json={"text": "用户叫小张", "domain": "称呼与身份"})
        assert r.status_code == 201 and len(r.json()["facts"]) == 2
        assert c.post(f"/api/chat/about/facts/{f['id']}/lock", json={"locked": True}).json()
        r = c.patch(f"/api/chat/about/facts/{f['id']}", json={"text": "用户读计算机"})
        assert any(x["text"] == "用户读计算机" for x in r.json()["facts"])
        assert c.post("/api/chat/about/domains", json={"domain": "学业", "on": False}).json()
        assert (
            c.post("/api/chat/about/domains", json={"domain": "坏", "on": False}).status_code == 400
        )
        assert c.post(f"/api/chat/about/facts/{f['id']}/reject").json()["rejected"]
        assert c.delete("/api/chat/about/facts/999").status_code == 404
        r = c.get("/api/chat/about/export", params={"fmt": "md"})
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        assert c.get("/api/chat/about/export", params={"fmt": "xls"}).status_code == 400
        assert c.delete("/api/chat/about", params={"rejected": True}).json()["deleted"] == 2


# --- 守卫 -------------------------------------------------------------------------------


@pytest.mark.parametrize("workflow", ["discussion", "office", "solo", "collab"])
async def test_profile_never_reaches_work_prompts(workflow):
    env = Env(confirm_threshold_usd=100.0)
    svc = RoundtableService(env.rt)
    mark = "画像标记ZQ9"
    svc.engine.about.add(f"用户{mark}求函数最值", "学业")
    sid = svc.create(MEDIUM, seed=3, workflow=workflow)
    async for _ in svc.events(sid):
        pass
    assert env.fake.calls
    sent = json.dumps([[m.content for m in c.messages] for c in env.fake.calls], ensure_ascii=False)
    assert mark not in sent


def test_only_about_plugin_writes_profile():
    writer = re.compile(r"\.(save_about_you|clear_about_you)\(")
    for f in SRC.rglob("*.py"):
        if writer.search(f.read_text(encoding="utf-8")):
            assert f == SRC / "core" / "member" / "about.py", f
    keys = re.compile(r"[\"']about_you:")
    for f in SRC.rglob("*.py"):
        if keys.search(f.read_text(encoding="utf-8")):
            assert f.name == "repository.py", f


# --- 知情范围（拟人插件调研第 1 项，2026-10-31）---------------------------------------


def _scope_setup():
    env, about = setup()
    env.rt.repo.ensure_chat_room(dm_room("V2"), kind="dm", title="V2", members=["V2"])
    return env, about


class _Always:
    def random(self):
        return 0.0

    def choice(self, seq):
        return seq[0]


def test_what_you_tell_one_member_in_private_only_she_knows():
    env, about = _scope_setup()
    say(env, "跟你说，我周末一般去爬山", room=dm_room("V1"))
    say(env, "我超爱吃辣", room=ROOM, hour=11)
    about.observe("V1", DAY.isoformat(), WINDOW, [clue("用户周末去爬山", "周末一般去爬山")])
    about.observe("V3", DAY.isoformat(), WINDOW, [clue("用户爱吃辣", "我超爱吃辣", "口味")])
    about.add("用户的生日是 5 月 1 日", "称呼与身份")
    v1 = about.lines("周末去爬山 吃辣 生日", member="V1")
    v2 = about.lines("周末去爬山 吃辣 生日", member="V2")
    assert "爬山" in v1 and "爬山" not in v2
    assert "吃辣" in v2 and "生日" in v2  # 群里说的、你在画像页写的：大家都知道
    view = {f["text"]: f for f in about.view()["facts"]}
    assert view["用户周末去爬山"]["known_by"] == ["V1"] and not view["用户周末去爬山"]["everyone"]
    assert view["用户爱吃辣"]["everyone"]


def test_a_secret_never_spreads_but_other_things_can_be_heard_from_a_colleague():
    env, about = _scope_setup()
    say(env, "我周末一般去爬山", room=dm_room("V1"))
    say(env, "别告诉别人，我在偷偷学吉他", room=dm_room("V1"), hour=12)
    about.observe(
        "V1",
        DAY.isoformat(),
        WINDOW,
        [clue("用户周末去爬山", "周末一般去爬山"), clue("用户在学吉他", "在偷偷学吉他")],
    )
    moved = about.gossip(["V1", "V2"], _Always())
    assert len(moved) == 1 and moved[0][1:] == ("V1", "V2")
    again = about.gossip(["V1", "V2"], _Always())
    assert again == []  # 吉他是秘密，永远不传
    line = about.lines("周末去爬山 学吉他", member="V2", names={"V1": "阿一"})
    assert "爬山（听阿一说的）" in line and "吉他" not in line
    assert "听" not in about.lines("周末去爬山", member="V1")


def test_guesses_are_not_passed_on_and_scope_off_means_everyone_knows():
    env, about = _scope_setup()
    say(env, "这周又熬夜了", room=dm_room("V1"))
    about.observe(
        "V1", DAY.isoformat(), WINDOW, [clue("用户常熬夜", "又熬夜了", "作息", said=False)]
    )
    assert about.gossip(["V1", "V2"], _Always()) == []
    assert "熬夜" not in about.lines("熬夜", member="V2")
    cfg = env.rt.config.roundtable
    rules = cfg.chat.engine.about.model_copy(update={"scope": False})
    engine = cfg.chat.engine.model_copy(update={"about": rules})
    chat = cfg.chat.model_copy(update={"engine": engine})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )
    assert "熬夜" in AboutYou(env.rt).lines("熬夜", member="V2")


def test_your_identity_comes_first_in_dm_and_group_chat():
    """10-31 用户定的初始身份：老板兼房东，私聊和群聊每次都带；关掉「关于你」就不带。"""
    from roundtable.core.chat import with_about_you
    from roundtable.core.memory import NONE

    from ..orchestrator.conftest import Env

    env = Env()
    text = with_about_you(env.rt, NONE, "你好")
    assert text.startswith("【对方是谁】") and "老板" in text and "房东" in text
    cfg = env.rt.config.roundtable
    about = cfg.chat.engine.about.model_copy(update={"enabled": False})
    engine = cfg.chat.engine.model_copy(update={"about": about})
    chat = cfg.chat.model_copy(update={"engine": engine})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )
    assert "老板" not in with_about_you(env.rt, NONE, "你好")


def test_relevance_is_not_diluted_by_a_second_clause():
    from roundtable.core.member.about import relevance

    q = "你还记得我养的猫叫什么吗？"
    assert relevance("养了一只橘猫叫土豆，特别能吃", q) == relevance("养了一只橘猫叫土豆", q) > 0.1
    assert relevance("不太吃辣，川菜只能吃微辣", q) == 0
