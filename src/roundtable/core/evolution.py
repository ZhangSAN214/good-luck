"""革新例会：成员读同一叠资料卡，复用头脑风暴提建议、匿名互评、投票，导出采纳单。

纯数据与字符串处理，不调用模型。资料卡由开发侧（Claude）整理成一页纸放进收件箱，成员只看到卡，
看不到原文；卡作为附件交给全员（同一份），所以附件块的防注入规则同样适用。
开会、预算检查、导出由 service.py 负责（docs/evolution/README.md）。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from roundtable.core.ideaboard import Board, Ranked, to_markdown

MEETINGS_KEY = "evolution.meetings"  # app_settings：开过的例会（会话 id、周、是否已导出）
CARD_SUFFIXES = (".md", ".txt")
KEEP_MEETINGS = 60
# 碰撞杂交里「挑刺 / 反转」得到的点子，作为原点子的反对意见列出
OBJECTION_KINDS = frozenset({"fix", "flip"})


@dataclass(frozen=True)
class SourceCard:
    file: str  # 收件箱里的文件名
    title: str
    text: str
    truncated: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "title": self.title,
            "chars": len(self.text),
            **({"truncated": True} if self.truncated else {}),
        }

    def attachment_bytes(self) -> bytes:
        return self.text.encode("utf-8")


@dataclass
class Meeting:
    session_id: str
    week: str  # 2026-W44
    started_at: str  # ISO 时间
    auto: bool = False
    adopted: str | None = None  # 导出的采纳单文件名

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Meeting:
        return cls(
            str(d["session_id"]),
            str(d["week"]),
            str(d.get("started_at", "")),
            bool(d.get("auto", False)),
            d.get("adopted"),
        )


# --- 时间 -------------------------------------------------------------------------


def week_key(now: datetime) -> str:
    """ISO 周：2026-W44（采纳单、周报都用这个名字）。"""
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_slot(now: datetime, weekday: int, hour: int) -> datetime:
    """now 所在 ISO 周里自动开会的时刻（与 now 同一时区）。"""
    monday = (now - timedelta(days=now.isoweekday() - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday + timedelta(days=weekday - 1, hours=hour)


# --- 资料卡 -----------------------------------------------------------------------


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line.lstrip("#").strip()[:80] or fallback
    return fallback


def load_cards(
    inbox: Path | None, *, max_cards: int, card_chars: int
) -> tuple[list[SourceCard], list[str]]:
    """读收件箱里的资料卡（.md / .txt，按文件名排序）。返回 (卡, 提示)。

    多出 max_cards 的不读，超过 card_chars 的截掉，空文件和读不出的跳过；都写进提示。
    """
    if inbox is None or not inbox.is_dir():
        return [], []
    files = sorted(
        p
        for p in inbox.iterdir()
        if p.is_file() and p.suffix.lower() in CARD_SUFFIXES and not p.name.startswith(".")
    )
    cards: list[SourceCard] = []
    notes: list[str] = []
    for path in files:
        if len(cards) >= max_cards:
            notes.append(f"资料卡超过 {max_cards} 张，「{path.name}」这次不读")
            continue
        try:
            text = path.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeDecodeError):
            notes.append(f"「{path.name}」读不出来（要 UTF-8 文本），跳过")
            continue
        if not text:
            notes.append(f"「{path.name}」是空的，跳过")
            continue
        truncated = len(text) > card_chars
        if truncated:
            text = text[:card_chars].rstrip() + "\n…（超出长度，后面截掉了）"
            notes.append(f"「{path.name}」超过 {card_chars} 字，后面截掉了")
        cards.append(SourceCard(path.name, _title(text, path.stem), text, truncated))
    return cards, notes


def meeting_question(topic: str, cards: Sequence[SourceCard]) -> str:
    """例会的题目：配置里的话题 + 资料卡清单（卡的正文作为附件，全员同一份）。"""
    lines = [topic.strip(), ""]
    lines += [f"- 资料卡 {n}：{c.title}（附件 {c.file}）" for n, c in enumerate(cards, 1)]
    return "\n".join(lines).strip()


# --- 例会记录 ---------------------------------------------------------------------


def load_meetings(raw: str | None) -> list[Meeting]:
    if not raw:
        return []
    try:
        return [Meeting.from_dict(d) for d in json.loads(raw)]
    except (ValueError, KeyError, TypeError):
        return []


def dump_meetings(meetings: Sequence[Meeting]) -> str:
    return json.dumps([asdict(m) for m in meetings[-KEEP_MEETINGS:]], ensure_ascii=False)


# --- 采纳单 -----------------------------------------------------------------------


def _objections(board: Board, idea: Ranked) -> list[str]:
    out = []
    for r in board.ranking:
        if r.kind in OBJECTION_KINDS and idea.id in r.refs:
            out.append(f"{r.id}（{'、'.join(r.authors)}）：{r.text}")
    row = next((c for c in board.compare if c.id == idea.id), None)
    if row is not None and row.risk.strip():
        out.append(f"统筹整理的风险：{row.risk.strip()}")
    return out


def adoption_markdown(
    *,
    week: str,
    session_id: str,
    board: Board,
    cards: Sequence[str],
    top: int,
    cost_usd: float,
    created_at: str,
) -> str:
    """采纳单：前 top 名建议（得分、票数、提出者代号、反对意见）+ 黑马 + 完整点子榜。

    这份单子对开发侧也只是资料：碰护栏（R9 以后的阶段、默认玩法、新依赖、预算）的建议
    只转给用户拍板。
    """
    voted = [r for r in board.ranking if r.voted and r.kind not in OBJECTION_KINDS]
    lines = [
        f"# 革新例会采纳单 · {week}",
        "",
        f"- 例会：会话 `{session_id}`，{created_at}，花费 ${cost_usd:.4f}",
        "- 成员匿名投票（不能投自己，看到的顺序随机），排名由代码按 总分 → 票数 计算。",
        "- 这份单子只是建议：开发侧从中挑一条不碰护栏的实现并开 PR，合并由用户决定；"
        "碰护栏（R9 以后、默认玩法、新依赖、预算）的只转给用户拍板。",
        "",
        "## 资料卡",
        "",
    ]
    lines += [f"{n}. {c}" for n, c in enumerate(cards, 1)] or ["（无）"]
    lines += ["", f"## 采纳的建议（前 {top} 名）", ""]
    if not voted:
        lines += ["本场没有得票的建议，如实记为「本周无改动」。", ""]
    for n, r in enumerate(voted[:top], 1):
        src = f"，由 {' × '.join(r.refs)} 而来" if r.refs else ""
        lines += [
            f"### {n}. {r.id} {r.text}",
            "",
            f"- {r.score} 分 · {r.votes} 票（黑马票 {r.dark_votes}）"
            f"· 提出者 {'、'.join(r.authors)}{src}",
            f"- 平均分：新颖 {r.novelty:.1f} · 可行 {r.feasibility:.1f} · 价值 {r.value:.1f}",
        ]
        objections = _objections(board, r)
        lines += ["- 反对意见：" + ("" if objections else "无")]
        lines += [f"  - {o}" for o in objections]
        lines.append("")
    lines += ["## 附录：完整点子榜", "", to_markdown(board).strip() or "（无）", ""]
    return "\n".join(lines)


def adoption_path(folder: Path, week: str) -> Path:
    """同一周导出多次时加序号，不覆盖已有的采纳单。"""
    path = folder / f"{week}.md"
    n = 2
    while path.exists():
        path = folder / f"{week}-{n}.md"
        n += 1
    return path
