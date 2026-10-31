"""AI 腔体检：至少 4 位一起高频、你本人很少说的片段才算；常用词、招牌、梗、你放行的不算；
只看生活群、私聊、成员互聊（工作群不算）；长片段盖住短片段；每周存起来、说出口前删掉那一小句；
整条只有它时照发；关掉不动；生活自检周报里写一行；客服腔规则表。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.chat import ROOM, WORK, ChatHub, dm_room, ensure_default_rooms
from roundtable.core.config import load_config
from roundtable.core.member.lingo import Lingo
from roundtable.core.member.slop import ACTIVE_KEY, Slop, grams, strip_phrases
from roundtable.core.plain_talk import check

from ..orchestrator.conftest import Env

START = datetime(2026, 10, 19, tzinfo=UTC)  # 周一
END = START + timedelta(days=7)
MEMBERS = ("V1", "V2", "V3", "V4")


def setup(**rules) -> Env:
    env = Env()
    if rules:
        cfg = env.rt.config.roundtable
        engine = cfg.chat.engine.model_copy(
            update={"slop": cfg.chat.engine.slop.model_copy(update=rules)}
        )
        chat = cfg.chat.model_copy(update={"engine": engine})
        env.rt.config = env.rt.config.model_copy(
            update={"roundtable": cfg.model_copy(update={"chat": chat})}
        )
    ensure_default_rooms(env.rt)
    return env


def say(env: Env, author: str, text: str, room: str = ROOM, day: int = 1) -> None:
    at = (START + timedelta(days=day, minutes=len(text))).isoformat()
    env.rt.repo.add_chat_message(room, author, text, created_at=at)


FILLER = "春夏秋冬梅兰竹菊琴棋书画山川湖海风花雪月金木水火土日星辰云雨雷电"


def chatter(env: Env, members=MEMBERS, line="说真的，{x}，抱抱你", room=ROOM, times=4):
    for k, m in enumerate(members):
        for i in range(times):
            n = k * times + i
            x = FILLER[n % len(FILLER)] + "某" + FILLER[(n * 7 + 3) % len(FILLER)]
            say(env, m, line.format(x=x), room=room, day=i % 6)
        say(env, m, "下雨了我在家煮面吃，盐放多了有点咸", room=room)


def phrases(env: Env) -> list[str]:
    return [f["phrase"] for f in Slop(env.rt).scan(START, END)]


def test_grams_and_strip():
    assert grams("说真的ok抱抱") == {"说真", "真的", "说真的", "抱抱"}
    assert strip_phrases("说真的，今天好累啊", ["说真的"]) == "今天好累啊"
    assert strip_phrases("今天好累啊，抱抱你\n明天见", ["抱抱"]) == "今天好累啊，\n明天见"
    assert strip_phrases("抱抱", ["抱抱"]) == "抱抱"  # 整条只有它：照发
    assert strip_phrases("今天好累", ["抱抱"]) == "今天好累"


def test_phrases_many_members_overuse_and_you_do_not_are_found():
    env = setup()
    chatter(env)
    found = phrases(env)
    assert "说真的" in found and "抱抱你" in found
    assert "真的" not in found and "说真" not in found  # 常用词 / 被长片段盖住
    assert "煮面" not in found  # 只说了一次
    (hit,) = [f for f in Slop(env.rt).scan(START, END) if f["phrase"] == "说真的"]
    assert hit["members"] == list(MEMBERS) and hit["user_rate"] == 0


def test_three_members_work_room_or_your_own_habit_are_not_slop():
    env = setup()
    chatter(env, members=MEMBERS[:3])
    assert phrases(env) == []
    env = setup()
    chatter(env, room=WORK)
    assert phrases(env) == []  # 工作群不算
    env = setup()
    ChatHub(env.rt).room(dm_room("V1"))
    chatter(env)
    for _ in range(80):
        say(env, "user", "说真的我也这么觉得", room=dm_room("V1"))
    found = phrases(env)
    assert "说真的" not in found and "抱抱你" in found  # 你自己也常说：不算


def test_allow_list_signatures_and_memes_are_protected():
    env = setup(allow=["抱抱"])
    chatter(env)
    assert not any("抱抱" in p for p in phrases(env))
    env = setup()
    sig = next(iter(env.rt.config.personas.signatures.values()), None)
    if sig is not None and sig.words:
        chatter(env, line=sig.words[0] + "，{x}")
        assert not any(sig.words[0] in p or p in sig.words[0] for p in phrases(env))


def test_weekly_stores_the_top_phrases_and_finish_strips_them():
    env = setup(max_active=1)
    chatter(env)
    top = Slop(env.rt).weekly(START, END)
    assert len(top) == 1 and env.rt.repo.setting(ACTIVE_KEY)
    active = Slop(env.rt).active()
    assert active == [top[0]["phrase"]]
    out = Lingo(env.rt).finish("V1", f"{active[0]}，今天下雨了好冷")
    assert active[0] not in "".join(out) and "下雨" in "".join(out)


def test_switched_off_does_nothing():
    env = setup(enabled=False)
    chatter(env)
    assert Slop(env.rt).weekly(START, END) == [] and env.rt.repo.setting(ACTIVE_KEY) is None
    env.rt.repo.set_setting(ACTIVE_KEY, '{"phrases": ["说真的"]}')
    assert Slop(env.rt).active() == []
    assert Lingo(env.rt).finish("V1", "说真的，今天下雨了") == ["说真的，今天下雨了"]


def test_weekly_life_report_mentions_it():
    from ..test_life import engine

    env = setup()
    chatter(env)
    life = engine(env)
    names = {m.id: m.name for m in life.hub.everyone()}
    report = life.guard.weekly(END + timedelta(hours=9), names)
    assert report and any("说真的" in f["phrase"] for f in report["slop"])
    assert any("大家一起说多了" in n for n in report["notes"])
    assert "一切正常" not in report["notes"]


def test_service_tone_rule():
    rules = load_config().roundtable.plain_talk.check

    def hit(text: str) -> bool:
        return "service_tone" in [h.rule for h in check(text, rules)]

    for text in (
        "谢谢你的分享，这个视频好好笑",
        "别怕，我会一直在这里的",
        "有什么事都可以跟我说哦",
        "这是一个很好的问题诶，我想想",
        "累了就歇会儿，我随时都在哦～",
    ):
        assert hit(text), text
    for text in (  # 不该动的
        "谢谢你哈，晚上请你吃饭",
        "我一直在这里等你下班呢，你快点",
        "我一直在想这事儿，越想越气",
        "这个问题挺好玩的，我也不知道",
    ):
        assert not hit(text), text
