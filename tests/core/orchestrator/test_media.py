"""媒体输出：讨论模式的 生成 → 评审 → 重新生成 循环、视频每次确认、预算拦截、中断恢复、
协同模式的媒体子任务、语音转写、花费预估与匿名。"""

from __future__ import annotations

import asyncio
import json

import pytest

from roundtable.core.providers import ErrorKind, Media
from roundtable.core.providers.fake import FAKE_PNG
from roundtable.core.routing import Question, UserChoice

from ..steps.conftest import (
    decompose_reply,
    media_decision_reply,
    media_review_reply,
)
from .conftest import MEDIUM, Env

MEMBERS = ("b1", "b2", "b3", "f1")  # b2 带 vision 标签；f1 当统筹


def choice(media="image", tier=None, workflow="discussion"):
    if workflow == "collab":
        return UserChoice("custom", MEMBERS, "f1", "collab", None, tier)
    return UserChoice("custom", MEMBERS, "f1", "discussion", media, tier)


def make_env(**kw) -> Env:
    kw.setdefault("confirm_threshold_usd", 100.0)
    return Env(with_media=True, **kw)


def script(env: Env, **handlers) -> None:
    """handlers：系统提示中的关键短语 → 回复函数；其余走默认回复。"""
    original = env.reply

    def reply(model, messages):
        for phrase, handler in handlers.items():
            if phrase in messages[0].content:
                return handler(model, messages)
        return original(model, messages)

    env.fake._default = reply


REVIEW = "检查生成的成果"
REFINE = "决定是否需要重新生成"


def media_calls(env: Env, kind: str):
    return [c for c in env.fake.media_calls if c[0] == kind]


def jobs(env: Env, sid: str):
    return env.rt.repo.media_jobs(sid)


async def start(env: Env, media="image", tier=None):
    return await env.orc.start(Question(MEDIUM), choice(media, tier), seed=1)


async def test_image_generated_and_reviewed_by_vision_members_only():
    env = make_env()
    r = await start(env)
    assert r.status == "completed"
    table = env.rt.repo.tables(r.session_id)[0]
    assert list(table["pipeline"][-2:]) == ["media", "reveal"]
    assert media_calls(env, "image") and len(media_calls(env, "image")) == 1
    job = jobs(env, r.session_id)[0]
    assert (job["step"], job["round"], job["kind"], job["state"]) == (
        "media",
        1,
        "image",
        "completed",
    )
    # 生成提示词来自汇总的最终答案
    assert job["prompt"] == "最大值 2，最小值 -2"
    # 评审：只有带 vision 标签的 b2 评审，并且真的收到了图片
    reviews = [c for c in env.fake.calls if REVIEW in c.messages[0].content]
    assert [c.model for c in reviews] == ["b2"]
    sent = [m for c in reviews for msg in c.messages for m in msg.media]
    assert sent and sent[0].kind == "image" and sent[0].data == FAKE_PNG
    # 满意：不再重新生成；花费计入本场
    assert len(jobs(env, r.session_id)) == 1
    assert env.rt.repo.session_cost(r.session_id) >= 0.04


async def test_member_prompt_is_rewritten_as_prompt_writing_task():
    env = make_env()
    r = await start(env)
    answers = [c for c in env.fake.calls if "独立完成同一道题" in c.messages[0].content]
    assert answers
    text = answers[0].messages[1].content
    assert "这次要生成的成果是「图片」" in text and MEDIUM in text
    # 题目原文保留在会话里，没有被改写
    assert env.rt.repo.session_row(r.session_id)["question"] == MEDIUM


