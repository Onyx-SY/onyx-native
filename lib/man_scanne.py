#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
命令扫描器封装模块 - 提供后台异步扫描接口
"""

import os
import sys
import threading
from pathlib import Path

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 导入 man.py 中的扫描器
from man import AsyncManScanner, get_scanner, start_background_scan, incremental_update

__all__ = ['AsyncManScanner', 'get_scanner', 'start_background_scan', 'incremental_update']