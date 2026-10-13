"""对比实验（R14）：题库格式、判分器、运行器（含花费上限）、汇总。"""

from __future__ import annotations

import pytest

from roundtable.core.bench import (
    BenchError,
    BenchMode,
    BenchQuestion,
    BenchRunner,
    Judge,
    cost_per_correct,
    judge,
    last_number,
    load_questions,
    parse_modes,
    summarize,
)
from roundtable.core.runtime import PROJECT_ROOT

from .orchestrator.conftest import MEDIUM, Env


def q(answer=-2, **kw) -> BenchQuestion:
    return BenchQuestion(
        id=kw.pop("id", "t-1"),
        category=kw.pop("category", "math"),
        text=kw.pop("text", MEDIUM),
        judge=Judge(type="number", answer=answer, **kw),
    )


# --- 题库 ------------------------------------------------------------------------


def test_shipped_question_bank_is_valid_and_complete():
    questions = load_questions(PROJECT_ROOT / "benchmarks")
    assert len(questions) >= 10
    assert len({x.id for x in questions}) == len(questions)
    for x in questions:
        # 标准答案不能出现在发给模型的题目文字里
        spec = x.judge
        secrets = [str(spec.answer)] if spec.answer is not None else []
        assert all(s not in x.text for s in secrets if len(s) > 1), x.id


def test_bad_bank_reports_file_and_field(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("questions:\n  - {id: a, category: x, text: t, judge: {type: number}}\n")
    with pytest.raises(BenchError, match=r"(?s)bad\.yaml.*questions\.0\.judge.*number"):
        load_questions(f)
    f.write_text(
        "questions:\n  - {id: a, category: x, text: t, judge: {type: exact, answer: z}, extra: 1}\n"
    )
    with pytest.raises(BenchError, match="extra"):
        load_questions(f)


def test_duplicate_ids_rejected(tmp_path):
    row = "  - {id: a, category: x, text: t, judge: {type: exact, answer: z}}\n"
    (tmp_path / "one.yaml").write_text("questions:\n" + row + row)
    with pytest.raises(BenchError, match="重复"):
        load_questions(tmp_path)


# --- 判分 ------------------------------------------------------------------------


def test_number_judge_uses_last_number_with_tolerance():
    spec = Judge(type="number", answer=0.3, tol=0.005)
    assert judge("概率是 3/10，即 0.30", spec)
    assert not judge("答案是 0.4", spec)
    assert last_number("共有 1,234 个") == 1234
    assert not judge("", spec) and not judge(None, spec) and not judge("没有数字", spec)


def test_exact_judge_ignores_whitespace_and_punctuation():
    spec = Judge(type="exact", answer="乙")
    assert judge("乙。", spec) and judge(" 乙 ", spec)
    assert not judge("甲", spec)


def test_keywords_judge_all_any_and_length():
    spec = Judge(type="keywords", all=["马氏体"], any=["冷却", "淬"], max_chars=10)
    assert judge("马氏体冷却", spec)
    assert not judge("冷却", spec)  # 缺必含词
    assert not judge("马氏体，需要很快地冷却才能得到", spec)  # 超长
    assert not judge("马氏体", spec)  # any 一个都没有


# --- 运行 ------------------------------------------------------------------------


async def run(env, questions, modes, **kw):
    runner = BenchRunner(env.orc, env.rt.repo)
    return await runner.run(questions, modes, batch="b1", seed=5, **kw)


async def test_runs_each_question_in_each_mode_and_stores_results():
    env = Env(confirm_threshold_usd=100.0)
    modes = parse_modes("solo:budget,discussion:budget")
    results = await run(env, [q(), q(answer=99, id="t-2")], modes)
    assert [(r.question_id, r.workflow) for r in results] == [
        ("t-1", "solo"),
        ("t-1", "discussion"),
        ("t-2", "solo"),
        ("t-2", "discussion"),
    ]
    assert all(r.status == "completed" for r in results)
    assert [r.correct for r in results] == [True, True, False, False]
    solo, discussion = results[0], results[1]
    assert 0 < solo.cost_usd < discussion.cost_usd  # 个人作答更便宜
    assert env.rt.repo.bench_runs("b1")[0]["session_id"] == solo.session_id
    assert env.rt.repo.bench_batches()[0]["runs"] == 4


async def test_models_never_see_the_answer_key():
    env = Env(confirm_threshold_usd=100.0)
    await run(env, [q(answer=424242)], parse_modes("solo:budget"))
    sent = " ".join(m.content for call in env.fake.calls for m in call.messages)
    assert "424242" not in sent


async def test_stops_at_max_usd_and_keeps_finished_results():
    env = Env(confirm_threshold_usd=100.0)
    many = [q(id=f"t-{i}") for i in range(5)]
    results = await run(env, many, parse_modes("discussion:budget"), max_usd=1e-9)
    assert len(results) == 1  # 第一次跑完就超了，后面不再开始
    assert len(env.rt.repo.bench_runs("b1")) == 1
    with pytest.raises(BenchError):
        await run(env, many, parse_modes("solo:budget"), max_usd=0)


async def test_unknown_mode_is_recorded_as_error_not_crash():
    env = Env(confirm_threshold_usd=100.0)
    results = await run(env, [q()], [BenchMode("solo", "nope")])
    assert results[0].status != "completed" and results[0].correct is None


# --- 汇总 ------------------------------------------------------------------------


def test_summarize_ignores_unfinished_and_computes_cost_per_correct():
    def row(workflow, status, correct, cost, seconds):
        return dict(
            workflow=workflow, tier="budget", category="math", status=status,
            correct=correct, cost_usd=cost, seconds=seconds,
        )  # fmt: skip

    rows = [
        row("solo", "completed", 1, 0.01, 2),
        row("solo", "completed", 0, 0.03, 4),
        row("solo", "error", None, 0.0, 1),
        row("collab", "completed", 0, 0.5, 9),
    ]
    summary = summarize(rows)
    solo = next(s for s in summary if s["workflow"] == "solo")
    assert solo["questions"] == 2 and solo["correct"] == 1 and solo["accuracy"] == 0.5
    assert solo["avg_cost_usd"] == pytest.approx(0.02) and solo["avg_seconds"] == 3
    per = cost_per_correct(rows)
    assert per[("solo", "budget")] == pytest.approx(0.04)
    assert per[("collab", "budget")] is None


def test_parse_modes_defaults_tier():
    assert parse_modes("solo, collab:flagship") == [
        BenchMode("solo", "budget"),
        BenchMode("collab", "flagship"),
    ]
    with pytest.raises(BenchError):
        parse_modes(" , ")
