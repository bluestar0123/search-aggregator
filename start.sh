#!/bin/bash
# Search Aggregator - 快速启动脚本
# 用法: ./start.sh          # 启动服务
#       ./start.sh stop     # 停止服务
#       ./start.sh status   # 查看状态

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PID_FILE="$PROJECT_DIR/data/search.pid"
LOG_DIR="$PROJECT_DIR/data/logs"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# 确保目录存在
mkdir -p "$LOG_DIR"

start() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo -e "${YELLOW}服务已在运行 (PID: $(cat "$PID_FILE"))${NC}"
        return 0
    fi

    if [ ! -d "$VENV_DIR" ]; then
        echo -e "${YELLOW}虚拟环境不存在，正在初始化...${NC}"
        source "$PROJECT_DIR/venv.sh" init
    fi

    echo -e "${GREEN}启动 Search Aggregator...${NC}"
    cd "$PROJECT_DIR"
    nohup "$VENV_DIR/bin/python" main.py \
        > "$LOG_DIR/server.log" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 2

    if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo -e "${GREEN}启动成功 (PID: $(cat "$PID_FILE"))${NC}"
        echo -e "管理界面: http://localhost:8830"
        echo -e "日志文件: $LOG_DIR/server.log"
    else
        echo -e "${RED}启动失败，请查看日志: $LOG_DIR/server.log${NC}"
        rm -f "$PID_FILE"
        return 1
    fi
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo -e "${YELLOW}停止服务 (PID: $PID)...${NC}"
            kill "$PID"
            sleep 1
            kill -0 "$PID" 2>/dev/null && kill -9 "$PID"
            echo -e "${GREEN}已停止${NC}"
        else
            echo -e "${YELLOW}进程已不存在${NC}"
        fi
        rm -f "$PID_FILE"
    else
        echo -e "${YELLOW}服务未运行${NC}"
    fi
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo -e "${GREEN}服务运行中 (PID: $(cat "$PID_FILE"))${NC}"
        curl -s http://localhost:8830/api/monitor/health 2>/dev/null && echo ""
    else
        echo -e "${RED}服务未运行${NC}"
    fi
}

case "${1:-start}" in
    start) start ;;
    stop)  stop ;;
    status) status ;;
    restart) stop; sleep 1; start ;;
    *) echo "用法: $0 {start|stop|status|restart}" ;;
esac
