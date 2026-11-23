"""游戏区的总管：开桌、轮到谁出、成员用模型还是本地电脑想、金币与下注。

- 牌桌只放在内存里（关掉圆桌就散桌，你带上桌的筹码在散桌前退回金币）。
- 金币存在本机设置里（``arcade.wallet``），每天第一次进来送一点；不能充值、不能换真钱。
- 成员出牌：用群聊里那位成员的模型（``ChatMember.model_id``），所有成员同一份提示词和参数，
  只从程序算好的合法出法里挑编号；
  没开、到了每天上限 / 聊天月预算 / 总预算、想太久、出错或答非所问时，
  本地电脑（``holdem_bot``）接手，这一步不花钱。调用挂在隐藏会话「群聊：arcade」上，计入聊天预算和工资。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from roundtable.core.arcade import holdem_bot
from roundtable.core.arcade.holdem import KIND_ZH, Holdem, HoldemError, Seat
from roundtable.core.budget.guard import day_window
from roundtable.core.chat import chat_budget
from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.providers import AllChannelsFailed, NoChannelAvailable, ProviderError

STEP = "arcade"
ROOM = "arcade"  # 成员出牌的调用挂在这个隐藏聊天会话上
WALLET_KEY = "arcade.wallet"
YOU = "you"
MAX_SAY = 30

GAMES = [
    {"id": "holdem", "name": "德州扑克", "ready": True, "players": "2～9 人"},
    {"id": "doudizhu", "name": "斗地主", "ready": False, "players": "3 人"},
    {"id": "paodekuai", "name": "跑得快", "ready": False, "players": "3 人"},
    {"id": "shengji", "name": "升级", "ready": False, "players": "4 人"},
    {"id": "mahjong", "name": "麻将", "ready": False, "players": "4 人"},
    {"id": "tanks", "name": "铁甲突围", "ready": False, "players": "1～2 人"},
]


class ArcadeError(ValueError):
    """给网页看的错误（中文、能直接显示）。"""


@dataclass
class Table:
    id: str
    game: Holdem
    members: dict[str, Any]  # 座位 id → ChatMember
    seated: bool  # 你自己有没有坐下
    think: bool  # 成员用不用模型想
    seed: int
    buy_in: int = 0
    bet: dict[str, Any] | None = None  # 旁观时押的这一手：{on, amount, players}
    says: dict[str, str] = field(default_factory=dict)  # 座位 id → 这一手说的话
    brains: dict[str, str] = field(default_factory=dict)  # 座位 id → 上一步谁想的：model / local
    thinking: str | None = None  # 正在想的座位
    note: str = ""  # 给你看的一句说明（如「今天的额度用完了」）
    last_bet: dict[str, Any] | None = None  # 上一手押注的结果


class Arcade:
    def __init__(
        self,
        rt: Any,
        everyone: Callable[[], list[Any]],
        rng: random.Random | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.rt = rt
        self.everyone = everyone
        self.rng = rng or random.Random()
        self.sleep = sleep
        self.table: Table | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.arcade

    # --- 金币 -------------------------------------------------------------------

    def _today(self) -> str:
        return self.rt.budget.now().date().isoformat()

    def _load_wallet(self) -> dict[str, Any]:
        raw = self.rt.repo.setting(WALLET_KEY)
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            data = {
                "coins": self.rules.wallet.start,
                "gift_day": self._today(),
                "won": 0,
                "lost": 0,
            }
            self._save_wallet(data)
        return data

    def _save_wallet(self, data: dict[str, Any]) -> None:
        self.rt.repo.set_setting(WALLET_KEY, json.dumps(data, ensure_ascii=False))

    def wallet(self) -> dict[str, Any]:
        """当前金币；每天第一次看时送 ``daily_gift``。"""
        data = self._load_wallet()
        today = self._today()
        if data.get("gift_day") != today:
            data["coins"] = int(data.get("coins", 0)) + self.rules.wallet.daily_gift
            data["gift_day"] = today
            data["gifted"] = self.rules.wallet.daily_gift
            self._save_wallet(data)
        else:
            data.pop("gifted", None)
        return data

    def _add_coins(self, amount: int, *, won: int = 0, lost: int = 0) -> int:
        data = self.wallet()
        data["coins"] = max(0, int(data.get("coins", 0)) + amount)
        data["won"] = int(data.get("won", 0)) + won
        data["lost"] = int(data.get("lost", 0)) + lost
        data.pop("gifted", None)
        self._save_wallet(data)
        return data["coins"]

    # --- 花费 -------------------------------------------------------------------

    def spent_today(self) -> float:
        """今天成员出牌真掏钱的部分：走还有本月赠送额度的渠道（如每月送的 API 额度）不算
        （你 2026-11-23 说的：送的额度每月要用完）；额度用完后照常算进来。"""
        start, end = day_window(self.rt.budget.now())
        by_channel = self.rt.repo.spent_in_steps_by_channel("chat", (STEP,), start, end)
        router = self.rt.router
        return sum(cost for ch, cost in by_channel.items() if not (ch and router.credit_left(ch)))

    def on_credit(self, model_id: str) -> bool:
        """这位成员的模型这次会先走还有赠送额度的渠道（不算进每天上限）。"""
        try:
            usable = self.rt.router.plan(model_id).usable
        except (KeyError, ValueError):
            return False
        # 有赠送额度且没用完的渠道总是排在最前（channel_priority），有一条就会先走它
        return any(self.rt.router.credit_left(r.channel) for r in usable)

    def allowed(self, cost: float, model_id: str | None = None) -> str | None:
        """能不能再花 cost；不能时返回原因。"""
        free = model_id is not None and self.on_credit(model_id)
        if not free and self.spent_today() + cost > self.rules.daily_usd + 1e-9:
            return "今天成员出牌的额度用完了，换本地电脑代打（不花钱）"
        if not self.rt.budget.check(cost).allowed:
            return "总预算到上限了，换本地电脑代打"
        if chat_budget(self.rt).would_exceed(cost):
            return "聊天这个月的预算到上限了，换本地电脑代打"
        return None

    # --- 状态 -------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        rules = self.rules
        return {
            "enabled": rules.enabled,
            "games": GAMES,
            "wallet": self._wallet_view(),
            "spend": {"today": round(self.spent_today(), 4), "cap": rules.daily_usd},
            "members_think": rules.members_think,
            "members": [
                {"id": m.id, "name": m.name, "available": m.available} for m in self.everyone()
            ],
            "holdem": {
                "stack": rules.holdem.stack,
                "blinds": [rules.holdem.small_blind, rules.holdem.big_blind],
                "max_members": rules.holdem.max_members,
                "max_bet": rules.wallet.max_bet,
            },
            "table": self.view() if self.table else None,
        }

    def _wallet_view(self) -> dict[str, Any]:
        w = self.wallet()
        return {
            "coins": int(w.get("coins", 0)),
            "gifted": int(w.get("gifted", 0)),
            "won": int(w.get("won", 0)),
            "lost": int(w.get("lost", 0)),
        }

    def view(self) -> dict[str, Any]:
        t = self._need_table()
        g = t.game
        data = g.view(reveal_all=not t.seated and g.street == "done")
        for s in data["seats"]:
            s["say"] = t.says.get(s["id"], "")
            s["brain"] = t.brains.get(s["id"], "")
            s["thinking"] = t.thinking == s["id"]
        if not t.seated:  # 旁观：比牌前看不到任何人的底牌
            for s in data["seats"]:
                if g.street != "done":
                    s["hole"] = ["??", "??"] if s["hole"] else []
        data.update(
            {
                "id": t.id,
                "game": "holdem",
                "seated": t.seated,
                "think": t.think,
                "thinking": t.thinking,
                "busy": self.busy(),
                "note": t.note,
                "bet": t.bet,
                "last_bet": t.last_bet,
                "can_deal": g.street in ("idle", "done") and g.can_start() and self._you_can_play(),
                "wallet": self._wallet_view(),
                "spend": {"today": round(self.spent_today(), 4), "cap": self.rules.daily_usd},
            }
        )
        return data

    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def _need_table(self) -> Table:
        if self.table is None:
            raise ArcadeError("现在没有牌桌，先开一桌")
        return self.table

    def _you_can_play(self) -> bool:
        t = self.table
        if t is None or not t.seated:
            return True
        return t.game.seats[t.game.index_of(YOU)].stack > 0

    # --- 开桌 / 离桌 --------------------------------------------------------------

    def open_holdem(
        self, member_ids: list[str], *, sit: bool = True, think: bool | None = None
    ) -> dict[str, Any]:
        rules = self.rules
        if not rules.enabled:
            raise ArcadeError("游戏区关着（config/roundtable.yaml 的 arcade.enabled）")
        if self.table is not None:
            self.leave()
        roster = {m.id: m for m in self.everyone()}
        picked = list(dict.fromkeys(i for i in member_ids if i in roster))
        if not picked:
            raise ArcadeError("至少选一位成员")
        if len(picked) > rules.holdem.max_members:
            raise ArcadeError(f"最多 {rules.holdem.max_members} 位成员")
        if not sit and len(picked) < 2:
            raise ArcadeError("旁观要至少两位成员一起打")
        stack = rules.holdem.stack
        buy_in = 0
        if sit:
            coins = int(self.wallet().get("coins", 0))
            if coins < rules.holdem.big_blind * 2:
                raise ArcadeError("金币不够上桌了，明天再来领，或者先旁观押注")
            buy_in = min(stack, coins)
            self._add_coins(-buy_in)
        seed = self.rng.randrange(2**31)
        rng = random.Random(seed)
        order = list(picked)
        rng.shuffle(order)
        seats = [Seat(id=i, name=roster[i].name, stack=stack) for i in order]
        if sit:
            seats.insert(rng.randrange(len(seats) + 1), Seat(id=YOU, name="你", stack=buy_in))
        game = Holdem(
            seats=seats,
            small_blind=rules.holdem.small_blind,
            big_blind=rules.holdem.big_blind,
            rng=rng,
        )
        self.table = Table(
            id=secrets.token_hex(4),
            game=game,
            members={i: roster[i] for i in picked},
            seated=sit,
            think=rules.members_think if think is None else bool(think),
            seed=seed,
            buy_in=buy_in,
        )
        if sit:  # 坐下就开第一手；旁观先让你押注再发牌
            self._start_hand()
        return self.view()

    def leave(self) -> dict[str, Any]:
        """散桌：你桌上剩的筹码换回金币；没结算的押注退回。"""
        t = self.table
        if t is None:
            return {"wallet": self._wallet_view()}
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        back = 0
        if t.seated:
            back = t.game.seats[t.game.index_of(YOU)].stack
            if t.game.street not in ("idle", "done"):  # 这一手没打完：下在桌上的也退回
                back += t.game.seats[t.game.index_of(YOU)].put
            net = back - t.buy_in
            self._add_coins(back, won=max(0, net), lost=max(0, -net))
        if t.bet is not None:
            self._add_coins(int(t.bet["amount"]))
        self.table = None
        return {"wallet": self._wallet_view(), "back": back}

    # --- 你的动作 ---------------------------------------------------------------

    def deal(self, bet_on: str | None = None, amount: int = 0) -> dict[str, Any]:
        """下一手。旁观时可以顺便押这一手谁赢（赢了按这一手的人数赔，平分就按人数分）。"""
        t = self._need_table()
        if self.busy():
            raise ArcadeError("成员还在想")
        if t.game.street not in ("idle", "done"):
            raise ArcadeError("这一手还没打完")
        if not self._you_can_play():
            raise ArcadeError("你的筹码输光了，可以离桌换一桌")
        if not t.game.can_start():
            raise ArcadeError("有筹码的人不到两位了，换一桌吧")
        if bet_on:
            if t.seated:
                raise ArcadeError("坐在桌上不能押注，旁观时才能押")
            amount = int(amount)
            if amount <= 0 or amount > self.rules.wallet.max_bet:
                raise ArcadeError(f"一手最多押 {self.rules.wallet.max_bet} 金币")
            seat = next((s for s in t.game.seats if s.id == bet_on), None)
            if seat is None or seat.stack <= 0:
                raise ArcadeError("押的人不在这桌，或者已经没筹码了")
            if int(self.wallet().get("coins", 0)) < amount:
                raise ArcadeError("金币不够")
            self._add_coins(-amount)
            t.bet = {"on": bet_on, "name": seat.name, "amount": amount}
        self._start_hand()
        return self.view()

    def act(self, kind: str, to: int | None = None) -> dict[str, Any]:
        t = self._need_table()
        if not t.seated:
            raise ArcadeError("你在旁观，没坐在桌上")
        if self.busy():
            raise ArcadeError("还没轮到你")
        try:
            t.game.act(t.game.index_of(YOU), kind, to)
        except HoldemError as e:
            raise ArcadeError(str(e)) from None
        t.says.pop(YOU, None)
        self._after_move()
        return self.view()

    def set_think(self, on: bool) -> dict[str, Any]:
        t = self._need_table()
        t.think = bool(on)
        t.note = "" if on else "成员都由本地电脑代打（不花钱）"
        return self.view()

    # --- 推进 -------------------------------------------------------------------

    def _start_hand(self) -> None:
        t = self._need_table()
        t.says, t.brains, t.note = {}, {}, ""
        if t.bet is not None:
            t.bet["players"] = len([s for s in t.game.seats if s.stack > 0])
        t.game.start_hand()
        self._after_move()

    def _after_move(self) -> None:
        t = self._need_table()
        if t.game.street == "done":
            self._settle()
            return
        if self._member_turn() is not None:
            self.kick()

    def _member_turn(self) -> int | None:
        t = self.table
        if t is None or t.game.turn is None:
            return None
        return t.game.turn if t.game.seats[t.game.turn].id != YOU else None

    def kick(self) -> None:
        """在后台让成员一个个出，直到轮到你或这一手结束（网页轮询看进度）。"""
        if self.busy():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 没有事件循环（同步测试）：调用方自己 await drive()
        self._task = loop.create_task(self.drive())

    async def drive(self) -> None:
        t = self.table
        while t is not None and t is self.table:
            i = self._member_turn()
            if i is None:
                break
            seat = t.game.seats[i]
            t.thinking = seat.id
            try:
                move, say, brain = await self._decide(t, i)
            finally:
                t.thinking = None
            if t is not self.table:  # 想的时候你离桌了
                return
            try:
                t.game.act(i, move["kind"], move.get("to"))
            except HoldemError:  # 模型挑的和局面对不上（不该发生）：本地电脑重挑
                fallback = holdem_bot.decide(t.game, i, t.game.rng, t.seed)
                t.game.act(i, fallback["kind"], fallback.get("to"))
                brain = "local"
            t.brains[seat.id] = brain
            if say:
                t.says[seat.id] = say
            if t.game.street == "done":
                self._settle()
                break

    def _settle(self) -> None:
        """一手结束：结算押注。"""
        t = self._need_table()
        if t.bet is None:
            return
        bet, t.bet = t.bet, None
        winners = t.game.winners()
        payout = 0
        if bet["on"] in winners:
            payout = int(bet["amount"]) * max(2, int(bet.get("players", 2))) // len(winners)
            self._add_coins(payout, won=payout - int(bet["amount"]))
        else:
            self._add_coins(0, lost=int(bet["amount"]))
        t.last_bet = {**bet, "payout": payout, "win": payout > 0}

    async def _decide(self, t: Table, i: int) -> tuple[dict[str, Any], str, str]:
        """(动作, 说的话, model / local)。"""
        options = t.game.legal(i)
        if t.think:
            picked = await self._model_pick(t, i, options)
            if picked is not None:
                return picked[0], picked[1], "model"
        if self.rules.think_delay_s:
            await self.sleep(self.rules.think_delay_s)
        return holdem_bot.decide(t.game, i, t.game.rng, t.seed), "", "local"

    async def _model_pick(
        self, t: Table, i: int, options: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], str] | None:
        rt, rules = self.rt, self.rules
        member = t.members.get(t.game.seats[i].id)
        if member is None or not member.available:
            return None
        reason = self.allowed(rules.estimate_usd, member.model_id)
        if reason:
            t.note = reason
            return None
        prompt = rt.prompts.render(
            "arcade_holdem",
            rt.config.roundtable.prompts["arcade_holdem"],
            name=member.name,
            options=option_lines(options, t.game.current, t.game.seats[i].bet),
            **t.game.brief(i),
        )
        params = {
            **rt.config.roundtable.step_params.get(STEP, {}),
            "max_tokens": rules.max_tokens,
        }
        session = rt.repo.chat_session(ROOM)
        try:
            completion = await asyncio.wait_for(
                rt.router.complete(member.model_id, prompt.messages, params), rules.timeout_s
            )
        except (AllChannelsFailed, NoChannelAvailable, ProviderError) as exc:
            rt.repo.record_call(
                session,
                step=STEP,
                role="member",
                model_id=member.model_id,
                messages=prompt.messages,
                prompt=prompt,
                failure=exc,
            )
            return None
        except TimeoutError:
            return None
        rt.repo.record_call(
            session,
            step=STEP,
            role="member",
            model_id=member.model_id,
            messages=prompt.messages,
            prompt=prompt,
            completion=completion,
        )
        return parse_pick(completion.text, options)


def option_lines(options: list[dict[str, Any]], current: int, bet: int) -> str:
    lines = []
    for n, o in enumerate(options, 1):
        kind = o["kind"]
        if kind == "call":
            text = f"跟注（再放 {current - bet}）"
        elif kind == "raise":
            text = f"加注到 {o['to']}"
        elif kind == "allin":
            text = f"全下（加到 {o['to']}）"
        else:
            text = KIND_ZH[kind]
        lines.append(f"{n}. {text}")
    return "\n".join(lines)


def parse_pick(text: str, options: list[dict[str, Any]]) -> tuple[dict[str, Any], str] | None:
    """读模型的回答：编号要在范围里；说的话截到 30 字、去掉换行。答非所问返回 None。"""
    try:
        data = extract_json_object(text)
    except JSONOutputError:
        return None
    pick = data.get("pick")
    with contextlib.suppress(TypeError, ValueError):
        pick = int(pick)
    if not isinstance(pick, int) or not 1 <= pick <= len(options):
        return None
    say = data.get("say")
    say = " ".join(str(say).split())[:MAX_SAY] if isinstance(say, str) else ""
    return options[pick - 1], say
