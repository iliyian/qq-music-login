#!/usr/bin/env bash
#
# 服务器一键部署脚本
# 安装依赖 + 创建 systemd timer（每 2 天自动运行）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="qq-music-login"

echo "=== QQ音乐 Key 自动刷新 - 服务器部署 ==="
echo ""

# ─── 1. 安装系统依赖 ──────────────────────────────────────

echo "[1/4] 安装系统依赖..."
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-pip python3-venv xvfb
elif command -v dnf &>/dev/null; then
    sudo dnf install -y python3 python3-pip xorg-x11-server-Xvfb
elif command -v yum &>/dev/null; then
    sudo yum install -y python3 python3-pip xorg-x11-server-Xvfb
else
    echo "警告：未识别的包管理器，请手动安装 python3、pip、xvfb"
fi

# ─── 2. 安装 Python 依赖 ──────────────────────────────────

echo "[2/4] 安装 Python 依赖..."
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    python3 -m venv "$SCRIPT_DIR/.venv"
fi
source "$SCRIPT_DIR/.venv/bin/activate"
pip install --quiet playwright python-dotenv requests
python -m playwright install --with-deps chromium
deactivate

# ─── 3. 创建 systemd 服务和定时器 ─────────────────────────

echo "[3/4] 创建 systemd 服务和定时器..."

sudo tee /etc/systemd/system/${SERVICE_NAME}.service >/dev/null <<EOF
[Unit]
Description=QQ音乐 Key 自动刷新
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${SCRIPT_DIR}
ExecStart=/usr/bin/xvfb-run --auto-servernum ${SCRIPT_DIR}/.venv/bin/python ${SCRIPT_DIR}/qq_music_login.py --headless
TimeoutStartSec=300
User=$(whoami)

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/${SERVICE_NAME}.timer >/dev/null <<EOF
[Unit]
Description=每2天运行一次QQ音乐 Key 刷新

[Timer]
OnCalendar=*-*-1/2 04:00:00
Persistent=true
RandomizedDelaySec=1800

[Install]
WantedBy=timers.target
EOF

# ─── 4. 启用并启动定时器 ──────────────────────────────────

echo "[4/4] 启用定时器..."
sudo systemctl daemon-reload
sudo systemctl enable --now ${SERVICE_NAME}.timer

echo ""
echo "=== 部署完成 ==="
echo ""
echo "查看定时器状态:  systemctl status ${SERVICE_NAME}.timer"
echo "查看下次运行时间: systemctl list-timers ${SERVICE_NAME}.timer"
echo "手动运行一次:    sudo systemctl start ${SERVICE_NAME}.service"
echo "查看运行日志:    journalctl -u ${SERVICE_NAME}.service -e"
