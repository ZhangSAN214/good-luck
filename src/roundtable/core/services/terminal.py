"""终端首页：游戏模式、自检、音乐卡片、一起看、出图渠道、公告栏、邮件栏、主时区、日历。

从 service.py 拆出（代码精简）。
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import subprocess
import time
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from roundtable.core import firstaid
from roundtable.core.allocation import coordinator_name
from roundtable.core.backup import BackupError
from roundtable.core.browse import REFRESH_KEY, feeds_of, set_feeds
from roundtable.core.budget.channels import MonthSpend
from roundtable.core.changelog import changelog_view, load_changelog, numbered
from roundtable.core.chat import ROOM, dm_room
from roundtable.core.config.loader import DEFAULT_CONFIG_DIR, ConfigError
from roundtable.core.hometz import home_zone
from roundtable.core.mail import MailAIError, MailBusy
from roundtable.core.nowplaying import NowPlaying, SmtcSource
from roundtable.core.office import payslip
from roundtable.core.providers import KeyRing
from roundtable.core.runtime import data_dir
from roundtable.core.screen import ScreenUnavailable, new_qr_pairing, qr_pairing_payload
from roundtable.core.services.common import ADB_QR_TTL_S, ServiceError, _has_tk, log


class TerminalService:
    """终端首页：游戏模式、自检、音乐卡片、一起看、出图渠道、公告栏、邮件栏、主时区、日历。"""

    # --- 游戏模式（core/gamemode.py，docs/GAMEMODE.md） ---------------------------------

    def _background_paused(self) -> bool:
        gm = self.rt.gamemode
        return gm is not None and gm.background_paused()

    def start_gamemode(self) -> None:
        """后台每隔几秒看一次：忙不忙、调 CPU 优先级、显卡空了补跑排队的活（只在正式启动时）。"""
        if self._gamemode_task is None and self.rt.gamemode is not None:
            self._gamemode_task = asyncio.get_running_loop().create_task(self._gamemode_loop())

    async def _gamemode_loop(self) -> None:
        gm = self.rt.gamemode
        assert gm is not None
        while True:
            try:
                await self.gamemode_tick()
            except Exception:  # noqa: BLE001 - 出错不影响其他功能，下次再看
                log.exception("游戏模式：检查出错")
            await asyncio.sleep(gm.rules.poll_seconds)

    async def gamemode_tick(self) -> dict[str, Any]:
        """看一次（列程序在线程里做，不卡网页）→ 调 CPU 优先级 → 能用显卡就补跑一件。"""
        gm = self.rt.gamemode
        if gm is None:
            return {}
        await asyncio.to_thread(gm.poll)
        gm.apply_priority()
        ran = await gm.drain_one()
        return gm.view() | {"ran": ran}

    def gamemode(self) -> dict[str, Any]:
        gm = self.rt.gamemode
        if gm is None:
            raise ServiceError("游戏模式不可用", 409)
        return gm.view()

    def set_gamemode(self, manual: bool) -> dict[str, Any]:
        """网页上的手动开关：开了马上让出显卡，关了以后照常等显卡连续空闲几分钟。"""
        gm = self.rt.gamemode
        if gm is None:
            raise ServiceError("游戏模式不可用", 409)
        gm.set_manual(bool(manual))
        gm.apply_priority()
        return gm.view()

    async def _deferred_moment_photo(self, payload: dict[str, Any]) -> None:
        """排队的朋友圈配图：显卡空了补画，画好挂到原来那条动态上。"""
        await self.life.moments.finish_photo(payload)

    def start_life(self) -> None:
        """后台推进成员的生活（只在正式启动的服务里调用；测试直接调 life_tick）。"""
        if self._life_task is None and self.rt.config.roundtable.chat.life.enabled:
            self._life_task = asyncio.get_running_loop().create_task(self._life_loop())

    async def _life_loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):  # 生活出错不影响其他功能，下次再试
                if not self._background_paused():
                    await self.life_tick()
            await asyncio.sleep(self.rt.config.roundtable.chat.life.tick_minutes * 60)

    def start_backup(self) -> None:
        """后台每天备份一次整库（只在正式启动的服务里调用；测试直接调 backup_tick）。"""
        if self._backup_task is None and self.backup.enabled:
            self._backup_task = asyncio.get_running_loop().create_task(self._backup_loop())

    async def _backup_loop(self) -> None:
        while True:
            try:
                self.backup_tick()  # 同一个数据库连接，在事件循环里做（整库一两秒）
            except Exception:  # noqa: BLE001 - 备份出错不影响其他功能，下次再试
                log.exception("整库备份：没备份成")
            await asyncio.sleep(self.rt.config.roundtable.backup.check_minutes * 60)

    def backup_tick(self) -> dict[str, Any] | None:
        """今天还没备份就备一份（按本地日期）。"""
        got = self.backup.daily(self._local_now())
        if got is not None:
            log.info("整库备份：已备份 %s", got.name)
        return got.view() if got is not None else None

    def backup_status(self) -> dict[str, Any]:
        return self.backup.status()

    def backup_checkpoint(self, label: str) -> dict[str, Any]:
        try:
            got = self.backup.checkpoint(label, self._local_now())
        except BackupError as exc:
            raise ServiceError(str(exc)) from None
        return {"saved": got.view(), **self.backup.status()}

    def backup_restore(self, name: str) -> dict[str, Any]:
        """把一份备份写回数据库（先自动存「恢复前」存档点）；之后要重启圆桌。"""
        try:
            out = self.backup.restore(name, self._local_now())
        except BackupError as exc:
            raise ServiceError(str(exc)) from None
        log.warning("整库备份：已恢复 %s（恢复前存档点 %s）", name, out["before"]["name"])
        return {**out, **self.backup.status()}

    # ---- 「出问题点这里」自检（docs/FIRSTAID.md）：只读，不改任何东西 ----

    def _db_path(self) -> Path | None:
        row = self.rt.repo.conn.execute("PRAGMA database_list").fetchone()
        path = row[2] if row is not None else ""
        return Path(path) if path else None

    def _app_version(self) -> str | None:
        try:
            rows = numbered(load_changelog(self.rt.changelog))
        except (ConfigError, OSError, ValueError):
            return None
        return rows[-1][0] if rows else None

    def firstaid_findings(self) -> list[firstaid.Finding]:
        now = self._local_now()
        cfg = self.rt.config
        db_path = self._db_path()
        names = [c.key_env for c in cfg.models.channels.values() if c.key_env]
        names += [p.key_env for p in cfg.models.search_providers.values() if p.key_env]
        keys = KeyRing.from_env(names, dotenv_path=DEFAULT_CONFIG_DIR.parent / ".env")
        channels = self.status()["channels"]
        found = firstaid.offline_findings(
            db_path=db_path,
            data=db_path.parent if db_path is not None else None,
            config_error=None,
            keys_present=[n for n in names if keys.has(n)],
            keys_missing=[n for n in names if not keys.has(n)],
            channels_ok=any(c["available"] for c in channels.values()),
            backup_files=[f.view() for f in self.backup.files()],
            backup_folder=self.backup.folder,
            backup_enabled=self.backup.enabled,
            now=now,
        )
        found += firstaid.check_tasks(
            {
                "每天备份": self._backup_task,
                "成员生活": self._life_task,
                "革新例会": self._evolution_task,
            }
        )
        found += firstaid.check_channels(channels)
        month, day = self.rt.budget.status()
        found += firstaid.check_budget(
            {
                "spent_usd": month.spent_usd,
                "limit_usd": month.limit_usd,
                "warn": month.warn,
                "exhausted": month.exhausted,
            },
            None
            if day is None
            else {
                "spent_usd": day.spent_usd,
                "limit_usd": day.limit_usd,
                "warn": day.warn,
                "exhausted": day.exhausted,
            },
        )
        since = datetime.now(UTC) - timedelta(days=1)
        found += firstaid.check_calls(*firstaid.recent_call_errors(self.rt.repo.conn, since))
        found += firstaid.check_sandbox(self.rt.sandbox()[1])
        found += firstaid.check_dsh(self.dsh_status() if self.dsh is not None else None)
        return found

    def firstaid(self) -> dict[str, Any]:
        """自检结果：整体结论、四类现场、每一项（红黄绿 + 一句话怎么办）。"""
        return firstaid.summarize(self.firstaid_findings())

    def firstaid_report(self) -> dict[str, Any]:
        """可以整段转交的体检报告（纯文本，不含聊天内容和 key）。"""
        summary = self.firstaid()
        meta = firstaid.meta_info(self._app_version(), self._local_now())
        return {"text": firstaid.render_report(summary, meta), "level": summary["level"]}

    def start_evolution(self) -> None:
        """后台按时开革新例会（只在正式启动的服务里调用；测试直接调 evolution_tick）。"""
        rules = self.rt.config.roundtable.evolution
        if self._evolution_task is None and rules.enabled and rules.auto:
            self._evolution_task = asyncio.get_running_loop().create_task(self._evolution_loop())

    async def _evolution_loop(self) -> None:
        while True:
            try:
                if not self._background_paused():
                    await self.evolution_scout_tick()
                    await self.evolution_plugins_tick()
                    self.evolution_tick()
            except Exception:  # noqa: BLE001 - 例会出错不影响其他功能，下次再试
                log.exception("革新例会：自动检查出错")
            await asyncio.sleep(self.rt.config.roundtable.evolution.tick_minutes * 60)

    async def life_tick(self) -> dict[str, int]:
        """推进一次生活（开机后第一次会补上关机期间的时间），再推进一次成员引擎（不花钱）。"""
        now = self._local_now()
        out = await self.life.tick(now)
        with contextlib.suppress(Exception):  # 日历出错不影响生活
            self.calendar.tick(now, self.engine.bus.publish)
        with contextlib.suppress(Exception):  # 引擎出错不影响生活
            await self.engine.tick(now)
        with contextlib.suppress(Exception):  # 醒来回消息出错不影响生活
            await self.member_turns(now)
        with contextlib.suppress(Exception):  # 成长出错不影响生活，下次推进再试
            await self.growth.tick(now)
        with contextlib.suppress(Exception):  # 见解出错不影响生活，下次推进再试
            await self.insight.tick(now)
        return out

    async def member_turns(self, now: datetime) -> int:
        """成员自己开口的时候（不是回应你刚发的那条）：睡着没看到你的消息、醒了来回。
        返回开口的人数。"""
        from roundtable.core.member.rhythm import Rhythm

        rhythm, n = Rhythm(self.rt), 0
        for entry in rhythm.due(now):
            try:
                chat = self.chats.room(entry["room"])
            except KeyError:
                continue
            member = next((m for m in chat.members() if m.id == entry["member"]), None)
            mine = [r for r in chat.recent() if r["author"] == "user" and r["id"] >= entry["msg"]]
            if member is None or not member.available or not mine:
                continue
            chat.notes[member.id] = rhythm.late_note(entry)
            said = await chat.replies(mine[-1]["id"], [member])
            n += bool(said)
        return n + await self._reach_out(now)

    async def _reach_out(self, now: datetime) -> int:
        """主动找你：这次推进最多一位，有真事由才开口（core/member/reach.py 挑时机）。"""
        from roundtable.core.member.reach import Reach

        reach = Reach(self.rt)
        members = {m.id: m for m in self.chats.everyone() if m.available}
        urge = reach.pick(list(members), dm_room, now)
        if urge is None:
            return 0
        chat = self.chats.room(dm_room(urge.member))
        rows = chat.recent()
        chat.notes[urge.member] = f"{urge.note}\n{reach.rules.tail}"
        if urge.reason == "longing":  # 想念记成她的一段情绪经历（意图卡会带上她自己理解的想念）
            from roundtable.core.member.feel import Feelings

            feel = Feelings(self.rt)
            feel.record(urge.member, feel.rules.missing, feel.rules.reach_miss, now)
        said = await chat.replies(rows[-1]["id"] if rows else 0, [members[urge.member]])
        chat.notes.pop(urge.member, None)
        if not said:
            return 0
        reach.sent(urge, said[0]["id"], now)
        return 1

    async def aclose(self) -> None:
        self.watch.stop("服务关闭，投屏停了")
        with contextlib.suppress(Exception):
            await self.bench.aclose()
            await self.supercomputer.aclose()
        if self.dsh is not None:
            with contextlib.suppress(Exception):
                await self.dsh.aclose()
        await self.wander.aclose()
        tasks = [*self._tasks.values(), *self._chat_tasks]
        tasks += [
            t
            for t in (
                self._life_task,
                self._evolution_task,
                self._backup_task,
                self._butler_task,
                self._weekly_task,
                self._weekly_force,
            )
            if t is not None
        ]
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.rt.aclose()

    # ---------- 音乐卡片：电脑上正在放的歌（QQ 音乐等，经系统媒体控制）与本地封面 ----------

    def _np(self) -> NowPlaying:
        if self._nowplaying is None:
            source = self.rt.nowplaying_source
            self._nowplaying = NowPlaying(source if source is not None else SmtcSource())
        return self._nowplaying

    def nowplaying(self, player: str = "qqmusic") -> dict[str, Any]:
        """只读：歌名、歌手、专辑、进度、能不能控制、封面地址。不存库、不调用模型。"""
        return self._np().view(player)

    def nowplaying_cover(self, player: str = "qqmusic") -> tuple[bytes, str]:
        found = self._np().cover(player)
        if found is None:
            raise ServiceError("没有封面", 404)
        return found

    def nowplaying_control(
        self, player: str, action: str, position: float | None = None
    ) -> dict[str, Any]:
        """播放 / 暂停 / 上一首 / 下一首 / 跳到某处：经系统媒体控制转给播放器。

        播放器不响应时 409。
        """
        try:
            ok = self._np().control(player, action, position)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        if not ok:
            raise ServiceError("播放器没有响应（可能没打开，或不支持这个操作）", 409)
        return self._np().view(player)

    # ---------- 投屏陪伴（docs/WATCH.md） ----------

    def watch_status(self, devices: bool = False) -> dict[str, Any]:
        """一起看的状态；devices=True 时另列可投的来源（会调 adb / 系统接口，较慢）。"""
        out = self.watch.status()
        if devices:
            out["devices"] = self.watch.devices()
            out["scrcpy"] = shutil.which(self.rt.config.roundtable.chat.watch.scrcpy) is not None
        return out

    def _adb(self) -> Any:
        src = self.watch.sources.get("android")
        if src is None or not hasattr(src, "pair"):
            raise ServiceError("这台电脑不能连安卓设备")
        return src

    def watch_adb_pair(self, host: str, port: int, code: str) -> dict[str, Any]:
        """安卓无线调试：用配对码配对（只建立 adb 连接）。配对后自动试着连上。"""
        src = self._adb()
        try:
            msg = src.pair(host, port, code)
        except ScreenUnavailable as e:
            raise ServiceError(str(e)) from None
        try:
            connected = src.connect()
        except ScreenUnavailable:
            connected = []
        return {"message": msg, "connected": connected}

    def watch_adb_connect(self, host: str = "", port: int = 0) -> dict[str, Any]:
        try:
            return {"connected": self._adb().connect(host, port)}
        except ScreenUnavailable as e:
            raise ServiceError(str(e)) from None

    def watch_adb_qr(self) -> dict[str, Any]:
        """安卓扫码配对：生成新的配对二维码（旧的作废），再用 watch_adb_qr_poll 等扫码。"""
        src = self._adb()
        try:
            src.qr_ready()
        except ScreenUnavailable as e:
            raise ServiceError(str(e)) from None
        name, password = new_qr_pairing()
        self._adb_qr = {"name": name, "password": password, "at": time.monotonic()}
        return {"payload": qr_pairing_payload(name, password), "expires_s": ADB_QR_TTL_S}

    def watch_adb_qr_poll(self) -> dict[str, Any]:
        """看手机扫了没有，扫了就配对并连上。state：waiting / connected / paired / expired / none"""
        qr = self._adb_qr
        if qr is None:
            return {"state": "none", "connected": []}
        if time.monotonic() - qr["at"] > ADB_QR_TTL_S:
            self._adb_qr = None
            return {"state": "expired", "connected": []}
        src = self._adb()
        try:
            addr = src.qr_pair(qr["name"], qr["password"])
        except ScreenUnavailable as e:
            self._adb_qr = None
            raise ServiceError(str(e)) from None
        if addr is None:
            return {"state": "waiting", "connected": []}
        self._adb_qr = None
        host = addr.rsplit(":", 1)[0]
        connected: list[str] = []
        for _ in range(3):  # 刚配对完手机要一两秒才广播连接端口
            try:
                connected = src.connect(host, 0)
                break
            except ScreenUnavailable:
                time.sleep(1.5)
        return {"state": "connected" if connected else "paired", "connected": connected}

    async def watch_start(self, source: str, watchers: list[str], consent: bool) -> dict[str, Any]:
        devices = await asyncio.to_thread(self.watch.devices)
        infos = {s["id"]: s for d in devices.values() for s in d["sources"]}
        if source not in infos:
            raise ServiceError("找不到这个画面来源（刷新一下列表）")
        try:
            self.watch.start(source, infos[source]["title"], watchers, consent=consent)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        task = asyncio.get_running_loop().create_task(self.watch.run())
        self.watch.attach(task)
        self._chat_tasks.add(task)
        task.add_done_callback(self._chat_tasks.discard)
        return self.watch.status()

    def watch_stop(self) -> dict[str, Any]:
        self.watch.stop()
        return self.watch.status()

    def watch_quiet(self, quiet: bool) -> dict[str, Any]:
        self.watch.set_quiet(quiet)
        return self.watch.status()

    def watch_frame(self) -> bytes:
        data = self.watch.preview()
        if data is None:
            raise ServiceError("现在没有画面", 404)
        return data

    def watch_messages(self, after: int = 0) -> dict[str, Any]:
        return {"messages": self.watch.messages(after), "typing": list(self.watch.typing)}

    async def watch_say(self, text: str) -> dict[str, Any]:
        try:
            msg, speakers = self.watch.post(text)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        if speakers:
            self._background(self.watch.replies(msg["id"], speakers))
        return {"id": msg["id"], "speakers": [m.name for m in speakers]}

    def watch_scrcpy(self, source: str) -> dict[str, Any]:
        """用 scrcpy 打开安卓设备的流畅投屏窗口（用户自己的工具，圆桌不读它的画面）。"""
        exe = shutil.which(self.rt.config.roundtable.chat.watch.scrcpy)
        if exe is None:
            raise ServiceError("没有找到 scrcpy（可选：装好并加入 PATH 后才能开流畅窗口）")
        serial = source.removeprefix("android:")
        if not source.startswith("android:") or not re.fullmatch(r"[A-Za-z0-9._:\-]{1,64}", serial):
            raise ServiceError("只有安卓设备能开 scrcpy 窗口")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen([exe, "-s", serial], creationflags=flags)  # noqa: S603
        except OSError:
            raise ServiceError("scrcpy 启动失败") from None
        return {"started": True}

    def watch_overlay(self, port: int) -> dict[str, Any]:
        """打开一起看的置顶悬浮窗（另一个进程，tkinter；只通过 HTTP 连本机圆桌，docs/WATCH.md）。"""
        import sys

        if not self.watch.running:
            raise ServiceError("现在没在投屏")
        proc = getattr(self, "_overlay", None)
        if proc is not None and proc.poll() is None:
            return {"started": False, "already": True}
        if not _has_tk():
            raise ServiceError("这台电脑的 Python 没带 tkinter，开不了悬浮窗；可以用「画中画」小窗")
        state = data_dir() / "desktop" / "watch_overlay.json"
        cmd = [
            sys.executable,
            "-m",
            "roundtable.desktop.overlay",
            "--url",
            f"http://127.0.0.1:{int(port)}",
            "--state",
            str(state),
        ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._overlay = subprocess.Popen(cmd, creationflags=flags)  # noqa: S603
        except OSError:
            raise ServiceError("悬浮窗启动失败") from None
        return {"started": True, "already": False}

    # --- 出图渠道（多渠道自动挑选：本地 → 不花钱 → 按价；见 media/draw_order.py） ----------------

    def draw_channels(self) -> dict[str, Any]:
        """每个出图模型一行：能不能用、属于哪一档、单价、剩余额度、在不在歇、上次跳过原因、
        有没有被手动关掉；按现在的出图顺序排（关掉的、用不了的在后面）。"""
        from roundtable.core.media.draw_order import band, draw_key, resting
        from roundtable.core.providers.cost import image_cost

        router = self.rt.router
        prefs = router.draw_prefs
        spent = MonthSpend(self.rt.repo, self.rt.budget.now, ttl_s=0).all()
        enabled = self.rt.config.models.enabled
        drawable = [m for m in enabled if not m.seat and "image_gen" in m.tags]
        rows = []
        for m in drawable:
            plan = router.plan(m.id)
            usable = [r.channel for r in plan.usable]
            channels = []
            for name in dict.fromkeys(r.channel for r in m.routes):
                credit = router.priority.credits.get(name)
                note = router.last_skip(name)
                channels.append(
                    {
                        "channel": name,
                        "kind": router.channels[name].kind,
                        "usable": name in usable,
                        "reason": plan.skipped.get(name),
                        "credit_left_usd": None
                        if credit is None
                        else round(max(0.0, credit - spent.get(name, 0.0)), 6),
                        "resting_s": round(router.cooling_s(name), 1),
                        "last_skip": None
                        if note is None
                        else {
                            "at": datetime.fromtimestamp(note.wall_ts, UTC).isoformat(),
                            "kind": note.kind,
                            "reason": note.reason,
                        },
                    }
                )
            rows.append(
                {
                    "id": m.id,
                    "tier": m.tier,
                    "band": band(router, m),
                    "price_usd": round(image_cost(m, None, 1), 6),
                    "available": bool(usable),
                    "resting": resting(router, m),
                    "disabled": m.id in prefs.disabled,
                    "manual_rank": prefs.order.index(m.id) if m.id in prefs.order else None,
                    "references": "image_edit" in m.tags,
                    "channels": channels,
                    "_key": (m.id in prefs.disabled, not usable, draw_key(router, m)),
                }
            )
        rows.sort(key=lambda r: r.pop("_key"))
        return {"order": list(prefs.order), "disabled": list(prefs.disabled), "models": rows}

    def set_draw_channels(
        self, *, disabled: list[str] | None = None, order: list[str] | None = None
    ) -> dict[str, Any]:
        from roundtable.core.media.draw_order import save_draw_prefs

        prefs = self.rt.router.draw_prefs
        try:
            saved = save_draw_prefs(
                self.rt.config,
                self.rt.repo,
                disabled=disabled if disabled is not None else list(prefs.disabled),
                order=order if order is not None else list(prefs.order),
            )
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        self.rt.router.set_draw_prefs(saved)
        return self.draw_channels()

    # --- 公告栏 ------------------------------------------------------------------

    def changelog(self) -> dict[str, Any]:
        """版本更新说明（只读；每次重新读 config/changelog.yaml 和 config/changelog.d/）。"""
        try:
            log = load_changelog(self.rt.changelog)
        except ConfigError as exc:
            raise ServiceError(str(exc), 500) from None
        return changelog_view(log)

    # --- 终端邮件栏（用户本人学校邮箱，只读；docs/MAIL.md） --------------------------------

    def _mail_view(self) -> dict[str, Any]:
        return {**self.mail.view(), "ai": self.mail_ai.status()}

    def mail_view(self) -> dict[str, Any]:
        return self._mail_view()

    # --- 主时区 ----------------------------------------------------------------------

    def home_tz(self) -> dict[str, Any]:
        """整个圆桌按哪个时区过日子（公寓、群聊、成员作息、日记）；``local`` = 跟随电脑。"""
        return home_zone(self.rt).view(self.rt.budget.now())

    def set_home_tz(self, zone: str) -> dict[str, Any]:
        try:
            home_zone(self.rt).set(zone)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        return self.home_tz()

    def mail_settings(self, *, ai_ok: bool | None = None, **changes: Any) -> dict[str, Any]:
        try:
            self.mail.set_settings(**{k: v for k, v in changes.items() if v is not None})
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        if ai_ok is not None:
            self.mail_ai.set_consent(ai_ok)
        return self._mail_view()

    def _mail_do(self, fn: Any, *args: Any, **kw: Any) -> Any:
        try:
            return fn(*args, **kw)
        except MailBusy as exc:
            raise ServiceError(str(exc), 409) from None
        except ValueError as exc:
            raise ServiceError(str(exc)) from None

    def mail_login(self) -> dict[str, Any]:
        """弹出专用窗口，由用户本人登录（圆桌不代填、不保存密码）。"""
        self._mail_do(self.mail.login)
        return self._mail_view()

    def mail_refresh(self, *, manual: bool) -> dict[str, Any]:
        started = self._mail_do(self.mail.refresh, manual=manual)
        return {**self._mail_view(), "started": started}

    def mail_message(self, message_id: str) -> dict[str, Any]:
        m = self.mail.message(message_id)
        if m is None:
            raise ServiceError("没有这封邮件（可能已超出保留天数）", 404)
        return m

    def mail_reminder(self, reminder_id: int, action: str) -> dict[str, Any]:
        if not self._mail_do(self.mail.set_reminder, reminder_id, action):
            raise ServiceError("没有这条提醒", 404)
        return self._mail_view()

    def mail_logout(self) -> dict[str, Any]:
        self._mail_do(self.mail.logout)
        self.mail_ai.set_consent(False)  # 退出 = 清除全部，包括「同意发给 AI」
        return self._mail_view()

    def mail_clear(self) -> dict[str, Any]:
        self.mail.clear_cache()
        return self._mail_view()

    def mail_mark(self, message_id: str, mark: str | None) -> dict[str, Any]:
        """手动标重要 / 不重要（None 交回规则）。只记在本机，不写回邮箱。"""
        if not self._mail_do(self.mail.set_mark, message_id, mark):
            raise ServiceError("没有这封邮件（可能已超出保留天数）", 404)
        return self._mail_view()

    def mail_category(self, message_id: str, category: str | None) -> dict[str, Any]:
        if not self._mail_do(self.mail.set_category, message_id, category):
            raise ServiceError("没有这封邮件（可能已超出保留天数）", 404)
        return self._mail_view()

    def mail_images(self, message_id: str) -> dict[str, Any]:
        """在后台取这封信的图文版正文（含邮件自带的图片）。"""
        self._mail_do(self.mail.load_images, message_id)
        return self.mail_message(message_id)

    async def mail_ai_do(self, kind: str, message_id: str | None, *, again: bool) -> dict:
        """AI 总结 / 翻译 / 重要邮件汇总：只在用户点了以后调用，有每月和每天上限。"""
        try:
            if kind == "digest":
                await self.mail_ai.digest()
                return self._mail_view()
            if not message_id:
                raise ServiceError("缺少邮件 id")
            fn = self.mail_ai.summarize if kind == "summary" else self.mail_ai.translate
            await fn(message_id, again=again)
        except MailAIError as exc:
            raise ServiceError(str(exc), 409) from None
        return {**self.mail_message(message_id), "ai": self.mail_ai.status()}

    # --- 日历底座（docs/CALENDAR.md；只给数据，不调模型） ------------------------------------

    def calendar_view(self, days: int | None = None, start: str | None = None) -> dict[str, Any]:
        """从 start（默认今天）起 days 天（默认 calendar.board_days）是什么日子。"""
        rules = self.rt.config.roundtable.calendar
        try:
            first = date.fromisoformat(start) if start else self._local_now().date()
        except ValueError:
            raise ServiceError("start 要写成 YYYY-MM-DD", 400) from None
        n = days or rules.board_days
        if not 1 <= n <= 366:
            raise ServiceError("days 要在 1～366 之间", 400)
        return {
            "enabled": rules.enabled,
            "days": self.calendar.span(first, n) if rules.enabled else [],
            "user_dates": self.calendar.user_dates(),
        }

    def calendar_add_date(self, name: str, when: str, lunar: bool = False) -> dict[str, Any]:
        """加一个你自己的纪念日（每年都过，只存本机）。"""
        try:
            row = self.calendar.add_user_date(name, when, lunar=lunar)
        except ValueError as exc:
            raise ServiceError(str(exc), 400) from None
        return {"date": row, "user_dates": self.calendar.user_dates()}

    def calendar_remove_date(self, date_id: str) -> dict[str, Any]:
        if not self.calendar.remove_user_date(date_id):
            raise ServiceError("没有这个纪念日", 404)
        return {"user_dates": self.calendar.user_dates()}

    def member_events_clear(self) -> dict[str, Any]:
        """一键清空成员引擎记下的全部事件（只存本机的经历数据）。"""
        return {"deleted": self.engine.bus.clear()}

    def life_reports(self) -> dict[str, Any]:
        """生活的每周自检（docs/CHAT.md G9）：已存的每周报告（新的在前）和最近 7 天的实时报告。"""
        now = self.rt.budget.now()
        names = {m.id: m.name for m in self.chats.everyone()}
        return {
            "reports": self.rt.repo.life_reports(),
            "recent": self.life.guard.report(now - timedelta(days=7), now, names),
            "halted": self.life.guard.halted(),
        }

    def chat_feeds(self) -> dict[str, Any]:
        """订阅源清单（网页改过的优先）、上次抓取时间、今天联网搜索次数与上限。"""
        rules = self.rt.config.roundtable.chat.browse
        feeds, source = feeds_of(self.rt)
        return {
            "enabled": rules.enabled,
            "feeds": [f.model_dump() for f in feeds],
            "source": source,
            "last_refresh": self.rt.repo.setting(REFRESH_KEY) or None,
            "search_used_today": self.life.browse.searches_today(),
            "search_per_day": rules.search_per_day,
            "items": len(self.rt.repo.web_items(limit=1000)),
        }

    def chat_set_feeds(self, feeds: list[dict[str, Any]] | None) -> dict[str, Any]:
        try:
            set_feeds(self.rt, feeds)
        except ValueError as exc:  # pydantic 的 ValidationError 也是 ValueError
            raise ServiceError(f"订阅源不合法：{exc}") from None
        return self.chat_feeds()

    async def chat_refresh_feeds(self) -> dict[str, Any]:
        """马上抓一遍订阅源（不调用模型、不花钱）。"""
        result = await self.life.browse.refresh(self._local_now(), force=True)
        return {**result, **self.chat_feeds()}

    def chat_memes(self) -> dict[str, Any]:
        """网络梗：梗库（经典梗在前）、每个梗哪几位懂、上次更新、热榜抓没抓到。"""
        from roundtable.core.chat import chat_members
        from roundtable.core.member.memes import MemeBook

        book, now = MemeBook(self.rt), self._local_now()
        rules = book.rules
        members = chat_members(self.rt.config, set())
        memes = [
            {
                **{
                    k: m.get(k, "")
                    for k in ("phrase", "meaning", "cue", "reply", "example", "since")
                },
                "aliases": list(m.get("aliases") or []),
                "classic": bool(m.get("classic")),
                "source": m.get("source", ""),
                "last_seen": m.get("last_seen") or None,
                "known_by": [x.name for x in members if book.knows(x.id, m, now)],
            }
            for m in book.all(now)
        ]
        return {
            "enabled": rules.enabled,
            "memes": memes,
            "status": self.life.memes.status(),
            "sources": [s.name for s in rules.sources if s.enabled],
            "fresh_days": rules.fresh_days,
            "stale": list(rules.stale),
            "search_used_today": self.life.memes.searches_today(),
            "search_per_day": rules.search_per_day,
        }

    def chat_add_meme(self, data: dict[str, Any]) -> dict[str, Any]:
        """你手动加一个梗（或改一个已有的）；classic=True 时永远不下架。"""
        from roundtable.core.member.memes import MemeBook

        try:
            MemeBook(self.rt).add(data, self._local_now())
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        return self.chat_memes()

    def chat_remove_meme(self, phrase: str) -> dict[str, Any]:
        from roundtable.core.member.memes import MemeBook

        if not MemeBook(self.rt).remove(phrase):
            raise ServiceError(f"没有这个梗：{phrase}")
        return self.chat_memes()

    async def chat_refresh_memes(self) -> dict[str, Any]:
        """马上更新一次梗库（抓热榜不花钱；提炼和搜索约 $0.01～0.03，算生活每日上限）。"""
        result = await self.life.memes.refresh(self._local_now(), force=True)
        return {**result, **self.chat_memes()}

    def chat_clear(self, room: str = ROOM) -> dict[str, Any]:
        """一键清空这个会话的聊天记录（花费记录保留，预算照算）。"""
        chat = self._chat_room(room)
        return {"deleted": self.rt.repo.clear_chat(chat.room)}

    def perception_clear(self) -> dict[str, Any]:
        """一键清空感官缓存（data/perception/ 里的感知卡和转写稿；聊天记录里的卡不动）。"""
        return {"deleted": self.perceiver.cache.clear()}

    def payslip(self, session_id: str) -> dict[str, Any]:
        """一件事的工资单：每位组员 / 统筹各领多少，外包（搜索、媒体、规划员等）另列。

        按会话视图汇总：匿名会话揭晓前只列代号和"统筹"，不含模型 id。
        """
        self._require(session_id)
        view = self.rt.repo.session_view(session_id)

        office = view.workflow == "office"  # 办公室模式里统筹就是经理

        def coordinator_label(model_id: str | None) -> str:
            if model_id:
                return coordinator_name(model_id, self.rt.config, office=office)
            return "经理" if office else "统筹"

        slip = payslip(view.calls, view.seats, coordinator_label=coordinator_label)
        return {"lines": [asdict(line) for line in slip.lines], "total_usd": slip.total_usd}
