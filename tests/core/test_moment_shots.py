"""朋友圈配图的摄影参数抽取（纯函数，不调用模型）。"""

from __future__ import annotations

import random
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from roundtable.core.config import load_config
from roundtable.core.config.schema import (
    PHOTO_DIMS,
    MomentPhotoRules,
    MomentShot,
    PhotoHabit,
    PhotoOption,
)
from roundtable.core.moment_shots import (
    NONE,
    PhotoSpec,
    event_time,
    fits,
    pick_shot,
    pick_spec,
    place_tags,
    time_of_day,
)

CFG = load_config()
RULES = CFG.roundtable.media.moment_photos
CARDS = CFG.personas.sticker_cards
SHOTS = {s.name: s for s in RULES.shots}


def spec(shot="selfie", habit=None, event=1, member="m", **kw) -> PhotoSpec:
    return pick_spec(RULES, habit, SHOTS[shot], event_id=event, member_id=member, **kw)


# --- 时间段与地点 ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "part"),
    [(4, "night"), (5, "morning"), (9, "morning"), (10, "day"), (16, "day"), (17, "evening")]
    + [(19, "evening"), (20, "night"), (0, "night")],
)
def test_time_of_day(hour, part):
    assert time_of_day(hour) == part


def test_event_time_uses_the_home_zone():
    at = datetime(2026, 11, 1, 13, 0, tzinfo=UTC).isoformat()  # 伦敦 13 点，上海 21 点
    assert event_time(at, ZoneInfo("Europe/London")) == "day"
    assert event_time(at, ZoneInfo("Asia/Shanghai")) == "night"
    assert event_time("2026-11-01T13:00:00Z", ZoneInfo("UTC")) == "day"
    for bad in (None, "", "昨天", "2026-11-01T13:00:00"):  # 没有时区的不猜
        assert event_time(bad, ZoneInfo("UTC")) is None


def test_place_tags():
    assert place_tags("dorm:OpenAI") == {"dorm", "indoor"}
    assert place_tags("canteen") == {"canteen", "indoor"}
    assert place_tags("rooftop") == {"rooftop", "outdoor"}
    assert place_tags("") == place_tags("somewhere") == place_tags("indoor") == frozenset()


def test_fits_needs_one_match_per_kind_and_ignores_unknowns():
    o = PhotoOption(name="lamp", text="x", when=["evening", "night", "dorm", "office"])
    assert fits(o, "night", place_tags("dorm:a"))
    assert not fits(o, "day", place_tags("dorm:a"))
    assert not fits(o, "night", place_tags("canteen"))
    assert fits(o, None, frozenset())  # 拿不到时间和地点：不过滤
    assert fits(PhotoOption(name="any", text="x"), "day", place_tags("gym"))


# --- 抽取 ------------------------------------------------------------------------------


def test_same_event_and_member_give_the_same_spec_and_others_vary():
    assert spec(event=7) == spec(event=7)
    seen = {spec(event=i).camera for i in range(40)}
    assert len(seen) > 5


def test_each_dimension_has_its_own_seed():
    """改光表不影响机位抽到什么（各维度种子独立）。"""
    fewer = RULES.model_copy(update={"lights": RULES.lights[:2]})
    for i in range(30):
        a = pick_spec(RULES, None, SHOTS["candid"], event_id=i, member_id="m")
        b = pick_spec(fewer, None, SHOTS["candid"], event_id=i, member_id="m")
        assert a.picks["angles"] == b.picks["angles"] and a.picks["moments"] == b.picks["moments"]


def test_light_follows_time_and_place():
    night_dorm = {
        spec(event=i, time="night", place="dorm:x").picks["lights"].name for i in range(80)
    }
    assert night_dorm and night_dorm <= {"ceiling", "desk_lamp", "screen", "flash"}
    roof_night = {
        spec(event=i, time="night", place="rooftop").picks["lights"].name for i in range(80)
    }
    assert roof_night and roof_night <= {"blue_hour", "night_city", "flash"}
    morning = {
        spec(event=i, time="morning", place="canteen").picks["lights"].name for i in range(80)
    }
    assert "golden" not in morning and "desk_lamp" not in morning and "window_side" in morning


def test_she_is_not_in_the_photo_means_no_portrait_options():
    for i in range(60):
        s = spec("thing", event=i)
        assert all("none" in o.her for o in s.picks.values())
        # 机位里只剩拍东西也成立的（俯拍、隔桌、高 / 低机位）
        assert s.picks["angles"].name in {"top_down", "across_table", "high_angle", "low_angle"}
        assert s.picks["moments"].name in {"decisive", "just_ready"}


