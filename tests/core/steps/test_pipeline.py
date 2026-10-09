"""协同流水线的规则：拆分校验、模板兜底、分配约束（纯逻辑，不调用模型）。"""

from __future__ import annotations

import random

import pytest

from roundtable.core.config import load_config
from roundtable.core.steps.collab_schemas import (
    Assignment,
    Subtask,
    layers,
    parse_decomposition,
)
from roundtable.core.steps.pipeline import (
    ancestors,
    assignment_conflicts,
    build_template,
    normalize_media,
    pick_template,
    pipeline_problems,
    solve_assignment,
)

CFG = load_config()
RULES = CFG.roundtable.collab
KINDS = RULES.kinds
QUESTION = "做一张四格人物介绍卡"
COUNTS = (2, 3, 5, 8, 12)


def st(sid, kind, deps=(), title=None, req="", media=None, gives=()):
    return Subtask(
        sid,
        title or f"{kind}-{sid}",
        req or f"{sid} 的具体要求",
        "验收",
        (),
        tuple(deps),
        media,
        kind,
        tuple(gives),
    )


def problems(subtasks, members=3, media=("image",), question=QUESTION, siblings=True):
    return pipeline_problems(
        subtasks,
        rules=RULES,
        members=members,
        question=question,
        media_available=media,
        check_siblings=siblings,
    )


GOOD = [
    st("T1", "analyze"),
    st("T2", "write", ["T1"]),
    st("T3", "prompt", ["T1", "T2"]),
    st("T4", "generate", ["T3"], media="image"),
    st("T5", "review", ["T4", "T1"]),
    st("T6", "assemble", ["T4", "T2", "T5"]),
]


# --- 配置 ----------------------------------------------------------------------


def test_config_has_the_ten_kinds_and_five_templates():
    assert set(KINDS) == {
        "analyze", "research", "write", "prompt", "generate",
        "code", "verify", "review", "factcheck", "assemble",
    }  # fmt: skip
    assert [t.name for t in RULES.templates] == [
        "creation_reference", "creation", "research", "data_code", "document",
    ]  # fmt: skip
    assert RULES.templates[-1].when.keywords == [] and RULES.templates[-1].when.media == []
    for k in KINDS.values():  # 每种类型都有工作提示词
        assert k.prompt in CFG.roundtable.prompts


def test_template_steps_reference_only_configured_kinds_and_earlier_steps():
    for t in RULES.templates:
        seen = []
        for step in t.steps:
            assert step.kind in KINDS
            for d in step.depends_on:
                assert (d if isinstance(d, str) else d.id) in seen
            seen.append(step.id)


def test_research_and_data_templates_follow_the_requested_chains():
    chain = {t.name: [s.kind for s in t.steps] for t in RULES.templates}
    assert chain["research"] == ["research", "analyze", "write", "factcheck", "assemble"]
    assert chain["data_code"] == ["analyze", "code", "verify", "review", "write"]


# --- 拆分校验 ------------------------------------------------------------------


def test_a_valid_pipeline_has_no_problems():
    assert problems(GOOD, members=3) == []
    assert problems(GOOD, members=6) == []


def test_whole_task_as_one_subtask_is_rejected():
    one = [st("T1", "write", title="完成整道题", req="独立完成题目的全部要求")]
    text = " ".join(problems(one, members=3))
    assert "整道题当作一个子任务" in text and "少于成员数" in text


def test_whole_task_phrase_or_copy_of_the_question_is_rejected():
    lazy = [*GOOD[:5], st("T6", "assemble", ["T4", "T2", "T5"], title="完成整道题")]
    assert any("整道题" in p for p in problems(lazy, members=3))
    copy = [*GOOD[:5], st("T6", "assemble", ["T4", "T2", "T5"], req=QUESTION)]
    assert any("整道题" in p for p in problems(copy, members=3))


def test_fewer_subtasks_than_members_is_rejected_with_the_split_hint():
    got = problems(GOOD, members=8)
    assert any("少于成员数 8" in p and "按内容拆成平行子任务" in p for p in got)


def test_members_above_max_subtasks_only_need_max_subtasks():
    twelve = [
        st(f"T{i}", "write", ["T1"] if i > 1 else [], title=f"部分{i}", req=f"内容{i}" * 3)
        for i in range(1, 13)
    ]
    twelve[0] = st("T1", "analyze", title="分析", req="分析")
    twelve[-1] = st("T12", "review", ["T2"], title="审", req="审查")
    assert not any("少于成员数" in p for p in problems(twelve, members=20))


