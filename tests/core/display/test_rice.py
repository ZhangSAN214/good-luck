"""token 用量显示为"大米"：1 粒 = 1000 token，1 勺 = 100 粒，1 碗 = 30 勺。"""

from __future__ import annotations

import pytest

from roundtable.core.config import load_config
from roundtable.core.config.schema import RiceRules
from roundtable.core.display import format_rice

RICE = load_config().roundtable.display.rice


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (0, "0 粒"),
        (800, "0.8 粒"),
        (3_500, "3.5 粒"),
        (85_000, "85 粒"),
        (99_600, "1 勺"),  # 先取整再换算：不会显示 100 粒
        (100_000, "1 勺"),
        (150_000, "1 勺 50 粒"),
        (3_000_000, "1 碗"),
        (3 * 3_000_000 + 12 * 100_000, "3 碗 12 勺"),
        (3 * 3_000_000 + 12 * 100_000 + 40_000, "3 碗 12 勺"),  # 碗以上舍去粒
    ],
)
def test_format_rice(tokens, expected):
    assert format_rice(tokens, RICE) == expected


def test_default_ratios_come_from_config():
    assert (RICE.grain_tokens, RICE.spoon_grains, RICE.bowl_spoons) == (1000, 100, 30)
    assert format_rice(85_000) == format_rice(85_000, RICE)


def test_ratios_and_names_are_configurable():
    rules = RiceRules(
        grain_name="g",
        spoon_name="s",
        bowl_name="b",
        grain_tokens=10,
        spoon_grains=5,
        bowl_spoons=2,
    )
    assert format_rice(30, rules) == "3 g"
    assert format_rice(70, rules) == "1 s 2 g"
    assert format_rice(10 * 5 * 2 * 3 + 5 * 10 * 1, rules) == "3 b 1 s"


def test_negative_is_clamped():
    assert format_rice(-5, RICE) == "0 粒"
