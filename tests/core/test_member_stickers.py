from __future__ import annotations

import asyncio
import io
import json

import pytest
from PIL import Image

from roundtable.core.config import load_config
from roundtable.core.member_stickers import (
    CHECK_STEP,
    DRAW_STEP,
    MemberStickers,
    StickerBudgetExceeded,
    StickerError,
    StickerUnavailable,
)
from roundtable.core.providers import ErrorKind, FakeProvider
from roundtable.core.runtime import Runtime

from .members_samples import write_assets

OK = json.dumps({"caption_ok": True, "looks_ok": True, "problems": ""})
BAD = json.dumps({"caption_ok": False, "looks_ok": True, "problems": "配文少了一个字"})


def build(tmp_path, *, root=True, assets=True, check_text=OK, **stickers):
    config = load_config()
    policy = config.roundtable.request.model_copy(update={"backoff_s": 0, "failover_rounds": 1})
    media = config.roundtable.media
    media = media.model_copy(update={"stickers": media.stickers.model_copy(update=stickers)})
    rt_cfg = config.roundtable.model_copy(update={"request": policy, "media": media})
    config = config.model_copy(update={"roundtable": rt_cfg})
    fake = FakeProvider("openrouter", default=check_text)
    members_dir = tmp_path / "art"
    if assets:
        write_assets(members_dir, "OpenAI")
    rt = Runtime.build(
        config=config,
        providers={"openrouter": fake},
        db_path=":memory:",
        members_dir=members_dir,
    )
    return rt, fake, MemberStickers(rt, tmp_path / "stickers" if root else None)


def image_calls(fake):
    return [c for c in fake.media_calls if c[0] == "image"]


def test_every_member_has_twelve_stills_and_two_animated():
    cards = load_config().personas.sticker_cards
    assert len(cards) == 8
    for card in cards.values():
        assert len([s for s in card.stickers if not s.frames]) >= 12  # 12 张基础 + 11-01 起的名梗
        assert len([s for s in card.stickers if s.frames]) == 2
    vendors = {m.vendor for m in load_config().models.models if m.seat}
    assert set(cards) <= vendors
    for card in cards.values():
        for spec in card.stickers:
            assert spec.caption and spec.action


def test_every_sticker_says_when_to_send_it():
    """10-31：记仇的「记着」被当成「好的」连用 6 次。每张都写用法；「收到」只在不打字时单发。"""
    for member, card in load_config().personas.sticker_cards.items():
        for spec in card.stickers:
            assert spec.use, f"{member}·{spec.name} 没写用法"
        got = {s.name: s for s in card.stickers}
        assert "不打字" in got["收到"].use and "收到" in got["收到"].means
    claude = load_config().personas.sticker_cards["Anthropic"].stickers
    assert "不是「好的」" in next(s for s in claude if s.name == "记着").use


def test_uses_and_means_only_cover_approved_stickers(tmp_path):
    rt, _, stickers = build(tmp_path)
    asyncio.run(stickers.draw("OpenAI", "晚安"))
    asyncio.run(stickers.draw("OpenAI", "啊这"))
    assert stickers.uses("OpenAI") == {} and stickers.means("OpenAI") == {}
    stickers.review("OpenAI", "晚安", "approved")
    assert stickers.uses("OpenAI") == {"晚安": "睡前道别"}
    assert "晚安" in stickers.means("OpenAI")["晚安"]
    assert stickers.uses("Nobody") == {} and stickers.means("Nobody") == {}


def test_means_falls_back_to_the_caption_without_punctuation():
    from roundtable.core.config.schema import StickerSpec

    spec = StickerSpec(name="收", caption="收到！", action="敬礼")
    assert spec.means == [] and spec.use == ""
    with pytest.raises(ValueError):
        StickerSpec(name="收", caption="收到", action="敬礼", means=["一个超过八个字的长词语"])


def test_draw_uses_reference_images_and_a_zh_model_then_waits_for_review(tmp_path):
    rt, fake, stickers = build(tmp_path)
    row = asyncio.run(stickers.draw("OpenAI", "快夸我"))
    assert row["status"] == "pending" and not row["problems"] and not row["stale"]
    [(_, route_model, prompt, _, refs, _)] = image_calls(fake)
    first = stickers.draw_models()[0]
    assert "zh" in first.tags and any(r.model == route_model for r in first.routes)
    assert refs and all(r.kind == "image" for r in refs)
    assert "快夸我" in prompt
    # 存成白底正方形 PNG
    data = stickers.file("OpenAI", "快夸我")
    with Image.open(io.BytesIO(data)) as im:
        assert im.size == (360, 360) and im.format == "PNG"
    # 审过才进可用清单
    assert stickers.approved("OpenAI") == []
    stickers.review("OpenAI", "快夸我", "approved")
    [ok] = stickers.approved("OpenAI")
    assert ok["name"] == "快夸我" and ok["caption"] == "快夸我"