def test_her_own_habit_shifts_the_odds_only_when_she_holds_the_phone():
    habit = PhotoHabit(boost={"lenses": {"ultra_wide": 20}})
    n = 300
    plain = Counter(spec("selfie", None, i).picks["lenses"].name for i in range(n))
    mine = Counter(spec("selfie", habit, i).picks["lenses"].name for i in range(n))
    assert mine["ultra_wide"] > 3 * plain["ultra_wide"]
    # 室友帮拍：和没有习惯完全一样
    for i in range(50):
        assert spec("candid", habit, i) == spec("candid", None, i)


def test_subjects_and_quirk_only_for_her_own_shots():
    habit = PhotoHabit(subjects=["a bowl of rice"], quirk="tilted")
    things = [spec("pov", habit, i) for i in range(200)]
    hits = sum(s.subject == "a bowl of rice" for s in things)
    assert 0.45 < hits / 200 < 0.75  # subject_chance 0.6
    quirks = sum(s.quirk == "tilted" for s in things)
    assert 0.15 < quirks / 200 < 0.45  # quirk_chance 0.3
    assert all(spec("selfie", habit, i).subject == "" for i in range(50))  # 她入镜时不换主体
    assert all(not spec("candid", habit, i).quirk for i in range(50))  # 室友帮拍不带她的毛病


def test_shot_boost_makes_her_post_more_selfies():
    habit = PhotoHabit(boost={"shots": {"selfie": 4}})
    plain = Counter(pick_shot(RULES, None, random.Random(i)).name for i in range(400))
    mine = Counter(pick_shot(RULES, habit, random.Random(i)).name for i in range(400))
    assert mine["selfie"] > 2 * plain["selfie"]
    # 没有习惯时和原来一样：按权重抽（用同一个随机数序列）
    rng_a, rng_b = random.Random(3), random.Random(3)
    assert (
        pick_shot(RULES, None, rng_a)
        == rng_b.choices(RULES.shots, [s.weight for s in RULES.shots])[0]
    )


def test_variables_and_tags():
    s = PhotoSpec(
        MomentShot(text="t", tags="selfie"),
        {
            "angles": PhotoOption(name="a", text="low angle", tags="from below"),
            "lenses": PhotoOption(name="main", text=""),
            "filters": PhotoOption(name="warm", text="warm filter", tags="warm colors"),
        },
        subject="rice",
        quirk="tilted",
    )
    v = s.variables()
    assert v["camera"] == "low angle" and v["light"] == NONE and v["moment"] == NONE
    assert (
        v["touches"] == "warm filter; what she likes to photograph: rice; her photo habit: tilted"
    )
    assert s.tags() == "selfie, from below, warm colors"
    assert PhotoSpec(MomentShot(text="t")).variables() == dict.fromkeys(v, NONE)


def test_empty_table_after_filtering_skips_the_dimension():
    rules = RULES.model_copy(update={"lights": [PhotoOption(name="sun", text="s", when=["day"])]})
    s = pick_spec(rules, None, SHOTS["selfie"], event_id=1, member_id="m", time="night")
    assert "lights" not in s.picks and s.light == NONE


# --- 配置 ------------------------------------------------------------------------------


def test_repo_tables_follow_phone_reality():
    for dim in PHOTO_DIMS:
        table = getattr(RULES, dim)
        assert table, dim
        assert all(o.name for o in table)
    # 1x 主摄是最常见的（不写）；11-02 起色调多用胶片感（用户看技能版样张最满意）
    assert max(RULES.lenses, key=lambda o: o.weight).text == ""
    assert max(RULES.filters, key=lambda o: o.weight).text != ""
    # 约七成她本人入镜
    full = sum(s.weight for s in RULES.shots if s.her == "full")
    assert 0.6 <= full / sum(s.weight for s in RULES.shots) <= 0.8
    # 画风跟设定图：不写写实画质 / 相机型号词
    banned = ("photorealistic", "realistic", "8k", "4k", "leica", "sony", "canon", "iphone")
    for dim in ("shots", *PHOTO_DIMS):
        for o in getattr(RULES, dim):
            text = f"{o.text} {o.tags}".lower()
            assert not any(b in text for b in banned), (dim, o.name)


def test_style_references_fit_the_shot_and_time():
    """技能版：大师风格参照只配合得上的拍法和时间（闪光快照只在晚上、决定性瞬间只配抓拍）。"""
    seen = Counter()
    for event in range(400):
        s = spec("selfie", event=event, time="day")
        name = s.picks["styles"].name
        seen[name] += 1
        assert name not in {"cartier_bresson", "nan_goldin", "rembrandt", "eggleston"}
        c = spec("candid", event=event, time="night")
        seen[c.picks["styles"].name] += 1
    assert seen["cartier_bresson"] and seen["nan_goldin"] and seen["rembrandt"]
    assert spec("candid", event=1).style == spec("candid", event=1).variables()["style"]


def test_every_member_has_a_photo_habit():
    for vendor, card in CARDS.items():
        assert card.photo is not None and card.photo.boost and card.photo.quirk, vendor
        assert all(s.isascii() for s in card.photo.subjects), vendor  # 提示词里只放英文短语