def test_disconnected_or_flat_pipelines_are_rejected():
    flat = [st("T1", "analyze"), st("T2", "write"), st("T3", "review")]
    got = " ".join(problems(flat, members=3))
    assert "不连通" in got and "同一层" in got
    island = [*GOOD, st("T7", "write", title="孤岛", req="孤岛内容")]
    assert any("不连通" in p for p in problems(island, members=7))


def test_a_single_kind_is_rejected():
    same = [st("T1", "write"), st("T2", "write", ["T1"]), st("T3", "write", ["T2"])]
    assert any("类型太单一" in p for p in problems(same, members=3))


def test_unknown_or_missing_kind_is_rejected():
    bad = [st("T1", None), *GOOD[1:]]
    assert any("T1" in p and "类型" in p for p in problems(bad, members=6))


def test_kind_rules_generate_review_assemble():
    no_prompt = [*GOOD[:3], st("T4", "generate", ["T2"], media="image"), *GOOD[4:]]
    assert any("T4" in p and "写提示词" in p for p in problems(no_prompt, members=6))
    review_alone = [*GOOD[:4], st("T5", "review", []), GOOD[5]]
    assert any("T5" in p and "被审查的成果" in p for p in problems(review_alone, members=6))
    thin = [*GOOD[:5], st("T6", "assemble", ["T5"])]
    assert any("T6" in p and "至少要依赖 2 个" in p for p in problems(thin, members=6))
    assert any("执行生成" in p and "没有可用" in p for p in problems(GOOD, members=6, media=()))
    chain = [
        GOOD[0],
        st("T2", "review", ["T1"]),
        st("T3", "review", ["T2"]),
        st("T4", "write", ["T3"]),
    ]
    assert any("T3" in p and "被审查的成果" in p for p in problems(chain, members=4))


def test_duplicate_parallel_subtasks_are_rejected():
    twins = [
        st("T1", "analyze"),
        st("T2", "write", ["T1"], title="写文案", req="写全部角色的文案"),
        st("T3", "write", ["T1"], title="写文案", req="写全部角色的文案"),
        st("T4", "review", ["T2", "T3"]),
    ]
    assert any("T2 和 T3 内容重复" in p for p in problems(twins, members=4))
    split = [
        twins[0],
        st("T2", "write", ["T1"], title="写文案·角色甲", req="只写角色甲的文案"),
        st("T3", "write", ["T1"], title="写文案·角色乙", req="只写角色乙的文案"),
        twins[3],
    ]
    assert problems(split, members=4) == []


def test_normalize_media_sets_it_only_for_generation_kinds():
    mixed = [st("T1", "write", media="image"), st("T2", "generate", ["T1"])]
    out = normalize_media(mixed, RULES, ["image", "video"])
    assert out[0].media is None and out[1].media == "image"
    assert normalize_media(mixed, RULES, [])[1].media is None


# --- 解析 ----------------------------------------------------------------------


def test_parse_decomposition_reads_kind_and_handoffs():
    text = (
        '{"subtasks": ['
        '{"id": "t1", "kind": "Analyze", "title": "看图", "depends_on": []},'
        '{"id": "T2", "kind": "write", "title": "写",'
        ' "depends_on": [{"id": "t1", "gives": "风格规范"}, "T1"]},'
        '{"id": "T3", "kind": "spaceship", "title": "x", "depends_on": ["T2"]}'
        "]}"
    )
    got = parse_decomposition(text, max_subtasks=12, vocabulary=[], kinds=KINDS)
    assert [s.kind for s in got] == ["analyze", "write", None]  # 不认识的类型当作没写
    assert got[1].depends_on == ("T1",) and got[1].handoff("T1") == "风格规范"
    assert got[2].handoff("T2", {"T2": "写"}) == "写"  # 没写交接内容时用上游的标题


def test_subtask_roundtrip_keeps_kind_and_handoffs_and_reads_old_rows():
    s = st("T2", "write", ["T1"], gives=[("T1", "风格规范")])
    assert Subtask.from_dict(s.to_dict()) == s
    old = {
        "id": "T1",
        "title": "旧",
        "requirements": "",
        "acceptance": "",
        "tags": [],
        "depends_on": [],
    }
    assert Subtask.from_dict(old).kind is None and Subtask.from_dict(old).gives == ()


