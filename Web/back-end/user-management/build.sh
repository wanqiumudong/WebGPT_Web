#!/bin/bash
echo "🔨 开始编译用户管理服务..."
mvn clean package -DskipTests
if [ $? -eq 0 ]; then
    echo "✅ 编译成功！"
    echo "📦 JAR文件: target/user-management-1.0.0.jar"
else
    echo "❌ 编译失败，请检查错误信息"
    exit 1
fi
