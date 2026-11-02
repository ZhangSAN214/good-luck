"""一次性安装 dsh 工作台（docs/DSH.md）：dsh 运行时 + 白名单插件。

    python scripts/setup_dsh.py            # 安装运行时和 config/dsh_plugins.yaml 里启用的插件
    python scripts/setup_dsh.py --check    # 只检查 Node / npm / pnpm 和是否已装好

需要：Node（版本见 config/dsh_plugins.yaml 的 runtime.node_min）、npm、pnpm（npm install -g pnpm）。
全部装在项目的 data/dsh/ 下（不进 git）；删掉 data/dsh/ 就是卸载，圆桌其他功能不受影响。
插件安装包下载后核对 sha256，对不上就拒绝安装。dsh 的模型 key（roundtable.yaml 的 dsh.key_env）
不在安装时使用，只在圆桌里点「启动」时才交给 dsh。
装完逐项核对是否真的生效（运行时文件、版本、插件安装包）；没过就把运行时整批退回到装之前。
核对结果写到 data/install_check.json 的 dsh 一节，圆桌「出问题点这里」会显示。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roundtable.core.config import load_config  # noqa: E402
from roundtable.core.dsh.catalog import CATALOG_FILE, load_catalog  # noqa: E402
from roundtable.core.dsh.channel import ModelKey  # noqa: E402
from roundtable.core.dsh.install import Check, InstallError, install_safely, verify  # noqa: E402
from roundtable.core.dsh.paths import DshPaths  # noqa: E402
from roundtable.core.installcheck import save_record  # noqa: E402
from roundtable.core.runtime import data_dir  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安装 dsh 工作台（运行时 + 白名单插件）")
    parser.add_argument("--check", action="store_true", help="只检查，不安装")
    args = parser.parse_args(argv)

    catalog = load_catalog(ROOT / "config" / CATALOG_FILE)
    paths = DshPaths(data_dir() / "dsh", catalog.runtime.package)
    if args.check:
        checks = verify(catalog, paths)
        for c in checks:
            print(("✓ " if c.ok else "✗ ") + c.detail)
        record(checks)
        return 0 if all(c.ok for c in checks) else 1

    rules = load_config().roundtable.dsh
    # 安装插件时 dsh 只需要家目录，不给它任何 key
    env = ModelKey(None, None).child_env(
        {
            "DSH_HOME": str(paths.home),
            "DSH_TELEMETRY_DISABLED": "1",
            "DSH_PERMISSION_MODE": rules.permission_mode,
        }
    )
    try:
        checks = install_safely(catalog, paths, env)
    except InstallError as exc:
        print(f"✗ {exc}")
        record(getattr(exc, "checks", None) or [Check("install", False, str(exc))])
        return 1
    for c in checks:
        print(("✓ " if c.ok else "✗ ") + c.detail)
    record(checks)
    print("装后核对全部通过。")
    return 0


TITLES = {"node": "Node", "npm": "npm", "pnpm": "pnpm", "runtime": "dsh 运行时"}
TITLES["runtime_version"] = "dsh 运行时版本"


def record(checks: list[Check]) -> None:
    """写到 data/install_check.json 的 dsh 一节（「出问题点这里」读）。"""
    items = [
        {
            "id": c.name,
            "ok": c.ok,
            "title": TITLES.get(c.name, "插件" if c.name.startswith("plugin_") else c.name),
            "detail": c.detail,
            "level": "warn",
            "fix": "" if c.ok else "看上面的提示，修好后再运行 python scripts/setup_dsh.py",
        }
        for c in checks
    ]
    save_record(data_dir(), "dsh", items, datetime.now())


if __name__ == "__main__":
    raise SystemExit(main())
