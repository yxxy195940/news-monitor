#!/bin/bash
# deploy.sh —— 服务器端部署脚本（由 GitHub Actions 远程调用）
set -e  # 任意命令失败即中止

REPO_DIR="${REPO_DIR:-$HOME/antigravity}"
VENV="$REPO_DIR/venv"
SERVICE_NAME="antigravity"

echo "=============================="
echo "🚀 Antigravity Bot 部署开始"
echo "=============================="
echo "📁 工作目录: $REPO_DIR"
echo "🕒 时间: $(date '+%Y-%m-%d %H:%M:%S')"

# 1. 拉取最新代码
cd "$REPO_DIR"
echo ""
echo "📥 [1/4] 拉取最新代码..."
git fetch origin
git reset --hard origin/main
echo "✓ 代码已更新到: $(git log -1 --pretty='%h %s')"

# 2. 安装/更新依赖
echo ""
echo "📦 [2/4] 检查并更新 Python 依赖..."
source "$VENV/bin/activate"
pip install -r requirements.txt --quiet --no-warn-script-location
echo "✓ 依赖检查完毕"

# 3. 重启服务
echo ""
echo "🔄 [3/4] 重启 Bot 服务..."
sudo systemctl restart "$SERVICE_NAME"
sleep 3

# 4. 验证服务状态
echo ""
echo "🔍 [4/4] 验证服务状态..."
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "✅ $SERVICE_NAME 服务运行正常！"
else
    echo "❌ 服务启动失败，最近10行日志："
    sudo journalctl -u "$SERVICE_NAME" -n 10 --no-pager
    exit 1
fi

echo ""
echo "=============================="
echo "🎉 部署完成！"
echo "=============================="
