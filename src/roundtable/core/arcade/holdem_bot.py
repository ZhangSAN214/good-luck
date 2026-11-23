"""德州扑克的本地电脑对手（不调模型、不花钱）：估胜率，和底池赔率比，决定弃 / 跟 / 加、加多少。

成员的模型想不出来（超时、出错、到了每天上限）时也由它接手。每位成员的「性格」只是两个数：
敢不敢加注（aggression）和多松（looseness），由成员 id 和种子算出来，同一局里固定。

比只看「对随机牌的胜率」聪明的地方：
- 别人加注了，就当她手里是好牌：模拟时把对手的牌限定在像样的范围里（加得越多越紧），不再傻跟到底。
- 加注按牌力挑大小：一般好牌加半个底池，很强加一个底池，不再一律最小加注。
- 翻牌、转牌有听牌（胜率中等）时偶尔半诈唬；大家都过牌、对手少时会试探着下注。
"""

from __future__ import annotations

import random
from typing import Any

from roundtable.core.arcade.cards import RANKS, SUITS
from roundtable.core.arcade.holdem import Holdem
from roundtable.core.arcade.poker_eval import score7

SIMS = 160


def preflop_strength(hole: list[str]) -> float:
    """两张底牌大概多好（0～1）：大牌、对子、同花、连张加分。"""
    a, b = sorted((RANKS.index(c[0]) for c in hole), reverse=True)  # 0 = 2 … 12 = A
    score = (a + b) / 24 * 0.55
    if a == b:
        score += 0.3 + a / 12 * 0.15
    if hole[0][1] == hole[1][1]:
        score += 0.06
    gap = a - b
    if 0 < gap <= 2:
        score += 0.05 - 0.015 * gap
    return min(1.0, score)


def equity(
    hole: list[str],
    board: list[str],
    opponents: int,
    rng: random.Random,
    tight: float = 0.0,
) -> float:
    """随机发完剩下的牌，算赢（平分算一半）的比例。tight > 0 时对手的底牌只从够好的里挑。"""
    if opponents <= 0:
        return 1.0
    known = set(hole) | set(board)
    deck = [r + s for r in RANKS for s in SUITS if r + s not in known]
    wins = 0.0
    for _ in range(SIMS):
        rng.shuffle(deck)
        k = 0
        others = []
        for _ in range(opponents):
            pick = deck[k : k + 2]
            # 对手加过注：她的牌多半不差，差的换一手（最多换几次，免得死循环）
            tries = 0
            while tight > 0 and preflop_strength(pick) < tight and tries < 6:
                j = rng.randrange(k + 2, len(deck) - 1)
                deck[k], deck[j] = deck[j], deck[k]
                j = rng.randrange(k + 2, len(deck) - 1)
                deck[k + 1], deck[j] = deck[j], deck[k + 1]
                pick = deck[k : k + 2]
                tries += 1
            others.append(pick)
            k += 2
        full = board + deck[k : k + 5 - len(board)]
        mine = score7(hole + full)
        best_other = max(score7(o + full) for o in others)
        if mine > best_other:
            wins += 1
        elif mine == best_other:
            wins += 0.5
    return wins / SIMS


def style(seat_id: str, seed: int) -> tuple[float, float]:
    r = random.Random(f"{seed}:{seat_id}")
    return (0.6 + r.random() * 0.8, -0.05 + r.random() * 0.12)  # (aggression, looseness)


def _pressure(game: Holdem, i: int) -> float:
    """别人这一手有多凶：这一轮和之前几轮别人的加注次数，加上现在要跟多少（占底池）。"""
    me = game.seats[i].id
    raises = sum(1 for h in game.history if h["seat"] != me and h["kind"] in ("raise", "allin"))
    now = sum(
        1
        for h in game.history
        if h["seat"] != me and h["kind"] in ("raise", "allin") and h["street"] == game.street
    )
    need = game.to_call(i)
    size = need / max(1, game.pot)
    return min(0.62, 0.1 * raises + 0.08 * now + 0.25 * min(1.0, size))


def _sized(raises: list[dict[str, Any]], target: int) -> dict[str, Any]:
    return min(raises, key=lambda o: abs(o["to"] - target))


def decide(game: Holdem, i: int, rng: random.Random, seed: int = 0) -> dict[str, Any]:
    """返回 legal() 里的一项。"""
    options = game.legal(i)
    if not options:
        raise ValueError("没轮到")
    s = game.seats[i]
    opponents = len(game.alive()) - 1
    tight = _pressure(game, i)
    eq = equity(s.hole, game.board, min(opponents, 4), rng, tight)
    aggression, loose = style(s.id, seed)
    eq += loose
    need = game.to_call(i)
    pot = game.pot
    odds = need / (pot + need) if need else 0.0
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for o in options:
        by_kind.setdefault(o["kind"], []).append(o)
    fair = 1.0 / (opponents + 1)  # 平均下来的胜率
    strong = eq > fair + 0.25 * aggression and eq > 0.5
    good = eq > fair + 0.08
    raises = by_kind.get("raise", [])
    pot_after = pot + need
    # 很强：一个底池；还不错：半个底池
    if strong and raises:
        if eq > 0.8 and "allin" in by_kind and (game.street == "river" or rng.random() < 0.3):
            return by_kind["allin"][0]
        frac = 1.0 if eq > 0.72 else 0.6
        return _sized(raises, game.current + int(pot_after * frac))
    if strong and "allin" in by_kind and eq > 0.8:
        return by_kind["allin"][0]
    drawing = game.street in ("flop", "turn") and 0.3 <= eq < fair + 0.25 and opponents <= 2
    if need == 0:
        # 没人下注：好牌下注拿价值；有听牌、对手少时偶尔半诈唬；最后一个说话、大家都过牌时试探一下
        if good and raises and rng.random() < 0.55 * aggression:
            return _sized(raises, game.current + int(pot_after * 0.5))
        if drawing and raises and rng.random() < 0.3 * aggression:
            return _sized(raises, game.current + int(pot_after * 0.5))
        if (
            raises
            and opponents == 1
            and game.street != "preflop"
            and rng.random() < 0.12 * aggression
        ):
            return _sized(raises, game.current + int(pot_after * 0.4))
        return by_kind["check"][0]
    if drawing and raises and need <= pot * 0.5 and rng.random() < 0.12 * aggression:
        return _sized(raises, game.current + int(pot_after * 0.6))  # 半诈唬加注
    if eq >= odds + 0.03 or (good and need <= game.big_blind * 2):
        if "call" in by_kind:
            return by_kind["call"][0]
        if "allin" in by_kind and eq > 0.45:
            return by_kind["allin"][0]
    return by_kind["fold"][0]
