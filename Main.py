#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极致优化版环境检查器 - 永久缓存支持秒开
支持登录模式：-l 参数用于在bash/zsh中模拟登录shell环境
"""
import os
import sys
import subprocess
import json
import shutil
import signal
import argparse
import re
from typing import List, Dict, Optional, Tuple, Any
from time import sleep
import time
import datetime
import platform
import concurrent.futures
from functools import lru_cache, wraps
import hashlib
import pickle
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from collections import defaultdict

# ========== 耗时统计装饰器 ==========
_MAX_TIMING_KEYS = 200  # 全局 key 数量上限，防止 _timings 字典无限膨胀导致内存泄漏

class PerformanceTracker:
    """全局性能追踪器"""
    _instance = None
    _timings = defaultdict(list)
    _timing_keys_order: List[str] = []  # 记录 key 插入顺序，用于淘汰最旧的 key
    _current_phase = None
    _phase_start = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._timings = defaultdict(list)
            cls._instance._timing_keys_order = []
            cls._instance._current_phase = None
            cls._instance._phase_start = None
        return cls._instance
    
    def start_phase(self, phase_name: str):
        """开始一个阶段"""
        self._current_phase = phase_name
        self._phase_start = time.perf_counter()
        return self._phase_start
    
    def end_phase(self, phase_name: str = None):
        """结束当前阶段"""
        if self._phase_start is None:
            return 0
        elapsed = (time.perf_counter() - self._phase_start) * 1000  # 转换为毫秒
        name = phase_name or self._current_phase
        if name:
            self._timings[name].append(elapsed)
            if len(self._timings[name]) > 100:
                self._timings[name].pop(0)
            # 控制 key 总量，防止内存泄漏
            self._prune_timing_keys(name)
        self._current_phase = None
        self._phase_start = None
        return elapsed
    
    def add_timing(self, name: str, elapsed_ms: float):
        """添加一个耗时记录（每 key 最多保留 100 条，防止内存泄漏）"""
        self._timings[name].append(elapsed_ms)
        if len(self._timings[name]) > 100:
            self._timings[name].pop(0)
        # 控制 key 总量，防止内存泄漏
        self._prune_timing_keys(name)
    
    def _prune_timing_keys(self, name: str):
        """当 key 数量超限时，淘汰最旧的 key（FIFO）"""
        if name not in self._timing_keys_order:
            self._timing_keys_order.append(name)
        while len(self._timing_keys_order) > _MAX_TIMING_KEYS:
            oldest = self._timing_keys_order.pop(0)
            if oldest in self._timings:
                del self._timings[oldest]
    
    def get_summary(self) -> Dict[str, Dict[str, float]]:
        """获取耗时汇总"""
        summary = {}
        for name, timings in self._timings.items():
            if timings:
                summary[name] = {
                    'count': len(timings),
                    'total_ms': sum(timings),
                    'avg_ms': sum(timings) / len(timings),
                    'min_ms': min(timings),
                    'max_ms': max(timings),
                    'last_ms': timings[-1] if timings else 0
                }
        return summary
    
    def print_summary(self):
        """打印耗时汇总"""
        summary = self.get_summary()
        if not summary:
            return
        
        print("\n" + "=" * 80)
        print("📊 性能耗时汇总报告")
        print("=" * 80)
        
        # 按总耗时排序
        sorted_items = sorted(summary.items(), key=lambda x: x[1]['total_ms'], reverse=True)
        
        total_time = 0
        for name, stats in sorted_items:
            total_time += stats['total_ms']
            print(f"\n  ⏱️  {name}:")
            print(f"      总耗时: {stats['total_ms']:>8.2f} ms")
            if stats['count'] > 1:
                print(f"      调用次数: {stats['count']}")
                print(f"      平均耗时: {stats['avg_ms']:>8.2f} ms")
                print(f"      最小耗时: {stats['min_ms']:>8.2f} ms")
                print(f"      最大耗时: {stats['max_ms']:>8.2f} ms")
            print(f"      最近一次: {stats['last_ms']:>8.2f} ms")
        
        print("\n" + "-" * 80)
        print(f"  📈 总计耗时: {total_time:>8.2f} ms = {total_time/1000:.3f} s")
        print("=" * 80 + "\n")
    
    def reset(self):
        """重置所有计时"""
        self._timings.clear()
        self._timing_keys_order.clear()
        self._current_phase = None
        self._phase_start = None


def timer(phase_name: str = None):
    """耗时统计装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            tracker = PerformanceTracker()
            name = phase_name or func.__name__
            tracker.start_phase(name)
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                elapsed = tracker.end_phase(name)
                # 同时写入日志
                log_print(f"[性能] {name} 耗时: {elapsed:.3f} ms")
        return wrapper
    return decorator


class TimeIt:
    """上下文管理器用于代码块耗时统计"""
    def __init__(self, name: str, log_to_file: bool = True):
        self.name = name
        self.start_time = None
        self.end_time = None
        self.log_to_file = log_to_file
    
    def __enter__(self):
        self.start_time = time.perf_counter()
        return self
    
    def __exit__(self, *args):
        self.end_time = time.perf_counter()
        elapsed_ms = (self.end_time - self.start_time) * 1000
        tracker = PerformanceTracker()
        tracker.add_timing(self.name, elapsed_ms)
        if self.log_to_file:
            log_print(f"[性能] {self.name} 耗时: {elapsed_ms:.3f} ms")
    
    @property
    def elapsed_ms(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time) * 1000
        return 0


MAIN_FILE_DIR = os.path.dirname(os.path.abspath(__file__))
# 不再强制切换目录，使用 __file__ 相对路径
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# 获取当前登录用户名
try:
    USER = os.getlogin()
except:
    USER = os.environ.get("USER", os.environ.get("USERNAME", "default"))

# ========== 日志相关配置 ==========
LOG_BASE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "onyx", "logs", USER)
os.makedirs(LOG_BASE_DIR, exist_ok=True)

# 永久缓存文件路径
PERMANENT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "onyx")
PERMANENT_CACHE_FILE = os.path.join(PERMANENT_CACHE_DIR, "Main_init_env.json")
os.makedirs(PERMANENT_CACHE_DIR, exist_ok=True)

# 极速启动标记文件
ULTRA_FAST_FLAG_FILE = os.path.join(PERMANENT_CACHE_DIR, "ultra_fast.flag")
# Termux C文件复制标记
TERMUX_C_COPIED_FLAG = os.path.join(PERMANENT_CACHE_DIR, "termux_c_copied.flag")
# 性能日志文件
PERFORMANCE_LOG_FILE = os.path.join(PERMANENT_CACHE_DIR, "performance_stats.json")
# First-run forced check flag filename (path resolved dynamically after HOME is set)
_FIRST_RUN_FLAG_FILENAME = "first_run_check_done.flag"


