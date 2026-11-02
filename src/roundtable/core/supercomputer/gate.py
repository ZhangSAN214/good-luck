"""自治闸门：定时活到点时，能不能不问你就开干（docs/SUPERCOMPUTER.md「管家」）。

- 你亲手交的、点「现在跑」的、点「批准」的：都是你发起的，直接干（闸门只记一笔风险）。
- 定时活到点：按 ``autonomy`` 和活的风险决定「自己跑」还是「等你确认」。高风险永远等你。
- 每天自己跑的件数有上限（``max_auto_per_day``），用完了也等你确认。

只看活的文字和配置，不看是哪位助手、哪家的（和派活一样中立）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from roundtable.core.config.schema import SuperGate

RANK = {"low": 0, "medium": 1, "high": 2}
RISK_NAME = {"low": "低", "medium": "中", "high": "高"}
AUTONOMY_NAME = {"off": "都等你确认", "low": "低风险自己跑", "medium": "低、中风险自己跑"}


@dataclass(frozen=True)
class Verdict:
    risk: str  # low / medium / high
    run: bool  # True：现在就干（或进队）；False：等你确认
    why: str
    hits: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        out = asdict(self)
        out["hits"] = list(self.hits)
        return out


def _found(text: str, words: list[str]) -> list[str]:
    low = text.lower()
    return [w for w in words if w and w.lower() in low]


def risk(text: str, gate: SuperGate) -> tuple[str, tuple[str, ...]]:
    """（风险，命中的词）。碰到高风险词是高；只碰到低风险词、没碰到改动类的词是低；其余是中。"""
    high = _found(text, gate.high_words)
    if high:
        return "high", tuple(high)
    write = _found(text, gate.write_words)
    low = _found(text, gate.low_words)
    if low and not write:
        return "low", tuple(low)
    return "medium", tuple(write)


def decide(text: str, gate: SuperGate, *, source: str, auto_used: int) -> Verdict:
    """source：manual（你发起的）/ schedule（定时活到点）。"""
    level, hits = risk(text, gate)
    name = RISK_NAME[level]
    if source != "schedule":
        return Verdict(level, True, f"你发起的（风险{name}）", hits)
    if level == "high":
        return Verdict(level, False, f"风险高（碰到「{'、'.join(hits)}」），一定要你点头", hits)
    if gate.autonomy == "off":
        return Verdict(level, False, f"闸门设成「{AUTONOMY_NAME['off']}」（风险{name}）", hits)
    if RANK[level] > RANK[gate.autonomy]:
        return Verdict(
            level, False, f"风险{name}，闸门只放行「{AUTONOMY_NAME[gate.autonomy]}」", hits
        )
    if auto_used >= gate.max_auto_per_day:
        return Verdict(
            level, False, f"今天自己跑的 {gate.max_auto_per_day} 件已经用完（风险{name}）", hits
        )
    return Verdict(level, True, f"风险{name}，闸门放行，自己跑", hits)
