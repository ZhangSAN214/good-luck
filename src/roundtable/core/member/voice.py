"""P5 决定与表达·表达：每位成员照自己人物卡的「说话风格」说话（docs/MEMBER_ENGINE.md「说话风格」）。

- **说话习惯**（``voice_of``）：从自我档案 ``style`` 分区读出句子长短、语气词、口头禅、常用表情、
  自称和几句示范，``personality.五项`` 的外向分定说话模式档位（话少 / 平常 / 话多，借 dsh maid），
  幽默分定表情包频率。全部由程序抽，不调模型；档案改了跟着变。用户可在档案 ``style.说话模式``
  写 quiet / normal / chatty 直接指定档位。
- **写进提示词**（``voice_text``）：一小段「说话习惯」放在聊天提示词的 ``{{ voice }}``，
  8 位同一个字数上限（``chat.engine.voice.budget_chars``）。示范句每次轮换着带，只学语气不照抄。
- **节奏**（``VoicePolicy``）：实现 ``chat_actions.ActionPolicy``，按档位定最多拆几条、拆条间隔；
  由 ``RoundtableService`` 赋给 ``ChatHub.policy``。
- **说出口前自查**（``voice_hits`` / ``before_voice``）：和自己最近说的太像、和别人这一轮说的太像、
  光附和、口头禅用得太勤、档案里没有记忆却说「上次我们……」。群里没被点名时跟风 / 光附和的直接不发；
  其余命中才用成员自己的模型改写一次（``voice_rewrite``），改不好发原文。之后照常过说话底线
  （``plain_talk``）。

只用于生活群和私聊（工作群、讨论、办公室……都不挂载，测试守卫）。规则、字数、档位表对所有成员相同，
只有她自己的档案不同（中立）。本模块只读档案，不写任何数据。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

from roundtable.core.chat_actions import STICKER_RE, DefaultPolicy
from roundtable.core.plain_talk import Hit

LIFE_ROOMS = frozenset({"group", "dm"})
REWRITE_ROLE = "voice_rewrite"
REWRITE_STEP = "voice_rewrite"
MODES = ("quiet", "normal", "chatty")
DROP_RULES = frozenset({"echo", "agree"})  # 群里没被点名时，命中这些直接不发

_QUOTED = re.compile(r"[“「\"『]([^”」\"』]{1,40})[”」\"』]")
_EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿⭐⭕✨❤‼⁉]️?")
_SAMPLE = re.compile(r"\*{0,2}([^\s*：:“「]{1,6})\*{0,2}\s*[：:]\s*[“「\"]([^”」\"]{2,200})[”」\"]")
_NOT_SAMPLE = ("口头禅", "语气", "表情", "自称", "称呼", "句子")  # 这些标签后面的引号不是示范句
_PAREN = re.compile(r"[（(][^）)]*[）)]")


@dataclass(frozen=True)
class Voice:
    """一位成员的说话习惯（从档案抽出来的）。"""

    mode: str = "normal"
    length: str = ""  # 短 / 中 / 长；没写为空
    particles: tuple[str, ...] = ()
    catchphrases: tuple[str, ...] = ()
    emojis: tuple[str, ...] = ()
    self_name: str = ""
    humor: int = 3
    samples: tuple[str, ...] = field(default=())

    @property
    def empty(self) -> bool:
        return not (
            self.length or self.particles or self.catchphrases or self.emojis or self.samples
        )


# --- 从档案抽 ---------------------------------------------------------------------


def _sentence(text: str, key: str) -> str:
    """含 key 的那一句（到句号或换行为止）。"""
    m = re.search(re.escape(key) + r"(?:[^。\n]|。(?=[」”\"』]))*", text)
    return m.group(0) if m else ""


def _length(text: str) -> str:
    m = re.search(r".{0,8}句.{0,24}", text)
    seg = m.group(0) if m else ""
    for words, value in (
        (("中等偏长", "偏长", "长句"), "长"),
        (("中等偏短", "短中", "偏短", "短句", "短"), "短"),
        (("中等", "中长"), "中"),
    ):
        if any(w in seg for w in words):
            return value
    return ""


def _particles(text: str) -> tuple[str, ...]:
    seg = _sentence(text, "语气词")
    if not seg:  # 「熟了以后多用“嗯、呢、呀”」
        m = re.search(r"(?:多用|常用|爱用)[“「\"][^”」\"]{1,20}[”」\"]", text)
        seg = m.group(0) if m else ""
    if not seg:
        return ()
    quoted = _QUOTED.findall(seg)
    raw = quoted or [re.split(r"[：:]", _PAREN.sub("", seg), maxsplit=1)[-1]]
    out: list[str] = []
    for chunk in raw:
        for x in re.split(r"[、，,/]|\s+", chunk):
            x = x.strip(' *。.“”「」"')
            if x and len(x) <= 6 and "语气词" not in x and x not in out:
                out.append(x)
    return tuple(out[:4])


def _emojis(text: str) -> tuple[str, ...]:
    seg = _sentence(text, "表情")
    if not seg:
        return ()
    found = list(dict.fromkeys(m.group(0) for m in _EMOJI.finditer(seg)))
    if not found:  # 「表情少用：哼、斧、🦇」里也可能有字
        after = re.split(r"[：:]", seg, maxsplit=1)[-1]
        found = [x.strip(" 。*") for x in re.split(r"[、，,]", after) if 0 < len(x.strip()) <= 2]
    return tuple(found[:3])


def _samples(text: str, limit: int) -> tuple[str, ...]:
    out: list[str] = []
    start = max(text.find("示范"), 0)
    for m in _SAMPLE.finditer(text[start:]):
        if any(k in m.group(1) for k in _NOT_SAMPLE):
            continue
        line = m.group(2).strip()
        if len(line) <= limit and line not in out:
            out.append(line)
    return tuple(out)


def _mode(data: dict[str, Any], rules: Any) -> str:
    chosen = str((data.get("style") or {}).get("说话模式") or "").strip()
    if chosen in MODES:
        return chosen
    scores = (data.get("personality") or {}).get("五项") or {}
    extro = scores.get("外向")
    if not isinstance(extro, int):
        return "normal"
    if extro <= rules.quiet_max:
        return "quiet"
    if extro >= rules.chatty_min:
        return "chatty"
    return "normal"


def voice_of(data: dict[str, Any], rules: Any) -> Voice:
    """档案（``Profile.data``）→ 说话习惯。档案里没有说话风格时各项为空。"""
    style = dict(data.get("style") or {})
    raw = str(style.get("说话风格") or "")
    # 口头禅和其他项写在同一句里时，档案里抽的可能混进语气词：这句里有引号就以这句为准
    quoted = _QUOTED.findall(_sentence(raw, "口头禅"))
    phrases = quoted or style.get("口头禅") or []
    humor = ((data.get("personality") or {}).get("五项") or {}).get("幽默", 3)
    return Voice(
        mode=_mode(data, rules),
        length=_length(raw),
        particles=_particles(raw),
        catchphrases=tuple(str(p) for p in phrases if str(p).strip())[:3],
        emojis=_emojis(raw),
        self_name=str(style.get("自称") or "").strip(),
        humor=humor if isinstance(humor, int) else 3,
        samples=_samples(raw, rules.sample_chars),
    )


def member_voice(rt: Any, member: str) -> Voice:
    engine = rt.config.roundtable.chat.engine
    if not (engine.enabled and engine.voice.enabled):
        return Voice()
    row = rt.repo.member_profile(member)
    return voice_of(row["data"] if row and row["data"] else {}, engine.voice)


# --- 写进提示词 -------------------------------------------------------------------


def _sticker_habit(voice: Voice, rules: Any) -> str:
    habits = rules.sticker_habits
    if not habits:
        return ""
    i = 0 if voice.humor <= 2 else (1 if voice.humor == 3 else 2)
    return habits[min(i, len(habits) - 1)]


def voice_text(voice: Voice, rules: Any, room_kind: str, seed: int = 0) -> str:
    """提示词里「说话习惯」那一段（≤ budget_chars 字）。seed 决定这次带哪几句示范。"""
    mode = rules.modes.get(voice.mode)
    lines: list[str] = []
    if voice.length:
        cap = rules.lengths.get(voice.length)
        tail = f"，一条一般不超过 {cap} 字" if cap else ""
        word = {"短": "偏短", "中": "中等", "长": "偏长"}.get(voice.length, voice.length)
        lines.append(f"句子：{word}{tail}；可以只回几个字，不用每次都说完整")
    if mode is not None and room_kind == "group":
        lines.append(f"接话：{mode.reply}")
    if voice.particles:
        lines.append(f"语气词：{'、'.join(voice.particles)}")
    if voice.catchphrases:
        lines.append(f"口头禅（隔好几条才用一次）：{'、'.join(voice.catchphrases)}")
    habit = _sticker_habit(voice, rules)
    if voice.emojis or habit:
        emoji = f"常用 {''.join(voice.emojis)}；" if voice.emojis else ""
        lines.append(f"表情：{emoji}{habit}".rstrip("；"))
    if voice.self_name:
        lines.append(f"自称：{voice.self_name}")
    if voice.samples and rules.samples:
        picks = random.Random(seed).sample(
            list(voice.samples), min(rules.samples, len(voice.samples))
        )
        lines.append("你平时这样说话（只学语气，别照抄）：" + " / ".join(picks))
    out: list[str] = []
    for ln in lines:  # 超出字数时从后往前丢（示范句最先丢）
        if len("\n".join([*out, ln])) > rules.budget_chars:
            break
        out.append(ln)
    return "\n".join(out)


# --- 节奏 -------------------------------------------------------------------------


class VoicePolicy(DefaultPolicy):
    """成员系统接管聊天节奏：按每位成员的说话模式档位（所有成员同一张档位表）。"""

    @property
    def voice_rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.voice

    def _mode(self, member: str) -> Any:
        return self.voice_rules.modes.get(member_voice(self.rt, member).mode)

    def max_segments(self, member: str, room_kind: str) -> int:
        base = super().max_segments(member, room_kind)
        mode = self._mode(member) if room_kind in LIFE_ROOMS else None
        return base if base <= 1 or mode is None else mode.max_segments

    def segment_gap(self, member: str, text: str) -> float:
        mode = self._mode(member)
        base = super().segment_gap(member, text)
        return base * mode.gap_scale if mode is not None else base

    def voice(self, member: str, room_kind: str, seed: int = 0) -> str:
        if room_kind not in LIFE_ROOMS:
            return ""
        voice = member_voice(self.rt, member)
        return "" if voice.empty else voice_text(voice, self.voice_rules, room_kind, seed)


# --- 说出口前自查 -----------------------------------------------------------------


@dataclass(frozen=True)
class Said:
    """自查要比对的话：她自己最近说的、别人这一轮说的。"""

    own: tuple[str, ...] = ()
    others: tuple[tuple[str, str], ...] = ()  # (名字, 内容)
    has_memory: bool = False


def _bare(text: str) -> str:
    return STICKER_RE.sub("", text).strip()


def voice_hits(text: str, said: Said, voice: Voice, rules: Any) -> list[Hit]:
    """按规则查一条要发的话（rules：``chat.engine.voice.check``）。纯函数，不调用模型。"""
    from roundtable.core.moments import similarity  # moments 依赖 chat，chat 用到本模块

    bare = _bare(text)
    if not rules.enabled or len(bare) < rules.min_chars:
        return []
    hits: list[Hit] = []
    for old in said.own[-rules.own_recent :]:
        if similarity(bare, _bare(old)) >= rules.repeat_similarity:
            hits.append(Hit("repeat", "和你自己刚才说过的话太像，换个说法或者说点新的", old[:60]))
            break
    for who, other in said.others:
        if similarity(bare, _bare(other)) >= rules.echo_similarity:
            hits.append(
                Hit("echo", f"和{who}刚说的差不多，别跟风：说你自己的看法，或者不接", other[:60])
            )
            break
    for pattern in rules.agree_patterns:
        m = re.match(pattern, bare)
        if m and len(bare[m.end() :].strip()) < rules.agree_rest_chars:
            hits.append(Hit("agree", "只是在附和，没说自己的看法", bare[:60]))
            break
    recent = said.own[-rules.catchphrase_recent :]
    for phrase in voice.catchphrases:
        key = phrase.rstrip("。.！!？?…")
        if key and key in bare and any(key in old for old in recent):
            hits.append(Hit("catchphrase", f"口头禅「{key}」刚用过，这次别用", key))
            break
    if not said.has_memory:
        for pattern in rules.memory_patterns:
            m = re.search(pattern, bare)
            if m:
                hits.append(Hit("memory", "提到了你记忆里没有的共同经历，别编", m.group(0)))
                break
    return hits


def should_drop(hits: list[Hit], room_kind: str, named: bool) -> bool:
    """群里没被点名、只是跟风或附和：这条不发（像真人一样不接）。"""
    return (
        room_kind == "group" and not named and bool(hits) and {h.rule for h in hits} <= DROP_RULES
    )


def rewrite_spent(rt: Any) -> float:
    from roundtable.core.budget.guard import month_window

    start, end = month_window(rt.budget.now())
    return rt.repo.spent_in_step(REWRITE_STEP, start, end)


async def rewrite_once(
    rt: Any, text: str, hits: list[Hit], *, room: str, name: str, model_id: str, voice: str
) -> str | None:
    """用这位成员自己的聊天档模型改写一次；超出月上限、失败或被截断返回 None。"""
    from roundtable.core.budget.guard import EPS
    from roundtable.core.chat import call_member, clean_reply  # chat 依赖本模块
    from roundtable.core.plain_talk import describe

    rules = rt.config.roundtable.chat.engine.voice.check
    if rewrite_spent(rt) + rules.estimate_usd > rules.monthly_usd + EPS:
        return None
    prompt = rt.prompts.render(
        REWRITE_ROLE,
        rt.config.roundtable.prompts[REWRITE_ROLE],
        name=name,
        voice=_neutral(voice or "（没写）"),
        problems=_neutral(describe(hits)),
        draft=_neutral(text),
    )
    params = dict(rt.config.roundtable.step_params.get("chat", {}))
    params["max_tokens"] = rules.max_tokens
    completion = await call_member(
        rt,
        room=room,
        model_id=model_id,
        prompt=prompt,
        step=REWRITE_STEP,
        params=params,
        estimate_usd=rules.estimate_usd,
    )
    if completion is None or completion.finish_reason == "length":
        return None
    return clean_reply(completion.text, name, rt.config.roundtable.chat.max_chars) or None


async def before_voice(
    rt: Any,
    text: str,
    said: Said,
    *,
    member: str,
    room: str,
    room_kind: str,
    named: bool,
    name: str,
    model_id: str,
    voice: str = "",
) -> str:
    """说出口前自查。返回要发的话；空串表示这条不发。关闭或不是生活群 / 私聊时原样返回。"""
    engine = rt.config.roundtable.chat.engine
    if room_kind not in LIFE_ROOMS or not (engine.enabled and engine.voice.enabled):
        return text
    rules = engine.voice.check
    profile = member_voice(rt, member)
    hits = voice_hits(text, said, profile, rules)
    if not hits:
        return text
    if should_drop(hits, room_kind, named):
        return ""
    better = await rewrite_once(
        rt, text, hits, room=room, name=name, model_id=model_id, voice=voice
    )
    if better and len(voice_hits(better, said, profile, rules)) < len(hits):
        return better
    return text


def _neutral(text: str) -> str:
    for tag in ("draft", "problems", "voice"):
        text = text.replace(f"</{tag}", f"<​/{tag}")
    return text