def test_index_and_files_survive_a_new_instance(tmp_path):
    rt, _, stickers = build(tmp_path)
    asyncio.run(stickers.draw("OpenAI", "晚安"))
    fresh = MemberStickers(rt, tmp_path / "stickers")
    [row] = [r for r in fresh.listing("OpenAI") if r["name"] == "晚安"]
    assert row["status"] == "pending"
    assert fresh.file("OpenAI", "晚安") == stickers.file("OpenAI", "晚安")
    missing = [r for r in fresh.listing("OpenAI") if r["status"] == "missing"]
    assert len(missing) == 16  # 17 张（含 3 张名梗）画了 1 张


def test_failed_check_redraws_with_another_model_and_keeps_the_problem(tmp_path):
    rt, fake, stickers = build(tmp_path, check_text=BAD)
    row = asyncio.run(stickers.draw("OpenAI", "收到"))
    calls = image_calls(fake)
    assert len(calls) == 2 and calls[0][1] != calls[1][1]
    assert row["problems"] == "配文少了一个字" and row["status"] == "pending"
    session = rt.repo.sticker_session()
    rows = rt.repo.conn.execute("SELECT step FROM calls WHERE session_id = ?", (session,))
    steps = [r[0] for r in rows]
    assert steps.count(DRAW_STEP) == 2 and steps.count(CHECK_STEP) == 2


def test_check_can_be_turned_off(tmp_path):
    _, fake, stickers = build(tmp_path, check=False)
    asyncio.run(stickers.draw("OpenAI", "好耶"))
    assert fake.calls == [] and len(image_calls(fake)) == 1


def test_without_review_new_stickers_are_usable_at_once(tmp_path):
    _, _, stickers = build(tmp_path, review=False)
    asyncio.run(stickers.draw("OpenAI", "啊这"))
    assert [s["name"] for s in stickers.approved("OpenAI")] == ["啊这"]


def test_costs_go_to_the_hidden_session_and_the_monthly_cap_blocks(tmp_path):
    rt, fake, stickers = build(tmp_path, monthly_usd=0.05)
    fake.media_cost = 0.03
    asyncio.run(stickers.draw("OpenAI", "想想"))
    assert stickers.month_spent() >= 0.03
    assert all(s["id"] != rt.repo.sticker_session() for s in rt.repo.list_sessions())
    with pytest.raises(StickerBudgetExceeded):
        asyncio.run(stickers.draw("OpenAI", "刹车"))


def test_creative_cap_also_blocks(tmp_path):
    rt, fake, stickers = build(tmp_path)
    fake.media_cost = 0.03
    media = rt.config.roundtable.media.model_copy(update={"monthly_usd": 0.03})
    rt.config = rt.config.model_copy(
        update={"roundtable": rt.config.roundtable.model_copy(update={"media": media})}
    )
    asyncio.run(stickers.draw("OpenAI", "想想"))
    with pytest.raises(StickerBudgetExceeded):
        asyncio.run(stickers.draw("OpenAI", "刹车"))


def test_unknown_sticker_member_or_missing_refs(tmp_path):
    _, _, stickers = build(tmp_path)
    with pytest.raises(StickerError):
        asyncio.run(stickers.draw("OpenAI", "不存在"))
    with pytest.raises(StickerError):
        stickers.listing("Nobody")
    with pytest.raises(StickerError):
        stickers.review("OpenAI", "晚安", "approved")  # 还没画
    _, _, bare = build(tmp_path / "bare", assets=False)
    with pytest.raises(StickerUnavailable):
        asyncio.run(bare.draw("OpenAI", "晚安"))


def test_all_image_calls_failing_is_unavailable(tmp_path):
    _, fake, stickers = build(tmp_path)
    fake.submit_errors.extend([ErrorKind.SERVER] * 20)
    fake.broken_models.update(m.id for m in stickers.draw_models())
    with pytest.raises(StickerUnavailable):
        asyncio.run(stickers.draw("OpenAI", "晚安"))
    assert stickers.file("OpenAI", "晚安") is None


