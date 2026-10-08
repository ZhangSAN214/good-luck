"""路由相关的确认卡片：单题花费超过门槛、升级（升级总是先询问）。"""

from __future__ import annotations

from roundtable.core.cards import CardOption, ConfirmationCard, money

from .decide import RoutingDecision


def _people(decision: RoutingDecision) -> str:
    lineup = decision.lineup
    text = f"{len(lineup.members)} 位组员 + 1 位统筹"
    if lineup.absent:
        text += f"，{len(lineup.absent)} 个模型因没有可用渠道缺席"
    return text


def _range(decision: RoutingDecision) -> str:
    """预计花费（及上限）：上限 = 每次调用都写满该步骤的输出长度上限。"""
    e = decision.estimate
    text = f"预计 {money(e.total_usd)}"
    if e.max_usd > e.total_usd:
        text += f"（最多约 {money(e.max_usd)}）"
    if e.calibrated:
        text += "，已按本机历史记录校准"
    return text


def cost_card(decision: RoutingDecision) -> ConfirmationCard:
    """预计花费超过单题门槛时：按当前档位继续、换成其他档位、或停止。"""
    current = decision.plan
    label = decision.options[current].label
    options = [CardOption("continue", f"按「{label}」继续", decision.estimate.total_usd)]
    for name, option in decision.options.items():
        if name != current and option.available and option.estimate:
            options.append(
                CardOption(f"plan:{name}", f"改用「{option.label}」", option.estimate.total_usd)
            )
    options.append(CardOption("stop", "停止", 0.0))
    return ConfirmationCard(
        kind="cost",
        situation=(
            f"「{label}」：{_people(decision)}，{_range(decision)}，"
            f"超过单题确认门槛 {money(decision.confirm_threshold_usd)}。"
        ),
        options=tuple(options),
        recommendation="continue",
        reason="这是你选择的档位；想省钱可以改用更便宜的档位（人数不变）",
        details={"plan": current, "max_usd": decision.estimate.max_usd},
    )


def escalation_card(escalation: RoutingDecision) -> ConfirmationCard:
    """汇总仍有分歧或把握低：询问是否用更高档位重做、采用当前结果、或停止。"""
    target = escalation.options[escalation.plan].label
    return ConfirmationCard(
        kind="escalation",
        situation=(
            f"当前结果{escalation.escalation_reason}。"
            f"用「{target}」（{_people(escalation)}）重做，{_range(escalation)}。"
        ),
        options=(
            CardOption("continue", f"用「{target}」重做", escalation.estimate.total_usd),
            CardOption("accept", "采用当前结果（保留分歧）", 0.0),
            CardOption("stop", "停止", 0.0),
        ),
        recommendation="continue",
        reason="当前结果存在未解决的分歧或把握不足",
        details={"from": escalation.escalated_from, "to": escalation.plan},
    )
