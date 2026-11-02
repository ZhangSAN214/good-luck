"""朋友圈配图的摄影参数：每张图抽一种拍法，再给机位、光、时机、焦段、滤镜各抽一个取值（纯函数，可复现）。

- 拍法（``media.moment_photos.shots``）按权重 × 她的拍照习惯 ``boost.shots`` 抽
  （她爱发自拍就多自拍）。
- 其他维度每个用独立种子 ``photo:<维度>:<事件>:<成员>``，互不影响；按时间段（主时区的钟点）、
  地点（生活事件的 place）和拍法的入镜方式（her）过滤；只有她自己拍（拍法 ``by: self``）时才乘她的
  ``boost``，室友帮拍用通用权重。
- 她自己拍、拍的是东西（her 不是 full）时，按 ``subject_chance`` 换成她爱拍的东西；
  按 ``quirk_chance`` 带上她的拍照小毛病。

差异全部来自配置（``media.moment_photos`` 和形象卡的 ``photo``），代码不看是哪位成员。
只在生活 / 聊天侧使用，工作模式不读。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, tzinfo

from roundtable.core.config.schema import (
    PHOTO_DIMS,
    PHOTO_PLACES,
    PHOTO_TIMES,
    MomentPhotoRules,
    MomentShot,
    PhotoHabit,
    PhotoOption,
)

NONE = "none"  # 提示词变量为空时的占位


def time_of_day(hour: int) -> str:
    """钟点 → morning（5–9 点）/ day（10–16）/ evening（17–19）/ night（其余）。"""
    if 5 <= hour < 10:
        return "morning"
    if 10 <= hour < 17:
        return "day"
    if 17 <= hour < 20:
        return "evening"
    return "night"


def event_time(at: object, tz: tzinfo | None) -> str | None:
    """生活事件的时间（ISO 字符串）在主时区的时间段；拿不到时为 None（不按时间过滤）。"""
    if not isinstance(at, str) or not at:
        return None
    try:
        moment = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return time_of_day(moment.astimezone(tz).hour)


def place_tags(place: str) -> frozenset[str]:
    """生活事件地点 → 条件标签（dorm:成员 → dorm + indoor；rooftop → rooftop + outdoor）。
    不认识的地点为空集合（不按地点过滤）。"""
    kind = (place or "").partition(":")[0].strip().lower()
    if kind not in PHOTO_PLACES or kind in ("indoor", "outdoor"):
        return frozenset()
    return frozenset({kind, "outdoor" if kind == "rooftop" else "indoor"})


def fits(option: PhotoOption, time: str | None, places: frozenset[str]) -> bool:
    """同类条件里满足一个即可；没写这类条件、或拿不到时间 / 地点时不按它过滤。"""
    times = set(option.when) & set(PHOTO_TIMES)
    where = set(option.when) & set(PHOTO_PLACES)
    if times and time is not None and time not in times:
        return False
    return not (where and places and not where & places)


def pick_shot(rules: MomentPhotoRules, habit: PhotoHabit | None, rng: random.Random) -> MomentShot:
    """按权重 × 她的 ``boost.shots`` 抽一种拍法（她发哪种照片多，是她自己的习惯）。"""
    boost = habit.boost.get("shots", {}) if habit else {}
    weights = [s.weight * boost.get(s.name, 1.0) for s in rules.shots]
    return rng.choices(rules.shots, weights)[0]


@dataclass(frozen=True)
class PhotoSpec:
    """一张配图的摄影参数。picks：维度 → 抽到的取值（过滤后没有可选项的维度不在里面）。"""

    shot: MomentShot
    picks: dict[str, PhotoOption] = field(default_factory=dict)
    subject: str = ""
    quirk: str = ""

    def _text(self, *dims: str) -> str:
        parts = [self.picks[d].text for d in dims if d in self.picks and self.picks[d].text]
        return ", ".join(parts) or NONE

    @property
    def camera(self) -> str:
        return self._text("angles", "lenses")

    @property
    def light(self) -> str:
        return self._text("lights")

    @property
    def moment(self) -> str:
        return self._text("moments")

    @property
    def touches(self) -> str:
        """手机滤镜、她爱拍的东西、拍照小毛病（都没有时为 none）。"""
        parts = [self.picks[d].text for d in ("filters",) if d in self.picks and self.picks[d].text]
        if self.subject:
            parts.append(f"what she likes to photograph: {self.subject}")
        if self.quirk:
            parts.append(f"her photo habit: {self.quirk}")
        return "; ".join(parts) or NONE

    def variables(self) -> dict[str, str]:
        """填进 moment_photo 提示词的摄影变量。"""
        return {
            "camera": self.camera,
            "light": self.light,
            "moment": self.moment,
            "touches": self.touches,
        }

    def tags(self) -> str:
        """标签版（拍法 + 各维度的 tags），给吃标签的本地绘图模型。"""
        parts = [self.shot.tags] + [self.picks[d].tags for d in PHOTO_DIMS if d in self.picks]
        return ", ".join(p for p in parts if p)


def pick_spec(
    rules: MomentPhotoRules,
    habit: PhotoHabit | None,
    shot: MomentShot,
    *,
    event_id: object,
    member_id: str,
    time: str | None = None,
    place: str = "",
) -> PhotoSpec:
    """给已抽好的拍法配齐各摄影维度。同一事件 + 成员结果固定；各维度种子独立。"""
    mine = habit if shot.by == "self" else None  # 室友帮拍：不用她的习惯
    places = place_tags(place)
    picks: dict[str, PhotoOption] = {}
    for dim in PHOTO_DIMS:
        options = [o for o in getattr(rules, dim) if shot.her in o.her and fits(o, time, places)]
        if not options:
            continue
        boost = mine.boost.get(dim, {}) if mine else {}
        rng = random.Random(f"photo:{dim}:{event_id}:{member_id}")
        picks[dim] = rng.choices(options, [o.weight * boost.get(o.name, 1.0) for o in options])[0]
    subject = quirk = ""
    if mine is not None:
        rng = random.Random(f"photo:habit:{event_id}:{member_id}")
        roll_subject, roll_quirk = rng.random(), rng.random()
        if mine.subjects and shot.her != "full" and roll_subject < rules.subject_chance:
            subject = rng.choice(mine.subjects)
        if mine.quirk and roll_quirk < rules.quirk_chance:
            quirk = mine.quirk
    return PhotoSpec(shot, picks, subject, quirk)
