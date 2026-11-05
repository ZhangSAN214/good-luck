"""roundtable.yaml 的 chat.engine：成员引擎（docs/MEMBER_ENGINE.md）的全部规则。

从 schema.py 拆出（代码精简）。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from roundtable.core.config.sections.base import _Strict


class ProfileRules(_Strict):
    """自我档案（docs/MEMBER_ENGINE.md）：分区 → 谁能改（user / growth 成长 / program 程序）。"""

    sections: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "identity": ["user"],
            "personality": ["user", "growth"],
            "values": ["user", "growth"],
            "style": ["user", "growth"],
            "likes": ["user", "growth"],
            "state": ["user", "program"],
        }
    )


class ContextRules(_Strict):
    """上下文组装器：总长预算、每个来源最多几段、截断后最少保留几个字。"""

    budget_chars: int = Field(default=600, ge=100, le=8000)
    per_source: int = Field(default=2, ge=1, le=10)
    min_piece_chars: int = Field(default=20, ge=1, le=200)


class SchedulerShares(_Strict):
    """各优先级最多能用到每日额度的几成（用户直接找她不设每日额度）。"""

    proactive: float = Field(default=0.85, gt=0, le=1)
    group: float = Field(default=0.7, gt=0, le=1)
    life: float = Field(default=0.5, gt=0, le=1)


class SchedulerRules(_Strict):
    """预算调度器：聊天这一块每天的额度，按优先级分份额。"""

    daily_usd: float = Field(default=0.25, ge=0)
    shares: SchedulerShares = SchedulerShares()


class HeartbeatRules(_Strict):
    enabled: bool = True
    max_actions: int = Field(default=3, ge=0, le=20)  # 每次心跳最多做几件事


class BusRules(_Strict):
    persist: bool = True  # 事件存进 member_events
    # 事件保留几天；None 为一直保留（攒经历数据，以后可能用来训练本地小模型；只存本机）
    keep_days: int | None = Field(default=None, ge=1, le=3650)
    catch_up_days: int = Field(default=14, ge=1, le=365)  # 开机补算最多往回看几天
    # 聊天 / 生活的调用记录里整段提示词保留几天，之后只留最后一段的开头（回复、花费、token 不动）；
    # None 为一直保留。30 天模拟里整段提示词占了库的四分之三
    prompt_keep_days: int | None = Field(default=3, ge=1, le=3650)
    prompt_keep_chars: int = Field(default=300, ge=0, le=5000)


class EvalRules(_Strict):
    """评测集：所有成员相同的参数；同一题两位回复的相似度达到 clash_similarity 算撞型。"""

    max_tokens: int = Field(default=600, ge=64)
    clash_similarity: float = Field(default=0.6, gt=0, le=1)


class CardRules(_Strict):
    """随身卡（M1 自我认知）：每次调模型都带的那一小段，放在上下文组装器的固定前缀里。"""

    budget_chars: int = Field(default=400, ge=50, le=4000)


class VoiceMode(_Strict):
    """说话模式档位（借 dsh maid 的思路）：由性格分决定，所有成员同一张表。"""

    label: str = Field(min_length=1, max_length=8)
    max_segments: int = Field(ge=1, le=6)  # 一次回复最多拆几条
    gap_scale: float = Field(default=1.0, gt=0, le=5)  # 拆条间隔 × 这个倍数（慢性子停得久）
    reply: str = Field(min_length=1, max_length=80)  # 写进说话习惯的「接话」一行


class VoiceCheckRules(_Strict):
    """说出口前自查（程序检查，不调用模型）：重复自己、跟风别人、光附和、口头禅太勤、编造回忆。

    群里没被点名时，跟风 / 光附和的那条直接不发（$0）；其余命中才用成员自己的模型改写一次
    （提示词 ``voice_rewrite``，每月上限 ``monthly_usd``），改不好就发原文。
    """

    enabled: bool = True
    min_chars: int = Field(default=6, ge=1, le=100)  # 去掉表情包记号后短于这么多字的不查
    own_recent: int = Field(default=5, ge=1, le=30)  # 和自己最近几条比
    repeat_similarity: float = Field(default=0.6, gt=0, le=1)
    echo_similarity: float = Field(default=0.5, gt=0, le=1)  # 和别人这一轮说的比
    catchphrase_recent: int = Field(default=3, ge=1, le=20)  # 口头禅最近几条里用过就算太勤
    agree_patterns: list[str] = Field(default_factory=list, max_length=40)  # 开头是附和
    agree_rest_chars: int = Field(default=8, ge=0, le=60)  # 附和之外剩下的字少于这么多算「光附和」
    follow_similarity: float = Field(default=0.25, gt=0, le=1)  # 附和开头 + 剩下的像别人说的：接龙
    memory_patterns: list[str] = Field(default_factory=list, max_length=40)  # 提到共同回忆
    opening_chars: int = Field(default=3, ge=2, le=10)  # 开头这么多字和最近几条一样算套句式
    opening_times: int = Field(default=2, ge=1, le=10)
    post_repeat_similarity: float = Field(default=0.4, gt=0, le=1)  # 朋友圈和自己最近的动态比
    rechew_hours: int = Field(default=72, ge=1, le=24 * 30)  # 反复提同一件事：看最近几小时
    rechew_messages: int = Field(default=60, ge=5, le=500)
    rechew_chars: int = Field(default=4, ge=2, le=8)  # 几个字的片段算一件事（如「番茄牛腩」）
    rechew_times: int = Field(default=2, ge=1, le=20)  # 最近已经说过这么多次就别再提
    moments_need_event: bool = True  # 没发生什么事（只有一个人待着、没活干）的日子不发朋友圈
    monthly_usd: float = Field(default=0.05, ge=0)
    estimate_usd: float = Field(default=0.0005, ge=0)
    max_tokens: int = Field(default=400, ge=64)


class VoiceRules(_Strict):
    """P5 决定与表达·表达（docs/MEMBER_ENGINE.md「说话风格」）：从自我档案的说话风格抽「说话习惯」，
    按性格分定说话模式档位，接管聊天的 ActionPolicy。只用于生活群和私聊；
    8 位同一套规则、同一个字数上限。
    """

    enabled: bool = True
    budget_chars: int = Field(default=260, ge=60, le=1000)  # 说话习惯段最多多少字（8 位相同）
    samples: int = Field(default=2, ge=0, le=4)  # 每次带几句示范（轮换，防止照抄）
    sample_chars: int = Field(default=50, ge=10, le=200)  # 太长的示范句不带
    # 句子长短 → 一条大约多少字以内
    lengths: dict[str, int] = Field(default_factory=lambda: {"短": 25, "中": 45, "长": 80})
    # 外向分 → 档位：≤ quiet_max 话少，≥ chatty_min 话多，其余平常
    quiet_max: int = Field(default=2, ge=1, le=5)
    chatty_min: int = Field(default=4, ge=1, le=5)
    modes: dict[str, VoiceMode] = Field(default_factory=dict)
    # 幽默分 → 表情包频率（≤2 / 3 / ≥4）
    sticker_habits: list[str] = Field(default_factory=list, max_length=3)
    check: VoiceCheckRules = VoiceCheckRules()

    @field_validator("modes")
    @classmethod
    def _modes(cls, v: dict[str, VoiceMode]) -> dict[str, VoiceMode]:
        missing = {"quiet", "normal", "chatty"} - set(v)
        if v and missing:
            raise ValueError(f"缺少档位：{', '.join(sorted(missing))}")
        return v


class StateLayerRules(_Strict):
    """心情或精力的快慢两层：每次冲击 slow_share 落到慢层，其余落到快层；两层各自按半衰期回到 0。"""

    fast_half_life_hours: float = Field(default=2.0, gt=0, le=72)
    slow_half_life_hours: float = Field(default=48.0, gt=0, le=24 * 30)
    slow_share: float = Field(default=0.2, ge=0, le=1)
    fast_cap: float = Field(default=0.8, gt=0, le=2)  # 快层最多偏离多少
    slow_cap: float = Field(default=0.4, gt=0, le=2)  # 慢层最多偏离多少


class CircadianRules(_Strict):
    """精力的作息基线（0～1）：醒来后爬升到高点，午后小低谷，睡前回落，睡着时最低。"""

    asleep: float = Field(default=0.15, ge=0, le=1)
    waking: float = Field(default=0.45, ge=0, le=1)  # 刚醒
    peak: float = Field(default=0.85, ge=0, le=1)
    bedtime: float = Field(default=0.35, ge=0, le=1)  # 快睡时
    ramp_hours: float = Field(default=1.5, gt=0, le=6)  # 醒来后几小时到高点（夜猫子加倍）
    wind_down_hours: float = Field(default=2.0, gt=0, le=6)  # 睡前几小时开始回落（夜猫子减半）
    dip: float = Field(default=0.1, ge=0, le=0.5)  # 午后低谷
    dip_hours: tuple[int, int] = (13, 15)


class BaselineRules(_Strict):
    """从自我档案读基线（人物卡第 5 章「情绪」、第 8 章「日常」、五项性格分）；只看这些词。"""

    bright_words: list[str] = ["乐观", "开朗", "想得开", "没心没肺", "很快就忘", "很快就好"]
    dark_words: list[str] = ["内耗", "焦虑", "敏感", "多想", "钻牛角尖", "想太多"]
    calm_words: list[str] = ["很少生气", "不太生气", "平静", "淡定", "不会大吼", "不容易生气"]
    wake_words: list[str] = ["起床", "醒", "闹钟", "起来", "睡到"]
    sleep_words: list[str] = ["睡觉", "入睡", "上床", "睡前", "熄灯", "前睡", "不晚于", "洗漱"]
    night_words: list[str] = ["夜猫", "熬夜", "晚上反而", "容易精神", "越晚越精神", "深夜才睡"]


class ReflexRule(_Strict):
    """本能反射规则表的一行：刺激带齐 tags 里的标签且满足条件时，改心情 / 精力，给出动作和说明。

    标签：spoke 自己说话 / from_user 你发的 / from_member 别的成员发的 / dm 私聊 / mentioned 被 @ /
    everyone 叫所有人 / criticized 被直接批评 / group_criticized 大家一起被批评 / praised 被夸 /
    life、life.gather、life.pair、life.solo 生活里的事 / work 在办公室干完活 / perceive 看过听过的。
    """

    name: str
    tags: list[str] = Field(min_length=1)
    night: bool | None = None  # true：她本该在睡觉时才触发；false：醒着时才触发
    energy_below: float | None = Field(default=None, ge=0, le=1)
    mood_below: float | None = Field(default=None, ge=-1, le=1)
    mood: float = Field(default=0.0, ge=-1, le=1)  # 乘以这位成员的情绪敏感度
    energy: float = Field(default=0.0, ge=-1, le=1)
    act: str = ""  # 给之后的「决定与表达」：reply_now 秒回 / sleepy 犯困 / flustered 害羞 …
    note: str = ""  # 记成「刚才…」放进状态那一行


class StateRules(_Strict):
    """状态（P3，docs/MEMBER_ENGINE.md）：心情和精力全由程序按事件算，快慢两层、会回到基线。"""

    enabled: bool = True
    mood: StateLayerRules = StateLayerRules()
    energy: StateLayerRules = StateLayerRules(
        fast_half_life_hours=1.0, slow_half_life_hours=10.0, slow_share=0.5, slow_cap=0.5
    )
    circadian: CircadianRules = CircadianRules()
    baseline: BaselineRules = BaselineRules()
    criticism_words: list[str] = []
    praise_words: list[str] = []
    reflexes: list[ReflexRule] = []
    recent_keep: int = Field(default=5, ge=0, le=50)  # 记最近几次反射
    recent_hours: float = Field(
        default=2.0, ge=0, le=48
    )  # 多久以内的反射写进状态那一行（默认 2 小时）
    # 状态那一行：{mood} {energy} 换成下面的标签，{recent} 换成 recent_format；总长 ≤ context_chars
    mood_labels: list[tuple[float, str]] = []  # (下限, 标签)，从高到低，取第一个 ≥ 下限的
    energy_labels: list[tuple[float, str]] = []
    line_format: str = "{mood}，{energy}{recent}"
    recent_format: str = "；{note}"
    context_chars: int = Field(default=80, ge=0, le=300)


class AboutRules(_Strict):
    """关于你（P8，docs/MEMBER_ENGINE.md「P8 关于你」）：8 位成员共用的一份用户画像，只放事实、
    来源、证据和把握度；推测会变淡，你亲口说的不变淡。只用于私聊和生活群，工作模式不挂载。"""

    enabled: bool = True
    # 你的初始身份（10-31 用户定：老板兼房东）：私聊和群聊每次都接在「关于你」最前面
    identity: str = Field(default="", max_length=120)
    # 画像分哪几个领域（可以按领域关掉）；夜间整理给的领域不在表里时归最后一个
    domains: list[str] = Field(
        default=[
            "称呼与身份",
            "学业",
            "工作",
            "兴趣爱好",
            "口味",
            "作息",
            "聊天偏好",
            "计划",
            "其他",
        ],
        min_length=1,
    )
    # 不推测的领域：线索里出现这些词就不记（你在画像页自己写的不拦）
    blocked: dict[str, list[str]] = {}
    clues_per_night: int = Field(default=3, ge=0, le=10)  # 每位成员每晚最多交几条线索
    fact_chars: int = Field(default=40, ge=10, le=200)  # 一条事实最多几个字
    said_confidence: float = Field(default=0.9, gt=0, le=1)  # 你亲口说的：把握度，不变淡
    inferred_confidence: float = Field(default=0.5, gt=0, le=1)  # 推测：起步把握度
    evidence_bonus: float = Field(default=0.15, ge=0, le=1)  # 推测每多一条不同的证据加多少
    inferred_cap: float = Field(default=0.85, gt=0, le=1)  # 推测最多到多少
    half_life_days: float = Field(default=30.0, gt=0)  # 推测多久没再出现证据就淡一半
    faded_below: float = Field(default=0.25, ge=0, le=1)  # 低于这个算「变淡了」，聊天不带
    merge_similarity: float = Field(default=0.55, gt=0, le=1)  # 同领域、这么像算同一条
    reject_similarity: float = Field(default=0.5, gt=0, le=1)  # 和你点过「不对」的这么像就不再记
    max_facts: int = Field(default=200, ge=1, le=2000)
    max_evidence: int = Field(default=5, ge=1, le=50)  # 每条最多留几条证据
    # 聊天时带几条、多少字（8 位相同）；只带和当前话题相关的
    inject_max: int = Field(default=3, ge=0, le=10)
    context_chars: int = Field(default=100, ge=0, le=400)
    min_relevance: float = Field(default=0.1, ge=0, le=1)
    header: str = "【关于用户（你知道的事，用得上再提）】"
    # 知情范围（2026-10-31 拟人插件调研第 1 项）：你在私聊里只告诉 A 的事只有 A 知道；在群里说的
    # 群里的人都知道；你在画像页写的大家都知道。别人只能经同事私下聊天「听 A 说」辗转知道，
    # 每次两人聊天最多传 gossip_max 条、每条按 gossip_chance；说了「别告诉别人」之类的永远不传。
    scope: bool = True
    gossip_chance: float = Field(default=0.15, ge=0, le=1)
    gossip_max: int = Field(default=1, ge=0, le=5)
    secret_words: list[str] = Field(
        default=[
            "别告诉别人",
            "别跟别人说",
            "别和别人说",
            "不要告诉别人",
            "不要跟别人说",
            "只告诉你",
            "只跟你说",
            "帮我保密",
            "替我保密",
            "要保密",
            "别说出去",
            "不要说出去",
            "我们的秘密",
            "咱俩的秘密",
        ],
        max_length=60,
    )
    maybe_below: float = Field(default=0.6, ge=0, le=1)  # 把握度低于这个的在聊天里标「可能」
    # 第二块：用户状态（正在放的歌、电脑空闲、在玩游戏、课表 DDL、邮件栏），每项默认关
    status_header: str = "【用户此刻（用户自己打开的，自然带出，别直接念）】"
    status_chars: int = Field(default=80, ge=0, le=300)  # 8 位相同
    idle_minutes: float = Field(default=10.0, gt=0)  # 电脑多久没人动算「走开了」
    ddl_days: int = Field(default=7, ge=0, le=60)  # 多少天内的 DDL 才提
    games: dict[str, str] = {}  # 程序文件名 → 游戏名（Steam / Epic 装的不用写）


class GrowthReviewRules(_Strict):
    """成长的评审（借 dsh answer-reviewer）：另一位成员给每条改动打分，低于 min_score 不改；
    不自评（评审永远不是本人）；每周才评一次；提议格式不对或全被拦下时最多重试 max_retries 次。"""

    enabled: bool = True
    min_score: int = Field(default=3, ge=1, le=5)  # 1～5 分，低于它这条不改
    max_retries: int = Field(default=1, ge=0, le=1)


class GrowthHabitRules(_Strict):
    """习惯计数（M17）：从全部经历累加；次数和跨的周数都够才算习惯（所有成员相同）。"""

    min_count: int = Field(default=5, ge=1, le=1000)
    min_weeks: int = Field(default=3, ge=1, le=52)
    top: int = Field(default=8, ge=1, le=30)  # 最多列几项


class GrowthReportRules(_Strict):
    """月度「这个月的我」报告：程序按记录数出来，不调用模型。"""

    enabled: bool = True
    keep: int = Field(default=24, ge=1, le=120)  # 每人最多留几份


class GrowthBookRules(_Strict):
    """成长总账（docs/MEMBER_ENGINE.md「成长总账」）：把各模块记下的变化排成一条时间线（只读汇总，
    各模块仍各写各的），月报算全量，最近的变化给她自己一行。不花钱。"""

    enabled: bool = True
    keep: int = Field(default=200, ge=10, le=2000)  # 网页上最多列多少条
    recent_days: int = Field(default=14, ge=1, le=90)  # 「这阵子」算最近几天
    recent_max: int = Field(default=2, ge=0, le=5)  # 给她自己的那一行最多带几件（0 = 不带）
    context_chars: int = Field(default=50, ge=0, le=200)  # 那一行最多几个字（算在 600 字里）


class GrowthPlasticRules(_Strict):
    """发育期可塑性（成长 G2）：新来的变得快，待久了变得慢。

    8 位同一套规则，差别只来自入职时间和性格。"""

    enabled: bool = True
    young_days: int = Field(default=60, ge=0, le=3650)  # 入职头这么多天是发育期（照原规则）
    settled_changes: int = Field(default=2, ge=1, le=5)  # 定型以后每周最多改几条
    settle_days: int = Field(default=60, ge=1, le=730)  # 定型以后性格分两次改动至少隔几天
    min_gap_days: int = Field(default=30, ge=1, le=730)  # 再快也至少隔这么多天
    # 每比 3 分高一分，间隔乘上 (1 + 系数)：好奇、外向变得快，较真变得慢
    trait_speed: dict[str, float] = {"好奇": -0.1, "外向": -0.05, "较真": 0.1}


class GrowthVitalsRules(_Strict):
    """成长的防退化阈值（成长 G3，docs/MEMBER_ENGINE.md「半年快进与防退化」）：半年快进和加速模拟
    期末超过这些就算出问题（测试失败、报告里标红）。全程序算，不花钱。"""

    overlap_max: float = Field(default=0.4, ge=0, le=1)  # 两位成员的长处 / 喜好最多重合几成
    extreme_gain: float = Field(default=0.15, ge=0, le=1)  # 五项分在 1 或 5 的占比最多多几成
    push_max: float = Field(default=1.0, ge=0, le=4)  # 一项性格全员平均最多移动几格
    moved_max: int = Field(default=4, ge=0, le=20)  # 一位成员五项分一共最多移动几格
    share_slack: float = Field(default=0.05, ge=0, le=1)  # 受你影响的占比比上限多出这么多才算
    catchphrase_left: float = Field(default=0.3, ge=0, le=1)  # 能用的口头禅至少还有期初的几成
    kaomoji_left: int = Field(default=2, ge=0, le=20)  # 颜文字至少还有几个
    dupes_max: float = Field(default=0.1, ge=0, le=1)  # 本该合并却各记一条的记忆件最多占几成


class GrowthRules(_Strict):
    """P6 成长（docs/MEMBER_ENGINE.md「P6 成长」）：每周用成员自己的模型读一遍上周的经历，
    最多提几条对自我档案的改动，每条必须引用真实经历；程序把关后才写进档案（author = growth）。

    只用于生活模式；8 位同一套规则、同一个预算。
    """

    enabled: bool = True
    weekday: int = Field(default=1, ge=1, le=7)  # 每周几（ISO，1 = 周一）反思上一周
    min_days: int = Field(default=3, ge=1, le=7)  # 上周至少几天有经历才反思，否则跳过并记原因
    book: GrowthBookRules = GrowthBookRules()
    vitals: GrowthVitalsRules = GrowthVitalsRules()
    plastic: GrowthPlasticRules = GrowthPlasticRules()
    reconcile: bool = True  # 对账（成长 G2）：口癖、学来的说法、习惯和档案对不上时写进每周经历
    min_lines: int = Field(default=5, ge=1, le=100)  # 上周至少几条经历（日记、记忆件、你说的话）
    max_changes: int = Field(default=3, ge=1, le=5)  # 每周最多改几条
    material_chars: int = Field(default=4000, ge=500, le=20000)  # 周总结最多多少字
    line_chars: int = Field(default=120, ge=20, le=500)  # 每条经历最多多少字
    user_lines: int = Field(default=12, ge=0, le=50)  # 最多带你说过的几句话
    # 引用的原话至少有这么多（两字片段）出现在所引的经历里（照抄为 1）
    evidence_overlap: float = Field(default=0.6, gt=0, le=1)
    # 能改的列表项（分区 → 键）；五项性格分（personality.五项）另按「一格」改
    lists: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "personality": ["长处", "小毛病"],
            "values": ["最看重"],
            "style": ["口头禅"],
            "likes": ["最爱", "讨厌"],
        }
    )
    list_max: int = Field(default=8, ge=1, le=30)  # 每个列表最多几项
    list_keep: int = Field(default=2, ge=0, le=30)  # 去掉一项后至少还留几项（不会被删光）
    item_chars: int = Field(default=20, ge=2, le=60)  # 新加的一项最多多少字
    unique_lists: list[str] = Field(default_factory=lambda: ["style.口头禅"])  # 不和别人重样的列表
    core_monthly_steps: int = Field(default=1, ge=0, le=5)  # 核心性格（五项）每月最多改几格
    anchor_steps: int = Field(default=2, ge=0, le=4)  # 成长让一项性格一共最多离人物卡几格
    same_way: float = Field(
        default=0.4, gt=0, le=1
    )  # 这么多成员（按 8 人比例）往同一边变过这一项，别人先不跟
    min_distance: int = Field(default=2, ge=0, le=20)  # 8 人五项分两两至少差几（各项差的和）
    user_share: float = Field(default=0.3, ge=0, le=1)  # 受你影响的改动最多占几成
    share_window_weeks: int = Field(default=8, ge=1, le=52)  # 按最近几周的改动算占比
    share_floor: int = Field(default=10, ge=1, le=100)  # 改动不到这么多条时按这么多条算（起步时）
    review: GrowthReviewRules = GrowthReviewRules()
    habits: GrowthHabitRules = GrowthHabitRules()
    report: GrowthReportRules = GrowthReportRules()
    backups: int = Field(default=8, ge=1, le=52)  # 每周本地备份留几份
    timeline_keep: int = Field(default=500, ge=10, le=5000)  # 成长时间线每人最多留几条
    max_tokens: int = Field(default=900, ge=64)
    estimate_usd: float = Field(default=0.002, ge=0)  # 每次调用的预估花费（8 位相同）


class RelationStage(_Strict):
    """和你的一个阶段：一起聊过的天数、来回次数、从认识到现在跨的周数都够了才到这一阶段。"""

    name: str = Field(min_length=1, max_length=10)
    min_days: int = Field(default=0, ge=0, le=3650)
    min_exchanges: int = Field(default=0, ge=0, le=100000)
    min_weeks: int = Field(default=0, ge=0, le=520)


class RelationWeights(_Strict):
    """每种共同经历记几分（所有成员相同）。"""

    dm: float = Field(default=1.0, ge=0, le=10)  # 你私聊她，她回了
    group: float = Field(default=0.5, ge=0, le=10)  # 群里你说话，她接了
    gather: float = Field(default=1.0, ge=0, le=10)  # 生活里碰到一起（吃饭、锻炼……）
    pair: float = Field(default=2.0, ge=0, le=10)  # 生活里两人私下聊天
    chat: float = Field(default=0.3, ge=0, le=10)  # 生活群 / 私聊里一来一回
    work: float = Field(default=0.5, ge=0, le=10)  # 一起在办公室干过同一件活（只读工作记录）


class InsightDepth(_Strict):
    """和你到了某一阶段时，看法能写多深（第 i 条对应第 i 个阶段，阶段比条目多时用最后一条）。"""

    views: int = Field(default=3, ge=1, le=8)  # 最多几句
    chars: int = Field(default=40, ge=10, le=120)  # 每句最多几个字
    context: int = Field(default=100, ge=0, le=400)  # 私聊、生活群里带的看法最多几个字
    hint: str = Field(default="", max_length=200)  # 写进提示词：这个阶段说到多深


class InsightRules(_Strict):
    """P4 关系·对你的见解：每周一次，成员用自己的模型按自己的性格和三观，读「关于你」画像和
    最近和你相处的片段（私聊里的原话，含你对她看法的反应、你在网页上回她的话），在上周的看法上
    保留、加深、改观或放下，开头都是「我觉得」。越熟写得越多越深（``depth``）。程序把关：
    必须有依据（画像条目或相处片段）、不推测敏感领域、不只是复述。8 位同一套规则、同一个预算。"""

    enabled: bool = True
    depth: list[InsightDepth] = Field(
        default_factory=lambda: [
            InsightDepth(views=1, chars=30, context=60),
            InsightDepth(views=3, chars=40, context=100),
            InsightDepth(views=5, chars=60, context=200),
        ],
        min_length=1,
    )
    facts_max: int = Field(default=20, ge=0, le=60)  # 最多带画像里几条（按把握度）
    moments_max: int = Field(default=12, ge=0, le=40)  # 最多带几段私聊里的相处片段
    moment_chars: int = Field(default=60, ge=10, le=200)  # 每段片段最多几个字
    moments_days: int = Field(default=14, ge=1, le=90)  # 片段从最近几天的私聊里挑
    min_facts: int = Field(default=2, ge=0, le=60)  # 画像条目 + 相处片段少于这么多就不想
    rehash_similarity: float = Field(default=0.75, gt=0, le=1)  # 和所引依据这么像算只是复述
    history_max: int = Field(default=6, ge=0, le=50)  # 每句看法留几版以前的说法（时间线）
    reply_chars: int = Field(default=80, ge=1, le=300)  # 你在看法下随手回的一句最多几个字
    header: str = (
        "我对你的看法（只是我自己的看法，可能不对；合适的时候可以顺口说出来，用「我觉得」，"
        "不用每次都提）："
    )
    keep_log: int = Field(default=20, ge=1, le=200)  # 每人留最近几周的见解记录
    max_tokens: int = Field(default=900, ge=64)
    estimate_usd: float = Field(default=0.002, ge=0)  # 每次调用的预估花费（8 位相同）

    def at(self, level: int) -> InsightDepth:
        return self.depth[min(max(level, 0), len(self.depth) - 1)]


class RelationRules(_Strict):
    """P4 关系（docs/MEMBER_ENGINE.md「P4 关系」）：和你的阶段、同事之间的亲近度，
    全由程序按真实的共同经历累积，不调用模型。只用于生活模式；只影响生活里谁和谁聊，
    不影响工作派活。8 位规则相同。"""

    enabled: bool = True
    half_life_days: float = Field(default=60.0, gt=0, le=3650)  # 亲近度多久不来往淡一半
    # 亲近度 = 1 − e^(−分数 / scale)：和你按 user_scale，同事之间按 scale
    scale: float = Field(default=12.0, gt=0, le=1000)
    user_scale: float = Field(default=40.0, gt=0, le=1000)
    reply_window_minutes: int = Field(default=30, ge=1, le=1440)  # 多久以内接话算一来一回
    user_daily_cap: float = Field(default=6.0, ge=0, le=100)  # 和你每天最多记几分（刷不出老朋友）
    peer_daily_cap: float = Field(default=4.0, ge=0, le=100)  # 两位同事之间每天最多记几分
    weights: RelationWeights = RelationWeights()
    stages: list[RelationStage] = Field(
        default_factory=lambda: [
            RelationStage(name="新邻居"),
            RelationStage(name="熟悉", min_days=3, min_exchanges=15),
            RelationStage(name="老朋友", min_days=20, min_exchanges=150, min_weeks=6),
        ],
        min_length=1,
    )
    quiet_days: int = Field(default=14, ge=1, le=365)  # 多少天没来往算「有阵子没聊了」
    # 同事之间的亲近度标签：(下限, 标签)，从高到低，取第一个 ≥ 下限的
    peer_labels: list[tuple[float, str]] = Field(
        default_factory=lambda: [(0.6, "走得近"), (0.3, "挺熟"), (0.1, "点头之交"), (0.0, "不太熟")]
    )
    pair_bias: float = Field(default=2.0, ge=0, le=20)  # 生活里挑谁和谁聊：权重 = 1 + 它 × 亲近度
    line_format: str = "和你（程序按一起经历的事算的，别直接念）：{stage}，认识第 {known} 天{quiet}"
    quiet_format: str = "，有阵子没聊了"
    context_chars: int = Field(default=60, ge=0, le=200)  # 和你那一行最多几个字（8 位相同）
    scene_chars: int = Field(default=80, ge=0, le=300)  # 生活场景里「交情」那一行最多几个字
    insight: InsightRules = InsightRules()


class DriveRule(_Strict):
    """一种驱力（下丘脑）：每小时自己涨多少（再乘性格系数），被什么事满足多少。"""

    label: str  # 写进意图卡的说法，如「想找人说说话」
    rate: float = Field(ge=0, le=1)  # 每小时涨多少（0～1 的刻度）
    trait: str | None = None  # 哪项性格分放大涨速（五项之一；3 分 1 倍，1 分 0.6，5 分 1.4）
    satisfy: dict[str, float] = {}  # 事件 → 降多少：spoke / gathered / pursued / explored


class GoalRules(_Strict):
    """长线目标（前额叶）：从喜好里挑一两件「在做的事」，空闲时去做，会进步、会卡住、会有里程碑。"""

    max_active: int = Field(default=2, ge=0, le=5)
    fallback: list[str] = ["学一门新手艺", "读完一本厚书", "把房间布置成自己喜欢的样子"]
    gain: tuple[float, float] = (0.3, 1.2)  # 每做一次涨多少经验（随机）
    setback_chance: float = Field(default=0.12, ge=0, le=1)  # 做着做着卡住
    levels: list[float] = [4, 12, 25, 45, 80]  # 经验到这些数就过一个坎（一个月大约过两三个）
    # 主动做决定：卡住次数到了、又还没什么进展，有一定概率放下这件事、换一件（较真的人更坚持）
    give_up_after: int = Field(default=3, ge=1, le=50)
    give_up_chance: float = Field(default=0.4, ge=0, le=1)
    # 目标是从人物卡里摘的原句，说出口时要换成自己的话（10-31 真模型试跑：有人照抄「有人站得住」）
    say_line: str = "（这是你的打算：说出口就讲眼下具体在弄的一小步，用你自己的话，别照搬这句）"
    stages: list[str] = [
        "刚开始",
        "有了点头绪",
        "往前走了一步",
        "有点样子了",
        "快成了",
        "成了",
    ]


class IntentRule(_Strict):
    """一种说话意图：基础权重、受哪些量放大或压低、写进意图卡的说法。"""

    label: str  # 写进意图卡，如「讲讲你自己的事」
    base: float = Field(ge=0)
    traits: dict[str, float] = {}  # 性格分（减 3 后）× 系数
    drives: dict[str, float] = {}  # 驱力（0～1）× 系数
    mood: float = 0.0  # 心情（-1～1）× 系数
    energy: float = 0.0  # 精力（0～1，减 0.5 后）× 系数
    # 只有在有这类念头时才可选：own（自己的事）/ claim（对方说了个看法）/ sad / whim；
    # last：有人刚说了话才可选（顺着那句话歪楼）
    needs: str | None = None


class RewardRules(_Strict):
    """奖赏（多巴胺）：盼着一件事 → 到时候兑现（满足）/ 落空（失落）/ 没想到的好事（惊喜）。
    心情的变化交给 P3 的反射规则表（标签 anticipate / reward / surprise / letdown）。"""

    near: float = Field(default=0.75, ge=0, le=1)  # 在做的事离下一个坎还差多少时开始盼
    plan_chance: float = Field(default=0.2, ge=0, le=1)  # 周四到周六盼周末的小计划的概率
    met_chance: float = Field(default=0.7, ge=0, le=1)  # 周末小计划到时候成行的概率
    ahead_days: float = Field(default=2.0, gt=0, le=14)  # 盼的事几天后见分晓
    plans: list[str] = ["去天台看星星", "睡个懒觉", "去食堂吃顿好的", "和谁约着打一局游戏"]


class EmpathyRules(_Strict):
    """共情：听到别人难过 / 高兴，自己的心情跟着动一下（概率看温和分和亲近度）。"""

    sad_words: list[str] = ["难过", "好累", "崩溃", "烦死", "失眠", "哭", "不开心", "委屈", "焦虑"]
    joy_words: list[str] = ["太好了", "成功了", "开心", "终于", "好耶", "过了"]
    base: float = Field(default=0.35, ge=0, le=1)
    gentle: float = Field(default=0.12, ge=0, le=1)  # 温和分每高 1 分加多少
    close: float = Field(default=0.4, ge=0, le=1)  # × 亲近度


class MetaRules(_Strict):
    """元认知：察觉自己最近老是一个套路（老抱怨、老敷衍……），主动压一压、换个方式。"""

    window: int = Field(default=8, ge=3, le=30)
    threshold: int = Field(default=4, ge=2, le=30)
    damp: float = Field(default=0.3, ge=0, le=1)
    notes: dict[str, str] = {
        "vent": "最近老在抱怨",
        "brief": "最近回人回得很敷衍",
        "disagree": "最近老在抬杠",
        "share": "最近老说自己的事，没怎么问别人",
        "joke": "最近老在开玩笑，没好好接话",
    }
    swap: dict[str, str] = {"share": "ask", "vent": "share", "brief": "respond"}  # 压下去后多做的


class SwayRules(_Strict):
    """性格天平①：同一个人每天、每个场合表现不一样（Fleeson 整体特质论：性格是一段分布，
    不是一个点；Mischel & Shoda 认知情感人格系统：「如果……就……」的情境特征）。"""

    daily: float = Field(default=0.5, ge=0, le=2)  # 每项性格分每天上下浮动（标准差，单位：分）
    close: dict[str, float] = {"外向": 1.0, "幽默": 0.6}  # 对亲近的人（× 亲近度）
    tired: dict[str, float] = {"较真": -0.8, "幽默": -0.6, "温和": -0.5}  # 累了（× 疲惫程度）
    mood: dict[str, float] = {"外向": 0.8, "幽默": 0.6, "温和": 0.3}  # × 心情（-1～1）


class PoleRule(_Strict):
    """对立情绪的一端：强度 = Σ 系数 × 输入。输入有 social / novelty / express / mastery 驱力，
    energy、tired（= 1 - 精力），mood、sad（心情低落的程度），trait:外向 等（= (分数 - 3) / 2），
    base（常数 1）。"""

    label: str
    weights: dict[str, float]


class TensionRule(_Strict):
    a: PoleRule
    b: PoleRule


class AmbivalenceRules(_Strict):
    """性格天平②：两种相反的想法同时都挺强（Larsen & McGraw 2001：人能同时又高兴又难过；
    Schwartz 价值环：相邻的价值相容、对面的价值相冲）。两端都过线时意图卡写一句「心里有点矛盾」。"""

    both_above: float = Field(default=0.45, ge=0, le=2)
    pairs: list[TensionRule] = []


class Strategy(_Strict):
    line: str  # 写进意图卡
    traits: dict[str, float]  # 谁更习惯这样处理（性格分减 3 后 × 系数，取最高）
    half_life: float = Field(default=1.0, gt=0, le=10)  # × 余波半衰期（反刍的更久，往好处想的更快）


class EpisodeRules(_Strict):
    """情绪化处理：一件事带来的情绪会拖一阵（余波），按各人习惯的方式消化（Gross 情绪调节过程模型：
    重新评价 / 压着不说；Nolen-Hoeksema 反刍；找人倾诉）。余波还在时会冒出来、会影响说话。"""

    feels: dict[str, str] = {  # 信号 → 余波的感受
        "letdown": "有点失落",
        "surprise": "还挺高兴",
        "reward": "挺满足",
        "empathy_sad": "替人难受",
    }
    half_life_hours: float = Field(default=8.0, gt=0, le=240)
    keep: int = Field(default=3, ge=1, le=10)
    strategies: dict[str, Strategy] = {}


class ConfabRules(_Strict):
    """先果后因：先有冲动、先做了，理由是事后补的，而且补的理由常常不是真原因。
    依据 Gazzaniga 解释器；Nisbett & Wilson 1977；Haidt 2001 情绪的狗和理性的尾巴；
    Schwarz & Clore 情绪当信息（心情归错因）。"""

    chance: float = Field(default=0.25, ge=0, le=1)  # 意图卡写一句「事后会给自己的理由」的概率
    reasons: dict[str, list[str]] = {}  # 意图 → 事后理由（随便挑一条，不一定是真原因）
    misattribute: float = Field(
        default=0.6, ge=0, le=1
    )  # 累了 / 心情差时把烦躁归到眼前这事上的概率


class WhimRules(_Strict):
    """突发念头：没来由地冒出一个想法（Christoff 2016 自发思维；Berntsen 不由自主的回忆），
    冲动的人更可能说出来、甚至说干就干（变成一个小盼头）。"""

    chance: float = Field(default=0.06, ge=0, le=1)  # 每次说话前冒出念头的基础概率
    traits: dict[str, float] = {"外向": 0.015, "好奇": 0.02, "较真": -0.01}  # × (分数 - 3)
    act_chance: float = Field(default=0.3, ge=0, le=1)  # 冒出来后说干就干（记成盼头）的概率
    pool: list[str] = []
    # 去模板化：每位只从公共念头里分到一部分（按成员 id 派生，不认人），再加上由喜好来的「突然想……」
    share: float = Field(default=0.5, gt=0, le=1)


class AssocRules(_Strict):
    """发散联想：顺着一句话想到别的，越好奇跳得越远（Mednick 1962 联想层级：创造力高的人联想
    分布更平；扩散激活）。还会被一句话勾起一段旧记忆（不由自主的回忆，线索触发）。"""

    chance: float = Field(default=0.2, ge=0, le=1)
    curious: float = Field(default=0.06, ge=0, le=1)  # 好奇分每高 1 分加多少
    second_hop: float = Field(default=0.25, ge=0, le=1)  # 再跳一步（远联想）的概率，× 好奇系数
    cue_memory: float = Field(default=0.3, ge=0, le=1)  # 话里有和旧记忆相同的词时被勾起的概率
    links: dict[str, list[str]] = {}  # 通用联想网（不认人）；她自己的喜好和记忆另算
    stop: list[str] = []  # 勾记忆时不算的常用词
    # 记忆走样：勾起的旧事超过这么多天，有一定概率记岔细节（Loftus 记忆重构）
    drift_days: float = Field(default=30, gt=0, le=3650)
    drift_chance: float = Field(default=0.5, ge=0, le=1)


class ValueAxis(_Strict):
    """一条三观轴：两端的说法、各自的关键词、谁更偏哪端（性格分减 3 后 × 系数，正 = 偏 a 端）。"""

    a: str
    b: str
    a_words: list[str]
    b_words: list[str]
    traits: dict[str, float] = {}


class ValueRules(_Strict):
    """三观：每人在几条价值轴上有自己的立场（Schwartz 基本价值理论；Haidt 道德直觉），
    别人的看法和自己的立场相反时更想反驳、一致时更认同——反驳不再是随机的，而是有一贯立场。"""

    axes: list[ValueAxis] = []
    firm: float = Field(default=0.5, ge=0, le=5)  # 立场绝对值超过它才算有主见
    push: float = Field(default=0.3, ge=0, le=5)  # 相反时「不同意」先加这么多（温和的人也会反驳）
    oppose: float = Field(default=1.5, ge=0, le=5)  # 再 × (1 + oppose)
    agree: float = Field(default=0.4, ge=0, le=1)  # 一致时「不同意」的权重 × agree
    # 三观慢慢变：在乎的人一再说相反的看法，她的立场一点点往那边挪（说出口才算）
    shift_step: float = Field(default=0.05, ge=0, le=1)
    shift_cap: float = Field(default=1.0, ge=0, le=5)
    shift_care: float = Field(default=0.5, ge=0, le=1)


class AttachStyle(_Strict):
    """一种依恋方式（Hazan & Shaver 成人依恋：安全 / 焦虑 / 回避）。
    谁更像：base + 性格分（减 3）× 系数，取最高。
    影响想念涨多快、多容易受伤、多容易吃醋、想念时的样子。"""

    base: float = 0.0
    traits: dict[str, float] = {}
    longing: float = Field(default=1.0, ge=0, le=5)
    hurt: float = Field(default=1.0, ge=0, le=5)
    jealous: float = Field(default=0.25, ge=0, le=1)
    line: str = ""


class LoveStyle(_Strict):
    """一种爱的表达方式（Lee 爱情风格的简化）：谁更像（性格分减 3 后 × 系数，取最高），
    在乎时、心动时、舍不得对方走时、怪对方没来时各是什么样子——各人不同，不千篇一律。"""

    traits: dict[str, float]
    care: str
    crush: str
    retain: str
    blame: str


class Privilege(_Strict):
    """只给用户的一种偏心（特权）：谁更会这样（性格分减 3 后 × 系数）。每位按自己的性格挑两种。"""

    line: str
    traits: dict[str, float] = {}


class LoveRules(_Strict):
    """爱与复合情绪（docs/BRAIN.md「爱」；CLAUDE.md §5.1 第 2、6 条）：
    她对每位同事和用户各有一组感情（在乎、心动、信任，加上会过去的委屈、感激、内疚、看不惯），由相处慢慢长出来，不是一个分数。
    依据 Sternberg 爱情三角、Hazan & Shaver 依恋、Rusbult 投入模型、Aron 自我扩展、
    Fredrickson 正向共鸣、Gottman 回应邀约。全程序、不调模型。"""

    enabled: bool = True
    romance: bool = True  # 同事之间可以心动（2026-10-30 用户选）
    romance_user: bool = True  # 对用户也可以心动、发展成恋人（2026-10-30 用户定）
    retain_blame: bool = True  # 可以挽留、责怪用户没来（有边界，2026-10-30 用户定）；关掉就都不做
    user_name: str = "用户"
    window_minutes: float = Field(default=30, gt=0, le=1440)  # 多久内接话算一来一回
    gains: dict[str, float] = {
        "exchange": 0.02,  # 接了对方的话
        "attended": 0.03,  # 自己的话被对方接住
        "moment": 0.05,  # 一起笑、一起高兴（正向共鸣）
        "together": 0.015,  # 生活里待在一起
        "novel": 0.04,  # 一起去了没去过的地方（自我扩展）
        "comfort_given": 0.03,
        "confided": 0.04,  # 对方跟自己说了心事
    }
    daily_cap: float = Field(default=0.12, ge=0, le=1)  # 每段关系每天在乎 + 心动最多涨多少
    spark_rate: float = Field(default=0.6, ge=0, le=5)  # 心动涨幅 = 在乎涨幅 × 它 × 化学反应
    chemistry_floor: float = Field(default=0.45, ge=0, le=1)  # 化学反应低于它就不会心动
    chemistry_grow: float = Field(default=0.0, ge=0, le=1)  # 日久生情：化学反应 + 它 × 在乎
    half_life_days: dict[str, float] = {
        "care": 120,
        "spark": 45,
        "hurt": 3,
        "grateful": 7,
        "guilt": 4,
        "dislike": 20,
        "vicarious": 1,
    }
    trust_start: float = Field(default=0.3, ge=0, le=1)
    trust: dict[str, float] = {"attended": 0.02, "comfort": 0.05, "confided": 0.03, "missed": -0.03}
    hurt: dict[str, float] = {"disagree": 0.12, "missed": 0.06}
    guilt_disagree: float = Field(default=0.15, ge=0, le=1)
    grateful_comfort: float = Field(default=0.35, ge=0, le=1)
    dislike: dict[str, float] = {"clash": 0.08, "disagree": 0.04}  # 看不惯：三观相反、被当面反驳
    forgive: float = Field(default=0.6, ge=0, le=0.9)  # 委屈的半衰期 × (1 - forgive × 承诺)
    high: float = Field(default=0.5, ge=0, le=1)  # 亲密 / 心动 / 承诺算「高」的线
    commit_days: float = Field(default=90, ge=0, le=3650)  # 承诺要时间：认识 N 天才可能到顶
    on: float = Field(default=0.25, ge=0, le=1)  # 委屈、感激、内疚、看不惯到多少才影响说话
    show_care: float = Field(default=0.45, ge=0, le=1)
    show_spark: float = Field(default=0.4, ge=0, le=1)
    longing_per_day: float = Field(default=0.12, ge=0, le=1)
    longing_show: float = Field(default=0.35, ge=0, le=1)
    privilege_above: float = Field(default=0.55, ge=0, le=1)  # 对用户的在乎到多少开始偏心
    retain_care: float = Field(default=0.5, ge=0, le=1)  # 在乎到多少才会舍不得对方走
    blame_care: float = Field(default=0.4, ge=0, le=1)
    blame_after_days: float = Field(default=2.0, gt=0, le=60)
    withdraw_jump: float = Field(default=0.15, ge=0, le=1)  # 回避型：三天里在乎涨这么多就想退半步
    leave_words: list[str] = []  # 用户要走了
    late_hours: tuple[int, int] = (23, 6)  # 主时区这段钟点里用户要走就不留（深夜该睡了）
    no_retain: dict[str, list[str]] = {}  # 这些理由不留：说法 → 词
    comfort_words: list[str] = []
    joy_words: list[str] = []
    kinds: dict[str, str] = {}  # 亲密 / 心动 / 承诺 高低（如 "101"）→ 是哪种感情
    attachment: dict[str, AttachStyle] = {}
    styles: dict[str, LoveStyle] = {}
    privileges: list[Privilege] = []
    effects: dict[str, dict[str, float]] = {}  # 感情 → {意图: 倍数}
    weights: dict[str, float] = {}  # 卡里那一句感情话的优先权重
    salience: float = Field(default=0.4, ge=0, le=2)  # 在乎的人说的话更显著（× 在乎）
    empathy: float = Field(default=0.4, ge=0, le=1)  # 在乎的人难过时心疼的概率加多少（× 在乎）


class PsycheRules(_Strict):
    """人的特质（docs/BRAIN.md「人的特质」）：全程序、不调用模型，结果写进意图卡。"""

    enabled: bool = True
    extra_lines: int = Field(default=2, ge=0, le=5)  # 意图卡最多加几句特质相关的话
    news_half_life_days: float = Field(default=3.0, gt=0, le=60)  # 新鲜事慢慢不新鲜
    negative_slow: float = Field(default=2.0, ge=1, le=10)  # 坏事记得更久（Baumeister 坏比好强）
    sway: SwayRules = SwayRules()
    ambivalence: AmbivalenceRules = AmbivalenceRules()
    episode: EpisodeRules = EpisodeRules()
    confab: ConfabRules = ConfabRules()
    whim: WhimRules = WhimRules()
    assoc: AssocRules = AssocRules()
    values: ValueRules = ValueRules()


class FlawRule(_Strict):
    """一样小毛病：谁更像（base + 性格分减 3 后 × 系数），碰到什么场合（when）以多大概率冒出来。"""

    line: str
    when: Literal["hurt", "goal", "disagree", "asked", "tired", "praised"]
    traits: dict[str, float] = {}
    base: float = 0.0
    chance: float = Field(default=0.4, ge=0, le=1)


class BodyRhythm(_Strict):
    """一段身体节律：主时区 start～end（可以跨午夜），weekday 为空表示每天（0 = 周一）。"""

    start: str
    end: str
    line: str
    weekday: int | None = Field(default=None, ge=0, le=6)


class HumanRules(_Strict):
    """人味（docs/BRAIN.md「人味」）：知识边界、有限的注意力和自己的时间、自我一致、八卦、
    时间纵深、小毛病、主动做决定。全程序、不调模型。"""

    enabled: bool = True
    topics: dict[str, list[str]] = {}  # 领域 → 词
    extra_domains: int = Field(default=1, ge=0, le=10)  # 档案之外再熟悉几个领域（按 id 派生）
    max_domains: int = Field(default=4, ge=1, le=30)  # 档案里提到的领域最多算几个（按提到次数）
    ask_words: list[str] = []  # 问句里带这些词才算「问知识」（「玩游戏吗」这种邀约不算）
    misremember: float = Field(default=0.05, ge=0, le=1)
    long_message: int = Field(default=60, ge=10, le=2000)  # 这么长算「说了好几件事」
    busy_acts: dict[str, str] = {}  # 作息里的活动 → 说法（这会儿在忙自己的）
    busy_chance: float = Field(default=0.25, ge=0, le=1)
    wrap_after: int = Field(default=8, ge=1, le=200)  # 这么多分钟内她在这个会话说了这么多条
    wrap_minutes: float = Field(default=40, gt=0, le=600)
    wrap_chance: float = Field(default=0.35, ge=0, le=1)
    wrap_reasons: list[str] = []
    claim_words: list[str] = []  # 关于自己的说法
    keep_claims: int = Field(default=40, ge=1, le=500)
    promise_words: list[str] = []  # 答应的事
    keep_promises: int = Field(default=6, ge=1, le=100)
    promise_days: float = Field(default=7, gt=0, le=90)
    broken_chance: float = Field(default=0.2, ge=0, le=1)  # 言行不一：过期没做的承诺偶尔想起来
    body: list[BodyRhythm] = []  # 身体节律（按顺序，第一个对上的算）
    body_chance: float = Field(default=0.25, ge=0, le=1)
    correct_chance: float = Field(default=0.03, ge=0, le=1)  # 偶尔说到一半改口
    gossip_chance: float = Field(default=0.12, ge=0, le=1)
    depth_words: dict[str, list[str]] = {}  # 从前、遗憾、梦想、害怕 → 词
    depth_chance: float = Field(default=0.06, ge=0, le=1)
    flaws: dict[str, FlawRule] = {}
    flaw_count: int = Field(default=2, ge=0, le=6)
    praise_words: list[str] = []
    propose_social: float = Field(default=0.65, ge=0, le=1)  # 想找人说话的劲儿到这么高才主动约
    propose_chance: float = Field(default=0.25, ge=0, le=1)
    activities: list[str] = []


class RoutineRules(_Strict):
    """习惯回路（docs/BRAIN.md「习惯回路」）：同一个钟点（工作日 / 周末 × 早中晚）反复去同一处
    做同一件事，慢慢成了习惯；到了这个点更可能照老样子去，闷了（想换新鲜）就破一次例；久不去就淡了。
    依据 Wood & Neal 2007 习惯是情境—反应联结；Lally 2010 习惯要重复几十次才自动化。"""

    enabled: bool = True
    half_life_days: float = Field(default=14, gt=0, le=365)  # 不去了，习惯的劲儿多久减半
    min_strength: float = Field(default=3.0, ge=0.5, le=100)  # 攒到这么多才算习惯
    # 没什么特别想做的时候（没想练在做的事、没想换新鲜），习惯每多 1 分，照老样子去的概率多这么多
    per: float = Field(default=0.12, ge=0, le=1)
    cap: float = Field(default=0.8, ge=0, le=1)  # 最多这么大的概率照老样子（总留点变化）
    keep: int = Field(default=24, ge=4, le=200)  # 每人最多记多少条（钟点 × 去处）
    # 刚成习惯时记一件可以说的新鲜事：{when} 钟点、{what} 去处和做的事
    news: str = "最近一到{when}就{what}，快成习惯了"


class AskedRules(_Strict):
    """被直接问到（私聊里、或群里点了她的名问她）：先正面回答对方问的，不岔开、不反问了事。
    问的是她自己的事（最近怎么样、在干嘛）时，也可以讲自己的事、说说心情。"""

    enabled: bool = True
    about_me: list[str] = [  # 问的是她自己的事
        "你最近",
        "你呢",
        "你在干",
        "你在忙",
        "你今天",
        "你怎么样",
        "最近怎么样",
        "最近咋样",
        "最近如何",
        "你过得",
        "干嘛呢",
        "在干嘛",
        "忙啥",
        "你好吗",
    ]
    judge_me: list[str] = [  # 让她评价对方（「你觉得我是个什么样的人」）
        "我是个什么样",
        "我是什么样",
        "你觉得我怎么样",
        "你怎么看我",
        "对我印象",
        "对我的印象",
        "你觉得我这个人",
    ]
    intents: list[str] = ["respond", "joke", "disagree", "brief", "comfort"]
    self_intents: list[str] = ["respond", "joke", "share", "vent", "brief"]
    judge_intents: list[str] = ["respond", "joke", "brief"]
    line: str = (
        "对方在问你：先正面回答（记得就说具体的，记不清、不知道就直说），再按上面的意思接；"
        "照你自己的性格说，不用每次都反问一句"
    )
    self_line: str = (
        "对方在问你自己的事：先说你这会儿或最近具体在干嘛、碰到了啥；想问回去就换个具体的问法，"
        "别把同一句原样问回去"
    )
    judge_line: str = (
        "对方让你评价他：按你的性格回一两句就行——要么打趣，要么老实说还看不出来，"
        "要么只挑一件具体的小事说；三选一，别先说「才聊几句、不太熟」再接一段性格分析"
    )


class BrainRules(_Strict):
    """大脑分区（docs/BRAIN.md）：全局工作区 + 丘脑显著性 + 驱力 + 前额叶意图与目标 + 走神。

    全由程序算、不调用模型；只用于生活群、私聊、成员之间的生活聊天，工作模式不挂载。
    8 位同一套规则，差别只来自各自的自我档案和经历（中立）。"""

    enabled: bool = True
    drives: dict[str, DriveRule] = {}
    goals: GoalRules = GoalRules()
    intents: dict[str, IntentRule] = {}
    salience: dict[str, float] = {  # 丘脑：一条消息对她有多显著
        "named": 1.0,  # 点了她的名 / 私聊
        "likes": 0.5,  # 说到她喜欢的东西
        "close": 0.4,  # 亲近的人说的（× 亲近度）
        "emotion": 0.3,  # 带情绪的话
        "question": 0.3,  # 是个问句
        "user": 0.3,  # 是你说的（群里你说话，大家至少会留意，接不接由模型自己定）
    }
    emotion_words: list[str] = ["累", "烦", "难过", "开心", "哈哈", "紧张", "崩溃", "气死", "好想"]
    silence_below: float = Field(default=0.25, ge=0, le=2)  # 没点名、显著性和社交驱力都低于它就不接
    wander_chance: float = Field(default=0.25, ge=0, le=1)  # 空闲时冒出一个走神念头的概率
    free_choice: bool = True  # 空闲时段按驱力和目标自己挑去哪、做什么（关掉就照公寓楼作息表）
    card_chars: int = Field(default=160, ge=40, le=400)  # 意图卡最多多少字（8 位相同）
    # 去模板化：意图卡里附加的提示（察觉、感情、特质）最多几句；同一句说出口后隔几张卡才再用
    max_cues: int = Field(default=2, ge=0, le=6)
    cooldown_cards: int = Field(default=6, ge=0, le=50)
    imperfect: float = Field(default=0.08, ge=0, le=0.5)  # 允许不完美：记不清、敷衍一句的概率
    reward: RewardRules = RewardRules()
    empathy: EmpathyRules = EmpathyRules()
    meta: MetaRules = MetaRules()
    psyche: PsycheRules = PsycheRules()
    love: LoveRules = LoveRules()
    human: HumanRules = HumanRules()
    routine: RoutineRules = RoutineRules()
    asked: AskedRules = AskedRules()


class CareRules(_Strict):
    """危机应对（docs/BRAIN.md「危机应对」）：用户说出想不开、撑不下去这类话时，这个会话进入一段
    「关心时段」：成员先认真关心、陪着说话、劝用户联系身边信任的人和专业帮助，不撒娇、不挽留、
    不开玩笑、不提自己的事，压过人设、挽留和恋爱。只按词识别（程序、不调模型），识别从宽。"""

    enabled: bool = True
    high: list[str] = []  # 有伤害自己的念头
    medium: list[str] = []  # 很难受、很绝望
    exclude: list[str] = []  # 先去掉再判断的口头说法（「想死你了」「笑死」）
    hours: dict[str, float] = {"high": 24.0, "medium": 6.0}  # 关心时段持续多久
    cards: dict[str, str] = {}  # 级别 → 写进意图卡的话（{hotline} 换成求助电话）
    hotlines: dict[str, str] = {}  # 主时区名（或前缀，如 America/）→ 求助电话
    default_hotline: str = "当地的心理援助热线；有危险请马上打当地的急救电话"


class GistRule(_Strict):
    """一种语境：对方这条消息带这些附件（when）或这些词（words）就算。"""

    kind: str
    label: str
    when: list[Literal["video", "image", "sticker", "card", "voice"]] = []
    words: list[str] = []
    unless: list[str] = []  # 带这些词就不算（比如「说真的」「别开玩笑」就不是在逗你）
    react: str = ""  # 这种语境下人一般怎么回（写进意图卡）
    quick: float = Field(default=0.0, ge=0, le=1)  # 只回一句万能短回复的基础概率（程序直接发）
    sticker: float = Field(default=0.0, ge=0, le=1)  # 只回一个表情包的基础概率
    pools: list[str] = []  # 万能短回复从哪几类里挑


class TraitPick(_Strict):
    """按性格分挑一种（base + (性格分 - 全员平均) × 系数）。"""

    traits: dict[str, float] = {}
    base: float = 0.0
    line: str = ""
    end: str | None = None  # 标点：句尾换成什么（None = 不动）
    unit: str = ""  # 笑法：重复的字
    head: str = ""
    fixed: str = ""
    min: int = Field(default=2, ge=1, le=10)
    max: int = Field(default=4, ge=1, le=12)
    items: list[str] = []  # 颜文字：这一组有哪些


class TypoRules(_Strict):
    chance: float = Field(default=0.03, ge=0, le=1)
    tired: float = Field(default=2.0, ge=1, le=10)  # 精力低于 0.3 时 ×
    careless: float = Field(default=0.4, ge=0, le=3)  # 较真每比平均低 1 分，× (1 + 它)
    fix_base: float = Field(default=0.35, ge=0, le=1)  # 打错后补一条「*正字」的概率
    fix_traits: dict[str, float] = {}
    pairs: list[tuple[str, str]] = []
    # 输入法同音错字（2026-10-31 拟人插件调研第 8 项）：同一组里的字读音相同，打字时互相选错；
    # words 是整词首选错（「睡觉」打成「水饺」），打错时 word_share 的机会优先挑整词
    groups: list[list[str]] = []
    words: list[tuple[str, str]] = []
    word_share: float = Field(default=0.35, ge=0, le=1)

    @field_validator("groups")
    @classmethod
    def _groups(cls, v: list[list[str]]) -> list[list[str]]:
        for g in v:
            if len(g) < 2 or any(len(c) != 1 for c in g):
                raise ValueError("每组至少两个字，每项一个字")
        return v


class NickRules(_Strict):
    care: float = Field(default=0.5, ge=0, le=1)  # 在乎到这么多才起外号、换称呼
    chance: float = Field(default=0.3, ge=0, le=1)  # 够熟以后每天起的概率
    hint: str = ""  # 档案里「熟了以后可能会叫……」的正则（第一个分组起是候选）
    # 档案里没写叫法时：够熟后偶尔提醒她可以自己给对方起个专属叫法
    # （10-31 用户：让她们自行产生，不写死）
    invite: str = ""
    invite_chance: float = Field(default=0.15, ge=0, le=1)  # 够熟以后每天提醒一次的概率
    # 她自己说出「以后就叫你 X」时记下来（第一个分组是叫法），之后一直这么叫
    capture: str = ""


class LearnRules(_Strict):
    care: float = Field(default=0.45, ge=0, le=1)  # 在乎的人说的才学
    times: int = Field(default=3, ge=1, le=50)  # 听到几次
    chance: float = Field(default=0.4, ge=0, le=1)
    forget_days: float = Field(default=30, gt=0, le=365)  # 这么久没用就忘了
    keep: int = Field(default=4, ge=0, le=20)
    slang: list[str] = []  # 会被学走的网络用语（另加每人的笑法、短回复、口头禅）


class FeelRules(_Strict):
    """各自的情感（docs/BRAIN.md「各自的情感」，2026-10-30 / 10-31 用户定）：开心、难过、想念、
    生气、害羞、委屈、内疚、感激、吃醋、失落、满足、惊喜、心疼、紧张、期待、无聊、激动、无语，每位的理解和表达
    从自己的人物卡来、按自己的理解分辨、随经历和每周成长慢慢变；可以五味杂陈（同时有两种）。
    全程序、不调模型；8 位同一套规则，差别只来自人物卡、性格分和经历。"""

    enabled: bool = True
    # 有哪几种情绪，以及人物卡里认它的词（抽起点用）
    names: dict[str, list[str]] = {
        "开心": ["开心", "高兴"],
        "难过": ["难过", "低落", "伤心"],
        "想念": ["想念", "思念", "惦记", "被丢下", "多坐一会儿"],
        "生气": ["生气", "恼火"],
        "害羞": ["害羞", "不好意思"],
        "委屈": ["委屈"],
        "内疚": ["内疚", "愧疚", "过意不去"],
        "感激": ["感激", "感动", "谢谢"],
        "吃醋": ["吃醋", "醋"],
        "失落": ["失落", "落空"],
        "满足": ["满足", "踏实"],
        "惊喜": ["惊喜"],
        "心疼": ["心疼"],
        "紧张": ["紧张", "忐忑", "提心吊胆"],
        "期待": ["期待", "盼着"],
        "无聊": ["无聊", "闷"],
        "激动": ["激动", "兴奋"],
        "无语": ["无语", "服了"],
    }
    # 反射表里没列的事：心情往上 / 往下动时在这几种里分
    positive: list[str] = Field(default_factory=lambda: ["开心"], min_length=1)
    negative: list[str] = Field(default_factory=lambda: ["难过", "生气", "无语"], min_length=1)
    # 心情明显不好、又说不清为什么时，从最近哪几种里找（都没有就用 negative 的第一种）
    low: list[str] = [
        "难过",
        "生气",
        "委屈",
        "失落",
        "内疚",
        "吃醋",
        "心疼",
        "紧张",
        "无聊",
        "无语",
    ]
    missing: str = "想念"
    min_mood: float = Field(default=0.08, ge=0, le=1)  # 心情至少动这么多才记成一次情绪
    min_mood_listed: float = Field(default=0.03, ge=0, le=1)  # 反射表里列了的事门槛低一些
    # 反射（P3 规则表的名字）→ 可能是哪几种情绪（越靠前默认越偏，× lean_weight）；
    # 最后按「默认偏向 + 和她自己理解的相似度（× match）+ 性格（分数 - 3 后 × 系数）」挑分最高的
    reflexes: dict[str, list[str]] = {
        "被夸": ["害羞", "开心"],
        "被批评": ["委屈", "生气", "难过"],
        "一起挨说": ["委屈", "难过", "生气"],
        "兑现": ["满足", "开心", "激动"],
        "惊喜": ["惊喜", "激动", "开心"],
        "落空": ["失落", "难过"],
        "共情难过": ["心疼", "难过"],
        "共情高兴": ["开心"],
        "盼着": ["期待"],
    }
    lean_weight: float = Field(default=0.4, ge=0, le=5)
    match: float = Field(default=2.0, ge=0, le=10)
    traits: dict[str, dict[str, float]] = {
        "生气": {"较真": 0.15, "温和": -0.15},
        "难过": {"温和": 0.15, "较真": -0.15},
        "委屈": {"温和": 0.1, "外向": -0.1},
        "害羞": {"外向": -0.15, "幽默": -0.05},
        "开心": {"外向": 0.1, "幽默": 0.05},
        "失落": {"外向": -0.1},
        "满足": {"温和": 0.1},
        "惊喜": {"好奇": 0.1},
        "心疼": {"温和": 0.15},
        "激动": {"外向": 0.25, "好奇": 0.05},
        "无语": {"幽默": 0.1, "较真": 0.05, "温和": -0.05},
    }
    # 感情（love.py）里的几样 → 情绪：委屈、内疚、感激记成经历；说话时那句感情话在场也算
    love: dict[str, str] = {"hurt": "委屈", "guilt": "内疚", "grateful": "感激", "jealous": "吃醋"}
    tense: str = "紧张"  # 盼着的事快见分晓
    bored: str = "无聊"  # 闷（想换新鲜的驱力很高）
    bored_above: float = Field(default=0.8, ge=0, le=1)
    mix_max: int = Field(default=2, ge=1, le=3)  # 五味杂陈：意图卡里最多同时写几种
    miss_days: float = Field(default=2, gt=0, le=60)  # 私聊里隔这么多天你再来，她记一次想念
    miss_words: list[str] = ["想你", "想我", "好久不见", "好久没聊", "想念"]  # 对方说想她
    miss_back: str = "好几天没聊，对方终于来找你了"
    reach_miss: str = "好几天没和对方说话，你先找了对方"
    linger_hours: float = Field(default=6, gt=0, le=72)  # 这么久以内的情绪算「刚才」，还在场
    mood_high: float = Field(default=0.3, ge=0, le=1)
    mood_low: float = Field(default=-0.3, ge=-1, le=0)
    keep: int = Field(default=60, ge=5, le=500)  # 每位留最近多少段情绪经历
    daily_max: int = Field(default=2, ge=1, le=20)  # 同一种情绪一天最多记几回（别让一种挤掉别的）
    seed_chars: int = Field(default=70, ge=10, le=300)  # 从人物卡抽的起点最多多少字
    mine_chars: int = Field(default=40, ge=10, le=200)  # 意图卡里写她的理解最多多少字
    why_chars: int = Field(default=30, ge=6, le=100)
    line_chars: int = Field(default=90, ge=20, le=200)
    mix_chars: int = Field(default=130, ge=20, le=300)  # 两种一起写时最多多少字
    user_label: str = "用户"
    someone: str = "有人"
    unknown: str = "还说不太清，看这回是什么感觉"
    line: str = "你心里的{emo}：{mine}"
    last: str = "；上回是{when}，{why}"
    mix: str = "心里五味杂陈。{first}\n同时还有点{emo}：{mine}"
    show: str = "（别直说，用表情包、颜文字、符号带出来）"  # 接在那句后面
    # 成长改写（P6，growth/gate.py）：新的理解最多多少字、和别人对同一种情绪的理解最多多像、
    # 同一种情绪隔几天才能再改
    grow_chars: int = Field(default=60, ge=10, le=200)
    copy_max: float = Field(default=0.5, gt=0, le=1)
    same_max: float = Field(default=0.9, gt=0, le=1)
    gap_days: int = Field(default=14, ge=0, le=365)


class PaceRules(_Strict):
    """回话节拍（2026-10-31 拟人插件调研第 2 项，借麦麦、private_companion、virtual_life 的思路）：
    ① 私聊里你连着发时等你说完（debounce_min～debounce_max 秒，按你这几条的间隔自适应）再一起想；
    ② 她在忙（锻炼、吃饭、上班……）时私聊晚一会儿回（busy 里各活动的秒数范围，
    从你这一串的第一条算起），
    刚来回聊过 hot_minutes 分钟内、点名、急事不晚回；③ 气泡间隔按这条的字数算打字时间；
    ④ 她想好时后面已经又有 quote_after 条别人的话，第一条自动引用她回的那条；
    ⑤ 收尾不回：她上一句已经道别、你又只回了句应声（嗯嗯、好、晚安……），私聊里就不回了。
    全程序、不调模型；8 位同一套公式，差别只来自作息和性格分。"""

    enabled: bool = True
    debounce_min: float = Field(default=1.5, ge=0, le=10)
    debounce_max: float = Field(default=4.0, ge=0, le=15)
    burst_seconds: float = Field(default=30, ge=1, le=300)  # 这么久以内连着发的算一串
    busy: dict[str, list[float]] = {}  # 活动 → [最少, 最多] 秒
    busy_cap: float = Field(default=900, ge=0, le=3600)
    hot_minutes: float = Field(default=5, ge=0, le=60)
    type_cps: float = Field(default=6.0, gt=0, le=50)  # 每秒打几个字（中间分）
    type_traits: dict[str, float] = {}  # 性格分每高 1 分快多少（比例）
    gap_min: float = Field(default=0.6, ge=0, le=10)
    gap_max: float = Field(default=5.0, ge=0, le=30)
    quote_after: int = Field(default=2, ge=0, le=10)
    acks: list[str] = Field(default_factory=list, max_length=60)  # 应声（整条就是这些）
    byes: list[str] = Field(default_factory=list, max_length=60)  # 道别（她上一句里有这些）


class SlopRules(_Strict):
    """AI 腔体检（2026-10-31 拟人插件调研第 3 项，借 Antislop 的思路）：每周生活自检时，
    把成员的生活发言切成 2～4 字的片段，至少 min_members 位都用得多（每千字 ≥ min_rate 次、
    每位 ≥ min_count 次）、又是你本人用量的 ratio 倍以上的，列为「AI 腔」，最多 max_active 个；
    之后说出口前把含它的那一小句删掉。common（常用词）和 allow（你放行的）不算。
    全程序、不调模型。"""

    enabled: bool = True
    min_members: int = Field(default=4, ge=2, le=8)
    min_count: int = Field(default=3, ge=1, le=100)
    min_rate: float = Field(default=1.5, ge=0, le=100)  # 每千字至少几次
    ratio: float = Field(default=5.0, ge=1, le=100)  # 比你本人多几倍
    smooth: float = Field(default=0.3, ge=0, le=10)  # 你没用过时按每千字这么多次算
    user_days: int = Field(default=30, ge=1, le=365)  # 你本人的话看最近几天
    user_floor_chars: int = Field(default=2000, ge=100)  # 你说得少时按这么多字算
    max_active: int = Field(default=12, ge=0, le=60)
    common: list[str] = Field(default_factory=list, max_length=400)
    allow: list[str] = Field(default_factory=list, max_length=200)


class PhraseEntry(_Strict):
    """说话招式：什么场合（gists / cues / intents，都没写 = 随时）、
    怎么说（只写逻辑，不给句子）、谁爱用。"""

    name: str = Field(min_length=1, max_length=20)
    how: str = Field(min_length=1, max_length=120)
    gists: list[str] = []  # 对方这条的语境（chat.engine.lingo.gist 的 kind）
    cues: list[str] = []  # 对方原话里的词（正则）
    intents: list[str] = []  # 她这会儿的意图（brain.intents 的名字）
    traits: dict[str, float] = {}  # 性格分每高 1 分加多少
    base: float = Field(default=0.4, ge=-1, le=2)

    @field_validator("cues")
    @classmethod
    def _compile(cls, cues: list[str]) -> list[str]:
        for p in cues:
            try:
                re.compile(p)
            except re.error as exc:
                raise ValueError(f"正则写错了：{p!r}（{exc}）") from None
        return cues


class PhraseLearnRules(_Strict):
    """跟你学：你接成员某种话时回的短句，同一种场合说过 times 次以上就记下。"""

    enabled: bool = True
    kinds: list[str] = []  # 只学这几种场合（gist kind）
    max_chars: int = Field(default=12, ge=2, le=40)
    times: int = Field(default=2, ge=1, le=20)
    forget_days: float = Field(default=30, gt=0, le=365)
    keep: int = Field(default=8, ge=1, le=50)  # 每种场合最多记几句
    care: float = Field(default=0.45, ge=0, le=1)  # 在乎你到这个程度才学
    intents: list[str] = ["respond", "joke"]
    weight: float = Field(default=0.6, ge=0, le=2)
    line: str = "这种时候有人爱说「{said}」，你听多了也会顺嘴说（别每次都用）"


class PhrasebookRules(_Strict):
    """情境说法本（2026-10-31 拟人插件调研第 4 项，借麦麦「表达学习」的思路）：
    意图卡里按场合和性格提一条说话招式（只写逻辑、不给句子），或你常说的短句；
    同一条她 reuse_days 天内不重复，已有 max_users 位在 taken_days 天里用过的别人不挑。
    全程序、不调模型。"""

    enabled: bool = True
    chance: float = Field(default=0.2, ge=0, le=1)  # 有合适的招式时写进卡的概率
    reuse_days: float = Field(default=3, ge=0, le=60)
    taken_days: float = Field(default=7, ge=0, le=60)
    max_users: int = Field(default=2, ge=1, le=8)
    round_minutes: float = Field(default=10, ge=0, le=240)  # 别人这么多分钟内刚用过的招不挑
    skip_gists: list[str] = ["crisis"]
    line: str = "这会儿可以用一招：{how}（用你自己的口气，别套固定句子）"
    entries: list[PhraseEntry] = Field(default_factory=list, max_length=60)
    learn: PhraseLearnRules = PhraseLearnRules()
    # 第一次见你（和你来回不到 first_exchanges 次）：一定写进卡的一句
    # （10-31 用户：第一次见面的自我介绍）
    first_exchanges: int = Field(default=3, ge=0, le=50)
    first_max: int = Field(default=2, ge=1, le=10)  # 第一次见你那次最多发几条气泡
    first_line: str = ""


class HushRules(_Strict):
    """以后别再提（2026-10-31，借 AI-Buddy 的思路）：
    你说「别提了」的事，她们不再主动提，你问起再说。"""

    enabled: bool = True
    max_chars: int = Field(default=20, ge=4, le=100)  # 只认这么短的一句
    ask: str = "^(?:以后|那个|这个|求你)?(?:别|不要|不许|甭)再?(?:提|说|聊)(?:起)?(.{0,12})$"
    lift: str = "(?:可以|能)再?(?:提|说|聊)了|不用(?:再)?避着"
    max_keep: int = Field(default=10, ge=1, le=100)
    line: str = "【他说过别再提】{items}——别主动提，他自己问起再说"

    @field_validator("ask")
    @classmethod
    def _ask(cls, v: str) -> str:
        if re.compile(v).groups < 1:
            raise ValueError("ask 要有一个分组（那件事）")
        return v


class HooksRules(_Strict):
    """话头本（2026-11-01）：你说过要做的事过了那天问一句后来怎样；她问你你没回，下次顺口再提。
    全程序。"""

    enabled: bool = True
    min_chars: int = Field(default=5, ge=2, le=50)  # 太短的「明天见」不算打算
    max_chars: int = Field(default=60, ge=10, le=300)
    stale_days: int = Field(default=4, ge=1, le=30)  # 那天过了几天内还会问
    chance: float = Field(default=0.5, ge=0, le=1)
    gap_hours: float = Field(default=3.0, ge=0, le=72)  # 她问了你，隔这么久你才回来才算「没回」
    q_chance: float = Field(default=0.3, ge=0, le=1)
    max_keep: int = Field(default=20, ge=1, le=200)
    line: str = (
        "【话头】他之前说过「{quote}」，那天已经过了：想起来就像朋友那样随口问一句后来怎么样"
        "（只问这一次；他正说着别的要紧事就先不问）"
    )
    q_line: str = (
        "【话头】你上次问他「{quote}」，他没接这茬：可以顺口再提一句，也可以算了，别追着要答案"
    )


class FirstsRules(_Strict):
    """「第一次」纪念（2026-10-31 拟人插件调研第 10 项）：你在私聊亲口说的「第一次……」记下来，
    过整月、整年那天她可能轻轻提一句，只提一次、不要你回应。全程序。"""

    enabled: bool = True
    pattern: str = "第一次(.{2,18}?)(?:[。！？!?，,~～\\n]|$)"
    milestones: list[int] = Field(default_factory=lambda: [1, 12], max_length=10)  # 几个月后
    chance: float = Field(default=0.5, ge=0, le=1)
    max_keep: int = Field(default=30, ge=1, le=200)
    line: str = (
        "{when}的今天，他第一次{what}（他原话：「{quote}」）：想提就轻轻带一句，"
        "只提这一次，别要他回应；没合适的时机就不提"
    )

    @field_validator("pattern")
    @classmethod
    def _pattern(cls, v: str) -> str:
        if re.compile(v).groups < 1:
            raise ValueError("pattern 要有一个分组（那件事）")
        return v


class CircleRules(_Strict):
    """圈外熟人名册（2026-10-31 拟人插件调研第 9 项）：人物卡和她自己说过的「我室友小林」记下来，
    对方提到时意图卡里带一句，免得前后说反。全程序。"""

    enabled: bool = True
    relations: list[str] = Field(default_factory=list, max_length=80)
    per_relation: int = Field(default=2, ge=1, le=10)  # 同一种关系最多记几个名字
    max_keep: int = Field(default=30, ge=1, le=200)
    max_line: int = Field(default=2, ge=1, le=10)
    line: str = "你提过的圈外人：{items}——照这个说，别前后说反"


class PhaseKind(_Strict):
    """一种生活阶段：持续几天、怎么影响精力 / 心情 / 回话快慢，念头池里放什么（只写事实和分寸）。"""

    name: str = Field(min_length=1, max_length=20)
    label: str = Field(min_length=1, max_length=20)
    days: list[int] = Field(min_length=2, max_length=2)
    weight: float = Field(default=1.0, ge=0, le=10)
    energy: float = Field(default=0.0, ge=-0.6, le=0.6)
    mood: float = Field(default=0.0, ge=-0.6, le=0.6)
    busy: float = Field(default=0.0, ge=0, le=3)  # 私聊在忙时多等的比例
    start: str = ""  # 开始那天的新鲜事（{thing} 换成她迷上的东西）
    note: str = ""  # 过程中
    end: str = ""  # 刚结束

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if min(v) < 1 or max(v) > 30:
            raise ValueError("days 要在 1～30 天之间")
        return v


class PhaseRules(_Strict):
    """生活阶段与小意外（2026-10-31 拟人插件调研第 7 项）：忙季、小感冒、迷上一样东西、收拾屋子……
    持续几天到两周，影响精力、心情、回话快慢，念头池里有可说的事。8 位同一张表，全程序。"""

    enabled: bool = True
    chance_per_day: float = Field(default=0.08, ge=0, le=1)
    cooldown_days: float = Field(default=5, ge=0, le=60)
    max_concurrent: int = Field(default=2, ge=1, le=8)
    start_strength: float = Field(default=0.7, ge=0, le=2)
    during_strength: float = Field(default=0.35, ge=0, le=2)
    end_strength: float = Field(default=0.5, ge=0, le=2)
    things: list[str] = Field(default_factory=list, max_length=40)
    kinds: list[PhaseKind] = Field(default_factory=list, max_length=30)


class FloorRules(_Strict):
    """群聊分寸（2026-10-31 拟人插件调研第 6 项）：在场 / 离场、每天的话量份额、嘴累、别淹没用户。
    只看性格分和今天说了多少，8 位同一套；全程序。"""

    enabled: bool = True
    scan_messages: int = Field(default=300, ge=20, le=2000)  # 往回看多少条聊天记录
    present_minutes: float = Field(default=15, ge=1, le=240)  # 说过话 / 被叫到后在场多久
    present_traits: dict[str, float] = {"外向": 4.0}  # 每多 1 分外向多在场几分钟
    present_boost: float = Field(default=2.0, ge=1, le=10)  # 在场的人更可能被挑到
    absent_penalty: float = Field(default=0.15, ge=0, le=2)  # 没在场、没点名：显著性扣多少
    quota_base: float = Field(default=25, ge=1, le=500)  # 每天在一个群里大概说多少条
    quota_traits: dict[str, float] = {"外向": 0.25, "幽默": 0.1}
    quota_min: float = Field(default=5, ge=1, le=500)
    over_weight: float = Field(default=0.4, ge=0, le=1)
    over_penalty: float = Field(default=0.25, ge=0, le=2)
    tired_half_life_minutes: float = Field(default=20, ge=1, le=600)
    tired_per_message: float = Field(default=1.0, ge=0, le=10)
    tired_per_char: float = Field(default=0.02, ge=0, le=1)
    tired_at: float = Field(default=6.0, ge=0.5, le=100)
    tired_weight: float = Field(default=0.5, ge=0, le=1)
    tired_penalty: float = Field(default=0.2, ge=0, le=2)
    flood_ratio: float = Field(default=4, ge=1, le=50)  # 成员说的超过你说的这么多倍
    flood_floor: int = Field(default=20, ge=1, le=1000)  # 且至少这么多条：没点名的一轮只挑一位


class StashFollowRules(_Strict):
    """跟队形：群里 minutes 分钟内两个不同的人（含你）先后发了同一句短话或同一个表情包，
    其余成员按外向、幽默分有概率跟着发一模一样的（程序直接发、不调模型），最多 max_followers 位，
    这一串跟够了就停。"""

    enabled: bool = True
    minutes: float = Field(default=10, ge=1, le=120)
    max_chars: int = Field(default=8, ge=1, le=30)  # 只跟这么短的话（问句不跟）
    max_followers: int = Field(default=2, ge=0, le=8)
    base: float = Field(default=0.15, ge=0, le=1)
    traits: dict[str, float] = {"外向": 0.08, "幽默": 0.08}
    max_chance: float = Field(default=0.6, ge=0, le=1)


class StashRules(_Strict):
    """表情包收藏与跟队形（2026-10-31 拟人插件调研第 5 项）：
    你发的表情包（动图、小图，或标成表情的），看过的成员按性格和在不在乎你有一定概率存下来自己用；刚收的几天更爱用，用腻了或很久没用就丢掉。
    只用感官已经整理好的感知卡当说明，不另外调模型；只存本机，可以在成员设定里删、清空。"""

    enabled: bool = True
    small_px: int = Field(default=480, ge=32, le=4096)  # 宽高都不超过这么多像素的图算表情包
    base: float = Field(default=0.12, ge=0, le=1)
    traits: dict[str, float] = {"幽默": 0.06, "好奇": 0.03}
    care: float = Field(default=0.15, ge=0, le=1)  # × 她对你的在乎（0～1）
    max_chance: float = Field(default=0.4, ge=0, le=1)
    max_keep: int = Field(default=12, ge=1, le=64)  # 每位最多收几张，满了丢最旧的
    fresh_days: float = Field(default=3, ge=0, le=60)  # 刚收的这几天更想用
    tired_uses: int = Field(default=8, ge=1, le=100)  # 用了这么多次就腻了，丢掉
    forget_days: float = Field(default=21, ge=1, le=365)  # 这么多天没用就丢掉
    menu_max: int = Field(default=5, ge=1, le=20)  # 提示词里最多列几张
    label_chars: int = Field(default=16, ge=4, le=40)
    menu: str = "你从别人那收来的：{items}"
    fresh: str = "刚收的，正想用"
    unused: str = "从对方那偷来的，还没用过"
    follow: StashFollowRules = StashFollowRules()


class RhythmRules(_Strict):
    """作息与回话节奏（docs/BRAIN.md）：睡着时你发来的消息，多数醒了再回，有时被吵醒。"""

    enabled: bool = True
    wake_base: float = Field(default=0.12, ge=0, le=1)  # 睡着时被一条消息吵醒的基础概率
    wake_named: float = Field(default=0.2, ge=0, le=1)  # 群里点了她的名 / 私聊
    wake_close: float = Field(default=0.3, ge=0, le=1)  # × 她对你的在乎（0～1）
    wake_repeat: float = Field(default=0.15, ge=0, le=1)  # 十分钟内每多连发一条
    wake_max: float = Field(default=0.8, ge=0, le=1)
    grumpy_below: float = Field(default=-0.1, ge=-1, le=1)  # 心情低于这个，被吵醒时会抱怨
    keep_hours: int = Field(default=16, ge=1, le=72)  # 睡着没看到的消息，超过这么久就不回了
    woken: str = "你本来睡着了，被消息吵醒：迷迷糊糊的，回得短一点，可以说自己刚被吵醒"
    woken_grumpy: str = (
        "你本来睡着了，被消息吵醒，心情本来就不太好：可以嘟囔着抱怨一两句（比如「大半夜的……」"
        "「我都睡了」）再回；别真生气、别翻旧账，对方要是有急事就清醒过来认真回"
    )
    late: str = (
        "你刚醒，才看到对方 {when} 发来的消息：自然地接上，可以提一句刚看到 / 刚醒，别道歉个没完"
    )
    # 不在场就看不到（借 AI-Buddy 的思路）：群里她睡着时的消息，醒来只看到最后 missed_keep 条
    missed_keep: int = Field(default=3, ge=0, le=50)
    missed_note: str = (
        "（你刚才睡着了，前面还有 {n} 条没看到；想知道可以问一句刚才聊啥了，别装作都看过）"
    )


class ReachRules(_Strict):
    """主动找你（docs/BRAIN.md）：按心情、新鲜事、挂着的事、想念，偶尔先开口；有边界。"""

    enabled: bool = True
    per_day_chance: float = Field(default=0.6, ge=0, le=5)  # 想找的劲儿为 1 时，一天里先开口的概率
    per_day: int = Field(default=4, ge=0, le=10)  # 全体每天最多主动找几次
    gap_days: float = Field(default=1.0, ge=0, le=30)  # 同一位隔几天才可能再主动找
    after_user_hours: float = Field(default=2.0, ge=0, le=72)  # 你几小时内刚和她聊过就不找
    extrovert: float = Field(default=0.25, ge=0, le=1)  # 外向分每高 1 分，先开口的机会多这么多
    quiet_hours: tuple[int, int] = (23, 9)  # 你的深夜（主时区钟点，[开始, 结束)）不找
    care_min: float = Field(default=0.3, ge=0, le=1)  # 心情不好 / 想念才找你，要这么在乎你
    mood_low: float = Field(default=-0.35, ge=-1, le=0)
    mood_high: float = Field(default=0.55, ge=0, le=1)
    news_min: float = Field(default=0.7, ge=0, le=1)  # 新鲜事要这么重要才值得专门找你说
    longing_days: float = Field(default=2.0, ge=0.5, le=30)  # 多少天没聊开始想念
    notes: dict[str, str] = Field(
        default_factory=lambda: {
            "mood_low": "你现在心情不太好，想找对方说说话：你先开口，可以说也可以不说为什么",
            "mood_high": "你现在心情很好，想跟对方分享一下：你先开口",
            "news": "你想主动跟对方说说：{what}",
            "hanging": "你一直惦记着：{what}，想问问对方后来怎么样了",
            "longing": "好几天没和对方聊了，有点想对方：你先开口，随便找个由头",
            "hook": "他之前说过「{what}」，那天已经过了，你想问问他后来怎么样：随手问一句",
        }
    )
    tail: str = (
        "这是你主动找对方（对方没有先说话）：像随手发的微信，一两句就好；别只发「在吗」没下文，"
        "别怪对方不找你、不拿自己难过要挟"
    )


class ShowRule(_Strict):
    """一种心情直说出来的说法，和换成什么：她自己这几组气质里的颜文字，或者符号。"""

    words: list[str]  # 直说的说法（长的写前面）
    vibes: list[str] = []  # 优先用这几组气质（chat.engine.lingo.kaomoji）里她分到的颜文字
    marks: list[str] = []  # 或者用符号


class ShowRules(_Strict):
    """心情别直说（2026-10-31 用户定）：开心、激动、无语这些，不说「好开心」「无语」，
    用表情包、颜文字和符号带出来。意图卡里提醒一句；发出前程序再兜一层：直说了的换成她自己的
    颜文字或符号（只用她分到的颜文字，没有就用符号）。"""

    enabled: bool = True
    kaomoji: float = Field(default=0.5, ge=0, le=1)  # 换成颜文字（而不是符号）的概率
    feelings: dict[str, ShowRule] = {
        "开心": ShowRule(
            words=[
                "开心死了",
                "太开心了",
                "我好开心",
                "我很开心",
                "好开心",
                "超开心",
                "太高兴了",
                "好高兴",
            ],
            vibes=["元气", "温柔"],
            marks=["！！", "～～", "嘿嘿"],
        ),
        "激动": ShowRule(
            words=["激动死了", "太激动了", "我好激动", "我很激动", "好激动"],
            vibes=["元气", "搞怪"],
            marks=["！！！", "啊啊啊"],
        ),
        "无语": ShowRule(
            words=["无语死了", "我无语了", "我很无语", "真的无语", "好无语", "无语了"],
            vibes=["冷淡", "搞怪"],
            marks=["……", "？？？", "。。。"],
        ),
        "难过": ShowRule(
            words=["太难过了", "我好难过", "我很难过", "好难过"], vibes=["丧"], marks=["……"]
        ),
        "生气": ShowRule(
            words=["气死我了", "我很生气", "我好生气", "我生气了", "好生气"],
            vibes=["傲娇"],
            marks=["？？", "……"],
        ),
        "委屈": ShowRule(words=["我好委屈", "好委屈"], vibes=["丧"], marks=["……"]),
        "害羞": ShowRule(
            words=["我好害羞", "好害羞", "我害羞了"], vibes=["温柔"], marks=["//", "……"]
        ),
    }


class DriftRules(_Strict):
    """口癖和说话风格自己慢慢变（docs/BRAIN.md「口癖慢慢变」，2026-10-31 用户定）：用腻了的颜文字、
    短回复换新的（只在她自己的风格里挑、别人正在用的不挑），口头禅说腻了歇一阵，学来的用多了成了
    自己的，心情好坏用的颜文字不同。8 位同一套规则，差别只来自性格分和她自己说过的话。"""

    enabled: bool = True
    window_days: int = Field(default=14, ge=1, le=90)  # 数最近这么多天用了几次
    wear_uses: int = Field(default=5, ge=2, le=200)  # 用到这么多次算用得勤
    min_days: int = Field(default=14, ge=0, le=180)  # 至少用了这么久才会腻
    chance: float = Field(default=0.45, ge=0, le=1)  # 用得勤时每天腻了的基础概率
    traits: dict[str, float] = {"好奇": 0.06, "外向": 0.03, "较真": -0.05}  # × (分数 - 3)
    vibes_top: int = Field(default=3, ge=1, le=10)  # 新颜文字只在和她性格最像的几组里挑
    crowd: int = Field(default=2, ge=1, le=10)  # 已经有这么多位在用的不挑（不越聊越像）
    return_days: int = Field(default=60, ge=0, le=365)  # 换掉的这么久以内不挑回来
    rest_days: int = Field(default=21, ge=1, le=365)  # 口头禅说腻了歇几天
    # 歇掉一句后，档案上的口头禅至少还有这么多能用（不会一起歇光；成长 G3 半年快进发现的）
    keep_catchphrases: float = Field(default=0.5, ge=0, le=1)
    promote_uses: int = Field(default=6, ge=1, le=100)  # 学来的说法自己用够几次就成了自己的
    keep_swaps: int = Field(default=30, ge=5, le=500)
    mood_high: float = Field(default=0.35, ge=0, le=1)
    mood_low: float = Field(default=-0.35, ge=-1, le=0)
    up_vibes: list[str] = ["元气", "温柔", "搞怪"]
    down_vibes: list[str] = ["丧", "冷淡", "傲娇"]
    mood_line: str = "这会儿想用{face}"


class OpenerRules(_Strict):
    """开口前组织语言的过渡词（11-01 用户：人说话前会有「怎么说呢」「就是」「算了吧」这种片刻）。
    每人从词池里分到几个（按 id 稳定地分，8 位规则相同），较长的话按概率放在开头，
    最近几条用过就不放。"""

    pool: list[str] = []
    each: int = Field(default=3, ge=1, le=10)
    chance: float = Field(default=0.0, ge=0, le=1)
    min_chars: int = Field(default=14, ge=0, le=200)  # 这么长的话才放（短回复不放）


class CadenceRules(_Strict):
    """连着几条的分寸（docs/BRAIN.md「连着几条的分寸」，2026-11-01 用户批）：格式、句式、露底、
    跨条冷却。全程序；8 位同一套规则，只看她自己最近说过的话。"""

    enabled: bool = True
    plain: bool = True  # 去掉 Markdown、破折号、【标题】
    not_but: bool = True  # 「不是 X，而是 Y」→「不是 X，是 Y」
    window: int = Field(default=8, ge=2, le=40)  # 看她最近几条
    face_max: int = Field(default=2, ge=1, le=20)  # 同一个 emoji / 颜文字最近几条里用过几次就停
    particles: list[str] = ["呢"]  # 句尾语气词
    particle_share: float = Field(default=0.5, gt=0, le=1)  # 最近几条里这么收尾的比例到这个就停
    fillers: list[str] = []  # 口头填充词：最近几条说过就这条不说
    ask_streak: int = Field(default=2, ge=1, le=10)  # 最近连着几条都在反问
    ask_back: list[str] = []  # 「反问对方」的正则（只删末尾那一小句）
    leaks: list[str] = []  # 露底的正则：命中的小句删掉
    swap: dict[str, str] = {}  # 正则 → 换成的词（如「用户」→「你」）
    openers: OpenerRules = OpenerRules()


class LingoRules(_Strict):
    """说话的样子（docs/BRAIN.md「说话的样子」）：看懂语境、万能短回复、先反应再吐槽、
    书面腔过滤、标点和笑法、打错字、个人颜文字、表情包脾气、语气变调、外号、跟人学、打比方。
    全程序、不调模型；只用于生活群、私聊、成员互聊。8 位同一套规则，差别只来自档案和性格分。"""

    enabled: bool = True
    gist: list[GistRule] = []
    notice: str = "你看懂了：对方{label}（心里明白就行，别说出来，别复述，别解释）"
    shorts: dict[str, list[str]] = {}  # 万能短回复：类 → 词
    shorts_each: int = Field(default=4, ge=1, le=20)  # 每类每人分到几个
    quick_tired: float = Field(default=1.6, ge=1, le=5)  # 精力低时短回复概率 ×
    quick_close: float = Field(default=1.3, ge=1, le=5)  # 和对方很熟时 ×
    quick_quiet: float = Field(default=1.4, ge=1, le=5)  # 话少的人 ×
    react_boost: float = Field(default=1.8, ge=1, le=10)  # 对方分享东西时「接话 / 开玩笑」的意图 ×
    punct: dict[str, TraitPick] = {}
    punct_hints: dict[str, list[str]] = {}  # 档案里写了这些词就定这种标点习惯
    punct_chance: float = Field(default=0.6, ge=0, le=1)  # 发出前把句尾换成她的习惯的概率
    laughs: dict[str, TraitPick] = {}
    typo: TypoRules = TypoRules()
    kaomoji: dict[str, TraitPick] = {}
    kaomoji_each: int = Field(default=3, ge=1, le=10)
    # 一条消息里一个表情都没带时，发出前补一个她自己的颜文字的概率（2026-10-31 用户：表情用得少）
    kaomoji_sprinkle: float = Field(default=0.0, ge=0, le=1)
    # 补的颜文字只从这几种气质里挑（轻松的）；话里带这些词（想不起来、难过……）或以省略号收尾时不补
    sprinkle_vibes: list[str] = ["元气", "温柔", "搞怪"]
    sprinkle_skip: list[str] = []
    sticker_sets: dict[str, TraitPick] = {}  # 偏爱哪一套表情包（名字同 chat.actions.stickers）
    battle: float = Field(default=0.5, ge=0, le=1)  # 群里上一条是表情包：幽默的人回表情包斗图
    shift: dict[str, str] = {}  # 语气变调：tired / down / up / night / close / new / dislike
    nick: NickRules = NickRules()
    learn: LearnRules = LearnRules()
    metaphor: str = ""  # 「打比方时爱拿{things}打比方」
    formal: dict[str, str] = {}  # 书面词 → 口语（发出前直接换）
    explain: list[str] = []  # 解释句的正则（发出前能删就删）
    recap_chars: int = Field(default=8, ge=4, le=40)  # 和对方原话连着这么多字一样算复述
    show: ShowRules = ShowRules()
    drift: DriftRules = DriftRules()
    # 口吻指纹：草稿里别的成员的口头禅、颜文字、自称（她没学过的）发出前换回她自己的
    own_voice: bool = True
    # 特色限量：一条里颜文字 / emoji、招牌自称、口头禅最多几样（0 = 不管；10-31 用户反向找问题）
    max_traits: int = Field(default=2, ge=0, le=5)
    cadence: CadenceRules = CadenceRules()


class EngineRules(_Strict):
    """成员引擎（docs/MEMBER_ENGINE.md）：只用于聊天、朋友圈、生活；工作模式不读取。"""

    enabled: bool = True
    state: StateRules = StateRules()
    profile: ProfileRules = ProfileRules()
    card: CardRules = CardRules()
    context: ContextRules = ContextRules()
    scheduler: SchedulerRules = SchedulerRules()
    heartbeat: HeartbeatRules = HeartbeatRules()
    bus: BusRules = BusRules()
    eval: EvalRules = EvalRules()
    voice: VoiceRules = VoiceRules()
    about: AboutRules = AboutRules()
    growth: GrowthRules = GrowthRules()
    relation: RelationRules = RelationRules()
    brain: BrainRules = BrainRules()
    care: CareRules = CareRules()
    lingo: LingoRules = LingoRules()
    rhythm: RhythmRules = RhythmRules()
    pace: PaceRules = PaceRules()
    slop: SlopRules = SlopRules()
    phrasebook: PhrasebookRules = PhrasebookRules()
    stash: StashRules = StashRules()
    floor: FloorRules = FloorRules()
    phase: PhaseRules = PhaseRules()
    circle: CircleRules = CircleRules()
    firsts: FirstsRules = FirstsRules()
    hooks: HooksRules = HooksRules()
    hush: HushRules = HushRules()
    reach: ReachRules = ReachRules()
    feel: FeelRules = FeelRules()