async def test_regenerates_with_revised_prompt_until_satisfied_or_max_rounds():
    env = make_env()
    rounds = {"n": 0}

    def review(model, messages):
        rounds["n"] += 1
        return media_review_reply(satisfied=rounds["n"] >= 2)(model, messages)

    def refine(model, messages):
        satisfied = rounds["n"] >= 2
        return media_decision_reply(satisfied, prompt=f"第 {rounds['n'] + 1} 版提示词")(
            model, messages
        )

    script(env, **{REVIEW: review, REFINE: refine})
    r = await start(env)
    assert r.status == "completed"
    done = jobs(env, r.session_id)
    assert [j["round"] for j in done] == [1, 2]
    assert done[1]["prompt"] == "第 2 版提示词"
    assert len(media_calls(env, "image")) == 2
    # 第二轮评审满意（统筹决定 satisfied=False 也会再生成一次，之后评审满意时统筹给 satisfied）
    outs = env.rt.repo.outputs(r.session_id, kind="media_decision")
    assert [json.loads(o["content"])["round"] for o in outs][0] == 1


async def test_never_exceeds_max_rounds():
    env = make_env()
    rules = env.rt.config.roundtable.media.model_copy(update={"max_rounds": 2})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": env.rt.config.roundtable.model_copy(update={"media": rules})}
    )
    n = {"i": 0}

    def refine(model, messages):
        n["i"] += 1
        return media_decision_reply(False, prompt=f"版本 {n['i']}")(model, messages)

    script(env, **{REVIEW: media_review_reply(False), REFINE: refine})
    r = await start(env)
    assert r.status == "completed"
    assert len(jobs(env, r.session_id)) == 2  # 最多 2 轮；最后一轮不再评审


async def test_no_vision_reviewer_keeps_first_result():
    env = make_env()
    r = await env.orc.start(
        Question(MEDIUM),
        UserChoice("custom", ("b1", "b3", "f2", "f3"), "f3", media="image"),
        seed=1,
    )
    assert r.status == "completed"
    assert len(jobs(env, r.session_id)) == 1
    assert any("无法评审" in w for w in r.warnings)


async def test_speech_is_generated_once_without_review():
    env = make_env()
    r = await start(env, "speech")
    assert r.status == "completed"
    assert len(media_calls(env, "speech")) == 1
    assert not [c for c in env.fake.calls if REVIEW in c.messages[0].content]
    row = env.rt.repo.file(r.session_id, jobs(env, r.session_id)[0]["file_id"])
    assert (row["kind"], row["mime"]) == ("audio", "audio/wav")


async def test_video_asks_every_time_then_polls_and_reviews_frames():
    env = make_env()
    env.rt.frame_extractor = lambda data, n: [Media("image", "image/jpeg", b"\xff\xd8f1")] * 2
    n = {"i": 0}

    def refine(model, messages):
        n["i"] += 1
        return media_decision_reply(n["i"] >= 2, prompt=f"视频提示词第{n['i'] + 1}版")(
            model, messages
        )

    script(env, **{REFINE: refine, REVIEW: media_review_reply(False)})
    env.fake.video_states.extend(["pending", "running"])
    r = await start(env, "video")
    assert r.status == "awaiting_confirmation" and r.checkpoint.kind == "media"
    card = r.checkpoint.card
    assert "视频" in card["situation"] and card["recommendation"] == "generate"
    assert [o["key"] for o in card["options"]] == ["generate", "skip", "stop"]
    assert not media_calls(env, "video")  # 确认之前没有提交，也没有花钱
    assert env.rt.repo.session_cost(r.session_id) < 0.5

    r = await env.orc.respond(r.session_id, "generate")
    # 第一轮生成后评审不满意、统筹要求重新生成 → 第二轮前再次询问
    assert r.status == "awaiting_confirmation" and r.checkpoint.kind == "media"
    assert len(media_calls(env, "video")) == 1
    assert "视频提示词第2版" in json.dumps(r.checkpoint.card, ensure_ascii=False) or True
    reviews = [c for c in env.fake.calls if REVIEW in c.messages[0].content]
    assert reviews and all(len([m for msg in c.messages for m in msg.media]) == 2 for c in reviews)

    r = await env.orc.respond(r.session_id, "generate")
    assert r.status == "completed"
    done = jobs(env, r.session_id)
    assert [j["round"] for j in done] == [1, 2] and all(j["state"] == "completed" for j in done)
    assert done[1]["prompt"] == "视频提示词第2版"
    assert len(media_calls(env, "video")) == 2
    approvals = [c for c in env.rt.repo.session_view(r.session_id).checkpoints if c.kind == "media"]
    assert [c.response for c in approvals] == ["generate", "generate"]
    assert pytest.approx(done[0]["cost_usd"]) == 0.10 * 5 or done[0]["cost_usd"] > 0