def test_boost_must_name_existing_options(tmp_path):
    import shutil

    import yaml

    from roundtable.core.config import DEFAULT_CONFIG_DIR, ConfigError

    shutil.copytree(DEFAULT_CONFIG_DIR, tmp_path / "config")
    path = Path(tmp_path / "config" / "personas.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    first = next(iter(data["sticker_cards"]))
    data["sticker_cards"][first]["photo"] = {"boost": {"lenses": {"fisheye_9000": 2}}}
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path / "config")
    assert "fisheye_9000" in str(e.value) and "lenses" in str(e.value)


def test_schema_rejects_bad_options():
    with pytest.raises(ValidationError):
        PhotoOption(name="x", when=["midnight"])
    with pytest.raises(ValidationError):
        PhotoOption(name="Bad Name")
    with pytest.raises(ValidationError):
        PhotoOption(name="x", bogus=1)
    with pytest.raises(ValidationError):
        PhotoHabit(boost={"lenses": {"main": 0}})
    with pytest.raises(ValidationError):
        PhotoHabit(boost={"bogus": {"main": 2}})
    with pytest.raises(ValidationError, match="重复"):
        MomentPhotoRules(lenses=[PhotoOption(name="a"), PhotoOption(name="a")])


def test_work_mode_does_not_read_photo_habits():
    """拍照习惯只在朋友圈配图里用：只有 moments / member_stickers 导入抽取模块。"""
    src = Path(__file__).resolve().parents[2] / "src" / "roundtable"
    users = {
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if "moment_shots" in p.read_text(encoding="utf-8") and p.name != "moment_shots.py"
    }
    assert users == {"core/moments.py", "core/stickers/member.py"}


def test_dimension_values_only_go_with_their_shots():
    """11-02 样张：自拍配「几米外 / 从背后 / 没注意到镜头」互相矛盾。"""
    third_person = {"few_meters", "from_behind", "over_shoulder", "doorway", "reflection", "zoom"}
    for event in range(300):
        s = spec("selfie", event=event)
        assert not {o.name for o in s.picks.values()} & third_person
        m = spec("mirror", event=event)
        assert "unaware" not in {o.name for o in m.picks.values()}
    assert fits(PhotoOption(name="x", shots=["candid"]), None, frozenset(), "selfie") is False
    assert fits(PhotoOption(name="x", shots=["candid"]), None, frozenset(), "candid")
    assert fits(PhotoOption(name="x", shots=["candid"]), None, frozenset(), "")  # 无名拍法不过滤


def test_unknown_shot_names_in_a_dimension_are_rejected():
    with pytest.raises(ValidationError, match="不存在的拍法"):
        MomentPhotoRules(angles=[PhotoOption(name="x", shots=["nope"])])


def test_landlord_only_with_her_in_frame_and_scaled_by_her_wish():
    full = [spec("candid", event=e).landlord for e in range(2000)]
    assert 0.15 < sum(full) / len(full) < 0.25
    assert not any(spec("thing", event=e).landlord for e in range(300))
    assert not any(spec("pov", event=e).landlord for e in range(300))
    shy = PhotoHabit(landlord=0)
    assert not any(spec("candid", shy, event=e).landlord for e in range(300))
    keen = PhotoHabit(landlord=3)
    assert sum(spec("candid", keen, event=e).landlord for e in range(2000)) > 2 * sum(full)


def test_mid_bite_only_in_the_canteen():
    """11-02 试画：宿舍看书的动态抽到「正咬一口」，画成啃面包。"""
    for event in range(300):
        s = spec("candid", event=event, place="dorm:x")
        assert s.picks.get("moments") is None or s.picks["moments"].name != "mid_bite"


def test_camera_is_picked_first_and_lenses_follow_it():
    """11-02 用户：加单反、胶片机、拍立得等相机；0.5x、人像模式只有手机，50mm 定焦只有单反。"""
    seen = Counter()
    for event in range(600):
        s = spec("candid", event=event, time="night")
        device = s.picks["cameras"].name
        seen[device] += 1
        lens = s.picks.get("lenses")
        if lens and lens.cameras:
            assert device in lens.cameras, (device, lens.name)
        if device == "instant" and "filters" in s.picks:
            assert s.picks["filters"].cameras in ([], ["instant"])
        assert s.picks["cameras"].text in s.camera or s.picks["cameras"].text == ""
    assert {"phone", "dslr", "film_camera", "instant", "ccd"} <= set(seen)
    assert seen["phone"] == max(seen.values())  # 手机还是最多
    for event in range(300):  # 单反不配自拍
        assert spec("selfie", event=event).picks["cameras"].name != "dslr"


def test_unknown_camera_names_are_rejected():
    with pytest.raises(ValidationError, match="不存在的相机"):
        MomentPhotoRules(lenses=[PhotoOption(name="x", cameras=["nope"])])