def test_parse_without_kinds_ignores_them_for_the_old_flow():
    got = parse_decomposition(
        '{"subtasks": [{"id": "T1", "kind": "write", "title": "a"}]}', max_subtasks=3, vocabulary=[]
    )
    assert got[0].kind is None


# --- 模板 ----------------------------------------------------------------------


@pytest.mark.parametrize("template", RULES.templates, ids=lambda t: t.name)
@pytest.mark.parametrize("members", COUNTS)
def test_templates_satisfy_the_rules_for_any_table_size(template, members):
    subtasks = build_template(template, members, RULES.max_subtasks)
    assert problems(subtasks, members=members, siblings=False) == []
    assert len(subtasks) >= min(members, RULES.max_subtasks)
    assert [s.id for s in subtasks] == [f"T{i}" for i in range(1, len(subtasks) + 1)]
    layers(subtasks)  # 无环
    assert len({s.title for s in subtasks}) == len(subtasks)  # 平行部分各有各的标题（内容划分）
    assert len({s.requirements for s in subtasks}) == len(subtasks)


@pytest.mark.parametrize("members", (8, 12))
def test_extra_members_get_parallel_parts_not_copies(members):
    template = next(t for t in RULES.templates if t.name == "creation_reference")
    subtasks = build_template(template, members, 12)
    writers = [s for s in subtasks if s.kind == "write"]
    assert len(writers) >= 2
    for s in writers:
        assert "你只负责「" in s.requirements and "不要重复他们的内容" in s.requirements
    # 单人类型永远只有一份
    for kind in ("prompt", "generate", "assemble"):
        assert len([s for s in subtasks if s.kind == kind]) == 1
    # 下游依赖上游的每一个平行部分
    prompt = next(s for s in subtasks if s.kind == "prompt")
    assert {s.id for s in writers} <= set(prompt.depends_on)
    assert dict(prompt.gives)[writers[0].id] == "角色设定与文案"


def test_template_without_enough_splittable_steps_stays_short():
    template = next(t for t in RULES.templates if t.name == "creation_reference")
    fixed = template.model_copy(
        update={"steps": [s.model_copy(update={"split": False}) for s in template.steps]}
    )
    assert len(build_template(fixed, 12, 12)) == len(fixed.steps)


def pick(question, image=False, media=(), tools=()):
    return pick_template(
        RULES, question=question, image_attachment=image, media_available=media, tools=tools
    ).name


def test_template_choice_follows_the_nature_of_the_question():
    assert pick("参考这张图做四格人物卡", True, ["image"], ["python"]) == "creation_reference"
    assert pick("画一张海报", False, ["image"], ["python"]) == "creation"
    assert pick("帮我调研一下固态电池的现状", False, ["image"], ["search", "python"]) == "research"
    assert pick("写一个 Python 脚本处理 CSV 数据", False, [], ["python", "search"]) == "data_code"
    assert pick("写一篇关于春天的作文", False, [], []) == "document"
    # 条件里的能力不可用时落到后面的模板
    assert pick("参考这张图做四格人物卡", True, [], []) == "document"
    assert pick("调研一下固态电池", False, [], []) == "document"
    assert pick("写一个脚本", False, [], []) == "document"
    # 有图片附件但题目不是创作类：不选创作模板
    assert pick("这张图里写了什么？", True, ["image"], []) == "document"


# --- 分配 ----------------------------------------------------------------------


def owners_of(subtasks, plan):
    return {s.id: (plan[s.id],) for s in subtasks}


def test_conflicts_single_owner_and_audit_rules():
    owners = {
        "T1": ("甲",),
        "T2": ("乙",),
        "T3": ("甲",),
        "T4": ("乙",),
        "T5": ("甲",),
        "T6": ("乙", "丙"),
    }
    hard, soft = assignment_conflicts(owners, GOOD, KINDS)
    assert any("T6" in h and "只能一人负责" in h for h in hard)
    assert any("T5" in h and "T3" in h for h in hard)  # 审查者同时是写提示词的人
    assert soft == []  # 审查者(甲)不是执行生成(乙)也不是撰写(乙)的人
    owners["T5"] = ("乙",)  # 审查者 = 撰写者 + 执行生成者
    hard2, soft2 = assignment_conflicts(owners, GOOD, KINDS)
    assert not any("T5" in h for h in hard2)
    assert any("T5" in x and "T4" in x for x in soft2) and any(
        "T5" in x and "T2" in x for x in soft2
    )


