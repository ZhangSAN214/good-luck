"""聊天人设：每位成员（厂商）一份，只用于私聊、群聊、朋友圈。

- 来源：网页「成员设定」存在本地数据库的优先，其次是 personas.yaml 的 ``personas``，都没有为空。
- 全部为空时提示词里写「没有设定」，成员用模型本身的说话风格。
- 工作模式与办公室的提示词不读取人设（tests/core/test_persona.py 守卫）。
- 成员有了自我档案（人物卡导入后，docs/MEMBER_ENGINE.md）时，提示词里用档案的随身卡
  （经上下文组装器，总长 ``chat.engine.context.budget_chars``），这里的简单人设不再进提示词。
"""

from __future__ import annotations

from typing import Any

from roundtable.core.config.schema import Persona
from roundtable.core.member.card import pocket_card
from roundtable.core.member.context import Piece, assemble
from roundtable.core.member.relation import SOURCE as RELATION
from roundtable.core.member.relation import RelationPlugin
from roundtable.core.member.state import SOURCE as STATE
from roundtable.core.member.state import StatePlugin

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


def card_text(rt: Any, member: str) -> str:
    """这位成员自我档案的随身卡；没有档案（或引擎关着）时为空串。"""
    engine = rt.config.roundtable.chat.engine
    if not engine.enabled:
        return ""
    row = rt.repo.member_profile(member)
    if row is None or not row["data"]:
        return ""
    return pocket_card(row["data"], engine.card.budget_chars)


def habit_text(rt: Any, member: str) -> str:
    """记忆插件由全部经历（不只最近）算出的「常做的事」（每晚重算）；没有时为空串。"""
    memory = rt.config.roundtable.chat.memory
    if not memory.enabled or memory.digest_chars == 0:
        return ""
    row = rt.repo.memory_digest(member)
    return row["text"] if row else ""


def chat_persona(rt: Any, member: str) -> str:
    """私聊、群聊、朋友圈、生活提示词里 <persona> 的内容：有随身卡用随身卡，否则用简单人设；
    后面接全量经历的习惯（固定前缀，每晚才变，利于缓存）。"""
    card = card_text(rt, member)
    habit = habit_text(rt, member)
    if not card:
        base = persona_text(persona_of(rt, member))
        return f"{base}\n{habit}" if habit else base
    r = rt.config.roundtable.chat.engine.context
    pieces = [Piece("card", card, fixed=True)]
    if habit:
        pieces.append(Piece("habit", habit, fixed=True))
    state = StatePlugin(rt).line(member)  # P3：程序算出的此刻心情和精力（放在随身卡后面）
    if state:
        pieces.append(Piece(STATE, state, score=1.0))
    relation = RelationPlugin(rt).line(member)  # P4：和你到了哪一步（程序按共同经历算）
    if relation:
        pieces.append(Piece(RELATION, relation, score=0.9))
    from roundtable.core.member.growth.book import recent_text  # 成长总账依赖记忆、关系等模块

    recent = recent_text(rt, member)  # P6：这阵子她自己变了什么（口癖换了、和你熟了……）
    if recent:
        pieces.append(Piece("growth", recent, score=0.5))
    return assemble(pieces, r.budget_chars, r.per_source, r.min_piece_chars).text
