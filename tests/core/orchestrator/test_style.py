"""阶段 22：风格规范提取、参考图传给画图模型、对照风格清单校验并退回重画（讨论 / 协同）。"""

from __future__ import annotations

import json
import re

from roundtable.core.attachments import ingest
from roundtable.core.routing import Question, UserChoice
from roundtable.core.service import RoundtableService

from ..attachments.samples import PNG
from ..routing.conftest import REPO
from ..steps.conftest import user_text
from .conftest import MEDIUM, Env
from .test_media import make_env, script

CHECKLIST = [f"清单第 {i} 条：具体可检查的风格要求" for i in range(1, 7)]
EXTRACTION = "## 画风\n扁平插画\n## 配色\n主色 #F2C94C\n## 版式\n四格排列，3px 描边"
EXTRACT_MARK = "独立、仔细地观察风格参考图"
MERGE_MARK = "合并成一份**风格规范**"
REVIEW_MARK = "检查生成的成果"
GATE_MARK = "选择媒体"

# 两位带 vision 标签的组员（b2、f1），b3 当统筹
MEMBERS = ("b1", "b2", "f1", "b3")
COORDINATOR = "b3"


def merge_reply(model, messages):
    return json.dumps(
        {"spec": "## 画风\n扁平插画（合并后）", "checklist": CHECKLIST}, ensure_ascii=False
    )


def review_reply(passes: list[bool]):
    """按顺序给出每次判定的结果；用完后一直通过。"""
    calls = iter(passes)

    def reply(model, messages):
        ok = next(calls, True)
        return json.dumps(
            {
                "satisfied": ok,
                "problems": [] if ok else [{"what": "配色偏冷", "fix": "强调暖黄 #F2C94C"}],
                "checks": [
                    {"item": CHECKLIST[0], "ok": ok, "note": "看到的配色" if ok else "配色偏冷"},
                    {"item": CHECKLIST[1], "ok": True, "note": "格数正确"},
                ],
                "checked": "逐条对照了风格清单与参考图",
            },
            ensure_ascii=False,
        )

    return reply


def upload(env: Env, name="ref.png", data=PNG):
    rt = env.rt
    return ingest(name, data, config=rt.config, router=rt.router, repo=rt.repo, store=rt.files)


def with_style_replies(env: Env, review=None) -> None:
    script(
        env,
        **{
            EXTRACT_MARK: lambda m, msgs: EXTRACTION,
            MERGE_MARK: merge_reply,
            REVIEW_MARK: review or review_reply([]),
        },
    )


def outputs(env: Env, sid: str, kind: str):
    return [(o["code"], json.loads(o["content"])) for o in env.rt.repo.outputs(sid, kind=kind)]


def discussion(media="image", tier=None):
    return UserChoice("custom", MEMBERS, COORDINATOR, "discussion", media, tier)


def collab(tier=None):
    return UserChoice("custom", MEMBERS, COORDINATOR, "collab", None, tier)


def table_pipeline(env: Env, sid: str) -> list[str]:
    return list(env.rt.repo.tables(sid)[0]["pipeline"])


# --- 触发条件 ------------------------------------------------------------------


async def test_style_step_runs_first_when_images_are_references_and_drawing_is_needed():
    env = make_env()
    with_style_replies(env)
    ref = upload(env)
    r = await env.orc.start(Question(MEDIUM), discussion(), seed=1, attachments=[ref.id])
    assert r.status == "completed"
    assert table_pipeline(env, r.session_id)[0] == "style"
    spec = outputs(env, r.session_id, "style_spec")
    assert len(spec) == 1
    data = spec[0][1]
    assert data["checklist"] == CHECKLIST and "合并后" in data["spec"]
    assert not data["text_only"] and data["extractors"]
    # 看原图的是带 vision 标签的组员；统筹合并；两者都不带风格规范本身
    extract = [c for c in env.fake.calls if EXTRACT_MARK in c.messages[0].content]
    assert {c.model for c in extract} <= {"b2", "f1"} and extract
    assert all(any(m.data == PNG for m in c.messages[1].media) for c in extract)
    merge = [c for c in env.fake.calls if MERGE_MARK in c.messages[0].content]
    assert [c.model for c in merge] == [COORDINATOR]
    assert "<style_spec>" not in merge[0].messages[1].content
    assert "风格清单" not in extract[0].messages[1].content


