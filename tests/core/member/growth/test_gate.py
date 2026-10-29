"""程序把关：只能这样改、锁定项不动、必须引用真实经历、核心性格每月一格、8 人不撞型、
受用户影响不超过三成；同一周的几条叠在一起检查。"""

from __future__ import annotations

from roundtable.core.chat import ChatHub
from roundtable.core.member.growth import Gate, Ledger, Line, Proposal, Week, parse
from roundtable.core.member.growth.gate import distance, fresh_patch, overlap
from roundtable.core.member.profile import ProfileStore

from ...orchestrator.conftest import Env
from .common import MONDAY, WEEK, profile, setup

LINES = [
    Line(1, "diary", "2026-10-19", "今天又去天文台看星星，越看越着迷。", False),
    Line(2, "user", "2026-10-22", "你最近好像特别爱看星星", True),
    Line(3, "item", "2026-10-21", "在天文台待了一晚上看流星", False),
]


def week() -> Week:
    return Week("V1", WEEK, MONDAY, list(LINES))


def env_and_gate():
    env = Env()
    setup(env, experiences=False)
    return env, Gate(env.rt)


def others(env) -> dict:
    store = ProfileStore(env.rt)
    return {m.id: store.current(m.id).data for m in ChatHub(env.rt).everyone() if m.id != "V1"}


def score(trait="好奇", delta=1, ev=(1,), quote="越看越着迷"):
    return Proposal(
        "score", f"personality.五项.{trait}", delta=delta, evidence=list(ev), quote=quote
    )


def add(path="likes.最爱", value="看星星", ev=(3,), quote="在天文台待了一晚上看流星", op="add"):
    return Proposal(op, path, value=value, evidence=list(ev), quote=quote)


def check(env, gate, proposals, ledger=None):
    return gate.check("V1", proposals, week(), ledger or Ledger(), others(env))


def test_parse():
    assert parse("不是 JSON", 3) == (None, "")
    got, skip = parse(
        '{"changes": [{"op": "Score", "path": "a", "evidence": ["2", "x"]}, 1], "skip": "s"}', 3
    )
    assert got[0].op == "score" and got[0].evidence == [2] and skip == "s"
    many = '{"changes": [' + ",".join(['{"op": "add"}'] * 5) + "]}"
    assert len(parse(many, 3)[0]) == 3


def test_overlap_and_distance():
    assert overlap("越看越着迷", LINES[0].text) == 1.0
    assert overlap("完全没写过的事", LINES[0].text) < 0.3
    assert distance({"a": 1, "b": 2}, {"a": 3, "b": 2, "c": 5}) == 2
    assert distance({"a": 1}, {"b": 1}) is None


def test_score_and_list_changes_pass():
    env, gate = env_and_gate()
    v1, v2 = check(env, gate, [score(), add()])
    assert v1.ok and v1.old == 3 and v1.new == 4 and not v1.user
    assert v1.patch == {
        "personality": {"五项": {**profile("V1")["personality"]["五项"], "好奇": 4}}
    }
    assert v2.ok and v2.patch == {"likes": {"最爱": ["红茶", "看星星"]}}
    v = check(env, gate, [add(value="红茶", op="remove")])[0]
    assert v.ok and v.patch == {"likes": {"最爱": []}}


def test_only_allowed_shapes():
    env, gate = env_and_gate()
    bad = [
        score(delta=2),
        score(trait="不存在"),
        Proposal("rewrite", "personality.性格", value="新的原文", evidence=[1], quote="越看越着迷"),
        add(path="identity.名字", value="新名字"),
        add(path="personality.性格", value="x"),
        add(value="这一项实在是太长太长太长太长太长太长太长了"),
        add(value="红茶"),
        add(value="没有这一项", op="remove"),
    ]
    got = check(env, gate, bad)
    assert not any(v.ok for v in got[:3])  # max_changes = 3
    for p in bad[3:]:
        assert not check(env, gate, [p])[0].ok, p
    reasons = [check(env, gate, [p])[0].reason for p in bad]
    assert "一次只能加 1 或减 1" in reasons[0] and "不是成长能改的列表" in reasons[3]


