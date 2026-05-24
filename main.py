#!/usr/bin/env python3
"""Search Aggregator - 主入口"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_root = str(Path(__file__).resolve().parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

# 加载环境变量 (.env 文件)
from dotenv import load_dotenv
load_dotenv(Path(_root) / '.env')

# 启动 FastAPI
import uvicorn

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--reload', action='store_true', help='开发模式启用文件监控重载')
    args = parser.parse_args()
    uvicorn.run(
        'web.app:app',
        host='0.0.0.0',
        port=8830,
        reload=args.reload,
    )
