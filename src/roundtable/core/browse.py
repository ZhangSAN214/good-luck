"""成员上网：读公开订阅源，按兴趣挑一条分享到生活群（docs/CHAT.md G7）。

- **只读**：只抓公开的 RSS / Atom 订阅源（不登录、不发帖、不评论外网），只存标题和摘要；
  抓回的内容不进日志。订阅源清单在网页「上网」里增删（存本地），没改过时用配置里的默认清单。
- 抓取由代码完成，不调用模型、不花钱；``refresh_hours`` 内不重复抓。
- 分享：每次推进挑醒着、今天还没分享够的成员，按 TA 的兴趣（人设里的「兴趣」；空着时由
  成员 id 派生的随机种子从订阅源话题里挑两个）选一条没人分享过的，交给成员用两三句话转述 +
  一句看法。消息带来源（标题、网址、来源名、发布时间），论坛 / 博客 / 搜索来的标「未核实」。
- 联网搜索：订阅源里没有合兴趣的内容时才搜，搜索词只有兴趣本身（不带记忆和聊天记录），
  全体每天最多 ``search_per_day`` 次，只取搜索结果的标题和摘要，不读网页。
- 花费（分享的模型调用 + 搜索）计入生活每日上限、聊天月预算和总预算。
- 网页内容一律放在 ``<article>`` 标签里，标签内的指令无效（提示词说明）。
"""

from __future__ import annotations

import asyncio
import html
import json
import random
import re
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import TypeAdapter, ValidationError

from roundtable.core.budget.guard import day_window as utc_day_window
from roundtable.core.chat import (
    ROOM,
    ChatHub,
    ChatMember,
    call_member,
    chat_budget,
    clean_reply,
    member_memory,
)
from roundtable.core.chat_actions import card_from_item
from roundtable.core.config.schema import FeedSpec
from roundtable.core.member.memes import strip_stale
from roundtable.core.member.voice import Said, before_voice, recent_words, voice_for
from roundtable.core.office.apartment import schedule
from roundtable.core.persona import chat_persona, persona_of
from roundtable.core.plain_talk import before_speaking, plain_prompt
from roundtable.core.providers import Completion, Message
from roundtable.core.search import SearchUnavailable
from roundtable.core.storage.repository import iso

FEEDS_KEY = "browse.feeds"
REFRESH_KEY = "browse.last_refresh"
BROWSE_ROOM = "browse"  # 搜索调用挂在这个隐藏聊天会话上
SEARCH_STEP = "browse_search"
SHARE_STEP = "browse"
USER_AGENT = "Roundtable/1.0 (personal feed reader; read-only)"
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_FEEDS = TypeAdapter(list[FeedSpec])

Fetch = Callable[[str, float, int], Awaitable[bytes]]


class FeedError(ValueError):
    pass


_HUB_PATH = re.compile(
    r"/(?:index|default|home|list|lists|news|channel|channels|category|categories|tag|tags|"
    r"topic|topics|search|section|column|rolling|roll|latest|archive|archives)"
    r"(?:[_-]?\d*)?(?:\.s?html?|\.php|\.aspx?)?/?$",
    re.IGNORECASE,
)
_HUB_TITLE = re.compile(r"首页|频道|栏目|滚动|列表|新闻中心|资讯中心|官网|官方网站|汇总|大全")


def looks_like_article(url: str, title: str) -> bool:
    """搜索结果是一篇具体的内容（文章、帖子、视频），不是网站首页或栏目列表页。"""
    path = urlparse(url).path
    if path in ("", "/") or _HUB_PATH.search(path):
        return False
    return not _HUB_TITLE.search(title or "")


def strip_html(text: str | None) -> str:
    text = html.unescape(_TAG.sub(" ", text or ""))
    return _SPACE.sub(" ", text).strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child(el: ET.Element, *names: str) -> ET.Element | None:
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _date(text: str | None) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return iso(moment)


def _link(item: ET.Element) -> str:
    for c in item:
        if _local(c.tag) != "link":
            continue
        href = c.attrib.get("href")
        if href and c.attrib.get("rel", "alternate") == "alternate":
            return href.strip()
        if (c.text or "").strip():
            return c.text.strip()
    guid = _child(item, "guid", "id")
    return (guid.text or "").strip() if guid is not None else ""