async def test_style_not_triggered_without_the_conditions():
    # 1 没有图片附件
    env = make_env()
    with_style_replies(env)
    r = await env.orc.start(Question(MEDIUM), discussion(), seed=1)
    assert "style" not in table_pipeline(env, r.session_id)
    # 2 有图片但不画图（讨论模式没选图片输出）
    env = make_env()
    with_style_replies(env)
    ref = upload(env)
    r = await env.orc.start(Question(MEDIUM), discussion(media=None), seed=1, attachments=[ref.id])
    assert "style" not in table_pipeline(env, r.session_id)
    # 3 图片取消了"风格参考"
    env = make_env()
    with_style_replies(env)
    ref = upload(env)
    r = await env.orc.start(
        Question(MEDIUM), discussion(), seed=1, attachments=[ref.id], style_refs=[]
    )
    assert "style" not in table_pipeline(env, r.session_id)
    assert env.rt.repo.session_attachments(r.session_id)[0]["style_ref"] == 0
    assert not [c for c in env.fake.calls if EXTRACT_MARK in c.messages[0].content]
    # 4 配置关闭
    env = make_env(rt_update={"style": REPO.roundtable.style.model_copy(update={"enabled": False})})
    with_style_replies(env)
    ref = upload(env)
    r = await env.orc.start(Question(MEDIUM), discussion(), seed=1, attachments=[ref.id])
    assert "style" not in table_pipeline(env, r.session_id)


async def test_collab_triggers_when_an_image_model_is_available():
    env = make_env()
    with_style_replies(env)
    ref = upload(env)
    r = await env.orc.start(Question(MEDIUM), collab(), seed=1, attachments=[ref.id])
    assert r.status == "completed" and table_pipeline(env, r.session_id)[0] == "style"
    env2 = Env(confirm_threshold_usd=100.0)  # 没有画图模型
    with_style_replies(env2)
    ref2 = upload(env2)
    r2 = await env2.orc.start(Question(MEDIUM), collab(), seed=1, attachments=[ref2.id])
    assert "style" not in table_pipeline(env2, r2.session_id)


# --- 风格规范进入每次调用 ----------------------------------------------------------


async def test_style_guide_is_in_every_later_call_and_identical_for_everyone():
    env = make_env()
    with_style_replies(env)
    ref = upload(env)
    r = await env.orc.start(Question(MEDIUM), discussion(), seed=1, attachments=[ref.id])
    assert r.status == "completed"
    later = [
        c
        for c in env.fake.calls
        if "规划员" not in c.messages[0].content
        and "图片转写成" not in c.messages[0].content
        and EXTRACT_MARK not in c.messages[0].content
        and MERGE_MARK not in c.messages[0].content
    ]
    assert later
    for c in later:
        assert (
            "<style_spec>" in c.messages[1].content and "<style_checklist>" in c.messages[1].content
        )
        assert "风格规范" in c.messages[0].content
        assert "1. " + CHECKLIST[0] in c.messages[1].content
    answers = [c for c in later if "独立完成同一道题" in c.messages[0].content]
    blocks = {
        re.search(r"<style_spec>.*</style_checklist>", c.messages[1].content, re.S).group(0)
        for c in answers
    }
    assert len(blocks) == 1  # 同一步骤所有成员收到的规范相同