def test_score_bounds_and_list_full():
    env, gate = env_and_gate()
    v = check(env, gate, [score(trait="幽默", delta=-1)])[0]
    assert v.ok
    ProfileStore(env.rt).change(
        "V1",
        {"personality": {"五项": {**profile("V1")["personality"]["五项"], "温和": 5}}},
        author="user",
    )
    assert "不能再高" in check(env, gate, [score(trait="温和")])[0].reason
    ProfileStore(env.rt).change(
        "V1", {"likes": {"最爱": [str(i) for i in range(8)]}}, author="user"
    )
    assert "已经有 8 项" in check(env, gate, [add()])[0].reason


def test_locked_items_untouched():
    env, gate = env_and_gate()
    store = ProfileStore(env.rt)
    store.lock("V1", "personality.五项")
    store.lock("V1", "likes.最爱")
    got = check(env, gate, [score(), add()])
    assert [v.ok for v in got] == [False, False] and all("锁住" in v.reason for v in got)


def test_must_cite_real_experience():
    env, gate = env_and_gate()
    assert "编号不对" in check(env, gate, [score(ev=())])[0].reason
    assert "编号不对" in check(env, gate, [score(ev=(1, 9))])[0].reason
    assert "找不到" in check(env, gate, [score(quote="我编的一句话完全没发生")])[0].reason
    assert "找不到" in check(env, gate, [score(quote="")])[0].reason


def test_core_personality_one_step_per_month():
    env, gate = env_and_gate()
    assert "这个月已经改过 1 格" in check(env, gate, [score()], Ledger(core_steps=1))[0].reason
    got = check(env, gate, [score(), score(trait="外向")])
    assert got[0].ok and not got[1].ok  # 同一周第二格也不行


def test_no_two_members_collide():
    env, gate = env_and_gate()
    store = ProfileStore(env.rt)
    near = profile("V2", (3, 4, 4, 4, 3))  # 和阿一只差 2
    store.change("V2", near, author="user")
    v = check(env, gate, [score()])[0]  # 好奇 3→4：和阿二只差 1
    assert not v.ok and v.clash == "V2" and "太像" in v.reason
    assert check(env, gate, [score(trait="外向", delta=-1)])[0].ok  # 离远了没关系


def test_catchphrase_not_copied_from_others():
    env, gate = env_and_gate()
    v = check(env, gate, [add(path="style.口头禅", value="V3的口头禅")])[0]
    assert not v.ok and v.clash == "V3"
    assert check(env, gate, [add(path="style.口头禅", value="好家伙")])[0].ok
    assert check(env, gate, [add(value="V3的口头禅")])[0].ok  # 喜好可以重样


def test_user_influence_capped_at_thirty_percent():
    env, gate = env_and_gate()
    user_add = add(ev=(2,), quote="特别爱看星星")
    v = check(env, gate, [user_add])[0]
    assert v.ok and v.user  # 起步按 10 条算：3 条以内可以
    v = check(env, gate, [user_add], Ledger(total=4, user=3))[0]
    assert not v.ok and "上限" in v.reason
    assert check(env, gate, [add()], Ledger(total=4, user=3))[0].ok  # 不受影响的照常
    assert check(env, gate, [user_add], Ledger(total=19, user=5))[0].ok  # 6/20 = 30%


def test_fresh_patch_recomputes_from_current_profile():
    p = add()
    assert fresh_patch({"likes": {"最爱": ["a"]}}, p) == {"likes": {"最爱": ["a", "看星星"]}}
    assert fresh_patch({"likes": {"最爱": ["看星星"]}}, p) == {}
    s = score()
    assert fresh_patch({"personality": {"五项": {"好奇": 5}}}, s) == {}
    assert fresh_patch({"personality": {"五项": {"好奇": 2, "外向": 1}}}, s) == {
        "personality": {"五项": {"好奇": 3, "外向": 1}}
    }
