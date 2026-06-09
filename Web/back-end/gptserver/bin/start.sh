#!/bin/bash

cd `dirname $0`
cd ..
ROOT_DIR=`pwd`
JAR=gpt-server.jar
LOGS_DIR=$ROOT_DIR/logs
SERVER_PORT="${WEB_FABGPT_GPTSERVER_PORT:-5107}"
MYSQL_PORT="${WEB_FABGPT_MYSQL_PORT:-4307}"
SPRING_DATASOURCE_URL="${WEB_FABGPT_GPTSERVER_DB_URL:-jdbc:mysql://127.0.0.1:${MYSQL_PORT}/zgpt?&characterEncoding=UTF-8&autoReconnect=true&allowMultiQueries=true&zeroDateTimeBehavior=convertToNull&useSSL=false&serverTimezone=GMT%2B8}"
DEBUG_PORT="${WEB_FABGPT_GPTSERVER_DEBUG_PORT:-5181}"
PROC_PATTERN="${JAR} --server.port=${SERVER_PORT}"

cd $ROOT_DIR

# 检查jar包是否存在
if [ ! -f $ROOT_DIR/$JAR ]; then
    echo "ERROR: $ROOT_DIR/$JAR not exits!"
    exit 1
fi

# 检查进程是否启动
PIDS=`ps -ef | grep "${PROC_PATTERN}" | grep -v grep | awk '{print $2}'`
if [ -n "$PIDS" ]; then
    echo "ERROR: The service already started!"
    echo "PID: $PIDS"
    exit 1
fi

# 创建日志文件夹
if [ ! -d $LOGS_DIR ]; then
   mkdir $LOGS_DIR
fi
STDOUT_FILE=$LOGS_DIR/stdout.log

# 配置启动参数，移除无效的 G1GCRegionSize
JAVA_OPTS=" -server -agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=*:${DEBUG_PORT}  -Xms1g -Xmx1g -verbose:gc -XX:+PrintGCDetails -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=$LOGS_DIR/ -Xlog:gc:$LOGS_DIR/gc-%t.log -XX:+UseG1GC "
JAVA_BIN=""

if [ -n "${JAVA_HOME:-}" ] && [ -x "${JAVA_HOME}/bin/java" ]; then
    JAVA_BIN="${JAVA_HOME}/bin/java"
else
    JAVA_BIN=`command -v java`
fi

if [ -z "$JAVA_BIN" ] || [ ! -x "$JAVA_BIN" ]; then
    echo "ERROR: java executable not found! JAVA_HOME=$JAVA_HOME"
    exit 1
fi

echo -e "Starting the service ..."
echo "nohup $JAVA_BIN $JAVA_OPTS -jar $ROOT_DIR/$JAR --server.port=${SERVER_PORT} --spring.datasource.url=${SPRING_DATASOURCE_URL} > $STDOUT_FILE 2>&1 &"
nohup $JAVA_BIN $JAVA_OPTS -jar $ROOT_DIR/$JAR --server.port="${SERVER_PORT}" --spring.datasource.url="${SPRING_DATASOURCE_URL}" > $STDOUT_FILE 2>&1 &

sleep 3
PIDS=`ps -ef | grep "${PROC_PATTERN}" | grep -v grep | awk '{print $2}'`
if [ -z "$PIDS" ]; then
    echo "ERROR: The service start error! Please check log!"
    if [ ! -z "$3" ]; then
        echo $PIDS > $3
    fi
    exit 1
fi
echo "OK!"
echo "PID: $PIDS"
if [ ! -z "$3" ]; then
  echo $PIDS > $3
fi
echo "STDOUT: $STDOUT_FILE"
