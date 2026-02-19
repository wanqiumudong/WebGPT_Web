#!/bin/bash
echo "🚀 启动用户管理服务..."

# 检查JAR文件
if [ ! -f "target/user-management-1.0.0.jar" ]; then
    echo "❌ JAR文件不存在，请先运行 ./build.sh"
    exit 1
fi

# 启动服务
nohup java -jar target/user-management-1.0.0.jar > logs/app.log 2>&1 &
echo $! > logs/app.pid

echo "✅ 服务启动成功！"
echo "📋 端口: 5203"
echo "📋 日志: logs/app.log"
echo "📋 测试: curl http://localhost:5203/api/users"
