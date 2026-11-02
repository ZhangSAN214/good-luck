"""旅行打卡：判定卡决定值不值得；不值得不写不画；日记带前 3 天上下文；额度和花费；配图默认关。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from roundtable.core.chat import ChatHub
from roundtable.core.member.checkin import Checkin, Scene, store
from roundtable.core.member.checkin import card as ck

from ...orchestrator.conftest import Env

AT = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)
GOOD = {
    "can": True,
    "why": "防波堤尽头空着，正好站一个人",
    "reject": None,
    "line": "浪拍在堤上，有渔船在进港",
    "action": "她扶着栏杆探头看那艘进港的渔船",
    "emotion": "安静",
    "stand": "right",
    "distance": "mid",
    "facing": "back",
    "score": 16,
    "prompt": "风把头发吹到一边",
}
JOURNAL = "# 防波堤上的风\n\n今天坐了很久的车才到海边小镇，风大得我差点没站稳。"


def member(env: Env, mid: str = "V1"):
    return next(m for m in ChatHub(env.rt).everyone() if m.id == mid)


def scene(mid: str = "V1", at: datetime = AT, title: str = "海边小镇") -> Scene:
    return Scene(mid, "trip", title, [f"在{title}坐在防波堤上看日落"], at, ref=str(at))


def calls(env: Env, word: str) -> list:
    return [c for c in env.fake.calls if word in c.messages[0].content]


async def test_a_good_card_gets_a_journal_and_a_record():
    env = Env()
    env.fake.queue("b1", json.dumps(GOOD, ensure_ascii=False), JOURNAL)
    rec = await Checkin(env.rt, lambda usd: True).run(member(env), scene())
    assert rec and rec["ok"] and not rec["reason"]
    assert rec["journal"] == {
        "title": "防波堤上的风",
        "body": "今天坐了很久的车才到海边小镇，风大得我差点没站稳。",
    }
    assert rec["photo"] is None  # 配图默认关：没问过用户不出图
    assert not env.fake.media_calls
    assert store.checkins(env.rt)[-1]["id"] == rec["id"]
    # 花费记在 travel 步骤（算生活每日上限）
    steps = {r[0] for r in env.rt.repo.conn.execute("SELECT step FROM calls").fetchall()}
    assert steps == {"travel"}


async def test_not_worth_it_means_no_journal_and_no_photo():
    env = Env()
    bad = GOOD | {"can": False, "reject": "文字页"}
    env.fake.queue("b1", json.dumps(bad, ensure_ascii=False))
    rec = await Checkin(env.rt, lambda usd: True).run(member(env), scene())
    assert rec and not rec["ok"] and rec["reason"] == "文字页"
    assert rec["journal"] is None and len(env.fake.calls) == 1  # 不硬写


def test_lazy_action_and_low_score_do_not_pass():
    card, why = ck.clean_card(GOOD | {"action": "她站在那里提着裙摆"}, "在房间里")
    assert why.startswith("动作偷懒")
    # 有风有水时可以提裙摆
    assert ck.clean_card(GOOD | {"action": "她提着裙摆跨过水洼"}, "海边起风了")[1] == ""
    assert "没那么想去" in ck.clean_card(GOOD | {"score": 5}, "")[1]
    assert ck.clean_card({"can": True, "score": 18}, "")[1]  # 没写动作和那一刻
    assert ck.clean_card({"can": "yes"}, "")[0]["can"] is False  # 只认 true


def test_shot_text_is_structured_by_code_with_fixed_constraints():
    text = ck.shot_text(ck.clean_card(GOOD, "")[0])
    assert text.startswith("她站在画面右侧；中景")
    assert "背对镜头" in text and GOOD["action"] in text and "风把头发" in text
    assert text.endswith(ck.FIXED) and "五根手指" in text


async def test_journal_carries_the_last_three_days_only():
    env = Env()
    ckn = Checkin(env.rt, lambda usd: True)
    for i, title in enumerate(["古镇", "湖边", "花海", "隔壁城市"]):
        at = AT + timedelta(days=i * 2 if i < 3 else 5)
        env.fake.queue("b1", json.dumps(GOOD, ensure_ascii=False), f"# {title}的一天\n\n正文{i}")
        await ckn.run(member(env), scene(at=at, title=title))
    prompts = [c.messages[-1].content for c in calls(env, "给自己写一篇旅行日记")]
    assert len(prompts) == 4
    assert "前几天没有出门打卡" in prompts[0]
    assert "古镇的一天" in prompts[1]  # 前一次（2 天前）的日记题目带进来了
    last = prompts[3]  # 第 4 次在第 5 天：前 3 天内只有第 4 天那次（花海）
    assert "花海的一天" in last and "古镇的一天" not in last


async def test_quota_and_allowance_are_checked_before_any_call():
    env = Env()
    ckn = Checkin(env.rt, lambda usd: True)
    env.fake.queue("b1", json.dumps(GOOD, ensure_ascii=False), JOURNAL)
    assert await ckn.run(member(env), scene())
    n = len(env.fake.calls)
    assert await ckn.run(member(env), scene(at=AT + timedelta(hours=1))) is None  # 每人每天 1 次
    assert len(env.fake.calls) == n
    broke = Checkin(env.rt, lambda usd: False)
    assert await broke.run(member(env, "V2"), scene("V2")) is None
    assert len(env.fake.calls) == n


async def test_trip_happenings_go_through_the_checkin_in_the_life_tick():
    from roundtable.core.life import LifeEngine
    from roundtable.core.member.story import Happening
    from roundtable.core.memory import MemoryKeeper

    env = Env()
    hub = ChatHub(env.rt)
    life = LifeEngine(env.rt, hub, MemoryKeeper(env.rt, hub.everyone))
    env.fake.queue("b1", json.dumps(GOOD, ensure_ascii=False), JOURNAL)
    h = Happening(AT, "travel_trip", 0, ["V1"], "在海边小镇捡了几个贝壳", "away")
    other = Happening(AT, "travel_go", 0, ["V2"], "出发去古镇了", "away")
    assert await life.trip_checkins([h, other]) == 1
    rec = store.checkins(env.rt)[-1]
    assert rec["source"] == "trip" and rec["member"] == "V1"
