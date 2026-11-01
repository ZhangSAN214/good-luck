"""连着几条的分寸：聊天里不发 Markdown 和破折号；「不是而是」改口语；助手话、「摘要」、程序抬头
不说出口；招牌 emoji、「呢」、口头填充词、每轮反问按她最近几条冷却（例子取自小克炒股私聊和第 4 次
真模型试跑）。"""

from __future__ import annotations

import random

from roundtable.core.config.loader import load_config
from roundtable.core.member.cadence import Cadence, faces_in, not_but, plain
from roundtable.core.member.lingo import Lingo

from .test_brain import setup

RULES = load_config().roundtable.chat.engine.lingo.cadence
CAD = Cadence(RULES)


def test_markdown_lists_tables_and_dashes_become_plain_chat():
    text = (
        "**【初始账户】**\n"
        "- 本金 10 万\n"
        "- 仓位 三成\n"
        "---\n"
        "| 日期 | 动作 |\n|---|---|\n| 10-08 | 买银行 |\n"
        "```\nprint(1)\n```\n"
        "我很清楚自己的边界——数据我能分析"
    )
    out = plain(text)
    for bad in ("**", "【", "- ", "---", "|", "```", "——"):
        assert bad not in out
    assert "初始账户：" in out
    assert "10-08，买银行" in out
    assert "边界，数据我能分析" in out


def test_not_x_but_y_drops_the_written_er():
    assert not_but("不是害怕亏钱，而是还没想好策略") == "不是害怕亏钱，是还没想好策略"


def test_assistant_talk_and_feed_words_never_get_said():
    text = "不过我得说实话呢，我的数据更新到今年某个时间点，10 月的我查不了。先按九月的来。"
    out = CAD.prepare(text)
    assert "数据更新" not in out
    assert "先按九月的来" in out
    out = CAD.prepare("可惜摘要只提了房价。这事挺离谱的")
    assert "摘要" not in out and "离谱" in out
    assert CAD.prepare("关于用户的生日我记着") == "关于你的生日我记着"
    assert CAD.prepare("这个功能的用户体验不错") == "这个功能的用户体验不错"


def test_admitting_being_ai_when_asked_is_left_alone():
    assert CAD.prepare("嗯，我是 AI，不过这不妨碍我记得你说过的事") != ""
    assert "我是 AI" in CAD.prepare("嗯，我是 AI，不过这不妨碍我记得你说过的事")


def test_signature_emoji_cools_down_after_two_recent_uses():
    recent = ["在呢。☕", "我也是呢。☕", "怎么了？"]
    assert "☕" not in CAD.after("白天在房间里窝着看书。☕", recent)
    assert "☕" in CAD.after("白天在房间里窝着看书。☕", ["在呢。☕", "怎么了？"])
    assert faces_in("好呀 (｡･ω･｡) 🍊", ["(｡･ω･｡)"]) == ["(｡･ω･｡)", "🍊"]


def test_ne_ending_is_dropped_when_she_has_been_saying_it_all_along():
    recent = ["我也是呢。", "这名字真温柔呢。", "后来下楼打游戏呢。", "嘿嘿"]
    out = CAD.after("小说看得比较多呢。", recent)
    assert out == "小说看得比较多。"
    assert CAD.after("那你呢？", recent) == "那你呢？"  # 问句里的「呢」不动
    assert CAD.after("小说看得比较多呢。", ["在吗", "嗯嗯", "好", "行"]) == "小说看得比较多呢。"


def test_filler_phrase_is_not_repeated_back_to_back():
    recent = ["不过说实话，我还没想好"]
    out = CAD.after("不过说实话，这个思路挺好", recent)
    assert "说实话" not in out and "这个思路挺好" in out


def test_asking_back_every_turn_gets_trimmed_but_a_lone_question_stays():
    recent = ["橙子和橘子也是日常必备。你呢，有特别喜欢的吗？", "还喜欢看书。你呢？"]
    out = CAD.after("还喜欢看理财入门的书。你呢？", recent)
    assert out == "还喜欢看理财入门的书。"
    assert CAD.after("你呢？", recent) == "你呢？"
    assert CAD.after("还喜欢看理财入门的书。你呢？", ["好", "嗯"]) == (
        "还喜欢看理财入门的书。你呢？"
    )


def test_stickers_are_never_dropped_with_a_clause():
    assert "[表情:小克·记着]" in CAD.prepare("摘要说了[表情:小克·记着]")


def test_lingo_finish_cools_against_what_she_said_in_any_room():
    env, _ = setup()
    rt = env.rt
    rt.repo.add_chat_message("main", "V1", "在呢 ☕")
    rt.repo.add_chat_message("dm:V1", "V1", "我也是 ☕")
    out = Lingo(rt, names={"V1": "小一"}).finish(
        "V1", "**白天**在看书——挺好的 ☕", rng=random.Random(1)
    )
    joined = "".join(out)
    assert "☕" not in joined and "**" not in joined and "——" not in joined


def test_goal_names_from_the_program_are_not_quoted_back():
    out = CAD.prepare("本小姐最近在琢磨「有人站得住」，刚开头。今天的酸梅汤不错")
    assert "「有人站得住」" not in out and "酸梅汤" in out


def test_openers_are_her_own_handful_and_cool_down():
    from roundtable.core.member.cadence import openers_of, with_opener

    mine = openers_of(RULES, "V1")
    assert len(mine) == RULES.openers.each and openers_of(RULES, "V1") == mine
    long = "这个思路其实挺有意思的我想先试试看能不能跑通"
    outs = {with_opener(RULES, "V1", long, [], random.Random(i)) for i in range(40)}
    assert any(o != long and o.endswith(long) for o in outs)  # 有时候先组织一下语言
    assert any(o == long for o in outs)  # 不是每次都有
    assert all(
        o == long or o.split("，")[0].rstrip("…") in [w.rstrip("…") for w in mine] for o in outs
    )
    used = [f"{mine[0]}，昨天也这么说"]
    assert all(with_opener(RULES, "V1", long, used, random.Random(i)) == long for i in range(40))
    assert with_opener(RULES, "V1", "好", [], random.Random(0)) == "好"  # 短的不放
