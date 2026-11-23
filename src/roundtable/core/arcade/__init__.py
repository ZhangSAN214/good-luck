"""游戏区（docs/ARCADE.md）：牌局规则、本地电脑对手、成员用自己的模型出牌、虚拟金币与下注。

只用于生活这边（和群聊一样不受工作模式中立规则的「全员上桌」约束），每位成员用群聊里的那个模型；
所有成员用同一份提示词和参数，只从程序算好的合法出法里挑。
"""

from roundtable.core.arcade.hub import Arcade, ArcadeError

__all__ = ["Arcade", "ArcadeError"]