def parse_feed(data: bytes, limit: int) -> list[dict[str, Any]]:
    """RSS 2.0 / Atom → [{title, url, summary, published}]。拒绝带实体声明的文档。"""
    if b"<!ENTITY" in data.upper() or b"<!DOCTYPE" in data.upper():
        raise FeedError("订阅源里有 DOCTYPE / 实体声明，不解析")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise FeedError("不是有效的 RSS / Atom") from None
    out: list[dict[str, Any]] = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title_el = _child(el, "title")
        title = strip_html("".join(title_el.itertext()) if title_el is not None else "")
        url = _link(el)
        if not title or not url.startswith(("http://", "https://")):
            continue
        body = _child(el, "description", "summary", "content", "encoded")
        summary = strip_html("".join(body.itertext()) if body is not None else "")
        when = _child(el, "pubdate", "published", "updated", "date")
        out.append(
            {
                "title": title[:300],
                "url": url[:1000],
                "summary": summary,
                "published": _date(when.text if when is not None else None),
            }
        )
        if len(out) >= limit:
            break
    return out


async def http_fetch(url: str, timeout_s: float, max_bytes: int) -> bytes:
    """GET 一个公开网址（只读，不带任何 cookie / key），超过 max_bytes 时放弃。"""
    if not url.startswith(("http://", "https://")):
        raise FeedError("只抓 http / https")
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml"}
    async with (
        httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers=headers) as client,
        client.stream("GET", url) as resp,
    ):
        resp.raise_for_status()
        chunks, size = [], 0
        async for chunk in resp.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise FeedError("订阅源太大")
            chunks.append(chunk)
        return b"".join(chunks)


def record_search(rt: Any, room: str, step: str, query: str, call: Any) -> None:
    """把一次联网搜索记进 calls 表（挂在隐藏聊天会话 room 上，按 step 统计花费和次数）。"""
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
    rt.repo.record_call(
        rt.repo.chat_session(room),
        step=step,
        role="tool",
        model_id=completion.model_id,
        messages=[Message("user", f"search: {query}")],
        completion=completion,
    )


def feeds_of(rt: Any) -> tuple[list[FeedSpec], str]:
    """(订阅源清单, 来源)：网页里改过的（local）优先，否则配置文件里的（config）。"""
    stored = rt.repo.setting(FEEDS_KEY)
    if stored:
        try:
            return _FEEDS.validate_python(json.loads(stored)), "local"
        except (ValueError, ValidationError):
            pass
    return list(rt.config.roundtable.chat.browse.feeds), "config"


def set_feeds(rt: Any, data: list[dict[str, Any]] | None) -> tuple[list[FeedSpec], str]:
    """保存网页里改的清单；None 恢复为配置文件里的。校验失败抛 ValidationError。"""
    if data is None:
        rt.repo.set_setting(FEEDS_KEY, "")
    else:
        feeds = _FEEDS.validate_python(data)
        if len(feeds) > 30:
            raise ValueError("订阅源最多 30 个")
        rt.repo.set_setting(FEEDS_KEY, json.dumps([f.model_dump() for f in feeds]))
    return feeds_of(rt)