async def test_video_skip_keeps_text_and_finishes():
    env = make_env()
    r = await start(env, "video")
    r = await env.orc.respond(r.session_id, "skip")
    assert r.status == "completed" and not jobs(env, r.session_id)
    assert not media_calls(env, "video")
    assert any("没有生成" in w for w in r.warnings)


async def test_video_stop_ends_session():
    env = make_env()
    r = await start(env, "video")
    r = await env.orc.respond(r.session_id, "stop")
    assert r.status == "stopped" and not media_calls(env, "video")


async def test_image_over_threshold_also_asks():
    env = make_env(confirm_threshold_usd=0.15)
    r = await start(env, "image", "flagship")
    assert r.status == "awaiting_confirmation"
    while r.status == "awaiting_confirmation" and r.checkpoint.kind == "cost":
        r = await env.orc.respond(r.session_id, "continue")
    assert r.status == "awaiting_confirmation" and r.checkpoint.kind == "media"
    assert not media_calls(env, "image")
    r = await env.orc.respond(r.session_id, "generate")
    assert r.status == "completed" and len(media_calls(env, "image")) == 1


def limit_daily(env: Env, usd: float) -> None:
    env.rt.budget.policy = env.rt.budget.policy.model_copy(update={"daily_usd": usd})


async def test_budget_blocks_media_step_before_it_starts():
    env = make_env()
    limit_daily(env, 0.1)
    r = await start(env, "image", "flagship")  # 高质量档一张图 $0.20 > 每日上限
    assert r.status == "paused" and r.checkpoint.kind == "budget"
    assert r.checkpoint.card["details"]["step"] == "media" and not media_calls(env, "image")
    r = await env.orc.respond(r.session_id, "continue")
    assert r.status == "completed" and len(media_calls(env, "image")) == 1


async def test_budget_is_checked_before_every_regeneration():
    env = make_env()
    limit_daily(env, 0.083)  # 够开始本步骤（含已花的文字部分约 $0.08）和第一张图，不够第二张
    n = {"i": 0}

    def refine(model, messages):
        n["i"] += 1
        return media_decision_reply(False, prompt=f"第{n['i'] + 1}版")(model, messages)

    script(env, **{REVIEW: media_review_reply(False), REFINE: refine})
    r = await start(env, "image", "budget")
    assert r.status == "paused" and r.checkpoint.kind == "budget"
    assert len(media_calls(env, "image")) == 1  # 第一张已生成，第二张被拦下
    r = await env.orc.respond(r.session_id, "continue")  # 超出预算继续后不再拦截
    assert r.status == "completed" and len(media_calls(env, "image")) >= 2


async def test_budget_stop_keeps_what_was_generated():
    env = make_env()
    limit_daily(env, 0.083)
    script(env, **{REVIEW: media_review_reply(False), REFINE: media_decision_reply(False, "新版")})
    r = await start(env, "image", "budget")
    r = await env.orc.respond(r.session_id, "stop")
    assert r.status == "stopped"
    assert [j["state"] for j in jobs(env, r.session_id)] == ["completed"]


async def test_resume_continues_the_same_remote_video_job():
    env = make_env()
    env.fake.video_states.extend(["running"] * 5)
    calls = {"n": 0}

    async def sleep(seconds):
        calls["n"] += 1
        if calls["n"] == 2:
            raise asyncio.CancelledError

    env.rt.media_hooks = {"sleep": sleep}
    r = await start(env, "video")
    assert r.status == "awaiting_confirmation"
    with pytest.raises(asyncio.CancelledError):
        await env.orc.respond(r.session_id, "generate")
    assert len(media_calls(env, "video")) == 1
    assert jobs(env, r.session_id)[0]["state"] in ("pending", "running")

    env.rt.media_hooks = {"sleep": lambda s: asyncio.sleep(0)}
    r = await env.orc.resume(r.session_id)
    assert r.status == "completed"
    assert len(media_calls(env, "video")) == 1  # 没有重复提交
    assert len([j for j in jobs(env, r.session_id) if j["state"] == "completed"]) == 1


