"""说话的样子：看懂语境（心里明白、不说破）；万能短回复程序直接发、不调模型；书面词换口语、
解释和复述删掉；标点、笑法、颜文字各人不同；偶尔打错字再改口；语气跟着状态和对象变；熟了换叫法；
跟在乎的人学说话、一阵不用又忘；没有人物卡、工作群、关心时段都原样说话。"""

from __future__ import annotations

import random
from datetime import timedelta

from roundtable.core.chat import ChatHub, dm_room
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.lingo import Gist, Lingo
from roundtable.core.member.love import USER, Heart
from roundtable.core.member.profile import ProfileStore
from roundtable.core.storage.repository import iso

from .test_brain import NOON, TRAITS, setup
from .test_love import bond

NAMES = {"V1": "小一", "V2": "小二", "V3": "小三"}
RNG = random.Random(0)


def _lingo(env, **update) -> None:
    cfg = env.rt.config.roundtable
    lingo = cfg.chat.engine.lingo.model_copy(update=update)
    engine = cfg.chat.engine.model_copy(update={"lingo": lingo})
    chat = cfg.chat.model_copy(update={"engine": engine})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )


def card(env, member, scores=(3, 3, 3, 3, 3), style=None) -> None:
    data = {"personality": {"五项": dict(zip(TRAITS, scores, strict=True))}}
    if style is not None:
        data["style"] = style
    ProfileStore(env.rt).change(member, data, author="user")


def world(**update):
    env, _ = setup()
    for m in ("V2", "V3"):
        card(env, m)
    if update:
        _lingo(env, **update)
    return env, Lingo(env.rt, names=NAMES)


def always(env, kind: str, **fields) -> None:
    """让某种语境一定只回短的（测试用）。"""
    rules = env.rt.config.roundtable.chat.engine.lingo
    gist = [g.model_copy(update=fields) if g.kind == kind else g for g in rules.gist]
    _lingo(env, gist=gist)


# --- 看懂语境 ---------------------------------------------------------------------


def test_reads_what_kind_of_message_it_is():
    env, lingo = world()
    assert lingo.gist("", {"media": [{"kind": "video"}]}).kind == "video"
    assert lingo.gist("看这个 https://b23.tv/x").kind == "video"
    assert lingo.gist("[表情:团子·开心]").kind == "sticker"
    assert lingo.gist("今天真的累死了不过[表情:团子·开心]").kind != "sticker"
    assert lingo.gist("这老师也太离谱了吧").kind == "complain"
    assert lingo.gist("我上岸了！！").kind == "good_news"
    assert lingo.gist("晚上吃了碗面").kind == "chat"
    notice = lingo.notice(lingo.gist("", {"media": [{"kind": "video"}]}))
    assert "别说出来" in notice and "不解释" in notice
    assert lingo.notice(lingo.gist("晚上吃了碗面")) == ""


# --- 万能短回复 -------------------------------------------------------------------


def test_short_replies_are_her_own_and_only_when_she_has_a_card():
    env, lingo = world()
    video = Gist("video", "分享了一个视频", quick=1.0, pools=("funny", "shock"))
    word = lingo.quick("V2", video, energy=0.6, care=0.0, quiet=False, rng=random.Random(1))
    mine = {w for p in ("funny", "shock") for w in lingo.habits("V2").shorts[p]}
    assert word in mine or word.startswith(("哈", "h", "2", "嘿")) or word in ("笑死", "呵")
    nobody = Lingo(env.rt, names=NAMES)
    assert nobody.quick("V9", video, energy=0.6, care=0.0, quiet=False, rng=RNG) == ""
    calm = Gist("question", "在问你事")
    assert lingo.quick("V2", calm, energy=0.6, care=0.0, quiet=False, rng=RNG) == ""


def test_tired_people_reply_shorter_more_often():
    env, lingo = world()
    g = Gist("video", "分享了一个视频", quick=0.3, pools=("funny",))

    def count(energy):
        rngs = (random.Random(i) for i in range(300))
        return sum(
            bool(lingo.quick("V2", g, energy=energy, care=0.0, quiet=False, rng=r)) for r in rngs
        )

    assert count(0.1) > count(0.8)


