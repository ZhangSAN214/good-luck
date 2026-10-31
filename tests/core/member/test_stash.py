"""表情包收藏与跟队形：你发的表情包（动图、小图）看过的人按性格可能存下来自己用，照片不收、
没看成的不收；刚收的标一下，用腻了、很久没用就丢；发的时候换成那张图。
群里两个人先后发同一句短话 / 同一个表情包，这一轮该回话的人可能跟一个一模一样的，跟够就停；
问句不跟、别人的专属表情跟不了；关掉不动。"""

from __future__ import annotations

import random
from datetime import timedelta

from roundtable.core.chat import ChatHub, GroupChat, dm_room
from roundtable.core.member.stash import Formation, Stash, chain_key, sticker_like

from .test_brain import NOON, setup

GIF = "a" * 32 + ".gif"
PHOTO = "b" * 32 + ".jpg"
SMALL = "c" * 32 + ".png"


def rules(env, follow=None, **update) -> None:
    cfg = env.rt.config.roundtable
    engine = cfg.chat.engine
    st = engine.stash
    if follow:
        update["follow"] = st.follow.model_copy(update=follow)
    st = st.model_copy(update=update)
    chat = cfg.chat.model_copy(update={"engine": engine.model_copy(update={"stash": st})})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )


def user_image(env, room, *items, text="[图片]"):
    media = [{"id": i, "kind": "image", **extra} for i, extra in items]
    cards = [
        {"id": i, "kind": "image", "mode": "fast", "text": f"一只翻白眼的猫{i[:1]}。旁边有字"}
        for i, _ in items
    ]
    return env.rt.repo.add_chat_message(
        room, "user", text, meta={"media": media, "perception": cards}
    )


def test_what_counts_as_a_sticker():
    assert sticker_like({"id": GIF, "kind": "image"}, 480)
    assert sticker_like({"id": SMALL, "kind": "image", "w": 240, "h": 240}, 480)
    assert sticker_like({"id": PHOTO, "kind": "image", "sticker": True}, 480)
    assert not sticker_like({"id": PHOTO, "kind": "image", "w": 3000, "h": 4000}, 480)
    assert not sticker_like({"id": PHOTO, "kind": "image"}, 480)  # 不知道多大：当照片
    assert not sticker_like({"id": GIF, "kind": "video"}, 480)


def test_she_keeps_your_sticker_but_not_your_photo_or_one_she_did_not_see():
    env, _ = setup((3, 3, 3, 5, 5))
    rules(env, base=1.0, max_chance=1.0)
    stash = Stash(env.rt)
    row = user_image(env, dm_room("V1"), (GIF, {}), (PHOTO, {"w": 3000, "h": 2000}))
    assert stash.consider("V1", row) == [1]
    assert stash.consider("V1", row) == []  # 已经收过
    kept = stash.items("V1")
    assert [k["id"] for k in kept] == [GIF] and kept[0]["label"] == "一只翻白眼的猫a"
    blind = env.rt.repo.add_chat_message(
        dm_room("V1"),
        "user",
        "[图片]",
        meta={"media": [{"id": SMALL, "kind": "image", "w": 99, "h": 99}]},
    )
    assert stash.consider("V1", blind) == []  # 感官没看成：不知道是什么，不收
    rules(env, base=0.0, max_chance=0.0)
    assert Stash(env.rt).consider("V2", row) == []


def test_chance_follows_personality_and_care_and_each_message_rolls_once():
    env, _ = setup()
    stash = Stash(env.rt)
    from roundtable.core.member.stash import chance

    r = stash.rules
    funny = chance(r, {"幽默": 5, "好奇": 5}, 0.0)
    dull = chance(r, {"幽默": 1, "好奇": 1}, 0.0)
    assert dull < r.base < funny <= r.max_chance
    assert chance(r, {}, 1.0) > chance(r, {}, 0.0)
    rules(env, base=0.3, max_chance=0.3)
    row = user_image(env, dm_room("V1"), (GIF, {}))
    first = Stash(env.rt).consider("V1", row, traits={}, care=0.0)
    Stash(env.rt).clear()
    assert Stash(env.rt).consider("V1", row, traits={}, care=0.0) == first  # 可复现


def test_menu_marks_new_ones_and_sending_swaps_in_the_picture():
    env, _ = setup()
    rules(env, base=1.0, max_chance=1.0)
    stash = Stash(env.rt)
    stash.consider("V1", user_image(env, dm_room("V1"), (GIF, {})))
    assert stash.pack("V1") == {"收藏": ["1"]}
    menu = stash.menu("V1")
    assert "[表情:收藏·1]" in menu and "还没用过" in menu and "偷你的图" not in menu
    rest, items = stash.extract("V1", "笑死\n[表情:收藏·1] [表情:收藏·9]")
    assert rest == "笑死" and items == [{"id": GIF, "kind": "image", "sticker": True}]
    assert "正想用" in Stash(env.rt).menu("V1")
    assert stash.extract("V2", "[表情:收藏·1]") == ("", [])  # 别人的收藏她用不了


def test_tired_of_it_or_forgot_it_and_full_drops_the_oldest():
    env, _ = setup()
    rules(env, base=1.0, max_chance=1.0, tired_uses=2, max_keep=2)
    stash = Stash(env.rt)
    stash.consider("V1", user_image(env, dm_room("V1"), (GIF, {})))
    stash.extract("V1", "[表情:收藏·1]")
    stash.extract("V1", "[表情:收藏·1]")
    assert stash.items("V1") == []  # 用腻了
    for i, name in enumerate(("d", "e", "f")):
        env.rt.budget.now = lambda i=i: NOON + timedelta(minutes=i)
        stash.consider("V1", user_image(env, dm_room("V1"), (name * 32 + ".gif", {})))
    assert [k["id"][:1] for k in stash.items("V1")] == ["f", "e"]
    env.rt.budget.now = lambda: NOON + timedelta(days=30)
    assert stash.items("V1") == []  # 很久没用：忘了
    assert stash.delete("V1", 99) is False


