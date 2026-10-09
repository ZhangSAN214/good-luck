"""媒体生成服务：图片 / 语音 / 视频、计价、幂等、轮询超时与重试、中断恢复、模型选择。"""

from __future__ import annotations

import asyncio

import pytest

from roundtable.core.media import Placement
from roundtable.core.providers import ErrorKind

from .conftest import Rig

WHERE = Placement(table_no=0, step="media", round_no=1)


async def test_image_generation_registers_file_job_and_billing(rig: Rig):
    svc = rig.service()
    res = await svc.generate("image", "一只坐在窗台上的橘猫", WHERE, tier="budget")
    assert res.ok and res.file_id and res.model_id in {"gemini-3.1-flash-image", "gpt-image-1-mini"}
    row = rig.rt.repo.file(rig.sid, res.file_id)
    assert (row["kind"], row["mime"], row["step"], row["path"]) == (
        "image",
        "image/png",
        "media",
        "image_r1.png",
    )
    job = rig.rt.repo.media_jobs(rig.sid)[0]
    assert job["state"] == "completed" and job["round"] == 1 and job["kind"] == "image"
    assert job["prompt"] == "一只坐在窗台上的橘猫" and job["channel"] == "openrouter"
    # 按张计价（渠道没有返回实际费用）：记入 calls，进入预算与按渠道统计
    model = rig.config.models.get(res.model_id)
    # 渠道没有返回用量和费用：按一张图的典型输出 token 数 × 图像输出价格估算
    expected = (300 * model.price.input + model.image_tokens * model.price.output) / 1e6
    assert res.cost_usd == pytest.approx(expected)
    assert rig.rt.repo.session_cost(rig.sid) == pytest.approx(res.cost_usd)
    assert rig.rt.repo.spent_by_channel()["openrouter"].calls == 1
    assert [e[0] for e in rig.events] == ["media_started", "media_done"]


async def test_tier_selects_models_by_tag_and_tier_only(rig: Rig):
    svc = rig.service()
    assert svc.model_for("image", "flagship").id == "gpt-image-2"
    assert svc.model_for("video", "budget").id == "alibaba-wan-video"
    assert svc.model_for("video", "flagship").id == "veo-3.1"
    assert (
        svc.model_for("speech", "flagship").id == "gemini-3.8-flash-tts"
    )  # 没有该档时退到另一档，取默认
    # 同一场、同档、同种类总是同一个模型
    assert {svc.model_for("image", "budget").id for _ in range(5)} == {svc.model_for("image").id}
    picks = set()
    for seed in range(40):
        picks.add(
            rig.service().__class__(**{**_kw(rig), "seed": seed}).model_for("image", "budget").id
        )
    assert picks == {"gemini-3.1-flash-image", "gpt-image-1-mini"}  # 同档内随机


def _kw(rig: Rig) -> dict:
    return dict(
        session_id=rig.sid,
        config=rig.config,
        router=rig.rt.router,
        repo=rig.rt.repo,
        store=rig.rt.files,
        scrubber=rig.rt.scrubber,
    )


async def test_same_placement_is_idempotent(rig: Rig):
    svc = rig.service()
    first = await svc.generate("image", "猫", WHERE)
    second = await svc.generate("image", "猫（改）", WHERE)
    assert second.file_id == first.file_id and len(rig.calls("image")) == 1
    assert len(rig.rt.repo.media_jobs(rig.sid)) == 1
    third = await svc.generate("image", "猫（改）", Placement(0, "media", 2))
    assert third.file_id != first.file_id and len(rig.calls("image")) == 2


async def test_prompt_is_scrubbed_before_leaving(rig: Rig):
    await rig.service().generate("image", "画出 gpt-image-2 的 logo 风格的猫", WHERE)
    sent = rig.calls("image")[0][2]
    assert "gpt-image-2" not in sent


