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
        (MAXIM, ["ending_maxim"]),
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
    # 金句在中间、结尾是具体的事：不算金句收尾
    assert rules_hit("说到底，平凡的日子才是最好的答案。不过今天的面确实咸了点，下次少放盐。") == []


def test_hits_carry_problem_and_excerpt():
    (hit,) = check(MAXIM, RULES)
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
    assert seen == [(MAXIM, ["ending_maxim"])]


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
