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
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roundtable.core.safe_update import SafeUpdate  # noqa: E402

DB_ENV = "ROUNDTABLE_DB_PATH"  # 和 core/runtime.py 一致


def db_path() -> Path:
    configured = os.environ.get(DB_ENV)
    if configured and configured != ":memory:":
        return ROOT / configured
    return ROOT / "data" / "roundtable.db"


# ---- 冒烟测试本体（在新代码、数据库副本上运行；用到 fastapi，所以放在脚本里而不是 core/）----


def run_smoke(db: Path) -> list[str]:
    """建运行时（会在副本上迁移）、建服务、打开首页和几个只读接口。返回问题列表。"""
    problems: list[str] = []
    from fastapi.testclient import TestClient

    from roundtable.api.app import create_app
    from roundtable.core.runtime import Runtime
    from roundtable.core.service import RoundtableService

    rt = Runtime.build(providers={}, db_path=db, dotenv_path=None)
    try:
        svc = RoundtableService(rt)
        with TestClient(create_app(svc), client=("127.0.0.1", 50000)) as client:
            for path in ("/", "/api/status", "/api/budget", "/api/members", "/api/firstaid"):
                r = client.get(path)
                if r.status_code != 200:
                    problems.append(f"GET {path} 返回 {r.status_code}")
        rt.repo.list_sessions(limit=5)
    finally:
        rt.repo.conn.close()
    return problems


def smoke_main(argv: Sequence[str]) -> int:
    """scripts/safe_update.py smoke --db 副本：打印结果，过了返回 0。"""
    db = Path(argv[argv.index("--db") + 1]) if "--db" in argv else None
    if db is None:
        print("缺少 --db")
        return 2
    try:
        problems = run_smoke(db)
    except Exception as exc:  # noqa: BLE001 - 任何异常都算冒烟没过
        from roundtable.core.providers.secrets import redact

        print(redact(f"{type(exc).__name__}: {exc}")[:600])
        return 1
    if problems:
        print("；".join(problems))
        return 1
    print("SMOKE OK")
    return 0


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
