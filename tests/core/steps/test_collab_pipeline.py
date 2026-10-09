"""协同流水线（阶段 21）：拆分校验与重拆、模板兜底、按规则分配、交接与上游文件（单桌，Fake）。"""

from __future__ import annotations

import json
import re

from roundtable.core.allocation import IdentityScrubber
from roundtable.core.attachments import FileStore
from roundtable.core.steps import get_step, restore_state
from roundtable.core.steps.collab import (
    dependencies_block,
    stage_inputs,
    subtask_block,
    upstream_files,
)
from roundtable.core.steps.pipeline import assignment_conflicts
from roundtable.core.tools import ToolBox

from .conftest import MEMBERS, Table, assign_reply, default_reply, user_text

BIG = {"甲": "b1", "乙": "b2", "丙": "b3", "丁": "f2", "戊": "f3"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def sub(sid, kind, deps=(), title=None, req=None, media=None):
    return {
        "id": sid,
        "kind": kind,
        "title": title or f"{kind} 段",
        "requirements": req or f"完成 {sid} 这一段（{kind}）的具体工作内容",
        "acceptance": "做得具体、可检查",
        "tags": [],
        "depends_on": [{"id": d, "gives": f"{d} 的产出"} for d in deps],
        "media": media,
    }


THREE = [sub("T1", "analyze"), sub("T2", "write", ["T1"]), sub("T3", "review", ["T2"])]
FOUR = [
    sub("T1", "analyze"),
    sub("T2", "write", ["T1"]),
    sub("T3", "prompt", ["T1", "T2"]),
    sub("T4", "review", ["T3", "T2"]),
]
WHOLE = [sub("T1", "write", title="完成整道题", req="独立完成题目的全部要求")]


def split_json(subtasks):
    return json.dumps({"subtasks": subtasks, "notes": "流水线"}, ensure_ascii=False)


def script(table: Table, first, retry=None, assign=None):
    """统筹的回复：第一次拆分、重拆（提示词里有"没有通过代码检查"）、分配；其他按默认。"""

    def reply(model, messages):
        system = messages[0].content
        if "没有通过代码检查" in system:
            return split_json(retry if retry is not None else first)
        if "把任务拆成子任务" in system and "学习小组的统筹" in system:
            return split_json(first)
        if assign is not None and "负责把子任务分配给组员" in system:
            return assign(model, messages)
        return default_reply(model, messages)

    table.fake._default = reply


async def run(table: Table, *steps: str):
    return [await get_step(s).run(table.ctx) for s in steps]


def coordinator_calls(table: Table, marker: str):
    return [c for c in table.fake.calls if marker in c.messages[0].content]


# --- 拆分 ----------------------------------------------------------------------


async def test_valid_pipeline_is_accepted_as_is():
    t = Table(1, pipeline=True)
    script(t, THREE)
    (res,) = await run(t, "decompose")
    c = t.ctx.state.collab
    assert c.pipeline and c.pipeline_info["source"] == "coordinator" and res.calls == 1
    assert [s.kind for s in c.subtasks] == ["analyze", "write", "review"]
    assert c.subtasks[1].handoff("T1") == "T1 的产出"
    assert "类型：撰写" in subtask_block(c.subtasks[1], ctx=t.ctx)
    assert not res.degraded and not res.notes
    # 恢复后拆分、类型和交接内容都在
    restored = restore_state(t.repo, t.session, 0).collab
    assert restored.pipeline and restored.subtasks == c.subtasks


async def test_whole_task_split_is_sent_back_once_with_the_problems():
    t = Table(2, pipeline=True)
    script(t, WHOLE, retry=THREE)
    (res,) = await run(t, "decompose")
    c = t.ctx.state.collab
    assert res.calls == 2 and c.pipeline_info["source"] == "retry"
    assert "按指出的问题重拆" in res.notes[0] and not res.degraded
    retry = coordinator_calls(t, "没有通过代码检查")
    assert len(retry) == 1
    sent = user_text(retry[0].messages)
    assert "完成整道题" in sent  # 带上上一次的拆分
    assert "整道题当作一个子任务" in sent and "少于成员数 3" in sent  # 具体问题
    assert [s.id for s in c.subtasks] == ["T1", "T2", "T3"]


async def test_still_bad_after_the_retry_falls_back_to_a_template():
    t = Table(3, pipeline=True)
    script(t, WHOLE)
    (res,) = await run(t, "decompose")
    c = t.ctx.state.collab
    assert res.calls == 2 and res.degraded == ("coordinator",)
    assert c.pipeline_info["source"] == "template" and c.pipeline_info["template"] == "document"
    assert "模板「文档」" in res.notes[0]
    assert len(c.subtasks) >= 3 and all(s.kind for s in c.subtasks)
    assert len(c.subtasks) > 1  # 不再是"整道题一个子任务"
    saved = [o for o in t.repo.outputs(t.session, table_no=0) if o["kind"] == "subtasks"]
    assert json.loads(saved[0]["content"])["info"]["template"] == "document"


async def test_template_fallback_gives_every_member_distinct_work():
    t = Table(4, members=BIG, pipeline=True)
    script(t, WHOLE)
    await run(t, "decompose", "volunteer", "assign")
    c = t.ctx.state.collab
    assert len(c.subtasks) >= len(BIG)
    owners = c.assignment.owners
    assert all(len(v) == 1 for v in owners.values())  # 没有几个人做同一份内容
    assert set(c.assignment.load()) == set(BIG)
    assert len({s.title for s in c.subtasks}) == len(c.subtasks)


async def test_too_few_subtasks_for_the_table_is_sent_back():
    t = Table(5, members=BIG, pipeline=True)
    script(t, THREE, retry=THREE)
    (res,) = await run(t, "decompose")
    assert res.calls == 2 and t.ctx.state.collab.pipeline_info["source"] == "template"
    problems = user_text(coordinator_calls(t, "没有通过代码检查")[0].messages)
    assert "少于成员数 5" in problems and "按内容拆成平行子任务" in problems


async def test_old_flow_is_unchanged_when_the_pipeline_is_off():
    t = Table(6)  # pipeline=False
    script(t, WHOLE)
    await run(t, "decompose")
    c = t.ctx.state.collab
    assert not c.pipeline and [s.id for s in c.subtasks] == ["T1"] and c.subtasks[0].kind is None
    assert len(coordinator_calls(t, "没有通过代码检查")) == 0


# --- 分配 ----------------------------------------------------------------------


def bad_assign(model, messages):
    """审查者 T4 和写提示词的 T3 同一个人，T3 还有两个人负责。"""
    return json.dumps(
        {
            "assignments": [
                {"subtask": "T1", "members": ["组员甲"]},
                {"subtask": "T2", "members": ["组员乙"]},
                {"subtask": "T3", "members": ["组员丙", "组员甲"]},
                {"subtask": "T4", "members": ["组员丙"]},
            ],
            "rationale": "随便分",
        },
        ensure_ascii=False,
    )


async def test_assignment_violating_the_rules_is_retried_then_fixed_by_code():
    t = Table(7, pipeline=True)
    script(t, FOUR, assign=bad_assign)
    (_, _, res) = await run(t, "decompose", "volunteer", "assign")
    c = t.ctx.state.collab
    assert res.calls == 2 and res.notes and "已由代码补齐" in res.notes[0]
    owners = c.assignment.owners
    assert all(len(v) == 1 for v in owners.values())
    prompt_owner, review_owner = owners["T3"][0], owners["T4"][0]
    assert prompt_owner != review_owner  # 写提示词的人不审查自己提示词生成的结果
    hard, _ = assignment_conflicts(owners, c.subtasks, t.config.roundtable.collab.kinds)
    assert hard == [] and set(c.assignment.load()) == set(MEMBERS)
    assert c.assignment.repaired  # 调整都写进了记录
    sent = user_text(coordinator_calls(t, "负责把子任务分配给组员")[0].messages)
    assert "类型：写提示词" in sent and "类型：审查" in sent


async def test_a_valid_assignment_from_the_coordinator_is_kept():
    t = Table(8, pipeline=True)
    ok = lambda m, msgs: json.dumps(  # noqa: E731
        {
            "assignments": [
                {"subtask": "T1", "members": ["组员甲"]},
                {"subtask": "T2", "members": ["组员乙"]},
                {"subtask": "T3", "members": ["组员丙"]},
            ],
            "rationale": "各取所长",
        },
        ensure_ascii=False,
    )
    script(t, THREE, assign=ok)
    (_, _, res) = await run(t, "decompose", "volunteer", "assign")
    assert res.calls == 1 and not res.notes
    assert t.ctx.state.collab.assignment.owners == {"T1": ("甲",), "T2": ("乙",), "T3": ("丙",)}


# --- 完成：交接、类型提示词、上游文件 -----------------------------------------------


async def test_work_follows_the_chain_with_handoffs_and_kind_prompts():
    t = Table(9, pipeline=True)
    t.ctx.scrubber = IdentityScrubber.from_config(t.config.models)  # 遮蔽测试池里的模型 id
    script(t, THREE, assign=assign_reply)
    await run(t, "decompose", "volunteer", "assign", "work")
    c = t.ctx.state.collab
    owners = {sid: o[0] for sid, o in c.assignment.owners.items()}
    work = [x for x in t.fake.calls if "你负责下面 <your_subtask>" in x.messages[0].content]
    assert [x.messages[0].content.count("类型：") for x in work] == [1, 1, 1]
    kinds_seen = [x.messages[0].content.split("（类型：")[1].split("）")[0] for x in work]
    assert kinds_seen == ["分析", "撰写", "审查"]  # 按依赖分批：上游先于下游
    t2_prompt = user_text(work[1].messages)
    assert '<dependency subtask="T1"' in t2_prompt and 'gives="T1 的产出"' in t2_prompt
    assert f'from="组员{owners["T1"]}"' in t2_prompt and 'kind="分析"' in t2_prompt
    # 交接记录：谁把什么交给了谁
    assert [(h["from_subtask"], h["to_subtask"]) for h in c.handoffs] == [
        ("T1", "T2"),
        ("T2", "T3"),
    ]
    assert c.handoffs[0]["from_code"] == owners["T1"] and c.handoffs[0]["to_code"] == owners["T2"]
    assert c.handoffs[0]["gives"] == "T1 的产出"
    events = [e for e in t.events if e.type == "handoff"]
    assert len(events) == 2 and events[0].data["from_code"] == owners["T1"]
    assert set(events[0].data) == {"from_code", "from_subtask", "to_subtask", "gives"}
    # 恢复后交接记录不变，重跑不会重复记录
    restored = restore_state(t.repo, t.session, 0).collab
    assert restored.handoffs == c.handoffs
    await run(t, "work")
    assert len(t.ctx.state.collab.handoffs) == 2
    # 发给模型的内容没有模型 id
    for call in work:
        text = "\n".join(m.content for m in call.messages)
        for m in t.config.models.models:
            if m.id != call.model:
                assert not re.search(rf"(?<![0-9A-Za-z]){re.escape(m.id)}(?![0-9A-Za-z])", text)


async def test_every_kind_has_its_own_work_prompt():
    t = Table(10, pipeline=True)
    for name, rule in t.config.roundtable.collab.kinds.items():
        template = t.ctx.prompts.get(rule.prompt, t.ctx.prompt_version(rule.prompt))
        wanted = {"code", "question", "plan", "subtask", "dependencies"}
        assert wanted <= set(template.variables), name
        assert template.render(**{v: "x" for v in template.variables}), name


def make_toolbox(t: Table) -> ToolBox:
    box = ToolBox(
        session_id=t.session,
        table_no=0,
        config=t.config,
        router=t.router,
        prompts=t.ctx.prompts,
        repo=t.repo,
        store=FileStore(None),
        scrubber=t.ctx.scrubber,
        sandbox=None,
    )
    t.ctx.toolbox = box
    t.ctx.file_store = box.store
    return box


async def staged_table():
    """T1 的作者生成了一张图和一个文本文件，T2 的负责人要用。"""
    t = Table(11, pipeline=True)
    box = make_toolbox(t)
    script(t, THREE)
    await run(t, "decompose")
    c = t.ctx.state.collab
    from roundtable.core.steps.collab_schemas import Assignment

    c.assignment = Assignment({"T1": ("甲",), "T2": ("乙",), "T3": ("丙",)}, "")
    c.works[("T1", "甲")] = "风格规范：扁平插画"
    ws = box.workspace("甲")
    ws.write("style.png", PNG)
    ws.write("notes.md", b"# notes")
    ws.collect(step="work", tool_call_id=None, step_count=0)
    return t, box


async def test_upstream_files_are_copied_into_the_next_owners_input_directory():
    t, box = await staged_table()
    c = t.ctx.state.collab
    rows = upstream_files(t.ctx, c.subtasks[1])
    assert {r[2]["path"] for r in rows} == {"style.png", "notes.md"} and {r[0] for r in rows} == {
        "T1"
    }
    names = stage_inputs(t.ctx, c.subtasks[1], "乙")
    ws = box.workspace("乙")
    assert sorted(names.values()) == ["notes.md", "style.png"]
    assert (ws.root / "in" / "style.png").read_bytes() == PNG
    assert stage_inputs(t.ctx, c.subtasks[1], "乙") == names  # 重复调用不重复写入
    # T3 看不到不在它上游的文件之外的东西；T1 没有上游文件
    assert stage_inputs(t.ctx, c.subtasks[0], "甲") == {}
    block = dependencies_block(t.ctx, c.subtasks[1], "乙", names)
    assert 'path="in/style.png"' in block and 'type="image"' in block
    assert "# notes" in block  # 文本文件附上内容
    assert "out/" not in block  # 对接收者来说路径是 in/


async def test_same_name_files_from_two_upstream_owners_get_a_prefix():
    t, box = await staged_table()
    c = t.ctx.state.collab
    from roundtable.core.steps.collab_schemas import Assignment

    # T3 依赖 T2；让 T2 的作者也生成同名文件
    c.assignment = Assignment({"T1": ("甲",), "T2": ("乙",), "T3": ("丙",)}, "")
    c.works[("T2", "乙")] = "设定"
    ws = box.workspace("乙")
    ws.write("style.png", PNG + b"2")
    ws.collect(step="work", tool_call_id=None, step_count=0)
    names = stage_inputs(t.ctx, c.subtasks[2], "丙")
    assert len(set(names.values())) == len(names) == 3  # 甲的两个 + 乙的一个，没有互相覆盖
    assert any(n.startswith("T2-") for n in names.values())


async def test_vision_members_receive_upstream_images_others_only_names():
    t, box = await staged_table()
    c = t.ctx.state.collab
    names = stage_inputs(t.ctx, c.subtasks[1], "乙")
    block = dependencies_block(t.ctx, c.subtasks[1], "乙", names)
    from roundtable.core.prompts import RenderedPrompt
    from roundtable.core.providers import Message

    prompt = RenderedPrompt("x", "v1", "h", (Message("system", "s"), Message("user", block)))
    vision = t.ctx.messages_for(prompt, "b2")  # b2 带 vision 标签
    plain = t.ctx.messages_for(prompt, "b1")
    assert [m.data for m in vision[1].media] == [PNG] and "随附图片 1" in vision[1].content
    assert plain[1].media == () and "随附图片" not in plain[1].content
    assert 'path="in/style.png"' in plain[1].content  # 非 vision 成员至少看到文件名


async def test_big_table_owners_are_exactly_one_per_block_unlike_the_old_flow():
    t = Table(12, members=BIG, pipeline=True)
    five = [
        sub("T1", "analyze"),
        sub("T2", "write", ["T1"], title="写角色甲", req="只写角色甲"),
        sub("T3", "write", ["T1"], title="写角色乙", req="只写角色乙"),
        sub("T4", "review", ["T2", "T3"]),
        sub("T5", "assemble", ["T2", "T3", "T4"]),
    ]
    script(t, five)
    await run(t, "decompose", "volunteer", "assign")
    c = t.ctx.state.collab
    assert c.pipeline_info["source"] == "coordinator"
    assert all(len(v) == 1 for v in c.assignment.owners.values())
    assert len({v[0] for v in c.assignment.owners.values()}) == 5
