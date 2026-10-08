"""路由相关的确认卡片：单题花费超过门槛、升级。"""

from __future__ import annotations

from roundtable.core.cards import CardOption, ConfirmationCard

from .decide import RoutingDecision


def cost_card(decision: RoutingDecision) -> ConfirmationCard:
    """预计花费超过单题门槛时：按当前方案继续、换成其他方案、或停止。"""
    current = decision.plan
    label = decision.options[current].label if current else "手动选择的阵容"
    options = [CardOption("continue", f"按「{label}」继续", decision.estimate.total_usd)]
    for name, option in decision.options.items():
        if name != current and option.available and option.estimate:
            options.append(
                CardOption(f"plan:{name}", f"改用「{option.label}」", option.estimate.total_usd)
            )
    options.append(CardOption("stop", "停止", 0.0))
    difficulty = decision.assessment.difficulty or "未判断"
    return ConfirmationCard(
        kind="cost",
        situation=(
            f"难度：{difficulty}；方案「{label}」预计 ${decision.estimate.total_usd:.2f}，"
            f"超过单题确认门槛 ${decision.confirm_threshold_usd:.2f}。"
        ),
        options=tuple(options),
        recommendation="continue",
        reason="这是根据题目难度或你的选择得出的方案",
        details={"plan": current, "difficulty": decision.assessment.difficulty},
    )


def escalation_card(escalation: RoutingDecision) -> ConfirmationCard:
    """需要升级且升级花费超过门槛时：升级、采用当前结果、或停止。"""
    target = escalation.options[escalation.plan].label
    return ConfirmationCard(
        kind="escalation",
        situation=(
            f"当前结果{escalation.escalation_reason}。"
            f"升级为「{target}」预计 ${escalation.estimate.total_usd:.2f}。"
        ),
        options=(
            CardOption("continue", f"升级为「{target}」", escalation.estimate.total_usd),
            CardOption("accept", "采用当前结果（保留分歧）", 0.0),
            CardOption("stop", "停止", 0.0),
        ),
        recommendation="continue",
        reason="当前结果存在未解决的分歧或把握不足",
        details={"from": escalation.escalated_from, "to": escalation.plan},
    )
