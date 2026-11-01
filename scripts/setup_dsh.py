"""一次性安装 dsh 工作台（docs/DSH.md）：dsh 运行时 + 白名单插件。

    python scripts/setup_dsh.py            # 安装运行时和 config/dsh_plugins.yaml 里启用的插件
    python scripts/setup_dsh.py --check    # 只检查 Node / npm / pnpm 和是否已装好

需要：Node（版本见 config/dsh_plugins.yaml 的 runtime.node_min）、npm、pnpm（npm install -g pnpm）。
全部装在项目的 data/dsh/ 下（不进 git）；删掉 data/dsh/ 就是卸载，圆桌其他功能不受影响。
插件安装包下载后核对 sha256，对不上就拒绝安装。dsh 的模型 key（roundtable.yaml 的 dsh.key_env）
不在安装时使用，只在圆桌里点「启动」时才交给 dsh。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roundtable.core.config import load_config  # noqa: E402
from roundtable.core.dsh.catalog import CATALOG_FILE, load_catalog  # noqa: E402
from roundtable.core.dsh.channel import ModelKey  # noqa: E402
from roundtable.core.dsh.install import InstallError, check_tools, install  # noqa: E402
from roundtable.core.dsh.paths import DshPaths  # noqa: E402
from roundtable.core.runtime import data_dir  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安装 dsh 工作台（运行时 + 白名单插件）")
    parser.add_argument("--check", action="store_true", help="只检查，不安装")
    args = parser.parse_args(argv)

    catalog = load_catalog(ROOT / "config" / CATALOG_FILE)
    paths = DshPaths(data_dir() / "dsh", catalog.runtime.package)
    if args.check:
        ok = True
        for c in check_tools(catalog):
            print(("✓ " if c.ok else "✗ ") + c.detail)
            ok = ok and c.ok
        print(
            ("✓ " if paths.installed else "✗ ")
            + f"dsh 运行时 {catalog.runtime.version}"
            + ("已安装" if paths.installed else "未安装")
            + f"（{paths.runtime}）"
        )
        return 0 if ok and paths.installed else 1

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
        install(catalog, paths, env)
    except InstallError as exc:
        print(f"✗ {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
