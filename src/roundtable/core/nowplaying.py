"""终端音乐卡片的「电脑上正在放的歌」：读 Windows 系统媒体控制（SMTC），并发送播放控制。

QQ 音乐等桌面播放器会把正在放的歌报给 Windows（就是按键盘媒体键时屏幕左上角弹出的那张卡片）。
这里只读这些公开信息（歌名、歌手、专辑、封面缩略图、进度、播放状态），并通过同一个系统接口
发送 播放 / 暂停 / 上一首 / 下一首 / 跳到某处。不登录任何账号、不抓取或保存音频、不调用播放器的
私有接口；歌和封面不写进数据库或磁盘，只在内存里留最近一份给网页显示。

隐私：默认只看「QQ 音乐」这一路（按应用 id 里的关键字认），浏览器标签页、视频软件等别的应用
正在放什么一律不报；用户在网页里选「任意播放器」才看系统当前那一路。

不在 Windows 上，或没装 pywinrt（`pip install -e ".[windows]"`）时返回 available=False
与原因，绝不抛错。
测试中注入 FakeMediaSource。另外：用户本机 data/avatars/music_covers/ 里的图片可以选作本地音乐的封面
（不进 git），这里只列出和读取，路径必须落在该目录内。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

# 认得出的播放器：键、显示名、应用 id 里的关键字（小写）。
# 不在表里的播放器只有选「任意播放器」时才看。
PLAYERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("qqmusic", "QQ 音乐", ("qqmusic",)),
    ("cloudmusic", "网易云音乐", ("cloudmusic",)),
    ("spotify", "Spotify", ("spotify",)),
)
ANY = "any"
ACTIONS = ("play", "pause", "toggle", "next", "prev", "seek")
CACHE_S = 0.4  # 网页每秒问一次；同一时间多处在问时少碰系统接口
TIMEOUT_S = 3.0
MAX_COVER_BYTES = 4 * 1024 * 1024
COVERS_DIR = "music_covers"
COVER_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
MAX_COVERS = 60
# 封面上的动态特效（画在网页里）；每张封面配哪种写在封面目录的 effects.json：
# {"<名字>": {"fx": "snow", "color": "#rrggbb"}}
FX = ("rain", "snow", "leaves", "petals", "stars", "bubbles")
EFFECTS_FILE = "effects.json"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class Playing:
    """系统媒体控制里的一路播放（一个应用一路）。"""

    app_id: str
    title: str = ""
    artist: str = ""
    album: str = ""
    status: str = "stopped"  # playing / paused / stopped / changing
    position: float | None = None  # 秒，已按「上次更新到现在」推算好
    duration: float | None = None
    can: frozenset[str] = frozenset()  # play / pause / next / prev / seek
    cover: bytes | None = field(default=None, repr=False)


class MediaSource(Protocol):
    def available(self) -> str | None:
        """不可用时返回原因（给用户看的中文）。"""

    def sessions(self) -> list[Playing]:
        """所有正在报告的播放器；第一个是系统当前那一路。"""

    def control(self, app_id: str, action: str, position: float | None = None) -> bool: ...


class MediaUnavailable(RuntimeError):
    pass


def cover_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def player_of(app_id: str) -> tuple[str, str] | None:
    low = app_id.lower()
    for key, label, words in PLAYERS:
        if any(w in low for w in words):
            return key, label
    return None


class NowPlaying:
    """给服务层用：挑出用户选的那一路，转成网页要的 dict；封面单独取（带版本号便于缓存）。"""

    def __init__(self, source: MediaSource, clock: Callable[[], float] = time.monotonic) -> None:
        self.source = source
        self.clock = clock
        self._at = -1e9
        self._rows: list[Playing] = []
        self._lock = threading.Lock()

    def _sessions(self) -> list[Playing]:
        with self._lock:
            if self.clock() - self._at > CACHE_S:
                self._rows = self.source.sessions()
                self._at = self.clock()
            return self._rows

    def _pick(self, player: str) -> Playing | None:
        rows = self._sessions()
        if player == ANY:
            return rows[0] if rows else None
        return next((r for r in rows if (player_of(r.app_id) or ("",))[0] == player), None)

    def view(self, player: str = "qqmusic") -> dict[str, Any]:
        player = player if player == ANY or any(k == player for k, _, _ in PLAYERS) else "qqmusic"
        base: dict[str, Any] = {
            "player": player,
            "players": [{"key": k, "label": label} for k, label, _ in PLAYERS]
            + [{"key": ANY, "label": "任意播放器"}],
        }
        reason = self.source.available()
        if reason:
            return {**base, "available": False, "reason": reason, "now": None, "found": []}
        try:
            rows = self._sessions()
            cur = self._pick(player)
        except MediaUnavailable as exc:
            return {**base, "available": False, "reason": str(exc), "now": None, "found": []}
        # 只告诉网页认得出的播放器在不在（不列别的应用，免得把浏览器在放什么也报出去）
        found = sorted({p[1] for r in rows if (p := player_of(r.app_id))})
        now = None
        if cur is not None:
            known = player_of(cur.app_id)
            now = {
                "source": known[1] if known else "系统播放器",
                "title": cur.title,
                "artist": cur.artist,
                "album": cur.album,
                "status": cur.status,
                "position": cur.position,
                "duration": cur.duration,
                "can": sorted(cur.can),
                "cover": (
                    f"/api/nowplaying/cover?player={player}&v={hashlib.sha1(cur.cover).hexdigest()[:12]}"
                    if cur.cover
                    else None
                ),
            }
        return {**base, "available": True, "reason": None, "now": now, "found": found}

    def cover(self, player: str = "qqmusic") -> tuple[bytes, str] | None:
        cur = self._pick(player) if not self.source.available() else None
        if cur is None or not cur.cover:
            return None
        mime = cover_mime(cur.cover)
        return (cur.cover, mime) if mime else None

    def control(self, player: str, action: str, position: float | None = None) -> bool:
        if action not in ACTIONS:
            raise ValueError(f"action 应为 {' / '.join(ACTIONS)}")
        if self.source.available():
            return False
        cur = self._pick(player)
        if cur is None:
            return False
        if action == "toggle":
            action = "pause" if cur.status == "playing" else "play"
        ok = self.source.control(cur.app_id, action, position)
        with self._lock:
            self._at = -1e9  # 下一次马上重读，网页立刻看到新状态
        return ok


class FakeMediaSource:
    """测试用：一组可改的播放器状态，记下收到的控制。"""

    def __init__(self, rows: list[Playing] | None = None, reason: str | None = None) -> None:
        self.rows = list(rows or [])
        self.reason = reason
        self.calls: list[tuple[str, str, float | None]] = []

    def available(self) -> str | None:
        return self.reason

    def sessions(self) -> list[Playing]:
        return list(self.rows)

    def control(self, app_id: str, action: str, position: float | None = None) -> bool:
        self.calls.append((app_id, action, position))
        for i, r in enumerate(self.rows):
            if r.app_id != app_id:
                continue
            if action == "play":
                self.rows[i] = replace(r, status="playing")
            elif action == "pause":
                self.rows[i] = replace(r, status="paused")
            elif action == "seek":
                self.rows[i] = replace(r, position=position)
            return True
        return False


# ---------- Windows：pywinrt 读系统媒体控制 ----------

_STATUS = {2: "changing", 4: "playing", 5: "paused"}


class SmtcSource:
    """Windows 系统媒体控制。

    pywinrt 的接口是异步的：放在一条专用线程的事件循环里跑，服务线程同步等结果。
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mgr: Any = None
        self._reason: str | None = None
        self._lock = threading.Lock()
        try:
            from winrt.windows.media.control import (  # noqa: F401
                GlobalSystemMediaTransportControlsSessionManager,
            )
        except Exception:  # noqa: BLE001 - 非 Windows 或没装 pywinrt
            import sys

            self._reason = (
                "要在 Windows 上运行圆桌才能连 QQ 音乐"
                if sys.platform != "win32"
                else '还没装系统媒体组件：在圆桌目录运行 pip install -e ".[windows]" 后重启服务'
            )

    def available(self) -> str | None:
        return self._reason

    def _run(self, coro: Any) -> Any:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="smtc", daemon=True).start()
                self._loop = loop
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(TIMEOUT_S)
        except TimeoutError:
            fut.cancel()
            raise MediaUnavailable("系统媒体控制没有响应，稍后再试") from None

    async def _manager(self) -> Any:
        if self._mgr is None:
            from winrt.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as Manager,
            )

            self._mgr = await Manager.request_async()
        return self._mgr

    async def _read(self) -> list[Playing]:
        mgr = await self._manager()
        cur = mgr.get_current_session()
        cur_id = cur.source_app_user_model_id if cur is not None else None
        out: list[Playing] = []
        for s in list(mgr.get_sessions()):
            try:
                out.append(await self._one(s))
            except Exception:  # noqa: BLE001 - 某个播放器读不出来就跳过，不影响其他
                continue
        out.sort(key=lambda p: p.app_id != cur_id)
        return out

    async def _one(self, s: Any) -> Playing:
        props = await s.try_get_media_properties_async()
        info = s.get_playback_info()
        tl = s.get_timeline_properties()
        status = _STATUS.get(int(info.playback_status), "stopped")
        c = info.controls
        can = {
            k
            for k, ok in (
                ("play", c.is_play_enabled),
                ("pause", c.is_pause_enabled),
                ("next", c.is_next_enabled),
                ("prev", c.is_previous_enabled),
                ("seek", c.is_playback_position_enabled),
            )
            if ok
        }
        duration = (tl.end_time - tl.start_time).total_seconds()
        position: float | None = (tl.position - tl.start_time).total_seconds()
        if status == "playing":
            # 播放器只在切歌、拖动时更新进度：按上次更新到现在补上
            with contextlib.suppress(Exception):
                position += (datetime.now(UTC) - tl.last_updated_time).total_seconds()
        if duration <= 0:
            duration, position = None, None
        elif position is not None:
            position = min(max(0.0, position), duration)
        cover = None
        if props.thumbnail is not None:
            try:
                cover = await self._thumb(props.thumbnail)
            except Exception:  # noqa: BLE001
                cover = None
        return Playing(
            app_id=s.source_app_user_model_id or "",
            title=props.title or "",
            artist=props.artist or "",
            album=props.album_title or "",
            status=status,
            position=position,
            duration=duration,
            can=frozenset(can),
            cover=cover,
        )

    async def _thumb(self, ref: Any) -> bytes | None:
        from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions

        stream = await ref.open_read_async()
        size = min(int(stream.size), MAX_COVER_BYTES)
        if size <= 0:
            return None
        buf = Buffer(size)
        await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
        reader = DataReader.from_buffer(buf)
        data = bytearray(buf.length)
        reader.read_bytes(data)
        return bytes(data)

    async def _control(self, app_id: str, action: str, position: float | None) -> bool:
        mgr = await self._manager()
        s = next((x for x in mgr.get_sessions() if x.source_app_user_model_id == app_id), None)
        if s is None:
            return False
        if action == "play":
            return bool(await s.try_play_async())
        if action == "pause":
            return bool(await s.try_pause_async())
        if action == "next":
            return bool(await s.try_skip_next_async())
        if action == "prev":
            return bool(await s.try_skip_previous_async())
        if action == "seek" and position is not None:
            start = s.get_timeline_properties().start_time.total_seconds()
            ticks = int((start + max(0.0, position)) * 10_000_000)
            return bool(await s.try_change_playback_position_async(ticks))
        return False

    def sessions(self) -> list[Playing]:
        try:
            return self._run(self._read())
        except MediaUnavailable:
            raise
        except Exception:  # noqa: BLE001 - 系统接口出错时当作没有播放器
            self._mgr = None
            return []

    def control(self, app_id: str, action: str, position: float | None = None) -> bool:
        try:
            return bool(self._run(self._control(app_id, action, position)))
        except MediaUnavailable:
            return False
        except Exception:  # noqa: BLE001
            self._mgr = None
            return False