async def test_a_quick_reply_is_posted_without_calling_the_model():
    env, _ = setup()
    always(env, "video", quick=1.0, sticker=0.0)
    dm = ChatHub(env.rt).room(dm_room("V1"))
    before, said = len(env.fake.calls), []
    for i in range(6):
        msg, speakers = dm.post(f"[视频] {i}", meta={"media": [{"kind": "video", "id": str(i)}]})
        said += await dm.replies(msg["id"], speakers)
    quick = [m for m in said if m["cost_usd"] == 0 and len(m["text"]) <= 8]
    assert quick and len(env.fake.calls) - before == len(said) - len(quick)


# --- 发出前收尾 -------------------------------------------------------------------


def test_bookish_words_become_spoken_and_explanations_and_recaps_go():
    env, lingo = world(punct_chance=0.0)
    out = lingo.colloquial("笑死，好笑的是他最后摔了一跤，这个非常经典")
    assert out == "笑死，这个超经典"
    asked = "我今天在食堂排队排了半个小时"
    assert lingo.colloquial("啊这，在食堂排队排了半个小时，太惨了", asked) == "啊这，太惨了"
    assert lingo.colloquial("好笑的是这个") == "好笑的是这个"  # 删光了就留原话


def test_punctuation_and_laughs_follow_her_habits():
    env, lingo = world(punct_chance=1.0)
    card(env, "V2", style={"说话风格": "想事情时会打省略号"})
    assert lingo.habits("V2").punct == "ellipsis"
    rng = random.Random(0)
    assert lingo.finish("V2", "我觉得还行。", rng=rng)[0] == "我觉得还行……"
    assert lingo.finish("V2", "你吃了吗？", rng=rng)[0] == "你吃了吗？"  # 问号不动
    assert lingo.finish("V2", "[表情:团子·开心]", rng=rng)[0] == "[表情:团子·开心]"
    assert lingo.finish("V2", "6", rng=rng)[0] == "6"  # 一两个字的短回复不加标点
    laugh = lingo.habits("V2").laugh
    said = lingo.finish("V2", "哈哈哈哈哈太好笑了", rng=rng)[0]
    pick = env.rt.config.roundtable.chat.engine.lingo.laughs[laugh]
    assert said.startswith(pick.fixed or (pick.head + pick.unit * pick.min))


def test_a_bare_correction_at_the_very_start_is_dropped():
    env, lingo = world(punct_chance=0.0)
    card(env, "V2")
    rng = random.Random(0)
    assert lingo.finish("V2", "不对，是……再过3天篝火夜", rng=rng)[0] == "再过3天篝火夜"
    kept = "周四吧，不对，是周五"
    assert lingo.finish("V2", kept, rng=rng)[0] == kept  # 前面真说了什么才改口的不动


def test_habits_differ_between_members_but_are_stable():
    env, lingo = world()
    for i, scores in enumerate(
        [(1, 5, 3, 2, 2), (5, 2, 4, 4, 5), (3, 3, 5, 5, 3), (2, 4, 2, 1, 4)]
    ):
        card(env, f"P{i}", scores)
    lingo = Lingo(env.rt, names={f"P{i}": f"P{i}" for i in range(4)})
    picks = [lingo.habits(f"P{i}") for i in range(4)]
    assert len({h.kaomoji for h in picks}) > 1 and len({h.laugh for h in picks}) > 1
    assert lingo.habits("P0") == Lingo(env.rt, names={f"P{i}": f"P{i}" for i in range(4)}).habits(
        "P0"
    )


