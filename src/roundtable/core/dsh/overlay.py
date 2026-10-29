"""启动 dsh 时用 ``--patch`` 叠加的配置层。

``--patch`` 在 dsh 的配置层里优先级最高（高于插件、profile 和 dsh 设置页写的内容），
所以这里写下的几行用户在 dsh 设置页里也改不掉：

- 关掉会话日志上传（``session-log-deepseek`` 默认会把会话日志随请求一起发给模型服务）；
- 关掉插件清单上报（``plugin-package-inventory-deepseek`` 会把装了哪些插件随请求发出去）；
- 关掉会话遥测（另外还用环境变量 ``DSH_TELEMETRY_DISABLED`` 关一次）；
- 身份说明换成圆桌登记过的中立版本（提示词 ``dsh_workbench``），不带任何人设。

行的 id 是 dsh 官方配置里的插件行 id（dsh 0.2.1-rc.3 实测）；dsh 改了 id 时，升级 PR 要同步改这里。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# 必须关掉的行：会把数据发出本机
DISABLED_ROWS = ("plugin-package-inventory-deepseek", "session-telemetry-otel")
SESSION_LOG_ROW = "session-log-deepseek"
PERSONA_ROW = "system-prompt"


def overlay_rows(persona: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [{"id": SESSION_LOG_ROW, "config": {"enabled": False}}]
    rows += [{"id": row, "disabled": True} for row in DISABLED_ROWS]
    rows.append({"id": PERSONA_ROW, "config": {"personaPrefix": persona.strip()}})
    return rows


def write_overlay(path: Path, persona: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (
        "# 圆桌自动生成，每次启动 dsh 时重写，不要手改（见 src/roundtable/core/dsh/overlay.py）\n"
    )
    body = yaml.safe_dump(overlay_rows(persona), allow_unicode=True, sort_keys=False)
    path.write_text(head + body, encoding="utf-8")
    return path