async def test_text_only_style_when_nobody_can_see_images():
    env = make_env()
    with_style_replies(env)
    ref = upload(env)
    blind = UserChoice("custom", ("b1", "b3", "f3", "f4"), "f4", "discussion", "image", None)
    r = await env.orc.start(Question(MEDIUM), blind, seed=1, attachments=[ref.id])
    assert r.status == "completed"
    data = outputs(env, r.session_id, "style_spec")[0][1]
    assert data["text_only"] and "可能不准" in data["warning"] and data["extractors"] == []
    assert not [c for c in env.fake.calls if EXTRACT_MARK in c.messages[0].content]
    merge = [c for c in env.fake.calls if MERGE_MARK in c.messages[0].content]
    assert len(merge) == 1 and "图中文字" in merge[0].messages[1].content  # 用图片的文字版
    assert "来自图片的文字版" in merge[0].messages[0].content
    # 之后每次调用的规范里也注明可能不准
    answer = next(c for c in env.fake.calls if "独立完成同一道题" in c.messages[0].content)
    assert "可能不准" in answer.messages[0].content


async def test_bad_merge_degrades_without_a_checklist_and_without_gate_calls():
    env = make_env()
    script(env, **{EXTRACT_MARK: lambda m, msgs: EXTRACTION, MERGE_MARK: lambda m, msgs: "乱写"})
    ref = upload(env)
    r = await env.orc.start(Question(MEDIUM), discussion(), seed=1, attachments=[ref.id])
    assert r.status == "completed"
    data = outputs(env, r.session_id, "style_spec")[0][1]
    assert data["degraded"] and data["checklist"] == [] and "拼接" in data["warning"]
    assert EXTRACTION.splitlines()[0] in data["spec"]


# --- 参考图传给画图模型 --------------------------------------------------------------


async def test_references_go_to_an_image_edit_model_and_are_recorded():
    env = make_env()
    with_style_replies(env)
    ref = upload(env)
    r = await env.orc.start(
        Question(MEDIUM), discussion(tier="flagship"), seed=1, attachments=[ref.id]
    )
    assert r.status == "completed"
    calls = [c for c in env.fake.media_calls if c[0] == "image"]
    assert calls and all(c[1] == "mi2" for c in calls)  # 带 image_edit 标签的模型
    assert all([m.data for m in c[4]] == [PNG] for c in calls)  # 参考图字节在请求里
    jobs = env.rt.repo.media_jobs(r.session_id)
    assert jobs and all(j["reference_count"] == 1 and "warning" not in j["params"] for j in jobs)
    svc = RoundtableService(env.rt)
    view = svc.session(r.session_id)["media"]
    assert view[0]["reference_count"] == 1 and view[0]["warning"] is None
    await svc.aclose()


async def test_no_image_edit_model_falls_back_to_text_with_a_clear_warning():
    env = make_env()
    with_style_replies(env)
    ref = upload(env)
    r = await env.orc.start(
        Question(MEDIUM), discussion(tier="budget"), seed=1, attachments=[ref.id]
    )
    assert r.status == "completed"
    calls = [c for c in env.fake.media_calls if c[0] == "image"]
    assert calls and all(c[4] == () for c in calls)  # 没传参考图
    jobs = env.rt.repo.media_jobs(r.session_id)
    assert all(j["reference_count"] == 0 for j in jobs)
    assert "没有支持参考图的画图模型，只能按文字风格规范生成" in jobs[0]["params"]["warning"]
    # 过程中的提示也写明（讨论模式的 media 步骤把它放进步骤提示）
    assert any("只能按文字风格规范生成" in w for w in r.warnings) or any(
        "只能按文字风格规范生成" in str(e.data) for _, e in env.events
    )
    warned = [e for _, e in env.events if e.type == "media_warning"]
    assert warned and "文字风格规范" in warned[0].data["message"]


