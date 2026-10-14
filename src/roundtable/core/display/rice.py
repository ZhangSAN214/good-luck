"""token 用量显示为"大米"：1 粒 = 1000 token，1 勺 = 100 粒，1 碗 = 30 勺（比例在配置里）。

自动换算到合适的单位：不足 100 粒只显示粒（"85 粒"），不足 1 碗显示"勺 + 粒"，
再大显示"碗 + 勺"（"3 碗 12 勺"，更小的单位舍去）。只用于 token 数量；金额一律是美元。
"""

from __future__ import annotations

from roundtable.core.config.schema import RiceRules


def format_rice(tokens: int | float, rules: RiceRules | None = None) -> str:
    rules = rules or RiceRules()
    grains = max(0.0, float(tokens)) / rules.grain_tokens
    if grains >= 10:
        grains = float(round(grains))  # 先取整再换算单位，99.6 粒会进位成 1 勺
    if grains < rules.spoon_grains:
        return f"{_grains(grains)} {rules.grain_name}"
    whole = int(grains)
    spoons, rest_grains = divmod(whole, rules.spoon_grains)
    if spoons < rules.bowl_spoons:
        tail = f" {rest_grains} {rules.grain_name}" if rest_grains else ""
        return f"{spoons} {rules.spoon_name}{tail}"
    bowls, rest_spoons = divmod(spoons, rules.bowl_spoons)
    tail = f" {rest_spoons} {rules.spoon_name}" if rest_spoons else ""
    return f"{bowls} {rules.bowl_name}{tail}"


def _grains(grains: float) -> str:
    if grains == 0:
        return "0"
    if grains < 10:  # 小数值保留一位，避免 800 token 显示成"1 粒"
        text = f"{grains:.1f}".rstrip("0").rstrip(".")
        return text if text != "0" else "0.1"
    return str(round(grains))
