"""革新例会·自己找资料（docs/evolution/README.md）：圆桌自己联网找新做法，写成资料卡放进收件箱。

- 搜索、过滤、读网页都由代码完成：按配置的搜索词搜，只留白名单网站（含子域名）的结果，
  写过的网址不再写。
- 写卡和复核由成员完成：在所选档位里按预计价格挑最便宜的一位写卡，再挑另一位复核（不自审），
  只看档位和价格，不看模型名或厂商。复核通过的卡才进收件箱。
- 防注入：网页正文放在 <page> 标签里，提示词说明标签内的指令无效；卡写好后先去掉代码块、截断，
  再遮蔽模型 id / 厂商名 / 昵称（成员读卡时不受品牌影响），复核也看得到原文，夹带指令的不通过。
- 花费：搜索、读网页、写卡、复核的调用都挂在一个隐藏会话上，计入总预算和革新例会的每月上限。
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from roundtable.core.allocation import NotEnoughModels, cheapest
from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.providers import (
    AllChannelsFailed,
    Completion,
    Message,
    NoChannelAvailable,
    ProviderError,
)
from roundtable.core.search import SearchUnavailable

SEEN_KEY = "evolution.scout.seen"  # app_settings：写过卡（或判定不合适）的网址
SCOUT_WEEK_KEY = "evolution.scout.week"  # app_settings：自动找资料最近一次是哪一周
SEARCH_STEP = "scout_search"
FETCH_STEP = "scout_fetch"
CARD_STEP = "scout_card"
CHECK_STEP = "scout_check"
SKIP = "SKIP"
_FENCE = re.compile(r"```.*?(```|$)", re.DOTALL)
_BLANKS = re.compile(r"\n{3,}")


class ScoutError(RuntimeError):
    """现在没法找资料（没开、没有搜索服务、成员不够、预算不够）。"""


def allowed_url(url: str, domains: Sequence[str]) -> bool:
    """只收 http(s) 网址，且主机是白名单里的域名或其子域名。"""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


def clean_card(text: str, max_chars: int) -> str | None:
    """成员写的卡：SKIP 或空的不要；去掉代码块，截断到 max_chars。"""
    text = text.strip()
    if not text or text.upper().startswith(SKIP):
        return None
    text = _BLANKS.sub("\n\n", _FENCE.sub("", text)).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n…（超出长度，后面截掉了）"
    return text or None


def parse_verdict(text: str) -> tuple[bool, str]:
    """复核结果：{"ok": bool, "reason": str}；解析不了的算不通过。"""
    try:
        data = extract_json_object(text)
    except JSONOutputError:
        return False, "复核结果不是 JSON"
    return data.get("ok") is True, str(data.get("reason") or "").strip()[:200]


def load_seen(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [str(u) for u in data] if isinstance(data, list) else []


def card_name(week: str, n: int) -> str:
    return f"{week}-scout-{n}.md"


@dataclass
class ScoutReport:
    written: list[dict[str, str]] = field(default_factory=list)  # {file, title, url}
    rejected: list[dict[str, str]] = field(default_factory=list)  # {url, reason}
    searched: int = 0
    candidates: int = 0
    cost_usd: float = 0.0

    def public(self) -> dict[str, Any]:
        return {
            "written": self.written,
            "rejected": self.rejected,
            "searched": self.searched,
            "candidates": self.candidates,
            "cost_usd": self.cost_usd,
        }


class Scout:
    def __init__(self, rt: Any, rng: random.Random | None = None) -> None:
        self.rt = rt
        self.evo = rt.config.roundtable.evolution
        self.rules = self.evo.scout
        self.rng = rng or random.Random()
        self.report = ScoutReport()
        self._session: str | None = None

    # --- 准备 ------------------------------------------------------------------------

    def estimate_usd(self) -> float:
        r = self.rules
        cards = min(r.cards, self.evo.max_cards)
        return len(r.queries) * r.search_estimate_usd + cards * (
            r.search_estimate_usd + r.card_estimate_usd
        )

    def writers(self) -> tuple[str, str]:
        """写卡的一位、复核的另一位：所选档位里按预计价格挑最便宜的（价格相同随机）。"""
        plan = self.rt.config.routing.plans.get(self.evo.plan)
        if plan is None:
            raise ScoutError(f"evolution.plan 不是已有的档位：{self.evo.plan!r}")
        tiers = set(plan.tiers)
        pool = [m for m in self.rt.router.available_models() if m.seat and m.tier in tiers]
        spec = self.rt.config.routing.planner
        tokens = {
            "input_tokens": spec.expected_input_tokens,
            "output_tokens": spec.expected_output_tokens,
        }
        try:
            writer = cheapest(pool, self.rng, **tokens)
            checker = cheapest([m for m in pool if m.id != writer.id], self.rng, **tokens)
        except NotEnoughModels:
            raise ScoutError(
                f"「{plan.label}」可用的成员不到两位（写卡和复核要不同的人）"
            ) from None
        return writer.id, checker.id

    @property
    def session(self) -> str:
        if self._session is None:
            self._session = self.rt.repo.scout_session()
        return self._session

    # --- 调用记录 ----------------------------------------------------------------------

    def _record_tool(self, step: str, text: str, call: Any) -> None:
        completion = Completion(
            text="",
            model_id=f"search:{call.provider}",
            channel=call.provider,
            channel_kind="search",
            route_model="search",
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost_usd=call.cost_usd,
            cost_source=call.cost_source,
            latency_s=call.latency_s,
            attempts=call.attempts,
        )
        self.rt.repo.record_call(
            self.session,
            step=step,
            role="tool",
            model_id=completion.model_id,
            messages=[Message("user", text)],
            completion=completion,
        )
        self.report.cost_usd += call.cost_usd

    async def _ask(self, step: str, model_id: str, prompt: Any) -> str | None:
        if not self.rt.budget.check(self.rules.card_estimate_usd).allowed:
            return None
        params = {"max_tokens": self.rules.max_tokens}
        try:
            completion = await self.rt.router.complete(model_id, prompt.messages, params)
        except (AllChannelsFailed, NoChannelAvailable, ProviderError) as e:
            self.rt.repo.record_call(
                self.session,
                step=step,
                role="member",
                model_id=model_id,
                messages=prompt.messages,
                prompt=prompt,
                failure=e,
            )
            return None
        self.rt.repo.record_call(
            self.session,
            step=step,
            role="member",
            model_id=model_id,
            messages=prompt.messages,
            prompt=prompt,
            completion=completion,
        )
        self.report.cost_usd += completion.cost_usd or 0.0
        return completion.text

    # --- 主流程 ------------------------------------------------------------------------

    async def _candidates(self, seen: set[str]) -> list[tuple[str, str]]:
        """按搜索词搜，留下白名单网站、没写过、不重复的结果：[(标题, 网址)]。"""
        rules, out, urls = self.rules, [], set()
        for query in rules.queries:
            try:
                result, call = await self.rt.search.search(query, rules.results_per_query)
            except SearchUnavailable:
                continue
            self.report.searched += 1
            self._record_tool(SEARCH_STEP, f"search: {query}", call)
            for hit in result.hits:
                url = hit.url.strip()
                if url in seen or url in urls or not allowed_url(url, rules.domains):
                    continue
                urls.add(url)
                out.append((hit.title.strip()[:200] or url, url))
        self.report.candidates = len(out)
        return out

    async def run(self, inbox: Path, week: str, budget_usd: float) -> ScoutReport:
        """找资料写卡，最多写 min(cards, max_cards) 张；花到 budget_usd 就停。"""
        rules, rt = self.rules, self.rt
        if not rules.enabled:
            raise ScoutError("自己找资料没有开启（evolution.scout.enabled）")
        if not rules.queries or not rules.domains:
            raise ScoutError("没有配置搜索词或白名单网站（evolution.scout）")
        if not rt.search.available or not rt.search.can_fetch:
            raise ScoutError(
                f"没有能用的联网搜索 / 读网页服务：{rt.search.reason() or '不能读网页'}"
            )
        estimate = self.estimate_usd()
        if estimate > budget_usd + 1e-9:
            raise ScoutError(f"找资料预计 ${estimate:.2f}，超过剩余额度 ${budget_usd:.2f}")
        if not rt.budget.check(estimate).allowed:
            raise ScoutError("总预算不够了")
        writer, checker = self.writers()
        seen = load_seen(rt.repo.setting(SEEN_KEY))
        want = min(rules.cards, self.evo.max_cards)
        roles = rt.config.roundtable.prompts
        for title, url in await self._candidates(set(seen)):
            if len(self.report.written) >= want or self.report.cost_usd >= budget_usd:
                break
            seen.append(url)
            reason = await self._one(title, url, writer, checker, roles, inbox, week)
            if reason:
                self.report.rejected.append({"url": url, "reason": reason})
        rt.repo.set_setting(SEEN_KEY, json.dumps(seen[-rules.seen_keep :], ensure_ascii=False))
        return self.report

    async def _one(
        self,
        title: str,
        url: str,
        writer: str,
        checker: str,
        roles: dict[str, str],
        inbox: Path,
        week: str,
    ) -> str | None:
        """写一张卡。写进收件箱返回 None，否则返回没写的原因。"""
        rt = self.rt
        try:
            fetched, call = await rt.search.fetch([url], self.rules.page_chars)
        except SearchUnavailable:
            return "读不了网页"
        self._record_tool(FETCH_STEP, f"fetch: {url}", call)
        page = next((p.text for p in fetched.pages if p.ok and p.text.strip()), "")
        if not page:
            return "网页是空的或读不出来"
        page = page[: self.rules.page_chars]
        prompt = rt.prompts.render(
            "evolution_card", roles["evolution_card"], title=title, url=url, page=page
        )
        text = await self._ask(CARD_STEP, writer, prompt)
        if text is None:
            return "写卡的成员没有回应（或预算不够）"
        card = clean_card(text, self.evo.card_chars)
        if card is None:
            return "写卡的成员认为和圆桌无关"
        card = rt.scrubber.scrub(card)
        check = rt.prompts.render(
            "evolution_check", roles["evolution_check"], url=url, page=page, card=card
        )
        verdict = await self._ask(CHECK_STEP, checker, check)
        if verdict is None:
            return "复核的成员没有回应（或预算不够）"
        ok, reason = parse_verdict(verdict)
        if not ok:
            return f"复核没通过：{reason or '没有说明'}"
        inbox.mkdir(parents=True, exist_ok=True)
        n = len(self.report.written) + 1
        path = inbox / card_name(week, n)
        while path.exists():
            n += 1
            path = inbox / card_name(week, n)
        path.write_text(card + "\n", encoding="utf-8")
        heading = card.splitlines()[0].lstrip("#").strip()[:80]
        self.report.written.append({"file": path.name, "title": heading, "url": url})
        return None
