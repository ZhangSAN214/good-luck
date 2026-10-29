"""投屏陪伴：用户投屏，选 1～3 位成员陪着一起看（docs/WATCH.md）。

- 开始前用户必须明确同意（截图会发给云端的识图模型）；开着时网页顶上有红点，随时一键停。
- 每 ``grab_seconds`` 截一张（只在内存里，网页预览用最近一张）。画面和上次「看过」的那张相比变化
  超过 ``change_threshold``、离上次看至少 ``describe_gap_seconds``、这小时还没看够
  ``describes_per_hour`` 次，才交给最便宜的识图模型写成一段文字（提示词 describe_screen）。
  成员读的是这段文字；所有陪看的成员拿到同一段文字、同一套提示词和参数（中立）。
- 前台是敏感窗口 / 应用（``sensitive`` 关键字：支付、银行、密码管理器……）时暂停：不截、不看。
- 看画面不思考（``describe_reasoning``，默认关）：思考会把字数花光、交回空白；看失败了隔
  ``fail_gap_seconds`` 再试，不每次截图都重试。每次带上当前窗口标题和上一段描述，写出变化。
- 成员说话（提示词 watch）：用户说话时被 @ 的或随机一位回应（@全员 / 大家 时全体依次回应）；
  没人说话、画面变了时，离上次大家说话至少 ``room_gap_seconds``、离自己上次至少
  ``react_gap_seconds`` 的成员随机一位「想说才说」（模型回「…」就不说），这次往哪个方向说从
  ``angles`` 随机抽。一位说完后，别的陪看成员按 ``followup_chance``（每多接一句减半）接她的话，
  最多 ``max_followups`` 句，也可以选择不接。都带各自的说话习惯（成员系统 P5）。
  「安静看」时不主动说，也不互相接话。
- 每次投屏一个 ``watch:<开始时间>`` 会话（成员 = 这次陪看的人，不出现在群聊列表），之后照常进
  陪看成员的记忆整理。每段画面描述作为感知事件
  ``perceive.screen`` 发到成员引擎的总线（每位陪看的成员一条），截图本身不存。
- 花费：画面描述和成员发言都挂在 watch 隐藏会话上，计入总预算和 ``monthly_usd``（不占聊天月
  预算）。月额度用完：不再看画面，只陪聊也停（系统消息说明）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from roundtable.core.allocation import cheapest
from roundtable.core.attachments.ingest import capable
from roundtable.core.budget.guard import EPS, month_window
from roundtable.core.chat import (
    EVERYONE,
    SYSTEM,
    USER,
    USER_LABEL,
    ChatMember,
    addresses_everyone,
    clean_reply,
    member_memory,
    mentioned,
)
from roundtable.core.member.voice import voice_for
from roundtable.core.persona import chat_persona
from roundtable.core.plain_talk import before_speaking, plain_prompt
from roundtable.core.providers import (
    AllChannelsFailed,
    Media,
    Message,
    NoChannelAvailable,
    ProviderError,
)

from .capture import Frame, ScreenSource, ScreenUnavailable, change_ratio, is_sensitive

log = logging.getLogger(__name__)

WATCH_PREFIX = "watch:"  # 每次投屏一个会话：watch:<开始时间>，成员 = 这次陪看的人（记忆只记给她们）
WORKFLOW = "watch"
DESCRIBE_STEP = "watch_describe"
SPEAK_STEP = "watch"
SILENT = ("…", "...", "……", "。。。")
# 提示词 watch v2 的 <turn>：这次为什么轮到她
TURN_USER, TURN_FRIEND, TURN_SCREEN = "用户", "朋友", "画面"
MAX_SAY_CHARS = 1000


@dataclass
class Screen:
    at: float  # clock() 的秒数
    text: str


class ScreenWatch:
    """一次只有一场投屏。start / stop / say / quiet 由服务层调用；tick() 由后台循环按间隔调用。"""

    def __init__(
        self,
        rt: Any,
        members: Callable[[], list[ChatMember]],
        sources: dict[str, ScreenSource],
        *,
        publish: Callable[[Any], Any] | None = None,
        on_change: Callable[[], None] | None = None,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rt = rt
        self.members = members
        self.sources = sources
        self.publish = publish
        self.on_change = on_change or (lambda: None)
        self.rng = rng or random.Random()
        self.clock = clock
        self._task: asyncio.Task[None] | None = None
        self._speaking = asyncio.Lock()
        self.room = ""  # 当前（或上一次）投屏的会话；停止后保留，网页还能看刚才的聊天
        self._reset()

    def _reset(self) -> None:
        self.running = False
        self.source_id = ""
        self.source_title = ""
        self.title = ""  # 当前窗口标题（每次截图时更新；拿不到时用开始时的名字）
        self.watchers: list[str] = []
        self.quiet = False
        self.started = 0.0
        self.frame: Frame | None = None  # 最近一张（网页预览）
        self.seen_sig: bytes | None = None  # 上次交给模型看过的那张
        self.last_look = -1e9
        self.looks: deque[float] = deque()
        self.screens: deque[Screen] = deque(maxlen=self.rules.recent_screens)
        self.spoke: dict[str, float] = {}
        self.room_spoke = -1e9
        self.paused = ""
        self.error = ""
        self.failures = 0
        self.typing: list[str] = []

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.watch

    # --- 状态 ------------------------------------------------------------------------

    def _source_for(self, source_id: str) -> ScreenSource | None:
        return self.sources.get("android" if source_id.startswith("android:") else "pc")

    def budget(self) -> dict[str, float]:
        start, end = month_window(self.rt.budget.now())
        spent = self.rt.repo.spent_in_workflow(WORKFLOW, start, end)
        return {"spent_usd": spent, "limit_usd": self.rules.monthly_usd}

    def _over_budget(self, estimate: float) -> bool:
        b = self.budget()
        if b["spent_usd"] + estimate > b["limit_usd"] + EPS:
            return True
        return not self.rt.budget.check(estimate).allowed

    def devices(self) -> dict[str, Any]:
        """可投的来源（会调 adb / 系统接口，服务层放到线程里跑）。"""
        out: dict[str, Any] = {}
        for kind, src in self.sources.items():
            reason = src.available()
            out[kind] = {
                "available": reason is None,
                "reason": reason,
                "sources": [s.__dict__ for s in src.sources()] if reason is None else [],
            }
        return out

    def status(self) -> dict[str, Any]:
        names = {m.id: m.name for m in self.members()}
        now = self.clock()
        return {
            "enabled": self.rules.enabled,
            "running": self.running,
            "source": (
                {"id": self.source_id, "title": self.title or self.source_title}
                if self.running
                else None
            ),
            "watchers": [{"id": w, "name": names.get(w, w)} for w in self.watchers],
            "quiet": self.quiet,
            "paused": self.paused,
            "error": self.error,
            "minutes": round((now - self.started) / 60, 1) if self.running else 0,
            "has_frame": self.frame is not None,
            # 透明：成员读到的就是这几段（用户能看到她们「看见」了什么）
            "screens": [
                {"ago_s": round(now - s.at), "text": s.text} for s in reversed(self.screens)
            ],
            "typing": list(self.typing),
            "budget": self.budget(),
            "max_watchers": self.rules.max_watchers,
        }

    def preview(self) -> bytes | None:
        """最近一张截图（JPEG，只在内存里）；没在投屏或暂停时没有。"""
        return self.frame.jpeg if self.running and self.frame and not self.paused else None

    def messages(self, after: int = 0) -> list[dict[str, Any]]:
        if not self.room:
            return []
        names = {m.id: m.name for m in self.members()} | {USER: "我", SYSTEM: "系统"}
        rows = self.rt.repo.chat_messages(self.room, after=after, limit=100)
        return [{**r, "name": names.get(r["author"], r["author"])} for r in rows]

    # --- 开始 / 停止 --------------------------------------------------------------------

    def _system(self, text: str) -> dict[str, Any]:
        msg = self.rt.repo.add_chat_message(self.room, SYSTEM, text)
        self.rt.repo.mark_chat_read(self.room, msg["id"])
        self.on_change()
        return msg

    def start(self, source_id: str, title: str, watchers: list[str], *, consent: bool) -> None:
        """检查参数并开始（后台循环由 run() 驱动，服务层负责建任务）。出错抛 ValueError。"""
        rules = self.rules
        if not rules.enabled:
            raise ValueError("投屏陪伴已在配置里关闭（chat.watch.enabled）")
        if not consent:
            raise ValueError("开始前要先同意：截图会发给云端的识图模型")
        if self.running:
            raise ValueError("已经在投屏了，先停止再换")
        src = self._source_for(source_id)
        if src is None:
            raise ValueError("不认识的画面来源")
        reason = src.available()
        if reason:
            raise ValueError(reason)
        online = {m.id: m for m in self.members() if m.available}
        picked = list(dict.fromkeys(w for w in watchers if w in online))
        if not picked:
            raise ValueError("至少选一位在线的成员陪你看")
        if len(picked) > rules.max_watchers:
            raise ValueError(f"最多选 {rules.max_watchers} 位")
        if not capable(self.rt.router, "vision"):
            raise ValueError("没有可用的识图模型（需要带 vision 标签且有可用渠道的模型）")
        if self._over_budget(rules.describe_estimate_usd):
            raise ValueError(f"本月一起看的额度（${rules.monthly_usd:.2f}）用完了，下个月 1 号恢复")
        self._reset()
        self.running = True
        self.source_id = source_id
        self.source_title = title or source_id
        self.watchers = picked
        self.started = self.clock()
        self.room = f"{WATCH_PREFIX}{self.rt.budget.now():%Y%m%d%H%M%S}"
        self.rt.repo.ensure_chat_room(self.room, kind="watch", title="一起看", members=picked)
        names = "、".join(online[w].name for w in picked)
        self._system(f"开始一起看「{self.source_title}」，陪你看的有：{names}。")

    def stop(self, why: str = "你停止了投屏") -> bool:
        if not self.running:
            return False
        minutes = (self.clock() - self.started) / 60
        self._reset()
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
        self._task = None
        self._system(f"{why}（一起看了约 {max(1, round(minutes))} 分钟）。")
        return True

    def set_quiet(self, quiet: bool) -> None:
        self.quiet = bool(quiet)
        self.on_change()

    def attach(self, task: asyncio.Task[None]) -> None:
        self._task = task

    async def run(self) -> None:
        """后台循环：按间隔截图、看画面、主动说话。"""
        while self.running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 一次出错不停，下次再试
                log.exception("投屏陪伴：推进出错")
            await asyncio.sleep(self.rules.grab_seconds)

    # --- 推进 ------------------------------------------------------------------------

    async def tick(self) -> str:
        """推进一次。返回这次做了什么（测试与日志用）：stopped / paused / failed / same /
        wait / looked / spoke。"""
        if not self.running:
            return "stopped"
        rules, now = self.rules, self.clock()
        if now - self.started > rules.max_minutes * 60:
            self.stop(f"投屏满 {rules.max_minutes} 分钟，自动停了")
            return "stopped"
        src = self._source_for(self.source_id)
        assert src is not None
        fg = await asyncio.to_thread(src.foreground, self.source_id)
        hit = is_sensitive(fg, rules.sensitive)
        if not hit:
            self.title = await asyncio.to_thread(self._current_title, src, fg)
        if hit:
            if not self.paused:
                self.paused = "前台是支付、银行或密码之类的窗口，先暂停，切走后自动继续"
                self.frame = None
                self.on_change()
            return "paused"
        try:
            frame = await asyncio.to_thread(
                src.grab, self.source_id, rules.max_side, rules.jpeg_quality
            )
        except ScreenUnavailable as exc:
            self.failures += 1
            self.error = str(exc)
            self.on_change()
            if self.failures >= rules.max_failures:
                self.stop(f"连续 {self.failures} 次截不到画面（{exc}），自动停了")
            return "failed"
        if is_sensitive(frame.foreground, rules.sensitive):  # 截图那一刻切到了敏感窗口
            self.frame = None
            return "paused"
        self.failures, self.error = 0, ""
        if self.paused:
            self.paused = ""
        self.frame = frame
        self.on_change()
        if change_ratio(self.seen_sig, frame.signature) < rules.change_threshold:
            return "same"
        while self.looks and now - self.looks[0] > 3600:
            self.looks.popleft()
        if now - self.last_look < rules.describe_gap_seconds:
            return "wait"
        if len(self.looks) >= rules.describes_per_hour:
            return "wait"
        if self._over_budget(rules.describe_estimate_usd):
            self.stop(f"本月一起看的额度（${rules.monthly_usd:.2f}）用完了，先停在这里")
            return "stopped"
        text = await self.describe(frame)
        if text is None:  # 看失败了：隔一会儿再看，不每次截图都重试
            self.last_look = now - rules.describe_gap_seconds + rules.fail_gap_seconds
            return "failed"
        self.seen_sig, self.last_look = frame.signature, now
        self.looks.append(now)
        self.screens.append(Screen(now, text))
        self._perceive(text)
        self.on_change()
        if not self.quiet and await self._react():
            return "spoke"
        return "looked"

    async def describe(self, frame: Frame) -> str | None:
        """一张截图 → 文字（最便宜的识图模型）。"""
        rules = self.rules
        pool = capable(self.rt.router, "vision")
        if not pool:
            self.error = "没有可用的识图模型"
            return None
        model = cheapest(pool, self.rng, input_tokens=1200, output_tokens=rules.describe_max_tokens)
        role = "describe_screen"
        prompt = self.rt.prompts.render(
            role,
            self.rt.config.roundtable.prompts[role],
            source=self.title or self.source_title,
            previous=self.screens[-1].text if self.screens else "（这是第一张）",
        )
        media = Media("image", "image/jpeg", frame.jpeg, "screen.jpg")
        messages = tuple(
            Message(m.role, m.content, (media,)) if m.role == "user" else m for m in prompt.messages
        )
        session = self.rt.repo.watch_session()
        params: dict[str, Any] = {"max_tokens": rules.describe_max_tokens}
        if rules.describe_reasoning:
            params["reasoning"] = dict(rules.describe_reasoning)
        try:
            completion = await self.rt.router.complete(model.id, messages, params)
        except (AllChannelsFailed, NoChannelAvailable, ProviderError) as exc:
            self.rt.repo.record_call(
                session,
                step=DESCRIBE_STEP,
                role="preprocess",
                model_id=model.id,
                messages=messages,
                prompt=prompt,
                failure=exc,
            )
            self.error = "看画面的模型暂时调用失败"
            return None
        self.rt.repo.record_call(
            session,
            step=DESCRIBE_STEP,
            role="preprocess",
            model_id=model.id,
            messages=messages,
            prompt=prompt,
            completion=completion,
        )
        text = completion.text.strip()
        if not text:
            self.error = "看画面的模型交回了空白，过一会儿再看"
            return None
        self.error = ""
        return text[:600]

    def _current_title(self, src: ScreenSource, fg: str) -> str:
        """当前窗口标题：窗口来源读那个窗口，整屏读前台窗口；安卓（包名）不用。"""
        if self.source_id.startswith("android:"):
            return ""
        title = ""
        reader = getattr(src, "title", None)
        if reader is not None:
            with contextlib.suppress(Exception):
                title = reader(self.source_id) or ""
        if not title and self.source_id == "pc":
            title = fg.split(" | ")[0]
        return title.strip()[:120]

    def _perceive(self, text: str) -> None:
        if self.publish is None:
            return
        from roundtable.core.member.bus import perception

        at = self.rt.budget.now()
        for w in self.watchers:
            with contextlib.suppress(Exception):  # 总线出错不影响一起看
                self.publish(perception("screen", at, w, text, source=self.source_title))

    # --- 说话 ------------------------------------------------------------------------

    def _watchers(self) -> list[ChatMember]:
        by_id = {m.id: m for m in self.members()}
        return [by_id[w] for w in self.watchers if w in by_id and by_id[w].available]

    async def _react(self) -> bool:
        """没人说话、画面变了：挑一位想说才说。"""
        rules, now = self.rules, self.clock()
        if now - self.room_spoke < rules.room_gap_seconds:
            return False
        ready = [
            m
            for m in self._watchers()
            if now - self.spoke.get(m.id, -1e9) >= rules.react_gap_seconds
        ]
        if not ready:
            return False
        member = self.rng.choice(ready)
        angle = self.rng.choice(rules.angles)
        said = await self._speak(member, reply_to=None, turn=TURN_SCREEN, angle=angle)
        if said:
            await self._follow(said, member)
        return bool(said)

    async def _follow(self, said: dict[str, Any], speaker: ChatMember) -> list[dict[str, Any]]:
        """一位刚说完：别的陪看成员按几率接她的话（每多接一句几率减半），最多 max_followups 句。
        每次接的是上一句；被接的人也可能再接回来。「安静看」时不接。"""
        rules, out = self.rules, []
        chance, last, last_by = rules.followup_chance, said, speaker.id
        for _ in range(rules.max_followups):
            if self.quiet or not self.running:
                break
            others = [m for m in self._watchers() if m.id != last_by]
            if not others or self.rng.random() >= chance:
                break
            member = self.rng.choice(others)
            reply = await self._speak(member, reply_to=last["id"], turn=TURN_FRIEND)
            if not reply:
                break
            out.append(reply)
            last, last_by = reply, member.id
            chance /= 2
        return out

    def pick(self, text: str) -> list[ChatMember]:
        watching = self._watchers()
        named = mentioned(text, watching)
        if f"@{EVERYONE}" in text or (addresses_everyone(text) and not named):
            named = list(watching)
            self.rng.shuffle(named)
        if named:
            return named
        return [self.rng.choice(watching)] if watching else []

    def post(self, text: str) -> tuple[dict[str, Any], list[ChatMember]]:
        """存下用户的一句话，选出回应的成员（回应由 replies() 生成）。"""
        text = (text or "").strip()
        if not text:
            raise ValueError("消息不能为空")
        if len(text) > MAX_SAY_CHARS:
            raise ValueError(f"消息太长（最多 {MAX_SAY_CHARS} 字）")
        if not self.running:
            raise ValueError("现在没在投屏")
        msg = self.rt.repo.add_chat_message(self.room, USER, text)
        self.rt.repo.mark_chat_read(self.room, msg["id"])
        self.room_spoke = self.clock()  # 你在说话：大家先听你的，不插嘴
        self.on_change()
        return msg, self.pick(text)

    async def replies(self, reply_to: int, speakers: list[ChatMember]) -> list[dict[str, Any]]:
        out = []
        for m in speakers:
            said = await self._speak(m, reply_to=reply_to, turn=TURN_USER)
            if said is None:
                break
            if said:
                out.append(said)
        if len(speakers) == 1 and out:  # 只有一位回了你：别人可能接一句（全员回过就不再接）
            out += await self._follow(out[-1], speakers[0])
        return out

    def _screen_text(self) -> str:
        now = self.clock()
        lines = []
        for s in self.screens:
            ago = now - s.at
            when = "刚才" if ago < 60 else f"{round(ago / 60)} 分钟前"
            lines.append(f"（{when}）{s.text}")
        return "\n".join(lines) or "（还没看到画面）"

    def _history(self, names: dict[str, str]) -> str:
        rows = self.rt.repo.chat_messages(self.room, limit=self.rules.history_messages)
        lines = []
        for r in rows:
            if r["author"] == SYSTEM:
                continue
            who = USER_LABEL if r["author"] == USER else names.get(r["author"], r["author"])
            lines.append(f"{who}：{r['text']}")
        return "\n".join(lines) or "（还没人说话）"

    def params(self) -> dict[str, Any]:
        params = dict(self.rt.config.roundtable.step_params.get("watch", {}))
        params.setdefault("max_tokens", self.rules.max_tokens)
        return params

    async def _speak(
        self, member: ChatMember, reply_to: int | None, *, turn: str, angle: str = ""
    ) -> dict[str, Any] | None:
        """一位成员说一句。返回消息；选择不说或调用失败返回 {}；额度用完返回 None。"""
        rules, rt = self.rules, self.rt
        async with self._speaking:
            if not self.running:
                return None
            if self._over_budget(rules.estimate_usd):
                self._system(
                    f"本月一起看的额度（${rules.monthly_usd:.2f}）用完了，大家先不说话了。"
                )
                return None
            everyone = self.members()
            names = {m.id: m.name for m in everyone}
            now = self.clock()
            screen = self._screen_text()
            history = self._history(names)
            others = "、".join(names.get(w, w) for w in self.watchers if w != member.id) or "用户"
            prompt = rt.prompts.render(
                "watch",
                rt.config.roundtable.prompts["watch"],
                name=member.name,
                persona=chat_persona(rt, member.id),
                voice=voice_for(rt, member.id, "watch", seed=len(self.spoke) + int(now)),
                memory=member_memory(rt, member.id, f"{screen}\n{history}"),
                title=self.title or self.source_title or "（不知道）",
                screen=screen,
                history=history,
                others=others,
                turn=turn,
                angle=angle or "（不限）",
            )
            prompt = plain_prompt(rt.prompts, rt.config, prompt)
            session = rt.repo.watch_session()
            self.typing.append(member.name)
            self.on_change()
            try:
                completion = await rt.router.complete(
                    member.model_id, prompt.messages, self.params()
                )
            except (AllChannelsFailed, NoChannelAvailable, ProviderError) as exc:
                rt.repo.record_call(
                    session,
                    step=SPEAK_STEP,
                    role="member",
                    model_id=member.model_id,
                    messages=prompt.messages,
                    prompt=prompt,
                    failure=exc,
                )
                return {}
            finally:
                self.typing.remove(member.name)
                self.on_change()
            rt.repo.record_call(
                session,
                step=SPEAK_STEP,
                role="member",
                model_id=member.model_id,
                messages=prompt.messages,
                prompt=prompt,
                completion=completion,
            )
            now = self.clock()
            self.spoke[member.id] = now
            text = clean_reply(completion.text, member.name, rules.max_chars)
            if not text or text.strip() in SILENT:
                return {}
            text = await before_speaking(
                rt, text, room=self.room, name=member.name, model_id=member.model_id
            )
            self.room_spoke = now
            if not self.running:
                return None
            msg = rt.repo.add_chat_message(
                self.room,
                member.id,
                text,
                model_id=member.model_id,
                reply_to=reply_to,
                cost_usd=completion.cost_usd or 0.0,
            )
            rt.repo.mark_chat_read(self.room, msg["id"])
            self.on_change()
            return msg
