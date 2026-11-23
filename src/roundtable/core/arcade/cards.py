"""扑克牌：52 张，牌面写成两个字符（点数 + 花色），如 ``As``（黑桃 A）、``Td``（方块 10）。"""

from __future__ import annotations

import random

RANKS = "23456789TJQKA"
SUITS = "shdc"  # 黑桃 红心 方块 梅花
SUIT_ZH = {"s": "黑桃", "h": "红心", "d": "方块", "c": "梅花"}
RANK_ZH = {"T": "10", "J": "J", "Q": "Q", "K": "K", "A": "A"}


def rank_of(card: str) -> int:
    """点数大小：2 → 2 … A → 14。"""
    return RANKS.index(card[0]) + 2


def suit_of(card: str) -> str:
    return card[1]


def new_deck(rng: random.Random) -> list[str]:
    deck = [r + s for r in RANKS for s in SUITS]
    rng.shuffle(deck)
    return deck


def card_zh(card: str) -> str:
    """给模型和日志看的中文写法：黑桃A、红心10。"""
    return f"{SUIT_ZH[card[1]]}{RANK_ZH.get(card[0], card[0])}"


def cards_zh(cards: list[str]) -> str:
    return " ".join(card_zh(c) for c in cards) or "（无）"
