"""B 站：成员「看过」视频、看完想发的私信和动态（L5，只用于生活）。

- **看过**：她自己上网（``core/wander``）逛 B 站时打开过的视频页，或你在网页上让她看的链接 →
  下载一份最低清晰度的原片（``yt-dlp``，不超过 ``MAX_SECONDS`` 秒、``MAX_MB`` MB，一次一条，
  不批量抓）→ 均匀取 ``FRAMES`` 帧存进聊天媒体目录 → 交给感官（声音转写 + 宫格图看图）写成一段
  「看到了什么」→ 原片删掉，只留几帧和这段记录。之后记一件生活小事（朋友圈、日记、记忆接着用）。
- **打卡**：看到的东西交给旅行打卡（判定卡 → 旅行日记），不值得就不打。
- **私信和动态**：看完按每天额度自动写（``bili_share`` 提示词，可以都不写），**只存在本机的列表里**
  给你事后看，**不会用任何账号发到 B 站或别的网站**（真发要你另外确认）。
- 额度写死在下面；一键总开关 ``member_bili_auto``（关掉后不自动看、不自动写私信 / 动态，
  你手动让她看的照样能看）。花费记在 ``travel`` 步骤，算生活每日上限。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from roundtable.core.chat import ChatMember
from roundtable.core.member.checkin import card as ck
from roundtable.core.member.checkin import store
from roundtable.core.storage.repository import iso

log = logging.getLogger(__name__)

# 额度（写死；用户 2026-11-02 选「全部自动」）
WATCH_PER_DAY = 2  # 全体每天最多自动看几个视频
WATCH_PER_MEMBER_DAY = 1
DM_PER_DAY = 3  # 全体每天最多几条私信
POST_PER_DAY = 3  # 全体每天最多几条动态
PER_MEMBER_DAY = 1  # 每位每天私信、动态各最多 1 条
MAX_SECONDS = 600
MAX_MB = 80
FRAMES = 4
FRAME_SIDE = 512
SHARE_USD = 0.003
LOOK_USD = 0.004
FETCH_TIMEOUT_S = 240
SEEN_CHARS = 600
_BV = re.compile(r"(?:bilibili\.com/video/|b23\.tv/)(BV[0-9A-Za-z]{10})")


class BiliUnavailable(RuntimeError):
    """电脑上没有 yt-dlp、下载失败或视频太长。"""


@dataclass(frozen=True)
class Fetched:
    data: bytes
    title: str
    seconds: float = 0.0


Fetch = Callable[[str], Awaitable[Fetched]]


def bv_of(url: str) -> str:
    m = _BV.search(url or "")
    return m.group(1) if m else ""


def video_url(bv: str) -> str:
    return f"https://www.bilibili.com/video/{bv}"


def _ytdlp() -> list[str] | None:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    try:
        import yt_dlp  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, "-m", "yt_dlp"]


async def ytdlp_fetch(url: str) -> Fetched:
    """用 yt-dlp 下载一条最低清晰度的原片（只下这一条，用完就删）。"""
    cmd = _ytdlp()
    if cmd is None:
        raise BiliUnavailable("电脑上没有 yt-dlp（pip install yt-dlp）")
    with tempfile.TemporaryDirectory(prefix="rt-bili-") as tmp:
        out = Path(tmp) / "v.%(ext)s"
        args = [
            *cmd,
            "-f",
            "worst[ext=mp4]/worst",
            "--no-playlist",
            "--max-filesize",
            f"{MAX_MB}M",
            "--match-filter",
            f"duration<={MAX_SECONDS}",
            "-j",
            "--no-simulate",
            "-o",
            str(out),
            url,
        ]
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), FETCH_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            raise BiliUnavailable("下载超时") from None
        files = [p for p in Path(tmp).iterdir() if p.is_file()]
        if proc.returncode != 0 or not files:
            raise BiliUnavailable("下载失败（视频太长、太大或打不开）")
        info: dict[str, Any] = {}
        for line in stdout.decode("utf-8", "replace").splitlines():
            try:
                info = json.loads(line)
            except ValueError:
                continue
        data = files[0].read_bytes()
        return Fetched(data, str(info.get("title") or "")[:80], float(info.get("duration") or 0))


def _local_day(row: dict[str, Any], now: datetime) -> str:
    try:
        at = datetime.fromisoformat(str(row.get("created_at") or ""))
    except ValueError:
        return ""
    return at.astimezone(now.tzinfo).date().isoformat()


class Bili:
    def __init__(
        self,
        rt: Any,
        allow: Callable[[float], bool],
        checkin: ck.Checkin,
        members: Callable[[], list[ChatMember]],
    ) -> None:
        self.rt = rt
        self.allow = allow
        self.checkin = checkin
        self.members = members

    @property
    def fetch(self) -> Fetch:
        return getattr(self.rt, "bili_fetch", None) or ytdlp_fetch

    def auto_on(self) -> bool:
        return store.switch(self.rt, store.AUTO_KEY, True)

    def enabled(self) -> bool:
        life = self.rt.config.roundtable.chat.life
        return life.enabled and life.story.enabled and life.story.travel.enabled

    # --- 读 -------------------------------------------------------------------------

    def view(self, limit: int = 30) -> dict[str, Any]:
        data = store.bili(self.rt)
        return {
            "auto": self.auto_on(),
            "photo": self.checkin.photo_on(),
            "external": False,  # 私信、动态只在本机列表里，不发到外部网站
            "quota": {
                "watch": WATCH_PER_DAY,
                "dm": DM_PER_DAY,
                "post": POST_PER_DAY,
                "per_member": PER_MEMBER_DAY,
            },
            "watched": list(reversed(data["watched"][-limit:])),
            "outbox": list(reversed(data["outbox"][-limit * 2 :])),
        }

    def _today(self, rows: list[dict[str, Any]], at: datetime) -> list[dict[str, Any]]:
        d = at.date().isoformat()
        return [r for r in rows if str(r.get("at", ""))[:10] == d]

    def candidates(self, now: datetime) -> list[tuple[str, str, str]]:
        """今天她自己逛 B 站时打开过、还没看过的视频页：[(成员, BV, 页面标题)]。"""
        from roundtable.core.wander.wander import wander_room

        seen = {(w["member"], w["bv"]) for w in store.bili(self.rt)["watched"]}
        day = now.date().isoformat()
        out: list[tuple[str, str, str]] = []
        for m in self.members():
            if not m.available:
                continue
            for row in self.rt.repo.chat_messages(wander_room(m.id), limit=100):
                w = (row.get("meta") or {}).get("wander") or {}
                bv = bv_of(str(w.get("url") or ""))
                if not bv or w.get("kind") != "page" or _local_day(row, now) != day:
                    continue
                if (m.id, bv) not in seen:
                    seen.add((m.id, bv))
                    out.append((m.id, bv, str(w.get("title") or "")))
        return out

    # --- 推进 -----------------------------------------------------------------------

    async def tick(self, now: datetime) -> int:
        """自动：按额度看今天逛到的视频，看完打卡、写私信 / 动态。返回看了几个。"""
        if not (self.enabled() and self.auto_on()):
            return 0
        data = store.bili(self.rt)
        today = self._today(data["watched"], now)
        n = 0
        for member, bv, title in self.candidates(now):
            if len(today) + n >= WATCH_PER_DAY:
                break
            if sum(r["member"] == member for r in today) >= WATCH_PER_MEMBER_DAY:
                continue
            rec = await self.watch(member, video_url(bv), now, title=title)
            if rec is None:
                break  # 没钱、没装 yt-dlp：今天先不看了
            n += 1
        return n

    async def watch(
        self, member_id: str, url: str, now: datetime, *, title: str = "", share: bool = True
    ) -> dict[str, Any] | None:
        """看一个视频（自动或你手动让她看）。下载失败、没钱时为 None。"""
        member = next((m for m in self.members() if m.id == member_id), None)
        bv = bv_of(url)
        if member is None or not bv or not self.allow(LOOK_USD):
            return None
        try:
            got = await self.fetch(video_url(bv))
        except BiliUnavailable as e:
            log.info("B 站视频没看成：%s", e)
            return None
        if got.seconds and got.seconds > MAX_SECONDS:
            return None
        frames = self._frames(got.data)
        seen = await self._look(member, bv, got.data)
        rec = {
            "id": f"{member.id}:{bv}:{now.date().isoformat()}",
            "member": member.id,
            "bv": bv,
            "url": video_url(bv),
            "title": got.title or title or bv,
            "at": now.isoformat(),
            "seen": seen,
            "frames": frames,
            "checkin": None,
        }
        self._life_event(member, rec, now)
        if seen:
            scene = ck.Scene(
                member.id,
                "bili",
                rec["title"],
                [seen],
                now,
                url=rec["url"],
                ref=rec["id"],
            )
            done = await self.checkin.run(member, scene)
            rec["checkin"] = done["id"] if done else None
        data = store.bili(self.rt)
        data["watched"].append(rec)
        store.save_bili(self.rt, data)
        if share and self.auto_on():
            await self.share(member, rec, now)
        return rec

    def _frames(self, data: bytes) -> list[dict[str, Any]]:
        """均匀取几帧存进聊天媒体目录（网页上能看到她看过的画面）；没装 av / 存不了时为空。"""
        from roundtable.core.chat_actions import generated_item
        from roundtable.core.chat_media import ChatMediaStore, MediaError
        from roundtable.core.perception.senses import SenseUnavailable, clock, sample_frames

        if self.rt.chat_media_dir is None:
            return []
        try:
            frames = sample_frames(data, FRAMES, FRAME_SIDE)
        except SenseUnavailable:
            return []
        out = []
        media = ChatMediaStore(self.rt.chat_media_dir)
        for f in frames:
            try:
                item = generated_item(media, f.jpeg, "image/jpeg")
            except (MediaError, ValueError):
                continue
            out.append({"t": clock(f.at), "id": item["id"]})
        return out

    async def _look(self, member: ChatMember, bv: str, data: bytes) -> str:
        """交给感官看一遍（声音转写 + 宫格图），记在生活的步骤上。"""
        from roundtable.core.member.checkin.card import STEP
        from roundtable.core.perception import Perceiver, PerceptionCache, SenseItem

        perceiver = Perceiver(self.rt, PerceptionCache(self.rt.perception_dir), step=STEP)
        item = SenseItem(f"{bv}.mp4", "video", data, "video/mp4")
        try:
            cards = await perceiver.perceive(f"bili:{member.id}", [item], "fast")
        except Exception as e:  # noqa: BLE001 — 解不开、模型挑不到：就当没看清
            log.info("B 站视频没看清：%s", type(e).__name__)
            return ""
        return cards[0].text[:SEEN_CHARS] if cards else ""

    def _life_event(self, member: ChatMember, rec: dict[str, Any], now: datetime) -> None:
        """记一件生活小事：朋友圈、日记、记忆接着用（不写成聊天）。"""
        topic = "\n".join(
            [
                f"时间：{now.hour} 点 {now.minute} 分",
                "地点：自己的宿舍（刷B站看了个视频）",
                f"看过：《{rec['title']}》{('：' + rec['seen'][:80]) if rec['seen'] else ''}",
            ]
        )
        self.rt.repo.add_life_event(
            iso(now), "solo", [member.id], place=f"dorm:{member.id}", topic=topic
        )

    async def share(self, member: ChatMember, rec: dict[str, Any], now: datetime) -> list[str]:
        """按额度写私信 / 动态（可以都不写）；只存本机列表。返回写了哪几种。"""
        data = store.bili(self.rt)
        today = self._today(data["outbox"], now)

        def left(kind: str, cap: int) -> bool:
            rows = [r for r in today if r["kind"] == kind]
            mine = sum(r["member"] == member.id for r in rows)
            return len(rows) < cap and mine < PER_MEMBER_DAY

        kinds = [k for k, cap in (("dm", DM_PER_DAY), ("post", POST_PER_DAY)) if left(k, cap)]
        if not kinds or not rec.get("seen") or not self.allow(SHARE_USD):
            return []
        journal = ""
        if rec.get("checkin"):
            hit = next((r for r in store.checkins(self.rt) if r["id"] == rec["checkin"]), None)
            if hit and hit.get("journal"):
                journal = hit["journal"]["title"]
        want = {
            ("dm", "post"): "要不要给用户发一条私信、要不要发一条动态。",
            ("dm",): "要不要给用户发一条私信（今天的动态已经发过了，post 留空）。",
            ("post",): "要不要发一条动态（今天已经给用户发过私信了，dm 留空）。",
        }[tuple(kinds)]
        text = await self.checkin._call(
            member,
            "bili_share",
            SHARE_USD,
            title=rec["title"],
            seen=rec["seen"],
            journal=journal or "（没有写）",
            want=want,
        )
        if not text:
            return []
        try:
            out = ck.extract_json_object(text)
        except ck.JSONOutputError:
            return []
        made = []
        data = store.bili(self.rt)
        for kind in kinds:
            words = str(out.get(kind) or "").strip()[:200]
            if not words:
                continue
            data["outbox"].append(
                {
                    "id": f"{rec['id']}:{kind}",
                    "member": member.id,
                    "kind": kind,
                    "text": words,
                    "title": rec["title"],
                    "url": rec["url"],
                    "journal": journal,
                    "at": (now + timedelta(seconds=len(made))).isoformat(),
                    "external": False,
                }
            )
            made.append(kind)
        store.save_bili(self.rt, data)
        return made
