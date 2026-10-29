"""聊天人设：来源优先级、提示词段落；人设和记忆不进工作模式与办公室的提示词（中立性守卫）。"""

from __future__ import annotations

import json

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
