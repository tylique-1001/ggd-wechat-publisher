#!/bin/bash
# ============================================================
# 广大大公众号 launchd 定时推送包装脚本
# 由 ~/Library/LaunchAgents/com.ggd.auto-publish.plist 调用
# 每天上午 8:30 自动查找当天的 Markdown 文章并推送
# ============================================================

set -e

LOG_FILE="/tmp/ggd_auto_publish_$(date +%Y%m%d_%H%M%S).log"
exec > "$LOG_FILE" 2>&1

echo "=== 广大大公众号自动推送 ==="
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "用户: $(whoami)"

# 加载环境变量
if [ -f "$HOME/.zshrc" ]; then
    source "$HOME/.zshrc"
fi

# 校验环境变量
if [ -z "$WECHAT_APPID_GGD" ] || [ -z "$WECHAT_SECRET_GGD" ]; then
    echo "❌ 环境变量未配置！请检查 ~/.zshrc"
    exit 1
fi

echo "✅ 环境变量已加载"
echo "   AppID: ${WECHAT_APPID_GGD:0:10}..."

# 查找今天的文章
DATE_STR=$(date +%m%d)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_DIR="$(dirname "$SCRIPT_DIR")"

# 优先查找 outputs/ 目录
MD_FILE="$WORKSPACE_DIR/outputs/wechat_draft_${DATE_STR}.md"
if [ ! -f "$MD_FILE" ]; then
    echo "❌ 找不到今天的文章: $MD_FILE"
    echo "   请确保 WorkBuddy 自动化已生成 outputs/wechat_draft_${DATE_STR}.md"
    exit 1
fi

echo "📖 文章文件: $MD_FILE"

# 查找封面图
COVER_FILE="/tmp/wechat_cover_${DATE_STR}.png"
if [ ! -f "$COVER_FILE" ]; then
    # 找最新的封面
    COVER_FILE=$(ls -t /tmp/wechat_cover_*.png 2>/dev/null | head -1)
fi

COVER_ARG=""
if [ -n "$COVER_FILE" ] && [ -f "$COVER_FILE" ]; then
    COVER_ARG="--cover $COVER_FILE"
    echo "📸 封面图: $COVER_FILE"
else
    echo "⚠️ 未找到封面图，尝试无封面推送（可能失败）"
fi

# 执行推送
PYTHON_PATH="/usr/bin/python3"
SCRIPT="$SCRIPT_DIR/auto_publish.py"

echo ""
echo "🚀 开始推送..."
$PYTHON_PATH "$SCRIPT" --file "$MD_FILE" $COVER_ARG

EXIT_CODE=$?
if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "✅ 推送成功！"
else
    echo ""
    echo "❌ 推送失败，退出码: $EXIT_CODE"
fi

# 清理旧日志（保留最近7天）
find /tmp -name "ggd_auto_publish_*.log" -mtime +7 -delete 2>/dev/null

exit $EXIT_CODE
