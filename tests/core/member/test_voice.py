"""P5 表达：从档案抽说话习惯（8 人各不相同）、说话模式档位、说出口前自查；只用于生活群和私聊。"""

from __future__ import annotations

import json
import random
from itertools import combinations

from roundtable.core.chat import GroupChat
from roundtable.core.member.engine import MemberEngine
from roundtable.core.member.voice import (
    Said,
    Voice,
    VoicePolicy,
    should_drop,
    voice_hits,
    voice_of,
    voice_text,
)
from roundtable.core.moments import similarity
from roundtable.core.service import RoundtableService

from ..orchestrator.conftest import Env

# 八种写法不同的「说话风格」章（测试自编，不出现任何模型、厂商名）
STYLES = [
    "以短中句为主。语气词常用“嗯”“诶”。\n**口头禅：**\n- “让我想想。”\n- “先留一页。”\n"
    "**常用表情**：🙂、✨。\n**自称**：我。\n**示范：**\n- **打招呼**：“早，电梯修好了。”",
    "- **句子**：中等长度，先回应再展开。\n- **语气词**：「嗯」「呢」。\n- **口头禅**（3 个）："
    "「我想想看。」「这笔先记着。」\n- **常用表情**：🍊 📖",
    "- **句子长短**：日常偏短、跳跃。\n- **语气词**：哈…、唔、欸（只在撒娇时）\n"
    "- **常用表情（≤3）**：🌀、🐱\n- **示范对话**：\n  - **开心**：「真的假的？！知音啊！」",
    "句子长短：中等偏短。先说重点。\n语气词：嗯、这样、来。\n口头禅：我先记一笔；先问清楚；不急。\n"
    "常用表情：🪭、📒。\n开玩笑：\n“这句我先记进待查。”",
    "句子偏短。语气词就三个：呵、行、得了。口头禅只留三句：「说完了？」「不想听就拦。」"
    "表情少用：哼、🦇。\n打招呼：「哟，新来的？话直。」",
    "**句子长短**：中等偏长。想事情时先停顿。\n**语气词**：嗯、诶、嘛。\n**口头禅**：\n"
    "1. “诶，这个有意思。”\n**常用表情**：🐳、🍚\n- **打招呼**：“嗯，你来啦。吃饭了吗？”",
    "句子偏短，语气平和，熟了以后多用“嗯、呢、呀”。\n- **口头禅**：“慢慢来。”\n"
    "- **常用表情**：🌙、🍵\n- **开玩笑**：“别催，我是未读先困。”",
    "句子偏短，节奏慢。语气词常用「这个嘛」「……大概吧」。口头禅：「本狐只知道这些」「先记下来」。"
    "表情：🦊 ☕。\n1. **打招呼**：「嗯，早。……茶刚泡的。」",
]
SCORES = [3, 2, 3, 3, 5, 3, 2, 2]  # 外向分


def profile(i: int) -> dict:
    return {
        "style": {"说话风格": STYLES[i]},
        "personality": {"五项": {"外向": SCORES[i], "幽默": 1 + i % 5}},
    }


def rules(env: Env | None = None):
    return (env or Env()).config.roundtable.chat.engine.voice


# --- 抽说话习惯 -----------------------------------------------------------------


def test_extracts_habits_from_different_writing_styles():
    r = rules()
    v = [voice_of(profile(i), r) for i in range(8)]
    assert v[0].length == "短" and v[0].particles == ("嗯", "诶") and v[0].emojis == ("🙂", "✨")
    assert v[0].samples == ("早，电梯修好了。",)
    assert v[1].length == "中" and v[1].catchphrases == ("我想想看。", "这笔先记着。")
    assert v[2].particles == ("哈…", "唔", "欸")
    assert v[3].particles == ("嗯", "这样", "来") and v[3].samples == ("这句我先记进待查。",)
    assert v[4].particles == ("呵", "行", "得了") and v[4].emojis == ("🦇",)
    assert v[5].length == "长"
    assert v[6].particles == ("嗯", "呢", "呀")  # 没写「语气词」三个字
    assert v[7].catchphrases == ("本狐只知道这些", "先记下来")  # 和语气词同一段也不混
    assert Voice().empty and voice_of({}, r).empty


def test_mode_follows_extroversion_and_user_can_pick():
    r = rules()
    assert voice_of(profile(1), r).mode == "quiet"
    assert voice_of(profile(0), r).mode == "normal"
    assert voice_of(profile(4), r).mode == "chatty"
    picked = profile(4) | {"style": {"说话风格": STYLES[4], "说话模式": "quiet"}}
    assert voice_of(picked, r).mode == "quiet"


def test_eight_members_get_eight_different_habits_within_the_same_budget():
    r = rules()
    texts = [voice_text(voice_of(profile(i), r), r, "group", seed=1) for i in range(8)]
    assert all(0 < len(t) <= r.budget_chars for t in texts)
    assert len(set(texts)) == 8
    for a, b in combinations(texts, 2):
        assert similarity(a, b) < 0.75, (a, b)


def test_samples_rotate_and_group_only_lines():
    r = rules()
    v = Voice(samples=("一句甲", "一句乙", "一句丙", "一句丁"), length="短", mode="quiet")
    picks = {voice_text(v, r, "group", seed=s).splitlines()[-1] for s in range(10)}
    assert len(picks) > 1
    assert "接话" in voice_text(v, r, "group") and "接话" not in voice_text(v, r, "dm")


# --- 说出口前自查 -----------------------------------------------------------------