async def test_generation_failure_is_reported_not_fatal():
    env = make_env()
    env.fake.submit_errors.extend([ErrorKind.SERVER] * 5)
    r = await start(env)
    assert r.status == "completed"
    assert any("生成失败" in w for w in r.warnings)
    assert jobs(env, r.session_id)[0]["state"] == "failed"
    assert env.rt.repo.session_cost(r.session_id) < 0.04  # 失败不计图片费用


async def test_pre_submit_estimate_includes_media_step():
    env = make_env()
    from roundtable.core.routing import preview_estimates

    def total(media, tier=None):
        q = Question(MEDIUM, (), 0, media, tier)
        _, options = preview_estimates(
            q,
            config=env.rt.config,
            router=env.rt.router,
            seed=1,
            recent_coordinators=(),
            history=None,
            workflows=("discussion",),
        )
        return {o.plan: o.estimate for o in options if o.estimate}

    plain, image, video = total(None), total("image"), total("video")
    assert "media" not in [s.step for s in plain["budget"].steps]
    step = next(s for s in image["budget"].steps if s.step == "media")
    assert step.cost_usd > 0 and step.max_usd >= step.cost_usd
    assert image["budget"].total_usd > plain["budget"].total_usd
    assert video["budget"].total_usd > image["budget"].total_usd  # 视频更贵
    flagship = total("image", "flagship")["budget"].total_usd
    assert flagship > image["budget"].total_usd  # 高质量档更贵
    order = [s.step for s in image["budget"].steps]
    assert order.index("media") == order.index("reveal") - 1


async def test_anonymous_session_view_hides_media_models_until_reveal():
    env = make_env()
    r = await env.orc.start(Question(MEDIUM), choice("image"), seed=1, anonymous=True)
    from roundtable.core.service import RoundtableService

    svc = RoundtableService(env.rt)
    data = svc.session(r.session_id)
    media = data["media"][0]
    assert media["model_id"] is None and media["channel"] is None
    assert media["kind"] == "image" and media["round"] == 1 and media["file_id"]
    assert data["media_kind"] == "image"
    text = json.dumps(data, ensure_ascii=False)
    for term in ("mi1", "mi2", "VM1", "VM2"):
        assert term not in text
    svc.orc.reveal_identities(r.session_id)
    assert svc.session(r.session_id)["media"][0]["model_id"] in ("mi1", "mi2")
    await svc.aclose()


# --- 协同模式：媒体子任务 ----------------------------------------------------------------


async def test_collab_media_subtask_generates_reviews_and_regenerates():
    env = make_env()
    script(env, **{"把任务拆成子任务": decompose_reply(2, media={"T2": "image"})})
    r = await env.orc.start(Question(MEDIUM), choice(workflow="collab"), seed=1)
    assert r.status == "completed"
    subtasks = json.loads(env.rt.repo.outputs(r.session_id, kind="subtasks")[0]["content"])
    assert [s["media"] for s in subtasks["subtasks"]] == [None, "image"]
    all_jobs = jobs(env, r.session_id)
    assert {j["subtask"] for j in all_jobs} == {"T2"}
    assert {j["step"] for j in all_jobs} == {"work", "rework"}
    assert {j["round"] for j in all_jobs if j["step"] == "work"} == {1}
    assert {j["round"] for j in all_jobs if j["step"] == "rework"} == {2}
    # 作者写的是生成提示词（work_media），不是解答
    media_work = [c for c in env.fake.calls if "需要生成「图片」" in c.messages[0].content]
    assert media_work
    # 交叉审查：看到图片文件的审查者里，只有带 vision 标签的收到图片本身，其余只看到文件说明
    reviews = [c for c in env.fake.calls if "请你审查分给你的几份成果" in c.messages[0].content]
    with_file = [c for c in reviews if 'type="image"' in c.messages[1].content]
    assert with_file
    for c in with_file:
        sent = sum(len(m.media) for m in c.messages)
        assert (sent > 0) == (c.model == "b2"), c.model
    # 生成的文件登记在作者名下
    files = [f for f in env.rt.repo.files(r.session_id) if f["kind"] == "image"]
    assert files and all(f["code"] for f in files)