def save_performance_stats():
    """保存性能统计到文件"""
    tracker = PerformanceTracker()
    summary = tracker.get_summary()
    if summary:
        try:
            stats_data = {
                'timestamp': time.time(),
                'summary': summary,
                'total_time_ms': sum(s['total_ms'] for s in summary.values())
            }
            with open(PERFORMANCE_LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump(stats_data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


def get_log_file_path() -> str:
    """生成第n次启动的日志文件路径"""
    log_files = []
    for f in os.listdir(LOG_BASE_DIR):
        if f.endswith(".log"):
            num_part = f.replace(".log", "")
            if num_part.isdigit():
                log_files.append(f)
    launch_count = len(log_files) + 1
    return os.path.join(LOG_BASE_DIR, f"{launch_count}.log")

LOG_FILE = get_log_file_path()

def log_print(content: str, is_error: bool = False, **kwargs):
    """写入日志，仅is_error=True时输出到控制台"""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {content}\n")
    if is_error:
        print(content, file=sys.stderr, **kwargs)


# ========== 依赖配置 ==========
REQUIRED_DEPENDENCIES: Dict[str, List[str]] = {
    "must_install_libs": ["tqdm", "colorama", "prompt_toolkit", "InquirerPy"],
    "python_libs": [
        "requests", 
        "pygments",
        "msgpack-python",
        "argon2-cffi",
        "tqdm",
        "colorama",
        "prompt_toolkit",
        "aiofiles", 
        "rich",
        "pyreadline3;platform_system=='Windows'",
        "fcntl;platform_system=='Linux'", 
        "struct;platform_system=='Linux'"
    ],
    "windows_pty_libs": ["pywinpty", "winpty"],
    "required_py_files": ["etc/config.json", "bin/ai_cmd.py", "Onyx.py"],
    "optional_pyc_files": ["bin/ai_cmd.pyc", "Onyx.pyc"],
    "pip_mirrors": [
        "https://mirrors.aliyun.com/pypi/simple/",
        "https://pypi.doubanio.com/simple/",
        "https://pypi.tuna.tsinghua.edu.cn/simple/"
    ]
}

# 双语文本配置
LANGUAGE_TEXT = {
    "chinese": {
        "check_steps": [
            "系统检测", "Python/pip版本适配", 
            "Python依赖库检查与安装",
            "Windows PTY支持检查",
            "PYC文件有效性检测", "核心PY文件检查", 
            "config.json验证", "Main启动文件确认"
        ],
        "messages": {
            "start_check": "🚀 开始全面环境检查...",
            "steps": "📋 检查步骤",
            "adapting_python": "正在适配 Python/pip 版本...",
            "python_found": "已适配 Python",
            "pip_found": "已适配 pip",
            "checking_libs": "检查Python依赖库",
            "libs_ready": "所有Python依赖库已就绪",
            "missing_libs": "发现缺失库，开始安装",
            "using_mirror": "使用源",
            "parallel_install": "并行安装缺失库...",
            "parallel_success": "并行安装完成",
            "parallel_failed": "部分库安装失败，将重试",
            "install_success": "安装成功",
            "install_failed": "安装失败",
            "system_detected": "检测到系统",
            "system_uptime": "系统已运行",
            "checking_pty": "检查Windows PTY支持（伪终端）...",
            "pty_ready": "✓ Windows PTY支持已就绪",
            "missing_pty": "⚠️ 缺少PTY支持库，正在安装",
            "pty_install_success": "✓ PTY支持库安装成功",
            "pty_install_failed": "✗ PTY支持库安装失败（某些功能可能受限）",
            "checking_py_files": "检查核心PY文件",
            "py_files_ready": "所有核心PY文件（或有效PYC）已就绪",
            "missing_py_files": "缺失必须PY文件（且无有效PYC兜底）",
            "validating_config": "正在验证 config.json...",
            "config_valid": "config.json 格式与核心配置项正常",
            "config_missing": "config.json 文件缺失",
            "config_invalid": "config.json 格式错误（非标准JSON）",
            "config_key_missing": "config.json 缺失核心项",
            "determine_start_file": "已确定启动文件",
            "using_pyc": "（优先使用有效PYC）",
            "using_py": "（PYC无效/缺失，使用PY兜底）",
            "check_passed": "=== 环境检查全部通过！===",
            "starting_main": "即将启动",
            "system_info": "系统信息",
            "python_path": "Python路径",
            "starting_onyx": "正在启动 Onyx 主程序...",
            "direct_import_failed": "直接导入Onyx失败，将用兼容模式启动",
            "onyx_error": "Onyx主程序运行异常",
            "check_complete": "环境检查完成，总耗时",
            "check_failed": "环境检查失败，总耗时",
            "init_error": "程序初始化异常",
            "using_cache": "使用永久缓存，极速启动...",
            "fast_launch": "极速启动完成",
            "best_mirror_selected": "已选择最快镜像源",
            "mirror_testing": "正在测试镜像源速度...",
            "mirror_test_complete": "镜像源测试完成",
            "cmd_mode_starting": "🚀 命令行模式启动: 执行单次命令",
            "cmd_executing": "执行命令",
            "cmd_executed": "命令执行完成，退出码",
            "permanent_cache_valid": "✓ 永久缓存有效，跳过环境检查",
            "permanent_cache_invalid": "✗ 永久缓存失效，开始环境检查",
            "cache_not_found": "未找到永久缓存，开始环境检查",
            "ultra_fast_launch": "⚡ 极致启动模式：直接跳转到主程序",
            "ultra_fast_enabled": "⚡ 极致启动模式已启用",
            "ultra_fast_disabled": "极致启动模式已禁用，执行完整检查",
            "login_mode_activated": "🔐 登录模式已激活",
            "shell_detected": "检测到Shell类型",
            "loading_profile": "加载Shell配置文件",
            "login_not_supported": "❌ 登录模式不支持当前Shell",
            "login_windows_info": "⚠️ Windows不支持登录模式，请使用bash/zsh（WSL）",
            "profile_not_found": "配置文件未找到，跳过加载",
            "profile_loaded": "配置文件加载完成",
            "termux_note": "Termux环境：跳过标准登录文件，使用.bashrc",
            "copy_c_files": "正在复制C扩展文件到Termux目录...",
            "copy_c_success": "✓ C扩展文件复制成功",
            "copy_c_failed": "✗ C扩展文件复制失败",
            "copy_c_skip": "✓ C文件已存在，跳过复制",
            "termux_detected": "📱 Termux环境检测完成，C文件将在首次需要时复制",
            "home_init_skip": "📁 sandbox=false，跳过 HOME 初始化，使用当前 HOME: {}",
            "home_init_virtual": "📁 初始化虚拟 HOME 目录...",
            "home_init_created": "   创建目录: {}",
            "home_init_success": "   虚拟 HOME: {}",
            "home_init_failed": "❌ 用户主目录初始化失败",
            "sandbox_config_missing": "未找到沙箱配置文件，默认启用",
            "sandbox_disabled": "沙箱已禁用",
            "sandbox_enabled": "沙箱已启用",
            "login_mode_force_home": "🔐 登录模式强制切换到虚拟 HOME",
            "first_run_title": "🔧 首次运行 — 强制环境检测 / First-Run Forced Environment Check",
            "first_run_subtitle": "正在逐阶段彻底检查运行环境，请稍候…",
            "first_run_stage_prefix": "阶段",
            "first_run_pass": "✓ 通过",
            "first_run_fail": "✗ 失败",
            "first_run_warn": "⚠ 警告",
            "first_run_skip": "⊙ 跳过",
            "first_run_py_version": "Python 版本",
            "first_run_pip_path": "pip 路径",
            "first_run_summary_pass": "✅ 所有阶段通过！环境就绪。",
            "first_run_summary_fail": "⚠️ 部分阶段未通过，但仍将尝试启动。",
            "first_run_flag_saved": "💾 首次检测标记已保存，后续启动将跳过此检查。",
            "first_run_proceeding": "🚀 正在进入 Onyx 主程序…",
            "setup_welcome": "🔧 欢迎！检测到这是首次启动，请完成初始配置。",
            "setup_step_lang": "第 1 步：选择语言",
            "setup_step_ai": "第 2 步：配置 AI",
            "ai_select_platform": "🤖 选择 AI 平台",
            "ai_custom_platform": "自定义 (Custom)",
            "ai_enter_key": "🔑 输入 {} API Key",
            "ai_enter_url": "🌐 输入 API 地址",
            "ai_enter_model": "📋 输入模型名称",
            "ai_select_model": "📋 选择模型",
            "ai_skip_config": "⊙ 已跳过 AI 配置（之后可在 Onyx 中用 ai 命令设置）",
            "ai_config_saved": "✅ AI 配置已保存"
        }
    },
    "english": {
        "check_steps": [
            "System Detection", "Python/pip Version Adaptation", 
            "Python Library Check and Installation",
            "Windows PTY Support Check",
            "PYC File Validation", "Core PY Files Check", 
            "config.json Verification", "Main Startup File Confirmation"
        ],
        "messages": {
            "start_check": "🚀 Starting comprehensive environment check...",
            "steps": "📋 Check steps",
            "adapting_python": "Adapting Python/pip versions...",
            "python_found": "Adapted Python",
            "pip_found": "Adapted pip",
            "checking_libs": "Checking Python libraries",
            "libs_ready": "All Python libraries are ready",
            "missing_libs": "Found missing libraries, starting installation",
            "using_mirror": "Using mirror",
            "parallel_install": "Parallel installing missing libraries...",
            "parallel_success": "Parallel installation completed",
            "parallel_failed": "Some libraries failed to install, will retry",
            "install_success": "Installation successful",
            "install_failed": "Installation failed",
            "system_detected": "Detected system",
            "system_uptime": "System uptime",
            "checking_pty": "Checking Windows PTY support (pseudo-terminal)...",
            "pty_ready": "✓ Windows PTY support ready",
            "missing_pty": "⚠️ Missing PTY support library, installing",
            "pty_install_success": "✓ PTY support library installed successfully",
            "pty_install_failed": "✗ PTY support library installation failed (some features may be limited)",
            "checking_py_files": "Checking core PY files",
            "py_files_ready": "All core PY files (or valid PYC) are ready",
            "missing_py_files": "Missing required PY files (and no valid PYC fallback)",
            "validating_config": "Validating config.json...",
            "config_valid": "config.json format and normal",
            "config_missing": "config.json file missing",
            "config_invalid": "config.json format error (non-standard JSON)",
            "config_key_missing": "config.json missing core items",
            "determine_start_file": "Startup file determined",
            "using_pyc": "(Priority use of valid PYC)",
            "using_py": "(PYC invalid/missing, using PY as fallback)",
            "check_passed": "=== All environment checks passed! ===",
            "starting_main": "About to start",
            "system_info": "System information",
            "python_path": "Python path",
            "starting_onyx": "Starting Onyx main program...",
            "direct_import_failed": "Direct import of Onyx failed, will use compatibility mode",
            "onyx_error": "Onyx main program runtime exception",
            "check_complete": "Environment check completed, total time",
            "check_failed": "Environment check failed, total time",
            "init_error": "Program initialization exception",
            "using_cache": "Using permanent cache, fast launching...",
            "fast_launch": "Fast launch completed",
            "best_mirror_selected": "Best mirror selected",
            "mirror_testing": "Testing mirror speeds...",
            "mirror_test_complete": "Mirror test completed",
            "cmd_mode_starting": "🚀 Command line mode: Executing single command",
            "cmd_executing": "Executing command",
            "cmd_executed": "Command executed, exit code",
            "permanent_cache_valid": "✓ Permanent cache valid, skipping environment check",
            "permanent_cache_invalid": "✗ Permanent cache invalid, starting environment check",
            "cache_not_found": "No permanent cache found, starting environment check",
            "ultra_fast_launch": "⚡ Ultra fast launch mode: Direct jump to main program",
            "ultra_fast_enabled": "⚡ Ultra fast launch mode enabled",
            "ultra_fast_disabled": "Ultra fast launch mode disabled, performing full check",
            "login_mode_activated": "🔐 Login mode activated",
            "shell_detected": "Shell type detected",
            "loading_profile": "Loading shell profile",
            "login_not_supported": "❌ Login mode not supported for current shell",
            "login_windows_info": "⚠️ Windows does not support login mode, please use bash/zsh (WSL)",
            "profile_not_found": "Profile not found, skipping load",
            "profile_loaded": "Profile loaded successfully",
            "termux_note": "Termux environment: Skipping standard login files, using .bashrc",
            "copy_c_files": "Copying C extension files to Termux directory...",
            "copy_c_success": "✓ C extension files copied successfully",
            "copy_c_failed": "✗ C extension files copy failed",
            "copy_c_skip": "✓ C files already exist, skipping copy",
            "termux_detected": "📱 Termux environment detected, C files will be copied on first use",
            "home_init_skip": "📁 sandbox=false, skip HOME initialization, using current HOME: {}",
            "home_init_virtual": "📁 Initializing virtual HOME directory...",
            "home_init_created": "   Created directory: {}",
            "home_init_success": "   Virtual HOME: {}",
            "home_init_failed": "❌ User home directory initialization failed",
            "sandbox_config_missing": "Sandbox config not found, default enabled",
            "sandbox_disabled": "Sandbox disabled",
            "sandbox_enabled": "Sandbox enabled",
            "login_mode_force_home": "🔐 Login mode force switch to virtual HOME",
            "first_run_title": "🔧 First-Run Forced Environment Check / 首次运行 — 强制环境检测",
            "first_run_subtitle": "Running thorough stage-by-stage environment verification, please wait…",
            "first_run_stage_prefix": "Stage",
            "first_run_pass": "✓ PASS",
            "first_run_fail": "✗ FAIL",
            "first_run_warn": "⚠ WARN",
            "first_run_skip": "⊙ SKIP",
            "first_run_py_version": "Python version",
            "first_run_pip_path": "pip path",
            "first_run_summary_pass": "✅ All stages passed! Environment ready.",
            "first_run_summary_fail": "⚠️ Some stages failed, but will attempt to start anyway.",
            "first_run_flag_saved": "💾 First-run flag saved — subsequent launches will skip this check.",
            "first_run_proceeding": "🚀 Proceeding to Onyx main program…",
            "setup_welcome": "🔧 Welcome! First launch detected — please complete initial setup.",
            "setup_step_lang": "Step 1: Select language",
            "setup_step_ai": "Step 2: Configure AI",
            "ai_select_platform": "🤖 Select AI platform",
            "ai_custom_platform": "Custom",
            "ai_enter_key": "🔑 Enter {} API Key",
            "ai_enter_url": "🌐 Enter API URL",
            "ai_enter_model": "📋 Enter model name",
            "ai_select_model": "📋 Select model",
            "ai_skip_config": "⊙ AI config skipped (you can set it later with the ai command in Onyx)",
            "ai_config_saved": "✅ AI config saved"
        }
    }
}


class BilingualManager:
    """双语管理器"""
    
    def __init__(self):
        self.current_language = "chinese"
        self.text = LANGUAGE_TEXT["chinese"]
        self.load_language_setting()
    
    def load_language_setting(self):
        try:
            config_dir = os.path.join(os.path.expanduser("~"), ".config", "onyx")
            language_file = os.path.join(config_dir, "language")
            if os.path.exists(language_file):
                with open(language_file, 'r', encoding='utf-8') as f:
                    lang_content = f.read().strip().lower()
                    if lang_content in ["english", "chinese"]:
                        self.current_language = lang_content
                        self.text = LANGUAGE_TEXT[lang_content]
            else:
                os.makedirs(config_dir, exist_ok=True)
                with open(language_file, 'w', encoding='utf-8') as f:
                    f.write("chinese")
        except Exception:
            pass
    
    def get_text(self, key: str) -> str:
        return self.text["messages"].get(key, key)
    
    def get_steps(self) -> List[str]:
        return self.text["check_steps"]


def _load_ai_models() -> dict:
    """Load AI platform configs from etc/ai/models.json.

    Returns a dict keyed by platform id.  When the JSON file is missing or
    unparseable a hardcoded fallback is used so the app never breaks.
    """
    models_path = os.path.join(ROOT_DIR, "onyx", "etc", "ai", "models.json")
    try:
        with open(models_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Strip meta keys (anything without an api_url)
        return {k: v for k, v in data.items() if isinstance(v, dict) and "api_url" in v}
    except Exception:
        pass
    # ── Hardcoded fallback (kept in sync with models.json) ──
    return {
        "deepseek": {
            "name": "深度求索DeepSeek",
            "api_url": "https://api.deepseek.com/v1/chat/completions",
            "default_model": "deepseek-v4-flash",
            "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
            "params": {"temperature": 0.1, "top_p": 0.2, "max_tokens": 8192},
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        },
        "openai": {
            "name": "OpenAI",
            "api_url": "https://api.openai.com/v1/chat/completions",
            "default_model": "gpt-5.5-instant",
            "models": ["gpt-5.5", "gpt-5.5-instant", "gpt-5.5-pro"],
            "params": {"temperature": 0.1, "top_p": 0.2, "max_tokens": 4096},
        },
        "anthropic": {
            "name": "Anthropic",
            "api_url": "https://api.anthropic.com/v1/messages",
            "default_model": "claude-sonnet-4-6",
            "models": ["claude-sonnet-4-6", "claude-opus-4-8"],
            "params": {"max_tokens": 4096},
        },
    }


class UltraFastEnvironmentChecker:
    """极致极速环境检查器 - 支持永久缓存、秒开启动和登录模式"""
    
    def __init__(self):
        with TimeIt("__init__初始化", log_to_file=True):
            self.lang = BilingualManager()
            self.system_type = None
            self.system_type = self.detect_system()
            self.python_exe = self.get_python_executable()
            self.pip_exe = self.get_pip_executable(self.python_exe)
            self.visual_tools_initialized = False
            self.tqdm = None
            self.Fore = None
            self.Style = None
            self.pyc_status = {}
            self.max_workers = min(4, os.cpu_count() or 2)
            
            # 永久缓存
            self.permanent_cache_file = PERMANENT_CACHE_FILE
            self.ultra_fast_flag_file = ULTRA_FAST_FLAG_FILE
            self.termux_c_copied_flag = TERMUX_C_COPIED_FLAG
            self._best_mirror_cache = None
            
            # Termux C文件复制标记
            self._termux_c_copied = False
        
        # 初始化性能追踪器
        self.perf_tracker = PerformanceTracker()
    
    def t(self, key: str) -> str:
        return self.lang.get_text(key)
    
    @timer("is_ultra_fast_enabled")
    def is_ultra_fast_enabled(self) -> bool:
        """检查是否启用极致启动模式"""
        if os.path.exists(self.ultra_fast_flag_file):
            try:
                with open(self.ultra_fast_flag_file, 'r', encoding='utf-8') as f:
                    flag_content = f.read().strip()
                    if flag_content == "ENABLED":
                        return True
            except:
                pass
        return False
    
    @timer("enable_ultra_fast_mode")
    def enable_ultra_fast_mode(self):
        """启用极致启动模式"""
        try:
            with open(self.ultra_fast_flag_file, 'w', encoding='utf-8') as f:
                f.write("ENABLED")
            log_print(f"✓ {self.t('ultra_fast_enabled')}")
        except Exception as e:
            log_print(f"⚠️ 启用极致启动模式失败: {e}")
    
    def disable_ultra_fast_mode(self):
        """禁用极致启动模式"""
        try:
            if os.path.exists(self.ultra_fast_flag_file):
                os.remove(self.ultra_fast_flag_file)
        except:
            pass
    
    def is_termux_c_already_copied(self) -> bool:
        """检查Termux C文件是否已经复制过"""
        if self._termux_c_copied:
            return True
        
        if os.path.exists(self.termux_c_copied_flag):
            try:
                termux_home = os.path.expanduser("~")
                target_c_dir = os.path.join(termux_home, "c")
                if os.path.exists(target_c_dir) and os.listdir(target_c_dir):
                    self._termux_c_copied = True
                    return True
                else:
                    os.remove(self.termux_c_copied_flag)
            except:
                pass
        
        return False
    
    def mark_termux_c_copied(self):
        """标记Termux C文件已复制"""
        try:
            with open(self.termux_c_copied_flag, 'w', encoding='utf-8') as f:
                f.write(str(time.time()))
            self._termux_c_copied = True
        except:
            pass
    
    @timer("copy_c_files_to_termux_home")
    def copy_c_files_to_termux_home(self):
        """在Termux环境下，复制C文件到用户主目录"""
        if not self.is_termux_environment():
            return False
        
        if self.is_termux_c_already_copied():
            log_print(f"✓ {self.t('copy_c_skip')}")
            return True
        
        log_print(f"📁 {self.t('copy_c_files')}")
        
        source_c_dir = os.path.join(MAIN_FILE_DIR, "lib", "c")
        
        if not os.path.exists(source_c_dir):
            log_print(f"⚠️ 源目录不存在: {source_c_dir}", is_error=True)
            return False
        
        termux_home = os.path.expanduser("~")
        target_c_dir = os.path.join(termux_home, "c")
        
        try:
            os.makedirs(os.path.dirname(target_c_dir), exist_ok=True)
            
            if os.path.exists(target_c_dir):
                if os.path.isdir(target_c_dir):
                    if os.listdir(target_c_dir):
                        self.mark_termux_c_copied()
                        log_print(f"✓ {self.t('copy_c_skip')}")
                        return True
                    shutil.rmtree(target_c_dir)
                else:
                    os.remove(target_c_dir)
            
            shutil.copytree(source_c_dir, target_c_dir)
            self.mark_termux_c_copied()
            
            log_print(f"✅ {self.t('copy_c_success')}")
            log_print(f"   源: {source_c_dir}")
            log_print(f"   目标: {target_c_dir}")
            
            return True
            
        except Exception as e:
            log_print(f"❌ {self.t('copy_c_failed')}: {str(e)}", is_error=True)
            return False
    
    def ensure_c_files_for_termux(self):
        """懒加载：首次真正需要C扩展时调用"""
        if not self.is_termux_environment():
            return True
        
        if self.is_termux_c_already_copied():
            return True
        
        return self.copy_c_files_to_termux_home()
    
    def is_termux_environment(self) -> bool:
        """判断是否为Termux环境"""
        return self.system_type == "Termux"
    
    @timer("detect_system")
    def detect_system(self) -> str:
        """检测系统类型"""
        is_termux = "termux" in sys.prefix.lower() or \
                    (os.path.exists("/data/data/com.termux") if hasattr(os, 'path') else False)
        
        if is_termux:
            system_type = "Termux"
            log_print(f"📱 {self.t('termux_detected')}")
        elif sys.platform.startswith("win32"):
            system_type = "Windows"
        elif sys.platform.startswith("darwin"):
            system_type = "macOS"
        else:
            if os.path.exists("/etc/os-release"):
                try:
                    with open("/etc/os-release", "r") as f:
                        content = f.read()
                        if "ID=kali" in content or "ID=parrot" in content:
                            system_type = "SpecialLinux"
                        else:
                            system_type = "Linux/macOS"
                except:
                    system_type = "Linux/macOS"
            else:
                system_type = "Linux/macOS"
        
        return system_type
    
    @timer("get_python_executable")
    def get_python_executable(self) -> str:
        """获取Python可执行文件路径"""
        if sys.executable and os.path.exists(sys.executable):
            return sys.executable
        
        for name in ["python3", "python", "python3.9", "python3.8", "python3.7"]:
            python_path = shutil.which(name)
            if python_path:
                return python_path
        
        raise FileNotFoundError("Python可执行文件未找到")
    
    def get_pip_executable(self, python_exe: str) -> str:
        """获取pip可执行文件路径"""
        return f"{python_exe} -m pip"
    
    def get_system_boot_timestamp(self) -> int:
        """获取系统启动时间戳"""
        return int(time.time())
    
    def format_time_seconds(self, seconds: int) -> str:
        """格式化时间"""
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}m{secs}s"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            secs = seconds % 60
            return f"{hours}h{minutes}m{secs}s"
    
    @timer("check_lib_installed_fast")
    def check_lib_installed_fast(self, lib_name: str) -> bool:
        """快速检查库是否安装"""
        clean_lib = lib_name.split(';')[0].strip()
        
        import_name_map = {
            'pywinpty': 'winpty',
            'msgpack-python': 'msgpack',
            'pyreadline3': 'pyreadline3',
            'colorama': 'colorama',
            'argon2-cffi': 'argon2',
            'prompt_toolkit': 'prompt_toolkit',
            'requests': 'requests',
            'pygments': 'pygments',
            'tqdm': 'tqdm',
        }
        
        import_name = import_name_map.get(clean_lib, clean_lib.replace('-', '_'))
        
        try:
            subprocess.run([self.python_exe, "-c", f"import {import_name}"],
                           check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=1)
            return True
        except:
            return False
    
    @timer("parallel_check_libs_fast")
    def parallel_check_libs_fast(self, libs: List[str]) -> List[str]:
        """并行检查库"""
        platform_filtered_libs = []
        for lib in libs:
            if "platform_system" in lib:
                lib_name, condition = lib.split(";")
                lib_name = lib_name.strip()
                condition = condition.strip()
                
                if condition == "platform_system=='Windows'":
                    if self.system_type == "Windows":
                        platform_filtered_libs.append(lib_name)
                elif condition == "platform_system=='Linux'":
                    if self.system_type in ["Linux/macOS", "Termux", "SpecialLinux"]:
                        platform_filtered_libs.append(lib_name)
            else:
                platform_filtered_libs.append(lib)
        
        missing = []
        
        for lib in platform_filtered_libs:
            if not self.check_lib_installed_fast(lib):
                missing.append(lib)
        
        return missing
    
    @timer("test_mirror_speed")
    def test_mirror_speed(self) -> str:
        """测试镜像源速度"""
        if hasattr(self, '_best_mirror_cache') and self._best_mirror_cache:
            return self._best_mirror_cache
        
        mirrors = REQUIRED_DEPENDENCIES["pip_mirrors"]
        best_mirror = mirrors[0]
        self._best_mirror_cache = best_mirror
        
        return best_mirror
    
    @timer("install_single_lib")
    def install_single_lib(self, lib_name: str, mirror: str) -> bool:
        """安装单个库"""
        try:
            pip_cmd = self.pip_exe.split() + ["install", "--no-cache-dir", "-i", mirror, lib_name]
            result = subprocess.run(pip_cmd, check=False, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=30)
            return result.returncode == 0
        except:
            return False
    
    @timer("parallel_install_libs")
    def parallel_install_libs(self, libs: List[str]) -> bool:
        """并行安装多个库"""
        if not libs:
            return True
        
        best_mirror = self.test_mirror_speed()
        
        try:
            pip_cmd = self.pip_exe.split() + ["install", "--no-cache-dir", "-i", best_mirror] + libs
            result = subprocess.run(pip_cmd, check=False, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=60)
            return result.returncode == 0
        except:
            return False
    
    @timer("check_and_install_windows_pty")
    def check_and_install_windows_pty(self) -> bool:
        """检查Windows PTY支持"""
        if self.system_type != "Windows":
            return True
        
        windows_pty_libs = REQUIRED_DEPENDENCIES["windows_pty_libs"]
        missing_pty_libs = self.parallel_check_libs_fast(windows_pty_libs)
        
        if not missing_pty_libs:
            return True
        else:
            return self.parallel_install_libs(missing_pty_libs)
    
    def quick_file_check(self, files: List[str]) -> List[str]:
        """快速文件检查"""
        return [f for f in files if not os.path.exists(f)]
    
    @timer("load_config")
    def load_config(self) -> bool:
        """加载配置文件"""
        config_path = os.path.join(ROOT_DIR, "onyx", "etc", "config.json")
        if not os.path.exists(config_path):
            return False
        
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            return True
        except:
            return False
    
    @timer("_check_sandbox_enabled")
    def _check_sandbox_enabled(self) -> bool:
        """检查 sandbox 是否启用"""
        sandbox_config_path = os.path.join(ROOT_DIR, "etc", "onyx", "sandbox")
        
        # 文件不存在 -> 启用 sandbox
        if not os.path.exists(sandbox_config_path):
            log_print(f"📁 {self.t('sandbox_config_missing')}")
            return True
        
        try:
            with open(sandbox_config_path, "r", encoding="utf-8") as f:
                content = f.read().strip().lower()
                enabled = content == "true"
                if enabled:
                    log_print(f"📁 {self.t('sandbox_enabled')}")
                else:
                    log_print(f"📁 {self.t('sandbox_disabled')}")
                return enabled
        except:
            return True
    
    @timer("init_user_home_for_onyx")
    def init_user_home_for_onyx(self, force_login_mode: bool = False) -> bool:
        """
        初始化用户主目录（完全负责目录创建和切换）
        
        规则：
        1. 读取 sandbox 配置文件，值为 false 时不进行任何操作
        2. sandbox 值为 true 或文件不存在时，初始化虚拟 HOME 并切换
        3. 如果没有 HOME 变量，也初始化虚拟 HOME
        4. -l 参数强制切换到虚拟 HOME
        """
        # 检查 sandbox 状态
        sandbox_enabled = self._check_sandbox_enabled()
        
        # 获取当前 HOME
        current_home = os.environ.get("HOME", "")
        
        # 规则1: sandbox = false 且 非强制登录模式，不进行任何操作
        if not sandbox_enabled and not force_login_mode:
            log_print(self.t('home_init_skip').format(current_home or '未设置/not set'))
            return True
        
        # 强制登录模式提示
        if force_login_mode:
            log_print(f"🔐 {self.t('login_mode_force_home')}")
        
        # 规则2/3/4: sandbox=true 或 无HOME 或 强制登录 -> 初始化虚拟 HOME
        log_print(f"📁 {self.t('home_init_virtual')}")
        
        # 获取用户名
        try:
            username = os.getlogin()
        except:
            username = os.environ.get("USER", os.environ.get("USERNAME", "default"))
        
        # 过滤非法字符
        username = re.sub(r'[\\/:*?"<>|]', "", username) or "default"
        
        # 检测是否为管理员
        is_admin = False
        if sys.platform.startswith("linux") or sys.platform == "darwin" or "termux" in sys.prefix.lower():
            try:
                is_admin = os.geteuid() == 0
            except:
                pass
        elif sys.platform.startswith("win32"):
            try:
                import ctypes
                is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            except:
                pass
        
        # 确定虚拟 HOME 路径
        if is_admin:
            user_home_dir = os.path.abspath(os.path.join(ROOT_DIR, "root"))
        else:
            user_home_dir = os.path.abspath(os.path.join(ROOT_DIR, "home", username))
        
        # 创建目录
        if not os.path.exists(user_home_dir):
            os.makedirs(user_home_dir, mode=0o755)
            log_print(self.t('home_init_created').format(user_home_dir))
        
        # 切换工作目录
        os.chdir(user_home_dir)
        
        # 设置 HOME 环境变量
        os.environ['HOME'] = user_home_dir
        os.environ['PWD'] = user_home_dir
        if sys.platform.startswith("win32"):
            os.environ['USERPROFILE'] = user_home_dir
        
        log_print(self.t('home_init_success').format(user_home_dir))

        # ── 自动编译 C 扩展库 ──
        self._auto_compile_c_extensions()

        # 初始化 .ai_s/onyx_ai.md（最高指示文件，AI 通过 [PROMPT]: 写入）
        ai_s_dir = os.path.join(user_home_dir, ".ai_s")
        onyx_ai_file = os.path.join(ai_s_dir, "onyx_ai.md")
        if not os.path.exists(onyx_ai_file):
            try:
                os.makedirs(ai_s_dir, exist_ok=True)
                with open(onyx_ai_file, "w", encoding="utf-8") as f:
                    f.write("# 最高指示 / Supreme Directives\n\n")
                    f.write("> 此文件由 AI 通过 `[PROMPT]:` 字段自动维护，记录跨会话的重要信息。\n")
                    f.write("> This file is auto-maintained by AI via `[PROMPT]:` field for cross-session memory.\n")
                log_print(f"📝 已初始化最高指示文件: {onyx_ai_file}")
            except Exception as e:
                log_print(f"⚠️ 初始化最高指示文件失败: {e}")

        return True

    def _auto_compile_c_extensions(self):
        """自动编译 lib/c/code/ 下的 C 扩展（仅在 .so 过期或不存在时编译）"""
        try:
            code_dir = os.path.join(ROOT_DIR, "onyx", "lib", "c_code")
            if not os.path.isdir(code_dir):
                return

            # 确定当前架构
            machine = platform.machine().lower()
            arch_map = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64",
                        "i386": "x86", "i686": "x86", "armv7l": "arm", "armv8l": "arm64"}
            arch = arch_map.get(machine, machine)

            out_base = os.path.join(ROOT_DIR, "onyx", "lib", "c")
            compiled_any = False

            # 收集所有 .c 文件（含一级子目录）
            sources = []
            for item in sorted(os.listdir(code_dir)):
                item_path = os.path.join(code_dir, item)
                if item.endswith(".c"):
                    # lib/c/code/xxx.c → lib/c/xxx/arm64.so
                    name = item[:-2]
                    sources.append((name, item_path))
                elif os.path.isdir(item_path):
                    # lib/c/code/xxx_lib/xxx.c → lib/c/xxx/arm64.so (去掉 _lib 后缀)
                    for sub in os.listdir(item_path):
                        if sub.endswith(".c"):
                            sub_name = sub[:-2]
                            out_name = item.replace("_lib", "")
                            # 如果子目录只有一个 .c 且与目录名匹配，用目录名
                            sources.append((out_name, os.path.join(item_path, sub)))

            for name, src_path in sources:
                out_dir = os.path.join(out_base, name)
                out_file = os.path.join(out_dir, f"{arch}.so")

                # 已存在且比源文件新 → 跳过
                if os.path.exists(out_file):
                    src_mtime = os.path.getmtime(src_path)
                    if os.path.getmtime(out_file) >= src_mtime:
                        continue

                os.makedirs(out_dir, exist_ok=True)
                cmd = ["gcc", "-shared", "-fPIC", "-O2", "-o", out_file, src_path]
                try:
                    subprocess.run(cmd, check=True, capture_output=True, timeout=30)
                    log_print(f"🔧 C 扩展: {name} → {arch}.so")
                    compiled_any = True
                except FileNotFoundError:
                    log_print("⚠️ gcc 未安装，跳过 C 扩展编译")
                    return
                except subprocess.TimeoutExpired:
                    log_print(f"⚠️ 编译超时: {name}")
                except subprocess.CalledProcessError as e:
                    err = e.stderr.decode()[:200] if e.stderr else 'unknown'
                    log_print(f"⚠️ 编译失败 {name}: {err}")

            if compiled_any:
                log_print("✅ C 扩展编译完成")
        except Exception as e:
            log_print(f"⚠️ C 扩展自动编译异常: {e}")

    @timer("detect_shell_type")
    def detect_shell_type(self) -> str:
        """检测shell类型"""
        shell_methods = []
        
        try:
            if sys.platform == "darwin":
                import subprocess as _subprocess
                try:
                    import ctypes
                    libc = ctypes.CDLL(None)
                    getppid = libc.getppid
                    getppid.argtypes = []
                    getppid.restype = ctypes.c_int
                    ppid = getppid()
                    if ppid > 0:
                        shell_bin = _subprocess.run(
                            ["ps", "-o", "comm=", "-p", str(ppid)],
                            capture_output=True, text=True, timeout=5
                        ).stdout.strip().lower()
                        if "bash" in shell_bin:
                            shell_methods.append("bash")
                        elif "zsh" in shell_bin:
                            shell_methods.append("zsh")
                except:
                    pass
            elif sys.platform.startswith("linux"):
                import ctypes
                libc = ctypes.CDLL(None)
                getppid = libc.getppid
                getppid.argtypes = []
                getppid.restype = ctypes.c_int
                
                ppid = getppid()
                if ppid > 0:
                    try:
                        with open(f"/proc/{ppid}/cmdline", "rb") as f:
                            cmdline = f.read().decode('utf-8', errors='ignore')
                            if "bash" in cmdline.lower():
                                shell_methods.append("bash")
                            elif "zsh" in cmdline.lower():
                                shell_methods.append("zsh")
                    except:
                        pass
        except:
            pass
        
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        for shell_name in ["bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh"]:
            for path_dir in path_dirs:
                shell_path = os.path.join(path_dir, shell_name)
                if os.path.exists(shell_path) and os.access(shell_path, os.X_OK):
                    shell_methods.append(shell_name)
        
        shell_var = os.environ.get("SHELL", "")
        if shell_var:
            shell_name = os.path.basename(shell_var)
            if shell_name in ["bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh"]:
                shell_methods.append(shell_name)
        
        try:
            result = subprocess.run(
                ["ps", "-p", str(os.getppid()), "-o", "comm="],
                capture_output=True, text=True, timeout=1
            )
            if result.returncode == 0:
                comm = result.stdout.strip().lower()
                for shell_name in ["bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh"]:
                    if shell_name in comm:
                        shell_methods.append(shell_name)
        except:
            pass
        
        term_program = os.environ.get("TERM_PROGRAM", "").lower()
        if "bash" in term_program:
            shell_methods.append("bash")
        elif "zsh" in term_program:
            shell_methods.append("zsh")
        
        if shell_methods:
            from collections import Counter
            shell_counter = Counter(shell_methods)
            most_common = shell_counter.most_common(1)
            if most_common:
                return most_common[0][0]
        
        return "unknown"
    
    def is_login_shell_supported(self) -> bool:
        """检查当前shell是否支持登录模式"""
        shell_type = self.detect_shell_type()
        return shell_type in ["bash", "zsh"]
    
    @timer("activate_login_mode")
    def activate_login_mode(self):
        """激活登录模式"""
        log_print(f"🔐 {self.t('login_mode_activated')}")
        
        shell_type = self.detect_shell_type()
        log_print(f"  ✓ {self.t('shell_detected')}: {shell_type}")
        
        if self.system_type == "Windows":
            log_print(f"  ⚠️ {self.t('login_windows_info')}", is_error=True)
            return False
        
        if not self.is_login_shell_supported():
            log_print(f"  ❌ {self.t('login_not_supported')}: {shell_type}", is_error=True)
            return False
        
        is_termux = self.system_type == "Termux"
        home_dir = os.environ.get("HOME", os.path.expanduser("~"))
        
        profile_files = []
        
        if is_termux:
            log_print(f"  📝 {self.t('termux_note')}")
            if shell_type == "bash":
                profile_files.append(os.path.join(home_dir, ".bashrc"))
        else:
            if shell_type == "bash":
                profile_files = [
                    os.path.join(home_dir, ".bash_profile"),
                    os.path.join(home_dir, ".bash_login"),
                    os.path.join(home_dir, ".profile"),
                    os.path.join(home_dir, ".bashrc")
                ]
            elif shell_type == "zsh":
                profile_files = [
                    os.path.join(home_dir, ".zprofile"),
                    os.path.join(home_dir, ".zlogin"),
                    os.path.join(home_dir, ".zshrc")
                ]
        
        loaded_files = []
        for profile_file in profile_files:
            if os.path.exists(profile_file):
                log_print(f"  📂 {self.t('loading_profile')}: {os.path.basename(profile_file)}")
                try:
                    self.source_shell_file(profile_file, shell_type)
                    loaded_files.append(profile_file)
                except Exception as e:
                    log_print(f"  ⚠️ 加载失败 {profile_file}: {str(e)[:50]}")
        
        if loaded_files:
            log_print(f"  ✅ {self.t('profile_loaded')}: {len(loaded_files)}个文件")
            return True
        else:
            log_print(f"  ⚠️ {self.t('profile_not_found')}")
            return False
    
    @timer("source_shell_file")
    def source_shell_file(self, file_path: str, shell_type: str):
        """模拟source命令加载shell配置文件"""
        try:
            if shell_type == "bash":
                cmd = f"source \"{file_path}\""
                subprocess.run(
                    ["bash", "-c", cmd],
                    env=os.environ.copy(),
                    check=False,
                    timeout=5
                )
            elif shell_type == "zsh":
                cmd = f"source \"{file_path}\""
                subprocess.run(
                    ["zsh", "-c", cmd],
                    env=os.environ.copy(),
                    check=False,
                    timeout=5
                )
            
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            for line in lines:
                line = line.strip()
                if line and not line.startswith("#"):
                    if "=" in line and not line.startswith("export "):
                        var_name, var_value = line.split("=", 1)
                        var_name = var_name.strip()
                        var_value = var_value.strip()
                        
                        if (var_value.startswith('"') and var_value.endswith('"')) or \
                           (var_value.startswith("'") and var_value.endswith("'")):
                            var_value = var_value[1:-1]
                        
                        os.environ[var_name] = var_value
                    elif line.startswith("export "):
                        export_line = line[7:].strip()
                        if "=" in export_line:
                            var_name, var_value = export_line.split("=", 1)
                            var_name = var_name.strip()
                            var_value = var_value.strip()
                            
                            if (var_value.startswith('"') and var_value.endswith('"')) or \
                               (var_value.startswith("'") and var_value.endswith("'")):
                                var_value = var_value[1:-1]
                            
                            os.environ[var_name] = var_value
                            
        except Exception as e:
            log_print(f"  ⚠️ source执行异常: {str(e)[:50]}")
    
    @timer("get_environment_hash_stable")
    def get_environment_hash_stable(self) -> str:
        """稳定版环境哈希生成方法"""
        func_start = time.perf_counter()
        
        try:
            result = subprocess.run(
                [self.python_exe, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                capture_output=True, text=True, timeout=1
            )
            if result.returncode == 0:
                python_version = result.stdout.strip()
            else:
                python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        except:
            python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        
        system_info = platform.system()
        
        key_files = ["onyx/etc/config.json", "Onyx.py", "bin/ai_cmd.py"]
        file_status = []
        
        for file in key_files:
            file_path = os.path.join(ROOT_DIR, file)
            if os.path.exists(file_path):
                file_status.append(f"{file}:1")
            else:
                file_status.append(f"{file}:0")
        
        key_libs = ["requests", "colorama", "prompt_toolkit"]
        lib_status = []
        
        for lib in key_libs:
            try:
                import_name = lib.replace('-', '_')
                cmd = f"import {import_name}; print('1')"
                result = subprocess.run(
                    [self.python_exe, "-c", cmd],
                    capture_output=True, text=True, timeout=0.5
                )
                lib_status.append(f"{lib}:{1 if result.returncode == 0 else 0}")
            except:
                lib_status.append(f"{lib}:0")
        
        cache_version = "v2.0"
        
        env_info = f"{cache_version}|{python_version}|{system_info}|{':'.join(sorted(file_status))}|{':'.join(sorted(lib_status))}"
        res = hashlib.md5(env_info.encode()).hexdigest()
        
        cost = round((time.perf_counter() - func_start)*1000,3)
        log_print(f"[极致优化] get_environment_hash_stable 耗时: {cost} ms")
        return res
    
    @timer("load_permanent_cache")
    def load_permanent_cache(self, force: bool = False) -> Optional[dict]:
        """加载永久缓存"""
        if not os.path.exists(self.permanent_cache_file):
            log_print(f"⚠️ {self.t('cache_not_found')}")
            return None
        
        try:
            with open(self.permanent_cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            if force or self.is_ultra_fast_enabled():
                log_print(f"⚡ {self.t('ultra_fast_launch')}")
                return cache_data.get('data', {})
            
            cache_time = cache_data.get('timestamp', 0)
            if time.time() - cache_time > 180 * 24 * 3600:
                log_print("⚠️ 永久缓存已过期（超过180天），但继续使用...")
            
            current_hash = self.get_environment_hash_stable()
            cached_hash = cache_data.get('hash')
            
            if cached_hash == current_hash:
                log_print(f"✅ {self.t('permanent_cache_valid')}")
                return cache_data.get('data', {})
            else:
                log_print(f"⚠️ 环境哈希轻微变化，但继续使用缓存（追求极致启动速度）")
                log_print(f"   缓存哈希: {cached_hash[:16] if cached_hash else 'None'}...")
                log_print(f"   当前哈希: {current_hash[:16]}...")
                return cache_data.get('data', {})
                
        except Exception as e:
            log_print(f"⚠️ 加载永久缓存失败: {e}")
            return None
    
    @timer("save_permanent_cache")
    def save_permanent_cache(self, data: dict):
        """保存永久缓存"""
        try:
            cache_data = {
                'hash': self.get_environment_hash_stable(),
                'timestamp': time.time(),
                'system_type': self.system_type,
                'python_exe': self.python_exe,
                'pip_exe': self.pip_exe,
                'data': data
            }
            
            with open(self.permanent_cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)
            
            self.enable_ultra_fast_mode()
            
            log_print(f"✅ 永久缓存已保存，极致启动模式已启用: {self.permanent_cache_file}")
        except Exception as e:
            log_print(f"⚠️ 保存永久缓存失败: {e}")
    
    def clear_permanent_cache(self):
        """清除永久缓存"""
        if os.path.exists(self.permanent_cache_file):
            try:
                os.remove(self.permanent_cache_file)
            except:
                pass
        
        # Also clear the first-run flag so the thorough check runs again
        flag_path = self._first_run_flag_path()
        if os.path.exists(flag_path):
            try:
                os.remove(flag_path)
            except:
                pass
        
        self.disable_ultra_fast_mode()
        log_print("✅ 永久缓存和极致启动标记已清除")
    
    @timer("jump_to_main_immediately")
    def jump_to_main_immediately(self, cached_results: dict):
        """极致优化：直接跳转到主程序"""
        try:
            # 使用 __file__ 获取路径，不使用 os.getcwd()
            main_file_dir = os.path.dirname(os.path.abspath(__file__))
            root_dir = main_file_dir  # Onyx 根目录（Main.py 同级）
            
            if root_dir not in sys.path:
                sys.path.insert(0, root_dir)
            
            os.environ["MAIN_START_TIME"] = str(time.time())
            
            self.ensure_c_files_for_termux()
            
            onyx_import_start = time.perf_counter()
            from Onyx import main_loop
            onyx_import_cost = round((time.perf_counter() - onyx_import_start) * 1000, 3)
            log_print(f"[Onyx加载] import Onyx 模块耗时: {onyx_import_cost} ms")
            
            os.environ["ONYX_IMPORT_TIME_MS"] = str(onyx_import_cost)
            
            # MCP 后台预加载（纯异步，不阻塞主流程 + 不阻塞 import）
            _mcp_flag = os.path.join(os.path.expanduser("~"), ".cache", "onyx", "mcp_preloaded.flag")
            if not os.path.exists(_mcp_flag):
                def _bg_mcp():
                    try:
                        from bin.ai_cmd import preload_mcp_servers
                        from Onyx import USER_HOME_DIR as _onyx_user_home
                        preload_mcp_servers(_onyx_user_home)
                        log_print("[MCP预加载] 后台完成")
                    except Exception:
                        pass
                t = threading.Thread(target=_bg_mcp, daemon=True, name="mcp-preload")
                t.start()
                log_print("[MCP预加载] 已调度后台")
            else:
                log_print("[MCP预加载] 已完成过，跳过")
            
            main_loop()
            
        except Exception as e:
            log_print(f"直接导入失败: {e}，尝试子进程启动")
            onyx_abs = os.path.join(main_file_dir, "Onyx.py")
            subprocess.run([self.python_exe, onyx_abs], check=False)
    
    @timer("execute_single_command")
    def execute_single_command(self, command: str, quiet: bool = False) -> int:
        """执行单个命令"""
        try:
            cache_data = self.load_permanent_cache(force=True)
            if cache_data:
                try:
                    main_file_dir = os.path.dirname(os.path.abspath(__file__))
                    root_dir = main_file_dir
                    if root_dir not in sys.path:
                        sys.path.insert(0, root_dir)
                    
                    import Onyx
                    exit_code = Onyx.run_command_once(command)
                    return exit_code
                except:
                    pass
        except:
            pass
        
        return self.execute_single_command_fallback(command, quiet)
    
    def execute_single_command_fallback(self, command: str, quiet: bool = False) -> int:
        """回退的命令执行"""
        try:
            if not self.minimal_env_check():
                return 1
            
            try:
                main_file_dir = os.path.dirname(os.path.abspath(__file__))
                root_dir = main_file_dir
                if root_dir not in sys.path:
                    sys.path.insert(0, root_dir)
                
                import Onyx
                exit_code = Onyx.run_command_once(command)
                return exit_code
            except:
                exit_code = subprocess.run([self.python_exe, "cmd.py", "-c", command], 
                                          check=False).returncode
                return exit_code
        except:
            return 1
    
    # ── First-time setup wizard ───────────────────────────────────────────────
    # Runs ONCE when the first-run flag file is missing.
    #   Step 1 — language selection (InquirerPy or numbered menu)
    #   Step 2 — AI platform / key / model / custom-URL / params (all with defaults)

    def _try_import_inquirerpy(self):
        """Try to import InquirerPy; returns the module or None."""
        try:
            from InquirerPy import inquirer as iq
            if sys.stdin.isatty() and sys.stdout.isatty():
                return iq
        except ImportError:
            pass
        return None

    def _select_one(self, iq, message: str, options: list, default: str = "") -> str:
        """Select one option from a list. Uses InquirerPy when available."""
        default = default or (options[0] if options else "")
        if iq and options:
            try:
                return iq.select(message=message, choices=options, default=default).execute()
            except Exception:
                pass  # fall through to numbered menu
        # ── Numbered fallback ──
        print(f"\n  {message}")
        for i, opt in enumerate(options, 1):
            marker = "  ← default" if opt == default else ""
            print(f"    [{i}] {opt}{marker}")
        try:
            default_idx = options.index(default) + 1 if default in options else 1
        except ValueError:
            default_idx = 1
        choice = input(f"  Enter (1-{len(options)}) [{default_idx}]: ").strip()
        try:
            idx = int(choice) - 1 if choice else default_idx - 1
            return options[idx] if 0 <= idx < len(options) else default
        except (ValueError, IndexError):
            return default

    def _text_input(self, iq, message: str, default: str = "") -> str:
        """Text input with optional default. Uses InquirerPy when available."""
        if iq:
            try:
                return iq.text(message=message, default=default).execute()
            except Exception:
                pass
        prompt = f"{message} [{default}]: " if default else f"{message}: "
        val = input(f"  {prompt}").strip()
        return val if val else default

    def _secret_input(self, iq, message: str) -> str:
        """Password-style masked input. Uses InquirerPy secret or getpass."""
        if iq:
            try:
                return iq.secret(message=message).execute()
            except Exception:
                pass
        import getpass
        try:
            return getpass.getpass(f"  {message}: ").strip()
        except (KeyboardInterrupt, EOFError):
            return ""

    def _save_language_setting(self, lang: str):
        """Persist language choice to ~/.config/onyx/language."""
        try:
            config_dir = os.path.join(os.path.expanduser("~"), ".config", "onyx")
            os.makedirs(config_dir, exist_ok=True)
            with open(os.path.join(config_dir, "language"), "w", encoding="utf-8") as f:
                f.write(lang)
            log_print(f"[FirstRun] Language set: {lang}")
        except Exception as e:
            log_print(f"[FirstRun] Failed to save language: {e}", is_error=True)

    def _save_ai_config(self, platform: str, api_key: str, model: str,
                        api_url: str, params: dict):
        """Persist AI config to key.conf (delegate to bin.ai_cmd)."""
        try:
            # 使用 ai_cmd 的 save_key_conf（自动混淆 api_key）
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "bin"))
            from bin.ai_cmd import save_key_conf
            save_key_conf(platform, api_key, model, params)
            log_print(f"[FirstRun] AI config saved")
        except Exception as e:
            log_print(f"[FirstRun] Failed to save AI config: {e}", is_error=True)

    _AI_PLATFORMS = _load_ai_models()

    def _first_time_setup_wizard(self):
        """First-time interactive setup: language → AI config.

        Uses InquirerPy when available; falls back to numbered menus with
        plain input().  All prompts are bilingual and switch to the
        user's chosen language immediately after Step 1.
        """
        iq = self._try_import_inquirerpy()

        # ── Header ────────────────────────────────────────────────────
        welcome = ("\n" + "=" * 64 + "\n"
                   "  🔧 欢迎！检测到首次启动 / Welcome! First launch detected\n"
                   "  请完成初始配置 / Please complete initial setup\n"
                   + "=" * 64)
        print(welcome)
        log_print("[FirstRun] Setup wizard started")

        # ── Step 1: Language ──────────────────────────────────────────
        print(f"\n  📌 {self.t('setup_step_lang')}")
        lang_title = "🌐 请选择语言 / Please select language"
        lang_options = ["中文 (Chinese)", "English"]
        lang_choice = self._select_one(iq, lang_title, lang_options, default=lang_options[0])
        lang = "chinese" if "中文" in lang_choice else "english"

        # Switch language immediately
        self.lang.current_language = lang
        self.lang.text = LANGUAGE_TEXT[lang]
        self._save_language_setting(lang)
        print(f"  ✅ {lang_choice}")

        # ── Step 2: AI Configuration ──────────────────────────────────
        t = self.t  # shorthand
        print(f"\n  📌 {t('setup_step_ai')}")

        # Build platform list (built-in + custom)
        plat_keys = list(self._AI_PLATFORMS.keys()) + ["custom"]
        plat_names = [self._AI_PLATFORMS[p]["name"] for p in self._AI_PLATFORMS]
        plat_names.append(t("ai_custom_platform"))

        plat_choice = self._select_one(iq, t("ai_select_platform"), plat_names)
        if not plat_choice:
            print(f"  {t('ai_skip_config')}")
            return
        plat_idx = plat_names.index(plat_choice)
        platform = plat_keys[plat_idx]

        # ── API Key (masked input) ──
        key_prompt = t("ai_enter_key").format(
            self._AI_PLATFORMS[platform]["name"] if platform != "custom" else t("ai_custom_platform")
        )
        api_key = self._secret_input(iq, key_prompt)
        if not api_key:
            print(f"  {t('ai_skip_config')}")
            return

        # ── URL & Model ──
        if platform == "custom":
            api_url = self._text_input(iq, t("ai_enter_url"),
                                       "https://api.openai.com/v1/chat/completions")
            model = self._text_input(iq, t("ai_enter_model"), "gpt-4")
            params = {"temperature": 0.1, "max_tokens": 4096}
        else:
            info = self._AI_PLATFORMS[platform]
            api_url = info["api_url"]
            model = self._select_one(iq, t("ai_select_model"), info["models"],
                                     default=info["default_model"])
            params = dict(info["params"])

        # ── Save ──
        self._save_ai_config(platform, api_key, model, api_url, params)
        info_name = self._AI_PLATFORMS[platform]["name"] if platform != "custom" else t("ai_custom_platform")
        print(f"  {t('ai_config_saved')}: {info_name} — {model}")

    # ── First-run forced check helpers ─────────────────────────────────────────
    # These are used only by first_run_forced_check() to print bilingual
    # stage-by-stage output to the console on the very first launch.

    def _print_stage_header(self, n: int, total: int, name: str):
        """Print a bilingual stage header to console and log."""
        prefix = self.t('first_run_stage_prefix')
        line = f"\n  [{prefix} {n}/{total}] {name}"
        print(line)
        log_print(f"[FirstRun] Stage {n}/{total}: {name}")

    def _print_stage_result(self, n: int, total: int, name: str,
                            passed: bool, detail: str = "", skipped: bool = False):
        """Print a bilingual PASS / FAIL / SKIP line for a completed stage."""
        if skipped:
            tag = self.t('first_run_skip')
        elif passed:
            tag = self.t('first_run_pass')
        else:
            tag = self.t('first_run_fail')
        msg = f"  └─ {tag}"
        if detail:
            msg += f"  ({detail})"
        print(msg)
        log_print(f"[FirstRun] Stage {n}/{total} result: {tag} {detail}")

    def _first_run_flag_path(self) -> str:
        """Return the first-run flag path under the CURRENT HOME.
        
        This is resolved dynamically because init_user_home_for_onyx()
        may have switched HOME to a virtual directory after module import.
        """
        home = os.environ.get("HOME", os.path.expanduser("~"))
        return os.path.join(home, ".cache", "onyx", _FIRST_RUN_FLAG_FILENAME)

    def _mark_first_run_done(self):
        """Create the first-run flag file so subsequent launches skip this check."""
        flag_path = self._first_run_flag_path()
        try:
            os.makedirs(os.path.dirname(flag_path), exist_ok=True)
            with open(flag_path, 'w', encoding='utf-8') as f:
                f.write(f"{time.time()}\n")
                f.write(f"system={self.system_type}\n")
                f.write(f"python={sys.version_info.major}.{sys.version_info.minor}\n")
            log_print(f"[FirstRun] Flag saved: {flag_path}")
        except Exception as e:
            log_print(f"[FirstRun] Failed to save flag: {e}", is_error=True)

    # ── First-run forced environment check ────────────────────────────────────

    def first_run_forced_check(self):
        """First-run forced environment check — thorough, sequential, bilingual.

        Runs every stage one-by-one with clear console output so the user can
        see exactly what is being verified.  This is intentionally exhaustive;
        startup speed is not a concern here.  The goal is to establish a
        verified baseline cache (Main_init_env.json) so that ALL subsequent
        launches can use the fast path.
        """
        steps = self.lang.get_steps()
        total = len(steps)

        # ── Bilingual header ──
        title = self.t('first_run_title')
        subtitle = self.t('first_run_subtitle')
        print(f"\n{'=' * 64}")
        print(f"  {title}")
        print(f"  {subtitle}")
        print(f"{'=' * 64}")
        log_print(f"[FirstRun] {title}")

        all_pass = True
        results: Dict[str, Any] = {}

        # ── Stage 1: System Detection ─────────────────────────────────────
        self._print_stage_header(1, total, steps[0])
        try:
            system_type = self.system_type
            py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            arch = platform.machine()
            print(f"  🖥  OS     : {system_type}")
            print(f"  🐍 Python : {py_ver}")
            print(f"  🔧 Arch   : {arch}")
            results['system_type'] = system_type
            self._print_stage_result(1, total, steps[0], True)
        except Exception as e:
            self._print_stage_result(1, total, steps[0], False, str(e))
            all_pass = False

        # ── Stage 2: Python/pip Version Adaptation ────────────────────────
        self._print_stage_header(2, total, steps[1])
        try:
            result = subprocess.run(
                [self.python_exe, "-c",
                 "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
                capture_output=True, text=True, timeout=5
            )
            py_full = (result.stdout.strip() if result.returncode == 0
                       else f"{sys.version_info.major}.{sys.version_info.minor}")
            print(f"  {self.t('first_run_py_version')}: {py_full}")
            print(f"  {self.t('first_run_pip_path')}: {self.pip_exe}")
            results['python_exe'] = self.python_exe
            results['pip_exe'] = self.pip_exe
            self._print_stage_result(2, total, steps[1], True)
        except Exception as e:
            self._print_stage_result(2, total, steps[1], False, str(e))
            all_pass = False

        # ── Stage 3: Python Dependency Library Check & Installation ───────
        self._print_stage_header(3, total, steps[2])
        try:
            missing_libs = self.parallel_check_libs_fast(
                REQUIRED_DEPENDENCIES["python_libs"]
            )
            if missing_libs:
                print(f"  {self.t('missing_libs')}: {', '.join(missing_libs)}")
                ok = self.parallel_install_libs(missing_libs)
                if ok:
                    print(f"  {self.t('parallel_success')}")
                    self._print_stage_result(3, total, steps[2], True)
                else:
                    print(f"  {self.t('parallel_failed')}")
                    self._print_stage_result(3, total, steps[2], False)
                    all_pass = False
            else:
                print(f"  {self.t('libs_ready')}")
                self._print_stage_result(3, total, steps[2], True)
            results['libs_installed'] = not bool(missing_libs)
        except Exception as e:
            self._print_stage_result(3, total, steps[2], False, str(e))
            all_pass = False

        # ── Stage 4: Windows PTY Support Check ────────────────────────────
        self._print_stage_header(4, total, steps[3])
        try:
            if self.system_type == "Windows":
                pty_ok = self.check_and_install_windows_pty()
                if pty_ok:
                    print(f"  {self.t('pty_ready')}")
                    self._print_stage_result(4, total, steps[3], True)
                else:
                    print(f"  {self.t('pty_install_failed')}")
                    self._print_stage_result(4, total, steps[3], False)
                    all_pass = False
            else:
                print(f"  {self.t('first_run_skip')}  (non-Windows platform)")
                self._print_stage_result(4, total, steps[3], True, skipped=True)
        except Exception as e:
            self._print_stage_result(4, total, steps[3], False, str(e))
            all_pass = False

        # ── Stage 5: PYC File Validation ──────────────────────────────────
        self._print_stage_header(5, total, steps[4])
        try:
            pyc_missing = False
            for pyc_rel in REQUIRED_DEPENDENCIES.get("optional_pyc_files", []):
                pyc_path = os.path.join(ROOT_DIR, "onyx", pyc_rel)
                exists = os.path.exists(pyc_path)
                mark = "✓" if exists else "✗"
                print(f"  {mark}  {pyc_rel}")
                if not exists:
                    pyc_missing = True
            if pyc_missing:
                print(f"  {self.t('first_run_warn')}: "
                      f"some .pyc files missing — .py fallback will be used")
                self._print_stage_result(5, total, steps[4], True, skipped=True)
            else:
                self._print_stage_result(5, total, steps[4], True)
        except Exception as e:
            self._print_stage_result(5, total, steps[4], False, str(e))
            all_pass = False

        # ── Stage 6: Core PY Files Check ──────────────────────────────────
        self._print_stage_header(6, total, steps[5])
        try:
            missing_files = self.quick_file_check(
                [os.path.join(ROOT_DIR, "onyx", f)
                 for f in REQUIRED_DEPENDENCIES["required_py_files"]]
            )
            if missing_files:
                print(f"  {self.t('missing_py_files')}: "
                      f"{', '.join(os.path.basename(m) for m in missing_files)}")
                self._print_stage_result(6, total, steps[5], False)
                all_pass = False
            else:
                print(f"  {self.t('py_files_ready')}")
                self._print_stage_result(6, total, steps[5], True)
        except Exception as e:
            self._print_stage_result(6, total, steps[5], False, str(e))
            all_pass = False

        # ── Stage 7: config.json Verification ─────────────────────────────
        self._print_stage_header(7, total, steps[6])
        try:
            config_ok = self.load_config()
            results['config_valid'] = config_ok
            if config_ok:
                print(f"  {self.t('config_valid')}")
                self._print_stage_result(7, total, steps[6], True)
            else:
                print(f"  {self.t('config_invalid')}")
                self._print_stage_result(7, total, steps[6], False)
                all_pass = False
        except Exception as e:
            self._print_stage_result(7, total, steps[6], False, str(e))
            all_pass = False

        # ── Stage 8: Main Startup File Confirmation ───────────────────────
        self._print_stage_header(8, total, steps[7])
        try:
            onyx_py = os.path.join(ROOT_DIR, "onyx", "Onyx.py")
            onyx_pyc = os.path.join(ROOT_DIR, "onyx", "Onyx.pyc")
            if os.path.exists(onyx_pyc):
                print(f"  {self.t('determine_start_file')}: Onyx.pyc  "
                      f"{self.t('using_pyc')}")
                results['start_file'] = 'Onyx.pyc'
            elif os.path.exists(onyx_py):
                print(f"  {self.t('determine_start_file')}: Onyx.py  "
                      f"{self.t('using_py')}")
                results['start_file'] = 'Onyx.py'
            else:
                print(f"  ✗  Onyx.py / Onyx.pyc NOT FOUND!")
                self._print_stage_result(8, total, steps[7], False)
                all_pass = False
                print(f"\n  ❌ CRITICAL: Onyx.py is missing — cannot start.\n")
                sys.exit(1)
            self._print_stage_result(8, total, steps[7], True)
        except SystemExit:
            raise
        except Exception as e:
            self._print_stage_result(8, total, steps[7], False, str(e))
            all_pass = False

        # ── Persist cache + flag ──────────────────────────────────────────
        results['status'] = 'success'
        results['timestamp'] = time.time()
        self.save_permanent_cache(results)
        self._mark_first_run_done()

        # ── Summary ───────────────────────────────────────────────────────
        print(f"\n{'─' * 64}")
        if all_pass:
            print(f"  {self.t('first_run_summary_pass')}")
        else:
            print(f"  {self.t('first_run_summary_fail')}")
        print(f"  {self.t('first_run_flag_saved')}")
        print(f"  {self.t('first_run_proceeding')}")
        print(f"{'─' * 64}\n")

        # Jump to main program
        self.jump_to_main_immediately(results)

    # ── Original minimal / ultra-fast checks ──────────────────────────────────

    def minimal_env_check(self) -> bool:
        """最小化环境检查"""
        try:
            config_ok = self.load_config()
            if not config_ok:
                return False
            return True
        except:
            return False
    
    @timer("ultra_fast_check")
    def ultra_fast_check(self, skip_to_main: bool = False, force_check: bool = False) -> bool:
        """完整环境检查"""
        total_func_start = time.perf_counter()
        
        if skip_to_main:
            self.jump_to_main_immediately({'status': 'direct_start'})
            total_cost = round((time.perf_counter() - total_func_start)*1000,3)
            log_print(f"[极致优化] ultra_fast_check【直接启动】整体总耗时: {total_cost} ms")
            return True
        
        if not force_check:
            permanent_cache = self.load_permanent_cache(force=True)
            if permanent_cache:
                cache_data = permanent_cache
                self.system_type = cache_data.get('system_type', self.system_type)
                self.python_exe = cache_data.get('python_exe', self.python_exe)
                self.pip_exe = cache_data.get('pip_exe', self.pip_exe)
                
                cache_data['permanent_cache_used'] = True
                self.jump_to_main_immediately(cache_data)
                total_cost = round((time.perf_counter() - total_func_start)*1000,3)
                log_print(f"[极致优化] ultra_fast_check【永久缓存秒开】整体总耗时: {total_cost} ms")
                return True
        
        start_time = time.time()
        
        try:
            log_print(f"{self.t('start_check')}")
            
            with TimeIt("并行检查-系统/库/文件"):
                with ThreadPoolExecutor(max_workers=3) as executor:
                    future_system = executor.submit(lambda: (
                        self.system_type,
                        self.get_system_boot_timestamp()
                    ))
                    future_libs = executor.submit(
                        self.parallel_check_libs_fast,
                        REQUIRED_DEPENDENCIES["python_libs"]
                    )
                    future_files = executor.submit(
                        self.quick_file_check,
                        [os.path.join(ROOT_DIR, "onyx", f) for f in REQUIRED_DEPENDENCIES["required_py_files"]]
                    )
                    
                    self.system_type, boot_time = future_system.result()
                    missing_libs = future_libs.result()
                    missing_files = future_files.result()
            
            if missing_libs:
                with TimeIt("安装缺失库"):
                    self.parallel_install_libs(missing_libs)
            
            with TimeIt("检查Windows PTY支持"):
                self.check_and_install_windows_pty()
            
            with TimeIt("加载配置文件"):
                config_valid = self.load_config()
            
            check_results = {
                'status': 'success',
                'start_file': 'Onyx.py',
                'system_type': self.system_type,
                'python_exe': self.python_exe,
                'pip_exe': self.pip_exe,
                'libs_installed': not bool(missing_libs),
                'config_valid': config_valid,
                'timestamp': time.time()
            }
            
            with TimeIt("保存永久缓存"):
                self.save_permanent_cache(check_results)
            
            total_check_time = time.time() - start_time
            log_print(f"\n✅ {self.t('check_complete')}: {total_check_time:.2f}s")
            
            with TimeIt("跳转到主程序"):
                self.jump_to_main_immediately(check_results)
            
        except Exception as e:
            log_print(f"环境检查异常: {e}", is_error=True)
            self.clear_permanent_cache()
            self.jump_to_main_immediately({'status': 'fallback'})
        
        total_cost = round((time.perf_counter() - total_func_start)*1000,3)
        log_print(f"[极致优化] ultra_fast_check【完整校验】整体总耗时: {total_cost} ms")
        return True


def signal_handler(signum, frame):
    """信号处理：优雅退出
    
    修复：SIGINT 不再被静默忽略。之前直接 return 导致 Ctrl+C 完全无效，
    用户在 MCP 连接卡死时无法中断。现在恢复默认 SIGINT 处理器并重新发送信号，
    让 Python 自然抛出 KeyboardInterrupt，可中断 select/sleep 等阻塞调用。
    """
    if signum == signal.SIGINT:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGINT)
        return  # 不会执行到这里
    print("\nReceived termination signal, exiting...")
    sys.exit(128 + signum)


def main() -> None:
    main_start = time.perf_counter()
    
    parser = argparse.ArgumentParser(
        description='Onyx Environment Checker and Launcher',
        epilog='Examples:\n'
               '  python Main.py                    # 正常启动Onyx\n'
               '  python Main.py -l                 # 登录模式启动\n'
               '  python Main.py -c "ls -la"       # 执行单个命令\n'
               '  python Main.py -c "cmd" -q       # 静默执行命令\n'
               '  python Main.py --skip-check      # 跳过环境检查直接启动\n'
               '  python Main.py --force-check      # 强制执行完整环境检查\n'
               '  python Main.py --clear-cache      # 清除永久缓存并检查\n'
               '  python Main.py --ultra-fast      # 极致启动模式（推荐）\n'
               '  python Main.py --perf-summary     # 显示性能汇总报告\n'
               '  python Main.py --reset-perf       # 重置性能统计数据'
    )
    parser.add_argument(
        '-l', '--login',
        action='store_true',
        help='Login mode: simulate login shell and load profile files'
    )
    parser.add_argument(
        '-c', '--command',
        type=str,
        help='Execute a single command with Onyx security'
    )
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Suppress non-error output in command mode'
    )
    parser.add_argument(
        '--skip-check',
        action='store_true',
        help='Skip environment check and directly launch Onyx'
    )
    parser.add_argument(
        '--force-check',
        action='store_true',
        help='Force full environment check (ignore cache)'
    )
    parser.add_argument(
        '--clear-cache',
        action='store_true',
        help='Clear permanent cache and force environment check'
    )
    parser.add_argument(
        '--ultra-fast',
        action='store_true',
        help='Ultra fast launch mode (use cache even if env changed)'
    )
    parser.add_argument(
        '--perf-summary',
        action='store_true',
        help='Show performance summary report'
    )
    parser.add_argument(
        '--reset-perf',
        action='store_true',
        help='Reset all performance statistics'
    )
    
    args = parser.parse_args()
    
    # 处理性能汇总请求
    if args.perf_summary:
        tracker = PerformanceTracker()
        # 尝试从文件加载历史数据
        if os.path.exists(PERFORMANCE_LOG_FILE):
            try:
                with open(PERFORMANCE_LOG_FILE, 'r', encoding='utf-8') as f:
                    history_data = json.load(f)
                    print(f"\n📊 历史性能数据 (来自 {PERFORMANCE_LOG_FILE}):")
                    print(f"   记录时间: {datetime.datetime.fromtimestamp(history_data.get('timestamp', 0)).strftime('%Y-%m-%d %H:%M:%S')}")
                    print(f"   总耗时: {history_data.get('total_time_ms', 0):.2f} ms")
            except:
                pass
        tracker.print_summary()
        sys.exit(0)
    
    if args.reset_perf:
        tracker = PerformanceTracker()
        tracker.reset()
        if os.path.exists(PERFORMANCE_LOG_FILE):
            os.remove(PERFORMANCE_LOG_FILE)
        print("✅ 性能统计数据已重置")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        with TimeIt("Main程序总执行时间"):
            checker = UltraFastEnvironmentChecker()
            
            # ========== 初始化用户主目录（在一切之前） ==========
            with TimeIt("初始化用户主目录"):
                if not checker.init_user_home_for_onyx(force_login_mode=args.login):
                    log_print(f"❌ {checker.t('home_init_failed')}", is_error=True)
                    sys.exit(1)
            # ==================================================

            # ── First-run gate ────────────────────────────────────────────
            #    If the flag file is missing this is a first launch (or the
            #    user cleared the cache).  Run the interactive setup wizard
            #    (language + AI config) followed by a thorough 8-stage
            #    environment check.  Both jump directly to the main program
            #    when finished; execution never reaches the routing below.
            if not os.path.exists(checker._first_run_flag_path()):
                with TimeIt("首次运行-交互配置"):
                    checker._first_time_setup_wizard()
                with TimeIt("首次运行-强制环境检测"):
                    checker.first_run_forced_check()
                # first_run_forced_check() calls jump_to_main_immediately()
                # internally and never returns; this line is unreachable.
                return
            # ────────────────────────────────────────────────────────────
            
            if args.clear_cache:
                with TimeIt("清除缓存"):
                    checker.clear_permanent_cache()
                    log_print("✅ 永久缓存和极致启动标记已清除")
            
            if args.login:
                with TimeIt("激活登录模式"):
                    login_success = checker.activate_login_mode()
                    if not login_success and checker.system_type == "Windows":
                        print("\n⚠️  Windows does not support login mode, please use bash/zsh (WSL)")
                        print("⚠️  Windows不支持登录模式，请使用bash/zsh（WSL）\n")
            
            if args.command:
                with TimeIt("执行单次命令"):
                    exit_code = checker.execute_single_command(args.command, args.quiet)
                    sys.exit(exit_code)
            elif args.skip_check:
                with TimeIt("跳过检查直接启动"):
                    checker.ultra_fast_check(skip_to_main=True)
            elif args.force_check:
                log_print(f"⚙️  {checker.t('ultra_fast_disabled')}")
                with TimeIt("强制执行完整检查"):
                    checker.ultra_fast_check(force_check=True)
            else:
                if args.ultra_fast:
                    with TimeIt("极致启动模式"):
                        checker.ultra_fast_check(force_check=False)
                else:
                    with TimeIt("标准启动模式"):
                        checker.ultra_fast_check()
            
    except Exception as e:
        log_print(f"Main检查器异常: {e}", is_error=True)
        try:
            onyx_abs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Onyx.py")
            subprocess.run([sys.executable, onyx_abs], check=False)
        except:
            pass
    
    all_cost = round((time.perf_counter() - main_start)*1000, 3)
    log_print(f"[极致优化] main函数全流程总耗时: {all_cost} ms")
    
    # 保存性能统计到文件
    save_performance_stats()
    
    # 可选：在退出前打印性能汇总（仅在开发/调试模式）
    if os.environ.get("ONYX_PERF_DEBUG", "0") == "1":
        tracker = PerformanceTracker()
        tracker.print_summary()


if __name__ == "__main__":
    # anti_sudo_check 已禁用（用户要求取消 sudo 检测）
    main()