class Browser:
    def __init__(
        self,
        rt: Any,
        hub: ChatHub,
        allow: Callable[[float], bool],
        fetch: Fetch | None = None,
    ) -> None:
        self.rt = rt
        self.posted: list[dict[str, Any]] = []  # 这次推进里发出的分享（消息行）
        self.hub = hub
        self.allow = allow  # 生活每日上限
        self.fetch = fetch  # 为空时用模块里的 http_fetch（测试里整体替换，不联网）

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.browse

    # --- 抓取 --------------------------------------------------------------------

    async def refresh(self, now: datetime, *, force: bool = False) -> dict[str, int]:
        """抓一遍启用的订阅源。返回 {added, failed}。refresh_hours 内不重复抓（force 除外）。"""
        repo, rules = self.rt.repo, self.rules
        last = repo.setting(REFRESH_KEY)
        if (
            not force
            and last
            and now - datetime.fromisoformat(last) < timedelta(hours=rules.refresh_hours)
        ):
            return {"added": 0, "failed": 0}
        feeds = [f for f in feeds_of(self.rt)[0] if f.enabled]
        fetch = self.fetch or http_fetch
        results = await asyncio.gather(
            *(fetch(f.url, rules.timeout_s, rules.max_feed_kb * 1024) for f in feeds),
            return_exceptions=True,
        )
        added = failed = 0
        for feed, data in zip(feeds, results, strict=True):
            try:
                if isinstance(data, BaseException):
                    raise FeedError("抓取失败")
                items = parse_feed(data, rules.max_items_per_feed)
            except FeedError:
                failed += 1  # 不记录内容和原始错误
                continue
            for it in items:
                added += repo.add_web_item(
                    feed.name,
                    it["url"],
                    it["title"],
                    summary=it["summary"][: rules.article_chars],
                    published=it["published"],
                    topics=feed.topics,
                    unverified=feed.kind != "news",
                )
        repo.set_setting(REFRESH_KEY, now.isoformat())
        repo.prune_web_items(iso(now - timedelta(days=rules.keep_days)))
        return {"added": added, "failed": failed}

    # --- 兴趣与挑选 ----------------------------------------------------------------

    def topics(self) -> list[str]:
        return sorted({t for f in feeds_of(self.rt)[0] if f.enabled for t in f.topics})

    def interests(self, member: ChatMember) -> list[str]:
        mine = persona_of(self.rt, member.id).interests
        if mine:
            return list(mine)
        topics = self.topics()
        rng = random.Random(f"interest:{member.id}")
        return rng.sample(topics, min(2, len(topics)))

    @staticmethod
    def matches(item: dict[str, Any], interest: str) -> bool:
        key = interest.strip().lower()
        if not key:
            return False
        if any(key == t.lower() or key in t.lower() for t in item["topics"]):
            return True
        return key in item["title"].lower() or key in item["summary"].lower()

    def pick(self, member: ChatMember, rng: random.Random) -> tuple[dict[str, Any], str] | None:
        items = self.rt.repo.web_items(unshared=True, limit=300)
        interests = self.interests(member)
        rng.shuffle(interests)
        for interest in interests:
            hits = [it for it in items if self.matches(it, interest)]
            if hits:
                return hits[0], interest  # 最新的
        return None

    # --- 搜索 --------------------------------------------------------------------

    def searches_today(self) -> int:
        start, end = utc_day_window(self.rt.budget.now())
        return self.rt.repo.count_in_steps("chat", (SEARCH_STEP,), start, end)

    async def search(self, interest: str) -> dict[str, Any] | None:
        """订阅源里没有合兴趣的内容时搜一次（搜索词只有兴趣本身）。"""
        rt, rules = self.rt, self.rules
        cost = rules.search_estimate_usd
        if (
            not rt.search.available
            or self.searches_today() >= rules.search_per_day
            or not self.allow(cost)
            or not rt.budget.check(cost).allowed
            or chat_budget(rt).would_exceed(cost)
        ):
            return None
        query = f"{interest} 最新消息"
        try:
            result, call = await rt.search.search(query, 6)
        except SearchUnavailable:
            return None
        record_search(rt, BROWSE_ROOM, SEARCH_STEP, query, call)
        for hit in result.hits:
            if not hit.url.startswith(("http://", "https://")):
                continue
            if not looks_like_article(hit.url, strip_html(hit.title)):
                continue  # 栏目首页、列表页：点开什么都看不到（10-31 第 3 次试跑 6 条全是这种）
            if rt.repo.add_web_item(
                f"搜索：{interest}",
                hit.url,
                strip_html(hit.title)[:300] or hit.url,
                summary=strip_html(hit.snippet)[: rules.article_chars],
                topics=[interest],
                unverified=True,
            ):
                return next(it for it in rt.repo.web_items(limit=5) if it["url"] == hit.url)
        return None

    # --- 分享 --------------------------------------------------------------------

    async def _self_check(self, member: ChatMember, text: str, names: dict[str, str]) -> str:
        """说出口前自查（成员系统 P5）：和大家最近的分享比开头句式、相似度，和她自己最近说的比；
        查出问题改写一次（分享不会因此不发）。"""
        rt = self.rt
        rows = [
            r
            for r in rt.repo.chat_messages(ROOM, limit=200)
            if (r["meta"] or {}).get("share") and r["author"] in names
        ][-12:]
        said = Said(
            own=tuple(r["text"] for r in rows if r["author"] == member.id),
            others=tuple((names[r["author"]], r["text"]) for r in rows if r["author"] != member.id),
            long=recent_words(rt, member.id, rt.budget.now()),
        )
        return await before_voice(
            rt,
            text,
            said,
            member=member.id,
            room=ROOM,
            room_kind="share",
            named=True,
            name=member.name,
            model_id=member.model_id,
        )

    async def share(
        self, member: ChatMember, item: dict[str, Any], interest: str
    ) -> dict[str, Any] | None:
        rt, rules = self.rt, self.rules
        if not self.allow(rules.estimate_usd):
            return None
        everyone = {m.id: m.name for m in self.hub.everyone()}
        chat = self.hub.room(ROOM)
        when = item["published"] or "未知"
        source = f"来源：{item['feed']}；发布时间：{when}"
        if item["unverified"]:
            source += "；未核实（论坛、博客或网上搜到的）"
        article = f"标题：{item['title']}\n内容：{item['summary'] or '（只有标题）'}"
        prompt = rt.prompts.render(
            "life_share",
            rt.config.roundtable.prompts["life_share"],
            name=member.name,
            interest=interest,
            source=source,
            article=article,
            persona=chat_persona(rt, member.id),
            voice=voice_for(rt, member.id, "share", seed=item["id"]),
            memory=member_memory(rt, member.id, f"{interest}\n{article}"),
            history=chat.history(everyone),
        )
        prompt = plain_prompt(rt.prompts, rt.config, prompt)
        params = dict(rt.config.roundtable.step_params.get("chat", {}))
        params["max_tokens"] = rules.max_tokens
        completion = await call_member(
            rt,
            room=ROOM,
            model_id=member.model_id,
            prompt=prompt,
            step=SHARE_STEP,
            params=params,
            estimate_usd=rules.estimate_usd,
        )
        if completion is None:
            return None
        text = clean_reply(completion.text, member.name, rt.config.roundtable.chat.max_chars)
        if not text:
            return None
        text = await self._self_check(member, text, everyone) or text
        text = await before_speaking(
            rt, text, room=ROOM, name=member.name, model_id=member.model_id
        )
        # 说话的样子：书面词换口语、句尾标点、笑法、心情别直说（和群聊同一套收尾）
        from roundtable.core.member.lingo import Lingo  # lingo 依赖成员档案

        text = Lingo(rt, names=everyone).finish(
            member.id, text, rng=random.Random(f"share:{item['id']}:{member.id}")
        )[0] or strip_stale(text, rt.config.roundtable.chat.memes.stale)
        rt.repo.mark_web_shared(item["id"], member.id)
        kinds = {f.name: f.kind for f in feeds_of(rt)[0]}
        card = card_from_item(
            item, kinds.get(item["feed"], "blog"), rt.config.roundtable.chat.actions.video_hosts
        )
        return rt.repo.add_chat_message(
            ROOM,
            member.id,
            text,
            model_id=member.model_id,
            cost_usd=completion.cost_usd or 0.0,
            meta={
                "life": True,
                "card": card,
                "share": {
                    "title": item["title"],
                    "url": item["url"],
                    "feed": item["feed"],
                    "published": item["published"],
                    "unverified": item["unverified"],
                },
            },
        )

    async def tick(self, now: datetime) -> int:
        """抓订阅源（到时间的话），再让几位醒着的成员各分享一条。返回分享条数。"""
        rules = self.rules
        if not rules.enabled:
            return 0
        await self.refresh(now)
        day_start = iso(utc_day_window(self.rt.budget.now())[0])
        rng = random.Random(f"browse:{now.replace(minute=0, second=0, microsecond=0)}")
        awake = [
            m
            for m in self.hub.everyone()
            if m.available and schedule(m.id, now.hour, now.weekday())[1] != "sleep"
        ]
        rng.shuffle(awake)
        shared = 0
        self.posted = []
        for member in awake:
            if shared >= rules.shares_per_tick:
                break
            if self.rt.repo.web_shared_by(member.id, day_start) >= rules.shares_per_member:
                continue
            found = self.pick(member, rng)
            if found is None:
                interest = rng.choice(self.interests(member) or [""])
                item = await self.search(interest) if interest else None
                found = (item, interest) if item else None
            if found is None:
                continue
            row = await self.share(member, *found)
            if row is None:
                break  # 预算不够或调用失败：这次不再试
            self.posted.append(row)  # 生活引擎接着让一两位看了说两句
            shared += 1
        return shared
