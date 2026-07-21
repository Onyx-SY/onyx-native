# lib/start_banner.py
"""
启动横幅模块 - 使用 rich 库美化 Onyx 启动界面
提供统一的横幅显示函数，支持彩色、表格、面板等丰富样式

使用方法:
    from lib.start_banner import show_start_banner, show_ready_prompt, show_error_banner
    
    show_start_banner(
        version="2.7.0",
        mode="TBS",
        system_type="Termux",
        tools_count=28,
        boot_time="943ms",
        language="chinese"  # 或 "english"
    )
"""

import os
import sys
import functools
from typing import Dict, Any, Optional, Tuple

# 延迟导入 rich，避免循环依赖
_rich_available = None
_console = None
_Panel = None
_Columns = None
_Text = None
_Table = None
_Align = None
_box = None

# 缓存终端宽度，避免重复调用
_terminal_width_cache = None
_last_width_check = 0
_WIDTH_CACHE_TTL = 1.0  # 缓存1秒


@functools.lru_cache(maxsize=8)
def _get_terminal_width() -> int:
    """
    获取终端宽度，带缓存和性能优化
    
    Returns:
        终端宽度（40-120之间）
    """
    global _terminal_width_cache, _last_width_check
    
    import time
    now = time.time()
    
    # 使用缓存，避免频繁调用 shutil（性能优化）
    if _terminal_width_cache is not None and (now - _last_width_check) < _WIDTH_CACHE_TTL:
        return _terminal_width_cache
    
    try:
        import shutil
        width = shutil.get_terminal_size().columns
        # 限制最小宽度，不设上限（让宽终端也能正确居中）
        result = max(40, width)
    except Exception:
        result = 80
    
    _terminal_width_cache = result
    _last_width_check = now
    return result


def _invalidate_width_cache():
    """强制刷新终端宽度缓存（在终端大小变化时调用）"""
    global _terminal_width_cache, _last_width_check
    _terminal_width_cache = None
    _last_width_check = 0


def _init_rich():
    """延迟初始化 rich 模块（单次初始化）"""
    global _rich_available, _console, _Panel, _Columns, _Text, _Table, _Align, _box
    
    if _rich_available is not None:
        return _rich_available
    
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.columns import Columns
        from rich.text import Text
        from rich.table import Table
        from rich.align import Align
        from rich import box
        
        # 使用轻量级配置，提高性能
        _console = Console(force_terminal=True, legacy_windows=False)
        _Panel = Panel
        _Columns = Columns
        _Text = Text
        _Table = Table
        _Align = Align
        _box = box
        _rich_available = True
    except ImportError:
        _rich_available = False
    
    return _rich_available


def get_rich_console():
    """获取 rich console 实例（供外部使用）"""
    _init_rich()
    return _console


def _get_fallback_banner() -> str:
    """获取纯文本后备横幅（当 rich 不可用时）"""
    return '''  ____    _   _  __     __ __   __ 
 / __ \\  | \\ | | \\ \\   / / \\ \\ / / 
| |  | | |  \\| |  \\ \\_/ /   \\ V /  
| |  | | | . ` |   \\   /     > <   
| |__| | | |\\  |    | |     / . \\  
 \\____/  |_| \\_|    |_|    /_/ \\_\\ 
                                           '''


def _get_lang_strings(language: str) -> Dict[str, str]:
    """获取多语言字符串（缓存友好）"""
    strings = {
        "chinese": {
            "version": "版本",
            "mode": "模式",
            "system": "系统",
            "tools_count": "工具数量",
            "boot_time": "启动耗时",
            "system_info": "系统信息",
            "shell_ready": "Shell 就绪",
            "shell_prompt": "输入命令开始操作（输入 exit 退出）",
            "error_title": "启动错误"
        },
        "english": {
            "version": "Version",
            "mode": "Mode",
            "system": "System",
            "tools_count": "Tools",
            "boot_time": "Boot Time",
            "system_info": "System Info",
            "shell_ready": "Shell Ready",
            "shell_prompt": "Enter command (type exit to quit)",
            "error_title": "Startup Error"
        }
    }
    return strings.get(language, strings["chinese"])


