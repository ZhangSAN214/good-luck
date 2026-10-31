"""各自的情感：起点从人物卡情绪章抽、按她自己的理解分辨难过还是生气、记下真有过的情绪、
说话时带上自己的理解和上回的事、每周成长可以改写（引用真实经历、不照抄别人、隔几周才改）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.chat import ChatHub, dm_room
from roundtable.core.hometz import home_zone
from roundtable.core.member.brain import Brain
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.feel import Feelings, seed_from
from roundtable.core.member.growth import Gate, Ledger, Line, Proposal, Week, build
from roundtable.core.member.growth.gate import fresh_patch
from roundtable.core.member.profile import ProfileStore
from roundtable.core.storage.repository import iso

from ..orchestrator.conftest import Env

AT = datetime(2026, 11, 4, 6, 0, tzinfo=UTC)  # 上海 下午 2 点
CARDS = {
    # 写法各不一样：冒号标签 / 加粗标签 / 小标题 / 「让我…的事：」换行
    "V1": "开心：被准确地夸奖，比一句笼统的真厉害更让我受用。\n生气：别人把客气当同意，催个不停。\n"
    "难过：认真做的东西没达到期待，我会先安静。",
    "V2": "### 开心\n\n尾巴会翘起来，语速变快，嘴上说就这。\n\n### 难过\n\n躲进懒人沙发发呆很久。",
    "V3": "让我生气的事：\n糊涂账、前后不一，越平静越认真生气。\n"
    "**难过时会**：整理袖口，把桌上摆正。\n不会轻易给别人看的一面：我其实很怕被丢下。",
}
TRAITS = {"V1": (3, 5, 3, 2, 3), "V2": (4, 1, 4, 5, 3), "V3": (2, 3, 3, 3, 2)}


def world(at=AT) -> Env:
    env = Env()
    env.rt.budget.now = lambda: at
    env.rt.repo.clock = lambda: iso(at)
    home_zone(env.rt).set("Asia/Shanghai")
    store = ProfileStore(env.rt)
    for m, card in CARDS.items():
        five = dict(zip(("外向", "较真", "好奇", "温和", "幽默"), TRAITS[m], strict=True))
        store.change(m, {"personality": {"情绪": card, "五项": five}}, author="user")
    return env


def test_each_starts_from_her_own_card_in_whatever_form_it_was_written():
    env = world()
    f = Feelings(env.rt)
    v1, v2, v3 = f.seed("V1"), f.seed("V2"), f.seed("V3")
    assert v1["生气"].startswith("别人把客气当同意") and v1["开心"].startswith("被准确地夸奖")
    assert v2["开心"].startswith("尾巴会翘起来") and "懒人沙发" in v2["难过"]
    assert v3["生气"].startswith("糊涂账") and v3["难过"].startswith("整理袖口")
    assert "被丢下" in v3["想念"]  # 卡里没有「想念」这一条：从提到它的那句来
    assert "想念" not in v1 and "生气" not in v2  # 没写的就空着，等经历来告诉她
    assert len({v1["开心"], v2["开心"]}) == 2


def test_long_card_lines_are_cut_but_never_too_short():
    got = seed_from(
        {"personality": {"情绪": "开心：什么都藏不住。" + "猫耳会竖起来尾巴画波浪线" * 10}},
        {"开心": ["开心"]},
        30,
    )
    assert 15 <= len(got["开心"]) <= 30


def test_whether_a_hurt_is_sadness_or_anger_is_her_own_call():
    env = world()
    f = Feelings(env.rt)
    assert f.classify("V1", 0.2, "被夸", "被夸") == "开心"
    assert f.classify("V1", -0.02, "小事", "被批评") == ""  # 心情没怎么动：不算一次情绪
    # 同样被说了几句：较真的那位当成生气，温和的那位当成难过
    assert f.classify("V1", -0.3, "用户说「你也太慢了吧」", "被批评") == "生气"
    assert f.classify("V2", -0.3, "用户说「你也太慢了吧」", "被批评") == "难过"
    # 像她自己理解的生气（被催）：本来偏难过的事也会觉得是生气
    assert f.classify("V1", -0.15, "被人催个不停", "落空") == "生气"


def test_real_feelings_are_recorded_from_reflexes_and_missing_from_gaps():
    env = world()
    bus = EventBus(env.rt)
    f = Feelings(env.rt, bus, {"V1": "阿一", "V2": "阿二"})
    hub = ChatHub(env.rt)
    room = dm_room("V1")
    hub.room(room)
    msg = env.rt.repo.add_chat_message(room, "user", "你也太慢了吧，快点", created_at=iso(AT))
    data = {"name": "被批评", "mood": -0.3, "msg": msg["id"] if isinstance(msg, dict) else msg}
    bus.publish(Event("state.reflex", AT, "V1", data))
    bus.publish(Event("state.reflex", AT, "V1", {"name": "兑现", "mood": 0.12, "why": "盼着的事"}))
    bus.publish(Event("state.reflex", AT, "V1", {"name": "说话", "mood": 0.0}))
    (angry, glad) = f.episodes("V1")
    assert angry["emo"] == "生气" and angry["why"] == "用户说「你也太慢了吧，快点」"
    assert glad == {"emo": "开心", "why": "盼着的事", "at": iso(AT)}
    # 好几天没聊，你又来私聊：想念
    later = AT + timedelta(days=3)
    second = env.rt.repo.add_chat_message(room, "user", "在吗", created_at=iso(later))
    second_id = second["id"] if isinstance(second, dict) else second
    data = {"room": room, "id": second_id, "from_user": True}
    bus.publish(Event("chat.message", later, None, data))
    assert f.episodes("V1", "想念")[-1]["why"] == f.rules.miss_back


def test_the_line_carries_her_understanding_and_the_last_time_it_happened():
    env = world()
    f = Feelings(env.rt)
    assert f.active("V1", mood=0.0, last="今天吃了面", at=AT) == ""
    assert f.active("V1", mood=0.0, last="好久不见，有点想你", at=AT) == "想念"
    f.record("V1", "生气", "被人催了三遍", AT - timedelta(days=1, hours=2))
    assert f.active("V1", mood=-0.5, last="", at=AT) == "生气"  # 心情差：最近那种负面的
    f.record("V1", "开心", "作业被老师夸了", AT - timedelta(hours=1))
    assert f.active("V1", mood=0.0, last="嗯", at=AT) == "开心"  # 刚有过：还在场
    line = f.line("V1", "开心", AT)
    assert line.startswith("你心里的开心：被准确地夸奖") and "上回是刚才，作业被老师夸了" in line
    assert "上回是昨天，被人催了三遍" in f.line("V1", "生气", AT)
    assert f.line("V2", "生气", AT) == ""  # 卡里没写过、也还没经历过：不写空话
    f.record("V2", "生气", "被人插队", AT - timedelta(days=2))
    assert "还说不太清" in f.line("V2", "生气", AT) and "上回是前天，被人插队" in f.line(
        "V2", "生气", AT
    )


def test_the_intent_card_brings_it_up_only_when_the_feeling_is_present():
    env = world()
    brain = Brain(env.rt)
    Feelings(env.rt).record("V1", "开心", "作业被老师夸了", AT - timedelta(minutes=30))
    card = brain.intent("V1", last="在干嘛", speaker=None, named=True, group=False, at=AT)
    assert "你心里的开心" in card.text and "feel" in card.tags
    calm = brain.intent("V2", last="在干嘛", speaker=None, named=True, group=False, at=AT)
    assert "你心里的" not in calm.text


def week_with(member: str, text: str) -> Week:
    line = Line(1, "feel", "2026-11-03", text, False)
    return Week(member, "2026-W45", datetime(2026, 11, 2).date(), [line])


def proposal(emo: str, value: str, quote: str = "等了好久对方终于回来") -> Proposal:
    return Proposal("feel", f"personality.情感.{emo}", value=value, evidence=[1], quote=quote)


def test_growth_can_rewrite_an_understanding_from_a_real_episode():
    env = world()
    gate = Gate(env.rt)
    week = week_with("V1", "想念：等了好久对方终于回来")
    new = "原来想念是一直留着对方那杯茶，等人回来时装作刚泡好"
    others = {m: ProfileStore(env.rt).current(m).data for m in ("V2", "V3")}
    (v,) = gate.check("V1", [proposal("想念", new)], week, Ledger(), others)
    assert v.ok and v.old == "" and v.new == new
    assert v.patch == {"personality": {"情感": {"想念": new}}}
    ProfileStore(env.rt).change("V1", v.patch, author="growth", reason="成长")
    assert Feelings(env.rt).understanding("V1")["想念"] == new
    assert Feelings(env.rt).understanding("V1")["生气"].startswith("别人把客气当同意")
    assert fresh_patch(ProfileStore(env.rt).current("V1").data, proposal("想念", new)) == {}


def test_growth_rewrite_is_gated():
    env = world()
    gate = Gate(env.rt)
    week = week_with("V1", "想念：等了好久对方终于回来")
    others = {m: ProfileStore(env.rt).current(m).data for m in ("V2", "V3")}

    def why(p: Proposal) -> str:
        return gate.check("V1", [p], week, Ledger(), others)[0].reason

    assert "不是能改写的情绪" in why(proposal("害怕", "一个人走夜路时会数路灯"))
    assert "6～" in why(proposal("想念", "想"))
    assert "几乎一样" in why(proposal("生气", "别人把客气当同意，催个不停。"))
    copied = gate.check("V1", [proposal("难过", "躲进懒人沙发发呆很久。")], week, Ledger(), others)
    assert "太像" in copied[0].reason and copied[0].clash == "V2"  # 不照抄别的成员
    assert "引用的原话" in why(proposal("想念", "一直留着对方那杯茶", quote="完全没写过的话"))
    first = "原来想念是一直留着对方那杯茶，等人回来时装作刚泡好"
    v = gate.check("V1", [proposal("想念", first)], week, Ledger(), others)[0]
    ProfileStore(env.rt).change("V1", v.patch, author="growth", reason="成长")
    assert "最近刚改过" in why(proposal("想念", "想念的时候会把聊天记录从头翻一遍"))


def test_the_week_material_lists_her_feelings():
    env = world()
    f = Feelings(env.rt)
    f.record("V1", "难过", "画稿没保存", datetime(2026, 11, 3, 4, tzinfo=UTC))
    f.record("V1", "开心", "上上周的事", datetime(2026, 10, 20, 4, tzinfo=UTC))
    week = build(env.rt, "V1", datetime(2026, 11, 2).date(), UTC)
    texts = [ln.text for ln in week.lines if ln.source == "feel"]
    assert texts == ["难过：画稿没保存"]
