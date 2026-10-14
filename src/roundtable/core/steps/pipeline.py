"""协同流水线的规则（纯逻辑，不调用模型）：

- 拆分校验 `pipeline_problems()`：不是"整题当一个子任务"、每位成员都有一块不同的实质工作、有上下游、
  类型不单一、各类型的规则（执行生成要依赖写提示词、审查要依赖被审成果、整合至少两个上游…）；
- 模板兜底 `pick_template()` / `build_template()`：统筹两次都拆不好时按模板生成流水线，
  成员比步骤多时把可以并行的步骤按内容拆成平行子任务；
- 分配 `assignment_conflicts()` / `solve_assignment()`：每块恰好一人、负担均衡、审查者回避上游的作者
  （写提示词的人永远不审查自己提示词生成的结果；成员足够时执行生成者也不审查自己生成的结果）。

类型、校验阈值和模板全部来自配置（`roundtable.yaml` 的 `collab`），这里不出现任何具体的类型名。
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from roundtable.core.config.schema import (
    CollabRules,
    KindRule,
    PipelineTemplate,
    TemplateDep,
    TemplateStep,
)

from .collab_schemas import STANCE_SCORE, Assignment, Subtask, Volunteer, layers
from .effort import similarity

KIND_TAG_WEIGHT = 3  # 类型偏好的能力标签比自荐更重要


def ancestors(subtasks: Sequence[Subtask]) -> dict[str, set[str]]:
    """每个子任务的全部上游（直接 + 间接）。"""
    by_id = {s.id: s for s in subtasks}
    result: dict[str, set[str]] = {}

    def walk(sid: str) -> set[str]:
        if sid not in result:
            result[sid] = set()  # 防环
            found: set[str] = set()
            for dep in by_id[sid].depends_on:
                if dep in by_id:
                    found |= {dep} | walk(dep)
            result[sid] = found
        return result[sid]

    for s in subtasks:
        walk(s.id)
    return result


def _connected(subtasks: Sequence[Subtask]) -> bool:
    ids = [s.id for s in subtasks]
    if len(ids) <= 1:
        return True
    adj: dict[str, set[str]] = {i: set() for i in ids}
    for s in subtasks:
        for d in s.depends_on:
            if d in adj:
                adj[s.id].add(d)
                adj[d].add(s.id)
    seen, stack = {ids[0]}, [ids[0]]
    while stack:
        for nxt in adj[stack.pop()] - seen:
            seen.add(nxt)
            stack.append(nxt)
    return len(seen) == len(ids)


def pipeline_problems(
    subtasks: Sequence[Subtask],
    *,
    rules: CollabRules,
    members: int,
    question: str,
    media_available: Sequence[str] = (),
    check_siblings: bool = True,
) -> list[str]:
    """拆分是否构成合格的流水线；返回具体问题（空表示合格），用来让统筹重拆。

    check_siblings：检查平行子任务有没有重复内容。模板生成的平行子任务按设计互不重叠
    （每个部分都写明了自己负责的那一份），不需要检查。
    """
    pr = rules.pipeline
    problems: list[str] = []
    n = len(subtasks)
    kinds = rules.kinds
    need = min(members, rules.max_subtasks)
    if n < need:
        problems.append(
            f"子任务只有 {n} 个，少于成员数 {members}：每位成员都要分到一块不同的实质工作。"
            "把可以并行的步骤按内容拆成平行子任务（例如四个角色的文案每人写一个），不要让几个人做同一份内容"
        )
    whole = [
        s.id
        for s in subtasks
        if any(p in f"{s.title}\n{s.requirements}" for p in pr.whole_task_phrases)
        or max(
            similarity(question, f"{s.title}\n{s.requirements}"),
            similarity(question, s.requirements),
        )
        >= pr.whole_task_similarity
    ]
    if n == 1 or whole:
        problems.append(
            "把整道题当作一个子任务了"
            + (f"（{'、'.join(whole)}）" if whole else "")
            + "：必须拆成流水线，每个子任务只做其中一段，产出交给下游继续做"
        )
    unknown = [s.id for s in subtasks if s.kind not in kinds]
    if unknown:
        problems.append(f"子任务 {'、'.join(unknown)} 没有标注类型，或类型不在给定的词表内")
    if n > 1 and not _connected(subtasks):
        problems.append("依赖图不连通：有子任务和其他子任务没有上下游关系，流水线要首尾相连")
    try:
        depth = len(layers(subtasks))
    except ValueError:
        depth = 0
    if n > 1 and depth < 2:
        problems.append("所有子任务都在同一层：至少要有上下游（后面的子任务依赖前面的产出）")
    used = {s.kind for s in subtasks if s.kind in kinds}
    if len(used) < min(pr.min_kinds, n):
        problems.append(
            f"类型太单一（只有 {len(used)} 种）：各段工作的性质应不同，"
            f"至少 {min(pr.min_kinds, n)} 种"
        )
    by_id = {s.id: s for s in subtasks}
    for s in subtasks:
        rule = kinds.get(s.kind or "")
        if rule is None:
            continue
        dep_kinds = {by_id[d].kind for d in s.depends_on if d in by_id}
        if rule.needs and not dep_kinds & set(rule.needs):
            names = "、".join(kinds[k].label for k in rule.needs)
            problems.append(f"{s.id}（{rule.label}）必须依赖「{names}」类型的子任务")
        if rule.audit and not any(
            by_id[d].kind in kinds and not kinds[by_id[d].kind].audit
            for d in s.depends_on
            if d in by_id
        ):
            problems.append(f"{s.id}（{rule.label}）必须依赖被审查的成果（非审查类子任务）")
        if len(s.depends_on) < rule.min_upstream:
            problems.append(
                f"{s.id}（{rule.label}）至少要依赖 {rule.min_upstream} 个上游子任务，"
                f"现在 {len(s.depends_on)} 个"
            )
        if rule.requires_media and not media_available:
            problems.append(f"{s.id}（{rule.label}）需要媒体生成模型，但当前没有可用的")
    groups: dict[tuple[str | None, tuple[str, ...]], list[Subtask]] = {}
    for s in subtasks:
        groups.setdefault((s.kind, tuple(sorted(s.depends_on))), []).append(s)
    for sibs in groups.values() if check_siblings else ():
        for i, a in enumerate(sibs):
            for b in sibs[i + 1 :]:
                same = (
                    similarity(a.title, b.title) >= 1.0
                    or (a.requirements and similarity(a.requirements, b.requirements) >= 1.0)
                    or similarity(f"{a.title}\n{a.requirements}", f"{b.title}\n{b.requirements}")
                    >= pr.sibling_similarity
                )
                if same:
                    problems.append(
                        f"{a.id} 和 {b.id} 内容重复：平行子任务必须按内容划分成互不重叠的部分"
                    )
    return problems


def normalize_media(
    subtasks: Sequence[Subtask], rules: CollabRules, available: Sequence[str]
) -> tuple[Subtask, ...]:
    """需要媒体生成的类型（执行生成）补上媒体种类；其他类型不带媒体（避免旧流程的重复生成）。"""
    result = []
    for s in subtasks:
        rule = rules.kinds.get(s.kind or "")
        if rule is None:
            result.append(s)
        elif rule.requires_media:
            result.append(replace(s, media=s.media or (available[0] if available else None)))
        else:
            result.append(replace(s, media=None))
    return tuple(result)


# --- 模板 ----------------------------------------------------------------------


def pick_template(
    rules: CollabRules,
    *,
    question: str,
    image_attachment: bool,
    media_available: Sequence[str],
    tools: Sequence[str],
) -> PipelineTemplate:
    """按顺序取第一个条件满足的模板（最后一个无条件，兜底）。"""
    text = question.lower()
    for t in rules.templates:
        w = t.when
        if w.image_attachment is not None and w.image_attachment != image_attachment:
            continue
        if not set(w.media) <= set(media_available):
            continue
        if not set(w.tools) <= set(tools):
            continue
        if w.keywords and not any(k.lower() in text for k in w.keywords):
            continue
        return t
    return rules.templates[-1]


def _dep(d: TemplateDep | str) -> tuple[str, str]:
    return (d, "") if isinstance(d, str) else (d.id, d.gives)


def build_template(
    template: PipelineTemplate, members: int, max_subtasks: int
) -> tuple[Subtask, ...]:
    """模板 → 子任务。成员比步骤多时，把 split 的步骤按内容拆成平行子任务（轮流加一份），
    保证每位成员都有不同的工作；子任务编号按顺序重排为 T1…Tn。"""
    steps = list(template.steps)
    parts = {s.id: 1 for s in steps}
    extra = max(0, min(members, max_subtasks) - len(steps))
    splittable = [s.id for s in steps if s.split]
    i = 0
    while extra > 0 and splittable:
        parts[splittable[i % len(splittable)]] += 1
        extra -= 1
        i += 1
    ids: dict[str, list[str]] = {}
    counter = 0
    for s in steps:
        ids[s.id] = [f"T{counter + k + 1}" for k in range(parts[s.id])]
        counter += parts[s.id]
    result: list[Subtask] = []
    for s in steps:
        deps: list[str] = []
        gives: dict[str, str] = {}
        for raw in s.depends_on:
            dep, text = _dep(raw)
            for new in ids[dep]:
                deps.append(new)
                if text:
                    gives[new] = text
        k = parts[s.id]
        for n, sid in enumerate(ids[s.id]):
            result.append(_part(s, sid, n, k, tuple(deps), gives))
    return tuple(result)


def _part(
    step: TemplateStep, sid: str, n: int, k: int, deps: tuple[str, ...], gives: dict[str, str]
) -> Subtask:
    title, requirements = step.title, step.requirements
    if k > 1:
        hint = step.split_hints[n] if n < len(step.split_hints) else f"第 {n + 1} 部分"
        title = f"{step.title}·{hint}"
        requirements = (
            f"{step.requirements}\n本步骤已按内容拆成 {k} 个平行子任务，你只负责「{hint}」。"
            f"{step.split_by}。其他部分由同伴负责，不要重复他们的内容。"
        ).strip()
    return Subtask(
        sid,
        title,
        requirements,
        step.acceptance,
        (),
        deps,
        step.media,
        step.kind,
        tuple(gives.items()),
    )


# --- 分配 ----------------------------------------------------------------------


def assignment_conflicts(
    owners: Mapping[str, Sequence[str]],
    subtasks: Sequence[Subtask],
    kinds: Mapping[str, KindRule],
) -> tuple[list[str], list[str]]:
    """分配违反的流水线规则：(硬性, 可放宽)。

    硬性：不可并行的类型有多个负责人；审查类的负责人同时负责 avoid_hard 类型的上游。
    可放宽：同上但属于 avoid_soft（成员太少无法满足时由 solve_assignment 放宽）。
    """
    hard: list[str] = []
    soft: list[str] = []
    by_id = {s.id: s for s in subtasks}
    anc = ancestors(subtasks)
    for s in subtasks:
        rule = kinds.get(s.kind or "")
        if rule is None:
            continue
        mine = set(owners.get(s.id, ()))
        if not rule.parallel and len(mine) > 1:
            hard.append(f"{s.id}（{rule.label}）只能一人负责")
        for bucket, avoid in ((hard, rule.avoid_hard), (soft, rule.avoid_soft)):
            for up in sorted(anc[s.id]):
                if by_id[up].kind in avoid and mine & set(owners.get(up, ())):
                    bucket.append(
                        f"{s.id}（{rule.label}）的负责人同时负责上游的 {up}"
                        f"（{kinds[by_id[up].kind].label}）"
                    )
    return hard, soft


@dataclass
class _Search:
    subtasks: Sequence[Subtask]
    codes: Sequence[str]
    kinds: Mapping[str, KindRule]
    fit: Mapping[tuple[str, str], int]
    anc: Mapping[str, set[str]]

    def cost(self, own: Mapping[str, str]) -> int:
        by_id = {s.id: s for s in self.subtasks}
        total = 0
        load = {c: 0 for c in self.codes}
        for c in own.values():
            load[c] += 1
        spread = max(load.values()) - min(load.values())
        total += 100_000 * max(0, spread - 1)
        for s in self.subtasks:
            rule = self.kinds.get(s.kind or "")
            total -= self.fit.get((own[s.id], s.id), 0)
            if rule is None:
                continue
            for up in self.anc[s.id]:
                k = by_id[up].kind
                if own[s.id] != own[up]:
                    continue
                if k in rule.avoid_hard:
                    total += 1_000_000
                elif k in rule.avoid_soft:
                    # avoid_soft 里越靠前的类型越晚放宽
                    total += 1_000 * (len(rule.avoid_soft) - rule.avoid_soft.index(k))
        return total


def solve_assignment(
    a: Assignment,
    codes: Sequence[str],
    subtasks: Sequence[Subtask],
    volunteers: Mapping[str, Volunteer],
    tags: Mapping[str, Sequence[str]],
    kinds: Mapping[str, KindRule],
    rng: random.Random,
) -> Assignment:
    """流水线的分配：每个子任务恰好一位负责人，负担相差不超过 1，满足回避规则。

    统筹的分配只作为起点：用爬山 + 随机重启在"负责人"空间里搜索。回避规则有硬有软，
    先试着全部满足，成员太少无法满足时只放宽 avoid_soft（靠前的类型最后放宽），avoid_hard 永不放宽。
    子任务比成员少（超过 max_subtasks 的大桌）时，没有任务的成员加入可并行类型的子任务。
    """
    present = list(codes)
    by_id = {s.id: s for s in subtasks}
    anc = ancestors(subtasks)

    def fit(code: str, sid: str) -> int:
        v = volunteers.get(code)
        stance = STANCE_SCORE[v.stance(sid)] if v else 1
        rule = kinds.get(by_id[sid].kind or "")
        kind_tags = set(rule.tags) if rule else set()
        mine = set(tags.get(code, ()))
        return stance + len(mine & set(by_id[sid].tags)) + KIND_TAG_WEIGHT * len(mine & kind_tags)

    table = {(c, s.id): fit(c, s.id) for c in present for s in subtasks}
    search = _Search(subtasks, present, kinds, table, anc)
    start: dict[str, str] = {}
    for s in subtasks:
        mine = [c for c in a.owners.get(s.id, ()) if c in present]
        if mine:
            start[s.id] = mine[0]
    order = list(present)
    rng.shuffle(order)
    rank = {c: i for i, c in enumerate(order)}

    def complete(own: dict[str, str]) -> dict[str, str]:
        load = {c: 0 for c in present}
        for c in own.values():
            load[c] += 1
        for s in subtasks:
            if s.id not in own:
                best = min(present, key=lambda c: (load[c], -table[(c, s.id)], rank[c]))
                own[s.id] = best
                load[best] += 1
        return own

    def climb(own: dict[str, str]) -> tuple[int, dict[str, str]]:
        cur = search.cost(own)
        for _ in range(300):
            best_move, best_cost = None, cur
            ids = [s.id for s in subtasks]
            for sid in ids:
                for c in present:
                    if c == own[sid]:
                        continue
                    trial = {**own, sid: c}
                    cost = search.cost(trial)
                    if cost < best_cost:
                        best_move, best_cost = trial, cost
            for i, x in enumerate(ids):
                for y in ids[i + 1 :]:
                    if own[x] == own[y]:
                        continue
                    trial = {**own, x: own[y], y: own[x]}
                    cost = search.cost(trial)
                    if cost < best_cost:
                        best_move, best_cost = trial, cost
            if best_move is None:
                break
            own, cur = best_move, best_cost
        return cur, own

    winner = climb(complete(dict(start)))
    for _ in range(24):  # 随机重启：从统筹的分配里随机丢掉一半再补全，避免卡在局部最优
        seed_map = dict(start)
        for sid in rng.sample([s.id for s in subtasks], k=max(1, len(subtasks) // 2)):
            seed_map.pop(sid, None)
        result = climb(complete(seed_map))
        if result[0] < winner[0]:
            winner = result
    own = winner[1]
    hard, soft_left = assignment_conflicts({k: [v] for k, v in own.items()}, subtasks, kinds)
    notes: list[str] = []
    if soft_left:
        notes.append("成员太少，无法完全避开审查者与上游作者重合：" + "；".join(soft_left))
    if hard:  # 极端情况（比如只有一两个成员且硬性规则无解）：保留结果并说明
        notes.append("无法满足以下硬性规则，请留意：" + "；".join(hard))
    owners: dict[str, list[str]] = {s.id: [own[s.id]] for s in subtasks}
    for s in subtasks:
        first = next((c for c in a.owners.get(s.id, ()) if c in present), None)
        if first != own[s.id]:
            notes.append(f"{s.id} 的负责人调整为 {own[s.id]}（流水线规则 / 均衡负担）")
    load = {c: sum(c in v for v in owners.values()) for c in present}
    for c in present:  # 子任务比成员少：没有任务的成员加入可并行类型的子任务
        if load[c]:
            continue
        candidates = [
            s for s in subtasks if (kinds.get(s.kind or "") is None or kinds[s.kind or ""].parallel)
        ] or list(subtasks)
        target = min(candidates, key=lambda s: (len(owners[s.id]), -table[(c, s.id)], s.id))
        owners[target.id].append(c)
        load[c] += 1
        notes.append(
            f"{c} 没有分到任务，加入 {target.id}（子任务比成员少，与原负责人各自独立完成）"
        )
    return Assignment(
        {k: tuple(v) for k, v in owners.items()},
        a.rationale,
        tuple([*a.repaired, *notes]),
        a.degraded,
    )
