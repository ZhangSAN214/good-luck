from __future__ import annotations

import pytest

from roundtable.core.cards import CardOption, ConfirmationCard


def test_card_validation():
    with pytest.raises(ValueError, match="不能重复"):
        ConfirmationCard("x", "s", (CardOption("a", "A"), CardOption("a", "B")), "a")
    with pytest.raises(ValueError, match="推荐"):
        ConfirmationCard("x", "s", (CardOption("a", "A"),), "b")


def test_card_dict():
    card = ConfirmationCard("x", "现状", (CardOption("go", "继续", 0.1),), "go", "理由")
    d = card.to_dict()
    assert d["situation"] == "现状" and d["options"][0]["cost_usd"] == 0.1
