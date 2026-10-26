"""革新例会·插件清单（docs/evolution/README.md「插件清单」）：例会开完后写给用户看的插件清单。

- 写清单的成员：在所选档位里按预计价格挑最便宜的一位（价格相同随机），只看档位和价格，
  不看模型名或厂商。
- 输入：本场资料卡（全员读过的同一份）和点子榜上的点子（只给编号和内容，不给提出者），都放在
  标签里，提示词说明标签内的指令无效。
- 输出 JSON，由代码校验：字段齐全才收，场景不在配置里的归到最后一个（兜底）场景，按名称去重，
  每个字段截断；写完先遮蔽模型 id / 厂商名 / 昵称。第一次不够 count 个时最多再补写几次，
  每次都把已写的名字附上、只要缺的个数（同一位成员不会收到完全相同的请求）。
- 花费：调用挂在革新例会的隐藏会话上（和自己找资料同一个），计入总预算和革新例会的每月上限。
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from roundtable.core.allocation import NotEnoughModels, cheapest
from roundtable.core.ideaboard import Board
from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.providers import AllChannelsFailed, NoChannelAvailable, ProviderError

PLUGINS_KEY = "evolution.plugins"  # app_settings：{"会话 id": {"plugins": [...], ...}}
KEEP_LISTS = 20
STEP = "evolution_plugins"
FIELDS = ("name", "scene", "what", "use", "pros", "cons", "why", "source")
LABELS = {
    "what": "做什么",
    "use": "有什么用",
    "pros": "优点",
    "cons": "缺点",
    "why": "为什么做",
    "source": "参考",
}
IDEA_CHARS = 300  # 每个点子交给写清单的成员时最多多少字


class PluginError(RuntimeError):
    """现在没法写插件清单（没开、成员不够、预算不够、成员没写出来）。"""


@dataclass(frozen=True)
class Plugin:
    name: str
    scene: str
    what: str
    use: str
    pros: str
    cons: str
    why: str
    source: str


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_plugins(text: str, scenes: Sequence[str], *, field_chars: int) -> list[Plugin]:
    """成员写的 JSON → 插件。缺字段的不要；场景不在清单里的归到最后一个场景。"""
    try:
        data = extract_json_object(text)
    except JSONOutputError:
        return []
    raw = data.get("plugins")
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        values = {k: _clip(item.get(k), field_chars) for k in FIELDS}
        values["name"] = _clip(values["name"], 40)
        values["source"] = values["source"] or "成员补充"
        if not all(values[k] for k in FIELDS):
            continue
        if values["scene"] not in scenes:
            values["scene"] = scenes[-1]
        out.append(Plugin(**values))
    return out


def merge(existing: Sequence[Plugin], new: Sequence[Plugin]) -> list[Plugin]:
    """按名称去重（忽略大小写和空格），先到的留下。"""
    seen = {p.name.replace(" ", "").lower() for p in existing}
    out = list(existing)
    for p in new:
        key = p.name.replace(" ", "").lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def ideas_text(board: Board) -> str:
    """点子榜交给写清单的成员：只给编号、得分和内容，不给提出者（不需要知道是谁提的）。"""
    lines = [
        f"- {r.id}（{r.score} 分 · {r.votes} 票）：{_clip(r.text, IDEA_CHARS)}"
        for r in board.ranking
    ]
    return "\n".join(lines) or "（本场没有点子）"


def cards_text(cards: Sequence[tuple[str, str]]) -> str:
    """资料卡：[(文件名, 正文)] → 「资料卡 N」分段。"""
    parts = [
        f"## 资料卡 {n}（{name}）\n\n{text.strip()}" for n, (name, text) in enumerate(cards, 1)
    ]
    return "\n\n".join(parts) or "（本场没有资料卡）"


def by_scene(plugins: Sequence[Plugin], scenes: Sequence[str]) -> dict[str, int]:
    counts = {s: 0 for s in scenes}
    for p in plugins:
        counts[p.scene] = counts.get(p.scene, 0) + 1
    return {s: n for s, n in counts.items() if n}


def plugins_markdown(*, week: str, session_id: str, plugins: Sequence[Plugin], cost_usd: float,
                     scenes: Sequence[str]) -> str:  # fmt: skip
    """插件清单（Markdown）：按场景分组，每个插件写做什么、有什么用、优缺点、为什么做、参考。"""
    lines = [
        f"# 革新例会插件清单 · {week}",
        "",
        f"- 例会：会话 `{session_id}`；共 {len(plugins)} 个插件，写清单花费 ${cost_usd:.4f}",
        "- 由一位成员根据本场资料卡和点子榜整理，只是给你看的资料：要做哪个，在例会里点「做这条」"
        "或直接告诉开发侧；碰规则（中立性、全员上桌、预算、隐私）的不会做。",
        "",
    ]
    n = 0
    for scene in [s for s in scenes if any(p.scene == s for p in plugins)]:
        lines += [f"## {scene}", ""]
        for p in (p for p in plugins if p.scene == scene):
            n += 1
            lines += [f"### {n}. {p.name}", ""]
            lines += [f"- **{LABELS[k]}**：{getattr(p, k)}" for k in LABELS]
            lines.append("")
    if not plugins:
        lines += ["（这场没有写出插件）", ""]
    return "\n".join(lines)


def load_lists(raw: str | None) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def dump_lists(lists: dict[str, dict[str, Any]]) -> str:
    keep = sorted(lists.items(), key=lambda kv: str(kv[1].get("at", "")))[-KEEP_LISTS:]
    return json.dumps(dict(keep), ensure_ascii=False)


def plugins_from(entry: dict[str, Any]) -> list[Plugin]:
    out = []
    for p in entry.get("plugins") or []:
        try:
            out.append(Plugin(**{k: str(p[k]) for k in FIELDS}))
        except (KeyError, TypeError):
            continue
    return out


@dataclass
class PluginReport:
    plugins: list[Plugin] = field(default_factory=list)
    calls: int = 0
    cost_usd: float = 0.0

    def public(self) -> dict[str, Any]:
        return {
            "plugins": [asdict(p) for p in self.plugins],
            "calls": self.calls,
            "cost_usd": self.cost_usd,
        }


class PluginWriter:
    def __init__(self, rt: Any, rng: random.Random | None = None) -> None:
        self.rt = rt
        self.evo = rt.config.roundtable.evolution
        self.rules = self.evo.plugins
        self.rng = rng or random.Random()
        self.report = PluginReport()

    def writer(self) -> str:
        """写清单的一位：所选档位里按预计价格挑最便宜的（价格相同随机）。"""
        plan = self.rt.config.routing.plans.get(self.evo.plan)
        if plan is None:
            raise PluginError(f"evolution.plan 不是已有的档位：{self.evo.plan!r}")
        tiers = set(plan.tiers)
        pool = [m for m in self.rt.router.available_models() if m.seat and m.tier in tiers]
        try:
            return cheapest(
                pool,
                self.rng,
                input_tokens=self.rt.config.routing.planner.expected_input_tokens,
                output_tokens=self.rules.max_tokens,
            ).id
        except NotEnoughModels:
            raise PluginError(f"「{plan.label}」现在没有可用的成员") from None

    async def _ask(self, session: str, model_id: str, prompt: Any) -> str | None:
        params = {"max_tokens": self.rules.max_tokens}
        try:
            completion = await self.rt.router.complete(model_id, prompt.messages, params)
        except (AllChannelsFailed, NoChannelAvailable, ProviderError) as e:
            self.rt.repo.record_call(
                session,
                step=STEP,
                role="member",
                model_id=model_id,
                messages=prompt.messages,
                prompt=prompt,
                failure=e,
            )
            return None
        self.rt.repo.record_call(
            session,
            step=STEP,
            role="member",
            model_id=model_id,
            messages=prompt.messages,
            prompt=prompt,
            completion=completion,
        )
        self.report.calls += 1
        self.report.cost_usd += completion.cost_usd or 0.0
        return completion.text

    async def run(
        self, cards: Sequence[tuple[str, str]], board: Board, budget_usd: float
    ) -> PluginReport:
        """写至少 count 个插件；不够时最多补写 rounds − 1 次。花到 budget_usd 就停。"""
        rules, rt = self.rules, self.rt
        if not rules.enabled:
            raise PluginError("插件清单没有开启（evolution.plugins.enabled）")
        if rules.estimate_usd > budget_usd + 1e-9:
            raise PluginError(
                f"插件清单预计 ${rules.estimate_usd:.2f}，超过本月剩余额度 ${budget_usd:.2f}"
            )
        model_id = self.writer()
        session = rt.repo.scout_session()
        role_version = rt.config.roundtable.prompts[STEP]
        cards_block, ideas_block = cards_text(cards), ideas_text(board)
        for _ in range(rules.rounds):
            missing = rules.count - len(self.report.plugins)
            if missing <= 0:
                break
            if self.report.cost_usd + rules.estimate_usd > budget_usd + 1e-9:
                break
            if not rt.budget.check(rules.estimate_usd).allowed:
                break
            existing = "\n".join(f"- {p.name}（{p.scene}）" for p in self.report.plugins)
            prompt = rt.prompts.render(
                "evolution_plugins",
                role_version,
                count=str(missing),
                scenes="、".join(rules.scenes),
                cards=cards_block,
                ideas=ideas_block,
                existing=existing or "无",
            )
            text = await self._ask(session, model_id, prompt)
            if text is None:
                break
            text = rt.scrubber.scrub(text)
            got = parse_plugins(text, rules.scenes, field_chars=rules.field_chars)
            before = len(self.report.plugins)
            self.report.plugins = merge(self.report.plugins, got)
            if len(self.report.plugins) == before:
                break  # 这次一个新的都没写出来，不再花钱
        if not self.report.plugins:
            raise PluginError("成员没有写出插件清单（或没有回应、预算不够）")
        return self.report