def test_a_typo_now_and_then_and_careful_people_correct_it():
    env, _ = world()
    rules = env.rt.config.roundtable.chat.engine.lingo
    _lingo(env, typo=rules.typo.model_copy(update={"chance": 1.0, "fix_base": 1.0}))
    lingo = Lingo(env.rt, names=NAMES)
    out = lingo.finish("V2", "明天再说", rng=random.Random(0))
    assert out == ["明天在说", "*再"]
    _lingo(env, typo=rules.typo.model_copy(update={"chance": 0.0}))
    assert Lingo(env.rt, names=NAMES).finish("V2", "明天再说", rng=random.Random(0)) == ["明天再说"]


def test_no_card_means_no_restyling():
    env, lingo = world()
    assert lingo.finish("V9", "这个视频非常好笑。") == ["这个视频非常好笑。"]
    assert lingo.voice_lines("V9") == []


# --- 语气变调、外号 ---------------------------------------------------------------


def test_tone_shifts_with_her_state_and_with_who_she_talks_to():
    env, lingo = world()
    kw = {"mood": 0.0, "care": 0.3, "dislike": 0.0, "at": NOON, "rng": RNG}
    assert "少打" in lingo.card_line("V2", USER, energy=0.1, **kw)
    assert "回得短" in lingo.card_line("V2", USER, energy=0.6, **(kw | {"mood": -0.6}))
    assert "客气" in lingo.card_line("V2", USER, energy=0.6, **(kw | {"care": 0.05}))
    assert "随便" in lingo.card_line("V2", USER, energy=0.6, **(kw | {"care": 0.7}))
    assert "斗图" in lingo.card_line("V2", "V3", energy=0.6, battle=True, **kw)


def test_close_friends_get_a_nickname_from_her_card_and_it_sticks():
    env, _ = world()
    _lingo(
        env, nick=env.rt.config.roundtable.chat.engine.lingo.nick.model_copy(update={"chance": 1.0})
    )
    card(env, "V2", style={"说话风格": "对张三叫“张三”，熟了以后可能会叫“三哥”或“三三”。"})
    lingo = Lingo(env.rt, names=NAMES)
    assert lingo.nickname("V2", USER, 0.2, NOON, RNG) == ""  # 还不熟
    nick = lingo.nickname("V2", USER, 0.7, NOON, random.Random(3))
    assert nick in ("三哥", "三三")
    assert lingo.nickname("V2", USER, 0.7, NOON + timedelta(days=5), random.Random(9)) == nick
    assert lingo.nicks("V2") == {USER: nick}


# --- 跟人学 -----------------------------------------------------------------------


def _say(env, bus, room, author, text, at):
    row = env.rt.repo.add_chat_message(room, author, text, created_at=iso(at))
    who = None if author == "user" else author
    bus.publish(
        Event("chat.message", at, who, {"room": room, "id": row["id"], "from_user": who is None})
    )


def test_picks_up_words_from_people_she_cares_about_and_forgets_unused_ones():
    env, _ = world()
    learn = env.rt.config.roundtable.chat.engine.lingo.learn.model_copy(update={"chance": 1.0})
    _lingo(env, learn=learn)
    env.rt.repo.ensure_chat_room("dm:V2", kind="dm", title="", members=["V2"])
    bus = EventBus(env.rt)
    lingo = Lingo(env.rt, bus, NAMES)
    word = next(w for w in learn.slang if w not in Lingo(env.rt, names=NAMES)._own("V2"))
    for i in range(3):
        _say(env, bus, "dm:V2", "user", f"{word}，今天又迟到了", NOON + timedelta(minutes=i))
    assert lingo.learned("V2", NOON) == []  # 不在乎的人说的不学
    bond(Heart(env.rt, names=NAMES), "V2", USER, care=0.6)
    for i in range(3):
        _say(env, bus, "dm:V2", "user", f"{word}，好困", NOON + timedelta(hours=1, minutes=i))
    assert (word, USER) in lingo.learned("V2", NOON + timedelta(hours=2))
    assert any(word in line and "跟用户学的" in line for line in lingo.voice_lines("V2"))
    assert lingo.learned("V2", NOON + timedelta(days=40)) == []  # 一个多月没用，忘了


