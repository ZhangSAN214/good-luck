"""说话底线（去八股化）：规则表检查、改写一次、月上限、全员相同的提示词、配置校验。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from roundtable.core.chat import ChatHub, dm_room
from roundtable.core.config import load_config
from roundtable.core.config.schema import PlainTalkCheck, PlainTalkRule
from roundtable.core.plain_talk import (
    REWRITE_STEP,
    Hit,
    before_speaking_all,
    check,
    last_sentence,
    plain_prompt,
    review,
    rewrite_spent,
    with_plain_talk,
)
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import Message

from .orchestrator.conftest import Env

RULES = load_config().roundtable.plain_talk.check
MAXIM = "今天下雨了，我在家煮了一碗面。说到底，平凡的日子才是最好的答案。"
PLAIN = "今天下雨了，我在家煮了一碗面，味道还行，就是盐放多了。"


def rules_hit(text: str) -> list[str]:
    return [h.rule for h in check(text, RULES)]


# --- 规则表 ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (PLAIN, []),
        (MAXIM, ["ending_maxim", "mug"]),
        ("周末去看了展，人很多但值得。愿你也能找到让自己安静下来的地方🌿", ["ending_maxim"]),
        ("新项目下周启动，大家不忘初心，砥砺前行！", ["slogan"]),
        ("有人在加班，有人在恋爱，有人在发呆，城市的夜晚挺热闹。", ["parallel"]),
        ("总而言之，这个方案可行，成本也低，下周可以开始。", ["empty_summary"]),
        ("这部剧挺好看的，剧情紧凑，演员也在线，推荐！", []),
        ("我刚到家，我饿了，我先去吃饭啦，晚点聊", []),  # 口语里的重复不算排比
        ("说到底才是答案", []),  # 太短不查
    ],
)
def test_rule_table(text, expected):
    assert rules_hit(text) == expected


def test_ending_rule_only_looks_at_the_last_sentence():
    assert last_sentence("第一句。第二句！！😄") == "第二句"
    # 金句在中间、结尾是具体的事：不算金句收尾（但马克杯警戒线照样拦）
    found = rules_hit("说到底，平凡的日子才是最好的答案。不过今天的面确实咸了点，下次少放盐。")
    assert found == ["mug"]


def test_hits_carry_problem_and_excerpt():
    hit = check(MAXIM, RULES)[0]
    assert hit.problem == "用金句、感悟或寄语收尾"
    assert hit.excerpt == "说到底，平凡的日子才是最好的答案"


# --- 改写一次 --------------------------------------------------------------------


def fixed(result):
    seen = []

    async def rewrite(text, hits):
        seen.append((text, [h.rule for h in hits]))
        return result

    return rewrite, seen


async def test_clean_text_is_not_rewritten():
    rewrite, seen = fixed("不该被调用")
    out = await review(PLAIN, RULES, rewrite)
    assert out.text == PLAIN and not out.rewritten and seen == []


async def test_hit_is_rewritten_once():
    rewrite, seen = fixed("今天下雨了，我在家煮了一碗面。")
    out = await review(MAXIM, RULES, rewrite)
    assert out.text == "今天下雨了，我在家煮了一碗面。" and out.rewritten
    assert seen == [(MAXIM, ["ending_maxim", "mug"])]


@pytest.mark.parametrize(
    "bad",
    [
        None,  # 改写失败 / 超预算
        MAXIM,  # 原样返回
        "今天下雨了。说到底，平凡的日子才是最好的答案。",  # 问题没少
        "今天下雨了，我在家煮了一碗面。" * 6,  # 变得太长
    ],
)
async def test_bad_rewrite_keeps_the_original(bad):
    rewrite, seen = fixed(bad)
    out = await review(MAXIM, RULES, rewrite)
    assert out.text == MAXIM and not out.rewritten and len(seen) == 1


async def test_check_can_be_switched_off():
    rewrite, seen = fixed("x")
    off = RULES.model_copy(update={"enabled": False})
    assert (await review(MAXIM, off, rewrite)).text == MAXIM and seen == []


# --- 接进聊天：用成员自己的聊天档模型改一次，计入聊天预算 ---------------------------


async def test_chat_reply_is_checked_and_rewritten_by_the_same_member():
    env = Env()
    dm = ChatHub(env.rt).room(dm_room("V1"))
    env.fake.queue("b1", MAXIM, "今天下雨了，我在家煮了一碗面。")
    msg, speakers = dm.post("今天干嘛了")
    (reply,) = await dm.replies(msg["id"], speakers)
    assert reply["text"] == "今天下雨了，我在家煮了一碗面。"
    first, second = env.fake.calls[-2:]
    assert first.model == second.model == "b1"
    assert "说话底线" in first.messages[0].content  # 第 1 层：提示词
    assert "<draft>" in second.messages[-1].content and MAXIM in second.messages[-1].content
    assert "用金句、感悟或寄语收尾" in second.messages[-1].content
    assert rewrite_spent(env.rt) > 0


async def test_clean_chat_reply_costs_no_extra_call():
    env = Env()
    dm = ChatHub(env.rt).room(dm_room("V1"))
    env.fake.queue("b1", PLAIN)
    msg, speakers = dm.post("今天干嘛了")
    await dm.replies(msg["id"], speakers)
    assert len(env.fake.calls) == 1 and rewrite_spent(env.rt) == 0


async def test_monthly_rewrite_cap_sends_the_original():
    rules = load_config().roundtable.plain_talk
    tight = rules.model_copy(update={"check": RULES.model_copy(update={"monthly_usd": 0})})
    env = Env(rt_update={"plain_talk": tight})
    dm = ChatHub(env.rt).room(dm_room("V1"))
    env.fake.queue("b1", MAXIM)
    msg, speakers = dm.post("今天干嘛了")
    (reply,) = await dm.replies(msg["id"], speakers)
    assert reply["text"] == MAXIM and len(env.fake.calls) == 1


def test_spent_in_step_only_counts_that_step():
    env = Env()
    from roundtable.core.budget.guard import month_window

    assert env.rt.repo.spent_in_step(REWRITE_STEP, *month_window(env.rt.budget.now())) == 0


# --- 第 1 层：全员相同、只接在配置的角色上 -----------------------------------------


def test_plain_talk_is_identical_for_every_member_and_only_for_listed_roles():
    config = load_config()
    lib = PromptLibrary()
    base = (Message("system", "S"), Message("user", "U"), Message("assistant", "A"))
    a = with_plain_talk(lib, config, base, "answer")
    b = with_plain_talk(lib, config, base, "answer")
    assert a == b and a[0].content.startswith("S\n\n说话底线")
    assert a[1].content.startswith("U\n\n") and a[2] == base[2]
    assert with_plain_talk(lib, config, base, "review") == base  # JSON 评审不接
    off = config.roundtable.model_copy(
        update={"plain_talk": config.roundtable.plain_talk.model_copy(update={"enabled": False})}
    )
    assert with_plain_talk(lib, config.model_copy(update={"roundtable": off}), base, "chat") == base


def test_plain_prompt_keeps_the_original_version_and_hash():
    config = load_config()
    lib = PromptLibrary()
    template = lib.get("chat", config.roundtable.prompts["chat"])
    rendered = template.render(**{v: "x" for v in template.variables})
    out = plain_prompt(lib, config, rendered)
    assert (out.role, out.version, out.sha256) == (rendered.role, rendered.version, rendered.sha256)
    assert "说话底线" in out.messages[0].content


def test_work_mode_and_chat_roles_are_covered():
    roles = set(load_config().roundtable.plain_talk.roles)
    assert {"chat", "chat_dm", "chat_work", "life_chat", "moment_post"} <= roles
    assert {"answer", "revise", "merge", "work_write", "office_handle"} <= roles


# --- 配置校验 --------------------------------------------------------------------


def test_rule_validation():
    with pytest.raises(ValidationError, match="正则写错了"):
        PlainTalkRule(name="x", problem="p", kind="phrase", patterns=["("])
    with pytest.raises(ValidationError, match="至少要有一条 patterns"):
        PlainTalkRule(name="x", problem="p", kind="ending")
    with pytest.raises(ValidationError, match="只能是一个字"):
        PlainTalkRule(name="x", problem="p", kind="parallel", heads=["是的"])
    rule = PlainTalkRule(name="x", problem="p", kind="phrase", patterns=["a"])
    with pytest.raises(ValidationError, match="不能重复"):
        PlainTalkCheck(rules=[rule, rule])


def test_roles_must_have_prompt_versions():
    rt = load_config().roundtable
    bad = rt.plain_talk.model_copy(update={"roles": ["no_such_role"]})
    with pytest.raises(ValidationError, match="no_such_role"):
        type(rt).model_validate(rt.model_dump() | {"plain_talk": bad.model_dump()})


def test_describe_lists_problems():
    from roundtable.core.plain_talk import describe

    assert describe([Hit("a", "问题一", "原文")]) == "- 问题一（原文：原文）"


# --- 马克杯警戒线与软感悟（2026-10-29 用户：能印在马克杯上的话一律不说） ----------------


@pytest.mark.parametrize(
    "text",
    [
        "生活总会在不经意间给你惊喜。",
        "每个人都值得被温柔以待。",
        "慢慢来，一切都会好的。",
        "今天跑完五公里。说到底，坚持才是最好的答案。",
    ],
)
def test_mug_lines_are_caught(text):
    assert "mug" in rules_hit(text)


@pytest.mark.parametrize(
    "text",
    [
        "这部剧真的值得一看，第三集反转很漂亮。",  # 有具体所指
        "我先去收盘子，今晚星星看不看得到，就看云给不给面子。",
        "本狐的版本来了：叶子留着当耳朵边上的装饰，眼睛橙黄圆睁，手里捧一本小书。",
        "走两圈差不多了，先把机器关了。天台那边云要是还厚，我就当免费吹个风，星星随缘吧。",
    ],
)
def test_concrete_talk_is_left_alone(text):
    assert rules_hit(text) == []


# 用户电脑上真实的朋友圈和评论（2026-10-29）：结尾补感悟的都要拦下
@pytest.mark.parametrize(
    "text",
    [
        "星期四的办公室安安静静，待命中。没什么事做的时候才发现，原来能坐着发发呆也是种休息。",
        "周三上午的办公室好安静，没什么事，就坐着发呆。这种什么都不用赶的上午，意外地挺舒服。",
        "办公室安静的时候确实容易走神，不过放空一下也挺治愈的。",
        "周三能有这种放空的时间太难得了，好好享受。",
        "这会儿安安静静等活儿，觉得周二就这么平平淡淡地过，也挺好。",
        "一点过后的办公室格外安静，暂时没有新任务。这种待命发呆的午后也挺舒服的。",
    ],
)
def test_soft_epiphany_endings_from_real_posts(text):
    assert {"soft_epiphany", "mug"} & set(rules_hit(text))


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("好问题！这个其实是缓存的锅。", "assistant_voice"),
        ("如果还有其他问题，随时问我哦。", "assistant_voice"),
        ("作为一个AI，我觉得这个方案可行。", "assistant_voice"),
    ],
)
def test_assistant_voice(text, rule):
    assert rule in rules_hit(text)


def test_mug_rule_validation():
    rule = PlainTalkRule(name="m", problem="p", kind="mug", patterns=["人生"], concrete=["\\d"])
    assert rule.min_hits == 2 and rule.max_chars == 40
    with pytest.raises(ValidationError, match="正则写错了"):
        PlainTalkRule(name="m", problem="p", kind="mug", patterns=["a"], concrete=["("])


# --- 连发的几条消息合起来查 -------------------------------------------------------


async def test_bubbles_are_checked_together_and_split_back():
    env = Env()
    # 每条单独都短于下限，合起来才看得出结尾是一句马克杯金句
    env.fake.queue("b1", "刚吃完饭\n准备上楼")
    out = await before_speaking_all(
        env.rt,
        ["刚吃完饭", "准备上楼", "慢慢来一切都会好的"],
        room="main",
        name="阿一",
        model_id="b1",
    )
    assert out == ["刚吃完饭", "准备上楼"]  # 改写后整条删掉的就少发一条
    assert "刚吃完饭\n准备上楼\n慢慢来一切都会好的" in env.fake.calls[-1].messages[-1].content


async def test_rewrite_never_adds_bubbles():
    env = Env()
    env.fake.queue("b1", "一\n二\n三")
    out = await before_speaking_all(
        env.rt, ["刚吃完饭", "慢慢来一切都会好的"], room="main", name="阿一", model_id="b1"
    )
    assert out == ["一", "二 三"]


async def test_clean_bubbles_cost_nothing():
    env = Env()
    texts = ["刚吃完饭", "准备上楼看星星"]
    assert (
        await before_speaking_all(env.rt, texts, room="main", name="阿一", model_id="b1") == texts
    )
    assert not env.fake.calls


async def test_chat_with_several_bubbles_is_rewritten_as_a_whole():
    env = Env()
    dm = ChatHub(env.rt).room(dm_room("V1"))
    env.fake.queue("b1", "刚吃完饭\n\n准备上楼\n\n慢慢来一切都会好的", "刚吃完饭\n准备上楼")
    msg, speakers = dm.post("在干嘛")
    await dm.replies(msg["id"], speakers)
    said = [m["text"] for m in env.rt.repo.chat_messages(dm_room("V1")) if m["author"] == "V1"]
    assert "慢慢来一切都会好的" not in said and said[:2] == ["刚吃完饭", "准备上楼"]
