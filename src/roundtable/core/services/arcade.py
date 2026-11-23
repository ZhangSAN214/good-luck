"""游戏区（docs/ARCADE.md）：开桌、出牌、发下一手、押注、离桌。牌局本身在 core/arcade。"""

from __future__ import annotations

from typing import Any

from roundtable.core.arcade import ArcadeError
from roundtable.core.services.common import ServiceError


def _wrap(fn, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except ArcadeError as e:
        raise ServiceError(str(e), 409) from None


class ArcadeService:
    """游戏区：德州扑克（之后斗地主、跑得快、升级、麻将、坦克）。"""

    def arcade_status(self) -> dict[str, Any]:
        return self.arcade.status()

    def arcade_table(self) -> dict[str, Any]:
        return _wrap(self.arcade.view)

    def arcade_open(
        self, game: str, members: list[str], *, sit: bool, think: bool | None
    ) -> dict[str, Any]:
        if game == "holdem":
            return _wrap(self.arcade.open_holdem, members, sit=sit, think=think)
        if game in ("doudizhu", "paodekuai", "shengji", "mahjong"):
            return _wrap(self.arcade.open_cards, game, members, sit=sit, think=think)
        raise ServiceError("这个游戏还没做好", 404)

    def arcade_act(self, kind: str, to: int | None) -> dict[str, Any]:
        return _wrap(self.arcade.act, kind, to)

    def arcade_play(self, kind: str, cards: list[str], bid: int) -> dict[str, Any]:
        return _wrap(self.arcade.play, {"kind": kind, "cards": cards, "bid": bid})

    def arcade_deal(self, bet_on: str | None, amount: int) -> dict[str, Any]:
        return _wrap(self.arcade.deal, bet_on, amount)

    def arcade_god(self, on: bool) -> dict[str, Any]:
        return _wrap(self.arcade.set_god, on)

    def arcade_think(self, on: bool) -> dict[str, Any]:
        return _wrap(self.arcade.set_think, on)

    def arcade_tanks_start(self, members: list[str]) -> dict[str, Any]:
        return _wrap(self.arcade.tanks_start, members)

    def arcade_tanks_result(self, token: str, place: int, kills: int) -> dict[str, Any]:
        return _wrap(self.arcade.tanks_result, token, place, kills)

    def arcade_leave(self) -> dict[str, Any]:
        return self.arcade.leave()
