"""协同（R8）：合并时，已被下游接手的上游成果只给统筹看开头摘录（单桌，Fake）。"""

from __future__ import annotations

from roundtable.core.steps.collab import excerpt, works_block
from roundtable.core.steps.schemas import EffortRecord

from .conftest import REASONING, Table, assign_reply, user_text
from .test_collab_clarify import without_clarify
from .test_collab_pipeline import THREE, run, script

LONG = "上游分析的详细内容。" * 120  # 约 1200 字，超过默认摘录长度


def long_upstream(t: Table, sids=("T1",)):
    """sids 里的子任务交出很长的成果；其他按默认。"""
    base = t.fake._default

    def reply(model, messages):
        text = user_text(messages)
        if "你负责下面 <your_subtask>" in messages[0].content and any(
            f'<your_subtask id="{s}"' in text for s in sids
        ):
            return f"{LONG}\n{REASONING}"
        return base(model, messages)

    t.fake._default = reply


async def setup(sids=("T1",)) -> Table:
    t = Table(9, pipeline=True)
    script(t, THREE, assign=assign_reply)
    long_upstream(t, sids)
    without_clarify(t)
    await run(t, "decompose", "volunteer", "assign", "work")
    return t


def merge_text(t: Table) -> str:
    (call,) = [x for x in t.fake.calls if "合并成一份完整成果" in x.messages[0].content]
    return user_text(call.messages)


def work_tag(text: str, sid: str) -> str:
    start = text.index(f'subtask="{sid}"')
    return text[start : text.index("</work>", start)]


async def test_merge_gets_only_an_excerpt_of_consumed_upstream_work():
    t = await setup()
    await run(t, "merge")
    text = merge_text(t)
    t1 = work_tag(text, "T1")
    assert 'excerpt="完整内容已交给 T2 继续使用"' in t1
    assert len(t1) < 800 and t1.rstrip().endswith("……")
    # 下游和终点子任务给全文；合并用的是 v5（说明摘录的含义）
    assert "excerpt=" not in work_tag(text, "T2") and "excerpt=" not in work_tag(text, "T3")
    assert t.ctx.prompt_version("merge") == "v5"
    assert t.ctx.state.collab.merge is not None


async def test_flagged_downstream_keeps_the_upstream_in_full():
    t = await setup()
    c = t.ctx.state.collab
    items = {v: k for k, v in c.items().items()}
    o2 = c.assignment.owners["T2"][0]
    t.ctx.state.effort[("work", o2, items[("T2", o2)])] = EffortRecord(
        "work", o2, "lazy", ("过短",), ("过短",), True, items[("T2", o2)]
    )
    assert "excerpt=" not in works_block(t.ctx, trim=True)


async def test_long_downstream_and_acceptance_are_untouched():
    t = await setup(("T1", "T2", "T3"))
    block = works_block(t.ctx, trim=True)
    assert 'excerpt="完整内容已交给 T2' in work_tag(block, "T1")
    assert 'excerpt="完整内容已交给 T3' in work_tag(block, "T2")
    assert "excerpt=" not in work_tag(block, "T3")  # 终点
    assert "excerpt=" not in works_block(t.ctx)  # 验收等其他用途给全文


async def test_disabled_trim_sends_full_text():
    t = await setup()
    rt = t.ctx.config.roundtable
    rule = rt.collab.merge_trim.model_copy(update={"enabled": False})
    collab = rt.collab.model_copy(update={"merge_trim": rule})
    t.ctx.config = t.ctx.config.model_copy(
        update={"roundtable": rt.model_copy(update={"collab": collab})}
    )
    assert "excerpt=" not in works_block(t.ctx, trim=True)


def test_excerpt_cuts_at_a_line_break_when_possible():
    text = "第一行内容\n" * 50
    out = excerpt(text, 100)
    assert len(out) <= 102 and out.endswith("\n……") and "第一行内容" in out
    assert excerpt("x" * 300, 100) == "x" * 100 + "\n……"