def test_media_off_means_no_models(tmp_path):
    _, _, stickers = build(tmp_path, enabled=False)
    assert stickers.draw_models() == []
    with pytest.raises(StickerUnavailable):
        asyncio.run(stickers.draw("OpenAI", "晚安"))


def test_editing_the_card_marks_the_sticker_stale(tmp_path):
    rt, _, stickers = build(tmp_path)
    asyncio.run(stickers.draw("OpenAI", "晚安"))
    personas = rt.config.personas
    card = personas.sticker_cards["OpenAI"].model_copy(update={"look": "换了个发型"})
    cards = {**personas.sticker_cards, "OpenAI": card}
    rt.config = rt.config.model_copy(
        update={"personas": personas.model_copy(update={"sticker_cards": cards})}
    )
    [row] = [r for r in stickers.listing("OpenAI") if r["name"] == "晚安"]
    assert row["stale"]


def test_model_order_only_looks_at_tags(tmp_path):
    _, _, stickers = build(tmp_path)
    order = stickers.draw_models()
    prefer = stickers.rules.prefer_tags
    scores = [sum(t in m.tags for t in prefer) for m in order]
    assert scores == sorted(scores, reverse=True)
    assert all("image_edit" in m.tags and not m.seat for m in order)


def test_memory_store_works_without_a_folder(tmp_path):
    _, _, stickers = build(tmp_path, root=False)
    asyncio.run(stickers.draw("OpenAI", "晚安"))
    assert stickers.file("OpenAI", "晚安")
    assert not (tmp_path / "stickers").exists()


def test_setting_sheet_one_level_up_comes_first_then_member_art(tmp_path):
    rt, fake, stickers = build(tmp_path)
    card = rt.config.personas.sticker_cards["OpenAI"]
    sheet = tmp_path / card.refs[0]
    sheet.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "purple").save(sheet, "JPEG")
    refs = stickers.references("OpenAI", card)
    assert refs[0].name == sheet.name and len(refs) >= 2
    # 往上只找一级：再往外的路径不认
    outside = card.model_copy(update={"refs": ["../../etc/passwd"]})
    assert all(r.name != "passwd" for r in stickers.references("OpenAI", outside))


def test_caption_mismatch_in_text_seen_overrides_the_models_ok(tmp_path):
    seen = json.dumps(
        {"text_seen": "本尤不傻", "caption_ok": True, "looks_ok": True, "problems": ""}
    )
    _, fake, stickers = build(tmp_path, check_text=seen, max_attempts=1)
    row = asyncio.run(stickers.draw("OpenAI", "不傻"))
    assert "本尤不傻" in row["problems"]
    same = json.dumps(
        {"text_seen": "本龙不傻！", "caption_ok": True, "looks_ok": True, "problems": ""}
    )
    _, _, ok = build(tmp_path / "b", check_text=same, max_attempts=1)
    assert asyncio.run(ok.draw("OpenAI", "不傻"))["problems"] == ""


def test_check_without_a_usable_answer_is_flagged_for_you(tmp_path):
    _, fake, stickers = build(tmp_path, check_text="", max_attempts=1)
    row = asyncio.run(stickers.draw("OpenAI", "晚安"))
    assert row["problems"] == "自动核对没给出结果，请自己看一下"
    assert fake.calls[-1].params["max_tokens"] == 1200


def grey_sticker(size=360, bg=(228, 228, 230)):
    """浅灰底上一个人物：白裙子碰不到边框，红色块在中间。"""
    im = Image.new("RGB", (size, size), bg)
    for x in range(60, 140):
        for y in range(40, 160):
            im.putpixel((x, y), (250, 250, 252) if y > 100 else (200, 40, 40))
    for x in range(40, 160):  # 深色描边把白裙子和底色隔开
        im.putpixel((x, 38), (30, 30, 30))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_light_grey_background_becomes_pure_white_but_the_character_stays(tmp_path):
    from roundtable.core.member_stickers import _square

    with Image.open(io.BytesIO(_square(grey_sticker(), 360))) as im:
        im = im.convert("RGB")
        assert im.getpixel((2, 2)) == (255, 255, 255)
        assert im.getpixel((357, 180)) == (255, 255, 255)
        assert im.getpixel((100, 80))[1] < 100  # 红色块不动
        assert im.getpixel((100, 130)) == (250, 250, 252)  # 白裙子不和边框连着，原样
    # 很接近白的灰也要刷到
    with Image.open(io.BytesIO(_square(grey_sticker(bg=(243, 244, 246)), 360))) as im:
        im = im.convert("RGB")
        assert im.getpixel((2, 2)) == (255, 255, 255)
        assert im.getpixel((100, 80))[1] < 100
    # 带颜色的底不是“浅灰”，不刷
    tinted = grey_sticker(bg=(250, 220, 200))
    with Image.open(io.BytesIO(_square(tinted, 360))) as im:
        assert im.convert("RGB").getpixel((2, 2)) != (255, 255, 255)


