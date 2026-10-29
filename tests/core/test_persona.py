"""聊天人设：来源优先级、提示词段落；人设和记忆不进工作模式与办公室的提示词（中立性守卫）。"""

from __future__ import annotations

import json
import re

import pytest

from roundtable.core.config.schema import Persona
from roundtable.core.persona import EMPTY, is_empty, persona_of, persona_text, set_persona
from roundtable.core.service import RoundtableService

from .orchestrator.conftest import MEDIUM, Env

MARK_PERSONA = "独特性格标记XYZ"
MARK_MEMORY = "独特记忆标记QWE"


def test_sources_local_then_config_then_empty():
    env = Env()
    assert is_empty(persona_of(env.rt, "V1"))
    cfg = env.config.personas.model_copy(
        update={"personas": {"V1": Persona(personality="配置里的")}}
    )
    env.config.__dict__["personas"] = cfg  # 冻结模型：只在测试里替换
    assert persona_of(env.rt, "V1").personality == "配置里的"
    set_persona(env.rt, "V1", {"personality": "网页里改的", "catchphrases": [" 好的 ", ""]})
    p = persona_of(env.rt, "V1")
    assert p.personality == "网页里改的" and p.catchphrases == ["好的"]
    set_persona(env.rt, "V1", None)  # 恢复为配置文件里的
    assert persona_of(env.rt, "V1").personality == "配置里的"


def test_persona_text():
    assert persona_text(Persona()) == EMPTY
    text = persona_text(Persona(personality="开朗", speaking_style="短句", catchphrases=["好耶"]))
    assert text.splitlines() == [
        "性格：开朗",
        "说话风格：短句",
        "口头禅（偶尔用，别每句都用）：好耶",
    ]


def test_invalid_persona_is_rejected():
    env = Env()
    with pytest.raises(ValueError):
        set_persona(env.rt, "V1", {"personality": "x" * 301})
    with pytest.raises(ValueError):
        set_persona(env.rt, "V1", {"unknown": 1})


@pytest.mark.parametrize("workflow", ["discussion", "office", "solo", "collab"])
async def test_persona_and_memory_never_reach_work_prompts(workflow):
    env = Env(confirm_threshold_usd=100.0)
    svc = RoundtableService(env.rt)
    for m in svc.chats.everyone():
        set_persona(env.rt, m.id, {"personality": MARK_PERSONA, "interests": [MARK_PERSONA]})
        env.rt.repo.add_memory(m.id, "note", MARK_MEMORY, source="user")
        svc.engine.profiles.change(
            m.id, {"identity": {"随身卡": MARK_PERSONA}}, author="user", reason="测试"
        )
    sid = svc.create(MEDIUM, seed=3, workflow=workflow)
    async for _ in svc.events(sid):
        pass
    assert env.fake.calls
    sent = json.dumps([[m.content for m in c.messages] for c in env.fake.calls], ensure_ascii=False)
    assert MARK_PERSONA not in sent and MARK_MEMORY not in sent


WORK_PACKAGES = ("orchestrator", "steps", "office", "allocation", "routing", "prompts", "tools")


def test_work_mode_code_never_imports_persona_or_member_profile():
    """工作模式的代码不引入人设、自我档案、随身卡（2026-10-29 用户：工作与生活严格分开）。"""
    from pathlib import Path

    import roundtable.core as core

    root = Path(core.__file__).parent
    bad = re.compile(
        r"roundtable\.core\.(persona|member|memory|life|moments)\b|from \.\.(persona|member)\b"
    )
    hits = [
        str(p.relative_to(root))
        for pkg in WORK_PACKAGES
        for p in (root / pkg).rglob("*.py")
        if bad.search(p.read_text(encoding="utf-8"))
    ]
    assert hits == []


@pytest.mark.parametrize("workflow", ["discussion", "office", "solo", "collab", "brainstorm"])
async def test_imported_profile_never_reaches_work_prompts(workflow):
    """导入完整人物卡后，工作模式发给模型的每条消息都不含档案或随身卡的内容。"""
    from .member.test_card import CARDS

    env = Env(confirm_threshold_usd=100.0)
    svc = RoundtableService(env.rt)
    ids = [m.id for m in svc.chats.everyone()]
    for mid in ids:
        svc.member_profile_import(CARDS, {"甲": mid, "乙": ""})
    assert all(svc.member_profile(mid)["card"]["text"] for mid in ids)
    sid = svc.create(MEDIUM, seed=5, workflow=workflow)
    async for _ in svc.events(sid):
        pass
    assert env.fake.calls
    sent = json.dumps([[m.content for m in c.messages] for c in env.fake.calls], ensure_ascii=False)
    for mark in ("阿甲", "海边小镇", "认真记得小事", "尊重选择", "先留一页", "随身卡"):
        assert mark not in sent, mark