async def test_speech_billed_per_character(rig: Rig):
    text = "大家好，欢迎收听。" * 5
    res = await rig.service().generate("speech", text, Placement(0, "media", 1))
    assert res.ok
    row = rig.rt.repo.file(rig.sid, res.file_id)
    assert (row["kind"], row["mime"]) == ("audio", "audio/wav")
    assert res.cost_usd == pytest.approx(
        len(text) * 0.000015
    )  # 中文脚本 → 擅长中文的模型，按字符计价
    params = rig.calls("speech")[0][3]
    assert params["voice"] == "alloy" and params["response_format"] == "mp3"


async def test_reported_cost_wins_over_configured_price(rig: Rig):
    rig.fake.media_cost = 0.123
    res = await rig.service().generate("image", "猫", WHERE)
    assert res.cost_usd == 0.123
    assert rig.rt.repo.session_cost(rig.sid) == 0.123


async def test_video_submit_poll_download(rig: Rig):
    rig.fake.video_states.extend(["pending", "running", "running"])
    res = await rig.service().generate("video", "海浪拍打礁石", WHERE, tier="budget")
    assert res.ok
    assert (
        len(rig.calls("video")) == 1
        and len(rig.calls("poll")) == 4
        and len(rig.calls("fetch")) == 1
    )
    assert rig.sleeps == [10.0, 10.0, 10.0]  # 轮询间隔
    row = rig.rt.repo.file(rig.sid, res.file_id)
    assert (row["kind"], row["mime"], row["path"]) == ("video", "video/mp4", "video_r1.mp4")
    assert res.cost_usd == pytest.approx(0.10 * 5)  # 每秒 0.10 × 5 秒
    params = rig.calls("video")[0][3]
    assert params["duration"] == 5 and params["resolution"] == "720p"
    job = rig.rt.repo.media_jobs(rig.sid)[0]
    assert job["external_id"] == "job-1" and job["state"] == "completed"


async def test_video_timeout_then_retry_then_give_up():
    rig = Rig(timeout_s=60, poll_interval_s=20, submit_retries=1)
    rig.fake.video_states.extend(["running"] * 50)
    res = await rig.service().generate("video", "海浪", WHERE)
    assert not res.ok and "60" in res.error and res.attempts == 2
    jobs = rig.rt.repo.media_jobs(rig.sid)
    assert [j["state"] for j in jobs] == ["timeout", "timeout"] and [
        j["attempt"] for j in jobs
    ] == [1, 2]
    assert len(rig.calls("video")) == 2 and len(rig.calls("fetch")) == 0
    assert rig.rt.repo.session_cost(rig.sid) == 0  # 失败不计费


async def test_video_failed_job_is_retried_and_billed_once():
    rig = Rig(submit_retries=1)
    rig.fake.video_states.extend(["failed", "running", "completed"])
    res = await rig.service().generate("video", "海浪", WHERE)
    assert res.ok and res.attempts == 2
    jobs = rig.rt.repo.media_jobs(rig.sid)
    assert [j["state"] for j in jobs] == ["failed", "completed"]
    assert rig.rt.repo.spent_by_channel()["openrouter"].calls == 1
    assert jobs[0]["error"] == "生成失败"


async def test_video_submit_failure(rig: Rig):
    rig.fake.submit_errors.extend([ErrorKind.SERVER] * 4)
    res = await rig.service().generate("video", "海浪", WHERE)
    assert not res.ok and "提交失败" in res.error
    assert rig.rt.repo.media_jobs(rig.sid)[-1]["state"] == "failed"


