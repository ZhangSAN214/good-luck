"""旅行打卡与 B 站（L5，docs/LIFE_STORY.md「旅行打卡」「B 站」）。只用于生活。

- ``card``：判定卡 → 旅行日记（前 3 天上下文）→ 配图（默认关，结构化拍法写在代码里）。
- ``bili``：下载取帧让成员「看过」视频；看完的私信和动态按每天额度自动写，只存本机列表。

和生活引擎（``member.story``）分开：那边只挑事、不调用模型；这边调用模型，花费算生活每日上限。
工作模式不 import 本包（守卫测试）。
"""

from roundtable.core.member.checkin.bili import Bili, BiliUnavailable, Fetched
from roundtable.core.member.checkin.card import Checkin, Scene

__all__ = ["Bili", "BiliUnavailable", "Checkin", "Fetched", "Scene"]
