"""人物卡 → 自我档案，自我档案 → 随身卡（M1 自我认知，docs/MEMBER_ENGINE.md）。

- **读卡**：人物卡是一份 Markdown（八位成员同一个模板，13 章）。每位成员一节，标题
  ``## <名字> · 完整角色卡``，章节标题 ``### <序号>. <章名>``。按章名里的关键词分进档案的分区：
  基本信息 / 身世 / 人际关系 / 二创 / 外观 → ``identity``；性格 / 情绪 → ``personality``；
  三观 / 成长 / 底线 → ``values``；说话风格 → ``style``；喜好 → ``likes``；日常 → ``state``。
  每章原文整段保留（以后的模块要用），另外抽几项短的（名字、小名、房号、五项性格分、长处、
  小毛病、最看重、口头禅、自称、最爱、讨厌、底线要点），给随身卡和网页用。
- **对应成员**：卡的标题名和成员的显示名一样就对上（不区分大小写）；对不上的由用户在网页上选。
- **随身卡**：每次调模型都带的那一小段（默认 ≤ 400 字，放进上下文组装器的固定前缀）。
  从档案的短项拼出来，不调模型、可复现；档案改了随身卡跟着变。用户在 ``identity.随身卡``
  写了自己的版本时直接用用户的。
- 所有成员同一套规则（抽取、拼法、字数上限），与是哪个模型无关。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CARD_TITLE = re.compile(r"^##\s+(.+?)\s*[·・]\s*完整角色卡\s*$", re.M)
CHAPTER = re.compile(r"^###\s*(\d{1,2})\s*[.．、]\s*(.+?)\s*$", re.M)
FOOTER = re.compile(r"^(模型：|---\s*$)", re.M)  # 每张卡末尾的生成记录
SCORE_TABLE_ROW = re.compile(  # 文件开头总表：| 名字 | … | 3/4/5/4/3 | … |
    r"^\|\s*([^|]+?)\s*\|.*?\|\s*" + r"\s*/\s*".join([r"(\d)"] * 5) + r"\s*\|"
)
BOLD = re.compile(r"\*\*(.+?)\*\*")
QUOTED = re.compile(r"[“「\"]([^”」\"]{1,40})[”」\"]")
ROOM = re.compile(r"(\d{1,3})\s*号房")
TRAITS = ("外向", "较真", "好奇", "温和", "幽默")
OVERRIDE = "随身卡"  # identity.随身卡：用户自己写的随身卡

# 章名关键词 → (分区, 键)。按顺序匹配，第一个命中的算。
CHAPTERS: list[tuple[str, str, str]] = [
    ("基本信息", "identity", "基本信息"),
    ("身世", "identity", "身世"),
    ("人际", "identity", "人际关系"),
    ("二创", "identity", "二创"),
    ("外观", "identity", "外观"),
    ("性格", "personality", "性格"),
    ("情绪", "personality", "情绪"),
    ("三观", "values", "三观"),
    ("成长", "values", "成长"),
    ("底线", "values", "底线"),
    ("说话", "style", "说话风格"),
    ("喜好", "likes", "喜好与讨厌"),
    ("日常", "state", "日常"),
]

# 基本信息里的字段：标签关键词 → 档案键（identity）
FIELDS: list[tuple[str, str]] = [
    ("小名", "小名"),
    ("名字", "名字"),
    ("年龄", "年龄感"),
    ("身高", "身高体型"),
    ("住", "住处"),
    ("介绍", "自我介绍"),
    ("打招呼", "自我介绍"),
]


@dataclass
class Card:
    """一位成员的人物卡（解析后）。"""

    name: str
    chapters: dict[str, str] = field(default_factory=dict)  # 章名 → 原文
    scores: dict[str, int] = field(default_factory=dict)  # 文件开头总表里的五项分（备用）

    def chapter(self, key: str) -> str:
        for title, text in self.chapters.items():
            if key in title:
                return text
        return ""


# --- 小工具 -----------------------------------------------------------------------


def _clean(text: str) -> str:
    """去掉 Markdown 记号，压成一行。"""
    text = re.sub(r"\*\*|__|`", "", text)
    text = re.sub(r"^\s*(?:[-*>]|\d+[.、)])\s*", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


PAIRS = {"“": "”", "「": "」", '"': '"', "『": "』"}


def _short(text: str, limit: int) -> str:
    text = _clean(text)
    if len(text) >= 2 and PAIRS.get(text[0]) == text[-1]:  # 整句带引号的去掉引号
        text = text[1:-1].strip()
    for a, b in PAIRS.items():  # 截断后只剩半边引号的补上
        if a != b and text.count(a) > text.count(b):
            text += b
    return text if len(text) <= limit else text[: limit - 1].rstrip("，、；： ") + "…"


def _first_sentence(text: str, limit: int) -> str:
    m = re.match(r"(.+?[。！？!?])", _clean(text))
    return _short(m.group(1) if m else text, limit)


def _phrase(text: str, limit: int = 16) -> str:
    """一项的名字：截到第一个逗号、冒号、括号或破折号前。"""
    text = _clean(text)
    text = re.split(r"[，,：:（(——;；。]|\s-\s", text, maxsplit=1)[0]
    return _short(text, limit)


def _lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


def _region(text: str, start: str, stop: str) -> list[str]:
    """从含 start 的那一行起，到下一行含 stop（或下一个小标题）前为止的几行。"""
    out: list[str] = []
    inside = False
    for ln in _lines(text):
        if not inside:
            if re.search(start, ln):
                inside = True
                out.append(ln)
            continue
        if re.search(stop, ln) or ln.lstrip().startswith("###"):
            break
        out.append(ln)
    return out


LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+[.、)])\s*")
ORDINAL = re.compile(r"第[一二三四五1-5][，,、：:]\s*([^。；;\n]+)")


def _items(lines: list[str], limit: int = 3, prefer_rest: bool = False) -> list[str]:
    """小标题下的几项：列表项（有加粗取加粗）、行首加粗，或「第一，……第二，……」。

    第一行是小标题本身，跳过。prefer_rest：加粗只是个短标题（≤6 字）时取冒号后面那句。
    """
    out: list[str] = []

    def add(item: str) -> None:
        if item and item not in out and len(out) < limit:
            out.append(item)

    for ln in lines[1:]:
        bold = re.match(r"^\s*(?:[-*]|\d+[.、)])?\s*\*\*(.+?)\*\*\s*[：:]?\s*(.*)$", ln)
        if bold:
            head, rest = bold.group(1).rstrip("：: "), bold.group(2)
            if prefer_rest and len(head) <= 6 and rest.strip():
                add(_short(_first_sentence(rest, 40).rstrip("。"), 40))
            else:
                add(_phrase(head, 20))
        elif LIST_ITEM.match(ln) and not ORDINAL.search(ln):
            add(_phrase(LIST_ITEM.sub("", ln), 20))
    if not out:
        for m in ORDINAL.finditer("\n".join(lines)):
            add(_phrase(m.group(1), 20))
    return out


# --- 读卡 -------------------------------------------------------------------------


def parse_cards(text: str) -> list[Card]:
    """把整份人物卡文件拆成每位成员一张。"""
    text = text.replace("\r\n", "\n").lstrip("﻿")
    scores: dict[str, dict[str, int]] = {}
    for ln in text.splitlines():
        m = SCORE_TABLE_ROW.match(ln)
        if m:
            scores[m.group(1).strip()] = {
                t: int(v) for t, v in zip(TRAITS, m.groups()[1:], strict=True)
            }
    titles = list(CARD_TITLE.finditer(text))
    cards: list[Card] = []
    for i, m in enumerate(titles):
        body = text[m.end() : titles[i + 1].start() if i + 1 < len(titles) else len(text)]
        name = _clean(m.group(1))
        card = Card(name, scores=scores.get(name, {}))
        heads = list(CHAPTER.finditer(body))
        for j, h in enumerate(heads):
            chunk = body[h.end() : heads[j + 1].start() if j + 1 < len(heads) else len(body)]
            if j + 1 == len(heads):
                f = FOOTER.search(chunk)
                chunk = chunk[: f.start()] if f else chunk
            # 去掉章内多余的分隔线
            chunk = re.sub(r"^---\s*$", "", chunk, flags=re.M).strip()
            card.chapters[_clean(h.group(2))] = chunk
        cards.append(card)
    return cards


LABEL = re.compile(r"(?:^|(?<=[。；]))\s*([^：:。，,；“”「」]{1,16})[：:]")


def _basic_fields(text: str) -> dict[str, str]:
    """「标签：内容」（标签可加粗；一行里可以有几项，如「名字：Grok。小名：大小姐。」）。"""
    out: dict[str, str] = {}
    lines = [_clean(ln) for ln in _lines(text)]
    for i, ln in enumerate(lines):
        marks = list(LABEL.finditer(ln))
        for j, m in enumerate(marks):
            key = next((k for kw, k in FIELDS if kw in m.group(1)), None)
            if key is None or key in out:
                continue
            end = marks[j + 1].start() if j + 1 < len(marks) else len(ln)
            value = ln[m.end() : end].strip()
            last = j + 1 == len(marks) and i + 1 < len(lines)
            if not value and last and not LABEL.match(lines[i + 1]):
                value = lines[i + 1]
            if value:
                out[key] = value
    for key in ("名字", "小名"):
        if key in out:
            out[key] = _phrase(out[key], 16)
    return out


def _scores(text: str, fallback: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in TRAITS:
        m = re.search(t + r"[\s*：:]{0,4}(\d)\s*(?:[／/]\s*5|分|[。：:，,*\s]|$)", text, re.M)
        if m:
            out[t] = int(m.group(1))
    return out if len(out) == len(TRAITS) else dict(fallback)


def _catchphrases(text: str) -> list[str]:
    lines = _region(text, r"口头禅", r"自称|表情|语气词|称呼|示范|句子")
    found: list[str] = []
    for ln in lines:
        for q in QUOTED.findall(ln):
            q = _short(q, 20)
            if q and q not in found:
                found.append(q)
    if not found and lines:  # 「口头禅：我先记一笔；先问清楚；不急。」
        after = re.split(r"[：:]", _clean(lines[0]), maxsplit=1)[-1]
        found = [_short(x, 20) for x in re.split(r"[；;]", after) if x.strip()]
    return found[:3]


def _self_name(text: str) -> str:
    for ln in _lines(text):
        for m in re.finditer("自称", ln):
            seg = re.sub(r"^(?:[与和]称呼[^：:]*)?[*\s]*[：:]?[*\s]*", "", ln[m.end() :])
            seg = re.split(r"称呼别人", seg)[0].strip()
            if seg:
                return _first_sentence(seg, 30)
    return ""


STAR_ITEM = re.compile(r"((?<!★)★{5}(?!☆)|(?<![\d.])5\s*星)")


def _favorites(text: str, limit: int = 6) -> list[str]:
    """喜欢的里面五星的几项（不够三项时补四星的）。"""

    def collect(pattern: re.Pattern[str]) -> list[str]:
        out: list[str] = []
        likes = re.split(r"讨厌", text, maxsplit=1)[0]
        for ln in _lines(likes):
            if ln.lstrip().startswith("|") and ln.count("|") >= 3:
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                for k, cell in enumerate(cells):
                    if not pattern.search(cell):
                        continue
                    before = pattern.split(cell, maxsplit=1)[0]
                    if _phrase(before) == "" and k > 0:
                        before = cells[k - 1]  # | 食物 | 橙子 | ★★★★★ |
                    for seg in re.split(r"[；;]", before)[-1:]:
                        item = _phrase(re.sub(r"^[^：:]{1,6}[：:]", "", _clean(seg)))
                        if item and item not in out:
                            out.append(item)
                continue
            for seg in re.split(r"[；;。]", ln):
                if not pattern.search(seg):
                    continue
                before = pattern.split(seg, maxsplit=1)[0]
                before = re.sub(r"^\s*(?:[-*]|\d+[.、)])\s*", "", before)
                before = re.sub(r"^\s*\*\*[^*]{1,8}\*\*\s*[：:]", "", before)  # **食物**：
                before = re.sub(r"^[^：:，,]{1,8}[：:]", "", _clean(before))  # 食物：
                item = _phrase(before.rstrip("（(，, *"))
                if item and item not in out:
                    out.append(item)
        return out

    found = collect(STAR_ITEM)
    if len(found) < 3:
        four = re.compile(r"((?<!★)★{4}(?![★☆])|(?<![\d.])4\s*星)")
        found += [x for x in collect(four) if x not in found]
    return found[:limit]


def _dislikes(text: str, limit: int = 3) -> list[str]:
    part = re.split(r"讨厌的?", text, maxsplit=1)
    if len(part) < 2:
        return []
    part = re.split(r"小癖好|收藏", part[1], maxsplit=1)[0]
    items = _items(["", *_lines(part)], limit)
    if not items:  # 「讨厌的：空心奉承。把……。」写成一段的
        first = _lines(part)[0] if _lines(part) else ""
        items = [_phrase(x) for x in re.split(r"[。；;]", re.sub(r"^[：:\s]+", "", first))]
        items = [x for x in items if x][:limit]
    return items


def _priorities(text: str) -> list[str]:
    lines = _region(
        text, r"看重|最重要|价值观", r"^[-*\s]*(?:\*\*)?(?:对)?(?:爱情|友情|钱|工作|被人认真问)"
    )
    items = _items(lines)
    if not items and lines:  # 「最看重：A、B、C」
        after = re.split(r"[：:]", _clean(lines[0]), maxsplit=1)[-1]
        items = [_phrase(x) for x in re.split(r"[、，,；;]", after) if x.strip()][:3]
    return items


def _bottom_lines(text: str) -> list[str]:
    lines = _lines(text)
    out: list[str] = []
    for ln in lines:
        if not LIST_ITEM.match(ln):
            continue
        b = re.match(r"^\s*(?:[-*]|\d+[.、)])\s*\*\*(.+?)\*\*\s*[：:]?\s*(.*)$", ln)
        if b and len(b.group(1)) <= 8 and b.group(2).strip():  # **共同底线**：……
            item = _first_sentence(b.group(2), 40)
        elif b:
            item = b.group(1)
        else:
            item = _first_sentence(LIST_ITEM.sub("", ln), 40)
        item = _short(re.sub(r"^[一二三四五][，,、]\s*", "", item).rstrip("：:。"), 40)
        if item and item not in out:
            out.append(item)
    if not out:  # 每条一段、没有列表记号
        out = [_short(_first_sentence(ln, 40).rstrip("。"), 40) for ln in lines[:3]]
    return [x for x in out if x][:3]


def card_patch(card: Card, source: str = "") -> dict[str, dict[str, Any]]:
    """一张卡 → 档案的修改（{分区: {键: 值}}）。"""
    patch: dict[str, dict[str, Any]] = {}
    for title, text in card.chapters.items():
        hit = next(((sec, key) for kw, sec, key in CHAPTERS if kw in title), None)
        if hit and text:
            patch.setdefault(hit[0], {})[hit[1]] = text
    identity = patch.setdefault("identity", {})
    basic = _basic_fields(card.chapter("基本信息"))
    identity.update(basic)
    identity.setdefault("名字", card.name)
    room = ROOM.search(basic.get("住处", ""))
    if room:
        identity["房号"] = room.group(1).zfill(2)
    if source:
        identity["来源"] = source
    personality = patch.setdefault("personality", {})
    character = card.chapter("性格")
    scores = _scores(character, card.scores)
    if scores:
        personality["五项"] = scores
    strengths = _items(_region(character, r"优点", r"毛病|缺点|反差|压力"))
    if strengths:
        personality["长处"] = strengths
    flaws = _items(_region(character, r"毛病|缺点", r"反差|压力|优点"))
    if flaws:
        personality["小毛病"] = flaws
    values = patch.setdefault("values", {})
    top = _priorities(card.chapter("三观"))
    if top:
        values["最看重"] = top
    bottom = _bottom_lines(card.chapter("底线"))
    if bottom:
        values["底线要点"] = bottom
    style = patch.setdefault("style", {})
    speech = card.chapter("说话")
    phrases = _catchphrases(speech)
    if phrases:
        style["口头禅"] = phrases
    me = _self_name(speech)
    if me:
        style["自称"] = me
    likes = patch.setdefault("likes", {})
    taste = card.chapter("喜好")
    fav = _favorites(taste)
    if fav:
        likes["最爱"] = fav
    hate = _dislikes(taste)
    if hate:
        likes["讨厌"] = hate
    return {sec: items for sec, items in patch.items() if items}


# --- 随身卡 -----------------------------------------------------------------------


def _join(items: Any, sep: str = "、") -> str:
    if isinstance(items, list):
        return sep.join(str(x) for x in items if str(x).strip())
    return str(items or "").strip()


def pocket_card(data: dict[str, Any], budget_chars: int = 400) -> str:
    """从档案拼随身卡（≤ budget_chars 字）。档案是空的返回空串。"""
    identity = dict(data.get("identity") or {})
    override = str(identity.get(OVERRIDE) or "").strip()
    if override:
        return override[:budget_chars]
    personality = dict(data.get("personality") or {})
    values = dict(data.get("values") or {})
    style = dict(data.get("style") or {})
    likes = dict(data.get("likes") or {})

    # (优先级, 文字)：超出字数时先去掉优先级数字大的
    lines: list[tuple[int, str]] = []
    name = str(identity.get("名字") or "").strip()
    who = name
    if identity.get("小名"):
        who += f"，小名{_short(str(identity['小名']), 12)}"
    if identity.get("房号"):
        who += f"，住公寓 {identity['房号']} 号房"
    if who:
        lines.append((0, who + "。"))
    if identity.get("年龄感"):
        lines.append((6, f"看起来：{_first_sentence(str(identity['年龄感']), 40)}"))
    if identity.get("自我介绍"):
        lines.append((7, f"自我介绍：{_short(str(identity['自我介绍']), 80)}"))
    scores = personality.get("五项") or {}
    trait = ""
    if isinstance(scores, dict) and scores:
        order = [k for k in TRAITS if k in scores] + [k for k in scores if k not in TRAITS]
        trait = " ".join(f"{k}{scores[k]}" for k in order) + "（满分 5）"
    if personality.get("长处"):
        trait += f"；长处：{_join(personality['长处'])}"
    if personality.get("小毛病"):
        trait += f"；小毛病：{_join(personality['小毛病'])}"
    if trait:
        lines.append((2, "性格：" + trait.lstrip("；")))
    if values.get("最看重"):
        lines.append((3, f"最看重：{_join(values['最看重'])}"))
    talk = []
    if style.get("口头禅"):
        talk.append("口头禅（偶尔用）：" + "".join(f"「{x}」" for x in style["口头禅"]))
    if style.get("自称"):
        talk.append(f"自称：{_short(str(style['自称']), 30)}")
    if talk:
        lines.append((4, "说话：" + "；".join(talk)))
    taste = []
    if likes.get("最爱"):
        taste.append(f"喜欢：{_join(likes['最爱'], '／')}")
    if likes.get("讨厌"):
        taste.append(f"讨厌：{_join(likes['讨厌'], '／')}")
    if taste:
        lines.append((5, "；".join(taste)))
    if values.get("底线要点"):
        lines.append((1, f"底线：{_join(values['底线要点'], '；')}"))

    def total(ls: list[tuple[int, str]]) -> int:
        return sum(len(t) for _, t in ls) + max(len(ls) - 1, 0)

    while len(lines) > 1 and total(lines) > budget_chars:
        worst = max(range(len(lines)), key=lambda i: (lines[i][0], i))
        lines.pop(worst)
    text = "\n".join(t for _, t in lines)
    return text if len(text) <= budget_chars else text[: budget_chars - 1] + "…"


# --- 导入 -------------------------------------------------------------------------


def source_title(text: str) -> str:
    """文件第一行的一级标题（如「八位成员完整角色卡 v2（2026-10-29）」），记进 identity.来源。"""
    m = re.search(r"^#\s+(.+?)\s*$", text.lstrip("﻿"), re.M)
    return _short(m.group(1), 60) if m else "人物卡"


def match_members(cards: list[Card], members: list[tuple[str, str]]) -> dict[str, str | None]:
    """卡名 → 成员 id：和成员的显示名或 id 一样（不区分大小写）就对上，对不上为 None。"""
    out: dict[str, str | None] = {}
    for card in cards:
        key = card.name.casefold()
        out[card.name] = next(
            (mid for mid, name in members if key in (name.casefold(), mid.casefold())), None
        )
    return out


def without_locked(
    patch: dict[str, dict[str, Any]], locked: list[str]
) -> dict[str, dict[str, Any]]:
    """重新导入时不动用户锁住的项。"""
    out: dict[str, dict[str, Any]] = {}
    for sec, items in patch.items():
        kept = {k: v for k, v in items.items() if f"{sec}.{k}" not in locked}
        if kept:
            out[sec] = kept
    return out
