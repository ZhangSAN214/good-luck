"""连着几条的分寸（docs/BRAIN.md「连着几条的分寸」，2026-11-01 用户批「按推荐进行」）。

收尾（``lingo.finish``）原来只看当前这一条，小克炒股那段私聊暴露的毛病都出在「连着几条」上：
☕ 34 条里贴了 10 次、「呢」25 条都有、「不过说实话」连用、每轮都反问「你呢」；另外聊天里
冒出列表、加粗、表格、破折号、「不是 X，而是 Y」，以及「我的数据更新到……」「摘要里没说」
这类一听就是程序在说话的句子。这里全部由程序处理，不调模型：

- **格式**（``plain``）：去掉 Markdown（标题井号、加粗、列表符号、代码框、分隔线、表格竖线），
  破折号换成逗号，「【标题】」换成「标题：」。微信里没人这么打字。
- **句式**（``not_but``）：「不是 X，而是 Y」→「不是 X，是 Y」（口语里本来就这么说）。
- **露底**（``leaks``）：含「数据更新到」「训练数据」「摘要」「新闻源」这类词的那一小句删掉；
  「用户」换成「你」（来自提示词抬头的说法）。删光了就整条不发。被认真问是不是 AI 时的承认
  不在这里管（那几个词不在名单里）。
- **冷却**（``cool``）：她最近几条（各个会话合起来）里已经用过两次的 emoji / 颜文字这条不再贴；
  最近几条里「呢」结尾超过一半，这条句尾的「呢」去掉（问句里的不动）；口头填充词
  （「说实话」「不得不说」……）最近几条用过就这条不说；最近连着几条都在反问，这条末尾的
  「你呢？」这类反问去掉（整条只有这一问时不动）。

8 位同一套规则，只看她自己说过的话（中立）。只用于生活群、私聊、成员互聊和朋友圈评论。
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Sequence
from typing import Any

_EMOJI = re.compile(r"[\U0001f300-\U0001faff☀-➿]️?")
_CLAUSE = re.compile(r"[^，,。！!？?～~…\n]+[，,。！!？?～~…]*")
_STICKER = re.compile(r"\[表情[:：][^\]]*\]")
_YOU_NE = re.compile(r"^\s*(?:那)?你呢[，,]?\s*$")

# --- 格式 -------------------------------------------------------------------------------

_FENCE = re.compile(r"^\s*```[^\n]*$", re.M)
_HEAD = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,}|={3,})\s*$", re.M)
_BULLET = re.compile(r"^\s*(?:[-*+•·]|\d{1,2}[.、)）])\s+", re.M)
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC = re.compile(r"(?<![*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![*\w])")
_CODE = re.compile(r"`([^`\n]+)`")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
_BRACKET_HEAD = re.compile(r"^\s*【([^】\n]{1,12})】\s*", re.M)
_DASH = re.compile(r"\s*(?:——|—{1,2}|--)\s*")


def plain(text: str) -> str:
    """把 Markdown 和书面排版换成发微信的样子。"""
    if not text:
        return text
    out = _FENCE.sub("", text)
    lines = []
    for line in out.split("\n"):
        if _TABLE_SEP.match(line) and "-" in line:
            continue
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 3:
            cells = [c.strip() for c in stripped.strip("|").split("|") if c.strip()]
            line = "，".join(cells)
        lines.append(line)
    out = "\n".join(lines)
    out = _RULE.sub("", out)
    out = _HEAD.sub("", out)
    out = _BULLET.sub("", out)
    out = _BOLD.sub(r"\2", out)
    out = _ITALIC.sub(r"\1", out)
    out = _CODE.sub(r"\1", out)
    out = _BRACKET_HEAD.sub(r"\1：", out)
    out = _DASH.sub("，", out)
    out = re.sub(r"，+", "，", out)
    out = re.sub(r"，(?=[。！!？?…～~，,]|$)", "", out, flags=re.M)
    out = re.sub(r"^，", "", out, flags=re.M)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


_NOT_BUT = re.compile(r"(不是[^，,。！!？?\n]{1,24})([，,])\s*而是")


def not_but(text: str) -> str:
    """「不是 X，而是 Y」→「不是 X，是 Y」。"""
    return _NOT_BUT.sub(r"\1\2是", text)


# --- 小句工具 ---------------------------------------------------------------------------


def _clauses(line: str) -> list[str]:
    return _CLAUSE.findall(line) or ([line] if line else [])


def _join(parts: list[str]) -> str:
    out = "".join(parts).strip()
    out = re.sub(r"^[，,、\s]+", "", out)
    return re.sub(r"[，,、]+$", "。", out) if out and out[-1] in "，,、" else out


def drop_clauses(text: str, hit: Any) -> str:
    """删掉命中 ``hit(clause)`` 的那几小句；表情包记号所在的小句不删。"""
    lines = []
    for line in text.split("\n"):
        parts = [c for c in _clauses(line) if _STICKER.search(c) or not hit(c)]
        lines.append(_join(parts))
    return "\n".join(x for x in lines if x.strip()).strip()


# --- 冷却 -------------------------------------------------------------------------------


def faces_in(text: str, kaomoji: Iterable[str]) -> list[str]:
    """一条里的 emoji 和颜文字（按出现先后去重）。"""
    found: list[str] = []
    for k in sorted(set(kaomoji), key=len, reverse=True):
        if k and k in text and k not in found:
            found.append(k)
    for m in _EMOJI.finditer(text):
        e = m.group(0).rstrip("️")
        if e not in found:
            found.append(e)
    return found


def _ends_with_particle(text: str, particle: str) -> bool:
    """有没有哪一小句是「……呢」收尾的陈述（问句「你呢？」不算）。"""
    body = _STICKER.sub("", text)
    pat = rf"(?<![你我他她那]){re.escape(particle)}(?=[。.！!～~…，,\s]|$)"
    return bool(re.search(pat, body))


def _asks(text: str) -> bool:
    body = _STICKER.sub("", text).strip()
    body = _EMOJI.sub("", body).rstrip()
    return body.endswith(("？", "?"))


class Cadence:
    """连着几条的分寸。``rules`` 是 ``chat.engine.lingo.cadence``。"""

    def __init__(self, rules: Any) -> None:
        self.rules = rules
        self._leaks = [re.compile(p) for p in rules.leaks]
        self._back = [re.compile(p) for p in rules.ask_back]

    def apply(
        self,
        text: str,
        recent: Sequence[str] = (),
        *,
        kaomoji: Iterable[str] = (),
    ) -> str:
        """发出前过一遍；``recent`` 是她最近说过的话（旧的在前）。返回空串表示整条不发。"""
        out = self.prepare(text)
        return self.after(out, recent, kaomoji=kaomoji) if out.strip() else out

    def prepare(self, text: str) -> str:
        """格式、句式、露底（收尾开头调用）。返回空串表示整条不发。"""
        r = self.rules
        if not r.enabled or not text.strip():
            return text
        out = plain(text) if r.plain else text
        out = not_but(out) if r.not_but else out
        return self.unleak(out)

    def after(self, text: str, recent: Sequence[str] = (), *, kaomoji: Iterable[str] = ()) -> str:
        """跨条冷却（收尾最后调用，别的步骤补上的颜文字也算）。"""
        r = self.rules
        if not r.enabled or not text.strip():
            return text
        window = [t for t in recent if t and t.strip()][-r.window :]
        return self.cool(text, window, kaomoji) if window else text

    def unleak(self, text: str) -> str:
        out = text
        for word, to in self.rules.swap.items():
            out = re.sub(word, to, out)
        if self._leaks:
            out = drop_clauses(out, lambda c: any(p.search(c) for p in self._leaks))
        return out

    def cool(self, text: str, window: list[str], kaomoji: Iterable[str]) -> str:
        r = self.rules
        kao = tuple(kaomoji)
        out = text
        # emoji / 颜文字：最近几条里用过 face_max 次的这条不再贴
        for face in faces_in(out, kao):
            used = sum(1 for t in window if face in t)
            if used >= r.face_max:
                out = out.replace(face + "️", "").replace(face, "")
        # 句尾语气词：最近几条里超过 particle_share 都这么收尾的，这条去掉
        for p in r.particles:
            share = sum(1 for t in window if _ends_with_particle(t, p)) / len(window)
            if share >= r.particle_share and _ends_with_particle(out, p):
                out = re.sub(
                    rf"(?<=[一-鿿])(?<![你我他她那]){re.escape(p)}(?=[。.！!～~…，,\s]|$)",
                    "",
                    out,
                    flags=re.M,
                )
        # 口头填充词：最近几条说过就这条不说
        for w in r.fillers:
            if w in out and any(w in t for t in window):
                out = re.sub(rf"{re.escape(w)}[，,：:]?\s*", "", out, count=1)
        # 每轮都反问：最近几条里 ask_streak 条都以问号收尾，这条末尾的反问去掉
        tail = window[-r.ask_streak :]
        streak = len(tail) >= r.ask_streak and all(_asks(t) for t in tail)
        often = sum(1 for t in window if "你呢" in t) >= r.ask_streak  # 「你呢」最近问过好几回
        if (streak or often) and self._back:
            out = self._drop_back(out)
        out = re.sub(r"[ \t]+\n", "\n", out)
        out = re.sub(r"(?<=[。！!？?])[，,]", "", out)
        return out.strip() or text

    def _drop_back(self, text: str) -> str:
        lines = text.split("\n")
        for n in range(len(lines) - 1, -1, -1):
            parts = _clauses(lines[n])
            if not parts:
                continue
            last = parts[-1]
            if any(p.search(last) for p in self._back):
                keep = parts[:-1]
                if keep and _YOU_NE.match(keep[-1]):  # 「你呢，有特别喜欢的吗？」整句一起去
                    keep = keep[:-1]
                rest = _join(keep)
                others = "\n".join(lines[:n]).strip()
                if not rest and not others:
                    return text  # 整条只有这一问：不动
                lines[n] = rest
            break
        return "\n".join(x for x in lines if x.strip())


_STARTS_SOFT = re.compile(
    r"^(?:[嗯啊哦噢诶欸额呃哈嘿唔哇咦呀哎唉害]|好|行|对|那|就|不是|算了|可以|说起来|等下)"
)


def openers_of(rules: Any, member: str) -> list[str]:
    """她分到的过渡词（按 id 稳定地分）。"""
    pool = list(rules.openers.pool)
    if not pool:
        return []
    return random.Random(f"opener:{member}").sample(pool, min(rules.openers.each, len(pool)))


def with_opener(
    rules: Any, member: str, text: str, recent: Sequence[str], rng: random.Random
) -> str:
    """较长的话按概率在开头放一个她的过渡词（「怎么说呢，……」）；最近几条用过、已经有了、
    开头本来就是语气词、或是表情包时不放。"""
    o = rules.openers
    mine = openers_of(rules, member)
    body = text.strip()
    if not rules.enabled or not mine or not o.chance or len(body) < o.min_chars:
        return text
    if _STICKER.match(body) or _STARTS_SOFT.match(body):
        return text
    window = [t for t in recent if t][-rules.window :]
    if any(w in t for w in mine for t in window) or any(w in body for w in mine):
        return text
    if rng.random() >= o.chance:
        return text
    w = rng.choice(mine)
    sep = "" if w.endswith(("…", "。")) else "，"
    return f"{w}{sep}{body}"


def apply(rules: Any, text: str, recent: Sequence[str] = (), kaomoji: Iterable[str] = ()) -> str:
    return Cadence(rules).apply(text, recent, kaomoji=kaomoji)