def test_whiten_repaints_stored_stickers_and_keeps_their_status(tmp_path):
    rt, _, stickers = build(tmp_path)
    asyncio.run(stickers.draw("OpenAI", "晚安"))
    stickers.review("OpenAI", "晚安", "approved")
    # 模拟以前画的、底色发灰的图
    data = stickers._index.load()
    data["items"]["OpenAI"]["晚安"]["file"] = "old.png"
    stickers._index.write("old.png", grey_sticker())
    stickers._index.save(data)
    assert stickers.whiten() == 1
    assert stickers.whiten() == 0  # 刷过一次再刷不变
    [ok] = stickers.approved("OpenAI")
    assert ok["file"] != "old.png"
    with Image.open(io.BytesIO(stickers.file("OpenAI", "晚安"))) as im:
        assert im.convert("RGB").getpixel((3, 3)) == (255, 255, 255)


def test_animated_sticker_is_drawn_as_four_panels_and_saved_as_a_gif(tmp_path, monkeypatch):
    from roundtable.core.config.schema import StickerCard, StickerSpec
    from roundtable.core.providers import ImageOutput, Media

    rt, fake, stickers = build(tmp_path)
    card = rt.config.personas.sticker_cards["OpenAI"]
    spin = StickerSpec(
        name="转圈", caption="好耶", action="转圈", frames=["站好", "转身", "背对", "转回来"]
    )
    cards = rt.config.personas.sticker_cards | {
        "OpenAI": StickerCard(**(card.model_dump() | {"stickers": [*card.stickers[:11], spin]}))
    }
    rt.config = rt.config.model_copy(
        update={"personas": rt.config.personas.model_copy(update={"sticker_cards": cards})}
    )
    # 2×2 四格：每格一个颜色不同的方块
    sheet = Image.new("RGB", (400, 400), "white")
    for i, color in enumerate(["red", "green", "blue", "orange"]):
        x, y = (i % 2) * 200, (i // 2) * 200
        sheet.paste(color, (x + 60, y + 60, x + 140, y + 140))
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    calls = []

    async def generate_image(model, prompt, params, images=(), api="chat"):
        calls.append(prompt)
        return ImageOutput((Media("image", "image/png", buf.getvalue()),), 0.02)

    monkeypatch.setattr(fake, "generate_image", generate_image)
    row = asyncio.run(stickers.draw("OpenAI", "转圈"))
    assert row["status"] == "pending"
    assert "2×2" in calls[0] and "Panel 4: 转回来" in calls[0] and "「好耶」" in calls[0]
    data = stickers.file("OpenAI", "转圈")
    with Image.open(io.BytesIO(data)) as gif:
        assert gif.format == "GIF" and gif.n_frames == 4 and gif.size == (360, 360)
        firsts = []
        for i in range(4):
            gif.seek(i)
            firsts.append(gif.convert("RGB").getpixel((180, 180)))
        assert firsts[0][0] > 200 and firsts[1][1] > 100 and firsts[2][2] > 200
    # 刷白不碰动图
    assert stickers.whiten() == 0


def test_frames_must_be_exactly_four():
    from pydantic import ValidationError

    from roundtable.core.config.schema import StickerSpec

    with pytest.raises(ValidationError):
        StickerSpec(name="a", caption="a", action="a", frames=["一", "二"])
    assert StickerSpec(name="a", caption="a", action="a").frames == []


def test_moment_photo_uses_the_card_and_refs_and_counts_in_the_sticker_budget(tmp_path):
    rt, fake, stickers = build(tmp_path)
    data, mime = asyncio.run(stickers.photo("OpenAI", "天台看星星", "今天的星星好多", "a selfie"))
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(data)) as im:
        assert im.format == "JPEG"
    [(_, _, prompt, _, refs, _)] = image_calls(fake)
    assert refs and "a selfie" in prompt and "<post>今天的星星好多</post>" in prompt
    assert stickers.month_spent() > 0  # 和表情包算在同一个每月小上限里
    # 不存进表情索引
    assert all(r["status"] == "missing" for r in stickers.listing("OpenAI"))


