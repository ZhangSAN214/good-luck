"""聊天人设：每位成员（厂商）一份，只用于私聊、群聊、朋友圈。

- 来源：网页「成员设定」存在本地数据库的优先，其次是 personas.yaml 的 ``personas``，都没有为空。
- 全部为空时提示词里写「没有设定」，成员用模型本身的说话风格。
- 工作模式与办公室的提示词不读取人设（tests/core/test_persona.py 守卫）。
"""

from __future__ import annotations

from typing import Any

from roundtable.core.config.schema import Persona

EMPTY = "（没有设定：用你自己的说话风格，口语化、短句，像朋友聊天。）"


def persona_of(rt: Any, member: str) -> Persona:
    stored = rt.repo.member_persona(member)
    if stored is not None:
        return Persona.model_validate(stored)
    return rt.config.personas.personas.get(member) or Persona()


def is_empty(persona: Persona) -> bool:
    return not any(
        (
            persona.personality.strip(),
            persona.speaking_style.strip(),
            persona.catchphrases,
            persona.interests,
        )
    )


def persona_text(persona: Persona) -> str:
    """写进聊天提示词的人设段落（放在 <persona> 标签内）。"""
    if is_empty(persona):
        return EMPTY
    lines = []
    if persona.personality.strip():
        lines.append(f"性格：{persona.personality.strip()}")
    if persona.speaking_style.strip():
        lines.append(f"说话风格：{persona.speaking_style.strip()}")
    if persona.catchphrases:
        lines.append(f"口头禅（偶尔用，别每句都用）：{'、'.join(persona.catchphrases)}")
    if persona.interests:
        lines.append(f"兴趣：{'、'.join(persona.interests)}")
    return "\n".join(lines)


def set_persona(rt: Any, member: str, data: dict[str, Any] | None) -> Persona:
    """保存网页里改的人设；data 为 None 时恢复为配置文件里的。返回生效的人设。"""
    if data is None:
        rt.repo.set_member_persona(member, None)
    else:
        persona = Persona.model_validate(data)
        clean = persona.model_copy(
            update={
                "catchphrases": [c.strip() for c in persona.catchphrases if c.strip()],
                "interests": [c.strip() for c in persona.interests if c.strip()],
            }
        )
        rt.repo.set_member_persona(member, clean.model_dump())
    return persona_of(rt, member)
