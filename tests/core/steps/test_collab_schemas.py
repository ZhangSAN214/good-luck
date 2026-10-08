"""协同模式的解析与规则：拆分校验、依赖分层、自荐、分配校验与代码补齐、合并。"""

from __future__ import annotations

import json
import random
from collections import Counter

import pytest

from roundtable.core.jsonout import JSONOutputError
from roundtable.core.steps.collab_schemas import (
    Assignment,
    Subtask,
    Volunteer,
    coverage_problems,
    fallback_merge,
    layers,
    parse_assignment,
    parse_decomposition,
    parse_merge,
    parse_volunteer,
    repair_assignment,
    volunteer_problems,
    work_items,
)

VOCAB = ["math", "writing"]
CODES = ["甲", "乙", "丙", "丁"]


def to_code(text):
    t = text.strip().removeprefix("组员").strip()
    return t if t in CODES else None


def decomposition(subtasks):
    return json.dumps({"subtasks": subtasks}, ensure_ascii=False)


def st(i, deps=(), tags=("math",)):
    return {"id": f"t{i}", "title": f"第{i}块", "tags": list(tags), "depends_on": list(deps)}


def test_parse_decomposition_normalizes_and_filters_tags():
    out = parse_decomposition(
        decomposition([st(1, tags=["math", "telepathy"]), st(2, deps=["t1"])]),
        max_subtasks=5,
        vocabulary=VOCAB,
    )
    assert [s.id for s in out] == ["T1", "T2"]
    assert out[0].tags == ("math",) and out[1].depends_on == ("T1",)


@pytest.mark.parametrize(
    "subtasks, error",
    [
        ([], ValueError),
        ([st(1), st(1)], ValueError),  # 重复 id
        ([st(1, deps=["t9"])], ValueError),  # 依赖不存在
        ([st(1, deps=["t1"])], ValueError),  # 依赖自己
        ([st(1, deps=["t2"]), st(2, deps=["t1"])], ValueError),  # 循环
        ([st(i) for i in range(1, 5)], ValueError),  # 超过上限 3
    ],
)
def test_invalid_decomposition(subtasks, error):
    with pytest.raises(error):
        parse_decomposition(decomposition(subtasks), max_subtasks=3, vocabulary=VOCAB)
    with pytest.raises(JSONOutputError):
        parse_decomposition("不是 JSON", max_subtasks=3, vocabulary=VOCAB)


def test_layers():
    subs = [
        Subtask("T1", "a"),
        Subtask("T2", "b", depends_on=("T1",)),
        Subtask("T3", "c"),
        Subtask("T4", "d", depends_on=("T2", "T3")),
    ]
    assert layers(subs) == [["T1", "T3"], ["T2"], ["T4"]]


def test_parse_volunteer_defaults_missing_to_can():
    v = parse_volunteer(
        json.dumps(
            {
                "strengths": "推导",
                "preferences": [
                    {"subtask": "t1", "stance": "WANT", "reason": "我会"},
                    {"subtask": "T9", "stance": "want", "reason": "不存在"},
                    {"subtask": "T2", "stance": "maybe", "reason": "?"},
                ],
            },
            ensure_ascii=False,
        ),
        ["T1", "T2", "T3"],
    )
    assert v.preferences == {"T1": ("want", "我会"), "T2": ("can", "?"), "T3": ("can", "")}
    assert Volunteer.from_dict(v.to_dict()) == v


def test_volunteer_problems():
    good = Volunteer("擅长求导和数值计算的检查", {"T1": ("want", "这块是求导，我做得多也做得准")})
    assert volunteer_problems(good, 10) == []
    assert "没有具体说明自己擅长什么" in volunteer_problems(Volunteer("会", good.preferences), 10)
    lazy = Volunteer("擅长求导和数值计算的检查", {"T1": ("can", "")})
    assert volunteer_problems(lazy, 10) == ["没有表示想做任何子任务"]
    short = Volunteer("擅长求导和数值计算的检查", {"T1": ("want", "行")})
    assert volunteer_problems(short, 10) == ["想做的子任务没有给出具体理由"]


def test_parse_assignment_and_coverage():
    a = parse_assignment(
        json.dumps(
            {
                "assignments": [
                    {"subtask": "t1", "members": ["组员甲", "甲", "组员戊"]},
                    {"subtask": "T9", "members": ["组员乙"]},
                ]
            },
            ensure_ascii=False,
        ),
        ["T1", "T2"],
        to_code,
    )
    assert a.owners == {"T1": ("甲",), "T2": ()}
    problems = coverage_problems(a, CODES)
    assert any("没有分到任务" in p for p in problems)
    assert any("没有负责人" in p for p in problems)
    ok = Assignment({"T1": ("甲", "乙"), "T2": ("丙", "丁")})
    assert coverage_problems(ok, CODES) == []
    uneven = Assignment({"T1": ("甲",), "T2": ("甲",), "T3": ("甲", "乙"), "T4": ("丙", "丁")})
    assert coverage_problems(uneven, CODES) == ["负担不均衡（块数相差超过 1）"]


SUBS = [Subtask("T1", "a", tags=("math",)), Subtask("T2", "b", tags=("writing",))]


@pytest.mark.parametrize("seed", range(30))
def test_repair_fills_everything(seed):
    vols = {
        "甲": Volunteer("", {"T1": ("want", ""), "T2": ("unfit", "")}),
        "乙": Volunteer("", {"T1": ("unfit", ""), "T2": ("want", "")}),
    }
    empty = Assignment({"T1": (), "T2": ()}, degraded=True)
    fixed = repair_assignment(empty, CODES, SUBS, vols, {}, random.Random(seed))
    assert coverage_problems(fixed, CODES) == []
    assert fixed.repaired and fixed.degraded
    assert "甲" in fixed.owners["T1"] and "乙" in fixed.owners["T2"]  # 尊重自荐


