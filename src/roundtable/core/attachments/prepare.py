"""需要模型处理的附件：图片 → 文字版（最便宜的 vision 模型），
音频 → 转写稿（最便宜的 transcribe 模型），视频 → 文字版（声音先转写，画面按时间取帧或
整段交给带 ``video_in`` 标签的模型，最便宜的 vision 模型写成时间线，见 docs/PERCEPTION.md）。

每个文件只处理一次，结果入库，费用记在本场（calls 表中 step="attachments"、role="preprocess"）。
模型生成的文字转给其他模型前做身份遮蔽。
"""

from __future__ import annotations

import logging
import random

from roundtable.core.allocation import IdentityScrubber, cheapest
from roundtable.core.config import AppConfig
from roundtable.core.media import pick_stt_model, unit_cost
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import (
    AllChannelsFailed,
    ChannelRouter,
    Completion,
    Media,
    Message,
    NoChannelAvailable,
)
from roundtable.core.storage import Repository

from .ingest import MODEL_TAGS, capable
from .model import Attachment
from .store import FileStore

log = logging.getLogger(__name__)

STEP = "attachments"
ROLES = {"image": ("describe_image", "vision"), "audio": ("transcribe", "transcribe")}


async def prepare_attachments(
    session_id: str,
    *,
    config: AppConfig,
    router: ChannelRouter,
    prompts: PromptLibrary,
    repo: Repository,
    store: FileStore,
    scrubber: IdentityScrubber,
    question: str,
    rng: random.Random,
) -> list[Attachment]:
    """处理本场所有待处理的附件，返回处理后的全部附件（失败的带 error）。"""
    rules = config.roundtable.uploads
    for row in repo.session_attachments(session_id):
        a = Attachment.from_row(row)
        if a.status != "pending" or a.kind not in MODEL_TAGS:
            continue
        if a.kind == "video":
            await _describe_video(
                session_id, a, config, router, prompts, repo, store, scrubber, question, rng
            )
            continue
        role, source = ROLES[a.kind]
        if a.kind == "audio" and await _transcribe_stt(
            session_id, a, config, router, repo, store, scrubber, question, rng
        ):
            continue
        pool = capable(router, MODEL_TAGS[a.kind])
        if not pool:
            repo.update_attachment(a.id, status="failed", error="没有可用的模型处理这个附件")
            continue
        max_tokens = rules.describe_max_tokens if a.kind == "image" else rules.transcribe_max_tokens
        model = cheapest(pool, rng, input_tokens=1500, output_tokens=max_tokens // 3)
        rendered = prompts.render(role, config.roundtable.prompts[role], name=a.name)
        media = Media(a.kind, a.mime, store.load(a.storage_key), a.name)  # type: ignore[arg-type]
        messages = tuple(
            Message(m.role, m.content, (media,)) if m.role == "user" else m
            for m in rendered.messages
        )
        try:
            completion = await router.complete(
                model.id, messages, {"max_tokens": max_tokens, "reasoning": {"effort": "low"}}
            )
        except (AllChannelsFailed, NoChannelAvailable) as exc:
            repo.record_call(
                session_id,
                step=STEP,
                role="preprocess",
                model_id=model.id,
                messages=messages,
                prompt=rendered,
                failure=exc,
            )
            repo.update_attachment(a.id, status="failed", error="处理附件的模型调用失败")
            log.warning("附件预处理失败（%s）", a.kind)
            continue
        repo.record_call(
            session_id,
            step=STEP,
            role="preprocess",
            model_id=model.id,
            messages=messages,
            prompt=rendered,
            completion=completion,
        )
        text = completion.text.strip()
        if not text:
            repo.update_attachment(a.id, status="failed", error="模型没有给出内容")
            continue
        if completion.truncated:
            text += "\n……（输出达到长度上限，以下内容缺失）"
        repo.update_attachment(
            a.id, status="ready", text=scrubber.scrub(text, question), text_source=source
        )
    return [Attachment.from_row(r) for r in repo.session_attachments(session_id)]


# 没有时长信息时按 128 kbps 的 MP3 估计（字节 / 秒）
BYTES_PER_SECOND = 16000


async def _transcribe_stt(
    session_id, a: Attachment, config, router, repo, store, scrubber, question, rng
) -> bool:
    """用 stt 模型（Whisper 系列）转写音频。成功返回 True；没有可用的 stt 模型或调用失败返回 False
    （由旧的 transcribe 标签对话模型接着处理）。费用按渠道返回的实际费用，否则按每分钟单价。"""
    media = Media("audio", a.mime, store.load(a.storage_key), a.name)
    said = await _stt_text(session_id, media, router, repo, rng)
    if said is None:
        return False
    text = said.strip()
    if not text:
        repo.update_attachment(a.id, status="failed", error="模型没有给出内容")
        return True
    repo.update_attachment(
        a.id, status="ready", text=scrubber.scrub(text, question), text_source="stt"
    )
    return True


async def _stt_text(session_id, media: Media, router, repo, rng) -> str | None:
    """stt 模型转写一段音频并记账；没有可用的 stt 模型或调用失败返回 None。"""
    model = pick_stt_model(router, rng)
    if model is None:
        return None
    try:
        inv = await router.invoke(
            model.id,
            lambda prov, route, m: prov.transcribe(route.model, media, m.params_for(route)),
        )
    except (AllChannelsFailed, NoChannelAvailable) as exc:
        repo.record_call(
            session_id,
            step=STEP,
            role="preprocess",
            model_id=model.id,
            messages=[Message("user", media.name)],
            failure=exc,
        )
        log.warning("音频转写（stt）失败，改用对话模型")
        return None
    out = inv.result
    seconds = out.seconds if out.seconds is not None else len(media.data) / BYTES_PER_SECOND
    if out.cost_usd is not None:
        cost, source = out.cost_usd, "reported"
    else:
        cost, source = unit_cost(model, inv.route, seconds=seconds) or 0.0, "estimated"
    completion = Completion(
        text=out.text,
        model_id=model.id,
        channel=inv.channel,
        channel_kind=inv.channel_kind,
        route_model=inv.route.model,
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        cost_usd=cost,
        cost_source=source,  # type: ignore[arg-type]
        latency_s=inv.latency_s,
        attempts=inv.attempts,
    )
    repo.record_call(
        session_id,
        step=STEP,
        role="preprocess",
        model_id=model.id,
        messages=[Message("user", media.name, (media,))],
        completion=completion,
    )
    return out.text


async def _complete(session_id, model_id: str, messages, rendered, params, router, repo):
    """一次预处理的对话调用并记账；失败返回 None。"""
    try:
        completion = await router.complete(model_id, messages, params)
    except (AllChannelsFailed, NoChannelAvailable) as exc:
        repo.record_call(
            session_id,
            step=STEP,
            role="preprocess",
            model_id=model_id,
            messages=messages,
            prompt=rendered,
            failure=exc,
        )
        return None
    repo.record_call(
        session_id,
        step=STEP,
        role="preprocess",
        model_id=model_id,
        messages=messages,
        prompt=rendered,
        completion=completion,
    )
    return completion


async def _video_sound(session_id, data: bytes, config, router, prompts, repo, rng) -> str | None:
    """视频声音的转写稿：没有声音轨返回空字符串，转不了返回 None。"""
    from roundtable.core.perception.senses import SenseUnavailable, extract_audio

    try:
        wav = extract_audio(data, config.roundtable.perception.max_audio_s)
    except SenseUnavailable:
        return None
    if wav is None:
        return ""
    audio = Media("audio", "audio/wav", wav, "sound.wav")
    said = await _stt_text(session_id, audio, router, repo, rng)
    if said is not None:
        return said.strip()
    pool = capable(router, "transcribe")
    if not pool:
        return None
    rules = config.roundtable.uploads
    model = cheapest(pool, rng, input_tokens=1500, output_tokens=rules.transcribe_max_tokens // 3)
    rendered = prompts.render(
        "transcribe", config.roundtable.prompts["transcribe"], name=audio.name
    )
    messages = tuple(
        Message(m.role, m.content, (audio,)) if m.role == "user" else m for m in rendered.messages
    )
    params = {"max_tokens": rules.transcribe_max_tokens, "reasoning": {"effort": "low"}}
    completion = await _complete(session_id, model.id, messages, rendered, params, router, repo)
    return completion.text.strip() if completion is not None else None


def _neutralize(text: str) -> str:
    for tag in ("transcript", "video_layout", "name"):
        text = text.replace(f"</{tag}", f"<\u200b/{tag}")
    return text


async def _describe_video(
    session_id, a: Attachment, config, router, prompts, repo, store, scrubber, question, rng
) -> None:
    """视频 → 文字版：声音转写；画面在精细模式下整段交给带 video_in 标签的模型（不超过
    ``perception.native_max_mb``），否则按时间均匀取 ``perception.fine.video_frames`` 帧
    作为随附图片；再由最便宜的 vision 模型写成时间线。文字版末尾附上声音转写稿。"""
    from roundtable.core.perception.senses import (
        SenseUnavailable,
        clock,
        sample_frames,
        video_duration,
    )

    perception = config.roundtable.perception
    fine = perception.fine
    data = store.load(a.storage_key)
    heard = await _video_sound(session_id, data, config, router, prompts, repo, rng)
    native = []
    if fine.native_video and len(data) <= perception.native_max_mb * 2**20:
        native = [m for m in capable(router, "video_in") if "vision" in m.tags]
    pool = native or capable(router, "vision")
    if not pool:
        repo.update_attachment(a.id, status="failed", error="没有可用的模型处理这个附件")
        return
    length = video_duration(data)
    if native:
        media: tuple[Media, ...] = (Media("video", a.mime, data, a.name),)
        layout = f"随附视频（约 {clock(length or 0)}，画面连声音）。"
    else:
        try:
            frames = sample_frames(data, fine.video_frames, fine.frame_side)
        except SenseUnavailable:
            frames = []
        if not frames:
            if heard:
                text = f"（没能取出画面，只有声音）\n\n【声音转写】\n{heard}"
                repo.update_attachment(
                    a.id, status="ready", text=scrubber.scrub(text, question), text_source="stt"
                )
            else:
                repo.update_attachment(a.id, status="failed", error="没能取出视频画面")
            return
        media = tuple(
            Media("image", "image/jpeg", f.jpeg, f"{a.name}#{k}") for k, f in enumerate(frames, 1)
        )
        times = "、".join(clock(f.at) for f in frames)
        layout = (
            f"视频约 {clock(length or 0)}。随附图片 1～{len(media)} 是按时间顺序取的画面，"
            f"每张一帧，取帧时间依次为 {times}。"
        )
    if heard:
        layout += f"\n<transcript>\n{_neutralize(heard)}\n</transcript>"
    else:
        layout += "\n（这段视频没有声音或听不清）" if heard == "" else "\n（声音没能转写）"
    max_tokens = config.roundtable.uploads.describe_max_tokens
    model = cheapest(pool, rng, input_tokens=800 * len(media), output_tokens=max_tokens // 3)
    rendered = prompts.render(
        "describe_video", config.roundtable.prompts["describe_video"], name=a.name, layout=layout
    )
    messages = tuple(
        Message(m.role, m.content, media) if m.role == "user" else m for m in rendered.messages
    )
    params = {"max_tokens": max_tokens, "reasoning": {"effort": "low"}}
    completion = await _complete(session_id, model.id, messages, rendered, params, router, repo)
    if completion is None or not completion.text.strip():
        error = "处理附件的模型调用失败" if completion is None else "模型没有给出内容"
        repo.update_attachment(a.id, status="failed", error=error)
        log.warning("视频文字版生成失败")
        return
    text = completion.text.strip()
    if completion.truncated:
        text += "\n……（输出达到长度上限，以下内容缺失）"
    if heard:
        text += f"\n\n【声音转写】\n{heard}"
    repo.update_attachment(
        a.id, status="ready", text=scrubber.scrub(text, question), text_source="vision"
    )