def test_ancestors_are_transitive():
    anc = ancestors(GOOD)
    assert anc["T6"] == {"T1", "T2", "T3", "T4", "T5"} and anc["T1"] == set()


@pytest.mark.parametrize("template", RULES.templates, ids=lambda t: t.name)
@pytest.mark.parametrize("members", (2, 3, 4, 5, 8, 12))
@pytest.mark.parametrize("seed", (1, 2))
def test_solved_assignments_respect_the_pipeline_rules(template, members, seed):
    subtasks = build_template(template, members, 12)
    codes = [f"c{i}" for i in range(members)]
    a = solve_assignment(
        Assignment({}, "", (), True), codes, subtasks, {}, {}, KINDS, random.Random(seed)
    )
    load = a.load()
    assert set(load) == set(codes)  # 每人至少一块
    assert max(load.values()) - min(load.values()) <= 1  # 负担相差不超过 1
    assert all(len(o) == 1 for o in a.owners.values())  # 每块恰好一人：没有几个人做同一份内容
    hard, soft = assignment_conflicts(a.owners, subtasks, KINDS)
    assert hard == []  # 写提示词的人永远不审查自己提示词生成的结果
    if members >= 3:
        assert soft == []  # 成员够多时执行生成者 / 撰写者也不审查自己的


def test_two_members_relax_only_the_soft_rules_and_never_the_prompt_rule():
    template = next(t for t in RULES.templates if t.name == "creation_reference")
    subtasks = build_template(template, 2, 12)
    for seed in range(5):
        a = solve_assignment(
            Assignment({}, "", (), True), ["甲", "乙"], subtasks, {}, {}, KINDS, random.Random(seed)
        )
        by_kind = {s.kind: a.owners[s.id][0] for s in subtasks}
        assert by_kind["review"] != by_kind["prompt"]
        hard, _ = assignment_conflicts(a.owners, subtasks, KINDS)
        assert hard == []


def test_solver_keeps_a_valid_start_close_to_the_coordinators_choice():
    template = next(t for t in RULES.templates if t.name == "document")
    subtasks = build_template(template, 4, 12)
    start = Assignment({"T1": ("a",), "T2": ("b",), "T3": ("c",), "T4": ("d",)}, "理由", ())
    a = solve_assignment(start, ["a", "b", "c", "d"], subtasks, {}, {}, KINDS, random.Random(0))
    assert a.owners == start.owners and a.rationale == "理由"


def test_solver_notes_what_it_changed_and_extra_members_join_parallel_kinds():
    template = next(t for t in RULES.templates if t.name == "document")
    subtasks = build_template(template, 4, 12)
    codes = [f"c{i}" for i in range(6)]  # 成员比子任务多（超过 max_subtasks 的大桌）
    a = solve_assignment(
        Assignment({s.id: ("c0",) for s in subtasks}, "", ()),
        codes,
        subtasks,
        {},
        {},
        KINDS,
        random.Random(0),
    )
    assert set(a.load()) == set(codes)
    assert any("调整为" in n for n in a.repaired)
    assert any("子任务比成员少" in n for n in a.repaired)
    multi = [s for s in subtasks if len(a.owners[s.id]) > 1]
    assert multi and all(KINDS[s.kind].parallel for s in multi)  # 只加入可并行的类型


def test_kind_tags_outweigh_volunteering():
    from roundtable.core.steps.collab_schemas import Volunteer

    subtasks = [st("T1", "analyze"), st("T2", "code", ["T1"]), st("T3", "review", ["T2"])]
    want_code = Volunteer("擅长", {"T1": ("can", ""), "T2": ("can", ""), "T3": ("can", "")})
    tags = {"a": ["vision"], "b": ["code"], "c": ["writing"]}
    a = solve_assignment(
        Assignment({}, "", ()),
        ["a", "b", "c"],
        subtasks,
        {"a": want_code, "b": want_code, "c": want_code},
        tags,
        KINDS,
        random.Random(3),
    )
    assert a.owners["T2"] == ("b",)  # 带 code 标签的人写代码
