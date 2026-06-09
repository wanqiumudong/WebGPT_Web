#!/bin/bash
set -euo pipefail

PORT="${WEB_FABGPT_USER_MANAGER_PORT:-5108}"
PID_FILE="logs/app.pid"
PROC_PATTERN="user-management-1.0.0.jar --server.port=${PORT}"

echo "🛑 停止用户管理服务..."

if [ -f "${PID_FILE}" ]; then
    PID=$(cat "${PID_FILE}")
    if ps -p "${PID}" > /dev/null; then
        kill "${PID}" || true
        sleep 2
        echo "✅ 已发送停止信号 (PID: ${PID})"
    else
        echo "⚠️ PID 文件存在，但进程不存在"
    fi
    rm -f "${PID_FILE}"
fi

if pgrep -f "${PROC_PATTERN}" > /dev/null; then
    pkill -f "${PROC_PATTERN}" || true
    sleep 2
fi

if ss -ltn | grep -q ":${PORT} "; then
    echo "❌ 端口 ${PORT} 仍被占用，请手动排查"
    exit 1
fi

echo "✅ 服务已停止"