async def test_video_resumes_the_same_remote_job_after_interruption(rig: Rig):
    """轮询到一半进程被中断：重新进入时继续轮询已提交的任务，不重复提交。"""
    rig.fake.video_states.extend(["running", "running", "running"])
    calls = {"n": 0}

    async def flaky_sleep(seconds):
        calls["n"] += 1
        if calls["n"] == 2:
            raise asyncio.CancelledError  # 模拟暂停 / 重启
        rig.now += seconds

    first = rig.service()
    first._sleep = flaky_sleep
    with pytest.raises(asyncio.CancelledError):
        await first.generate("video", "海浪", WHERE)
    job = rig.rt.repo.media_jobs(rig.sid)[0]
    assert job["state"] in ("pending", "running") and job["external_id"] == "job-1"

    res = await rig.service().generate("video", "海浪", WHERE)  # 新的服务实例 = 重启后
    assert res.ok
    assert len(rig.calls("video")) == 1  # 只提交过一次
    assert len(rig.rt.repo.media_jobs(rig.sid)) == 1
    assert rig.rt.repo.spent_by_channel()["openrouter"].calls == 1
    again = await rig.service().generate("video", "海浪", WHERE)
    assert again.file_id == res.file_id and len(rig.calls("poll")) == len(rig.calls("poll"))


async def test_video_poll_errors_are_tolerated_until_limit(rig: Rig):
    rig.fake.video_states.extend([ErrorKind.NETWORK, ErrorKind.NETWORK, "completed"])
    assert (await rig.service().generate("video", "海浪", WHERE)).ok
    rig2 = Rig(submit_retries=0)
    rig2.fake.video_states.extend([ErrorKind.NETWORK] * 5)
    res = await rig2.service().generate("video", "海浪", WHERE)
    assert not res.ok and "状态" in res.error


async def test_video_oversize_download_fails(rig: Rig):
    rig2 = Rig(max_mb=0.00001, submit_retries=0)
    res = await rig2.service().generate("video", "海浪", WHERE)
    assert not res.ok and "上限" in res.error


async def test_unavailable_when_no_model(rig: Rig):
    rules = rig.config.roundtable.media.model_copy(update={"enabled": False})
    rig.config = rig.config.model_copy(
        update={"roundtable": rig.config.roundtable.model_copy(update={"media": rules})}
    )
    svc = rig.service()
    assert "关闭" in svc.unavailable_reason("image")
    res = await svc.generate("image", "猫", WHERE)
    assert not res.ok and "关闭" in res.error and rig.fake.media_calls == []


async def test_image_failure_is_recorded_without_cost(rig: Rig):
    rig.fake.submit_errors.extend([ErrorKind.SERVER] * 3)
    res = await rig.service().generate("image", "猫", WHERE)
    assert not res.ok
    assert rig.rt.repo.media_jobs(rig.sid)[0]["state"] == "failed"
    assert rig.rt.repo.session_cost(rig.sid) == 0


def test_estimate_uses_unit_prices(rig: Rig):
    svc = rig.service()
    assert svc.estimate("video", "flagship") == pytest.approx(0.40 * 5)
    assert svc.estimate("image", "flagship") == pytest.approx((300 * 8.0 + 1056 * 30.0) / 1e6)
    # 默认的语音模型按 token 计价：输入 1000 token、输出 1000 × 3 个音频 token
    assert svc.estimate("speech", chars=1000) == pytest.approx((1000 * 0.5 + 3000 * 9.0) / 1e6)


async def test_speech_model_follows_script_language_and_default(rig: Rig):
    svc = rig.service()
    zh = await svc.generate("speech", "大家好，欢迎收听今天的节目。", Placement(0, "media", 1))
    en = await svc.generate(
        "speech", "Hello everyone, welcome to the show.", Placement(0, "media", 2)
    )
    assert zh.model_id == "qwen-audio-3.0-tts-flash"  # 主要是中文：带 zh 标签的优先
    assert en.model_id == "gemini-3.8-flash-tts"  # 其余：默认模型
    # 按 token 计价的默认模型：渠道没返回费用时按每字符约 3 个音频输出 token 估算
    text = "Hello everyone, welcome to the show."
    assert en.cost_usd == pytest.approx((len(text) * 0.5 + len(text) * 3 * 9.0) / 1e6)
    # 混合文字：中文占比不到三成时不算中文
    mixed = await svc.generate(
        "speech",
        "Please read this: 你好 and then continue in English for a while.",
        Placement(0, "media", 3),
    )
    assert mixed.model_id == "gemini-3.8-flash-tts"