def test_work_rooms_teach_nothing():
    env, _ = world()
    _lingo(
        env,
        learn=env.rt.config.roundtable.chat.engine.lingo.learn.model_copy(update={"chance": 1.0}),
    )
    env.rt.repo.ensure_chat_room("work", kind="work", title="", members=["V2"])
    bus = EventBus(env.rt)
    lingo = Lingo(env.rt, bus, NAMES)
    bond(Heart(env.rt, names=NAMES), "V2", USER, care=0.9)
    for i in range(5):
        _say(env, bus, "work", "user", "绝了绝了", NOON + timedelta(minutes=i))
    assert lingo.learned("V2", NOON) == []


# --- 接进意图卡和聊天 -------------------------------------------------------------


def test_the_card_says_what_she_understood_without_saying_it():
    env, _ = setup()
    from roundtable.core.member.brain import Brain

    got = Brain(env.rt, names=NAMES).intent(
        "V1", last="[视频]", group=False, at=NOON, room="dm:V1", meta={"media": [{"kind": "video"}]}
    )
    assert "gist:video" in got.tags and "别说出来" in got.text and "不解释" in got.text


async def test_care_mode_and_work_rooms_are_left_as_they_are():
    env, _ = setup()
    rules = env.rt.config.roundtable.chat.engine.lingo
    _lingo(env, typo=rules.typo.model_copy(update={"chance": 1.0, "fix_base": 1.0}))
    hub = ChatHub(env.rt)
    dm = hub.room(dm_room("V1"))
    member = next(m for m in dm.members() if m.id == "V1")
    assert len(dm._finish(member, "明天再说", 0)) == 2
    from roundtable.core.member.care import Care

    Care(env.rt).note(dm.room, "最近真的撑不下去了", NOON)
    assert dm._finish(member, "明天再说", 0) == ["明天再说"]


def test_teasing_and_being_lumped_with_any_ai_are_read_but_a_serious_question_is_not():
    env, lingo = world()
    tease = lingo.gist("你们是不是人机啊，回这么快")
    assert tease.kind == "tease" and "压力一个AI的来了" in tease.react
    assert lingo.gist("反正AI都一样，换个AI聊也行").kind == "compare"
    assert lingo.gist("认真问一句，你是不是AI？别开玩笑").kind not in ("tease", "compare")


def test_new_prompts_talk_like_texting_and_are_sure_of_who_they_are():
    from roundtable.core.prompts import PromptLibrary

    lib, cfg = PromptLibrary(), setup()[0].rt.config.roundtable.prompts
    for role in ("chat", "chat_dm", "life_chat"):
        text = lib.get(role, cfg[role]).system
        assert "先给反应" in text and "不解释" in text and "不复述" in text
        assert "独一无二" in text and "如实承认" in text and "压力一个 AI 的来了" in text


def test_hard_moments_and_goodbyes_never_get_a_canned_short_reply():
    env, lingo = world()
    for text in ("最近真的撑不下去了，有时候觉得不想活了", "困了，我去睡了，晚安", "我回来啦"):
        gist = lingo.gist(text)
        assert gist.kind in ("crisis", "bye", "back") and not gist.quick and not gist.sticker
    assert lingo.gist("不想活了 哈哈哈").kind == "crisis"  # 比好笑的事先看
    assert "别留" in lingo.gist("今晚约了大学室友吃饭，先不聊啦").react
    assert lingo.gist("晚安是不可能晚安的，睡不着").kind != "bye"


def test_real_stress_and_everyday_words_are_not_read_as_teasing():
    env, lingo = world()
    assert lingo.gist("最近压力好大，组会好难").kind == "emo"
    assert lingo.gist("我家扫地机器人又卡住了").kind != "tease"
    assert lingo.gist("我请好假了，下周回家").kind != "tease"
    assert lingo.gist("压力一下你们，今天谁最懒").kind == "tease"
    assert lingo.gist("今天好难过了").kind == "emo"
    assert lingo.gist("我错过了末班车").kind != "good_news"
    assert lingo.gist("买了一盒草莓").kind != "funny"
    assert lingo.gist("考试终于过了！").kind == "good_news"


