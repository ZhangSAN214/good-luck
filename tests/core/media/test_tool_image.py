"""generate_image 工具：按模型配置走对话接口或 /images 接口，一个模型失败后换同档的其他模型。"""

from __future__ import annotations

import pytest

from roundtable.core.attachments import FileStore
from roundtable.core.providers import Media, RawCompletion
from roundtable.core.providers.fake import FAKE_PNG
from roundtable.core.tools import ToolBox, ToolRequest

from .conftest import Rig

CHAT_MODEL = "google/gemini-3.1-flash-image"  # 对话接口
IMAGES_MODEL = "openai/gpt-image-1-mini"  # 专门的图像接口


def box_for(rig: Rig) -> ToolBox:
    return ToolBox(
        session_id=rig.sid,
        table_no=0,
        config=rig.config,
        router=rig.rt.router,
        prompts=rig.rt.prompts,
        repo=rig.rt.repo,
        store=FileStore(None),
        scrubber=rig.rt.scrubber,
        media_tier="budget",
    )


async def draw(box: ToolBox, **attrs):
    return await box.execute(
        ToolRequest("generate_image", {"path": "a.png", **attrs}, "一只猫"),
        step="answer",
        code="甲",
        round_no=1,
        call_id=None,
        allowed=["generate_image"],
    )


def used_models(rig: Rig) -> list[str]:
    chat = [c.model for c in rig.fake.calls]
    images = [c[1] for c in rig.fake.media_calls if c[0] == "image"]
    return chat + images


@pytest.fixture
def rig() -> Rig:
    return Rig()


def queue_chat_image(rig: Rig) -> None:
    out = RawCompletion("", images=(Media("image", "image/png", FAKE_PNG),))
    rig.fake.queue(CHAT_MODEL, out, out, out)


async def test_each_model_uses_the_interface_it_is_configured_for(rig: Rig):
    queue_chat_image(rig)
    box = box_for(rig)
    try:
        first = box.image_model()
        assert (await draw(box)).status == "ok"
        if first.image_api == "chat":
            assert rig.fake.calls and not rig.fake.media_calls
        else:
            assert rig.fake.media_calls and rig.fake.media_calls[0][5] == "images"
    finally:
        box.close()


async def test_gpt_image_models_use_the_images_interface(rig: Rig):
    box = box_for(rig)
    try:
        rig.fake.broken_models = {CHAT_MODEL}  # 即使对话模型不可用，也有 /images 模型接替
        result = await draw(box)
        assert result.status == "ok"
        calls = [c for c in rig.fake.media_calls if c[0] == "image" and c[1] == IMAGES_MODEL]
        assert calls and all(c[5] == "images" for c in calls)
    finally:
        box.close()


async def test_a_failing_model_is_replaced_by_the_other_one_of_the_tier(rig: Rig):
    box = box_for(rig)
    try:
        queue_chat_image(rig)
        first = box.image_model()
        rig.fake.broken_models = {r.model for r in first.routes} - {CHAT_MODEL}
        result = await draw(box)
        assert result.status == "ok", used_models(rig)
        if first.id != "gemini-3.1-flash-image":
            assert len(used_models(rig)) >= 2  # 第一个失败后又试了别的
        rows = rig.rt.repo.conn.execute("SELECT status FROM tool_calls").fetchall()
        assert [r["status"] for r in rows] == ["ok"]
    finally:
        box.close()


async def test_when_every_image_model_fails_the_tool_reports_an_error(rig: Rig):
    box = box_for(rig)
    try:
        rig.fake.broken_models = {
            r.model for m in rig.config.models.models if "image_gen" in m.tags for r in m.routes
        }
        result = await draw(box)
        assert result.status == "error" and "图像生成失败" in result.text
    finally:
        box.close()