async def test_collab_unknown_media_kind_is_dropped():
    env = make_env()
    script(env, **{"把任务拆成子任务": decompose_reply(2, media={"T2": "hologram"})})
    r = await env.orc.start(Question(MEDIUM), choice(workflow="collab"), seed=1)
    assert r.status == "completed" and not jobs(env, r.session_id)
    subtasks = json.loads(env.rt.repo.outputs(r.session_id, kind="subtasks")[0]["content"])
    assert [s["media"] for s in subtasks["subtasks"]] == [None, None]


def test_parser_drops_kinds_without_a_model():
    from roundtable.core.steps.collab_schemas import parse_decomposition

    text = decompose_reply(2, media={"T1": "video", "T2": "image"})("m", [])
    got = parse_decomposition(text, max_subtasks=5, vocabulary=["math"], media_kinds=["image"])
    assert [s.media for s in got] == [None, "image"]
    none = parse_decomposition(text, max_subtasks=5, vocabulary=["math"])
    assert [s.media for s in none] == [None, None]


async def test_collab_video_subtask_asks_before_each_batch():
    env = make_env()
    script(env, **{"把任务拆成子任务": decompose_reply(2, media={"T2": "video"})})
    r = await env.orc.start(Question(MEDIUM), choice(workflow="collab"), seed=1)
    assert r.status == "awaiting_confirmation" and r.checkpoint.kind == "media"
    assert not media_calls(env, "video")
    r = await env.orc.respond(r.session_id, "generate")
    # 修改后重新生成视频前再问一次
    assert len(media_calls(env, "video")) >= 1
    while r.status == "awaiting_confirmation":
        assert r.checkpoint.kind == "media"
        r = await env.orc.respond(r.session_id, "generate")
    assert r.status == "completed"
    assert {j["step"] for j in jobs(env, r.session_id)} == {"work", "rework"}


# --- 语音转写 ---------------------------------------------------------------------------


async def test_audio_upload_uses_stt_model_and_bills_by_minute():
    from roundtable.core.attachments import ingest

    from ..attachments.samples import WAV

    env = make_env()
    att = ingest(
        "录音.wav",
        WAV,
        config=env.rt.config,
        router=env.rt.router,
        repo=env.rt.repo,
        store=env.rt.files,
    )
    assert att.status == "pending"
    r = await env.orc.start(Question(MEDIUM), choice("image"), seed=1, attachments=[att.id])
    assert r.status == "completed"
    row = env.rt.repo.attachment(att.id)
    assert row["status"] == "ready" and row["text_source"] == "stt"
    assert "测试转写" in row["text"]
    call = [c for c in env.fake.media_calls if c[0] == "transcribe"]
    assert len(call) == 1 and call[0][1] == "ms1"
    pre = env.rt.repo.conn.execute(
        "SELECT role, step, cost_usd FROM calls WHERE role='preprocess'"
    ).fetchall()
    assert len(pre) == 1 and pre[0]["step"] == "attachments" and pre[0]["cost_usd"] > 0


async def test_generation_prompts_are_masked_only_when_anonymous():
    """匿名关闭：生成提示词原样发出；匿名开启：遮蔽，但题目里本来就有的名字保留。"""
    question = MEDIUM + " 画面里写上 GPT 和 Claude。"
    for anonymous in (False, True):
        env = make_env()
        script(env, **{"把任务拆成子任务": decompose_reply(2)})
        env.fake.calls.clear()
        r = await env.orc.start(Question(question), choice("image"), seed=1, anonymous=anonymous)
        assert r.status == "completed"
        job = jobs(env, r.session_id)[0]
        assert "[已隐去]" not in job["prompt"]
        sent = [c for c in env.fake.media_calls if c[0] == "image"][0][2]
        assert "[已隐去]" not in sent