def test_other_members_voice_marks_are_swapped_back_to_her_own():
    env, lingo = world()
    card(env, "V2", style={"口头禅": ["先说结论。"], "自称": "本狐"})
    card(env, "V3", style={"口头禅": ["这笔先记着。"], "自称": "我"})
    theirs = lingo.habits("V2").kaomoji[0]
    mine = set(lingo.habits("V3").kaomoji)
    out = lingo.own_voice("V3", f"先说结论，本狐觉得可以 {theirs}\n明天见", random.Random(1))
    assert "先说结论" not in out and "本狐" not in out and theirs not in out or theirs in mine
    assert "明天见" in out and "我觉得可以" in out
    assert lingo.own_voice("V2", "先说结论，本狐觉得可以", RNG) == "先说结论，本狐觉得可以"
    # 跟人学过的不算串味；整句只有那个口头禅时不删光
    data = lingo._load("V3")
    data["learned"]["先说结论"] = {"from": "V2", "at": iso(NOON), "used": ""}
    lingo._save("V3", data)
    env.rt.budget.now = lambda: NOON
    assert "先说结论" in lingo.own_voice("V3", "先说结论，可以", RNG)
    assert lingo.own_voice("V1", "这笔先记着。", RNG) == "这笔先记着。"
    _lingo(env, own_voice=False)
    assert lingo.own_voice("V3", "本狐觉得", RNG) == "本狐觉得"


def test_her_own_approved_stickers_come_first(monkeypatch):
    import roundtable.core.chat_actions as acts

    env, lingo = world()
    general = lingo.sticker_pack("V2")
    assert general is None or general[0] in env.rt.config.roundtable.chat.actions.stickers
    monkeypatch.setattr(
        acts, "own_stickers", lambda rt, m, name: {name: ["挂账"]} if m == "V2" else {}
    )
    assert lingo.sticker_pack("V2") == ("小二", ["挂账"])
    assert any("「小二」那套" in line for line in lingo.voice_lines("V2"))
    assert lingo.sticker_pack("V3") == general or lingo.sticker_pack("V3")[0] != "小三"


# --- 心情别直说 -------------------------------------------------------------------


def test_feelings_are_shown_with_her_own_faces_and_marks_not_named():
    env, lingo = world()
    card(env, "V1", (5, 3, 3, 5, 3))  # 外向又温和：分到元气、温柔的颜文字
    mine = set(lingo.kaomoji("V1"))
    rules = env.rt.config.roundtable.chat.engine.lingo.show
    happy = set(rules.feelings["开心"].marks)
    for seed in range(12):
        rng = random.Random(seed)
        out = lingo.show("V1", "好开心啊！", rng)
        assert "开心" not in out and (out in mine or out in happy)
        both = lingo.show("V1", "今天被老师夸了，太开心了", rng)
        assert both.startswith("今天被老师夸了") and "开心" not in both
        meh = lingo.show("V1", "这也太离谱了吧无语了", rng)
        assert meh.startswith("这也太离谱了吧") and "无语" not in meh
    assert lingo.show("V1", "我们去吃火锅吧", RNG) == "我们去吃火锅吧"
    others = {k for m in ("V2", "V3") for k in lingo.kaomoji(m)} - mine
    for seed in range(12):  # 只用她自己分到的颜文字
        assert not any(k in lingo.show("V1", "我无语了", random.Random(seed)) for k in others)


def test_showing_can_be_switched_off():
    env, lingo = world()
    card(env, "V1")
    rules = env.rt.config.roundtable.chat.engine.lingo.show.model_copy(update={"enabled": False})
    _lingo(env, show=rules)
    assert Lingo(env.rt, names=NAMES).show("V1", "好开心", RNG) == "好开心"


def test_dropping_someone_elses_catchphrase_keeps_the_rest_of_the_clause():
    assert Lingo._drop_clause("确实，忘了诶 让我想想……", "让我想想").startswith("确实，忘了诶")
    assert Lingo._drop_clause("好呀，让我想想", "让我想想") == "好呀"
