"""
lib/native_fs/ — Onyx 文件面板与虚拟路径展示模块

标记语言文件编辑系统（markup_parser / engine / process_markup）已随
标记语言移除而删除（纯 Markdown 直通 + function calling 工具）；
本包保留 panels.py（number_lines 等展示辅助，read_file / memory 工具使用）。

使用入口：
    from lib.native_fs.panels import number_lines
"""

from .panels import PanelManager

# 全局面板管理器（单例，历史兼容）
panel_manager = PanelManager()