def test_moment_photo_without_her_sends_no_reference_images(tmp_path):
    _, fake, stickers = build(tmp_path)
    asyncio.run(stickers.photo("OpenAI", "食堂", "饭好香", "only the food", refs=False))
    [(_, _, prompt, _, refs, _)] = image_calls(fake)
    assert refs == () and "only the food" in prompt and "never a photoreal stock photo" in prompt


def test_moment_photo_attaches_the_room_last_and_names_the_place(tmp_path):
    """11-01：场景必须在八人公寓里——按事件地点附上那间房的场景图（最后一张）。"""
    _, fake, stickers = build(tmp_path)
    asyncio.run(stickers.photo("OpenAI", "宿舍", "看书", "a selfie", place="dorm:OpenAI"))
    asyncio.run(stickers.photo("OpenAI", "宿舍", "看书", "a view", refs=False, place="dorm"))
    rooms = tmp_path / "art" / "rooms"
    rooms.mkdir()
    (rooms / "canteen_day.png").write_bytes(png("green"))
    (rooms / "rooms.json").write_text('{"canteen": {"day": "canteen_day.png"}}', encoding="utf-8")
    asyncio.run(stickers.photo("OpenAI", "食堂", "饭好香", "a selfie", place="canteen"))
    asyncio.run(stickers.photo("OpenAI", "天台", "风好大", "a selfie", place="rooftop"))
    asyncio.run(stickers.photo("OpenAI", "?", "?", "a selfie", place="somewhere"))
    dorm, view, canteen, rooftop, other = image_calls(fake)
    assert dorm[4][-1].name == "room_day.webp" and len(dorm[4]) >= 2
    assert "她自己的宿舍（最后一张参考图就是这间房）" in dorm[2]
    assert [m.name for m in view[4]] == ["room_day.webp"]  # 她不入镜也照这间房画
    assert canteen[4][-1].name == "canteen_day.png" and "公寓楼食堂（最后一张" in canteen[2]
    # 天台没有场景图：只写地点，不附图
    assert "公寓楼天台" in rooftop[2] and all("rooftop" not in m.name for m in rooftop[4])
    assert "公寓楼里（看今天的事在哪间房）" in other[2]


def test_moment_photo_uses_cues_and_the_daily_must():
    """日常照不扛巨斧；每人有标志小物件，只露手时也认得出（如小鲸鱼发卡）。"""
    cards = load_config().personas.sticker_cards
    assert all(card.cues for card in cards.values())
    assert "小鲸鱼发卡" in cards["DeepSeek"].cues
    assert "黑金巨斧" in cards["xAI"].must
    assert cards["xAI"].daily_must and "黑金巨斧" not in cards["xAI"].daily_must


def test_moment_photo_prompt_carries_cues_and_daily_must(tmp_path):
    rt, fake, stickers = build(tmp_path)
    card = rt.config.personas.sticker_cards["OpenAI"]
    daily = card.model_copy(update={"daily_must": ["白色长发"], "cues": ["小金铃"]})
    cards = {**rt.config.personas.sticker_cards, "OpenAI": daily}
    rt.config.personas.__dict__["sticker_cards"] = cards
    asyncio.run(stickers.photo("OpenAI", "宿舍", "看书", "a selfie"))
    [(_, _, prompt, _, _, _)] = image_calls(fake)
    assert "must be visible: 白色长发." in prompt and "小金铃" in prompt
    assert "龙翼" not in prompt.split("must be visible:")[1].split("\n")[0]


def test_phone_look_is_deterministic_and_changes_the_image():
    import random

    from roundtable.core.member_stickers import phone_look

    src = png("blue", (400, 300))
    a = phone_look(src, random.Random("x"))
    assert a == phone_look(src, random.Random("x")) and a != src
    with Image.open(io.BytesIO(a)) as im:
        assert im.format == "JPEG" and im.width < 400 and im.height < 300  # 裁过
    assert phone_look(b"not an image", random.Random(1)) == b"not an image"


