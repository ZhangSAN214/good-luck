"""测试用的假形象素材：临时目录里的 members.json 与 1×1 png（不使用、不提交真实图片）。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

# 1×1 透明 PNG
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def write_assets(
    root: Path, vendor: str, *, member_id: str = "m1", extra: list | None = None
) -> Path:
    """生成一个成员（vendor 由调用方从配置里取）的完整素材，返回素材目录。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / member_id).mkdir(exist_ok=True)
    for name in ("c128.png", "c64.png", "chibi.png"):
        (root / member_id / name).write_bytes(PNG_1PX)
    members = [
        {
            "id": member_id,
            "name": "成员一",
            "vendor": vendor,
            "nickname": "小一",
            "theme_color": "#3366cc",
            "order": 1,
            "status": "active",
            "extra_field": "容忍多余字段",
            "images": {
                "avatar_circle_light": {
                    "256": f"{member_id}/missing256.png",
                    "128": f"{member_id}/c128.png",
                    "64": f"{member_id}/c64.png",
                },
                "chibi_transparent": f"{member_id}/chibi.png",
                "avatar": f"{member_id}/nope.png",
            },
        },
        *(extra or []),
    ]
    (root / "members.json").write_text(
        json.dumps({"version": 2, "members": members}, ensure_ascii=False), encoding="utf-8"
    )
    return root
