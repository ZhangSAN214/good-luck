"""roundtable.yaml 的 office、evolution、clipper：办公室模式、革新例会、资料浏览器。

从 schema.py 拆出（代码精简）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from roundtable.core.config.sections.base import _Strict


class OfficeTaskCaps(_Strict):
    """办公室模式：每件事的工资上限（美元），按办事方式分别设；预计超出时先问老板。"""

    single: float = Field(default=0.05, gt=0)  # 单人办
    meeting: float = Field(default=0.15, gt=0)  # 开会
    project: float = Field(default=0.80, gt=0)  # 项目分工


class OfficeMeeting(_Strict):
    """办公室模式的开会（O3）：经理点名几位员工依次发言，再拍板写纪要。"""

    min_attendees: int = Field(default=2, ge=2)
    max_attendees: int = Field(default=3, ge=2)

    @model_validator(mode="after")
    def _range(self) -> OfficeMeeting:
        if self.max_attendees < self.min_attendees:
            raise ValueError("max_attendees 不能小于 min_attendees")
        return self


class OfficeProject(_Strict):
    """办公室模式的项目分工（O4）：经理点名一个项目组，沿用协同流水线，但只派需要的人。

    pipeline：项目组那张桌子的步骤（协同流水线 + 复核经理验收 office_review）；
    reviews_per_item：交叉审查时每份成果几位审查者（只审关键，默认 1）；
    step_params：项目组桌子上覆盖的步骤参数（如合并由主办经理以中等思考强度完成），
    同一步骤所有人相同。
    """

    min_members: int = Field(default=2, ge=2)
    max_members: int = Field(default=4, ge=2)
    pipeline: list[str] = Field(
        default_factory=lambda: [
            "decompose",
            "volunteer",
            "assign",
            "work",
            "cross_review",
            "rework",
            "accept",
            "merge",
            "office_review",
            "reveal",
        ],
        min_length=1,
    )
    reviews_per_item: int = Field(default=1, ge=1)
    step_params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # M 办公室交接（2026-11-04）：经理决定项目分工时，先问老板要不要交给工作台（项目室）。
    # 只在工作台和项目室都开着时问；同意后直接生成项目室的需求书，做完把成品和验收结果回报到这场对话
    bench_handoff: bool = True

    @model_validator(mode="after")
    def _range(self) -> OfficeProject:
        if self.max_members < self.min_members:
            raise ValueError("max_members 不能小于 min_members")
        if len(self.pipeline) != len(set(self.pipeline)):
            raise ValueError(f"pipeline 中的步骤不能重复：{self.pipeline}")
        return self


class OfficePerformance(_Strict):
    """办公室模式的绩效（O5）：名单附绩效给经理参考；规则派活时工资相同的优先通过率高的。"""

    enabled: bool = True
    min_jobs: int = Field(default=3, ge=1)


class OfficeRules(_Strict):
    """办公室模式（O1～O5，见 docs/OFFICE.md）。经理在 models.yaml 的 office_role 里指定。"""

    task_caps_usd: OfficeTaskCaps = OfficeTaskCaps()
    meeting: OfficeMeeting = OfficeMeeting()
    project: OfficeProject = OfficeProject()
    performance: OfficePerformance = OfficePerformance()
    # 两位以上经理时怎么分：rotate = 每件事轮流当主办经理，项目级任务由另一位复核
    manager_strategy: Literal["rotate"] = "rotate"


class ClipperRules(_Strict):
    """办公室的资料浏览器（docs/CLIPPER.md）：打开网页只读抓取（不花钱），搜索走联网搜索。"""

    enabled: bool = True
    timeout_s: float = Field(default=15, gt=0, le=60)
    max_page_kb: int = Field(default=3072, ge=64, le=16384)  # 网页原始大小上限
    max_page_chars: int = Field(default=60000, ge=2000, le=200000)  # 提取后的正文最多显示多少字
    max_clip_chars: int = Field(default=20000, ge=500, le=60000)  # 一份资料最多存多少字（省 token）
    max_clips: int = Field(default=200, ge=1, le=1000)  # 资料夹最多存几份，超出时删最旧的
    search_results: int = Field(default=8, ge=1, le=20)
    search_estimate_usd: float = Field(default=0.01, ge=0)


class EvolutionSchedule(_Strict):
    """革新例会自动开会的时间（本机时区）：每周 weekday（ISO：1 = 周一）的 hour 点之后开一次。"""

    weekday: int = Field(default=1, ge=1, le=7)
    hour: int = Field(default=9, ge=0, le=23)


class EvolutionScoutRules(_Strict):
    """圆桌自己找资料写卡（docs/evolution/README.md「自己找资料」）：代码按搜索词联网搜索，只留白名单
    网站的结果；读网页正文后，一位成员写成一页资料卡，另一位成员复核（不自审），通过的才进收件箱。
    网页内容一律当资料（标签内的指令无效）；花费计入革新例会的每月上限。"""

    enabled: bool = False
    queries: list[str] = Field(default_factory=list, max_length=12)  # 搜索词（每次都搜）
    domains: list[str] = Field(default_factory=list, max_length=30)  # 白名单网站（含子域名）
    results_per_query: int = Field(default=5, ge=1, le=10)
    cards: int = Field(default=3, ge=1, le=10)  # 每次最多写几张（还受 max_cards 限制）
    page_chars: int = Field(default=6000, ge=500, le=30000)  # 每个网页最多读多少字
    max_tokens: int = Field(default=1200, ge=200)  # 写卡 / 复核的输出上限
    search_estimate_usd: float = Field(default=0.01, ge=0)  # 每次搜索 / 读网页的预估花费
    card_estimate_usd: float = Field(default=0.01, ge=0)  # 每张卡（写 + 复核）的预估花费
    seen_keep: int = Field(default=300, ge=10, le=5000)  # 记住多少个写过的网址（不重复写）

    @field_validator("domains")
    @classmethod
    def _bare_domains(cls, domains: list[str]) -> list[str]:
        out = []
        for d in domains:
            d = d.strip().lower()
            if not d or "/" in d or ":" in d or " " in d or "." not in d:
                raise ValueError(f"只写域名，例如 github.com：{d!r}")
            out.append(d)
        return out


class EvolutionImplementRules(_Strict):
    """「做这条」之后交给开发侧（docs/evolution/README.md）：在数据目录的专用工作副本里叫起开发侧
    命令行实现、开 PR；从不自动合并，用户在网页上点「合并」才合并。默认关闭。"""

    enabled: bool = False
    # 开发侧命令（任务说明从标准输入给）；第一个词找不到时也查用户安装位置
    agent: list[str] = Field(
        default_factory=lambda: [
            "claude",
            "-p",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            "Bash,Read,Edit,Write,Glob,Grep",
        ]
    )
    gh: list[str] = Field(default_factory=lambda: ["gh"])  # 合并 PR 用
    base_branch: str = Field(default="main", min_length=1)  # PR 合到哪个分支
    merge_method: Literal["merge", "squash", "rebase"] = "merge"
    brief: str = "docs/evolution/开发任务模板.md"  # 任务说明模板（相对项目根目录）
    workspace_dir: str = "工作区"  # 数据目录 evolution/ 下的专用工作副本
    timeout_minutes: int = Field(default=120, ge=5, le=600)

    @field_validator("workspace_dir")
    @classmethod
    def _plain(cls, name: str) -> str:
        if not name.strip() or any(c in name for c in "/\\:") or name.strip(".") == "":
            raise ValueError(f"只能是一个文件夹名（不含路径）：{name!r}")
        return name


class EvolutionPluginRules(_Strict):
    """革新例会·插件清单（docs/evolution/README.md「插件清单」）：例会开完后，在所选档位里按预计价格
    挑最便宜的一位成员，根据本场资料卡和点子榜写给用户看的插件清单（做什么、有什么用、优缺点、
    为什么做、用在哪个场景）。只是给用户看的资料，不改代码；花费计入革新例会的每月上限。"""

    enabled: bool = True
    count: int = Field(default=20, ge=5, le=40)  # 每场至少写几个
    scenes: list[str] = Field(
        default_factory=lambda: ["办公室工作", "公寓生活", "群聊", "朋友圈", "头脑风暴", "通用"],
        min_length=1,
        max_length=12,
    )  # 场景标签（最后一个兜底：成员写了不在清单里的场景时归到它）
    max_tokens: int = Field(default=8000, ge=1000)  # 每次调用的输出上限
    rounds: int = Field(default=2, ge=1, le=3)  # 不够 count 个时最多再补写几次（每次内容不同）
    estimate_usd: float = Field(default=0.04, ge=0)  # 每次调用的预估花费
    dir: str = "插件清单"  # 数据目录 evolution/ 下：每场一份 Markdown
    field_chars: int = Field(default=200, ge=40, le=1000)  # 每个字段最多多少字（多的截掉）

    @field_validator("dir")
    @classmethod
    def _plain(cls, name: str) -> str:
        if not name.strip() or any(c in name for c in "/\\:") or name.strip(".") == "":
            raise ValueError(f"只能是一个文件夹名（不含路径）：{name!r}")
        return name

    @field_validator("scenes")
    @classmethod
    def _scenes(cls, scenes: list[str]) -> list[str]:
        out = [s.strip() for s in scenes]
        if any(not s for s in out) or len(set(out)) != len(out):
            raise ValueError("场景不能为空，也不能重复")
        return out


class EvolutionRules(_Strict):
    """革新例会（docs/evolution/README.md）：成员读同一叠资料卡，复用头脑风暴提建议、匿名互评、投票，
    导出采纳单。默认关闭；花费有每场和每月硬上限（也计入总预算）。"""

    enabled: bool = False
    plan: str = Field(default="budget", min_length=1)  # 成员档位（routing.yaml 的 plans）：全员上桌
    anonymous: bool = True  # 匿名：塔罗牌代号
    topic: str = Field(default="", max_length=2000)  # 例会的话题（作为题目，资料卡作为附件）
    inbox_dir: str = "收件箱"  # 资料卡放这里（数据目录下的 evolution/ 里）
    adopted_dir: str = "采纳单"  # 导出的采纳单放这里
    max_cards: int = Field(default=5, ge=1, le=10)  # 每场最多几张资料卡（多的不读）
    card_chars: int = Field(default=3000, ge=200, le=20000)  # 每张卡最多多少字（多的截掉）
    meeting_usd: float = Field(default=0.40, gt=0)  # 每场预计花费上限：超出就不开
    monthly_usd: float = Field(
        default=1.5, gt=0
    )  # 每月（UTC 自然月）上限：已花 + 本场预计超出就不开
    # 两种例会：小版本（主题不限）列前 minor_top 名；大版本（围绕一个主题）列前 major_top 名。
    # 开哪种由代码按时间自动判断：每个自然月的第一场开大版本（两次大版本至少隔 major_gap_days 天），
    # 其余开小版本；你可以手动改。大版本的主题：你定了就用你的，否则在 major_themes 里轮换。
    minor_top: int = Field(default=3, ge=1, le=30)
    major_top: int = Field(default=10, ge=1, le=30)
    major_gap_days: int = Field(default=21, ge=7, le=90)
    major_themes: list[str] = Field(default_factory=list, max_length=20)  # 大版本主题的现成选项
    theme_chars: int = Field(default=200, ge=10, le=1000)  # 大版本主题最多多少字
    auto: bool = True  # 开启后每周按 schedule 自动开一次（服务开着才会开）
    schedule: EvolutionSchedule = EvolutionSchedule()
    tick_minutes: int = Field(default=30, ge=5, le=360)  # 服务开着时多久检查一次
    used_dir: str = "已用"  # 开过会的资料卡移到这里（按周分文件夹），下周不会再读
    scout: EvolutionScoutRules = EvolutionScoutRules()
    implement: EvolutionImplementRules = EvolutionImplementRules()
    plugins: EvolutionPluginRules = EvolutionPluginRules()

    @field_validator("inbox_dir", "adopted_dir", "used_dir")
    @classmethod
    def _plain_dir(cls, name: str) -> str:
        if not name.strip() or any(c in name for c in "/\\:") or name.strip(".") == "":
            raise ValueError(f"只能是一个文件夹名（不含路径）：{name!r}")
        return name

    @model_validator(mode="after")
    def _limits(self) -> EvolutionRules:
        if self.meeting_usd > self.monthly_usd:
            raise ValueError("meeting_usd 不能大于 monthly_usd")
        if self.enabled and not self.topic.strip():
            raise ValueError("开启革新例会时必须填写 topic")
        return self