def _calculate_center_padding(text_lines: list, terminal_width: int) -> int:
    """
    计算文本居中的左内边距
    
    Args:
        text_lines: 文本行列表
        terminal_width: 终端宽度
    
    Returns:
        左边距字符数
    """
    max_line_len = max(len(line) for line in text_lines) if text_lines else 0
    return max(0, (terminal_width - max_line_len) // 2)


def _build_ascii_art(terminal_width: int) -> Optional['_Text']:
    """
    构建带颜色的 ASCII 艺术。

    统一使用一版图案，通过 _Align.center 在任意宽度终端居中。
    """
    ascii_lines = [
        "  ____    _   _  __     __ __   __",
        " / __ \\  | \\ | | \\ \\   / / \\ \\ / /",
        "| |  | | |  \\| |  \\ \\_/ /   \\ V /",
        "| |  | | | . ` |   \\   /     > <",
        "| |__| | | |\\  |    | |     / . \\",
        " \\____/  |_| \\_|    |_|    /_/ \\_\\"
    ]

    colors = ["bright_cyan", "cyan", "bright_blue", "blue", "cyan", "bright_cyan"]

    text_parts = []
    for i, line in enumerate(ascii_lines):
        text_parts.append((line.rstrip(), colors[i % len(colors)]))

    # 一次性构建 Text 对象
    ascii_text = _Text()
    for i, (line, color) in enumerate(text_parts):
        ascii_text.append(line, style=color)
        if i < len(text_parts) - 1:
            ascii_text.append("\n")

    return ascii_text


def _get_boot_bar(boot_time: str, terminal_width: int) -> Tuple[str, int]:
    """
    获取启动进度条和耗时
    
    Args:
        boot_time: 启动时间字符串
        terminal_width: 终端宽度
    
    Returns:
        (显示字符串, 毫秒数)
    """
    boot_ms = 0
    if "ms" in boot_time:
        try:
            boot_ms = int(boot_time.replace("ms", "").strip())
        except ValueError:
            boot_ms = 100
    else:
        boot_ms = 100
    
    # 动态计算进度条长度（基于终端宽度）
    bar_max_len = min(20, max(5, (terminal_width - 60) // 3))
    bar_len = min(bar_max_len, max(1, boot_ms // 80))
    boot_bar = "█" * bar_len + "░" * (bar_max_len - bar_len)
    
    return f"[yellow]{boot_time}[/yellow]  [dim]{boot_bar}[/dim]", boot_ms


def show_start_banner(
    version: str,
    mode: str,
    system_type: str,
    tools_count: int,
    boot_time: str,
    language: str = "chinese",
    ascii_art: Optional[str] = None
) -> None:
    """
    显示 Onyx 启动横幅（使用 rich 美化）
    
    Args:
        version: 程序版本号
        mode: 运行模式 (OS/TBS)
        system_type: 系统类型 (Windows/Linux/Termux 等)
        tools_count: 工具数量
        boot_time: 启动耗时
        language: 语言 (chinese/english)
        ascii_art: 自定义 ASCII 艺术（可选）
    """
    lang = _get_lang_strings(language)
    
    if not _init_rich():
        # Rich 不可用，使用传统的 colorama 显示
        from lib.terminal.colors import Fore, Style
        ascii_art_str = ascii_art if ascii_art else _get_fallback_banner()
        art_lines = ascii_art_str.split('\n')
        art_width = max(len(l) for l in art_lines) if art_lines else 40
        
        terminal_width = _get_terminal_width()
        art_padding = " " * max(0, (terminal_width - art_width) // 2)
        
        print()
        for line in art_lines:
            print(Fore.CYAN + art_padding + line + Style.RESET_ALL)
        print()
        
        # 显示信息表格
        padding = " " * max(0, (terminal_width - 40) // 2)
        
        print(f"{padding}{lang['version']}: {Fore.GREEN}{version}{Style.RESET_ALL}")
        mode_color = Fore.BLUE if mode == "TBS" else Fore.MAGENTA
        print(f"{padding}{lang['mode']}: {mode_color}{mode}{Style.RESET_ALL}")
        sys_color = Fore.GREEN if "Termux" in system_type else Fore.YELLOW
        print(f"{padding}{lang['system']}: {sys_color}{system_type}{Style.RESET_ALL}")
        print(f"{padding}{lang['tools_count']}: {Fore.CYAN}{tools_count}{Style.RESET_ALL}")
        print(f"{padding}{lang['boot_time']}: {Fore.YELLOW}{boot_time}{Style.RESET_ALL}")
        print()
        return
    
    terminal_width = _get_terminal_width()
    
    # 1. 构建 ASCII 艺术
    ascii_text = _build_ascii_art(terminal_width)
    
    # 2. 创建 ASCII 艺术面板
    ascii_panel = _Panel(
        ascii_text,
        border_style="cyan",
        box=_box.ROUNDED,
        padding=(1, 3)
    )
    
    # 3. 创建信息表格
    info_table = _Table(
        box=_box.MINIMAL,
        show_header=False,
        border_style="dim",
        padding=(0, 3)
    )
    
    info_table.add_column("Label", style="bold yellow", width=12, justify="right")
    info_table.add_column("Value", style="bright_white", width=25)
    
    # 版本信息
    version_display = f"[bold green]{version}[/bold green]"
    info_table.add_row(f"{lang['version']}:", version_display)
    
    # 模式信息
    mode_color = "bold blue" if mode == "TBS" else "bold magenta"
    info_table.add_row(f"{lang['mode']}:", f"[{mode_color}]{mode}[/{mode_color}]")
    
    # 系统类型
    if "Termux" in system_type:
        sys_color = "bold green"
    elif "Windows" in system_type:
        sys_color = "bright_blue"
    else:
        sys_color = "yellow"
    info_table.add_row(f"{lang['system']}:", f"[{sys_color}]{system_type}[/{sys_color}]")
    
    # 工具数量
    info_table.add_row(f"{lang['tools_count']}:", f"[bold cyan]{tools_count}[/bold cyan]")
    
    # 启动耗时（带动态进度条）
    boot_display, _ = _get_boot_bar(boot_time, terminal_width)
    info_table.add_row(f"{lang['boot_time']}:", boot_display)
    
    # 4. 创建信息面板（宽度随终端自适应，最多 100 列避免拉伸过度）
    # PC 宽屏时面板填满终端，手机窄屏时保持紧凑
    panel_width = max(min(terminal_width - 6, 80), 50)
    info_panel = _Panel(
        _Align.center(info_table),
        border_style="green",
        box=_box.ROUNDED,
        title=f"[bold green]{lang['system_info']}[/bold green]",
        title_align="center",
        width=panel_width,
        padding=(1, 2),
    )
    
    # 5. 输出（居中 + 批量输出减少刷新）
    # 5. 输出（居中渲染 + 即时刷新）
    _console.print()
    _console.print(_Align.center(ascii_panel))
    _console.print()
    _console.print(_Align.center(info_panel))
    _console.print()


def show_ready_prompt(language: str = "chinese") -> None:
    """显示 Shell 就绪提示（美化版）"""
    lang = _get_lang_strings(language)
    
    if not _init_rich():
        from lib.terminal.colors import Fore, Style
        print(Fore.GREEN + f"\n[{lang['shell_ready']}] {lang['shell_prompt']}" + Style.RESET_ALL)
        print()
        return
    
    terminal_width = _get_terminal_width()
    panel_width = max(min(terminal_width - 4, 130), 50)
    
    # 使用 Text 对象构建内容
    content = _Text()
    content.append("✓ ", style="bold green")
    content.append(lang['shell_ready'], style="bold bright_white")
    content.append("\n    ", style="dim")
    content.append(lang['shell_prompt'], style="dim italic")
    
    ready_panel = _Panel(
        _Align.center(content),
        border_style="green",
        box=_box.ROUNDED,
        padding=(0, 2),
        width=panel_width
    )
    
    _console.print()
    _console.print(_Align.center(ready_panel))
    _console.print()


def show_error_banner(error_msg: str, title: Optional[str] = None, language: str = "chinese") -> None:
    """显示错误横幅（美化版）"""
    lang = _get_lang_strings(language)
    error_title = title if title else lang['error_title']
    
    if not _init_rich():
        from lib.terminal.colors import Fore, Style
        print(Fore.RED + f"\n{'='*50}")
        print(f"[{error_title}] {error_msg}")
        print(f"{'='*50}" + Style.RESET_ALL)
        return
    
    terminal_width = _get_terminal_width()
    panel_width = max(min(terminal_width - 4, 120), 50)
    
    content = _Text()
    content.append("❌ ", style="bold red")
    content.append(error_title, style="bold red")
    content.append("\n\n")
    content.append(error_msg, style="red")
    
    error_panel = _Panel(
        _Align.center(content),
        border_style="red",
        box=_box.ROUNDED,
        padding=(1, 3),
        width=panel_width
    )
    _console.print()
    _console.print(error_panel)
    _console.print()


def show_info_card(info_dict: Dict[str, Any], title: str = "信息", language: str = "chinese") -> None:
    """
    显示通用信息卡片（美化版）
    
    Args:
        info_dict: 键值对字典
        title: 卡片标题
        language: 语言
    """
    if not info_dict:
        return
    
    if not _init_rich():
        from lib.terminal.colors import Fore, Style
        print(Fore.CYAN + f"\n--- {title} ---" + Style.RESET_ALL)
        for key, value in info_dict.items():
            print(f"  {key}: {value}")
        return
    
    terminal_width = _get_terminal_width()
    panel_width = max(min(terminal_width - 4, 130), 50)
    
    table = _Table(
        box=_box.ROUNDED,
        title=f"📋 {title}",
        title_style="bold cyan",
        title_justify="center",
        border_style="cyan",
        padding=(0, 2),
        width=panel_width
    )
    table.add_column("项目", style="bold yellow", width=15, justify="right")
    table.add_column("值", style="bright_white", width=40, justify="left")
    
    for key, value in info_dict.items():
        table.add_row(str(key), str(value))
    
    _console.print()
    _console.print(table)
    _console.print()


def show_success_banner(message: str, title: Optional[str] = None, language: str = "chinese") -> None:
    """
    显示成功横幅（美化版）
    
    Args:
        message: 成功消息
        title: 标题（可选）
        language: 语言
    """
    if not _init_rich():
        from lib.terminal.colors import Fore, Style
        print(Fore.GREEN + f"\n✅ {message}" + Style.RESET_ALL)
        return
    
    terminal_width = _get_terminal_width()
    panel_width = max(min(terminal_width - 4, 120), 50)
    
    display_title = title if title else "成功"
    
    content = _Text()
    content.append("✓ ", style="bold green")
    content.append(display_title, style="bold green")
    content.append("\n\n")
    content.append(message, style="green")
    
    success_panel = _Panel(
        _Align.center(content),
        border_style="green",
        box=_box.ROUNDED,
        padding=(1, 3),
        width=panel_width
    )
    _console.print()
    _console.print(success_panel)
    _console.print()


def show_warning_banner(message: str, title: Optional[str] = None, language: str = "chinese") -> None:
    """
    显示警告横幅（美化版）
    
    Args:
        message: 警告消息
        title: 标题（可选）
        language: 语言
    """
    if not _init_rich():
        from lib.terminal.colors import Fore, Style
        print(Fore.YELLOW + f"\n⚠️ {message}" + Style.RESET_ALL)
        return
    
    terminal_width = _get_terminal_width()
    panel_width = max(min(terminal_width - 4, 120), 50)
    
    display_title = title if title else "警告"
    
    content = _Text()
    content.append("⚠ ", style="bold yellow")
    content.append(display_title, style="bold yellow")
    content.append("\n\n")
    content.append(message, style="yellow")
    
    warning_panel = _Panel(
        _Align.center(content),
        border_style="yellow",
        box=_box.ROUNDED,
        padding=(1, 3),
        width=panel_width
    )
    _console.print()
    _console.print(warning_panel)
    _console.print()


# ==================== 性能优化辅助函数 ====================

def batch_print(*items):
    """批量打印，减少 I/O 次数"""
    if _console:
        _console.print(*items)
    else:
        for item in items:
            print(item, end=' ')
        print()


def set_terminal_width_cache_ttl(ttl_seconds: float):
    """
    设置终端宽度缓存的 TTL
    
    Args:
        ttl_seconds: 缓存有效期（秒）
    """
    global _WIDTH_CACHE_TTL
    _WIDTH_CACHE_TTL = max(0.1, min(ttl_seconds, 5.0))  # 限制在 0.1-5 秒


# ==================== 单元测试 ====================

def _run_tests():
    """运行模块单元测试"""
    import time
    from io import StringIO
    from contextlib import redirect_stdout
    
    print("\n" + "=" * 60)
    print("lib/start_banner 单元测试")
    print("=" * 60)
    
    tests_passed = 0
    tests_failed = 0
    
    def assert_test(condition: bool, test_name: str, error_msg: str = ""):
        nonlocal tests_passed, tests_failed
        if condition:
            print(f"  ✅ {test_name}")
            tests_passed += 1
        else:
            print(f"  ❌ {test_name}: {error_msg}" if error_msg else f"  ❌ {test_name}")
            tests_failed += 1
    
    # 测试 1: 初始化
    print("\n📋 测试 1: 模块初始化")
    try:
        _init_rich()
        assert_test(True, "rich 初始化完成")
    except Exception as e:
        assert_test(False, "rich 初始化", str(e))
    
    # 测试 2: 终端宽度获取（带缓存）
    print("\n📋 测试 2: 终端宽度获取（性能测试）")
    try:
        start = time.perf_counter()
        width1 = _get_terminal_width()
        time1 = time.perf_counter() - start
        
        start = time.perf_counter()
        width2 = _get_terminal_width()
        time2 = time.perf_counter() - start
        
        assert_test(40 <= width1 <= 120, f"宽度 {width1} 在合理范围内")
        assert_test(time2 < time1 * 0.1 or time2 < 0.001, f"缓存生效（首次:{time1:.5f}s 二次:{time2:.5f}s）")
    except Exception as e:
        assert_test(False, "终端宽度获取", str(e))
    
    # 测试 3: 居中计算
    print("\n📋 测试 3: 居中计算")
    try:
        padding = _calculate_center_padding(["abc", "defg"], 100)
        assert_test(padding == 48, f"居中计算正确（预期48，实际{padding}）")
        
        padding = _calculate_center_padding(["abc"], 10)
        assert_test(padding == 0, f"超宽文本处理正确（预期0，实际{padding}）")
    except Exception as e:
        assert_test(False, "居中计算", str(e))
    
    # 测试 4: 启动横幅
    print("\n📋 测试 4: 启动横幅显示")
    try:
        show_start_banner(
            version="2.7.0.a1",
            mode="TBS",
            system_type="Termux",
            tools_count=28,
            boot_time="579ms",
            language="chinese"
        )
        assert_test(True, "中文横幅调用成功")
    except Exception as e:
        assert_test(False, "启动横幅显示", str(e))
    
    # 测试 5: 就绪提示
    print("\n📋 测试 5: 就绪提示显示")
    try:
        show_ready_prompt("chinese")
        assert_test(True, "就绪提示调用成功")
    except Exception as e:
        assert_test(False, "就绪提示显示", str(e))
    
    # 测试 6: 错误横幅
    print("\n📋 测试 6: 错误横幅显示")
    try:
        show_error_banner("测试错误消息", None, "chinese")
        assert_test(True, "错误横幅调用成功")
    except Exception as e:
        assert_test(False, "错误横幅显示", str(e))
    
    # 测试总结
    print("\n" + "=" * 60)
    print(f"测试结果: {tests_passed} 通过, {tests_failed} 失败")
    print("=" * 60)
    
    if tests_failed > 0:
        print("\n⚠️ 部分测试失败，请检查 rich 库是否安装:")
        print("   pip install rich")
    else:
        print("\n✅ 所有测试通过！")
    
    return tests_failed == 0


# 模块自测入口
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("演示 Onyx 启动横幅效果")
    print("=" * 60 + "\n")
    
    show_start_banner(
        version="2.7.0.a1",
        mode="TBS",
        system_type="Termux",
        tools_count=28,
        boot_time="579ms",
        language="chinese"
    )
    
    show_ready_prompt("chinese")
    
    print("\n" + "=" * 60)
    print("额外功能演示")
    print("=" * 60 + "\n")
    
    show_success_banner("Onyx 已成功启动！", "启动成功", "chinese")
    show_warning_banner("请定期更新工具以获取最新功能", "温馨提示", "chinese")
    
    # 性能测试
    print("\n" + "=" * 60)
    print("性能测试")
    print("=" * 60)
    
    import time
    iterations = 100
    start = time.perf_counter()
    for _ in range(iterations):
        _get_terminal_width()
    elapsed = time.perf_counter() - start
    print(f"  📊 终端宽度查询 {iterations} 次: {elapsed:.4f} 秒 (平均 {elapsed/iterations*1000:.2f}ms)")
    
    _run_tests()