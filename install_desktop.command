#!/bin/sh
# 圆桌桌面版（macOS，在访达里双击一次）：安装依赖，并在「应用程序」里生成「圆桌.app」（见 docs/DESKTOP.md）。
# 再双击一次就是更新（git pull + 安装）。
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
        PY="$c"; break
    fi
done
if [ -z "$PY" ]; then
    echo "[error] 需要 Python 3.11 以上：从 https://www.python.org/downloads/macos/ 安装后再双击本文件。"
    read -r _ ; exit 1
fi

[ -x .venv/bin/python ] || "$PY" -m venv .venv || { echo "[error] 创建 .venv 失败"; read -r _; exit 1; }

echo "[0/3] git pull ..."
GIT_TERMINAL_PROMPT=0 git pull || echo "[warn] git pull 失败，用本地代码继续。"

echo "[1/3] pip install -e .[dev,desktop] ..."
.venv/bin/python -m pip install -e ".[dev,desktop]" || { echo "[error] 安装失败"; read -r _; exit 1; }

echo "[2/3] 代码沙箱 ..."
.venv/bin/python scripts/setup_sandbox.py --check >/dev/null 2>&1 || .venv/bin/python scripts/setup_sandbox.py \
    || echo "[warn] 沙箱没装好：python 工具不可用。"

echo "[3/3] 生成 圆桌.app ..."
.venv/bin/python scripts/install_desktop.py || { echo "[error] 生成失败"; read -r _; exit 1; }
[ -f .env ] || echo "[warn] 没有 .env：把 .env.example 复制成 .env，至少填一个 API key。"
echo "完成：在「应用程序」（访达 → 前往 → 个人 → 应用程序）里打开「圆桌」，可以拖到程序坞。"
