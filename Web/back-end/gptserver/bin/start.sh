#!/bin/bash

cd `dirname $0`
cd ..
ROOT_DIR=`pwd`
JAR=gpt-server.jar
LOGS_DIR=$ROOT_DIR/logs

cd $ROOT_DIR

# 检查jar包是否存在
if [ ! -f $ROOT_DIR/$JAR ]; then
    echo "ERROR: $ROOT_DIR/$JAR not exits!"
    exit 1
fi

# 检查进程是否启动
PIDS=`ps -ef | grep "${JAR}" | grep -v grep | awk '{print $2}'`
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
JAVA_OPTS=" -server -agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=*:8081  -Xms1g -Xmx1g -verbose:gc -XX:+PrintGCDetails -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=$LOGS_DIR/ -Xlog:gc:$LOGS_DIR/gc-%t.log -XX:+UseG1GC "

echo -e "Starting the service ..."
echo "nohup $JAVA_HOME/bin/java $JAVA_OPTS -jar $ROOT_DIR/$JAR > $STDOUT_FILE 2>&1 &"
nohup $JAVA_HOME/bin/java $JAVA_OPTS -jar $ROOT_DIR/$JAR > $STDOUT_FILE 2>&1 &

sleep 3
PIDS=`ps -ef | grep "${JAR}" | grep -v grep | awk '{print $2}'`
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
