#!/bin/bash
# Search Aggregator - 虚拟环境初始化与进入脚本
# 用法: source venv.sh          # 初始化并进入虚拟环境
#       source venv.sh enter     # 仅进入已有虚拟环境
#       source venv.sh init      # 仅初始化(不进入)

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

init_venv() {
    echo -e "${YELLOW}[1/3] 创建虚拟环境...${NC}"
    if [ -d "$VENV_DIR" ]; then
        echo -e "${GREEN}  → 虚拟环境已存在: $VENV_DIR${NC}"
    else
        python3 -m venv "$VENV_DIR"
        echo -e "${GREEN}  → 虚拟环境创建成功${NC}"
    fi

    echo -e "${YELLOW}[2/3] 激活虚拟环境...${NC}"
    source "$VENV_DIR/bin/activate"

    echo -e "${YELLOW}[3/3] 安装依赖...${NC}"
    pip install -q --upgrade pip
    pip install -q -r "$PROJECT_DIR/requirements.txt"
    echo -e "${GREEN}  → 依赖安装完成${NC}"

    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  Search Aggregator 虚拟环境就绪!       ${NC}"
    echo -e "${GREEN}  Python: $(which python)${NC}"
    echo -e "${GREEN}  项目目录: $PROJECT_DIR${NC}"
    echo -e "${GREEN}  启动: python main.py${NC}"
    echo -e "${GREEN}========================================${NC}"
}

enter_venv() {
    if [ -d "$VENV_DIR" ]; then
        source "$VENV_DIR/bin/activate"
        echo -e "${GREEN}已进入虚拟环境: $VENV_DIR${NC}"
        echo -e "Python: $(which python)"
    else
        echo -e "${RED}虚拟环境不存在，正在初始化...${NC}"
        init_venv
    fi
}

# 主逻辑
case "${1:-enter}" in
    init)
        init_venv
        ;;
    enter|*)
        enter_venv
        ;;
esac
