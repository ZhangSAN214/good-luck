"""成员自己上网（docs/WANDER.md）：成员在自己的隔离浏览器里逛白名单网站，第一期只看不互动。

- 同一时间只有一位在逛（电脑内存有限）；每次最多 ``max_steps`` 页、``max_minutes`` 分钟。
- 每一页：程序取回页面文字和链接（不花钱）→ 交给成员（她自己的模型，``wander`` 提示词，
  所有成员同一套提示词和参数）→ 她说一句感想并决定下一步（打开第几个链接 / 搜索 / 返回 / 结束）。
  只按链接地址打开、按搜索地址模板搜索，不点按钮、不填表、不登录。
- 浏览记录存在隐藏会话 ``wander:<成员>``（不出现在群聊列表），每页一条感想（带网址和标题），
  结束时一条小结；小结同时发感知事件 ``perceive.web``，留给成员自己的记忆。
- 花费记在这个隐藏会话上（step = wander），计入生活每日上限、聊天月预算和总预算；
  每看一页之前检查生活每日上限，不够就收尾。
- 自动安排：在心跳上登记——醒着、闲着（看书 / 聊天 / 打游戏 / 看星星 / 待命）、在
  ``start_hour``～``end_hour`` 之间、今天还没逛够、按 ``trip_chance`` 抽中时出门。
  所有成员的规则完全相同，不看是哪个模型。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from roundtable.core.budget.guard import day_window as utc_day_window
from roundtable.core.chat import ChatHub, ChatMember, call_member, member_memory
from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.member.heartbeat import Intent
from roundtable.core.member.scheduler import Priority
from roundtable.core.office.apartment import schedule
from roundtable.core.persona import chat_persona, persona_of
from roundtable.core.storage.repository import iso
from roundtable.core.wander.web import PlaywrightWeb, Snapshot, Web, WebError

log = logging.getLogger(__name__)

WANDER_PREFIX = "wander:"
STEP = "wander"
SYSTEM = "system"
LEISURE = frozenset({"idle", "chat", "read", "game", "stargaze"})
ACTIONS = ("open", "search", "back", "done")


def wander_room(member: str) -> str:
    return WANDER_PREFIX + member


@dataclass
class Trip:
    id: str
    member: ChatMember
    site: Any  # WanderSite
    by: str  # user / auto
    started: datetime
    steps: int = 0
    url: str = ""
    title: str = ""
    spent: float = 0.0
    notes: list[str] = field(default_factory=list)
    stop: str = ""  # 有值时尽快收尾（原因）


@dataclass
class Decision:
    note: str
    action: str
    link: int | None = None
    query: str = ""
    summary: str = ""


def parse_decision(text: str, links: int) -> Decision | None:
    try:
        data = extract_json_object(text)
    except JSONOutputError:
        return None
    action = str(data.get("action") or "").strip().lower()
    if action not in ACTIONS:
        return None
    note = str(data.get("note") or "").strip()[:200]
    link: int | None = None
    if action == "open":
        try:
            link = int(data.get("link"))
        except (TypeError, ValueError):
            return None
        if not 1 <= link <= links:
            return None
    query = str(data.get("query") or "").strip()[:40]
    if action == "search" and not query:
        return None
    summary = str(data.get("summary") or "").strip()[:400]
    return Decision(note, action, link, query, summary)


class Wanderer:
    def __init__(
        self,
        rt: Any,
        hub: ChatHub,
        allow: Callable[[float], bool],
        *,
        profiles: Path,
        publish: Callable[[Any], Any] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.rt = rt
        self.hub = hub
        self.allow = allow  # 生活每日上限
        self.profiles = profiles
        self.publish = publish
        self.sleep = sleep
        self.trip: Trip | None = None
        self._task: asyncio.Task[None] | None = None
        self._frame: bytes | None = None
        self.last_error = ""

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.wander

    def sites(self) -> list[Any]:
        return [s for s in self.rules.sites if s.enabled]

    def site(self, site_id: str | None, rng: random.Random | None = None) -> Any:
        sites = self.sites()
        if not sites:
            raise ValueError("没有可以逛的网站（配置 chat.wander.sites）")
        if site_id is None:
            return (rng or random.Random()).choice(sites)
        for s in sites:
            if s.id == site_id:
                return s
        raise ValueError("这个网站不在白名单里")

    def web(self) -> Web:
        factory = getattr(self.rt, "wander_web", None)
        if factory is not None:
            return factory()
        r = self.rules
        return PlaywrightWeb(
            channels=list(r.channels),
            headless=r.headless,
            page_chars=r.page_chars,
            max_links=r.max_links,
            timeout_s=r.timeout_s,
            load_wait_s=r.load_wait_seconds,
        )

    def unavailable(self) -> str | None:
        if not self.rules.enabled:
            return "已关闭（chat.wander.enabled）"
        if getattr(self.rt, "wander_web", None) is not None:
            return None
        return PlaywrightWeb.available()

    def now(self) -> datetime:
        return self.rt.budget.now()

    # --- 记录 ---------------------------------------------------------------------------

    def _add(self, trip: Trip, author: str, text: str, kind: str, **extra: Any) -> dict[str, Any]:
        meta = {"wander": {"trip": trip.id, "kind": kind, "site": trip.site.name, **extra}}
        model = trip.member.model_id if author != SYSTEM else None
        return self.rt.repo.add_chat_message(
            wander_room(trip.member.id), author, text, model_id=model, meta=meta
        )

    def log(self, member: str, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        return self.rt.repo.chat_messages(wander_room(member), after=after, limit=limit)

    def trips_today(self, member: str) -> int:
        start = iso(utc_day_window(self.now())[0])
        rows = self.rt.repo.chat_messages(wander_room(member), limit=100)
        return sum(
            1
            for r in rows
            if r["created_at"] >= start
            and ((r["meta"] or {}).get("wander") or {}).get("kind") == "start"
        )

    def frame(self) -> bytes | None:
        return self._frame if self.trip is not None else None

    def status(self) -> dict[str, Any]:
        trip = self.trip
        running = None
        if trip is not None:
            running = {
                "member": trip.member.id,
                "name": trip.member.name,
                "site": trip.site.name,
                "by": trip.by,
                "started_at": iso(trip.started),
                "steps": trip.steps,
                "max_steps": self.rules.max_steps,
                "url": trip.url,
                "title": trip.title,
                "spent_usd": round(trip.spent, 4),
                "frame": self._frame is not None,
            }
        members = []
        for m in self.hub.everyone():
            last = self.rt.repo.chat_messages(wander_room(m.id), limit=1)
            members.append(
                {
                    "id": m.id,
                    "name": m.name,
                    "available": m.available,
                    "today": self.trips_today(m.id),
                    "last_at": last[0]["created_at"] if last else None,
                }
            )
        return {
            "enabled": self.rules.enabled,
            "auto": self.rules.auto,
            "unavailable": self.unavailable(),
            "sites": [{"id": s.id, "name": s.name, "home": s.home} for s in self.sites()],
            "running": running,
            "members": members,
            "per_day": self.rules.trips_per_member_per_day,
            "last_error": self.last_error,
        }

    # --- 出门 / 收工 ----------------------------------------------------------------------

    def start(self, member: str, site_id: str | None = None, *, by: str = "user") -> Trip:
        why = self.unavailable()
        if why:
            raise ValueError(f"现在不能上网：{why}")
        if self.trip is not None:
            raise ValueError(f"{self.trip.member.name} 正在逛，一次只能一位")
        found = next((m for m in self.hub.everyone() if m.id == member), None)
        if found is None or not found.available:
            raise ValueError("这位成员现在不在")
        site = self.site(site_id)
        if not self.allow(self.rules.estimate_usd):
            raise ValueError("今天生活的花费到上限了，明天再逛")
        now = self.now()
        trip = Trip(now.strftime("%Y%m%d%H%M%S"), found, site, by, now)
        self.trip = trip
        self._frame = None
        self.last_error = ""
        self.rt.repo.ensure_chat_room(
            wander_room(found.id), kind="wander", title=f"{found.name}上网", members=[found.id]
        )
        how = "你让她" if by == "user" else "空闲时"
        self._add(trip, SYSTEM, f"{how}去逛{site.name}", "start")
        self._task = asyncio.get_running_loop().create_task(self.run(trip))
        return trip

    def stop(self, why: str = "你叫停了") -> bool:
        if self.trip is None:
            return False
        self.trip.stop = why
        return True

    async def wait(self) -> None:
        """等这次出门结束（测试和关服务时用）。"""
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    async def aclose(self) -> None:
        self.stop("服务关闭")
        if self._task is not None and not self._task.done():
            try:
                await asyncio.wait_for(self.wait(), timeout=10)
            except TimeoutError:
                self._task.cancel()

    async def run(self, trip: Trip) -> None:
        rules = self.rules
        web = self.web()
        end = "逛完了"
        summary = ""
        deadline = trip.started + timedelta(minutes=rules.max_minutes)
        try:
            await web.start(self.profiles / trip.member.id, list(trip.site.hosts))
            snap = await web.goto(trip.site.home)
            failures = 0
            for i in range(rules.max_steps):
                if trip.stop:
                    end = trip.stop
                    break
                if self.now() >= deadline:
                    end = f"到 {rules.max_minutes:g} 分钟了"
                    break
                if not self.allow(rules.estimate_usd):
                    end = "今天生活的花费到上限了"
                    break
                trip.url, trip.title = snap.url, snap.title
                self._frame = await web.screenshot()
                decision = await self.decide(trip, snap, rules.max_steps - i)
                trip.steps = i + 1
                if decision is None:
                    failures += 1
                    if failures >= 2:
                        end = "她没想好下一步，先不逛了"
                        break
                    continue
                if decision.note:
                    trip.notes.append(f"{snap.title or snap.url}：{decision.note}")
                    self._add(
                        trip, trip.member.id, decision.note, "page", url=snap.url, title=snap.title
                    )
                if decision.action == "done":
                    summary = decision.summary
                    break
                try:
                    snap = await self.act(web, trip, snap, decision)
                    failures = 0
                except WebError as e:
                    failures += 1
                    self._add(trip, SYSTEM, str(e), "error")
                    if failures >= 2:
                        end = "网页一直打不开，先不逛了"
                        break
                if rules.step_wait_seconds:
                    await self.sleep(rules.step_wait_seconds)
            else:
                end = f"看了 {rules.max_steps} 页"
        except WebError as e:
            end = str(e)
            self.last_error = str(e)
        except Exception:  # noqa: BLE001 - 出错只结束这次出门
            log.exception("上网：%s 出错", trip.member.id)
            end = "出了点问题，先不逛了"
        finally:
            with contextlib.suppress(Exception):
                await web.close()
            self.finish(trip, end, summary)

    async def act(self, web: Web, trip: Trip, snap: Snapshot, d: Decision) -> Snapshot:
        if d.action == "open" and d.link is not None:
            return await web.goto(snap.links[d.link - 1][1])
        if d.action == "search":
            if not trip.site.search:
                raise WebError("这个网站这里不能搜索")
            return await web.goto(trip.site.search.replace("{q}", quote(d.query)))
        return await web.back()

    def finish(self, trip: Trip, end: str, summary: str) -> None:
        if summary:
            self._add(trip, trip.member.id, summary, "summary")
        self._add(trip, SYSTEM, f"{end}（看了 {trip.steps} 页，花了 ${trip.spent:.4f}）", "end")
        card = summary or "；".join(trip.notes[-5:])
        if card and self.publish is not None:
            from roundtable.core.member.bus import perception

            with contextlib.suppress(Exception):
                self.publish(perception("web", self.now(), trip.member.id, card, trip.site.name))
        self.trip = None
        self._frame = None

    # --- 一步 ---------------------------------------------------------------------------

    def interests(self, member: str) -> str:
        mine = persona_of(self.rt, member).interests
        return "、".join(mine) if mine else "随便看看，碰到有意思的就多看两眼"

    def params(self) -> dict[str, Any]:
        params = dict(self.rt.config.roundtable.step_params.get(STEP, {}))
        params.setdefault("max_tokens", self.rules.max_tokens)
        return params

    async def decide(self, trip: Trip, snap: Snapshot, steps_left: int) -> Decision | None:
        rt, m = self.rt, trip.member
        interests = self.interests(m.id)
        links = "\n".join(f"{k}. {text}" for k, (text, _) in enumerate(snap.links, 1))
        actions = "打开页面上列出的某个链接"
        if trip.site.search:
            actions += "、用这个网站的搜索功能搜一个词"
        actions += "、返回上一页"
        prompt = rt.prompts.render(
            STEP,
            rt.config.roundtable.prompts[STEP],
            name=m.name,
            site=trip.site.name,
            interests=interests,
            persona=chat_persona(rt, m.id),
            memory=member_memory(rt, m.id, f"{trip.site.name} {interests}"),
            page=f"标题：{snap.title or '（没有标题）'}\n\n{snap.text or '（这一页没有文字）'}",
            links=links or "（这一页没有能打开的链接）",
            visited="\n".join(trip.notes[-8:]) or "（刚打开首页）",
            steps_left=str(steps_left),
            actions=actions,
        )
        completion = await call_member(
            rt,
            room=wander_room(m.id),
            model_id=m.model_id,
            prompt=prompt,
            step=STEP,
            params=self.params(),
            estimate_usd=self.rules.estimate_usd,
        )
        if completion is None:
            return None
        trip.spent += completion.cost_usd or 0.0
        return parse_decision(completion.text, len(snap.links))

    # --- 心跳 ---------------------------------------------------------------------------

    def want(self, member: str, now: datetime) -> Intent | None:
        """心跳问：这位成员现在想不想出去逛逛（代码判断，不调用模型）。"""
        rules = self.rules
        if not (rules.enabled and rules.auto) or self.trip is not None or self.unavailable():
            return None
        if not rules.start_hour <= now.hour < rules.end_hour:
            return None
        if schedule(member, now.hour, now.weekday())[1] not in LEISURE:
            return None
        if self.trips_today(member) >= rules.trips_per_member_per_day or not self.sites():
            return None
        rng = random.Random(f"wander:{member}:{now:%Y%m%d%H%M}")
        if rng.random() >= rules.trip_chance:
            return None
        site = self.site(None, rng)
        estimate = rules.estimate_usd * rules.max_steps

        async def go() -> None:
            if self.trip is None:
                with contextlib.suppress(ValueError):
                    self.start(member, site.id, by="auto")

        return Intent(member, Priority.LIFE, estimate, go, f"想去逛{site.name}，今天额度不够")