def test_switched_off_keeps_nothing_and_offers_nothing():
    env, _ = setup()
    rules(env, base=1.0, max_chance=1.0)
    Stash(env.rt).consider("V1", user_image(env, dm_room("V1"), (GIF, {})))
    rules(env, enabled=False, base=1.0, max_chance=1.0)
    stash = Stash(env.rt)
    assert stash.pack("V1") == {} and stash.menu("V1") == ""
    assert stash.consider("V1", user_image(env, dm_room("V1"), (SMALL, {"sticker": True}))) == []
    assert stash.extract("V1", "[表情:收藏·1]") == ("[表情:收藏·1]", [])


async def test_dm_reply_with_a_kept_sticker_posts_the_picture():
    env, _ = setup()
    rules(env, base=1.0, max_chance=1.0)
    room = dm_room("V1")
    dm = ChatHub(env.rt).room(room)
    env.fake._default = lambda model, messages: "哈哈哈哈\n\n[表情:收藏·1]"
    msg = user_image(env, room, (GIF, {}))
    first = await dm.replies(msg["id"], [m for m in dm.members() if m.id == "V1"])
    assert first
    rows = [r for r in env.rt.repo.chat_messages(room) if r["author"] == "V1"]
    pic = [r for r in rows if (r["meta"] or {}).get("media")]
    assert pic and pic[0]["meta"]["media"][0]["id"] == GIF and pic[0]["meta"]["media"][0]["sticker"]
    assert pic[0]["text"] == "[表情]" and pic[0]["meta"]["perception"][0]["text"]
    assert Stash(env.rt).items("V1")[0]["uses"] == 1
    prompt = "\n".join(str(m.content) for m in env.fake.calls[-1].messages)
    assert "[表情:收藏·1]" in prompt


def test_chain_key_only_short_lines_and_single_stickers():
    def row(text, media=None):
        return {"text": text, "meta": {"media": media} if media else {}}

    assert chain_key(row("6"), 8, 480) == ("text", "6")
    assert chain_key(row("哈哈哈哈"), 8, 480) == ("text", "哈哈哈哈")
    assert chain_key(row("你们吃了吗？"), 8, 480) is None
    assert chain_key(row("今天上班真的好累啊啊啊"), 8, 480) is None
    assert chain_key(row("[表情]", [{"id": GIF, "kind": "image"}]), 8, 480) == ("media", GIF)
    assert chain_key(row("看", [{"id": PHOTO, "kind": "image"}]), 8, 480) is None


def _group(env):
    return GroupChat(env.rt, rng=random.Random(0))


def test_two_people_said_the_same_thing_so_others_may_follow_then_it_stops():
    env, _ = setup()
    rules(env, follow={"base": 1.0, "max_chance": 1.0})
    repo = env.rt.repo
    repo.add_chat_message("main", "V2", "6")
    msg = repo.add_chat_message("main", "user", "6")
    got = Formation(env.rt).followers("main", msg, ["V2", "V3", "V4", "V5"])
    assert [m for m, _ in got] == ["V3", "V4"] and got[0][1] == {"text": "6"}  # 最多 2 位
    for m, _ in got:
        repo.add_chat_message("main", m, "6")
    again = repo.add_chat_message("main", "V5", "6")
    assert Formation(env.rt).followers("main", again, ["V6"]) == []  # 跟够了，就停
    lone = repo.add_chat_message("main", "user", "好")
    assert Formation(env.rt).followers("main", lone, ["V3"]) == []  # 只有一个人说：不成队形
    rules(env, follow={"base": 0.0, "max_chance": 0.0})
    repo.add_chat_message("main", "V2", "绝了")
    msg = repo.add_chat_message("main", "user", "绝了")
    assert Formation(env.rt).followers("main", msg, ["V3", "V4"]) == []


def test_someone_elses_own_sticker_cannot_be_followed():
    env, _ = setup()
    rules(env, follow={"base": 1.0, "max_chance": 1.0})
    repo = env.rt.repo
    repo.add_chat_message("main", "V2", "[表情:小二·晚安]")
    msg = repo.add_chat_message("main", "user", "[表情:小二·晚安]")
    stickers = {"V3": {"团子": ["晚安"]}, "V2": {"小二": ["晚安"]}}
    assert Formation(env.rt).followers("main", msg, ["V3"], stickers=stickers) == []
    repo.add_chat_message("main", "V2", "[表情:团子·晚安]")
    msg = repo.add_chat_message("main", "user", "[表情:团子·晚安]")
    got = Formation(env.rt).followers("main", msg, ["V3"], stickers=stickers)
    assert got == [("V3", {"text": "[表情:团子·晚安]"})]


async def test_group_round_becomes_a_formation_without_calling_a_model():
    env, _ = setup()
    rules(env, follow={"base": 1.0, "max_chance": 1.0})
    room = _group(env)
    env.rt.repo.add_chat_message(
        "main", "V2", "[表情]", meta={"media": [{"id": GIF, "kind": "image", "sticker": True}]}
    )
    msg, _ = room.post("[表情]", meta={"media": [{"id": GIF, "kind": "image", "sticker": True}]})
    calls = len(env.fake.calls)
    speakers = [m for m in room.members() if m.id in ("V3", "V4", "V5")]
    said = await room.replies(msg["id"], speakers)
    assert len(said) == 2 and len(env.fake.calls) == calls
    assert all(r["meta"]["media"][0]["id"] == GIF for r in said)