def test_moment_photo_blocked_by_the_monthly_cap(tmp_path):
    _, _, stickers = build(tmp_path, monthly_usd=0.000001)
    with pytest.raises(StickerBudgetExceeded):
        asyncio.run(stickers.photo("OpenAI", "天台", "好看", "a selfie"))


def png(color="red", size=(300, 400)):
    im = Image.new("RGB", size, (236, 236, 236))
    im.paste(color, (100, 100, 200, 300))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_prompt_text_is_what_draw_would_send(tmp_path):
    rt, fake, stickers = build(tmp_path)
    text = stickers.prompt_text("OpenAI", "快夸我")
    assert "「快夸我」" in text
    anim = stickers.prompt_text("OpenAI", "摇尾巴")
    assert "2×2" in anim and "Panel 4:" in anim


def test_imported_still_is_squared_whitened_and_waits_for_review_without_spending(tmp_path):
    rt, fake, stickers = build(tmp_path)
    row = stickers.import_image("OpenAI", "快夸我", png())
    assert row["status"] == "pending" and not row["stale"]
    with Image.open(io.BytesIO(stickers.file("OpenAI", "快夸我"))) as im:
        assert im.size == (360, 360) and im.getpixel((2, 2))[:3] == (255, 255, 255)
    assert image_calls(fake) == [] and stickers.month_spent() == 0
    entry = json.loads((tmp_path / "stickers" / "index.json").read_text(encoding="utf-8"))
    assert entry["items"]["OpenAI"]["快夸我"]["model"] == "imported"
    with pytest.raises(StickerError):
        stickers.import_image("OpenAI", "快夸我", b"not an image")


def test_imported_animated_sticker_takes_a_four_panel_sheet_or_a_gif(tmp_path):
    rt, fake, stickers = build(tmp_path)
    sheet = Image.new("RGB", (400, 400), "white")
    for i, color in enumerate(["red", "green", "blue", "orange"]):
        x, y = (i % 2) * 200, (i // 2) * 200
        sheet.paste(color, (x + 60, y + 60, x + 140, y + 140))
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    stickers.import_image("OpenAI", "摇尾巴", buf.getvalue())
    with Image.open(io.BytesIO(stickers.file("OpenAI", "摇尾巴"))) as gif:
        assert gif.format == "GIF" and gif.n_frames == 4 and gif.size == (360, 360)
    frames = [Image.new("RGB", (200, 200), c) for c in ("red", "blue", "green")]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=200)
    stickers.import_image("OpenAI", "扑翅膀", buf.getvalue())
    with Image.open(io.BytesIO(stickers.file("OpenAI", "扑翅膀"))) as gif:
        assert gif.n_frames == 3


# --- 本地出图（ComfyUI，image_prompt: tags）------------------------------------------


def build_local(tmp_path, **photos):
    """打开仓库里默认关掉的本地绘图模型（渠道 comfyui，价格 0，吃标签）。"""
    config = load_config()
    policy = config.roundtable.request.model_copy(update={"backoff_s": 0, "failover_rounds": 1})
    media = config.roundtable.media
    media = media.model_copy(
        update={"moment_photos": media.moment_photos.model_copy(update=photos)}
    )
    models = config.models.model_copy(
        update={
            "models": [
                m.model_copy(update={"enabled": True}) if m.image_prompt == "tags" else m
                for m in config.models.models
            ]
        }
    )
    config = config.model_copy(
        update={
            "models": models,
            "roundtable": config.roundtable.model_copy(update={"request": policy, "media": media}),
        }
    )
    cloud, local = FakeProvider("openrouter", default=OK), FakeProvider("comfyui")
    members_dir = tmp_path / "art"
    write_assets(members_dir, "OpenAI")
    rt = Runtime.build(
        config=config,
        providers={"openrouter": cloud, "comfyui": local},
        db_path=":memory:",
        members_dir=members_dir,
    )
    return rt, cloud, local, MemberStickers(rt, tmp_path / "stickers")


def test_free_local_model_is_tried_first_but_stickers_skip_tag_models(tmp_path):
    _, _, _, stickers = build_local(tmp_path)
    order = stickers.draw_models()
    assert order[0].image_prompt == "tags"  # 价格 0 排最前（只看价格，不看名字）
    assert all(m.image_prompt == "text" for m in order[1:])
    assert stickers.sticker_models() == order[1:]  # 表情包没有标签版提示词


