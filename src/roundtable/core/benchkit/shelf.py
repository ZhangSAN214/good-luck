"""项目列表不堆积（2026-11-05 用户：「工作台可以删除不要的项目……不要堆积在这里」）。

- **收起**：你点「收起」的项目，和失败 / 叫停超过 ``FOLD_DAYS`` 天的项目，不再出现在
  「最近的项目」和顶上的切换框里，放进「收起的项目」，随时能放回来。
- **删除**：整个项目文件夹挪进数据目录里的 ``.trash/``（回收站），``TRASH_DAYS`` 天内能恢复，
  过期的打开工作台时自动清掉。正在做的项目不能删。

这里只做纯数据和文件处理，不调模型。
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

FOLD_DAYS = 3  # 失败 / 叫停的项目放这么多天后自动收起
TRASH_DAYS = 30  # 回收站里放这么多天后自动清掉
TRASH = ".trash"
STOPPED = ("failed", "cancelled")


def _when(b: dict[str, Any]) -> datetime | None:
    for k in ("finished_at", "created_at"):
        v = b.get(k)
        if v:
            try:
                return datetime.fromisoformat(str(v))
            except ValueError:
                continue
    return None


def folded(b: dict[str, Any], now: datetime) -> bool:
    """这个项目该不该收起：你点过收起，或失败 / 叫停放了 FOLD_DAYS 天以上（你点过放回来就不收）。"""
    if b.get("shelved") is not None:
        return bool(b["shelved"])
    if b.get("status") not in STOPPED:
        return False
    t = _when(b)
    if t is None:
        return False
    if t.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    return now - t > timedelta(days=FOLD_DAYS)


def trash_dir(root: Path) -> Path:
    return root / TRASH


def to_trash(root: Path, bid: str, now: datetime) -> None:
    """删除 = 挪进回收站（文件夹名前面加上删除时间，好算放了几天）。"""
    src = root / bid
    if not src.is_dir():
        raise KeyError(bid)
    dest = trash_dir(root)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest / f"{now:%Y%m%d%H%M%S}~{bid}"))


def _trashed(root: Path) -> list[tuple[Path, str, datetime | None]]:
    d = trash_dir(root)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.iterdir(), reverse=True):
        if not p.is_dir() or "~" not in p.name:
            continue
        stamp, bid = p.name.split("~", 1)
        try:
            when = datetime.strptime(stamp, "%Y%m%d%H%M%S")
        except ValueError:
            when = None
        out.append((p, bid, when))
    return out


def trash_list(root: Path | None, now: datetime) -> list[dict[str, Any]]:
    """回收站里的项目：标题、删除时间、还剩几天自动清掉。"""
    if root is None:
        return []
    out = []
    for p, bid, when in _trashed(root):
        title = ""
        try:
            b = json.loads((p / "build.json").read_text(encoding="utf-8"))
            title = b.get("title") or str(b.get("request") or "")[:24]
        except (OSError, ValueError):
            pass
        left = None
        if when is not None:
            left = max(0, TRASH_DAYS - (now.replace(tzinfo=None) - when).days)
        out.append(
            {"id": bid, "title": title, "deleted_at": when and when.isoformat(), "left": left}
        )
    return out


def restore(root: Path, bid: str) -> None:
    """从回收站放回原处（原处已经有同名项目就不放，免得覆盖）。"""
    for p, b, _ in _trashed(root):
        if b == bid:
            if (root / bid).exists():
                raise RuntimeError("原来的位置已经有这个项目了")
            shutil.move(str(p), str(root / bid))
            return
    raise KeyError(bid)


def purge(root: Path | None, now: datetime, bid: str | None = None) -> int:
    """清掉回收站里放满 TRASH_DAYS 天的（给了 bid 就只清这一个，不管放了几天）。"""
    if root is None:
        return 0
    n = 0
    for p, b, when in _trashed(root):
        old = when is not None and now.replace(tzinfo=None) - when > timedelta(days=TRASH_DAYS)
        if (bid is None and old) or b == bid:
            shutil.rmtree(p, ignore_errors=True)
            n += 1
    return n
