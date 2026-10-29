"""从各种模式的记录里取出最终成果；导出不调用模型。"""

from __future__ import annotations

import json

from roundtable.core.export.deliverable import deliverable, shift_headings, title_of
from roundtable.core.storage.repository import OutputView, SessionView


def view(outputs: list[tuple[int, str, dict | str]], question: str = "怎么复习？") -> SessionView:
    outs = tuple(
        OutputView(t, "s", kind, "甲", c if isinstance(c, str) else json.dumps(c))
        for t, kind, c in outputs
    )
    return SessionView(
        id="s1", created_at="", question=question, status="completed", tier="budget",
        workflow="discussion", anonymous=True, revealed=False, routing=None, seats=(),
        outputs=outs, calls=(), checkpoints=(), cost_usd=0.0,
    )  # fmt: skip


def test_synthesis_final_answer_with_open_questions():
    s = {
        "final_answer": "# 方案\n\n先做 A。",
        "consensus": ["都同意 A"],
        "disagreements": [
            {"point": "B 要不要做", "positions": [{"members": ["甲"], "view": "要"}]}
        ],
        "open_questions": ["时间"],
        "confidence": "high",
    }
    doc = deliverable(view([(0, "answer", "初稿"), (0, "synthesis", s)]))
    assert doc.title == "怎么复习？"
    assert "## 方案" in doc.markdown and "先做 A。" in doc.markdown
    assert "## 共识\n\n- 都同意 A" in doc.markdown and "- B 要不要做" in doc.markdown
    assert "## 仍需核实\n\n- 时间" in doc.markdown
    assert "初稿" not in doc.markdown and "要" not in doc.markdown.split("B 要不要做")[1]


def test_collab_merge_and_office_versions():
    merge = {"result": "合并的成果", "gaps": ["缺图"], "open_questions": [], "confidence": "mid"}
    doc = deliverable(view([(0, "subtasks", {}), (0, "merge", merge)]))
    assert "合并的成果" in doc.markdown and "## 缺失\n\n- 缺图" in doc.markdown
    revised = deliverable(view([(0, "merge", merge), (0, "office_revised", {"text": "修改后"})]))
    assert "修改后" in revised.markdown and "合并的成果" not in revised.markdown
    office = view([(0, "office_work", {"text": "初稿"}), (0, "office_fix", {"text": "改稿"})])
    assert "改稿" in deliverable(office).markdown
    meeting = view(
        [(0, "office_speech", {"text": "发言"}), (0, "office_decision", {"text": "结论"})]
    )
    assert "结论" in deliverable(meeting).markdown and "发言" not in deliverable(meeting).markdown


def test_brainstorm_solo_and_revisions():
    mm = {"markdown": "# 导图\n- 想法", "document": "## 点子榜\n\n1. 好点子\n# 导图\n- 想法"}
    assert "好点子" in deliverable(view([(0, "mindmap", mm)])).markdown
    assert "唯一答案" in deliverable(view([(0, "answer", "唯一答案")])).markdown
    two = deliverable(
        view(
            [(0, "revision", {"answer": "一", "skipped": False}), (0, "revision", {"answer": "二"})]
        )
    )
    assert "## 答案 1" in two.markdown and "## 答案 2" in two.markdown


def test_nothing_to_export_and_several_tables():
    assert deliverable(view([(0, "answer", "  ")])) is None
    assert deliverable(view([])) is None
    doc = deliverable(view([(0, "answer", "## 小节\n甲桌"), (1, "answer", "乙桌")]))
    assert "## 第 1 张桌子" in doc.markdown and "### 小节" in doc.markdown
    assert "## 第 2 张桌子" in doc.markdown


def test_long_question_is_quoted_and_headings_are_shifted():
    q = "第一行题目\n第二行补充"
    doc = deliverable(view([(0, "answer", "### 深\n#### 更深")], question=q))
    assert doc.markdown.startswith("# 第一行题目\n\n> 第一行题目\n> 第二行补充")
    assert "\n## 深\n" in doc.markdown and "\n### 更深" in doc.markdown
    assert title_of("x" * 50).endswith("…") and len(title_of("x" * 50)) == 40
    assert shift_headings("```\n# 代码\n```\n### 标题", 2) == "```\n# 代码\n```\n## 标题"


def test_question_can_be_left_out_and_process_appended():
    outs = [
        (0, "answer", "甲的初稿"),
        (0, "review", {"x": 1}),
        (0, "revision", {"answer": "甲改过", "skipped": False}),
        (0, "synthesis", {"final_answer": "定稿", "consensus": [], "disagreements": []}),
    ]
    q = "第一行\n第二行"
    plain = deliverable(view(outs, question=q), question=False)
    assert "> 第一行" not in plain.markdown and "附：过程" not in plain.markdown
    full = deliverable(view(outs, question=q), process=True).markdown
    appendix = full.split("## 附：过程", 1)[1]
    assert "### 甲 初稿\n\n甲的初稿" in appendix and "### 甲 修订后\n\n甲改过" in appendix
    assert "定稿" in full.split("## 附：过程")[0] and "x" not in appendix
