"""装后核对：装完逐项核对是否真的生效（不只看安装命令的返回码），结果写到 data/install_check.json。

    python scripts/install_check.py --source start --extras dev,windows    # start.bat 用
    python scripts/install_check.py --source update --extras dev,media     # update_and_start.bat 用

退出码：0 全部通过或只有提醒；1 有会导致起不来的问题（批处理里只提示，照常启动）。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roundtable.core.installcheck import BAD, print_items, run_checks, save_record  # noqa: E402

DB_ENV = "ROUNDTABLE_DB_PATH"  # 和 core/runtime.py 一致


def data_dir() -> Path:
    configured = os.environ.get(DB_ENV)
    if configured and configured != ":memory:":
        return (ROOT / configured).parent
    return ROOT / "data"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="装后核对")
    parser.add_argument("--source", default="start", choices=["start", "update"])
    parser.add_argument("--extras", default="dev", help="和 pip install -e .[extras] 一致")
    parser.add_argument("--port", type=int, default=8000, help="0 = 不查端口")
    args = parser.parse_args(argv)
    data = data_dir()
    extras = [e for e in args.extras.split(",") if e]
    items = run_checks(ROOT, data, extras, port=args.port or None)
    save_record(data, args.source, items, datetime.now())
    bad = [i for i in items if not i.ok]
    print(f"装后核对：{len(items) - len(bad)}/{len(items)} 项通过")
    print_items(bad)
    return 1 if any(i.level == BAD for i in bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
