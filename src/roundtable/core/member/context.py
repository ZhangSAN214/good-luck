"""上下文组装器：要调模型时，从各模块各取一两句最相关的，总长固定预算（默认约 600 字）。

- 每个模块交上来几段 ``Piece``（来源、文字、相关度）。``fixed`` 的段（随身卡、规则）按交上来的
  顺序放在最前面，内容不变时前缀也不变，支持缓存的模型能读缓存（只要原价的一到两成）。
- 其余的段按相关度从高到低挑，同分按来源名和原顺序（结果可复现），每个来源最多 ``per_source`` 段，
  装不下的整段丢掉；剩余空间够 ``min_piece_chars`` 时最后一段截断后放进去。
- 只改顺序和取舍，不改内容；所有成员用同一套规则（中立）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

SEP = "\n"
ELLIPSIS = "…"


@dataclass(frozen=True)
class Piece:
    source: str  # 哪个模块：card / memory / mood / relation …
    text: str
    score: float = 0.0  # 相关度，越大越先放
    fixed: bool = False  # 固定前缀（随身卡、规则）


@dataclass
class Context:
    prefix: str  # 固定部分（放最前，利于缓存）
    body: str  # 按相关度挑的部分
    used: list[Piece] = field(default_factory=list)
    dropped: list[Piece] = field(default_factory=list)

    @property
    def text(self) -> str:
        return SEP.join(t for t in (self.prefix, self.body) if t)

    @property
    def chars(self) -> int:
        return len(self.text)


def assemble(
    pieces: list[Piece], budget_chars: int = 600, per_source: int = 2, min_piece_chars: int = 20
) -> Context:
    """把各模块的段落装进 budget_chars 字以内（含段与段之间的换行）。"""
    used: list[Piece] = []
    dropped: list[Piece] = []
    room = budget_chars

    def take(p: Piece, text: str) -> None:
        nonlocal room
        room -= len(text) + (len(SEP) if used else 0)
        used.append(Piece(p.source, text, p.score, p.fixed))

    def fit(p: Piece) -> None:
        text = p.text.strip()
        need = len(text) + (len(SEP) if used else 0)
        if not text:
            return
        if need <= room:
            take(p, text)
            return
        left = room - (len(SEP) if used else 0) - len(ELLIPSIS)
        if left >= min_piece_chars:
            take(p, text[:left].rstrip() + ELLIPSIS)
        else:
            dropped.append(p)

    fixed = [p for p in pieces if p.fixed]
    rest = [(i, p) for i, p in enumerate(pieces) if not p.fixed]
    for p in fixed:
        fit(p)
    n_fixed = len(used)
    count: dict[str, int] = {}
    for _, p in sorted(rest, key=lambda x: (-x[1].score, x[1].source, x[0])):
        if count.get(p.source, 0) >= per_source:
            dropped.append(p)
            continue
        before = len(used)
        fit(p)
        if len(used) > before:
            count[p.source] = count.get(p.source, 0) + 1
    prefix = SEP.join(p.text for p in used[:n_fixed])
    body = SEP.join(p.text for p in used[n_fixed:])
    return Context(prefix, body, used, dropped)
