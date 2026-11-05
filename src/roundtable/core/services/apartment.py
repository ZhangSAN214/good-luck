"""公寓楼：成员自己上网、旅行与 B 站、朋友圈、星图。

从 service.py 拆出（代码精简）。
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict
from datetime import timedelta
from typing import Any

from roundtable.core import starmap
from roundtable.core.chat import ROOM
from roundtable.core.chat_actions import card_from_url, is_bare_url
from roundtable.core.chat_actions import card_text as chat_card_text
from roundtable.core.hometz import home_zone
from roundtable.core.member import ProfileError
from roundtable.core.member.growth import monday, week_id
from roundtable.core.member.growth.book import BookError
from roundtable.core.moments import SEEN_KEY as MOMENTS_SEEN
from roundtable.core.services.common import MAX_MOMENT_CHARS, ServiceError


class ApartmentService:
    """公寓楼：成员自己上网、旅行与 B 站、朋友圈、星图。"""

    # --- 成员自己上网 ---------------------------------------------------------------------

    def wander_status(self) -> dict[str, Any]:
        return self.wander.status()

    def wander_start(self, member: str, site: str | None = None) -> dict[str, Any]:
        try:
            self.wander.start(member, site or None)
        except ValueError as e:
            raise ServiceError(str(e)) from None
        return self.wander.status()

    def wander_stop(self) -> dict[str, Any]:
        self.wander.stop()
        return self.wander.status()

    def wander_log(self, member: str, after: int = 0) -> list[dict[str, Any]]:
        if member not in {m.id for m in self.chats.everyone()}:
            raise ServiceError("没有这位成员")
        return self.wander.log(member, after)

    def wander_frame(self) -> bytes | None:
        return self.wander.frame()

    # --- 旅行与 B 站（L5，docs/LIFE_STORY.md）-------------------------------------------

    def trips(self) -> dict[str, Any]:
        """旅行、打卡、B 站看过的视频和私信 / 动态（只存本机，不发到外部网站）。"""
        life = self.life
        today = home_zone(self.rt).local(self.rt.budget.now()).date()
        members = self.chats.everyone()
        from roundtable.core.member.story import store as life_store

        travel = {
            m.id: life.story.travel.view(life_store.load(self.rt, m.id), today) for m in members
        }
        return {
            "enabled": life.checkin.enabled(),
            "names": {m.id: m.name for m in members},
            "travel": travel,
            "checkins": life.checkin.records(limit=40),
            "bili": life.bili.view(),
        }

    def set_trip_switches(self, *, auto: bool | None = None, photo: bool | None = None) -> dict:
        """B 站一键总开关（自动看、自动写私信 / 动态）；打卡配图开关（默认关）。"""
        from roundtable.core.member.checkin import store as ck_store

        if auto is not None:
            ck_store.set_switch(self.rt, ck_store.AUTO_KEY, auto)
        if photo is not None:
            ck_store.set_switch(self.rt, ck_store.PHOTO_KEY, photo)
        return self.trips()

    async def bili_watch(self, member: str, url: str) -> dict[str, Any]:
        """你让她看一个 B 站视频（不受自动开关影响，照样算生活每日上限）。"""
        from roundtable.core.member.checkin.bili import bv_of

        if member not in {m.id for m in self.chats.everyone()}:
            raise ServiceError("没有这位成员")
        if not bv_of(url):
            raise ServiceError("不是 B 站视频链接（要带 BV 号）")
        now = home_zone(self.rt).local(self.rt.budget.now())
        rec = await self.life.bili.watch(member, url, now)
        if rec is None:
            raise ServiceError(
                "没看成：电脑上没有 yt-dlp、视频超过 10 分钟，或今天生活的钱用完了", 409
            )
        return rec

    # --- 朋友圈 --------------------------------------------------------------------

    def _background(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._chat_tasks.add(task)
        task.add_done_callback(self._chat_tasks.discard)

    def _moment_out(self, row: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
        def who(author: str) -> str:
            return names.get(author, author)

        by_id = {c["id"]: c for c in row["comments"]}
        return {
            "id": row["id"],
            "author": row["author"],
            "name": who(row["author"]),
            "text": row["text"],
            "meta": row["meta"],
            "offline": row["offline"],
            "created_at": row["created_at"],
            "likes": [{"id": w, "name": who(w)} for w in row["likes"]],
            "liked": "user" in row["likes"],
            "comments": [
                {
                    "id": c["id"],
                    "author": c["author"],
                    "name": who(c["author"]),
                    "text": c["text"],
                    "reply_to": c["reply_to"],
                    "reply_name": who(by_id[c["reply_to"]]["author"])
                    if c["reply_to"] in by_id
                    else None,
                    "offline": c["offline"],
                    "created_at": c["created_at"],
                }
                for c in row["comments"]
            ],
        }

    def _moment(self, moment_id: int) -> dict[str, Any]:
        row = self.rt.repo.moment(moment_id)
        if row is None:
            raise ServiceError("没有这条动态", 404)
        return row

    def moments_view(self, author: str | None = None, before: int | None = None) -> dict[str, Any]:
        """朋友圈（新的在前，每页 20 条）；author 只看某位成员（或 user）。看第一页时标为已读。"""
        if author and author != "user":
            self._chat_member(author)
        everyone = self.chats.everyone()
        names = {m.id: m.name for m in everyone} | {"user": "我"}
        rows = self.rt.repo.moments(author=author or None, before=before, limit=20)
        if not before and not author:
            self.rt.repo.set_setting(MOMENTS_SEEN, str(self.rt.repo.latest_moment_id()))
        return {
            "moments": [self._moment_out(r, names) for r in rows],
            "members": [asdict(m) for m in everyone],
            "enabled": self.rt.config.roundtable.chat.moments.enabled,
        }

    def _moment_text(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            raise ServiceError("内容不能为空")
        if len(text) > MAX_MOMENT_CHARS:
            raise ServiceError(f"内容太长（最多 {MAX_MOMENT_CHARS} 字）")
        return text

    async def moments_post(self, text: str, media: list[dict] | None = None) -> dict[str, Any]:
        """用户发一条动态（可带图片 / 视频 / 语音）；一两位成员在后台评论。"""
        text = (text or "").strip()
        if len(text) > MAX_MOMENT_CHARS:
            raise ServiceError(f"内容太长（最多 {MAX_MOMENT_CHARS} 字）")
        text, meta = self._media_meta(text, media)
        if meta is None and is_bare_url(text):  # 只发了一个链接：显示成转发卡片
            hosts = self.rt.config.roundtable.chat.actions.video_hosts
            card = card_from_url(text, hosts, self.rt.repo.web_item_by_url(text))
            text, meta = chat_card_text(card), {"card": card}
        row = self.rt.repo.add_moment("user", self._moment_text(text), meta=meta)
        mode = self.perceiver.mode_for(text) if meta else None
        if self.rt.config.roundtable.chat.moments.enabled or mode:
            self._background(self._look_and_comment(row["id"], mode))
        return {"id": row["id"], "look": mode}

    async def _look_and_comment(self, moment_id: int, mode: str | None) -> None:
        """动态带图片 / 视频 / 语音时，先由感官整理成感知卡，再请成员评论。"""
        if mode is not None:
            with contextlib.suppress(Exception):  # 没看成也照常评论，成员只看到占位
                await self._perceive_moment(moment_id, mode)
        if self.rt.config.roundtable.chat.moments.enabled:
            await self.life.moments.react_to_user_post(moment_id)

    async def _perceive_moment(self, moment_id: int, mode: str) -> None:
        row = self.rt.repo.moment(moment_id)
        if row is None:
            return
        meta = dict(row["meta"] or {})
        items = self._sense_items(meta)
        if not items:
            return
        cards = await self.perceiver.perceive(ROOM, items, mode)
        if cards:
            meta["perception"] = [
                {"id": c.id, "kind": c.kind, "mode": c.mode, "text": c.text} for c in cards
            ]
            self.rt.repo.set_moment_meta(moment_id, meta)

    def moments_like(self, moment_id: int) -> dict[str, Any]:
        """用户点赞 / 取消点赞。"""
        self._moment(moment_id)
        if not self.rt.repo.like_moment(moment_id, "user"):
            self.rt.repo.unlike_moment(moment_id, "user")
            return {"liked": False}
        return {"liked": True}

    async def moments_comment(
        self, moment_id: int, text: str, reply_to: int | None = None
    ) -> dict[str, Any]:
        """用户评论（或回复某条评论）；被评论 / 被回复的成员在后台回一句。"""
        self._moment(moment_id)
        text = self._moment_text(text)
        if reply_to is not None:
            parent = self.rt.repo.moment_comment(reply_to)
            if parent is None or parent["moment_id"] != moment_id:
                raise ServiceError("要回复的评论不在这条动态下")
        row = self.rt.repo.add_moment_comment(moment_id, "user", text, reply_to=reply_to)
        if self.rt.config.roundtable.chat.moments.enabled:
            self._background(self.life.moments.reply_to_user_comment(row["id"]))
        return {"id": row["id"]}

    def moments_delete(self, moment_id: int) -> dict[str, Any]:
        """删掉一条动态（连同点赞、评论）。都是本地数据，成员的也能删。"""
        if not self.rt.repo.delete_moment(moment_id):
            raise ServiceError("没有这条动态", 404)
        return {"deleted": 1}

    def moments_delete_comment(self, comment_id: int) -> dict[str, Any]:
        if not self.rt.repo.delete_moment_comment(comment_id):
            raise ServiceError("没有这条评论", 404)
        return {"deleted": 1}

    # --- 星图（docs/STARMAP.md）：圆桌星图与成员的人生星图，只读、不调模型 ----------------

    def star_map_member(self, member: str) -> dict[str, Any]:
        """一位成员的人生星图：身份、性格、三观、喜好、能力、记忆、关系、情绪、表达、成长。"""
        member = self._profile_member(member)
        m = next(x for x in self.chats.everyone() if x.id == member)
        states = {s["member"]: s for s in self.member_states()["members"]}
        return starmap.life_map(
            member={"id": m.id, "name": m.name},
            profile=self.member_profile(member),
            growth=self.member_growth(member),
            relation=self.member_relation(member),
            memory=self.chat_memory_items(member, None, 200),
            state=states.get(member),
            today=self._local_now().date(),
        )

    def star_map(self) -> dict[str, Any]:
        """圆桌星图：版本更新挂在各部门下、成员（完整度与缺的星域）、体检里没过的项。"""
        members, peers = [], {}
        for m in self.chats.everyone():
            life = self.star_map_member(m.id)
            members.append(
                {"id": m.id, "name": m.name, "score": life["score"], "missing": life["missing"]}
            )
            peers[m.id] = self.engine.relation.summary(m.id)["peers"]
        return starmap.roundtable_map(
            changelog=self.changelog(),
            members=members,
            findings=[f.view() for f in self.firstaid_findings()],
            peers=peers,
            today=self._local_now().date(),
        )

    async def member_growth_run(self, member: str) -> dict[str, Any]:
        """马上让这位成员反思上一周（每周只反思一次；上周已经反思过就不再调用）。"""
        member = self._profile_member(member)
        now = self._local_now()
        start = monday(now.date()) - timedelta(days=7)
        if self.growth.log.done_week(member) == week_id(start):
            raise ServiceError("上一周已经反思过了，每周只反思一次", 409)
        m = next(x for x in self.chats.everyone() if x.id == member)
        got = await self.growth.run(m, start, now.tzinfo, today=now.date())
        if got is None:
            raise ServiceError("今天生活的额度或聊天预算不够，下次推进时再试", 409)
        return {"result": got, **self.member_growth(member)}

    def member_growth_undo(self, member: str, token: str) -> dict[str, Any]:
        """在成长总账里撤回一条（交回原来那个模块撤：设定、口癖、收藏）。"""
        member = self._profile_member(member)
        try:
            self.growth.book.undo(member, token)
        except (BookError, ProfileError) as e:
            raise ServiceError(str(e)) from None
        return self.member_growth(member)

    def member_growth_revert(self, member: str, entry_id: int) -> dict[str, Any]:
        """撤回一条成长改动（以你的身份存成档案的新版本）。"""
        member = self._profile_member(member)
        self._profile_do(lambda: self.growth.log.revert(member, entry_id))
        return self.member_growth(member)

    def member_growth_clear(self, member: str) -> dict[str, Any]:
        """清空这位成员的成长时间线（档案本身和它的修改记录不动）。"""
        member = self._profile_member(member)
        return {"member": member, "deleted": self.growth.log.clear(member)}

    def member_events_count(self) -> dict[str, Any]:
        return {"events": self.rt.repo.count_member_events()}