def test_moment_photo_on_the_local_model_uses_the_tag_prompt(tmp_path):
    rt, cloud, local, stickers = build_local(tmp_path)
    card = rt.config.personas.sticker_cards["OpenAI"]
    asyncio.run(
        stickers.photo(
            "OpenAI", "天台", "<b>星星</b>", "a selfie", her="full", shot_tags="selfie, from above"
        )
    )
    assert image_calls(cloud) == []
    [(_, model, prompt, _, refs, _)] = image_calls(local)
    assert model.endswith(".json") and refs  # 路由的 model 是工作流模板；带设定图
    positive, negative = prompt.split("\n")
    assert positive.startswith("positive: ") and negative.startswith("negative: ")
    assert "1girl, solo" in positive and "selfie, from above" in positive
    assert card.look_tags and card.look_tags in positive
    assert "星星" not in prompt and ", ," not in prompt  # 中文正文不进标签；空变量不留空项
    assert "weapon" in negative and "photorealistic" in negative
    assert stickers.month_spent() == 0  # 本地不花钱


def test_local_tag_prompt_without_her_drops_the_look(tmp_path):
    rt, _, local, stickers = build_local(tmp_path)
    asyncio.run(stickers.photo("OpenAI", "食堂", "饭", "only the food", refs=False))
    [(_, _, prompt, _, refs, _)] = image_calls(local)
    card = rt.config.personas.sticker_cards["OpenAI"]
    assert refs == () and "no humans" in prompt and card.look_tags not in prompt


def test_room_image_goes_to_the_cloud_model_but_not_the_local_tag_model(tmp_path):
    """房间图只给看得懂"最后一张是房间"的云端模型；本地标签模型把参考图都当人物参考。"""
    _, cloud, local, stickers = build_local(tmp_path)
    asyncio.run(stickers.photo("OpenAI", "宿舍", "看书", "a selfie", place="dorm"))
    [(_, _, _, _, refs, _)] = image_calls(local)
    assert refs and all(m.name != "room_day.webp" for m in refs)
    local.broken_models.update(r.model for r in stickers.draw_models()[0].routes)
    asyncio.run(stickers.photo("OpenAI", "宿舍", "看书", "a selfie", place="dorm", seed="2"))
    [(_, _, _, _, refs, _)] = image_calls(cloud)
    assert refs[-1].name == "room_day.webp"


def test_local_failure_falls_back_to_a_cloud_model_unless_local_only(tmp_path):
    _, cloud, local, stickers = build_local(tmp_path)
    local.broken_models.update(r.model for r in stickers.draw_models()[0].routes)
    asyncio.run(stickers.photo("OpenAI", "天台", "好看", "a selfie"))
    assert len(image_calls(local)) == 1 and len(image_calls(cloud)) == 1
    [(_, _, prompt, _, _, _)] = image_calls(cloud)
    assert "<post>好看</post>" in prompt  # 云端模型照旧用自然语言提示词

    _, cloud, local, stickers = build_local(tmp_path / "b", local_only=True)
    local.broken_models.update(r.model for r in stickers.draw_models()[0].routes)
    with pytest.raises(StickerUnavailable):
        asyncio.run(stickers.photo("OpenAI", "天台", "好看", "a selfie"))
    assert image_calls(cloud) == []  # 只用本地：画不出来就只发文字，不花钱


def test_local_only_without_a_local_model_draws_nothing(tmp_path):
    _, fake, stickers = build(tmp_path)
    rt = stickers.rt
    media = rt.config.roundtable.media
    photos = media.moment_photos.model_copy(update={"local_only": True})
    rt.config = rt.config.model_copy(
        update={
            "roundtable": rt.config.roundtable.model_copy(
                update={"media": media.model_copy(update={"moment_photos": photos})}
            )
        }
    )
    with pytest.raises(StickerUnavailable):
        asyncio.run(stickers.photo("OpenAI", "天台", "好看", "a selfie"))
    assert image_calls(fake) == []


def test_tidy_tags_drops_empty_items():
    from roundtable.core.member_stickers import tidy_tags

    assert tidy_tags("positive: a, , b,  ,c\nnegative: x,") == "positive: a, b, c\nnegative: x"


def test_every_card_has_english_look_tags():
    for vendor, card in load_config().personas.sticker_cards.items():
        assert card.look_tags, vendor
        assert card.look_tags.isascii(), vendor  # 标签模型读不懂中文
