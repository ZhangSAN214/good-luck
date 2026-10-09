"""各步骤的输出格式与解析：互评 JSON、修订文本、汇总 JSON，以及空泛评审检测。"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from roundtable.core.config.schema import ReviewQuality
from roundtable.core.jsonout import extract_json_object

Verdict = Literal["correct", "partially_correct", "incorrect", "unclear"]
Severity = Literal["major", "minor"]
Confidence = Literal["high", "medium", "low"]


class _Loose(BaseModel):
    """模型输出的宽松解析：多余字段忽略；null 当作没写（用默认值）；数字当文字；
    文字字段写成了列表时用"；"连起来（便宜模型常见的小毛病，不值得整份重来）。"""

    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    @model_validator(mode="before")
    @classmethod
    def _tolerate(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = {}
        for key, value in data.items():
            if value is None:
                continue
            field = cls.model_fields.get(key)
            if field is not None and field.annotation is str and isinstance(value, list | tuple):
                value = "；".join(str(x) for x in value)
            out[key] = value
        return out


def _choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


# --- 互评 ---------------------------------------------------------------------


class ReviewIssue(_Loose):
    location: str = ""
    problem: str = ""
    suggestion: str = ""
    severity: Severity = "minor"

    @field_validator("severity", mode="before")
    @classmethod
    def _severity(cls, v: Any) -> str:
        return _choice(v, ("major", "minor"), "minor")


class PeerReview(_Loose):
    target: str
    verdict: Verdict = "unclear"
    issues: list[ReviewIssue] = Field(default_factory=list)
    checked: str = ""
    strengths: str = ""

    @field_validator("verdict", mode="before")
    @classmethod
    def _verdict(cls, v: Any) -> str:
        text = str(v or "").strip().lower().replace(" ", "_").replace("-", "_")
        if text in ("partial", "partly_correct", "mostly_correct"):
            text = "partially_correct"
        return _choice(text, ("correct", "partially_correct", "incorrect", "unclear"), "unclear")

    @field_validator("issues", mode="before")
    @classmethod
    def _issues(cls, v: Any) -> Any:
        """问题可能写成单个对象或字符串列表：字符串当作只写了问题（缺位置 / 建议即不完整）。"""
        if isinstance(v, dict | str):
            v = [v]
        if isinstance(v, list):
            return [{"problem": x} if isinstance(x, str) else x for x in v]
        return v


class ReviewOutput(_Loose):
    reviews: list[PeerReview]

    @model_validator(mode="before")
    @classmethod
    def _shapes(cls, data: Any) -> Any:
        """便宜模型常把评审写成：整个数组、单条评审对象、或 reviews 是单个对象。"""
        if isinstance(data, dict):
            if "reviews" not in data and "_list" in data:
                data = {"reviews": data["_list"]}
            elif "reviews" not in data and "target" in data:
                data = {"reviews": [data]}
            elif isinstance(data.get("reviews"), dict):
                data = {**data, "reviews": [data["reviews"]]}
        return data


@dataclass(frozen=True)
class CheckedReview:
    """一条经过代码检查的评审。invalid_reasons 非空即为无效，不转给作者。"""

    reviewer: str  # 代号
    target: str  # 代号
    verdict: str
    issues: tuple[ReviewIssue, ...]
    checked: str
    strengths: str
    invalid_reasons: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.invalid_reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewer": self.reviewer,
            "target": self.target,
            "verdict": self.verdict,
            "issues": [i.model_dump() for i in self.issues],
            "checked": self.checked,
            "strengths": self.strengths,
            "valid": self.valid,
            "invalid_reasons": list(self.invalid_reasons),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CheckedReview:
        return cls(
            reviewer=d["reviewer"],
            target=d["target"],
            verdict=d["verdict"],
            issues=tuple(ReviewIssue.model_validate(i) for i in d["issues"]),
            checked=d["checked"],
            strengths=d["strengths"],
            invalid_reasons=tuple(d["invalid_reasons"]),
        )


_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)


def _is_vague(text: str, phrases: Iterable[str]) -> bool:
    """文本去掉标点和空白后，只由空泛短语拼成（如"挺好！没问题。"）。"""
    rest = _PUNCT.sub("", text.lower())
    if not rest:
        return True
    for phrase in sorted({_PUNCT.sub("", p.lower()) for p in phrases}, key=len, reverse=True):
        if phrase:
            rest = rest.replace(phrase, "")
    return not rest


def check_review(
    review: PeerReview, reviewer: str, target: str, quality: ReviewQuality
) -> CheckedReview:
    reasons: list[str] = []
    # 只保留写全了"位置 + 问题 + 修改建议"的问题
    complete = [
        i
        for i in review.issues
        if i.location.strip() and i.problem.strip() and i.suggestion.strip()
    ]
    if review.issues and not complete:
        reasons.append("列出的问题都缺少位置、问题或修改建议")
    elif not review.issues:
        checked = review.checked.strip()
        if len(checked) < quality.min_checked_chars or _is_vague(checked, quality.vague_phrases):
            reasons.append("没有指出具体问题，也没有具体说明检查了什么")
        if review.verdict in ("incorrect", "partially_correct"):
            reasons.append("判定答案有误，却没有指出问题")
    return CheckedReview(
        reviewer=reviewer,
        target=target,
        verdict=review.verdict,
        issues=tuple(complete),
        checked=review.checked.strip(),
        strengths=review.strengths.strip(),
        invalid_reasons=tuple(reasons),
    )


@dataclass(frozen=True)
class ReviewParse:
    reviews: tuple[CheckedReview, ...]
    missing: tuple[str, ...]  # 应评审但没有评审的代号
    ignored: tuple[str, ...]  # 被忽略的目标（自评、未知代号、重复）


def parse_reviews(
    text: str,
    *,
    reviewer: str,
    expected: Iterable[str],
    to_code: Callable[[str], str | None],
    quality: ReviewQuality,
) -> ReviewParse:
    """解析评审 JSON。格式错误时抛出 ValueError（JSONOutputError / ValidationError）。"""
    output = ReviewOutput.model_validate(extract_json_object(text))
    expected_set = set(expected)
    seen: dict[str, CheckedReview] = {}
    ignored: list[str] = []
    for review in output.reviews:
        code = to_code(review.target)
        if code is None or code == reviewer or code not in expected_set or code in seen:
            ignored.append(review.target)  # 自评永远丢弃
            continue
        seen[code] = check_review(review, reviewer, code, quality)
    ordered = tuple(seen[c] for c in expected if c in seen)
    missing = tuple(c for c in expected if c not in seen)
    return ReviewParse(ordered, missing, tuple(ignored))


# --- 修订 ---------------------------------------------------------------------

ANSWER_HEADING = "## 修订后的答案"
RESPONSE_HEADING = "## 对审阅意见的回应"


@dataclass(frozen=True)
class ReviewDecision:
    """作者对一条评审意见的回应：reviewer 为代号，issue 为问题序号（整条意见时为空）。"""

    reviewer: str
    issue: int | None
    decision: str  # accepted / partial / rejected

    def to_dict(self) -> dict[str, Any]:
        return {"reviewer": self.reviewer, "issue": self.issue, "decision": self.decision}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReviewDecision:
        return cls(d["reviewer"], d.get("issue"), d["decision"])

    @property
    def accepted(self) -> bool:
        return self.decision in ("accepted", "partial")


@dataclass(frozen=True)
class Revision:
    answer: str
    responses: str
    skipped: bool = False  # 没有有效评审，沿用原答案、未调用模型
    degraded: bool = False  # 输出格式不符，整段作为答案
    decisions: tuple[ReviewDecision, ...] = ()  # 从回应中解析出的逐条采纳情况

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "responses": self.responses,
            "skipped": self.skipped,
            "degraded": self.degraded,
            "decisions": [d.to_dict() for d in self.decisions],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Revision:
        decisions = tuple(ReviewDecision.from_dict(x) for x in d.get("decisions") or ())
        return cls(d["answer"], d["responses"], d["skipped"], d["degraded"], decisions)


def parse_revision(text: str) -> Revision | None:
    """按两个固定标题切分；找不到"修订后的答案"标题时返回 None。"""
    start = text.find(ANSWER_HEADING)
    if start == -1:
        return None
    body = text[start + len(ANSWER_HEADING) :]
    split = body.find(RESPONSE_HEADING)
    answer = (body[:split] if split != -1 else body).strip()
    responses = body[split + len(RESPONSE_HEADING) :].strip() if split != -1 else ""
    return Revision(answer, responses) if answer else None


_DECISION_WORDS = {"部分采纳": "partial", "不采纳": "rejected", "采纳": "accepted"}
_DECISION_LINE = re.compile(
    r"^[\s\-*•·]*(?P<who>[^:：]+?)\s*(?:[·・\-—]\s*问题\s*(?P<n>\d+)\s*)?[:：]\s*"
    r"(?P<d>部分采纳|不采纳|采纳)"
)


def parse_decisions(
    responses: str, to_code: Callable[[str], str | None]
) -> tuple[ReviewDecision, ...]:
    """从"对审阅意见的回应"中解析逐条的采纳情况；格式不符的行忽略（不计入贡献）。

    每行形如"- 鲸鱼娘·全力 · 问题 1：采纳 —— 理由"或"- 愚者：不采纳 —— 理由"（称呼可含"·"）。
    同一评审者的同一问题只取第一次出现的回应。
    """
    seen: dict[tuple[str, int | None], ReviewDecision] = {}
    for line in responses.splitlines():
        m = _DECISION_LINE.match(line.replace("**", ""))
        if not m:
            continue
        code = to_code(m["who"])
        if code is None:
            continue
        key = (code, int(m["n"]) if m["n"] else None)
        seen.setdefault(key, ReviewDecision(code, key[1], _DECISION_WORDS[m["d"]]))
    return tuple(seen.values())


# --- 汇总 ---------------------------------------------------------------------


class Position(_Loose):
    members: list[str] = Field(default_factory=list)
    view: str = ""


class Disagreement(_Loose):
    point: str
    positions: list[Position] = Field(default_factory=list)
    assessment: str = ""
    resolved: bool = False


class Adoption(_Loose):
    """最终答案采用的一个要点，以及它来自哪些组员（synthesize/v3 起）。"""

    point: str = ""
    members: list[str] = Field(default_factory=list)


class SynthesisOutput(_Loose):
    consensus: list[str] = Field(default_factory=list)
    disagreements: list[Disagreement] = Field(default_factory=list)
    final_answer: str = Field(min_length=1)
    adopted_from: list[Adoption] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    confidence: Confidence = "medium"

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, v: Any) -> str:
        return _choice(v, ("high", "medium", "low"), "medium")


@dataclass(frozen=True)
class Synthesis:
    output: SynthesisOutput
    degraded: bool = False  # 统筹输出无法解析，使用兜底结果
    error: str | None = None

    @property
    def usable_final_answer(self) -> str | None:
        """可作为后续步骤（如媒体生成的提示词）输入的最终答案；降级或占位时为 None。"""
        if self.degraded or is_placeholder_text(self.output.final_answer):
            return None
        return self.output.final_answer.strip()

    @property
    def unresolved_disagreements(self) -> int:
        return sum(1 for d in self.output.disagreements if not d.resolved)

    def to_dict(self) -> dict[str, Any]:
        return {**self.output.model_dump(), "degraded": self.degraded, "error": self.error}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Synthesis:
        return cls(SynthesisOutput.model_validate(d), d.get("degraded", False), d.get("error"))


def parse_synthesis(text: str, to_code: Callable[[str], str | None]) -> Synthesis:
    """解析汇总 JSON，并把各方观点中的成员统一为代号（未知的丢弃）。格式错误抛 ValueError。"""
    output = SynthesisOutput.model_validate(extract_json_object(text))
    for d in output.disagreements:
        for p in d.positions:
            p.members = [c for c in (to_code(m) for m in p.members) if c]
    for a in output.adopted_from:
        a.members = list(dict.fromkeys(c for c in (to_code(m) for m in a.members) if c))
    output.adopted_from = [a for a in output.adopted_from if a.point.strip() and a.members]
    return Synthesis(output)


PLACEHOLDER_FINAL = "（汇总失败，请直接参考各组员修订后的答案）"


def is_placeholder_text(text: str | None) -> bool:
    """降级占位文字（汇总失败的兜底等）：不是真正的内容，不能当作生成提示词或交付物。"""
    t = (text or "").strip()
    return (
        not t or t == PLACEHOLDER_FINAL or (t.startswith("（") and t.endswith("）") and "失败" in t)
    )


def fallback_synthesis(error: str) -> Synthesis:
    return Synthesis(
        SynthesisOutput(
            final_answer=PLACEHOLDER_FINAL,
            open_questions=["统筹未能给出可用的汇总"],
            confidence="low",
        ),
        degraded=True,
        error=error,
    )


@dataclass(frozen=True)
class EffortRecord:
    """一份产出的实质内容检查结果：redone（重做后合格）或 lazy（仍不合格，标记敷衍）。"""

    step: str
    code: str
    status: str  # redone / lazy
    reasons: tuple[str, ...]  # 第一次不合格的原因
    final_reasons: tuple[str, ...] = ()  # 重做后仍不合格的原因（lazy 时）
    redone: bool = True  # 是否打回重做过（配置关闭重做时为 False）
    item: str | None = None  # 协同模式：成果编号（同一成员可能负责多块）

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.step, self.code, self.item or "")

    @property
    def lazy(self) -> bool:
        return self.status == "lazy"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "final_reasons": list(self.final_reasons),
            "redone": self.redone,
            "item": self.item,
        }

    @classmethod
    def from_dict(cls, step: str, code: str, d: dict[str, Any]) -> EffortRecord:
        return cls(
            step,
            code,
            d["status"],
            tuple(d["reasons"]),
            tuple(d.get("final_reasons") or ()),
            d.get("redone", True),
            d.get("item"),
        )


@dataclass(frozen=True)
class StyleSpec:
    """风格规范（style 步骤的产出）：全员共用的规范和可逐条检查的风格清单。"""

    spec: str
    checklist: tuple[str, ...]
    extractors: tuple[str, ...] = ()  # 各自看过原图的成员代号
    text_only: bool = False  # 来自图片的文字版（没有 vision 成员）：可能不准
    warning: str | None = None
    degraded: bool = False  # 合并失败，规范是各份描述的拼接、没有清单

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec,
            "checklist": list(self.checklist),
            "extractors": list(self.extractors),
            "text_only": self.text_only,
            "warning": self.warning,
            "degraded": self.degraded,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StyleSpec:
        return cls(
            d["spec"],
            tuple(d.get("checklist") or ()),
            tuple(d.get("extractors") or ()),
            bool(d.get("text_only", False)),
            d.get("warning"),
            bool(d.get("degraded", False)),
        )


@dataclass
class TableState:
    """一张桌子在各步骤之间传递的数据（可从数据库恢复）。键均为代号。"""

    answers: dict[str, str] = field(default_factory=dict)
    reviews: dict[str, tuple[CheckedReview, ...]] = field(default_factory=dict)
    revisions: dict[str, Revision] = field(default_factory=dict)
    synthesis: Synthesis | None = None
    dropped: dict[str, str] = field(default_factory=dict)  # 代号 → 退出原因
    ready_for_reveal: bool = False
    # (步骤, 代号, 成果编号或空) → 实质内容检查结果（只记录重做过或被标记敷衍的）
    effort: dict[tuple[str, str, str], EffortRecord] = field(default_factory=dict)
    # 风格规范（有风格参考图又要画图时由 style 步骤产生）
    style: StyleSpec | None = None
    # 协同模式的数据
    collab: Any = field(default_factory=lambda: _new_collab())

    def flagged(self, code: str, item: str | None = None) -> bool:
        """该组员（的某份成果）是否有产出被标记为敷衍。"""
        return any(
            r.lazy and r.code == code and (item is None or r.item in (None, item))
            for r in self.effort.values()
        )


def _new_collab():
    from .collab_schemas import CollabState

    return CollabState()
