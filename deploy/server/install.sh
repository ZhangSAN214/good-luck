#!/usr/bin/env bash
# 圆桌：在 Ubuntu 24.04 云服务器上一键安装（docs/SERVER.md）。用 root 运行，可重复运行。
#   curl -fsSL https://raw.githubusercontent.com/ZhangSAN214/good-luck/ccr-86975127-sv4cvy/deploy/server/install.sh | bash
# 只装网页服务和用 API 的功能；显卡出图、本地配音、ChatGPT 网页画图、游戏模式等留在电脑上。
# 不会读取或写入任何 key：.env 由你自己在服务器上填。
set -euo pipefail

REPO="${RT_REPO:-https://github.com/ZhangSAN214/good-luck.git}"
BRANCH="${RT_BRANCH:-ccr-86975127-sv4cvy}"
APP=/opt/roundtable
SWAP_GB="${RT_SWAP_GB:-4}"

say() { printf '\n== %s\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "请用 root 运行（sudo -i 后再运行）"; exit 1; }

say "1/7 安装系统软件（约 1～3 分钟）"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git python3 python3-venv python3-pip ffmpeg unzip sqlite3 ufw curl ca-certificates

say "2/7 加 ${SWAP_GB} GB 交换空间（2 GB 内存不够时兜底）"
if ! swapon --show | grep -q .; then
  fallocate -l "${SWAP_GB}G" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -q vm.swappiness=20
  echo 'vm.swappiness=20' > /etc/sysctl.d/90-roundtable.conf
else
  echo "已有交换空间，跳过"
fi

say "3/7 建专用用户 roundtable，下载代码（分支 ${BRANCH}）"
id roundtable >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/roundtable --shell /bin/bash roundtable
if [ -d "$APP/.git" ]; then
  echo "代码已存在，跳过下载（更新用 deploy/server/update.sh）"
else
  git clone --branch "$BRANCH" "$REPO" "$APP"
fi
chown -R roundtable:roundtable "$APP"

say "4/7 安装 Python 依赖（约 2～5 分钟）"
sudo -u roundtable bash -c "cd '$APP' && python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -e '.[media]'"

say "5/7 代码运行沙箱（Deno + Pyodide，一次性下载；失败不影响其他功能）"
sudo -u roundtable bash -c "cd '$APP' && .venv/bin/python scripts/setup_sandbox.py" || echo "[提示] 沙箱没装上：python 工具先关着，其余照常"

if [ ! -f "$APP/.env" ]; then
  install -o roundtable -g roundtable -m 600 "$APP/.env.example" "$APP/.env"
  echo "[提示] 已生成空的 .env：稍后用 nano $APP/.env 填 key（只在服务器上填，不要发到聊天里）"
fi
chmod 600 "$APP/.env"

say "6/7 装 Tailscale（只有你账号里的设备能连圆桌）"
command -v tailscale >/dev/null 2>&1 || curl -fsSL https://tailscale.com/install.sh | sh

say "7/7 防火墙：只放行 SSH 和 Tailscale，8000 端口不对公网开放"
ufw allow OpenSSH >/dev/null
ufw allow in on tailscale0 >/dev/null
ufw --force enable >/dev/null
ufw status verbose | sed -n 1,12p

install -m 644 "$APP/deploy/server/roundtable.service" /etc/systemd/system/roundtable.service
systemctl daemon-reload
systemctl enable --now roundtable
sleep 3
systemctl --no-pager --lines=5 status roundtable || true

cat <<MSG

装好了。接下来（见 docs/SERVER.md）：
  1. tailscale up            # 打印一个网址，用电脑浏览器打开、登录你自己的 Tailscale 账号
  2. nano $APP/.env          # 填 key，Ctrl+O 保存、Ctrl+X 退出；然后 systemctl restart roundtable
  3. 在电脑上用 SSH 隧道打开圆桌、设访问口令（文档第 4 步）
MSG