# ---------- 本地封面（用户本机素材，不进 git） ----------


def list_covers(folder: Path | None) -> list[dict[str, str]]:
    """封面目录里的图片：[{name, url}]，按文件名排序；目录不存在时为空。"""
    if folder is None or not folder.is_dir():
        return []
    fx = cover_effects(folder)
    out = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name):
        if p.is_file() and p.suffix.lower() in COVER_TYPES:
            name = p.stem.removeprefix("cover_")
            url = f"/api/music/covers/{quote(p.name)}"
            out.append({"name": name, "url": url, **fx.get(name, {})})
        if len(out) >= MAX_COVERS:
            break
    return out


def cover_effects(folder: Path) -> dict[str, dict[str, str]]:
    """封面目录里 effects.json 写的特效；文件没有、读不了或写错的条目一律忽略。"""
    p = folder / EFFECTS_FILE
    try:
        if not p.is_file() or p.stat().st_size > 64 * 1024:
            return {}
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, str]] = {}
    for name, v in raw.items() if isinstance(raw, dict) else ():
        if not isinstance(v, dict) or v.get("fx") not in FX:
            continue
        item = {"fx": v["fx"]}
        if isinstance(v.get("color"), str) and _HEX.match(v["color"]):
            item["color"] = v["color"].lower()
        out[str(name)[:40]] = item
    return out


def read_cover(folder: Path | None, name: str) -> tuple[bytes, str] | None:
    """读一张封面；只允许目录里直接放着的 png / jpg / webp（防路径穿越）。"""
    if folder is None or not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    mime = COVER_TYPES.get(Path(name).suffix.lower())
    if mime is None:
        return None
    root = folder.resolve()
    p = (root / name).resolve()
    if p.parent != root or not p.is_file() or p.stat().st_size > 2 * MAX_COVER_BYTES:
        return None
    return p.read_bytes(), mime