async def test_discussion_review_judges_the_checklist_item_by_item():
    env = make_env()
    with_style_replies(env, review=review_reply([False, True]))
    ref = upload(env)
    r = await env.orc.start(Question(MEDIUM), discussion(), seed=1, attachments=[ref.id])
    assert r.status == "completed"
    reviews = outputs(env, r.session_id, "media_review")
    first = reviews[0][1]
    assert not first["satisfied"] and first["checks"][0]["ok"] is False
    assert any(not c["ok"] for c in first["checks"])
    # 评审看到参考图（附件原图）和生成的图片
    review_calls = [c for c in env.fake.calls if REVIEW_MARK in c.messages[0].content]
    assert review_calls and all(len(c.messages[1].media) >= 1 for c in review_calls)
    assert "<style_checklist>" in review_calls[0].messages[1].content
    # 统筹的改写意见针对不符合的条目：决定提示词里带着"不符合风格清单"
    decide = [c for c in env.fake.calls if "决定是否需要重新生成" in c.messages[0].content]
    assert decide and "不符合风格清单：" + CHECKLIST[0] in user_text(decide[0].messages)


async def test_a_model_cannot_pass_by_ignoring_a_failed_check():
    from roundtable.core.steps.media import parse_media_review

    text = json.dumps(
        {
            "satisfied": True,
            "checks": [{"item": "a", "ok": True}, {"item": "b", "ok": False, "note": "不对"}],
            "checked": "检查了清单",
        },
        ensure_ascii=False,
    )
    review = parse_media_review(text)
    assert not review.satisfied and review.valid  # 任意一条不符合即不通过，且给出了问题
    assert review.problems and "b" in review.problems[0][0]


# --- 协同：生成后对照清单校验，不通过退回重画 ---------------------------------------------


def pipeline_env(review) -> Env:
    env = make_env(pipeline=True)
    with_style_replies(env, review=review)
    return env


QUESTION = "参考这张图做一张四格人物介绍卡片"


async def run_collab(env: Env):
    ref = upload(env)
    return await env.orc.start(Question(QUESTION), collab(), seed=3, attachments=[ref.id])


def whole_task(env: Env, review) -> None:
    whole = json.dumps(
        {
            "subtasks": [
                {
                    "id": "T1",
                    "kind": "write",
                    "title": "完成整道题",
                    "requirements": "独立完成题目的全部要求",
                }
            ]
        },
        ensure_ascii=False,
    )
    script(
        env,
        **{
            "把任务拆成子任务": lambda m, msgs: whole,
            "没有通过代码检查": lambda m, msgs: whole,
            EXTRACT_MARK: lambda m, msgs: EXTRACTION,
            MERGE_MARK: merge_reply,
            REVIEW_MARK: review,
        },
    )


async def test_generated_image_failing_the_checklist_is_redrawn_until_it_passes():
    env = make_env(pipeline=True)
    whole_task(env, review_reply([False, True]))
    r = await run_collab(env)
    assert r.status == "completed"
    subtasks = outputs(env, r.session_id, "subtasks")[0][1]
    assert subtasks["info"]["template"] == "creation_reference"  # 有图片附件、有画图模型
    gen = next(s for s in subtasks["subtasks"] if s["kind"] == "generate")
    gates = [d for c, d in outputs(env, r.session_id, "style_gate") if d["subtask"] == gen["id"]]
    redraws = [
        d for c, d in outputs(env, r.session_id, "style_redraw") if d["subtask"] == gen["id"]
    ]
    phase1 = [g for g in gates if g["phase"] == 1]
    assert [g["attempt"] for g in phase1] == [1, 2]
    assert not phase1[0]["passed"] and not phase1[0]["final"]
    assert phase1[1]["passed"] and phase1[1]["final"]
    assert len([d for d in redraws if d["phase"] == 1]) == 1  # 重画了一次
    # 判定的人不是作者
    author = next(c for c, d in outputs(env, r.session_id, "work") if d["subtask"] == gen["id"])
    assert all(author not in g["reviewers"] for g in gates)
    # 重画：同一处的第二张图，带着参考图；作者的成果是最终采用的提示词
    jobs = [j for j in env.rt.repo.media_jobs(r.session_id) if j["subtask"] == gen["id"]]
    assert {j["step"] for j in jobs} >= {"work", "style_gate"}
    assert all(j["reference_count"] in (0, 1) for j in jobs)
    work = next(d for c, d in outputs(env, r.session_id, "work") if d["subtask"] == gen["id"])
    assert work["text"] == next(
        d["prompt"] for d in redraws if d["phase"] == 1
    )  # 成果是最终采用的提示词
    assert work["media"]["style"]["passed"] is True and work["media"]["style"]["attempts"] == 2
    # 下游（审查、整合）收到的是校验后的图片：审查类 / 整合类的依赖块里有文件
    assert any(e.type == "style_checked" for _, e in env.events)


