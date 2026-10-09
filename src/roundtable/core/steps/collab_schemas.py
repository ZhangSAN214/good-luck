"""协同模式的数据结构与解析：子任务拆分、自荐、分配（代码校验与补齐）、合并。"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import Field, field_validator

from roundtable.core.jsonout import extract_json_object

from .schemas import Confidence, _choice, _Loose

# --- 子任务 -------------------------------------------------------------------


class SubtaskModel(_Loose):
    id: str
    title: str = Field(min_length=1)
    requirements: str = ""
    acceptance: str = ""
    tags: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    media: str | None = None  # image / speech / video：这一块的成果是生成的图片 / 语音 / 视频


class DecompositionOutput(_Loose):
    subtasks: list[SubtaskModel] = Field(min_length=1)
    notes: str = ""


@dataclass(frozen=True)
class Subtask:
    id: str
    title: str
    requirements: str = ""
    acceptance: str = ""
    tags: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    media: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "requirements": self.requirements,
            "acceptance": self.acceptance,
            "tags": list(self.tags),
            "depends_on": list(self.depends_on),
            "media": self.media,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Subtask:
        return cls(
            d["id"],
            d["title"],
            d.get("requirements", ""),
            d.get("acceptance", ""),
            tuple(d.get("tags") or ()),
            tuple(d.get("depends_on") or ()),
            d.get("media") or None,
        )


def _norm_id(text: str) -> str:
    return str(text).strip().upper()


def layers(subtasks: Sequence[Subtask]) -> list[list[str]]:
    """按依赖分层：每层内的子任务可以并行，后一层依赖前面的层。有循环依赖时抛 ValueError。"""
    remaining = {s.id: set(s.depends_on) for s in subtasks}
    order = [s.id for s in subtasks]
    done: set[str] = set()
    result: list[list[str]] = []
    while remaining:
        ready = [i for i in order if i in remaining and remaining[i] <= done]
        if not ready:
            raise ValueError(f"子任务存在循环依赖：{sorted(remaining)}")
        result.append(ready)
        done.update(ready)
        for i in ready:
            del remaining[i]
    return result


def parse_decomposition(
    text: str,
    *,
    max_subtasks: int,
    vocabulary: Iterable[str],
    media_kinds: Iterable[str] = (),
) -> tuple[Subtask, ...]:
    """解析并校验拆分结果：数量、id 唯一、依赖存在且无环；未知标签丢弃。不合格抛 ValueError。"""
    output = DecompositionOutput.model_validate(extract_json_object(text))
    if len(output.subtasks) > max_subtasks:
        raise ValueError(f"子任务数量 {len(output.subtasks)} 超过上限 {max_subtasks}")
    vocab = set(vocabulary)
    kinds = set(media_kinds)  # 当前可以生成的媒体种类；其他写法一律当作普通子任务
    ids = [_norm_id(s.id) for s in output.subtasks]
    if len(set(ids)) != len(ids) or not all(ids):
        raise ValueError("子任务 id 为空或重复")
    known = set(ids)
    subtasks = []
    for sid, s in zip(ids, output.subtasks, strict=True):
        deps = tuple(dict.fromkeys(_norm_id(d) for d in s.depends_on))
        if sid in deps or not set(deps) <= known:
            raise ValueError(f"子任务 {sid} 的依赖无效：{list(deps)}")
        subtasks.append(
            Subtask(
                sid,
                s.title.strip(),
                s.requirements.strip(),
                s.acceptance.strip(),
                tuple(t for t in s.tags if t in vocab),
                deps,
                (s.media or "").strip().lower()
                if (s.media or "").strip().lower() in kinds
                else None,
            )
        )
    layers(subtasks)  # 检查循环依赖
    return add_implied_dependencies(tuple(subtasks))


_ID_MENTION = re.compile(r"(?<![A-Za-z0-9])T\d+(?![0-9])", re.IGNORECASE)


def add_implied_dependencies(subtasks: tuple[Subtask, ...]) -> tuple[Subtask, ...]:
    """子任务的标题、要求或验收标准里提到了别的子任务（如"核对 T3"），却没写依赖时补上依赖，
    否则它会与被提到的子任务同时开始、拿不到其结果。补上会形成循环的依赖不加。"""
    known = {s.id for s in subtasks}
    result = list(subtasks)
    for i, s in enumerate(result):
        text = f"{s.title}\n{s.requirements}\n{s.acceptance}"
        mentioned = [m.upper() for m in _ID_MENTION.findall(text)]
        for dep in dict.fromkeys(mentioned):
            if dep == s.id or dep not in known or dep in result[i].depends_on:
                continue
            candidate = replace(s, depends_on=(*result[i].depends_on, dep))
            trial = [*result[:i], candidate, *result[i + 1 :]]
            try:
                layers(trial)
            except ValueError:
                continue  # 会形成循环：不加
            result = trial
    return tuple(result)


def fallback_decomposition() -> tuple[Subtask, ...]:
    """统筹拆分失败时：整道题作为一个子任务，全员各自独立完成。"""
    return (
        Subtask(
            "T1",
            "完成整道题",
            "独立完成题目的全部要求，写出关键推理与结论",
            "覆盖题目全部要求，推理正确，结论明确",
        ),
    )


# --- 自荐 ---------------------------------------------------------------------

Stance = Literal["want", "can", "unfit"]
STANCE_SCORE = {"want": 2, "can": 1, "unfit": -1}


class Preference(_Loose):
    subtask: str
    stance: Stance = "can"
    reason: str = ""

    @field_validator("stance", mode="before")
    @classmethod
    def _stance(cls, v: Any) -> str:
        return _choice(v, ("want", "can", "unfit"), "can")


class VolunteerOutput(_Loose):
    strengths: str = ""
    preferences: list[Preference] = Field(default_factory=list)


@dataclass(frozen=True)
class Volunteer:
    strengths: str
    preferences: dict[str, tuple[str, str]]  # 子任务 id → (stance, reason)
    degraded: bool = False  # 输出无法解析：全部按"可以做"处理

    def stance(self, subtask: str) -> str:
        return self.preferences.get(subtask, ("can", ""))[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strengths": self.strengths,
            "preferences": [
                {"subtask": k, "stance": v[0], "reason": v[1]} for k, v in self.preferences.items()
            ],
            "degraded": self.degraded,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Volunteer:
        prefs = {p["subtask"]: (p["stance"], p["reason"]) for p in d["preferences"]}
        return cls(d["strengths"], prefs, d.get("degraded", False))


def parse_volunteer(text: str, subtask_ids: Sequence[str]) -> Volunteer:
    output = VolunteerOutput.model_validate(extract_json_object(text))
    known = set(subtask_ids)
    prefs: dict[str, tuple[str, str]] = {}
    for p in output.preferences:
        sid = _norm_id(p.subtask)
        if sid in known and sid not in prefs:
            prefs[sid] = (p.stance, p.reason.strip())
    for sid in subtask_ids:  # 没表态的按"可以做"
        prefs.setdefault(sid, ("can", ""))
    return Volunteer(output.strengths.strip(), {s: prefs[s] for s in subtask_ids})


def default_volunteer(subtask_ids: Sequence[str]) -> Volunteer:
    return Volunteer("", {s: ("can", "") for s in subtask_ids}, degraded=True)


def volunteer_problems(v: Volunteer, min_reason_chars: int) -> list[str]:
    """自荐是否敷衍：没有说明擅长什么，或想做的子任务都没有给出具体理由。"""
    problems = []
    if len(v.strengths) < min_reason_chars:
        problems.append("没有具体说明自己擅长什么")
    wants = [r for s, r in v.preferences.values() if s == "want"]
    if not wants:
        problems.append("没有表示想做任何子任务")
    elif all(len(r) < min_reason_chars for r in wants):
        problems.append("想做的子任务没有给出具体理由")
    return problems


# --- 分配 ---------------------------------------------------------------------


class AssignmentItem(_Loose):
    subtask: str
    members: list[str] = Field(default_factory=list)


class AssignmentOutput(_Loose):
    assignments: list[AssignmentItem] = Field(default_factory=list)
    rationale: str = ""


@dataclass(frozen=True)
class Assignment:
    owners: dict[str, tuple[str, ...]]  # 子任务 id → 负责人代号（保持子任务顺序）
    rationale: str = ""
    repaired: tuple[str, ...] = ()  # 代码补齐的说明
    degraded: bool = False  # 统筹的分配完全不可用，全部由代码分配

    def load(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for codes in self.owners.values():
            for c in codes:
                counts[c] = counts.get(c, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignments": [{"subtask": k, "members": list(v)} for k, v in self.owners.items()],
            "rationale": self.rationale,
            "repaired": list(self.repaired),
            "degraded": self.degraded,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Assignment:
        owners = {a["subtask"]: tuple(a["members"]) for a in d["assignments"]}
        return cls(owners, d.get("rationale", ""), tuple(d.get("repaired") or ()), d["degraded"])


def parse_assignment(
    text: str, subtask_ids: Sequence[str], to_code: Callable[[str], str | None]
) -> Assignment:
    output = AssignmentOutput.model_validate(extract_json_object(text))
    owners: dict[str, list[str]] = {s: [] for s in subtask_ids}
    for a in output.assignments:
        sid = _norm_id(a.subtask)
        if sid not in owners:
            continue
        for m in a.members:
            code = to_code(m)
            if code and code not in owners[sid]:
                owners[sid].append(code)
    return Assignment({k: tuple(v) for k, v in owners.items()}, output.rationale.strip())


def coverage_problems(a: Assignment, codes: Sequence[str]) -> list[str]:
    """分配是否满足规则：每人至少一块、每块至少一人、负担相差不超过 1。"""
    problems = []
    load = a.load()
    idle = [c for c in codes if c not in load]
    if idle:
        problems.append(f"没有分到任务的组员：{idle}")
    empty = [s for s, owners in a.owners.items() if not owners]
    if empty:
        problems.append(f"没有负责人的子任务：{empty}")
    counts = [load.get(c, 0) for c in codes]
    if counts and max(counts) - min(counts) > 1:
        problems.append("负担不均衡（块数相差超过 1）")
    return problems


def repair_assignment(
    a: Assignment,
    codes: Sequence[str],
    subtasks: Sequence[Subtask],
    volunteers: Mapping[str, Volunteer],
    tags: Mapping[str, Sequence[str]],
    rng: random.Random,
) -> Assignment:
    """按规则补齐统筹的分配（只做必要的改动）：

    1. 去掉不在场的组员；2. 没有负责人的子任务交给最合适且负担最轻的组员；
    3. 没有任务的组员加入最合适的子任务（与原负责人各自独立完成）；
    4. 负担相差超过 1 时，把负担最重者的一块移交给负担最轻、且该块还没有他的人。
    合适程度 = 自荐（想做 2 / 可以 1 / 不适合 -1）+ 能力标签与子任务建议标签的重合数。
    """
    present = set(codes)
    owners = {s.id: [c for c in a.owners.get(s.id, ()) if c in present] for s in subtasks}
    by_id = {s.id: s for s in subtasks}
    notes: list[str] = []
    order = list(codes)
    rng.shuffle(order)  # 同分时随机，不偏向座位顺序
    rank = {c: i for i, c in enumerate(order)}

    def fit(code: str, sid: str) -> int:
        stance = volunteers[code].stance(sid) if code in volunteers else "can"
        overlap = len(set(tags.get(code, ())) & set(by_id[sid].tags))
        return STANCE_SCORE[stance] + overlap

    def load(code: str) -> int:
        return sum(code in v for v in owners.values())

    for sid, current in owners.items():
        if not current:
            best = min(codes, key=lambda c: (load(c), -fit(c, sid), rank[c]))
            current.append(best)
            notes.append(f"{sid} 没有负责人，交给 {best}")
    for code in codes:
        if load(code) == 0:
            sid = min(owners, key=lambda s: (-fit(code, s), len(owners[s]), s))
            owners[sid].append(code)
            notes.append(f"{code} 没有分到任务，加入 {sid}（与原负责人各自独立完成）")
    for _ in range(len(codes) * len(subtasks)):
        heavy = max(codes, key=lambda c: (load(c), -rank[c]))
        light = min(codes, key=lambda c: (load(c), rank[c]))
        if load(heavy) - load(light) <= 1:
            break
        movable = [s for s, v in owners.items() if heavy in v and light not in v]
        if not movable:
            break
        sid = max(movable, key=lambda s: (fit(light, s), s))
        owners[sid] = [light if c == heavy else c for c in owners[sid]]
        notes.append(f"{sid} 从 {heavy} 移交给 {light}（均衡负担）")
    return Assignment(
        {k: tuple(v) for k, v in owners.items()},
        a.rationale,
        tuple([*a.repaired, *notes]),
        a.degraded,
    )


def work_items(subtasks: Sequence[Subtask], assignment: Assignment) -> dict[str, tuple[str, str]]:
    """每份成果的编号：W1、W2…（按子任务顺序、再按负责人顺序）→ (子任务 id, 代号)。"""
    items: dict[str, tuple[str, str]] = {}
    for s in subtasks:
        for code in assignment.owners.get(s.id, ()):
            items[f"W{len(items) + 1}"] = (s.id, code)
    return items


# --- 合并 ---------------------------------------------------------------------

Level = Literal["full", "partial", "none"]


class AdoptedWork(_Loose):
    member: str
    level: Level = "none"
    reason: str = ""

    @field_validator("level", mode="before")
    @classmethod
    def _level(cls, v: Any) -> str:
        return _choice(v, ("full", "partial", "none"), "none")


class SubtaskAdoption(_Loose):
    subtask: str
    adopted: list[AdoptedWork] = Field(default_factory=list)


class MergeOutput(_Loose):
    result: str = Field(min_length=1)
    subtasks: list[SubtaskAdoption] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    confidence: Confidence = "medium"

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, v: Any) -> str:
        return _choice(v, ("high", "medium", "low"), "medium")


@dataclass(frozen=True)
class Merge:
    output: MergeOutput
    degraded: bool = False
    error: str | None = None

    def adoption(self) -> dict[tuple[str, str], str]:
        """(子任务 id, 代号) → full / partial / none。"""
        return {(s.subtask, a.member): a.level for s in self.output.subtasks for a in s.adopted}

    def to_dict(self) -> dict[str, Any]:
        return {**self.output.model_dump(), "degraded": self.degraded, "error": self.error}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Merge:
        return cls(MergeOutput.model_validate(d), d.get("degraded", False), d.get("error"))


RESULT_HEADING = "## 完整成果"
NOTES_HEADING = "## 合并说明"


def parse_merge(
    text: str, subtask_ids: Sequence[str], to_code: Callable[[str], str | None]
) -> Merge:
    """解析合并结果；采纳情况中的子任务与成员统一为 id 与代号，未知的丢弃。

    merge/v2：成果是"## 完整成果"下的 Markdown 正文，采纳情况等在"## 合并说明"后的 JSON 里。
    正文可以单独成立：合并说明缺失或格式不符时保留正文，只记录说明不可用。
    没有"完整成果"标题时按 merge/v1 的整段 JSON 解析。格式错误抛 ValueError。
    """
    start = text.find(RESULT_HEADING)
    if start == -1:
        return _clean_merge(
            MergeOutput.model_validate(extract_json_object(text)), subtask_ids, to_code
        )
    body = text[start + len(RESULT_HEADING) :]
    split = body.rfind(NOTES_HEADING)  # 正文里可能有自己的二级标题，取最后一个"合并说明"
    result = (body[:split] if split != -1 else body).strip()
    if not result:
        raise ValueError("完整成果为空")
    notes: dict[str, Any] = {}
    error = None
    if split == -1:
        error = "缺少合并说明，未记录采纳情况"
    else:
        try:
            notes = extract_json_object(body[split + len(NOTES_HEADING) :])
        except ValueError:
            error = "合并说明格式不符，未记录采纳情况"
    try:
        output = MergeOutput.model_validate({**notes, "result": result})
    except ValueError:
        output, error = MergeOutput(result=result), "合并说明格式不符，未记录采纳情况"
    merge = _clean_merge(output, subtask_ids, to_code)
    return Merge(merge.output, error=error)


def _clean_merge(
    output: MergeOutput, subtask_ids: Sequence[str], to_code: Callable[[str], str | None]
) -> Merge:
    known = set(subtask_ids)
    cleaned = []
    for s in output.subtasks:
        sid = _norm_id(s.subtask)
        if sid not in known:
            continue
        adopted = []
        for a in s.adopted:
            code = to_code(a.member)
            if code:
                adopted.append(AdoptedWork(member=code, level=a.level, reason=a.reason))
        cleaned.append(SubtaskAdoption(subtask=sid, adopted=adopted))
    output.subtasks = cleaned
    return Merge(output)


def fallback_merge(
    error: str, subtasks: Sequence[Subtask], texts: Mapping[tuple[str, str], str]
) -> Merge:
    """统筹合并失败：按子任务顺序把各份成果原样拼接（把握程度 low，会触发升级询问）。"""
    parts = []
    for s in subtasks:
        for (sid, code), text in texts.items():
            if sid == s.id:
                parts.append(f"## {s.id} {s.title}（{code}）\n\n{text}")
    return Merge(
        MergeOutput(
            result="\n\n".join(parts) or "（合并失败，也没有可用的成果）",
            gaps=["统筹未能合并，以上为各份成果原文"],
            confidence="low",
        ),
        degraded=True,
        error=error,
    )


@dataclass
class CollabState:
    """协同模式在各步骤之间传递的数据（可从数据库恢复）。"""

    subtasks: tuple[Subtask, ...] = ()
    subtasks_degraded: bool = False
    volunteers: dict[str, Volunteer] = field(default_factory=dict)
    assignment: Assignment | None = None
    works: dict[tuple[str, str], str] = field(default_factory=dict)  # (子任务, 代号) → 成果
    cross_reviews: dict[str, tuple] = field(default_factory=dict)  # 评审者 → CheckedReview…
    reworks: dict[tuple[str, str], Any] = field(default_factory=dict)  # (子任务, 代号) → Revision
    merge: Merge | None = None

    def items(self) -> dict[str, tuple[str, str]]:
        return work_items(self.subtasks, self.assignment) if self.assignment else {}

    def latest(self) -> dict[tuple[str, str], str]:
        """每份成果的最终版：有修改用修改稿，否则用原成果。"""
        return {
            key: (self.reworks[key].answer if key in self.reworks else text)
            for key, text in self.works.items()
        }
