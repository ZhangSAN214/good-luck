"""配置模型共用的底座（严格模式基类、档位类型）。

从 schema.py 拆出（代码精简）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# 档位：flagship 旗舰（高价高能力）/ budget 便宜档。成本优先路由只看档位和能力标签
Tier = Literal["flagship", "budget"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())
