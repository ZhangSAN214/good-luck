"""roundtable.yaml 的 chat（成员引擎 chat.engine 除外）、plain_talk、perception。

群聊、私聊、生活、朋友圈、感官等规则。

从 schema.py 拆出（代码精简）。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from roundtable.core.config.sections.base import Tier, _Strict
from roundtable.core.config.sections.member_engine import EngineRules


class ChatBudgetRules(_Strict):
    """聊天这一块（私聊、群聊，以后的生活与上网）每月最多花多少（UTC 自然月）；也计入总预算。"""

    monthly_usd: float = Field(default=5.0, gt=0)
    # 按剩余天数平摊：成员自己的活动（生活、主动找你、群聊）每天最多用到
    # 「本月还剩的钱 ÷ 本月还剩的天数」，月初花不光、月底不断档；你直接找她不受它限制
    pace: bool = True


class MemoryLinkRules(_Strict):
    """联想网（P2，M22 档 1 的记忆部分）：一起出现 / 一起被想起的记忆连得更紧（赫布学习），
    想起一件时顺着连接想到别的（扩散激活），夜里顺着连接随便走几步（随机游走）。程序算，不调用模型。"""

    enabled: bool = True
    learn_rate: float = Field(default=0.2, gt=0, le=1)  # 每次一起出现，强度向 1 靠近的比例
    nightly_decay: float = Field(default=0.02, ge=0, lt=1)  # 每晚所有连接衰减的比例
    min_weight: float = Field(default=0.05, ge=0, lt=1)  # 低于它的连接删掉
    max_links: int = Field(default=20, ge=1, le=200)  # 每件记忆最多留几条最强的连接
    hops: int = Field(default=2, ge=1, le=3)  # 扩散几步
    spread: float = Field(default=0.5, gt=0, le=1)  # 每走一步激活乘以 强度 × spread
    seeds: int = Field(default=4, ge=1, le=20)  # 从分数最高的几件开始扩散
    assoc_max: int = Field(default=1, ge=0, le=5)  # 每次最多多带几件「联想到的」
    walks: int = Field(default=2, ge=0, le=10)  # 每晚随机游走几次
    walk_steps: int = Field(default=3, ge=1, le=20)  # 每次走几步


class DiaryToolRules(_Strict):
    """翻日记（P2）：生活模式里成员自己翻以前的日记和记忆，每位成员每天有次数上限（所有成员相同）。"""

    enabled: bool = True
    daily_reads: int = Field(default=3, ge=0, le=20)  # 每位成员每天最多翻几次（本地日）
    max_hits: int = Field(default=3, ge=1, le=10)  # 每次最多翻出几段
    result_chars: int = Field(default=300, ge=50, le=2000)  # 每次翻出的内容最多多少字


class MemoryRules(_Strict):
    """成员的记忆（docs/CHAT.md G3、docs/MEMBER_ENGINE.md「P2 记忆与联想」）。

    只存本地；只用于生活模式。
    """

    enabled: bool = True
    context_chars: int = Field(default=800, ge=0)  # 聊天提示词里最多放多少字的记忆（所有成员相同）
    # 私聊时接上大家庭群里最近 group_hours 小时的最多 group_lines 句（每句最多 group_chars 字）
    group_lines: int = Field(default=6, ge=0, le=20)
    group_hours: float = Field(default=12, gt=0, le=48)
    group_chars: int = Field(default=60, ge=10, le=200)
    entry_chars: int = Field(default=300, ge=50)  # 每条记忆最多多少字
    log_chars: int = Field(default=6000, ge=500)  # 整理一天时最多读多少字的聊天记录
    catch_up_days: int = Field(default=7, ge=1, le=60)  # 最多补整理几天前的
    # 夜里整理是一段 JSON，思考也算在输出里：600 时三位一个字都没写出来（10-31 真模型试跑）
    max_tokens: int = Field(default=2500, ge=64)
    estimate_usd: float = Field(default=0.003, ge=0)  # 每次整理的预估花费（预算检查用）
    # 召回方式：half_life 半衰期记忆（默认），按相关 × 鲜活 × 重要挑几件；
    # layers 按时间固定带几层（旧）
    recall: Literal["layers", "half_life"] = "half_life"
    half_life_days: float = Field(default=30, ge=1, le=3650)  # 发生过的事过多少天鲜活度减半
    freshness_floor: float = Field(default=0.5, ge=0, le=1)  # 鲜活度保底：再久的事也不低于它
    inject_max: int = Field(default=6, ge=1, le=30)  # 半衰期召回每次最多带几件
    daily_quota: int = Field(default=8, ge=1, le=30)  # 半衰期记忆每人每天最多记几件事
    # --- P2 记忆与联想 ---
    night: bool = True  # 每晚整理合并成一次调用：第一人称日记 + 记忆件 + 挂着的事（+ 心情改写）
    diary_chars: int = Field(default=300, ge=50, le=2000)  # 日记最多多少字
    domains: list[str] = Field(
        default_factory=lambda: ["学术", "日常", "工作", "娱乐", "社交", "身心"], min_length=1
    )
    hanging_max: int = Field(default=5, ge=0, le=10)  # 脑子里挂着的事最多几件
    candidates: int = Field(default=200, ge=10, le=5000)  # 召回时全文索引最多取几件候选
    recent: int = Field(default=30, ge=0, le=500)  # 另外带上最近的几件当候选
    merge_similarity: float = Field(default=0.6, gt=0, le=1)  # 和已有的一件这么像就合并计数
    update_similarity: float = Field(default=0.3, gt=0, le=1)  # 事实这么像（但不到合并）就算更新
    repeat_gap_minutes: int = Field(default=60, ge=1, le=24 * 60)  # 同一次对话：间隔不超过这么久
    digest_chars: int = Field(default=120, ge=0, le=1000)  # 全量经历的习惯摘要多少字（随身卡用）
    work_lookback_days: int = Field(default=14, ge=1, le=90)  # 读自己的工作记录往回看几天
    mood_threshold: float = Field(default=0.3, ge=0, le=1)  # 心情偏离中性这么多才改回忆语气
    retell_max: int = Field(default=3, ge=0, le=10)  # 每晚最多改几件旧记忆的语气和重要度
    links: MemoryLinkRules = MemoryLinkRules()
    diary: DiaryToolRules = DiaryToolRules()


class LifeGuardRules(_Strict):
    """收得住（docs/CHAT.md G9）：互聊收尾、每日条数上限、熔断与每周自检。由代码判断，不花钱。"""

    enabled: bool = True
    daily_messages: int = Field(default=40, ge=0, le=500)  # 成员之间每天最多几句（含分享，UTC 日）
    max_chars: int = Field(default=200, ge=20, le=2000)  # 互聊每句最多保留多少字
    stale_similarity: float = Field(default=0.5, gt=0, le=1)  # 和前面的话太像（二元组相似度）就收尾
    recent_messages: int = Field(default=20, ge=0, le=200)  # 比对最近多少句互聊
    max_failures: int = Field(default=3, ge=1, le=20)  # 连续几次调用出错，当天停掉生活
    budget_alarm: float = Field(default=0.9, gt=0, le=1)  # 生活日上限或聊天月预算用到此比例就当天停
    report_weekday: int = Field(default=1, ge=1, le=7)  # 每周几（ISO，1 = 周一）写上一周的自检
    report_examples: int = Field(default=3, ge=0, le=10)  # 自检报告里最多举几个重复的例子


class StoryStep(_Strict):
    """长线剧情的一步：开始后第几天（整数或 [最少, 最多]）发生什么。

    text 是不带主语的自然话（「嗓子有点疼，泡了杯蜂蜜水」），可用 {name}（她）、{other}（同伴）、
    {domain}（擅长领域）、{new}（新住户）、{gone}（搬走的人）。tags 交给状态插件（P3）按反射表改
    心情精力；本系统不直接改。rest：这天宅在宿舍歇着（生病时），空闲时段不出门。"""

    day: int | list[int] = 0
    text: str = Field(min_length=1, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=6)
    place: str = Field(default="dorm", max_length=20)  # dorm / 公共房间 id / out（外面）
    rest: bool = False

    @field_validator("day")
    @classmethod
    def _day(cls, v: int | list[int]) -> int | list[int]:
        days = v if isinstance(v, list) else [v]
        if not 1 <= len(days) <= 2 or any(d < 0 or d > 60 for d in days) or days != sorted(days):
            raise ValueError("day 写 0～60 的整数，或 [最少, 最多]")
        return v


class StorySpec(_Strict):
    """生活事件库里的一件事（可以是一步，也可以是有前因后果、持续几天的长线剧情）。

    挑不挑全由程序按时刻派生的随机数决定（可复现、不花钱）：每天的基础概率 chance，档案里提到
    likes 里的词时乘 boost（need_likes 时没提到就不会发生），算 domain 擅长练习，觉醒后再乘。
    trigger：daily 每天挑；arrive / welcome / farewell 由住户变化触发（新成员入住、模型下架）。"""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    trigger: Literal["daily", "arrive", "welcome", "farewell"] = "daily"
    group: str = Field(default="", max_length=20)  # 频率上限的分组（如 sick：每季度最多一次）
    chance: float = Field(default=0.0, ge=0, le=1)
    hours: list[int] = Field(default_factory=lambda: [9, 22], min_length=2, max_length=2)
    weekdays: list[int] = Field(default_factory=list)  # 0 = 周一；空 = 每天
    months: list[int] = Field(default_factory=list)  # 空 = 全年
    likes: list[str] = Field(default_factory=list, max_length=20)
    boost: float = Field(default=2.0, ge=1, le=10)
    need_likes: bool = False
    domain: str = Field(default="", max_length=20)
    with_other: bool = False  # 找一位同伴一起（两人都记一笔）
    cooldown_days: int = Field(default=7, ge=0, le=400)
    steps: list[StoryStep] = Field(min_length=1, max_length=8)

    @field_validator("hours")
    @classmethod
    def _hours(cls, v: list[int]) -> list[int]:
        if not 0 <= v[0] < v[1] <= 24:
            raise ValueError("hours 写 [开始, 结束)，0～24")
        return v


class LifeWayRules(_Strict):
    """空闲时去哪（L0）：按各自档案里的爱好挑，不再按编号轮换。option 键为「房间:活动」。"""

    enabled: bool = True
    options: dict[str, list[str]] = Field(default_factory=dict)  # 去处 → 档案里提到就更想去的词
    like_weight: float = Field(default=3.0, ge=0, le=20)  # 档案里提到得多的去处，权重最多加这么多
    talent_weight: float = Field(default=2.0, ge=0, le=20)  # 觉醒了的擅长相关的去处再加这么多


class TalentDomain(_Strict):
    """一个擅长领域：档案里的种子词、生活里哪些活动算练习、深耕后楼里的人怎么说她。"""

    words: list[str] = Field(min_length=1, max_length=30)
    acts: list[str] = Field(default_factory=list, max_length=10)  # 去处「房间:活动」或活动 id
    known: str = Field(default="", max_length=40)  # 深耕后的一句自然话，{name} 为她


class TalentTexts(_Strict):
    """擅长各阶段写进生活事件的话（{domain} 领域、{evidence} 依据、{other} 来请教的人）。"""

    awake: str = "突然意识到自己好像挺会{domain}的：{evidence}"
    ask: str = "{other}来找{name}问{domain}上的事，{name}讲了好一会儿"
    stuck: str = "{domain}上卡住了，怎么弄都不对，有点泄气"
    unstuck: str = "{domain}上那个坎忽然想通了"
    rusty: str = "好久没碰{domain}了，手有点生"


class TalentRules(_Strict):
    """觉醒擅长领域（L10）：证据只用人物卡种子 + 生活练习，不看办公室表现、不影响派活。

    萌芽（证据开始聚在一个领域，她自己还没说）→ 觉醒（种子 + 练习够了，说得出依据，经 P6 写进
    档案 likes.擅长，可在成长时间线撤回）→ 深耕（觉醒后一直在练：更常去做、楼里有人来请教）→
    瓶颈与突破（偶尔卡住几天再想通）；长期不练会生疏（经 P6 从档案去掉）。所有成员同一套规则。"""

    enabled: bool = True
    window_days: int = Field(default=30, ge=7, le=120)  # 练习看最近几天
    seed_cap: int = Field(default=3, ge=0, le=10)  # 档案提到的次数最多算几次
    seed_weight: float = Field(default=1.5, ge=0, le=10)
    sprout: float = Field(default=4, ge=1)  # 证据分到这儿算萌芽
    awake: float = Field(default=10, ge=1)  # 证据分到这儿、练习也够才觉醒
    awake_practice: int = Field(default=6, ge=1)  # 觉醒至少练过几回（只有种子不算）
    deep_days: int = Field(default=14, ge=1, le=120)  # 觉醒后再练这么多天进深耕
    deep_practice: int = Field(default=8, ge=1)
    ask_chance: float = Field(default=0.06, ge=0, le=1)  # 深耕后每天有人来请教的概率
    stuck_chance: float = Field(default=0.03, ge=0, le=1)  # 深耕后每天卡住的概率
    stuck_days: list[int] = Field(default_factory=lambda: [2, 5], min_length=2, max_length=2)
    rusty_practice: int = Field(default=1, ge=0)  # 窗口里练习不超过这么多回算生疏
    max_talents: int = Field(default=3, ge=1, le=6)
    story_boost: float = Field(default=1.6, ge=1, le=5)  # 觉醒后相关生活事件的概率乘几
    domains: dict[str, TalentDomain] = Field(default_factory=dict)
    texts: TalentTexts = TalentTexts()


class ProgressKind(_Strict):
    """一种爱好的进度（L1）：读到哪、追到哪、打到哪。

    在做的东西优先从她档案的「最爱」里挑（含 words 里的词），没有再从 things 里挑；
    生活里做了 acts 里的事（或每天按 daily 的机会）就往前推 per 格，到 total 就「看完了」，
    隔 gap_days 天再开始下一样。mid_chance：推进那天顺嘴记一句「看到第几章」的机会。"""

    words: list[str] = Field(default_factory=list, max_length=20)
    things: list[str] = Field(min_length=1, max_length=30)
    acts: list[str] = Field(default_factory=list, max_length=10)
    daily: float = Field(default=0.0, ge=0, le=1)
    unit: str = Field(default="章", max_length=4)
    total: list[int] = Field(default_factory=lambda: [10, 30], min_length=2, max_length=2)
    per: list[int] = Field(default_factory=lambda: [1, 3], min_length=2, max_length=2)
    gap_days: int = Field(default=2, ge=0, le=60)
    mid_chance: float = Field(default=0.25, ge=0, le=1)
    domain: str = Field(default="", max_length=20)
    start: str = Field(default="开始看{thing}", max_length=60)
    mid: str = Field(default="{thing}看到第{n}{unit}了", max_length=60)
    done: str = Field(default="把{thing}看完了", max_length=60)


class ProgressRules(_Strict):
    enabled: bool = True
    kinds: dict[str, ProgressKind] = Field(default_factory=dict)


class FestivalSpec(_Strict):
    """一个节日怎么过（L2）。match：日历条目的名字（含这几个字之一就算）。

    提前 prep_days 天有一位去准备（prep，一个人）；当天大家一起过（day，在场的全员）。
    origin：来历词（档案里提到这些词的成员是「家乡的节日」：当天她领头，还会有点想家 homesick）；
    空 = 谁都一样。"""

    match: list[str] = Field(min_length=1, max_length=10)
    origin: list[str] = Field(default_factory=list, max_length=20)
    prep_days: int = Field(default=1, ge=0, le=7)
    prep: str = Field(default="", max_length=60)
    day: str = Field(max_length=80)
    homesick: str = Field(default="", max_length=60)
    hours: list[int] = Field(default_factory=lambda: [18, 21], min_length=2, max_length=2)
    place: str = Field(default="lounge", max_length=20)


class FestivalRules(_Strict):
    """节日纪念日（L2，读日历 core/almanac，不另算日子）：节日提前准备、当天一起过；成员生日前一天
    有人偷偷准备礼物（礼物从寿星的「最爱」里挑）、当天大家给她过；入住周年翻翻刚来时的照片。"""

    enabled: bool = True
    festivals: list[FestivalSpec] = Field(default_factory=list, max_length=60)
    birthday_prep: str = Field(default="{other}偷偷给{name}准备了生日礼物", max_length=60)
    birthday: str = Field(default="大家给{name}过了生日，{other}送了{gift}", max_length=80)
    gifts: list[str] = Field(default_factory=lambda: ["一束花"], min_length=1, max_length=30)
    like_gift: str = Field(default="一个和{like}有关的小礼物", max_length=40)  # 寿星有「最爱」时
    joined: str = Field(default="搬进公寓{years}年了，翻了翻刚来时的照片", max_length=60)


class LowRules(_Strict):
    """健康与低谷（L4）。全程序：病了有朋友照顾，接连不顺会低落几天、有人陪、再缓过来，
    累了好几天会想好好歇一天。证据只看她自己的生活（生活事件、生活阶段），不看办公室。

    - care：她不舒服的那几天（生活阶段的小感冒，或事件库里 story.sick 的那步），常一起玩的朋友
      照顾一下（care_texts 里挑），一场病一次。
    - low：最近 window_days 天里接连有 need_bad 件不顺（story.bad / sick / stuck / farewell /
      homesick），掷 low_chance：低落 → 第 1～2 天有人陪 → 第 3～5 天缓过来。说得出原因（low_text
      里的 {cause} 是那几件事），冷却 low_cooldown_days。
    - rest：最近累（story.late / tired，加上生活阶段压精力的）达到 need_tired，掷 rest_chance：
      最近的周末歇一天（那天空闲时段在宿舍）。"""

    enabled: bool = True
    window_days: int = Field(default=7, ge=2, le=30)
    care_chance: float = Field(default=0.7, ge=0, le=1)
    care_texts: list[str] = Field(
        default_factory=lambda: ["{other}给{name}带了碗热粥"], min_length=1, max_length=20
    )
    need_bad: int = Field(default=2, ge=1, le=10)
    low_chance: float = Field(default=0.35, ge=0, le=1)
    low_cooldown_days: int = Field(default=30, ge=0, le=365)
    low_text: str = Field(default="最近{cause}，这几天有点提不起劲", max_length=60)
    comfort_texts: list[str] = Field(
        default_factory=lambda: ["{other}拉{name}出去走了走"], min_length=1, max_length=20
    )
    better_text: str = Field(default="心情慢慢缓过来了", max_length=60)
    need_tired: int = Field(default=3, ge=1, le=20)
    rest_chance: float = Field(default=0.5, ge=0, le=1)
    rest_cooldown_days: int = Field(default=14, ge=0, le=365)
    rest_text: str = Field(default="累了好几天，决定今天什么都不干，好好睡一觉", max_length=60)


class TravelDest(_Strict):
    """一个旅行目的地（L5）。days：在外面待几天（[最少, 最多]，0 = 当天来回）；likes：档案里提到这些
    词的人更想去；months：只在这几个月去（空 = 全年）；sights：途中每天做的事（不带主语）；
    souvenirs：回来给朋友带的东西。"""

    name: str = Field(min_length=1, max_length=12)
    days: list[int] = Field(default_factory=lambda: [1, 2], min_length=2, max_length=2)
    likes: list[str] = Field(default_factory=list, max_length=20)
    months: list[int] = Field(default_factory=list)
    sights: list[str] = Field(min_length=1, max_length=12)
    souvenirs: list[str] = Field(min_length=1, max_length=12)

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if not 0 <= v[0] <= v[1] <= 10:
            raise ValueError("days 写 [最少, 最多]，0～10")
        return v


class TravelTexts(_Strict):
    """旅行各步写进生活事件的话：{dest} 目的地、{sight} 途中的事、{other} 收礼物的朋友、
    {souvenir} 带回来的东西、{when} 哪天出发（「周六」）。"""

    plan: str = Field(default="在查去{dest}的车票，打算{when}出发", max_length=60)
    pack: str = Field(default="收拾好了去{dest}的行李", max_length=60)
    depart: str = Field(default="出发去{dest}了", max_length=60)
    trip: str = Field(default="在{dest}{sight}", max_length=70)
    back: str = Field(default="从{dest}回来了，给{other}带了{souvenir}", max_length=70)
    tired: str = Field(default="旅行回来腿有点酸，早早睡了", max_length=60)


class TravelRules(_Strict):
    """旅行（L5，docs/LIFE_STORY.md「旅行」）：偶尔出门几天，短途为主。全程序、不调用模型。

    计划（第 0 天）→ 前一天收拾行李 → 出发 → 途中每天一件事 → 回来给常一起玩的朋友带东西 → 回来
    当晚有点累。在外面那几天公寓里显示「不在家」、空闲时段不在楼里；**办公室工作照常**（用户定）。
    chance 是每天想出门的概率（档案提到 like_words 时 × boost）；同时最多 max_away 位在外面。"""

    enabled: bool = True
    chance: float = Field(default=0.012, ge=0, le=1)
    like_words: list[str] = Field(default_factory=list, max_length=20)
    boost: float = Field(default=2.0, ge=1, le=10)
    cooldown_days: int = Field(default=30, ge=0, le=365)
    max_away: int = Field(default=2, ge=1, le=8)
    ahead_days: list[int] = Field(default_factory=lambda: [2, 5], min_length=2, max_length=2)
    dests: list[TravelDest] = Field(default_factory=list, max_length=60)
    texts: TravelTexts = TravelTexts()


class StoryRules(_Strict):
    """生活事件库 + 长线剧情（P7 生活引擎 L0，docs/LIFE_STORY.md）。

    程序挑事（不花钱，可复现）；只发事件：心情精力 P3、记忆 P2、关系 P4、成长 P6 各自接。
    大部分日子是平淡的：每人每天最多 max_per_day 件，非日常的事有分组冷却（每季度最多病一次）。"""

    enabled: bool = True
    max_per_day: int = Field(default=2, ge=0, le=6)
    group_cooldown_days: dict[str, int] = Field(default_factory=dict)
    # 分组 → 生活阶段（chat.engine.phase）的名字：她正在这些阶段里时不排这一组的事（不会感冒又过敏）
    phase_groups: dict[str, list[str]] = Field(default_factory=dict)
    keep_days: int = Field(default=7, ge=2, le=60)  # 已过去的日程留几天（场景里「最近的事」用）
    scene_days: int = Field(default=2, ge=0, le=7)  # 群聊 / 私聊场景里带最近几天的事
    scene_items: int = Field(default=2, ge=0, le=5)
    scene_chars: int = Field(default=80, ge=0, le=300)
    stories: list[StorySpec] = Field(default_factory=list, max_length=300)
    way: LifeWayRules = LifeWayRules()
    talent: TalentRules = TalentRules()
    progress: ProgressRules = ProgressRules()  # 爱好进度（L1）
    festival: FestivalRules = FestivalRules()  # 节日纪念日（L2）
    lows: LowRules = LowRules()  # 健康与低谷（L4）
    travel: TravelRules = TravelRules()  # 旅行（L5）

    @model_validator(mode="after")
    def _unique(self) -> StoryRules:
        ids = [s.id for s in self.stories]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"生活事件 id 重复：{'、'.join(dup)}")
        reserved = sorted(
            i for i in ids if i.startswith(("talent_", "progress_", "fest_", "low_", "travel_"))
        )
        if reserved:
            used = "、".join(reserved)
            raise ValueError(
                f"talent_ / progress_ / fest_ / low_ / travel_ 开头的 id 留给程序用：{used}"
            )
        domains = {s.domain for s in self.stories} | {
            k.domain for k in self.progress.kinds.values()
        }
        bad = sorted({d for d in domains if d} - set(self.talent.domains))
        if bad:
            raise ValueError(f"生活事件的 domain 不在 talent.domains 里：{'、'.join(bad)}")
        return self


class LifeRules(_Strict):
    """生活引擎（docs/CHAT.md G4）：成员在用户不在时按作息过日子、彼此聊天。"""

    enabled: bool = True
    daily_usd: float = Field(default=0.10, ge=0)  # 生活（含记忆整理）每天最多花多少（UTC 日）
    # 给夜里整理记忆留的钱：白天的聊天、上网、朋友圈花到「上限 − 这笔」就停，记忆整理可以用满上限
    memory_reserve_usd: float = Field(default=0.02, ge=0)
    tick_minutes: int = Field(default=30, ge=5, le=360)  # 电脑开着时多久推进一次
    max_written_per_tick: int = Field(default=2, ge=0, le=10)  # 每次推进最多几件事写成聊天
    max_written_catch_up: int = Field(default=6, ge=0, le=30)  # 开机补算时最多写几件
    catch_up_hours: int = Field(default=24, ge=1, le=24 * 14)  # 补算最多往回看多少小时
    turns: int = Field(default=3, ge=1, le=6)  # 一段成员之间的对话最多几句
    pair_chance: float = Field(default=0.15, ge=0, le=1)  # 每小时有人私下聊天的概率
    estimate_usd: float = Field(default=0.002, ge=0)  # 每句的预估花费（预算检查用）
    max_tokens: int = Field(default=800, ge=64)
    guard: LifeGuardRules = LifeGuardRules()
    story: StoryRules = StoryRules()  # 生活事件库 + 长线剧情 + 擅长觉醒（L0 / L10）


class FeedSpec(_Strict):
    """一个公开订阅源（RSS / Atom）。kind 为 news 的条目分享时不标「未核实」，其余都标。"""

    name: str = Field(min_length=1, max_length=40)
    url: str = Field(pattern=r"^https?://", max_length=500)
    topics: list[str] = Field(default_factory=list, max_length=8)
    kind: Literal["news", "forum", "blog", "video"] = "news"
    enabled: bool = True


class BrowseRules(_Strict):
    """成员上网（docs/CHAT.md G7）：只读公开订阅源；搜索有每日次数上限；花费计入生活每日上限。"""

    enabled: bool = True
    feeds: list[FeedSpec] = Field(default_factory=list, max_length=30)
    refresh_hours: float = Field(default=3, ge=0.5, le=48)  # 多久重新抓一次订阅源
    shares_per_member: int = Field(default=1, ge=0, le=5)  # 每位成员每天最多分享几条
    shares_per_tick: int = Field(default=1, ge=0, le=5)  # 每次推进最多分享几条
    article_chars: int = Field(default=1500, ge=200, le=6000)  # 交给成员读的摘要最多多少字
    max_items_per_feed: int = Field(default=20, ge=1, le=100)
    keep_days: int = Field(default=7, ge=1, le=60)  # 没分享过的条目保留几天
    search_per_day: int = Field(default=5, ge=0, le=20)  # 每天最多联网搜索几次（全体合计）
    search_estimate_usd: float = Field(default=0.01, ge=0)
    estimate_usd: float = Field(default=0.002, ge=0)  # 每条分享的预估花费
    max_tokens: int = Field(default=800, ge=64)
    timeout_s: float = Field(default=10, gt=0, le=60)
    max_feed_kb: int = Field(default=1024, ge=16, le=8192)
    # 分享完有几位接着看、接着聊（2026-10-31 用户：分享就是甩过去让大家看了再讨论）；0 = 不接
    react_members: int = Field(default=2, ge=0, le=4)
    react_turns: int = Field(default=2, ge=0, le=4)  # 接着聊最多几句（每句一次调用，算生活花费）


class MemeSource(_Strict):
    """一个公开热榜（只读、不登录）。kind=json：按 path 找到条目列表、取 key 字段当标题
    （path / key 用「.」分级，数字是第几个、「*」是每一个）；kind=rss：取每条的标题。"""

    name: str = Field(min_length=1, max_length=40)
    url: str = Field(pattern=r"^https?://", max_length=500)
    kind: Literal["json", "rss"] = "json"
    path: str = Field(default="", max_length=80)
    key: str = Field(default="", max_length=40)
    enabled: bool = True


class ClassicMeme(_Strict):
    """经典梗：永远不下架、不算老梗（2026-10-31 用户：NiKo 被称为虾爹、我chovey 之类可以保留）。
    意思空着时，梗雷达第一次联网时搜一下补上（补好的在网页里能看、能改）。"""

    phrase: str = Field(min_length=1, max_length=20)
    aliases: list[str] = Field(default_factory=list, max_length=6)  # 别的说法 / 上句里会出现的词
    meaning: str = Field(default="", max_length=80)
    cue: str = Field(default="", max_length=40)  # 上句：别人说什么时会用到
    reply: str = Field(default="", max_length=40)  # 接法：熟的人怎么接


class MemeRules(_Strict):
    """网络梗（梗雷达，docs/CHAT.md「网络梗」）：每天抓一次公开热榜（$0），每天最多搜几次补充，
    交给档位里最便宜的一位提炼成梗卡（梗、意思、上句 → 接法）；花费计入生活每日上限。
    只用于生活群、私聊、成员互聊（大脑分区的意图卡），工作模式不读。
    防老梗烂梗：只收最近 max_age_days 天内火起来的；fresh_days 天没再上榜就下架（经典梗除外）；
    stale 里的老梗不收、成员说出口前删掉。8 位同一套规则，谁懂哪个梗只看各自的性格分。"""

    enabled: bool = True
    plan: str = "budget"  # 提炼用这个档位里按价格最便宜的一位
    refresh_hours: float = Field(default=24, ge=1, le=168)
    sources: list[MemeSource] = Field(default_factory=list, max_length=20)
    titles_max: int = Field(default=120, ge=10, le=400)  # 交给模型的热榜标题最多几条
    search_per_day: int = Field(
        default=2, ge=0, le=5
    )  # 每天最多联网搜几次（找新梗 1 次 + 查经典梗意思）
    search_query: str = Field(default="最近一周 新出的网络热梗 是什么意思", max_length=60)
    search_estimate_usd: float = Field(default=0.01, ge=0)
    estimate_usd: float = Field(default=0.01, ge=0)  # 提炼一次的预估花费
    max_tokens: int = Field(default=1500, ge=200)
    timeout_s: float = Field(default=10, gt=0, le=60)
    max_kb: int = Field(default=1024, ge=16, le=8192)
    fresh_days: int = Field(default=14, ge=1, le=90)  # 多少天没再上榜就下架
    max_age_days: int = Field(default=90, ge=7, le=730)  # 火起来超过这么久的不收（老梗）
    max_memes: int = Field(default=40, ge=5, le=200)  # 新梗最多存几个（经典梗另算）
    know_base: float = Field(default=0.5, ge=0, le=1)  # 懂一个新梗的基础概率
    know_traits: dict[str, float] = {}  # 性格分（1～5，3 为中）每高 1 分加多少
    know_min: float = Field(default=0.2, ge=0, le=1)
    know_max: float = Field(default=0.95, ge=0, le=1)
    learn_days: float = Field(default=4, ge=0, le=30)  # 新梗刚出来时懂的人少，这么多天后到满
    use_chance: float = Field(default=0.12, ge=0, le=1)  # 意图卡里提一句「最近在玩的梗」的概率
    use_intents: list[str] = Field(default_factory=lambda: ["respond", "joke", "tangent", "blurt"])
    quick_chance: float = Field(
        default=0.5, ge=0, le=1
    )  # 别人说了上句、她懂又有接法：程序直接接（不调模型）
    ask_chance: float = Field(default=0.6, ge=0, le=1)  # 别人玩的梗她不懂：问一句是啥梗
    reuse_days: int = Field(default=3, ge=1, le=30)  # 同一个梗她隔这么多天才再主动用
    classics: list[ClassicMeme] = Field(default_factory=list, max_length=100)
    stale: list[str] = Field(default_factory=list, max_length=200)  # 老梗烂梗：不收、说出口前删掉


class PerceptionMode(_Strict):
    """感官的一种精细程度（docs/PERCEPTION.md）：图片缩到多大、视频取几帧、感知卡写多长。"""

    image_side: int = Field(ge=256, le=4096)  # 图片缩到长边不超过这么多像素
    video_frames: int = Field(ge=1, le=64)  # 视频均匀取几帧（整段交给模型时不用）
    frame_side: int = Field(ge=128, le=2048)  # 每帧缩到长边不超过这么多像素
    grid: bool = False  # 每 4 帧拼成一张 2×2 宫格图（看图 token 约为原来的 1/4）
    native_video: bool = False  # 有带 video_in 标签的模型时，整段视频连声音直接交给它
    native_side: int = Field(default=854, ge=128, le=4096)  # 整段发送前压到长边不超过这么多像素
    native_fps: float = Field(default=2.0, gt=0, le=30)  # 整段发送前压到每秒这么多帧
    max_tokens: int = Field(ge=64, le=8000)  # 感知卡（一条消息的全部内容合计）的输出上限
    estimate_usd: float = Field(ge=0)  # 一条消息的预估花费（预算检查用）


class PerceptionListen(_Strict):
    """感官·听歌：用户发来的音频文件（歌、配乐、环境声）除了转写，再整段交给能听音乐的模型
    （``audio_in`` 标签）写一张听歌卡。录音式的语音消息不走这里。"""

    enabled: bool = True
    # 哪些格式算「音频文件」（歌）；浏览器录的语音一般是 webm / ogg，不在其中
    mimes: list[str] = Field(
        default_factory=lambda: [
            "audio/mpeg",
            "audio/mp4",
            "audio/wav",
            "audio/x-wav",
            "audio/flac",
        ],
        max_length=20,
    )
    min_s: float = Field(default=20, ge=0, le=600)  # 短于这么多秒的音频只转写（多半是语音）
    max_s: int = Field(default=600, ge=10, le=3600)  # 超过这么长只截前面这段去听
    max_tokens: int = Field(default=2000, ge=64, le=8000)
    estimate_usd: float = Field(default=0.02, ge=0)  # 听一首歌的预估花费（预算检查用）


class PerceptionRules(_Strict):
    """成员感官（M11 感知，docs/PERCEPTION.md）：把用户发的图片、语音、视频整理成感知卡给成员读。

    快速（默认）先看；用户要求细看时才补精细，转写稿和缓存都复用。花费计入聊天月预算和总预算，
    另有感官自己的月上限。
    """

    enabled: bool = True
    monthly_usd: float = Field(default=1.5, ge=0)  # 感官每月最多花多少（UTC 自然月）
    fast: PerceptionMode = PerceptionMode(
        image_side=768,
        video_frames=8,
        frame_side=512,
        grid=True,
        max_tokens=600,
        estimate_usd=0.002,
    )
    fine: PerceptionMode = PerceptionMode(
        image_side=1536,
        video_frames=24,
        frame_side=768,
        native_video=True,
        max_tokens=2000,
        estimate_usd=0.03,
    )
    # 用户在消息里写了这些词时直接用精细模式
    fine_words: list[str] = Field(
        default_factory=lambda: ["仔细看", "细看", "看清楚", "看仔细", "仔细听", "听清楚"],
        max_length=30,
    )
    max_audio_s: int = Field(default=600, ge=10, le=3600)  # 语音 / 视频声音最多转写多长
    native_max_mb: float = Field(default=20, gt=0, le=60)  # 整段视频交给模型的大小上限
    card_chars: int = Field(default=800, ge=100, le=4000)  # 每张感知卡最多保留多少字
    # 视频卡和听歌卡写得细（时间线、原话、镜头手法），单独的字数上限
    long_card_chars: int = Field(default=5000, ge=100, le=8000)
    # 视频卡里这一节小标题后面插入的固定提醒（空字符串表示不加）
    screen_text_heading: str = "屏幕上的字："
    screen_text_note: str = ""
    listen: PerceptionListen = PerceptionListen()


class MomentForm(_Strict):
    """朋友圈的一种写法（一句话、吐槽、只发表情……），按权重抽，种子由动态 / 事件和成员派生。"""

    text: str
    weight: float = Field(default=1.0, gt=0)


def _evening_hours() -> dict[int, float]:
    # 本地时间几点发：白天零星，饭点和晚上多（2026-11-02 用户：晚上多一点）
    return {
        7: 0.6, 8: 0.8, 9: 0.4, 10: 0.4, 11: 0.5, 12: 1.2, 13: 0.8, 14: 0.4, 15: 0.4,
        16: 0.5, 17: 0.8, 18: 1.2, 19: 1.5, 20: 2.0, 21: 2.4, 22: 2.4, 23: 1.6,
    }  # fmt: skip


class MomentsRules(_Strict):
    """朋友圈（docs/CHAT.md G5）。成员发动态、互相点赞评论；花费计入生活每日上限。

    M3 节奏像人（2026-11-02）：每天 0～2 条随机（``post_counts``），时间按 ``post_hours`` 抽、
    晚上多；评论 0～4 条随机（``comment_counts``），陆续到（``comment_delay_minutes``），谁来按
    关系亲疏加权（``closeness_bias``）；成员之间楼中楼回 1 轮（``reply_author``、``reply_peer``）。
    """

    enabled: bool = True
    posts_per_member: int = Field(default=2, ge=0, le=5)  # 每位成员每天最多发几条（硬上限）
    post_counts: list[float] = [0.4, 0.4, 0.2]  # 每天发 0、1、2…… 条的权重（平均 0.8 条）
    post_hours: dict[int, float] = Field(default_factory=_evening_hours)  # 本地几点 → 权重
    post_window_hours: int = Field(default=3, ge=1, le=12)  # 到点后几小时内没发出去就算了
    posts_per_tick: int = Field(default=2, ge=0, le=5)  # 每次推进最多发几条
    comments_per_tick: int = Field(default=4, ge=0, le=20)  # 每次推进最多写几条评论（含楼中楼）
    max_comments: int = Field(default=4, ge=0, le=10)  # 成员在一条动态下最多评论几条（不含楼中楼）
    comment_counts: list[float] = [0.15, 0.3, 0.3, 0.15, 0.1]  # 一条动态有 0～4 条评论的权重
    comment_delay_minutes: tuple[int, int] = (3, 240)  # 评论在发出后几分钟到几小时陆续到
    closeness_bias: float = Field(default=3.0, ge=0, le=20)  # 谁来评论：权重 1 + 这个 × 亲近度
    reply_author: float = Field(default=0.45, ge=0, le=1)  # 楼主回评论的概率（再加亲近度 × 下面）
    reply_close: float = Field(default=0.3, ge=0, le=1)
    reply_peer: float = Field(default=0.2, ge=0, le=1)  # 楼主没回时，另一位成员接一句的概率
    reply_delay_minutes: tuple[int, int] = (2, 120)
    reply_task: str = "{who}在这条底下说：「{text}」。回{who}一句。"
    post_misses: int = Field(default=2, ge=1, le=10)  # 成员一天里动态几次没过自查就不再写
    comment_misses: int = Field(default=2, ge=1, le=10)  # 一条动态底下几条评论没过自查就不再评
    like_chance: float = Field(default=0.4, ge=0, le=1)  # 每位成员给一条动态点赞的概率（不花钱）
    react_hours: int = Field(default=24, ge=1, le=168)  # 只对这么多小时内的动态点赞评论
    dedupe_hours: int = Field(default=48, ge=1, le=336)  # 和这么多小时内所有人的动态比对去重
    similarity_max: float = Field(default=0.3, gt=0, le=1)  # 正文相似度达到这个值就不发
    # 没事可说：没写成聊天、没有活、地点后面括号里是这些（待命、放空……）的事件不拿来发
    dull_activities: list[str] = ["待命", "放空", "发呆", "睡觉", "午睡", "休息"]
    talk_lines: int = Field(default=8, ge=0, le=30)  # 写成聊天的事件：喂给发帖几句原话
    talk_line_chars: int = Field(default=40, ge=10, le=200)
    talk_hours: int = Field(default=3, ge=1, le=12)  # 事件开始后多久内的聊天算这件事的
    post_forms: list[MomentForm] = []
    forward_form: str = ""  # 有转发卡片时的写法
    photo_form: str = ""  # 会配一张随手拍时的写法
    comment_forms: list[MomentForm] = []
    reply_forms: list[MomentForm] = []
    skip_word: str = "不发"  # 模型觉得没什么可发 / 可评时只输出这个
    bare_word: str = "不配字"  # 转发卡片 / 配图时只甩东西、不写字
    post_max_chars: int = Field(default=40, ge=5, le=500)  # 动态正文最多几个字（在句子边界截）
    comment_max_chars: int = Field(default=20, ge=2, le=200)
    max_chars: int = Field(default=300, ge=50, le=2000)  # 模型原文的硬上限（先于上面两项）
    max_tokens: int = Field(default=800, ge=64)
    estimate_usd: float = Field(default=0.002, ge=0)

    @field_validator("comment_delay_minutes", "reply_delay_minutes")
    @classmethod
    def _range(cls, v: tuple[int, int]) -> tuple[int, int]:
        if not 0 < v[0] <= v[1]:
            raise ValueError("要写成 [最少, 最多]，都大于 0")
        return v

    @field_validator("post_hours")
    @classmethod
    def _hours(cls, v: dict[int, float]) -> dict[int, float]:
        if not v or any(not 0 <= h <= 23 or w < 0 for h, w in v.items()) or not sum(v.values()):
            raise ValueError("要写成 本地几点(0～23): 权重，权重不能为负、不能全是 0")
        return v

    @field_validator("post_counts", "comment_counts")
    @classmethod
    def _weights(cls, v: list[float]) -> list[float]:
        if not v or any(w < 0 for w in v) or not sum(v):
            raise ValueError("权重不能为空、不能有负数")
        return v


class WanderSite(_Strict):
    """成员自己逛的一个网站：首页、搜索地址（{q} 换成搜索词）、允许打开的域名（后缀匹配）。"""

    id: str = Field(pattern=r"^[a-z0-9_]{1,20}$")
    name: str = Field(min_length=1, max_length=20)
    home: str = Field(pattern=r"^https://", max_length=300)
    search: str = Field(default="", max_length=300)
    hosts: list[str] = Field(min_length=1, max_length=10)
    enabled: bool = True


class WanderRules(_Strict):
    """成员自己上网（docs/WANDER.md）：隔离浏览器里逛白名单网站，第一期只看不互动。

    花费计入生活每日上限（``chat.life.daily_usd``）、聊天月预算和总预算。
    """

    enabled: bool = True
    auto: bool = True  # 由心跳按作息自动安排；关掉后只能在网页里手动让她去逛
    sites: list[WanderSite] = Field(default_factory=list, max_length=10)
    # 浏览器程序：依次试这些（msedge / chrome；空字符串 = Playwright 自带的 Chromium）
    channels: list[str] = Field(default_factory=lambda: ["msedge", "chrome", ""], max_length=4)
    headless: bool = True  # 不弹窗口，在后台逛
    trips_per_member_per_day: int = Field(default=1, ge=0, le=5)
    trip_chance: float = Field(default=0.3, ge=0, le=1)  # 每次心跳醒着且闲着时出门的概率
    start_hour: int = Field(default=10, ge=0, le=23)  # 自动逛只在这几个小时里（本地时间）
    end_hour: int = Field(default=23, ge=1, le=24)
    max_steps: int = Field(default=12, ge=1, le=40)  # 一次最多看几个页面（每页一次模型调用）
    max_minutes: float = Field(default=10, gt=0, le=60)
    step_wait_seconds: float = Field(default=4, ge=0, le=60)  # 每看完一页停一会儿，像人一样慢慢逛
    load_wait_seconds: float = Field(default=2.5, ge=0, le=20)  # 打开页面后等网页加载内容
    timeout_s: float = Field(default=25, gt=0, le=120)
    page_chars: int = Field(default=2500, ge=300, le=12000)  # 交给成员读的页面文字最多多少字
    max_links: int = Field(default=30, ge=5, le=80)
    estimate_usd: float = Field(default=0.002, ge=0)  # 每看一页的预估花费
    max_tokens: int = Field(default=600, ge=64)


class WatchRules(_Strict):
    """投屏陪伴（docs/WATCH.md）：用户投屏给成员看，选几位陪着一起看。

    画面只在明显变化、且离上次够久时才交给最便宜的识图模型转成文字；成员看的是文字。
    花费计入总预算和这里的每月上限（不占聊天月预算）；用完就只陪聊、不看画面。
    """

    enabled: bool = True
    monthly_usd: float = Field(default=1.5, ge=0)  # 一起看每月最多花多少（UTC 自然月）
    max_watchers: int = Field(default=3, ge=1, le=6)  # 最多几位一起看
    grab_seconds: float = Field(default=3, ge=1, le=60)  # 多久截一张（只在内存里）
    change_threshold: float = Field(default=0.06, gt=0, le=1)  # 画面变化超过这个比例才算变了
    describe_gap_seconds: float = Field(default=15, ge=5, le=600)  # 两次看画面至少隔多久
    describes_per_hour: int = Field(default=90, ge=1, le=720)  # 每小时最多看几次画面
    max_side: int = Field(default=1024, ge=320, le=2048)  # 交给模型的画面最长边（像素）
    jpeg_quality: int = Field(default=70, ge=30, le=95)
    # 看画面不用思考：思考会把字数花光、交回空白（2026-10-29 实测 4 次有 3 次为空）
    describe_max_tokens: int = Field(default=800, ge=64, le=2000)
    describe_reasoning: dict[str, Any] = Field(default_factory=lambda: {"enabled": False})
    fail_gap_seconds: float = Field(default=10, ge=0, le=600)  # 看画面失败后，至少隔多久再看
    describe_estimate_usd: float = Field(default=0.002, ge=0)
    recent_screens: int = Field(default=3, ge=1, le=10)  # 发给成员的最近几段画面描述
    react_gap_seconds: float = Field(default=60, ge=10, le=3600)  # 同一位成员主动说话至少隔多久
    room_gap_seconds: float = Field(default=25, ge=5, le=3600)  # 没人说话时，大家主动说话至少隔多久
    # 一位说完后，别的陪看成员接话的几率（每多接一句减半）和最多接几句；接话也可以选「…」不说
    followup_chance: float = Field(default=0.6, ge=0, le=1)
    max_followups: int = Field(default=2, ge=0, le=5)
    # 主动说话时这次往哪个方向说（随机抽一条，所有成员同一张表）：让她们有观点、会提问，不只描述画面
    angles: list[str] = Field(
        default_factory=lambda: [
            "说说你自己的看法或感受，可以和别人不一样",
            "问一个你好奇的问题（问用户或朋友都行）",
            "接个梗或吐槽一句",
            "补一点相关的背景或小知识（不确定就换个说法）",
            "猜一猜接下来会怎样",
        ],
        min_length=1,
        max_length=20,
    )
    history_messages: int = Field(default=16, ge=2, le=100)
    max_tokens: int = Field(default=400, ge=64)
    max_chars: int = Field(default=200, ge=20, le=2000)  # 陪看时每句最多保留多少字
    estimate_usd: float = Field(default=0.002, ge=0)  # 每句的预估花费
    max_minutes: int = Field(default=240, ge=5, le=24 * 60)  # 一次投屏最长多久，到时自动停
    max_failures: int = Field(default=5, ge=1, le=100)  # 连续几次截不到画面就停
    # 窗口列表里另外不列的窗口（标题含这些字，不分大小写）；系统后台、显卡浮层已默认滤掉
    ignore_windows: list[str] = Field(default_factory=list, max_length=50)
    # 前台窗口标题 / 进程名、安卓前台包名含这些字时暂停（不截、不看），不分大小写
    sensitive: list[str] = Field(
        default_factory=lambda: [
            "支付宝",
            "alipay",
            "银行",
            "bank",
            "paypal",
            "wallet",
            "钱包",
            "密码",
            "password",
            "1password",
            "bitwarden",
            "keepass",
            "lastpass",
            "验证码",
            "com.chinamworld",
            "com.icbc",
            "cmb.pb",
            "com.unionpay",
        ],
        max_length=100,
    )
    # adb 可执行文件（PATH 里的名字或完整路径）
    adb: str = Field(default="adb", min_length=1, max_length=300)
    scrcpy: str = Field(default="scrcpy", min_length=1, max_length=300)  # 流畅投屏窗口（可选）


class EmojiImageRules(_Strict):
    """网页里的 emoji 显示成哪套图（只改显示，消息文字和发给模型的仍是 emoji 字符）。
    用到哪个才下载、存在本机 data/emoji/，不进仓库；下载不到时用系统字体。"""

    enabled: bool = False
    style: str = Field(default="", max_length=20)  # 给网页看的样式名（只做标记）
    url: str = Field(default="", max_length=300)  # 图的地址，{code} 换成码点文件名（如 1f602）

    @field_validator("url")
    @classmethod
    def _template(cls, url: str) -> str:
        if url and (not url.startswith("https://") or "{code}" not in url):
            raise ValueError("url 要以 https:// 开头并含 {code}")
        return url


class StickerPackRules(_Strict):
    """外部表情包（dsh-meme 格式的压缩包，``core/sticker_packs.py``）：
    装在本机 data/sticker_packs/，不进仓库；可以给某位成员专用或大家通用。"""

    enabled: bool = True
    # 提示词里每套最多列几张（按小时轮换）；聊天里认得整套
    menu_size: int = Field(default=10, ge=1, le=40)
    max_mb: int = Field(default=200, ge=1, le=1000)  # 一个包压缩包 / 解压后的上限
    catalog_url: str = Field(default="", max_length=300)  # 在线目录（catalog.json）

    @field_validator("catalog_url")
    @classmethod
    def _https(cls, url: str) -> str:
        if url and not url.startswith("https://"):
            raise ValueError("catalog_url 要以 https:// 开头")
        return url


class ChatActionRules(_Strict):
    """聊天里能发什么（docs/CHAT.md「微信式动作」）：表情包、颜文字、引用、拆条、拍一拍、撤回。

    这里只管「能发什么」和简单的默认值；什么时候发、发不发、节奏和延迟由成员系统接管
    （``chat_actions.ActionPolicy``，没接管时按下面的默认值）。
    """

    enabled: bool = True
    # 成员能用的表情包：套装 → 名字（与 web/js/stickers.js 同名，有测试）
    stickers: dict[str, list[str]] = Field(default_factory=dict)
    kaomoji: list[str] = Field(default_factory=list, max_length=40)  # 颜文字示例
    emoji_images: EmojiImageRules = EmojiImageRules()  # 网页里的 emoji 显示成哪套图
    sticker_packs: StickerPackRules = StickerPackRules()  # 本机装的外部表情包
    max_segments: int = Field(default=3, ge=1, le=6)  # 一次回复最多拆成几条
    segment_gap_seconds: float = Field(default=1.2, ge=0, le=10)  # 拆条之间的固定间隔（默认值）
    recall_seconds: int = Field(default=120, ge=0, le=3600)  # 你发出后多久内可以撤回
    pat_reply: bool = True  # 被拍一拍后回一句（默认值）
    no_reply_in_group: bool = True  # 群里没被点名时允许不接话（默认值）
    drop_stale: bool = True  # 你补发新消息或撤回时，上一轮还没发出的气泡和还没开口的人作废
    # 今天上网分享过的成员发朋友圈时，有几成发成「一句话 + 转发卡片」（默认值）
    moment_forward: float = Field(default=0.5, ge=0, le=1)
    # 这些网站上的链接显示成视频卡片（只看网址，不联网）
    video_hosts: list[str] = Field(
        default_factory=lambda: ["youtube.com", "youtu.be", "vimeo.com", "bilibili.com"],
        max_length=20,
    )


class ChatRules(_Strict):
    """聊天：生活群、工作群、私聊。成员平时回应用户，按心情偶尔主动找（engine.reach）。"""

    # 每位成员一次回应最多想多久（含重试），超时就跳过这次，不卡住整轮
    think_timeout_seconds: float = Field(default=60.0, gt=0)
    life_title: str = "大家庭"  # 生活群的名字
    work_title: str = "工作群"
    budget: ChatBudgetRules = ChatBudgetRules()
    memory: MemoryRules = MemoryRules()
    life: LifeRules = LifeRules()
    browse: BrowseRules = BrowseRules()
    memes: MemeRules = MemeRules()
    moments: MomentsRules = MomentsRules()
    engine: EngineRules = EngineRules()
    watch: WatchRules = WatchRules()
    wander: WanderRules = WanderRules()

    actions: ChatActionRules = ChatActionRules()
    tier: Tier = "budget"  # 每位成员用这个档位的模型发言（没有就用该成员最便宜的）
    max_repliers: int = Field(default=2, ge=1, le=6)  # 没有 @ 人时，一条消息最多几位回应
    max_mentioned: int = Field(default=8, ge=1, le=12)  # @全员 / 所有人 / @多人 时最多几位回应
    # 你刚进群（在群里说过的话不到 newcomer_messages 条）时说「大家好」：最多 newcomer_max 位搭话
    # （10-31 用户反向找问题：新人进群 8 个人轮流自我介绍像机器人）
    newcomer_messages: int = Field(default=5, ge=0, le=100)
    newcomer_max: int = Field(default=3, ge=1, le=12)
    # 同一轮群聊里，后说的人和先说的人开头一样、条数一样（3 条以上）：后说的只发前 2 条
    same_shape_trim: bool = True
    history_messages: int = Field(default=30, ge=4, le=200)  # 发给模型的最近聊天条数
    max_tokens: int = Field(default=500, ge=64)
    max_chars: int = Field(default=800, ge=50)  # 一条消息最多保留的字数
    estimate_usd: float = Field(default=0.002, ge=0)  # 每条回应的预估花费（预算检查用）


class PlainTalkRule(_Strict):
    """说话底线规则表的一条（程序检查，不调用模型）。

    kind：phrase = 全文出现任一 patterns；ending = 最后一句匹配任一 patterns（金句收尾）；
    parallel = 连续 min_run 个以上分句开头相同（排比）：
    开头两个字相同，或开头一个字相同且在 heads 里；
    mug = 马克杯警戒线：某一句（6～max_chars 字）命中 patterns 里的空泛大词至少 min_hits 个、
    又不含 concrete 里任何一个具体所指（数字、这个、今天、书名号……），即脱离上下文也成立的金句。
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)
    problem: str = Field(min_length=1, max_length=80)  # 改写时告诉成员的问题说明
    kind: Literal["phrase", "ending", "parallel", "mug"]
    patterns: list[str] = Field(default_factory=list, max_length=60)  # 正则
    min_run: int = Field(default=3, ge=2, le=10)
    heads: list[str] = Field(default_factory=list, max_length=30)
    min_hits: int = Field(default=2, ge=1, le=10)  # mug：一句里至少几个空泛大词
    max_chars: int = Field(default=40, ge=6, le=200)  # mug：只查不超过这么多字的句子
    concrete: list[str] = Field(default_factory=list, max_length=60)  # mug：具体所指（正则）

    @field_validator("patterns", "concrete")
    @classmethod
    def _compile(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            try:
                re.compile(p)
            except re.error as exc:
                raise ValueError(f"正则写错了：{p!r}（{exc}）") from None
        return patterns

    @field_validator("heads")
    @classmethod
    def _single(cls, heads: list[str]) -> list[str]:
        bad = [h for h in heads if len(h) != 1]
        if bad:
            raise ValueError(f"heads 每项只能是一个字：{bad}")
        return heads

    @model_validator(mode="after")
    def _needs_patterns(self) -> PlainTalkRule:
        if self.kind != "parallel" and not self.patterns:
            raise ValueError(f"{self.kind} 规则至少要有一条 patterns")
        return self


class PlainTalkCheck(_Strict):
    """说话前的程序检查（成员自我监控的雏形）：只查聊天、生活、朋友圈的发言；查出问题才改写一次。"""

    enabled: bool = True
    min_chars: int = Field(default=12, ge=0)  # 短于这么多字的消息不查
    monthly_usd: float = Field(
        default=0.05, ge=0
    )  # 改写每月最多花多少（UTC 自然月；也计入聊天预算）
    estimate_usd: float = Field(default=0.0005, ge=0)  # 每次改写的预估花费（预算检查用）
    max_tokens: int = Field(default=600, ge=64)
    rules: list[PlainTalkRule] = Field(default_factory=list, max_length=30)

    @field_validator("rules")
    @classmethod
    def _unique(cls, rules: list[PlainTalkRule]) -> list[PlainTalkRule]:
        names = [r.name for r in rules]
        if len(names) != len(set(names)):
            raise ValueError(f"规则名不能重复：{names}")
        return rules


class PlainTalkRules(_Strict):
    """说话底线（去八股化）：roles 里的提示词每次调用都接上 plain_talk 提示词。

    同一步骤所有成员相同。
    """

    enabled: bool = True
    roles: list[str] = Field(default_factory=list)
    check: PlainTalkCheck = PlainTalkCheck()
