#!/usr/bin/env bash
# 圆桌服务器更新（docs/SERVER.md）：先存档点、拉新代码、冒烟测试不过就原样退回，再重启服务。用 root 运行。
set -euo pipefail
APP=/opt/roundtable
[ "$(id -u)" -eq 0 ] || { echo "请用 root 运行"; exit 1; }
set +e
sudo -u roundtable bash -c "cd '$APP' && .venv/bin/python scripts/safe_update.py update --extras media --ff-only"
code=$?
set -e
case $code in
  0) systemctl restart roundtable && echo "已更新并重启" ;;
  2) echo "新版本没通过冒烟测试，已退回旧版本，服务没动" ;;
  3) echo "没更新成，照旧用现在的代码" ;;
  *) echo "更新出错（退出码 $code），服务没动"; exit 1 ;;
esac
