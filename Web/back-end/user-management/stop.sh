#!/bin/bash
echo "🛑 停止用户管理服务..."

if [ -f "logs/app.pid" ]; then
    PID=$(cat logs/app.pid)
    if ps -p $PID > /dev/null; then
        kill $PID
        echo "✅ 服务已停止 (PID: $PID)"
        rm logs/app.pid
    else
        echo "⚠️ 服务进程不存在"
        rm logs/app.pid
    fi
else
    echo "⚠️ PID文件不存在"
fi