async def test_still_failing_after_the_retries_keeps_the_last_version_and_flags_it():
    env = make_env(pipeline=True)
    whole_task(env, review_reply([False] * 20))
    r = await run_collab(env)
    assert r.status == "completed"
    retries = env.config.roundtable.media.style_retries
    subtasks = outputs(env, r.session_id, "subtasks")[0][1]
    gen = next(s for s in subtasks["subtasks"] if s["kind"] == "generate")
    gates = [
        d
        for c, d in outputs(env, r.session_id, "style_gate")
        if d["subtask"] == gen["id"] and d["phase"] == 1
    ]
    assert len(gates) == retries + 1 and gates[-1]["final"] and not gates[-1]["passed"]
    assert [g["final"] for g in gates[:-1]] == [False] * retries
    work = next(d for c, d in outputs(env, r.session_id, "work") if d["subtask"] == gen["id"])
    assert work["media"]["ok"] and work["media"]["style"]["passed"] is False
    # 合并时统筹看到"风格未通过"标记
    merge_call = next(c for c in env.fake.calls if "合并成一份完整成果" in c.messages[0].content)
    assert 'style="重画后仍未通过参考图风格清单"' in merge_call.messages[1].content
    # 恢复后标记仍在
    from roundtable.core.steps import restore_state

    state = restore_state(env.rt.repo, r.session_id, 0)
    assert any(sid == gen["id"] for sid, _ in state.collab.style_failed)


async def test_retries_zero_only_marks():
    media = REPO.roundtable.media.model_copy(update={"style_retries": 0})
    env = make_env(pipeline=True, rt_update={"media": media})
    whole_task(env, review_reply([False] * 5))
    r = await run_collab(env)
    subtasks = outputs(env, r.session_id, "subtasks")[0][1]
    gen = next(s for s in subtasks["subtasks"] if s["kind"] == "generate")
    gates = [
        d
        for c, d in outputs(env, r.session_id, "style_gate")
        if d["subtask"] == gen["id"] and d["phase"] == 1
    ]
    assert len(gates) == 1 and gates[0]["final"] and not gates[0]["passed"]
    assert not outputs(env, r.session_id, "style_redraw")


async def test_style_records_and_view_have_no_identity_when_anonymous():
    env = make_env(pipeline=True)
    whole_task(env, review_reply([False, True]))
    ref = upload(env)
    r = await env.orc.start(
        Question(QUESTION), collab(), seed=3, attachments=[ref.id], anonymous=True
    )
    assert r.status == "completed"
    svc = RoundtableService(env.rt)
    data = svc.session(r.session_id)
    text = json.dumps(data, ensure_ascii=False, default=str) + json.dumps(
        [(e.type, e.code, e.data) for _, e in env.events], ensure_ascii=False, default=str
    )
    terms = {m.id for m in env.config.models.models} | set(env.config.personas.nicknames.values())
    terms |= {m.vendor for m in env.config.models.models}
    found = [t for t in terms if re.search(rf"(?<![0-9A-Za-z]){re.escape(t)}(?![0-9A-Za-z])", text)]
    assert found == []
    assert any(o["kind"] == "style_spec" for o in data["outputs"])
    await svc.aclose()


async def test_resume_does_not_repeat_style_or_gate_calls():
    env = make_env(pipeline=True)
    whole_task(env, review_reply([False, True]))
    r = await run_collab(env)
    before = len(env.fake.calls)
    media_before = len(env.fake.media_calls)
    again = await env.orc.resume(r.session_id)
    assert again.status == "completed"
    assert len(env.fake.calls) == before and len(env.fake.media_calls) == media_before
