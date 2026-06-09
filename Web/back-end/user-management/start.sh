#!/bin/bash
set -euo pipefail

PORT="${WEB_FABGPT_USER_MANAGER_PORT:-5108}"
MYSQL_PORT="${WEB_FABGPT_MYSQL_PORT:-4307}"
DB_URL="${WEB_FABGPT_USER_MANAGER_DB_URL:-jdbc:mysql://127.0.0.1:${MYSQL_PORT}/zgpt?useSSL=false&serverTimezone=UTC&allowPublicKeyRetrieval=true}"
GPTSERVER_PORT="${WEB_FABGPT_GPTSERVER_PORT:-5107}"
GPTSERVER_LOGIN_URL="${WEB_FABGPT_GPTSERVER_LOGIN_URL:-http://127.0.0.1:${GPTSERVER_PORT}/login}"
JAR_PATH="target/user-management-1.0.0.jar"
LOG_DIR="logs"
LOG_FILE="${LOG_DIR}/app.log"
PID_FILE="${LOG_DIR}/app.pid"
PROC_PATTERN="user-management-1.0.0.jar --server.port=${PORT}"

echo "🚀 启动用户管理服务..."

if [ ! -f "${JAR_PATH}" ]; then
    echo "❌ JAR文件不存在，请先运行 ./build.sh"
    exit 1
fi

mkdir -p "${LOG_DIR}"

if pgrep -f "${PROC_PATTERN}" > /dev/null; then
    echo "⚠️ 服务已在运行"
    echo "📋 端口: ${PORT}"
    echo "📋 日志: ${LOG_FILE}"
    exit 0
fi

if ss -ltn | grep -q ":${PORT} "; then
    echo "❌ 端口 ${PORT} 已被其他进程占用，请先执行 ./stop.sh"
    exit 1
fi

JAVA_BIN=""
if [ -n "${JAVA_HOME:-}" ] && [ -x "${JAVA_HOME}/bin/java" ]; then
    JAVA_BIN="${JAVA_HOME}/bin/java"
else
    JAVA_BIN="$(command -v java || true)"
fi

if [ -z "${JAVA_BIN}" ] || [ ! -x "${JAVA_BIN}" ]; then
    echo "❌ java 可执行文件不存在，请检查 JAVA_HOME"
    exit 1
fi

nohup env WEB_FABGPT_GPTSERVER_LOGIN_URL="${GPTSERVER_LOGIN_URL}" "${JAVA_BIN}" -jar "${JAR_PATH}" --server.port="${PORT}" --spring.datasource.url="${DB_URL}" > "${LOG_FILE}" 2>&1 &
echo $! > "${PID_FILE}"

for _ in $(seq 1 15); do
    if pgrep -f "${PROC_PATTERN}" > /dev/null && ss -ltn | grep -q ":${PORT} "; then
        echo "✅ 服务启动成功！"
        echo "📋 端口: ${PORT}"
        echo "📋 日志: ${LOG_FILE}"
        echo "📋 测试: curl http://localhost:${PORT}/api/users"
        exit 0
    fi
    sleep 1
done

echo "❌ 服务启动失败，请检查日志: ${LOG_FILE}"
tail -n 50 "${LOG_FILE}" || true
exit 1