def test_repair_keeps_valid_assignment_untouched():
    ok = Assignment({"T1": ("甲", "乙"), "T2": ("丙", "丁")}, rationale="r")
    assert repair_assignment(ok, CODES, SUBS, {}, {}, random.Random(1)) == ok


def test_repair_uses_tags_and_balances_load():
    tags = {"甲": ("writing",), "乙": ("math",), "丙": (), "丁": ()}
    subs = [Subtask(f"T{i}", "x", tags=("math",)) for i in range(1, 7)]
    heavy = Assignment({s.id: ("甲",) for s in subs})
    fixed = repair_assignment(heavy, CODES, subs, {}, tags, random.Random(0))
    assert coverage_problems(fixed, CODES) == []
    load = Counter(c for owners in fixed.owners.values() for c in owners)
    assert max(load.values()) - min(load.values()) <= 1


def test_repair_drops_absent_members():
    a = Assignment({"T1": ("甲", "戊"), "T2": ("乙",)})
    fixed = repair_assignment(a, ["甲", "乙"], SUBS, {}, {}, random.Random(0))
    assert fixed.owners == {"T1": ("甲",), "T2": ("乙",)}


def test_work_items_numbering():
    a = Assignment({"T1": ("乙", "甲"), "T2": ("丙",)})
    assert work_items(SUBS, a) == {"W1": ("T1", "乙"), "W2": ("T1", "甲"), "W3": ("T2", "丙")}


def test_parse_merge_and_fallback():
    m = parse_merge(
        json.dumps(
            {
                "result": "成果",
                "subtasks": [
                    {
                        "subtask": "t1",
                        "adopted": [
                            {"member": "组员甲", "level": "FULL"},
                            {"member": "组员戊", "level": "full"},
                            {"member": "乙", "level": "???"},
                        ],
                    },
                    {"subtask": "T9", "adopted": []},
                ],
                "confidence": "low",
            },
            ensure_ascii=False,
        ),
        ["T1", "T2"],
        to_code,
    )
    assert m.adoption() == {("T1", "甲"): "full", ("T1", "乙"): "none"}
    assert m.output.confidence == "low"
    fb = fallback_merge("坏了", SUBS, {("T1", "组员甲"): "甲的", ("T2", "组员乙"): "乙的"})
    assert fb.degraded and fb.output.confidence == "low"
    assert fb.output.result.index("甲的") < fb.output.result.index("乙的")


MERGE_V2 = """## 完整成果
# 设定稿
第一部分含有"引号"和换行
## 小节标题（正文自己的二级标题）
内容

## 合并说明
```json
{"subtasks": [{"subtask": "t1", "adopted": [{"member": "组员甲", "level": "partial"}]}],
 "gaps": [], "open_questions": ["核实比例"], "confidence": "high"}
```"""


def test_parse_merge_v2_text_and_notes():
    m = parse_merge(MERGE_V2, ["T1"], to_code)
    assert m.output.result.startswith("# 设定稿") and "## 小节标题" in m.output.result
    assert "合并说明" not in m.output.result
    assert m.adoption() == {("T1", "甲"): "partial"}
    assert m.output.confidence == "high" and m.error is None and not m.degraded


def test_parse_merge_v2_keeps_result_when_notes_broken():
    broken = MERGE_V2.replace('"confidence": "high"}', '"confidence": "high"')
    m = parse_merge(broken, ["T1"], to_code)
    assert "设定稿" in m.output.result and not m.degraded
    assert m.adoption() == {} and "合并说明格式不符" in m.error
    missing = parse_merge("## 完整成果\n成果正文", ["T1"], to_code)
    assert missing.output.result == "成果正文" and "缺少合并说明" in missing.error
    with pytest.raises(ValueError):
        parse_merge("## 完整成果\n\n## 合并说明\n{}", ["T1"], to_code)


def test_json_with_raw_newlines_in_strings_is_accepted():
    """模型常把多行 Markdown 直接放进 JSON 字符串而不转义换行：不应整份拒绝。"""
    from roundtable.core.jsonout import extract_json_object

    text = '{"result": "第一行\n第二行\t缩进", "confidence": "low"}'
    assert extract_json_object(text)["result"] == "第一行\n第二行\t缩进"
    m = parse_merge('{"result": "多行\n成果", "confidence": "low"}', ["T1"], to_code)
    assert m.output.result == "多行\n成果"


def test_mentioned_subtasks_become_dependencies():
    text = decomposition(
        [
            st(1),
            st(2),
            {"id": "T3", "title": "质检标准", "requirements": "针对 T1、t2 的产出制定评审表"},
            {"id": "T4", "title": "复盘", "requirements": "参考T3", "depends_on": ["T3"]},
        ]
    )
    out = parse_decomposition(text, max_subtasks=5, vocabulary=VOCAB)
    by = {s.id: s for s in out}
    assert by["T3"].depends_on == ("T1", "T2")
    assert by["T4"].depends_on == ("T3",)
    assert layers(out) == [["T1", "T2"], ["T3"], ["T4"]]


def test_implied_dependency_that_would_create_a_cycle_is_skipped():
    text = decomposition(
        [
            {"id": "T1", "title": "a", "requirements": "与 T2 保持一致"},
            {"id": "T2", "title": "b", "depends_on": ["T1"]},
        ]
    )
    out = parse_decomposition(text, max_subtasks=5, vocabulary=VOCAB)
    assert out[0].depends_on == () and out[1].depends_on == ("T1",)
