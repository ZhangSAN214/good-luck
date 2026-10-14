"""贡献统计：根据一张桌子的状态，计算每位组员的贡献（只看产出，不看是哪个模型）。

- answered        有作答（协同模式：完成的成果份数）
- adopted         汇总的 adopted_from 中，来自该组员的要点数
                  （协同模式：合并时全部或部分采用的成果份数）
- volunteer_accepted  协同模式：分到了自己"想做"的子任务的次数
- valid_review    写出的有效评审数
- valid_issue     有效评审中指出的问题数
- issue_accepted  被作者在修订回应中明确采纳（含部分采纳）的问题数；
                  只认作者确实收到的有效评审里的问题，未列具体问题的整条采纳按 1 计
- redo            被打回重做的次数
- lazy            被标记敷衍的次数
- dropped         中途退出
"""

from __future__ import annotations

from collections import Counter

from .schemas import TableState

KINDS = (
    "answered",
    "adopted",
    "volunteer_accepted",
    "valid_review",
    "valid_issue",
    "issue_accepted",
    "redo",
    "lazy",
    "dropped",
)


def table_contributions(state: TableState, codes: list[str]) -> dict[str, Counter[str]]:
    result: dict[str, Counter[str]] = {c: Counter() for c in codes}

    for code in codes:
        if code in state.answers:
            result[code]["answered"] += 1
        if code in state.dropped:
            result[code]["dropped"] += 1

    synthesis = state.synthesis
    if synthesis is not None and not synthesis.degraded:
        for adoption in synthesis.output.adopted_from:
            for code in adoption.members:
                if code in result:
                    result[code]["adopted"] += 1

    # 有效评审：(评审者, 作者) → 问题数
    valid: dict[tuple[str, str], int] = {}
    for reviewer, reviews in state.reviews.items():
        for r in reviews:
            if r.valid and reviewer in result:
                result[reviewer]["valid_review"] += 1
                result[reviewer]["valid_issue"] += len(r.issues)
                valid[(reviewer, r.target)] = len(r.issues)

    for author, revision in state.revisions.items():
        for d in revision.decisions:
            issues = valid.get((d.reviewer, author))
            if issues is None or not d.accepted or d.reviewer not in result:
                continue  # 作者没收到这位组员的有效评审：不计
            if d.issue is None or 1 <= d.issue <= issues:
                result[d.reviewer]["issue_accepted"] += 1

    _collab(state, result)

    for record in state.effort.values():
        code = record.code
        if code in result:
            if record.redone:
                result[code]["redo"] += 1
            if record.lazy:
                result[code]["lazy"] += 1
    return {code: +counter for code, counter in result.items()}  # 去掉为 0 的类别


def _collab(state: TableState, result: dict[str, Counter[str]]) -> None:
    c = state.collab
    if c.assignment is None:
        return
    items = c.items()
    item_of = {v: k for k, v in items.items()}
    for _sid, code in c.works:
        if code in result:
            result[code]["answered"] += 1
    for sid, owners in c.assignment.owners.items():
        for code in owners:
            v = c.volunteers.get(code)
            if code in result and v is not None and v.stance(sid) == "want":
                result[code]["volunteer_accepted"] += 1
    if c.merge is not None and not c.merge.degraded:
        for (sid, code), level in c.merge.adoption().items():
            if code in result and level in ("full", "partial") and (sid, code) in c.works:
                result[code]["adopted"] += 1
    valid: dict[tuple[str, str], int] = {}  # (评审者, 成果编号) → 问题数
    for reviewer, reviews in c.cross_reviews.items():
        for r in reviews:
            if r.valid and reviewer in result and items.get(r.target, ("", ""))[1] != reviewer:
                result[reviewer]["valid_review"] += 1
                result[reviewer]["valid_issue"] += len(r.issues)
                valid[(reviewer, r.target)] = len(r.issues)
    for key, revision in c.reworks.items():
        item = item_of.get(key)
        for d in revision.decisions:
            issues = valid.get((d.reviewer, item))
            if issues is None or not d.accepted:
                continue
            if d.issue is None or 1 <= d.issue <= issues:
                result[d.reviewer]["issue_accepted"] += 1
