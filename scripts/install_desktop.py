"""生成桌面版图标入口（Windows 快捷方式 / macOS .app），见 docs/DESKTOP.md。

python scripts/install_desktop.py              # 默认位置（Windows 桌面与开始菜单；macOS 应用程序）
python scripts/install_desktop.py --dest 目录  # 放到指定目录（CI 测试用）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from roundtable.desktop.install import install


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="生成圆桌桌面版的图标入口")
    p.add_argument("--dest", type=Path, default=None, help="放到这个目录（默认系统位置）")
    args = p.parse_args(argv)
    # 控制台不是 UTF-8 时（没有 chcp 65001 的 cmd、CI）中文路径也不能让安装失败
    sys.stdout.reconfigure(errors="replace")
    root = Path(__file__).resolve().parents[1]
    for path in install(root, args.dest):
        print(f"已生成：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
