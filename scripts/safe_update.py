"""安全更新（docs/SAFE_UPDATE.md）：先存档点、只许改白名单、冒烟测试不过就原样退回。

    python scripts/safe_update.py update --extras dev,windows --ff-only   # start.bat 用
    python scripts/safe_update.py update --extras dev,media --strict      # update_and_start.bat 用
    python scripts/safe_update.py undo              # 撤销上一次更新（先关掉圆桌）
    python scripts/safe_update.py smoke --db 副本    # 内部用：在数据库副本上跑冒烟测试

退出码：0 已更新或已是最新；2 更新没通过、已退回旧版本；3 没更新成、照旧用本地代码；1 出错。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roundtable.core.safe_update import SafeUpdate, smoke_main  # noqa: E402

DB_ENV = "ROUNDTABLE_DB_PATH"  # 和 core/runtime.py 一致


def db_path() -> Path:
    configured = os.environ.get(DB_ENV)
    if configured and configured != ":memory:":
        return ROOT / configured
    return ROOT / "data" / "roundtable.db"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["smoke"]:
        return smoke_main(argv[1:])
    parser = argparse.ArgumentParser(description="安全更新圆桌")
    parser.add_argument("action", choices=["update", "undo"])
    parser.add_argument("--extras", default="dev", help="pip install -e .[extras]")
    parser.add_argument("--ff-only", action="store_true", help="git pull --ff-only")
    parser.add_argument("--strict", action="store_true", help="拉不到新代码也算失败")
    args = parser.parse_args(argv)
    db = db_path()
    box = SafeUpdate(ROOT, db.parent, db=db)
    if args.action == "undo":
        return box.undo()
    return box.update(args.extras, ff_only=args.ff_only, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