def test_self_check_rules():
    c = rules().check
    v = Voice(catchphrases=("让我想想。",))
    said = Said(
        own=("今天的晚霞粉得像草莓牛奶一样", "让我想想。周末去哪"),
        others=(("阿二", "火锅吧，天冷吃火锅最舒服了"),),
    )
    rule = lambda t: {h.rule for h in voice_hits(t, said, v, c)}  # noqa: E731
    assert rule("今天的晚霞粉得像草莓牛奶") == {"repeat"}
    assert rule("火锅吧，天冷吃火锅最舒服") == {"echo"}
    assert rule("说得对！我也想") == {"agree"}
    assert rule("说得对") == {"agree"}  # 很短也查
    assert rule("我也觉得火锅好，天冷吃火锅") == {"agree"}  # 附和接龙
    assert voice_hits("我也是", Said(), v, c) == []  # 这一轮没人说过话（私聊回你）：不算附和
    assert "agree" not in rule("确实，不过我更想吃楼下那家新开的烤肉")
    assert rule("让我想想。烤肉也行吧") == {"catchphrase"}
    assert rule("上次我们一起去的那家店不错") == {"memory"}
    has_memory = Said(own=said.own, others=said.others, has_memory=True)
    assert voice_hits("上次我们一起去的那家店不错", has_memory, v, c) == []
    assert voice_hits("[表情:小黄脸·开心]", said, v, c) == []  # 太短不查


def test_drop_only_echo_or_agree_in_group_when_not_named():
    c = rules().check
    hits = voice_hits("说得对", Said(others=(("阿二", "去天台"),)), Voice(), c)
    assert should_drop(hits, "group", named=False)
    assert not should_drop(hits, "group", named=True)
    assert not should_drop(hits, "dm", named=True)


# --- 接进聊天 ---------------------------------------------------------------------


async def no_sleep(_s: float) -> None:
    return None


def setup(env: Env) -> MemberEngine:
    engine = MemberEngine(env.rt, lambda: [])
    for i, mid in enumerate(("V1", "V2", "V3")):
        engine.profiles.change(mid, profile(i), author="user")
    return engine


def room(env: Env, name: str = "main") -> GroupChat:
    policy = VoicePolicy(env.rt)
    return GroupChat(env.rt, name, rng=random.Random(0), policy=lambda: policy, sleep=no_sleep)


async def test_voice_reaches_life_prompts_and_differs_per_member():
    env = Env()
    setup(env)
    r = room(env)
    msg, _ = r.post("@全员 早")
    await r.replies(msg["id"], r.pick("@全员 早"))
    chats = [c for c in env.fake.calls if "能发的东西" in c.messages[0].content]
    voices = [c.messages[-1].content.split("<voice>")[1].split("</voice>")[0] for c in chats]
    habits = [v for v in voices if "说话风格" not in v]  # 没有档案的成员是「没写」
    assert len(habits) == 3 and len(set(habits)) == 3
    assert any("嗯、诶" in v for v in habits)


async def test_work_room_never_gets_voice():
    env = Env()
    setup(env)
    r = room(env, "work")
    msg, _ = r.post("@全员 早")
    await r.replies(msg["id"], r.pick("@全员 早"))
    sent = json.dumps([[m.content for m in c.messages] for c in env.fake.calls], ensure_ascii=False)
    assert env.fake.calls and "嗯、诶" not in sent and "说话习惯" not in sent
    assert VoicePolicy(env.rt).voice("V1", "work") == ""


async def test_echo_in_group_is_not_sent_when_not_named():
    env = Env()
    setup(env)
    r = room(env)
    msg, _ = r.post("今天吃什么")
    env.rt.repo.add_chat_message("main", "V2", "火锅吧，天冷吃火锅最舒服了", reply_to=msg["id"])
    env.fake.queue("b1", "火锅吧，天冷吃火锅最舒服")
    v1 = next(m for m in r.members() if m.id == "V1")
    assert await r.replies(msg["id"], [v1]) == []
    assert not [m for m in env.rt.repo.chat_messages("main") if m["author"] == "V1"]


async def test_named_echo_is_rewritten_once_in_own_voice():
    env = Env()
    setup(env)
    r = room(env)
    msg, _ = r.post("@阿一 今天吃什么")
    env.rt.repo.add_chat_message("main", "V2", "火锅吧，天冷吃火锅最舒服了", reply_to=msg["id"])
    env.fake.queue("b1", "火锅吧，天冷吃火锅最舒服")
    env.fake.queue("b1", "嗯……我想吃烤红薯")
    v1 = next(m for m in r.members() if m.id == "V1")
    await r.replies(msg["id"], [v1])
    said = [m["text"] for m in env.rt.repo.chat_messages("main") if m["author"] == "V1"]
    assert said == ["嗯……我想吃烤红薯"]
    rewrite = env.fake.calls[-1].messages
    assert "和阿二刚说的差不多" in rewrite[-1].content and "嗯、诶" in rewrite[-1].content


def test_quiet_members_split_less_and_pause_longer():
    env = Env()
    setup(env)
    p = VoicePolicy(env.rt)
    assert p.max_segments("V2", "group") < p.max_segments("V1", "group")
    assert p.segment_gap("V2", "x") > p.segment_gap("V1", "x")
    assert p.max_segments("V1", "work") == 1


def test_service_installs_voice_policy():
    assert isinstance(RoundtableService(Env().rt).chats.policy, VoicePolicy)
