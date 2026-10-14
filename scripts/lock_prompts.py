"""登记新的提示词版本到 prompts/versions.lock。

已登记的版本一旦内容改变，本脚本会拒绝更新并退出码 1：
修改已发布的提示词必须新建版本文件（v2、v3…），旧版本保持不变，
这样历史讨论记录里的"版本号 + 哈希"永远能对应到原文。

用法（在项目根目录）：
    python scripts/lock_prompts.py           # 登记新版本
    python scripts/lock_prompts.py --check   # 只检查，不写文件
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from roundtable.core.prompts import DEFAULT_PROMPTS_DIR, PromptLibrary

LOCK_NAME = "versions.lock"


def current_hashes(root: Path) -> dict[str, str]:
    library = PromptLibrary(root)
    return {
        f"{role}/{version}": library.get(role, version).sha256
        for role in library.roles()
        for version in library.versions(role)
    }


def diff(root: Path) -> tuple[dict[str, str], list[str], list[str], dict[str, str]]:
    """返回 (当前哈希, 新增, 已删除, 已改动)。"""
    lock_path = root / LOCK_NAME
    locked = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.is_file() else {}
    current = current_hashes(root)
    added = sorted(set(current) - set(locked))
    removed = sorted(set(locked) - set(current))
    changed = {k: v for k, v in current.items() if k in locked and locked[k] != v}
    return current, added, removed, changed


def main(argv: list[str] | None = None, root: Path = DEFAULT_PROMPTS_DIR) -> int:
    parser = argparse.ArgumentParser(description="登记提示词版本")
    parser.add_argument("--check", action="store_true", help="只检查，不写文件")
    args = parser.parse_args(argv)

    current, added, removed, changed = diff(root)
    for key in changed:
        print(f"✗ {key} 已发布后被修改。请恢复原文，并把改动放进新版本文件。")
    for key in removed:
        print(f"✗ {key} 已登记但文件不存在。已发布的版本不能删除。")
    if changed or removed:
        return 1
    if args.check:
        for key in added:
            print(f"! {key} 尚未登记，请运行 python scripts/lock_prompts.py")
        return 1 if added else 0
    if added:
        (root / LOCK_NAME).write_text(
            json.dumps(current, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        for key in added:
            print(f"✓ 已登记 {key}")
    else:
        print("没有新版本需要登记。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
