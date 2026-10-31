"""梗雷达（docs/CHAT.md「网络梗」）：及时知道网上最近在玩什么梗。

1. 抓热榜（代码完成，不调模型、不花钱）：配置里的公开热榜（微博、百度、抖音、知乎……），
   只取标题；抓不到的跳过。已收的梗在标题里又出现了，就算「还在热」（续保质期）。
2. 搜索补充（每天最多 ``search_per_day`` 次）：找新梗一次，再查一个还不知道意思的经典梗。
   搜索词是固定的（不带记忆、聊天记录），只取结果的标题和摘要。
3. 提炼（每天一次调用，档位里最便宜的一位）：把标题和搜索摘要交给它，挑出真正的网络梗，
   写梗卡（梗、意思、上句 → 接法、例句、哪个月火的）；拿不准意思的不收；老梗、悲剧、
   政治、攻击真人、低俗歧视的不收。网页内容一律放在标签里，标签内的指令无效。
4. 程序再筛一遍：老梗名单里的、你删过的、火起来超过 ``max_age_days`` 天的不收；新梗最多存
   ``max_memes`` 个；两周没再上榜的下架（经典梗不下架）。

花费（提炼 + 搜索）计入生活每日上限、聊天月预算和总预算。
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from roundtable.core import browse
from roundtable.core.allocation import NotEnoughModels, cheapest
from roundtable.core.browse import FeedError, parse_feed, record_search, strip_html
from roundtable.core.chat import chat_budget
from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.member.memes import FIELDS, MemeBook, norm
from roundtable.core.providers import AllChannelsFailed, NoChannelAvailable, ProviderError
from roundtable.core.search import SearchUnavailable
from roundtable.core.storage.repository import iso

ROOM = "memes"  # 提炼和搜索调用挂在这个隐藏聊天会话上
STEP = "memes"
SEARCH_STEP = "memes_search"
LAST_KEY = "memes.last_refresh"
STATUS_KEY = "memes.last_status"
_KEYS = ("word", "title", "name", "query", "keyword", "desc")

Fetch = Callable[[str, float, int], Awaitable[bytes]]


def _dig(data: Any, path: str) -> list[Any]:
    """按「a.b.0.c」「a.*.b」找到值；「*」展开列表，最后返回一组值。"""
    nodes = [data]
    for part in [p for p in path.split(".") if p]:
        nxt: list[Any] = []
        for node in nodes:
            if part == "*" and isinstance(node, list):
                nxt.extend(node)
            elif part.isdigit() and isinstance(node, list):
                i = int(part)
                if i < len(node):
                    nxt.append(node[i])
            elif isinstance(node, dict) and part in node:
                nxt.append(node[part])
        nodes = nxt
    return nodes


def parse_hot(data: bytes, source: Any, limit: int = 60) -> list[str]:
    """一个热榜的标题列表。格式不对时抛 FeedError（不记录原始内容）。"""
    if source.kind == "rss":
        return [it["title"] for it in parse_feed(data, limit) if it.get("title")]
    try:
        doc = json.loads(data.decode("utf-8", errors="replace"))
    except ValueError:
        raise FeedError("不是 JSON") from None
    found = _dig(doc, source.path)
    items: list[Any] = []
    for node in found:
        items.extend(node if isinstance(node, list) else [node])
    titles: list[str] = []
    for item in items:
        value: Any = None
        if source.key:
            got = _dig(item, source.key)
            value = got[0] if got else None
        if value is None and isinstance(item, dict):
            value = next((item[k] for k in _KEYS if isinstance(item.get(k), str)), None)
        if value is None and isinstance(item, str):
            value = item
        text = strip_html(str(value or ""))[:60].strip()
        if text:
            titles.append(text)
        if len(titles) >= limit:
            break
    if not titles:
        raise FeedError("没有找到标题")
    return titles


class MemeRadar:
    def __init__(self, rt: Any, allow: Callable[[float], bool], fetch: Fetch | None = None) -> None:
        self.rt = rt
        self.allow = allow  # 生活每日上限
        self.fetch = fetch  # 为空时用上网模块的 http_fetch
        self.book = MemeBook(rt)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.memes

    # --- 抓热榜 ------------------------------------------------------------------

    async def hot_titles(self) -> tuple[list[str], int]:
        rules = self.rules
        sources = [s for s in rules.sources if s.enabled]
        fetch = self.fetch or browse.http_fetch  # 按名字现取：测试里整体替换成不联网的
        results = await asyncio.gather(
            *(fetch(s.url, rules.timeout_s, rules.max_kb * 1024) for s in sources),
            return_exceptions=True,
        )
        titles: list[str] = []
        failed = 0
        for source, data in zip(sources, results, strict=True):
            try:
                if isinstance(data, BaseException):
                    raise FeedError("抓取失败")
                titles += parse_hot(data, source)
            except FeedError:
                failed += 1
        return list(dict.fromkeys(titles))[: rules.titles_max], failed

    # --- 搜索 --------------------------------------------------------------------

    def searches_today(self) -> int:
        from roundtable.core.budget.guard import day_window

        start, end = day_window(self.rt.budget.now())
        return self.rt.repo.count_in_steps("chat", (SEARCH_STEP,), start, end)

    async def search(self, query: str) -> list[str]:
        rt, rules = self.rt, self.rules
        cost = rules.search_estimate_usd
        if (
            not rt.search.available
            or self.searches_today() >= rules.search_per_day
            or not self.allow(cost)
            or not rt.budget.check(cost).allowed
            or chat_budget(rt).would_exceed(cost)
        ):
            return []
        try:
            result, call = await rt.search.search(query, 8)
        except SearchUnavailable:
            return []
        record_search(rt, ROOM, SEARCH_STEP, query, call)
        return [
            f"{strip_html(h.title)[:80]}：{strip_html(h.snippet)[:200]}"
            for h in result.hits
            if h.title or h.snippet
        ]

    # --- 提炼 --------------------------------------------------------------------

    def model_id(self) -> str | None:
        plan = self.rt.config.routing.plans.get(self.rules.plan)
        if plan is None:
            return None
        tiers = set(plan.tiers)
        pool = [m for m in self.rt.router.available_models() if m.seat and m.tier in tiers]
        try:
            return cheapest(
                pool, random.Random(0), input_tokens=3000, output_tokens=self.rules.max_tokens
            ).id
        except NotEnoughModels:
            return None

    async def distill(
        self, now: datetime, hot: list[str], found: list[str], unexplained: list[str]
    ) -> dict[str, Any] | None:
        rt, rules = self.rt, self.rules
        cost = rules.estimate_usd
        model_id = self.model_id()
        if (
            model_id is None
            or not self.allow(cost)
            or not rt.budget.check(cost).allowed
            or chat_budget(rt).would_exceed(cost)
        ):
            return None
        known = [m["phrase"] for m in self.book.all(now)]
        prompt = rt.prompts.render(
            "meme_radar",
            rt.config.roundtable.prompts["meme_radar"],
            today=now.date().isoformat(),
            max_age_days=str(rules.max_age_days),
            hot="\n".join(hot) or "（今天没抓到）",
            found="\n".join(found) or "（没有）",
            known="、".join(known) or "（还没有）",
            unexplained="、".join(unexplained) or "（没有）",
            stale="、".join(rules.stale[:60]) or "（没有）",
        )
        params = {**rt.config.roundtable.step_params.get(STEP, {}), "max_tokens": rules.max_tokens}
        session = rt.repo.chat_session(ROOM)
        try:
            completion = await rt.router.complete(model_id, prompt.messages, params)
        except (AllChannelsFailed, NoChannelAvailable, ProviderError) as exc:
            rt.repo.record_call(
                session,
                step=STEP,
                role="tool",
                model_id=model_id,
                messages=prompt.messages,
                prompt=prompt,
                failure=exc,
            )
            return None
        rt.repo.record_call(
            session,
            step=STEP,
            role="tool",
            model_id=model_id,
            messages=prompt.messages,
            prompt=prompt,
            completion=completion,
        )
        try:
            return extract_json_object(completion.text)
        except JSONOutputError:
            return None

    # --- 一次刷新 ----------------------------------------------------------------

    def _too_old(self, since: str, now: datetime) -> bool:
        if len(since) < 7:
            return False
        try:
            when = datetime(int(since[:4]), int(since[5:7]), 1, tzinfo=now.tzinfo)
        except ValueError:
            return False
        return now - when > timedelta(days=self.rules.max_age_days + 31)

    def _clean(self, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        meme: dict[str, Any] = {}
        for key in FIELDS:
            value = raw.get(key)
            if key == "aliases":
                meme[key] = [str(a)[:20] for a in value][:6] if isinstance(value, list) else []
            else:
                meme[key] = str(value or "").strip()
        meme["phrase"] = meme["phrase"][:20]
        meme["meaning"] = meme["meaning"][:80]
        meme["cue"], meme["reply"] = meme["cue"][:40], meme["reply"][:40]
        meme["example"], meme["since"] = meme["example"][:80], meme["since"][:7]
        if not meme["phrase"] or not meme["meaning"]:
            return None  # 拿不准意思的不收
        return meme

    async def refresh(self, now: datetime, *, force: bool = False) -> dict[str, int]:
        """抓一遍、提炼一次。refresh_hours 内不重复（force 除外）。返回各项数量。"""
        out = {"titles": 0, "failed": 0, "added": 0, "kept": 0, "explained": 0, "dropped": 0}
        rules, repo, book = self.rules, self.rt.repo, self.book
        if not rules.enabled:
            return out
        last = repo.setting(LAST_KEY)
        if not force and last:
            try:
                if now - datetime.fromisoformat(last) < timedelta(hours=rules.refresh_hours):
                    return out
            except ValueError:
                pass
        repo.set_setting(LAST_KEY, now.isoformat())
        hot, failed = await self.hot_titles()
        out["titles"], out["failed"] = len(hot), failed
        items = book.stored()
        blob = norm("\n".join(hot))
        for m in items:  # 已收的梗又上榜了：续保质期（不花钱）
            keys = [m.get("phrase"), m.get("cue"), *(m.get("aliases") or [])]
            if any(k and len(norm(k)) >= 2 and norm(k) in blob for k in keys):
                m["last_seen"] = iso(now)
                out["kept"] += 1
        book.save(items)
        found = await self.search(rules.search_query)
        unexplained = [m["phrase"] for m in book.all(now) if not m.get("meaning")][:3]
        if unexplained:
            found += await self.search(f"{unexplained[0]} 梗 是什么意思 出处")
        if not hot and not found:
            self._status(now, out)
            return out
        data = await self.distill(now, hot, found, unexplained)
        if data is None:
            self._status(now, out)
            return out
        removed = book.removed()
        items = {norm(m["phrase"]): m for m in book.stored()}
        for raw in data.get("seen") or []:
            key = norm(str(raw))
            if key in items:
                items[key]["last_seen"] = iso(now)
        for raw in data.get("memes") or []:
            meme = self._clean(raw)
            if meme is None:
                continue
            key = norm(meme["phrase"])
            if key in removed or book.is_stale(meme["phrase"]) or self._too_old(meme["since"], now):
                out["dropped"] += 1
                continue
            old = items.get(key, {})
            items[key] = {
                **old,
                **{k: v for k, v in meme.items() if v not in ("", [])},
                "classic": bool(old.get("classic", False)),
                "source": old.get("source") or "radar",
                "first_seen": old.get("first_seen") or iso(now),
                "last_seen": iso(now),
            }
            out["added"] += int(not old)
        classics = {norm(c.phrase) for c in rules.classics}
        for raw in data.get("explained") or []:  # 经典梗 / 你加的梗：补上意思和接法
            meme = self._clean(raw)
            if meme is None:
                continue
            key = norm(meme["phrase"])
            if key not in classics and key not in items:
                continue
            old = items.get(key, {"phrase": meme["phrase"], "first_seen": iso(now)})
            filled = {k: v for k, v in meme.items() if v and not old.get(k)}
            items[key] = {
                **old,
                **filled,
                "classic": True if key in classics else old.get("classic"),
            }
            items[key].setdefault("source", "classic" if key in classics else "user")
            items[key]["last_seen"] = items[key].get("last_seen") or iso(now)
            out["explained"] += 1
        cutoff = iso(now - timedelta(days=rules.fresh_days))
        keep = [m for m in items.values() if m.get("classic") or str(m.get("last_seen")) >= cutoff]
        fresh = sorted(
            (m for m in keep if not m.get("classic") and norm(m["phrase"]) not in classics),
            key=lambda m: str(m.get("last_seen")),
            reverse=True,
        )
        lasting = [m for m in keep if m.get("classic") or norm(m["phrase"]) in classics]
        book.save(lasting + fresh[: rules.max_memes])
        self._status(now, out)
        return out

    def _status(self, now: datetime, out: dict[str, int]) -> None:
        self.rt.repo.set_setting(STATUS_KEY, json.dumps({"at": now.isoformat(), **out}))

    def status(self) -> dict[str, Any]:
        raw = self.rt.repo.setting(STATUS_KEY)
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}
