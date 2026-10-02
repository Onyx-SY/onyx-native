# lib/terminal/input_lib.py
"""
输入处理核心模块 - 现代化增强版
提供命令输入、历史记录去重、上下键导航、路径补全等功能
支持彩色元数据、命令/子命令补全、绝对路径补全
新增：语法高亮、命令分隔符处理、补全问题修复
新增：实时错误标红、路径存在校验、引号匹配、变量高亮、频率排序、前缀历史搜索
新增：历史持久化（异步、无上限文件、内存限制1000条）
新增：命令缓存（异步）、Ctrl+上下键补全选择
修复：上下键历史导航前缀匹配及连续切换问题
修复：用户手动编辑后导航状态重置问题
修复：使用索引追踪历史位置，解决重复命令问题
修复：前缀导航时前缀固定不变
修复：命令频率更新问题，改为异步加载和保存
新增：多行命令输入支持（here document、if/fi、for/do 等），Pygments 语法高亮
新增：here-document 支持 #语法 切换（如 #python、#bash）
修复：多行命令历史导航显示问题，正确格式化换行
修复：历史导航中多行命令一次性完整显示，而非逐行显示
修复：多行输入模式下语法高亮和补全失效问题
修复：历史记录存储格式问题，使用实际换行符而非 ^J 转义
修复：历史导航显示 ^J 混乱问题，统一转义字符处理
新增：终端类型适配，加载 other_terminal_cmd.json
新增：CMD 多行输入支持（IF/FOR/ELSE 块结构）
新增：com_cmd.json 路径参数传递，支持选项和参数补全
新增：空格保留补全修复、鼠标支持（仅在补全菜单激活时接管）
新增：配置文件系统
修复：多行输入中语法切换被覆盖，始终使用同一补全器/高亮器
修复：嵌套多行结构误判，增加深度栈管理
新增：补全菜单翻页键绑定支持
修复：鼠标滚动过度接管问题，移除全局 mouse_support，仅保留键盘导航
修复：多行输入自动缩进逻辑，基于 Pygments 词法分析实现智能缩进
修复：多行模式下语法检测与切换，充分利用 SmartSyntaxDetector

修复（AST 语法检查专项）：
- 多行输入模式无条件使用 ml_input.kb（不再依赖 HAS_PYGMENTS），
  避免无 Pygments 环境下 Enter 直接提交续行、破坏多行输入
- CMD 多行输入同样使用 ml_input.kb 保证 Enter 语义一致
- _multiline_text_complete 的 Python 分支复用 ASTValidator.is_complete_python 严格模式

修复（PromptSession 缓存专项）：
- 命中缓存时，completer/lexer/kb/style/auto_suggest 全部从缓存 bundle 复用，
  不再「先建一遍再丢弃」——此前每次回车会白建一次 SmartCompleter，约 2~7ms
- ptk.json 的 mtime 参与缓存键，用户改配置后自动重建
- use_dropdown_menu 参与缓存键，并传给 create_key_bindings / PromptSession

新增（命令树专项）：
- _get_context 返回值扩展为 5 元组，追加 Optional[CommandNode]，
  供树驱动补全使用；ctx_type 新增 "tree" 分支

新增（动态命令补全专项 · 脚本式扩展）：
- SmartCompleter 构造时自动发现 cmd_com/*.py 与 ~/.cmd_com/*.py
  等目录下的用户 Python 脚本，为任意命令注册动态补全器
- 动态补全与 JSON 静态配置共存：动态优先、静态兜底、结果去重
- 构造完成后打印一行动态补全统计（命令数 / 脚本数 / 失败数）

修复（历史保真专项 · P0）：
- 历史多行编码改为「\x01 前缀 + JSON」，单行条目原样存储、原样读回，
  绝不再对单行命令做 ^J / \n / \r 的反转义（旧版兼容分支曾把
  `echo ^J done` 读成 `echo n done`、把字面 JSON 命令吞掉外壳）
- _clean_display_text 彻底停做「^J → 换行 / \\n → 换行」的替换，
  只清 ANSI 控制序列；多行显示完全依赖从 JSON 块还原的真实换行符
- _decode_multiline_from_storage 只解码「明确标记过」的多行条目
  (\x01 前缀 或 \x00 旧分隔符)，其它一律原样返回

修复（多行续行累积 · P2）：
- _process_multiline_input 中把每行 append 到 state.lines，
  使未闭合引号 / 行中未闭合括号等续行检查能看到完整上下文
"""

import os
import sys
import time
import uuid
import json
import threading
import re
import shlex
import queue
import shutil
import traceback
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Tuple, Union, Iterable

from prompt_toolkit import prompt, PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PromptStyle
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion, AutoSuggestFromHistory
from prompt_toolkit.history import History
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.validation import Validator, ValidationError

# ── PromptSession 复用缓存：prompt() 快捷函数每次调用都会重建会话（约 20~45ms），
#    按输入签名缓存后，提示符刷新不再付出会话构建开销。
#    bundle 里保存 (session, completer, lexer, kb, auto_suggest, comp_style)，
#    命中时整包复用；此前命中后仍然每帧重建 completer/kb/style 再丢弃，白费 ~2-7ms。
_SESSION_CACHE = {"key": None, "session": None, "bundle": None}

# 导入拆分的模块
from .kb import create_key_bindings
from .com import (
    set_history_highlight_token, clear_history_highlight_token,
    set_nav_reset_callback,
    get_path_cache,
    PathCache,
    SmartCompleter,
    CommandConfigLoader,
    CommandLexer,
    FirstSuggestionAutoSuggest,
    get_command_cache,
    get_command_freq,
    get_detected_terminal_type,
    set_terminal_type,
    set_other_terminal_cmds_path,
    get_other_terminal_cmds,
    set_com_cmd_config_path,
    get_com_cmd_config,
    get_posix_mode,
    COLORS,
    META_COLORS,
    META_TEXTS_EN,
    LANG_TEXTS,
    load_ptk_config,
    DEFAULT_PTK_CONFIG,
    PTK_CONFIG_PATH,
)

# 导入多行输入模块
from .mul_line import (
    handle_multiline_input,
    detect_syntax_from_command,
    MultiLineDetector,
    MultiLineState,
    MultiLineInput,
    MultiLineFormatter,
    SyntaxDetector,
    SyntaxType,
    SmartSyntaxDetector,
    ASTValidator,
    HAS_PYGMENTS,
)

# ===================== 全局变量 =====================
_HISTORY_BUFFER: List[str] = []          # 历史记录列表（按时间倒序，最新的在索引0）
_CURRENT_HISTORY_INDEX: int = -1          # 普通导航：当前在 _HISTORY_BUFFER 中的索引
_NAVIGATION_START_INPUT: str = ""         # 普通导航：开始导航时的原始输入
_HISTORY_INITIALIZED: bool = False
_CURRENT_LANG: str = "chinese"

# 前缀导航专用状态
_PREFIX_NAVIGATION_ACTIVE: bool = False   # 前缀导航是否激活
_PREFIX_VALUE: str = ""                   # 固定的前缀值（导航过程中不变）
_PREFIX_FILTERED_INDICES: List[int] = []  # 匹配前缀的历史记录在 _HISTORY_BUFFER 中的索引
_PREFIX_CURRENT_POS: int = -1             # 当前在 _PREFIX_FILTERED_INDICES 中的位置

# 当前导航到的原始历史条目（多行命令恢复用；None=未在导航中或用户已编辑）
_NAVIGATION_RAW_COMMAND: Optional[str] = None

# 虚影补全接受的多行命令（含 \n）：回车后以多行形式重放，不塞进单行缓冲区
_PENDING_MULTILINE_RECALL: Optional[str] = None

# 多行输入状态
_MULTILINE_STATE: Optional[MultiLineState] = None  # 当前多行输入状态
_MULTILINE_BUFFER: List[str] = []                  # 多行输入缓冲
_MULTILINE_ACTIVE: bool = False                    # 多行输入是否激活
_MULTILINE_ABORTED: bool = False                   # 多行输入被取消/中断（防止把首行残片写入历史）

# 虚拟根目录（从主程序注入）
_VIRTUAL_ROOT: str = ""

# 命令补全配置缓存
_CMD_CONFIG_CACHE: Dict[str, Dict] = {}

# 有效命令集合（用于实时校验）
_VALID_COMMANDS: set = set()

# 用户主目录（用于持久化）
_USER_HOME_DIR: str = ""

# 历史持久化配置
_HISTORY_FILE_NAME = ".onyx_history.txt"
_HISTORY_MAX_MEMORY = 1000          # 内存中保留条数
_HISTORY_MAX_FILE = 50000           # 文件保留最大行数（后台整理）

# 历史文件中多行命令的分隔符（旧版只读兼容）
_HISTORY_MULTILINE_SEPARATOR = "\x00"
# 新版多行条目在文件中的行首标记（SOH，正常命令里不会出现）
_MULTILINE_MARKER = "\x01"

# 异步写入队列和线程
_history_write_queue = queue.Queue()
_history_writer_thread: Optional[threading.Thread] = None
_history_writer_stop = threading.Event()

# 新增：终端类型检测
_TERMINAL_TYPE: str = ""

# com_cmd.json 配置路径
_COM_CMD_CONFIG_PATH: str = ""

# ptk 配置（从 ~/.config/onyx/ptk.json 加载）
_ptk_config: Dict[str, Any] = {}

# 元信息文本
META_TEXTS = META_TEXTS_EN


def _ensure_ptk_config() -> None:
    global _ptk_config
    # 2026-09 修复：禁用 prompt_toolkit 的光标位置查询（CPR）。
    # Onyx 的多行 prompt 样式会让 ptk 在渲染时发送 ESC[6n 并等待 ESC[row;colR 应答；
    # 部分终端（如 TBS 模拟器）应答超时后才到达，`1;1R` 残余会落进输入缓冲，
    # 显示为 ";1R" 并在回车后被当作命令提交给 bash（syntax error near `;'）。
    # prompt_toolkit 官方开关 PROMPT_TOOLKIT_NO_CPR=1 会让 responds_to_cpr 恒为 False，
    # 渲染层彻底不再发起 CPR 请求。
    os.environ["PROMPT_TOOLKIT_NO_CPR"] = "1"
    # load_ptk_config 内部已按 mtime 缓存，不再每次读文件
    _ptk_config = load_ptk_config()

    # 应用配置覆盖
    global _HISTORY_MAX_MEMORY, _HISTORY_MAX_FILE, _HISTORY_FILE_NAME
    history_cfg = _ptk_config.get("history", {})
    if "memory_limit" in history_cfg:
        _HISTORY_MAX_MEMORY = history_cfg["memory_limit"]
    if "file_limit" in history_cfg:
        _HISTORY_MAX_FILE = history_cfg["file_limit"]
    if "file_name" in history_cfg:
        _HISTORY_FILE_NAME = history_cfg["file_name"]


def set_language(lang: str) -> None:
    """设置语言"""
    global _CURRENT_LANG, META_TEXTS
    if lang.lower() in ["chinese", "english"]:
        _CURRENT_LANG = lang.lower()
        META_TEXTS = META_TEXTS_EN


def get_text(key: str) -> str:
    """获取本地化文本"""
    return LANG_TEXTS.get(_CURRENT_LANG, LANG_TEXTS["chinese"]).get(key, key)


def set_virtual_root(root: str) -> None:
    global _VIRTUAL_ROOT
    _VIRTUAL_ROOT = root


def get_virtual_root() -> str:
    return _VIRTUAL_ROOT


def set_valid_commands(cmds: Iterable[str]) -> None:
    global _VALID_COMMANDS
    _VALID_COMMANDS = set(cmds)


def set_user_home_dir(home_dir: str) -> None:
    global _USER_HOME_DIR
    _USER_HOME_DIR = home_dir
    history_file = os.path.join(home_dir, _HISTORY_FILE_NAME) if home_dir else None
    freq_mgr = get_command_freq(home_dir, history_file)
    freq_mgr.set_user_home_dir(home_dir, history_file)


def set_com_cmd_config_path_from_root(root_dir: str) -> None:
    """
    根据虚拟根目录设置 com_cmd.json 路径
    """
    global _COM_CMD_CONFIG_PATH
    _COM_CMD_CONFIG_PATH = os.path.join(root_dir, "onyx", "etc", "com_cmd.json")
    # 同时设置到 com 模块
    set_com_cmd_config_path(_COM_CMD_CONFIG_PATH)


def get_com_cmd_config_path() -> str:
    """获取 com_cmd.json 路径"""
    global _COM_CMD_CONFIG_PATH
    return _COM_CMD_CONFIG_PATH


def detect_and_set_terminal_type() -> str:
    """检测并设置终端类型，同时加载对应的命令"""
    global _TERMINAL_TYPE
    _TERMINAL_TYPE = get_detected_terminal_type()
    return _TERMINAL_TYPE


def get_terminal_type() -> str:
    """获取当前终端类型"""
    global _TERMINAL_TYPE
    if not _TERMINAL_TYPE:
        _TERMINAL_TYPE = get_detected_terminal_type()
    return _TERMINAL_TYPE


def _get_terminal_specific_commands() -> List[str]:
    """从 other_terminal_cmd.json 获取当前终端的内置命令。

    跨平台策略：
    - 基础：把当前 terminal_type 对应的节全部纳入
    - PowerShell：额外并入 'cmd' 节（dir/type/where/findstr/cls 等
      在 PowerShell 里也能用，且用户从 cmd 过渡过来时常敲）
    - 若运行在 macOS（sys.platform == 'darwin'）：额外并入 'macos' 节
      （open / pbcopy / pbpaste / brew / defaults / say 等）
    - 'common' 节永远纳入
    - 结果去重保序，避免重复候选浪费补全列表空间
    """
    import sys as _sys

    terminal_type = get_terminal_type()
    all_cmds = get_other_terminal_cmds()

    commands: List[str] = []

    # 当前终端专属
    if terminal_type in all_cmds:
        commands.extend(all_cmds[terminal_type])

    # PowerShell：并入 cmd 一节的常用命令
    if terminal_type == 'powershell' and 'cmd' in all_cmds:
        commands.extend(all_cmds['cmd'])

    # macOS：并入 macos 一节
    if _sys.platform == 'darwin' and 'macos' in all_cmds:
        commands.extend(all_cmds['macos'])

    # 通用
    if 'common' in all_cmds:
        commands.extend(all_cmds['common'])

    # 去重保序
    seen = set()
    result: List[str] = []
    for c in commands:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result


# ===================== 异步历史持久化系统 =====================
def _get_history_file_path() -> str:
    if _USER_HOME_DIR:
        return os.path.join(_USER_HOME_DIR, _HISTORY_FILE_NAME)
    return os.path.join(str(Path.home()), _HISTORY_FILE_NAME)


def _start_history_writer():
    global _history_writer_thread, _history_writer_stop
    if _history_writer_thread and _history_writer_thread.is_alive():
        return
    _history_writer_stop.clear()
    _history_writer_thread = threading.Thread(target=_history_writer_loop, daemon=True)
    _history_writer_thread.start()


def _encode_multiline_for_storage(cmd: str) -> str:
    """把命令编码为历史文件中的一行。

    多行命令：以 \\x01 打头，后接 JSON（{"cmd": "..."}）。JSON 会把真实换行
    转义成 \\n，文件仍是纯文本、单行可读；解码时严格凭 \\x01 前缀还原。
    单行命令：原样存储 —— 用户敲进什么，文件里就是什么。
    """
    if '\n' in cmd:
        return _MULTILINE_MARKER + json.dumps({"cmd": cmd}, ensure_ascii=False)
    return cmd


def _decode_multiline_from_storage(line: str) -> str:
    """把历史文件中的一行解码。

    只认两种「明确标记过」的多行条目：
        1. \\x01 前缀 + JSON（新版）
        2. \\x00 分隔符（旧版，该字节不会出现在正常单行命令中）

    其它一律原样返回 —— 用户命令里的字面 ^J、\\n、
    {"multiline": true, ...} 必须逐字符保留，绝不能被反转义。
    """
    if not line:
        return line

    # 新版：\x01 + JSON
    if line.startswith(_MULTILINE_MARKER):
        payload = line[len(_MULTILINE_MARKER):]
        try:
            data = json.loads(payload)
            if isinstance(data, dict) and isinstance(data.get("cmd"), str):
                return data["cmd"]
        except (json.JSONDecodeError, ValueError):
            pass
        # JSON 损坏 → 去掉前缀原样返回，至少不丢内容
        return payload

    # 旧版：\x00 分隔符
    if _HISTORY_MULTILINE_SEPARATOR in line:
        return line.replace(_HISTORY_MULTILINE_SEPARATOR, '\n')

    # 单行条目：原样返回
    return line


# ANSI 转义序列正则：CSI（\x1b[...m 等）、OSC（\x1b]...\x07）、单字符 ESC 序列
_ANSI_ESCAPE_RE = re.compile(
    r'\x1b\[[0-9;?]*[ -/]*[@-~]'
    r'|\x1b\][^\x07\x1b]*(\x07|\x1b\\)'
    r'|\x1b[@-Z\\-_]'
)


def _clean_display_text(cmd: str, decode_escapes: bool = True) -> str:
    """只清理 ANSI 控制序列，不再对命令内容做任何改写。

    历史多行显示完全依赖 _decode_multiline_from_storage 从 JSON 块还原出的
    真实换行符；这里不再做 ^J / \\n / \\r / \\t 的替换，避免把用户命令
    （如 `echo ^J done` 或 `printf "a\\nb"`）静默改写成另一条命令。

    decode_escapes 参数保留仅为兼容旧调用点，实际不再生效。
    """
    if not cmd:
        return cmd
    result = _ANSI_ESCAPE_RE.sub('', cmd)
    result = result.replace('^[', '')
    return result


# ── 终端控制序列泄漏过滤 ──
# CPR 应答（ESC[row;colR 或其残余 row;colR）、鼠标上报、OSC 标题等，可能绕过
# prompt_toolkit 落进输入缓冲；仅当整串剥离后无可见字符时才判为纯噪声丢弃。
_CSI_NOISE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_NOISE_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ESC_NOISE_RE = re.compile(r"\x1b[@-Z\\-_]")
_CPR_RESIDUE_RE = re.compile(r"(?:\x1b\[)?[0-9]*;[0-9]+R")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_control_noise(text: str) -> str:
    """
    过滤终端控制序列泄漏（CPR 应答残余、鼠标上报、OSC 等）。

    仅当整串在剥离控制序列后已无任何可见字符时才判定为“纯噪声”并返回空串，
    否则原样返回 —— 保证 printf 等含普通字符的正常命令不受影响。
    """
    if not text:
        return text
    stripped = text
    for pat in (_CSI_NOISE_RE, _OSC_NOISE_RE, _ESC_NOISE_RE, _CPR_RESIDUE_RE):
        stripped = pat.sub("", stripped)
    stripped = _CTRL_CHARS_RE.sub("", stripped)
    if stripped.strip() == "":
        return ""
    return text


def _history_writer_loop():
    batch = []
    file_path = _get_history_file_path()
    last_flush = time.time()
    while not _history_writer_stop.is_set():
        try:
            cmd = _history_write_queue.get(timeout=0.5)
            batch.append(cmd)
            while True:
                try:
                    cmd = _history_write_queue.get_nowait()
                    batch.append(cmd)
                except queue.Empty:
                    break
        except queue.Empty:
            pass

        now = time.time()
        if batch and (len(batch) >= 10 or now - last_flush > 2.0):
            try:
                with open(file_path, 'a', encoding='utf-8') as f:
                    for cmd in batch:
                        encoded = _encode_multiline_for_storage(cmd)
                        f.write(encoded + '\n')
                batch.clear()
                last_flush = now
                threading.Thread(target=_trim_history_file, daemon=True).start()
            except Exception:
                pass


def _trim_history_file():
    file_path = _get_history_file_path()
    max_lines = _HISTORY_MAX_FILE
    try:
        if not os.path.exists(file_path):
            return
        with open(file_path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            block_size = 8192
            data = []
            remaining = file_size
            while remaining > 0:
                seek_size = min(block_size, remaining)
                f.seek(remaining - seek_size, os.SEEK_SET)
                chunk = f.read(seek_size)
                data.insert(0, chunk)
                remaining -= seek_size
            content = b''.join(data).decode('utf-8', errors='ignore')
            all_lines = content.splitlines()
            lines_to_keep = all_lines[-max_lines:] if len(all_lines) > max_lines else all_lines
        if len(all_lines) > max_lines:
            temp_file = file_path + '.tmp'
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines_to_keep) + '\n')
            os.replace(temp_file, file_path)
    except Exception:
        pass


def _load_history_buffer() -> List[str]:
    """加载历史记录，正确解码多行命令，单行条目原样保留。"""
    file_path = _get_history_file_path()
    old_json_path = os.path.join(os.path.dirname(file_path), ".prompt_onyx_cmd_history.json")

    if not os.path.exists(file_path) and os.path.exists(old_json_path):
        try:
            with open(old_json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    migrated = []
                    for item in data:
                        if isinstance(item, str) and item.strip():
                            migrated.append(item)
                    if migrated:
                        with open(file_path, 'w', encoding='utf-8') as nf:
                            for cmd in migrated:
                                encoded = _encode_multiline_for_storage(cmd)
                                nf.write(encoded + '\n')
        except Exception:
            pass

    try:
        if not os.path.exists(file_path):
            return []

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            all_lines = f.readlines()

        if len(all_lines) > _HISTORY_MAX_MEMORY:
            all_lines = all_lines[-_HISTORY_MAX_MEMORY:]

        result = []
        for line in reversed(all_lines):
            line = line.rstrip('\n').rstrip('\r')
            if not line:
                continue
            decoded = _decode_multiline_from_storage(line)
            cleaned = _clean_display_text(decoded)
            result.append(cleaned)

        return result
    except Exception:
        return []


def _save_history_buffer_async(cmd: str):
    global _history_writer_thread
    if not cmd:
        return
    _start_history_writer()
    _history_write_queue.put(cmd)


# ===================== prompt_toolkit 历史桥接 =====================
class OnyxHistory(History):
    """
    把项目自己的历史缓冲（_HISTORY_BUFFER，来自 .onyx_history.txt）桥接给 prompt_toolkit，
    使 Ctrl+R 反向增量搜索能覆盖【跨会话】的完整历史。

    背景：此前 PromptSession 未传 history=，ptk 用的是空的 InMemoryHistory，
    导致 Ctrl+R 只能搜到“本次进程运行期间”输入过的命令，搜不到历史文件里的旧命令
    （↑/↓ 走的是项目自己的 _HISTORY_BUFFER，因此不受影响）。

    - load_history_strings：ptk 要求 oldest-first，而 _HISTORY_BUFFER 最新在前 → 反转。
    - store_string：故意不落盘 —— 落盘由既有的 add_to_history / _save_history_buffer_async
      管线负责，避免重复写入；ptk 仍会把新命令追加进 buffer._working_lines 供搜索。
    """

    def load_history_strings(self):
        try:
            for cmd in reversed(list(_HISTORY_BUFFER)):
                if cmd:
                    yield cmd
        except Exception:
            return

    def store_string(self, string: str) -> None:
        # 持久化交给项目既有管线，这里不做任何事（否则会重复写历史文件）
        return


_PTK_HISTORY = OnyxHistory()


def _detect_editor() -> Optional[str]:
    """
    探测可用的终端编辑器路径，供 Ctrl+X Ctrl+E（外部编辑器）使用。

    prompt_toolkit 的内置回退表是硬编码的 /usr/bin/*，在 Termux/Android 上并不存在，
    因此这里主动解析一个真实路径并通过 $EDITOR 告知 ptk。
    """
    for env in ("VISUAL", "EDITOR"):
        val = os.environ.get(env)
        if val:
            try:
                if shutil.which(val.split()[0]):
                    return val
            except Exception:
                pass
    for cand in ("nano", "vi", "vim", "micro", "emacs"):
        path = shutil.which(cand)
        if path:
            return path
    return None


# ===================== 历史导航（修复版 - 使用索引追踪） =====================
def init_history_navigation() -> None:
    """初始化历史导航，并触发命令频率管理器从历史文件中预加载"""
    global _HISTORY_BUFFER, _CURRENT_HISTORY_INDEX, _NAVIGATION_START_INPUT, _HISTORY_INITIALIZED
    global _PREFIX_NAVIGATION_ACTIVE, _PREFIX_VALUE, _PREFIX_FILTERED_INDICES, _PREFIX_CURRENT_POS

    if not _HISTORY_INITIALIZED:
        # ⚠️ 必须「就地」写入：SmartCompleter 持有 _HISTORY_BUFFER 的引用，
        # 一旦重新绑定（= 换成新 list），被缓存 PromptSession 里的补全器就永远
        # 停在旧对象上 —— 表现为「虚影只认历史文件里的旧命令，当前会话新命令补不出来」。
        # _load_history_buffer 已按新格式严格解码并只清 ANSI，这里不再二次处理。
        _loaded = _load_history_buffer()
        _HISTORY_BUFFER[:] = _loaded

    _CURRENT_HISTORY_INDEX = -1
    _NAVIGATION_START_INPUT = ""
    _PREFIX_NAVIGATION_ACTIVE = False
    _PREFIX_VALUE = ""
    _PREFIX_FILTERED_INDICES = []
    _PREFIX_CURRENT_POS = -1
    _HISTORY_INITIALIZED = True

    # 注册导航重置回调：lexer 自动清除高亮时同步重置导航状态
    set_nav_reset_callback(reset_history_index)

    if _USER_HOME_DIR:
        history_file = os.path.join(_USER_HOME_DIR, _HISTORY_FILE_NAME)
        freq_mgr = get_command_freq(_USER_HOME_DIR, history_file)


def add_to_history(cmd: str) -> bool:
    """添加命令到历史记录。

    修复：不再对用户输入做 ^J / \\n 的“兼容”替换。历史多行存储走
    _encode_multiline_for_storage 的 \\x01 + JSON 路径，这里必须原样保存，
    否则用户敲 `echo ^J done` 会被改写成另一条命令。
    """
    global _HISTORY_BUFFER
    cmd_stripped = cmd.strip()
    if not cmd_stripped:
        return False

    if _HISTORY_BUFFER and _HISTORY_BUFFER[0] == cmd_stripped:
        return False

    if cmd_stripped in _HISTORY_BUFFER:
        _HISTORY_BUFFER.remove(cmd_stripped)

    _HISTORY_BUFFER.insert(0, cmd_stripped)

    # ⚠️ 就地截断，绝不能重新绑定：SmartCompleter 持有本列表的引用，
    # 一旦换成新 list，补全器就停在旧对象上 → 当前会话新命令再也补不出虚影
    # （历史文件满载 1000 条时首条新命令即触发，用户侧表现为「只认旧会话命令」）。
    if len(_HISTORY_BUFFER) > _HISTORY_MAX_MEMORY:
        del _HISTORY_BUFFER[_HISTORY_MAX_MEMORY:]

    _save_history_buffer_async(cmd_stripped)

    freq_manager = get_command_freq(
        _USER_HOME_DIR,
        os.path.join(_USER_HOME_DIR, _HISTORY_FILE_NAME) if _USER_HOME_DIR else None
    )
    freq_manager.record(cmd_stripped)
    return True


def _find_history_index_by_content(content: str, start_from: int = 0) -> int:
    for i in range(start_from, len(_HISTORY_BUFFER)):
        if _HISTORY_BUFFER[i] == content:
            return i
    return -1


def _build_prefix_filtered_indices(prefix: str) -> List[int]:
    """基于 token 匹配（任意位置子串）+ 按命令文本去重 — 用于 Up/Down 裸键"""
    indices = []
    seen = set()
    for i, cmd in enumerate(_HISTORY_BUFFER):
        if prefix in cmd and cmd not in seen:
            seen.add(cmd)
            indices.append(i)
    return indices


def _build_strict_prefix_filtered_indices(prefix: str) -> List[int]:
    """严格前缀匹配 + 按命令文本去重 — 用于 Alt+Up/Down"""
    indices = []
    seen = set()
    for i, cmd in enumerate(_HISTORY_BUFFER):
        if cmd.startswith(prefix) and cmd not in seen:
            seen.add(cmd)
            indices.append(i)
    return indices


# ── 历史导航匹配信息（供 kb.py 底部工具栏使用）──
_NAV_MATCH_INFO: str = ""


def _get_nav_match_info() -> str:
    """返回当前历史导航的匹配信息，供底部工具栏展示"""
    return _NAV_MATCH_INFO


def _set_nav_match_info(token: str, current: int, total: int) -> None:
    """设置历史导航匹配信息"""
    global _NAV_MATCH_INFO
    if token and total > 0:
        _NAV_MATCH_INFO = f"🔍 \"{token}\" — {current}/{total}"
    else:
        _NAV_MATCH_INFO = ""


def _format_history_for_display(cmd: str) -> str:
    """返回历史条目在缓冲区中的回填文本（导航显示与继续导航比较统一使用）。

    多行命令直接保留真实换行：ptk 会把含 \\n 的缓冲区按多行渲染，
    不再压成一行 —— 用户能直观看到多行结构；同时缓冲区内容与历史原始
    命令完全一致，回车后原样提交执行，不会出现“只识别第一行”的内容丢失。
    这里只负责清理 ANSI 残留，不再做任何内容改写。
    """
    if not cmd:
        return cmd
    return _clean_display_text(cmd)


def handle_up_arrow_normal(current_input: str) -> Tuple[str, int]:
    """处理普通 Up 键：空输入→线性遍历全部历史；有文字→子串匹配筛选 + ANSI 反色高亮"""
    global _CURRENT_HISTORY_INDEX, _NAVIGATION_START_INPUT, _HISTORY_BUFFER, _NAVIGATION_RAW_COMMAND
    global _PREFIX_NAVIGATION_ACTIVE, _PREFIX_VALUE, _PREFIX_FILTERED_INDICES, _PREFIX_CURRENT_POS

    if not _HISTORY_BUFFER:
        return current_input, len(current_input)

    _token = current_input.strip()

    # ── 已在导航中 + 用户未手动编辑 → 继续当前模式 ──
    if _CURRENT_HISTORY_INDEX != -1:
        if _CURRENT_HISTORY_INDEX < len(_HISTORY_BUFFER) and current_input == _format_history_for_display(_HISTORY_BUFFER[_CURRENT_HISTORY_INDEX]):
            if _PREFIX_NAVIGATION_ACTIVE and _PREFIX_FILTERED_INDICES:
                # 继续子串筛选（高亮用原始 token）
                set_history_highlight_token(_PREFIX_VALUE)
                if _PREFIX_CURRENT_POS < len(_PREFIX_FILTERED_INDICES) - 1:
                    _PREFIX_CURRENT_POS += 1
                else:
                    return current_input, len(current_input)
                _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]]
                formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
                return formatted, len(formatted)
            else:
                # 继续线性遍历
                if _CURRENT_HISTORY_INDEX < len(_HISTORY_BUFFER) - 1:
                    _CURRENT_HISTORY_INDEX += 1
                else:
                    return current_input, len(current_input)
                _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_CURRENT_HISTORY_INDEX]
                formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
                return formatted, len(formatted)
        else:
            # 用户手动编辑了 → 重置所有状态
            _CURRENT_HISTORY_INDEX = -1
            _NAVIGATION_START_INPUT = ""
            _NAVIGATION_RAW_COMMAND = None
            _PREFIX_NAVIGATION_ACTIVE = False
            _PREFIX_VALUE = ""
            _PREFIX_FILTERED_INDICES = []
            _PREFIX_CURRENT_POS = -1

    # ── 全新开始 ──
    if not _token:
        # 空输入 → 原版线性遍历
        clear_history_highlight_token()

        if _CURRENT_HISTORY_INDEX == -1:
            _NAVIGATION_START_INPUT = current_input
            idx = _find_history_index_by_content(current_input)
            if idx != -1 and idx < len(_HISTORY_BUFFER) - 1:
                _CURRENT_HISTORY_INDEX = idx + 1
            else:
                _CURRENT_HISTORY_INDEX = 0
        else:
            if _CURRENT_HISTORY_INDEX < len(_HISTORY_BUFFER) - 1:
                _CURRENT_HISTORY_INDEX += 1
            else:
                return current_input, len(current_input)
        _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_CURRENT_HISTORY_INDEX]
        formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
        return formatted, len(formatted)

    # 有文字 → filtered-list 子串匹配
    if not _PREFIX_NAVIGATION_ACTIVE:
        _PREFIX_NAVIGATION_ACTIVE = True
        _PREFIX_VALUE = _token
        _PREFIX_FILTERED_INDICES = _build_prefix_filtered_indices(_token)
        set_history_highlight_token(_PREFIX_VALUE)

        if not _PREFIX_FILTERED_INDICES:
            _NAVIGATION_RAW_COMMAND = None
            return current_input, len(current_input)

        _PREFIX_CURRENT_POS = 0
        # 如果当前输入恰好是第一个匹配，且还有更多匹配 → 跳到第二个（用格式化文本比较）
        if _format_history_for_display(_HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[0]]) == current_input and len(_PREFIX_FILTERED_INDICES) > 1:
            _PREFIX_CURRENT_POS = 1

        _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]]
        formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
        return formatted, len(formatted)

    # 继续筛选列表（高亮始终用原始搜索 token _PREFIX_VALUE，而非当前缓冲区文字）
    set_history_highlight_token(_PREFIX_VALUE)
    if _PREFIX_CURRENT_POS < len(_PREFIX_FILTERED_INDICES) - 1:
        _PREFIX_CURRENT_POS += 1
        _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]]
        formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
        return formatted, len(formatted)
    _NAVIGATION_RAW_COMMAND = None
    return current_input, len(current_input)


def handle_down_arrow_normal(current_input: str) -> Tuple[str, int]:
    """处理普通 Down 键：空输入→线性遍历全部历史（反向）；有文字→子串匹配筛选（反向）"""
    global _CURRENT_HISTORY_INDEX, _NAVIGATION_START_INPUT, _HISTORY_BUFFER, _NAVIGATION_RAW_COMMAND
    global _PREFIX_NAVIGATION_ACTIVE, _PREFIX_VALUE, _PREFIX_FILTERED_INDICES, _PREFIX_CURRENT_POS

    if not _HISTORY_BUFFER:
        return current_input, len(current_input)

    _token = current_input.strip()

    # ── 已在导航中 + 用户未手动编辑 → 继续当前模式 ──
    if _CURRENT_HISTORY_INDEX != -1:
        if _CURRENT_HISTORY_INDEX < len(_HISTORY_BUFFER) and current_input == _format_history_for_display(_HISTORY_BUFFER[_CURRENT_HISTORY_INDEX]):
            if _PREFIX_NAVIGATION_ACTIVE and _PREFIX_FILTERED_INDICES:
                set_history_highlight_token(_PREFIX_VALUE)
                if _PREFIX_CURRENT_POS > 0:
                    _PREFIX_CURRENT_POS -= 1
                elif _PREFIX_CURRENT_POS == 0:
                    _PREFIX_NAVIGATION_ACTIVE = False
                    _PREFIX_VALUE = ""
                    _PREFIX_FILTERED_INDICES = []
                    _PREFIX_CURRENT_POS = -1
                    _NAVIGATION_RAW_COMMAND = None
                    clear_history_highlight_token()
                    return current_input, len(current_input)
                else:
                    return current_input, len(current_input)
                _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]]
                formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
                return formatted, len(formatted)
            else:
                if _CURRENT_HISTORY_INDEX > 0:
                    _CURRENT_HISTORY_INDEX -= 1
                elif _CURRENT_HISTORY_INDEX == 0:
                    _CURRENT_HISTORY_INDEX = -1
                    original = _NAVIGATION_START_INPUT
                    _NAVIGATION_START_INPUT = ""
                    _NAVIGATION_RAW_COMMAND = None
                    return original, len(original)
                else:
                    return current_input, len(current_input)
                _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_CURRENT_HISTORY_INDEX]
                formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
                return formatted, len(formatted)
        else:
            _CURRENT_HISTORY_INDEX = -1
            _NAVIGATION_START_INPUT = ""
            _NAVIGATION_RAW_COMMAND = None
            _PREFIX_NAVIGATION_ACTIVE = False
            _PREFIX_VALUE = ""
            _PREFIX_FILTERED_INDICES = []
            _PREFIX_CURRENT_POS = -1

    # ── 全新开始 ──
    if not _token:
        clear_history_highlight_token()
        if _CURRENT_HISTORY_INDEX == -1:
            _NAVIGATION_RAW_COMMAND = None
            return current_input, len(current_input)
        if _CURRENT_HISTORY_INDEX < len(_HISTORY_BUFFER):
            if current_input != _format_history_for_display(_HISTORY_BUFFER[_CURRENT_HISTORY_INDEX]):
                _CURRENT_HISTORY_INDEX = -1
                _NAVIGATION_START_INPUT = ""
                _NAVIGATION_RAW_COMMAND = None
                return current_input, len(current_input)
        if _CURRENT_HISTORY_INDEX > 0:
            _CURRENT_HISTORY_INDEX -= 1
            _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_CURRENT_HISTORY_INDEX]
            formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
            return formatted, len(formatted)
        elif _CURRENT_HISTORY_INDEX == 0:
            _CURRENT_HISTORY_INDEX = -1
            original = _NAVIGATION_START_INPUT
            _NAVIGATION_START_INPUT = ""
            _NAVIGATION_RAW_COMMAND = None
            return original, len(original)
        return current_input, len(current_input)

    # 有文字 → filtered-list 子串匹配（反向）
    if not _PREFIX_NAVIGATION_ACTIVE:
        _PREFIX_NAVIGATION_ACTIVE = True
        _PREFIX_VALUE = _token
        _PREFIX_FILTERED_INDICES = _build_prefix_filtered_indices(_token)
        set_history_highlight_token(_PREFIX_VALUE)

        if not _PREFIX_FILTERED_INDICES:
            _NAVIGATION_RAW_COMMAND = None
            return current_input, len(current_input)

        _PREFIX_CURRENT_POS = len(_PREFIX_FILTERED_INDICES) - 1
        _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]]
        formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
        return formatted, len(formatted)

    # 继续筛选列表（高亮始终用原始 token）
    set_history_highlight_token(_PREFIX_VALUE)
    if _PREFIX_CURRENT_POS > 0:
        _PREFIX_CURRENT_POS -= 1
        _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]]
        formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
        return formatted, len(formatted)
    elif _PREFIX_CURRENT_POS == 0:
        _PREFIX_NAVIGATION_ACTIVE = False
        _PREFIX_VALUE = ""
        _PREFIX_FILTERED_INDICES = []
        _PREFIX_CURRENT_POS = -1
        _NAVIGATION_RAW_COMMAND = None
        clear_history_highlight_token()
        return current_input, len(current_input)
    return current_input, len(current_input)


def handle_up_arrow_with_prefix(current_input: str) -> Tuple[str, int]:
    """处理 Alt+Up：基于前缀的历史导航"""
    global _HISTORY_BUFFER, _NAVIGATION_RAW_COMMAND
    global _CURRENT_HISTORY_INDEX, _NAVIGATION_START_INPUT
    global _PREFIX_NAVIGATION_ACTIVE, _PREFIX_VALUE, _PREFIX_FILTERED_INDICES, _PREFIX_CURRENT_POS

    _CURRENT_HISTORY_INDEX = -1
    _NAVIGATION_START_INPUT = ""

    # 如果当前 PREFIX 状态是普通 Up/Down 留下的（token 是子串而非前缀），重置
    if _PREFIX_NAVIGATION_ACTIVE and _PREFIX_VALUE and not current_input.startswith(_PREFIX_VALUE):
        _PREFIX_NAVIGATION_ACTIVE = False
        _PREFIX_VALUE = ""
        _PREFIX_FILTERED_INDICES = []
        _PREFIX_CURRENT_POS = -1
        _NAVIGATION_RAW_COMMAND = None

    if not _PREFIX_NAVIGATION_ACTIVE:
        prefix = current_input.strip()
        if not prefix:
            return handle_up_arrow_normal(current_input)

        _PREFIX_NAVIGATION_ACTIVE = True
        _PREFIX_VALUE = prefix
        _PREFIX_FILTERED_INDICES = _build_strict_prefix_filtered_indices(prefix)

        if not _PREFIX_FILTERED_INDICES:
            _NAVIGATION_RAW_COMMAND = None
            return current_input, len(current_input)

        for pos, idx in enumerate(_PREFIX_FILTERED_INDICES):
            if _format_history_for_display(_HISTORY_BUFFER[idx]) == current_input:
                if pos < len(_PREFIX_FILTERED_INDICES) - 1:
                    _PREFIX_CURRENT_POS = pos + 1
                    _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]]
                    formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
                    return formatted, len(formatted)
                else:
                    _PREFIX_CURRENT_POS = pos
                    _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[idx]
                    return current_input, len(current_input)

        _PREFIX_CURRENT_POS = 0
        _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[0]]
        formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
        return formatted, len(formatted)

    if _PREFIX_CURRENT_POS >= 0 and _PREFIX_CURRENT_POS < len(_PREFIX_FILTERED_INDICES):
        expected_idx = _PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]
        expected_cmd = _HISTORY_BUFFER[expected_idx]
        if current_input != _format_history_for_display(expected_cmd):
            if current_input.startswith(_PREFIX_VALUE):
                for pos, idx in enumerate(_PREFIX_FILTERED_INDICES):
                    if _format_history_for_display(_HISTORY_BUFFER[idx]) == current_input:
                        _PREFIX_CURRENT_POS = pos
                        _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[idx]
                        break
            else:
                _PREFIX_NAVIGATION_ACTIVE = False
                _PREFIX_VALUE = ""
                _PREFIX_FILTERED_INDICES = []
                _PREFIX_CURRENT_POS = -1
                _NAVIGATION_RAW_COMMAND = None
                clear_history_highlight_token()
                return current_input, len(current_input)

    if _PREFIX_CURRENT_POS < len(_PREFIX_FILTERED_INDICES) - 1:
        _PREFIX_CURRENT_POS += 1
        _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]]
        formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
        return formatted, len(formatted)
    else:
        return current_input, len(current_input)


def handle_down_arrow_with_prefix(current_input: str) -> Tuple[str, int]:
    """处理 Alt+Down：基于前缀的历史导航（反向）"""
    global _HISTORY_BUFFER, _NAVIGATION_RAW_COMMAND
    global _CURRENT_HISTORY_INDEX, _NAVIGATION_START_INPUT
    global _PREFIX_NAVIGATION_ACTIVE, _PREFIX_VALUE, _PREFIX_FILTERED_INDICES, _PREFIX_CURRENT_POS

    _CURRENT_HISTORY_INDEX = -1
    _NAVIGATION_START_INPUT = ""

    if _PREFIX_NAVIGATION_ACTIVE and _PREFIX_VALUE and not current_input.startswith(_PREFIX_VALUE):
        _PREFIX_NAVIGATION_ACTIVE = False
        _PREFIX_VALUE = ""
        _PREFIX_FILTERED_INDICES = []
        _PREFIX_CURRENT_POS = -1
        _NAVIGATION_RAW_COMMAND = None

    if not _PREFIX_NAVIGATION_ACTIVE:
        prefix = current_input.strip()
        if not prefix:
            _NAVIGATION_RAW_COMMAND = None
            return current_input, len(current_input)

        _PREFIX_NAVIGATION_ACTIVE = True
        _PREFIX_VALUE = prefix
        _PREFIX_FILTERED_INDICES = _build_strict_prefix_filtered_indices(prefix)

        if not _PREFIX_FILTERED_INDICES:
            _NAVIGATION_RAW_COMMAND = None
            return current_input, len(current_input)

        _PREFIX_CURRENT_POS = len(_PREFIX_FILTERED_INDICES) - 1
        _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]]
        formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
        return formatted, len(formatted)

    if _PREFIX_CURRENT_POS >= 0 and _PREFIX_CURRENT_POS < len(_PREFIX_FILTERED_INDICES):
        expected_idx = _PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]
        expected_cmd = _HISTORY_BUFFER[expected_idx]
        if current_input != _format_history_for_display(expected_cmd):
            if current_input.startswith(_PREFIX_VALUE):
                for pos, idx in enumerate(_PREFIX_FILTERED_INDICES):
                    if _format_history_for_display(_HISTORY_BUFFER[idx]) == current_input:
                        _PREFIX_CURRENT_POS = pos
                        _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[idx]
                        break
            else:
                _PREFIX_NAVIGATION_ACTIVE = False
                _PREFIX_VALUE = ""
                _PREFIX_FILTERED_INDICES = []
                _PREFIX_CURRENT_POS = -1
                _NAVIGATION_RAW_COMMAND = None
                clear_history_highlight_token()
                return current_input, len(current_input)

    if _PREFIX_CURRENT_POS > 0:
        _PREFIX_CURRENT_POS -= 1
        _NAVIGATION_RAW_COMMAND = _HISTORY_BUFFER[_PREFIX_FILTERED_INDICES[_PREFIX_CURRENT_POS]]
        formatted = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
        return formatted, len(formatted)
    elif _PREFIX_CURRENT_POS == 0:
        _PREFIX_NAVIGATION_ACTIVE = False
        _PREFIX_VALUE = ""
        _PREFIX_FILTERED_INDICES = []
        _PREFIX_CURRENT_POS = -1
        _NAVIGATION_RAW_COMMAND = None
        return current_input, len(current_input)
    else:
        return current_input, len(current_input)


def reset_history_index() -> None:
    """重置所有历史导航状态"""
    global _CURRENT_HISTORY_INDEX, _NAVIGATION_START_INPUT, _NAVIGATION_RAW_COMMAND
    global _PREFIX_NAVIGATION_ACTIVE, _PREFIX_VALUE, _PREFIX_FILTERED_INDICES, _PREFIX_CURRENT_POS
    global _MULTILINE_STATE, _MULTILINE_BUFFER, _MULTILINE_ACTIVE, _MULTILINE_ABORTED

    _CURRENT_HISTORY_INDEX = -1
    _NAVIGATION_START_INPUT = ""
    _NAVIGATION_RAW_COMMAND = None
    _PREFIX_NAVIGATION_ACTIVE = False
    _PREFIX_VALUE = ""
    _PREFIX_FILTERED_INDICES = []
    _PREFIX_CURRENT_POS = -1
    _MULTILINE_STATE = None
    _MULTILINE_BUFFER = []
    _MULTILINE_ACTIVE = False
    _MULTILINE_ABORTED = False
    clear_history_highlight_token()


# ===================== 新增：CMD 多行命令检测 =====================
def _is_cmd() -> bool:
    """检查当前是否是 CMD 终端"""
    return get_terminal_type() == 'cmd'


def _detect_cmd_multiline(line: str) -> Optional[str]:
    """
    检测 CMD 的多行命令类型。
    返回类型字符串或 None。
    """
    if not _is_cmd():
        return None

    stripped = line.strip().lower()

    # 检测 IF 块（可能带 ELSE）
    if re.search(r'\bif\b.*\(', stripped) and not re.search(r'\)', stripped):
        return 'cmd_if'

    # 检测 FOR 循环
    if re.search(r'\bfor\b.*\(', stripped) and not re.search(r'\)', stripped):
        return 'cmd_for'

    # 检查未闭合的括号块
    open_count = stripped.count('(')
    close_count = stripped.count(')')
    if open_count > close_count:
        return 'cmd_block'

    # 检查行续符 ^
    if stripped.endswith('^'):
        return 'cmd_continuation'

    return None


def _is_cmd_block_terminated(lines: List[str], line: str) -> bool:
    """
    检查 CMD 块是否已终止（通过匹配括号闭合）。
    使用栈式括号匹配，支持嵌套。
    """
    all_lines = lines + [line]
    full_text = '\n'.join(all_lines)
    balance = 0
    for ch in full_text:
        if ch == '(':
            balance += 1
        elif ch == ')':
            balance -= 1
            if balance < 0:
                return True  # 多余闭合，视为终止（错误状态）
    return balance == 0


# 单行自闭合结构的终止符（行内即可闭合，如 'if x; then y; fi'）
_SAME_LINE_TERMINATORS = {
    'if_fi': re.compile(r'\bfi\b'),
    'for_do': re.compile(r'\bdone\b'),
    'while_do': re.compile(r'\bdone\b'),
    'until_do': re.compile(r'\bdone\b'),
    'case_esac': re.compile(r'\besac\b'),
    'function': re.compile(r'\}'),
    'brace_block': re.compile(r'\}'),
    'subshell': re.compile(r'\)'),
}


def _multiline_text_complete(text: str, expected_syntax: str) -> bool:
    """
    判断输入文本是否已是自闭合的完整结构（无需继续输入）：
    - 粘贴的整块多行命令（已含终止行，如 'if …; then\\n…\\nfi'、heredoc 已含 EOF）
    - 单行自闭合命令（如 'if x; then y; fi'、'for i in x; do echo; done'）

    返回 True 时不应进入续行模式，命令原样交给执行器；
    否则仍按原逻辑逐行收集（如 'if x; then' 换行等待 'fi'）。

    修复：Python 分支复用 ASTValidator.is_complete_python 严格模式（唯一真源），
    避免与 validate_python 的行为分叉。
    """
    if expected_syntax == "python":
        try:
            return ASTValidator.is_complete_python(text)
        except Exception:
            try:
                import ast as _ml_ast
                _ml_ast.parse(text)
                return True
            except SyntaxError:
                pass
            except Exception:
                pass

    state = None
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        # 1) 当前存在未闭合结构：先尝试闭合（多行场景）
        if state is not None:
            result = MultiLineDetector.update_depth(state, line)
            if result == "TERMINATED":
                state = None
                continue
            if result is not None:
                state = result
                continue
        # 2) 尝试开启新结构
        new_state = MultiLineDetector.detect(line, expected_syntax)
        if new_state is None:
            continue
        # 3) 单行自闭合：开启行自身已含终止符（如 'if x; then y; fi'）
        closer = _SAME_LINE_TERMINATORS.get(new_state.type)
        if closer is not None and closer.search(line):
            continue
        state = new_state
    return state is None


def _replay_multiline_command(raw_command: str, virtual_root: str = "") -> Optional[str]:
    """
    多行命令整块回显（简简单单，无续行提示符、无逐行状态机），原样返回执行。
    只做一件事：让用户在终端上看到完整的多行命令（补成多行的样子）。
    """
    if raw_command and "\n" in raw_command:
        try:
            print(raw_command)
        except Exception:
            pass
    return raw_command


def _auto_close_heredoc_on_eof(state: Optional[MultiLineState]) -> Optional[str]:
    """
    Ctrl+D（EOF）结束 heredoc 输入：自动补上结束符，返回完整命令。
    避免把不完整的 heredoc 交给执行器，触发二次「📥 等待Here Document输入」提示。
    非 heredoc 状态返回 None。
    """
    global _MULTILINE_BUFFER
    if state is None or state.type != 'heredoc' or not _MULTILINE_BUFFER:
        return None
    if _MULTILINE_BUFFER[-1].strip() != state.delimiter:
        _MULTILINE_BUFFER.append(state.delimiter)
    result = '\n'.join(_MULTILINE_BUFFER)
    _MULTILINE_BUFFER = []
    return result


def _process_multiline_input(
    user_input: str,
    virtual_root: str = "",
    completer: Optional[SmartCompleter] = None,
    lexer: Optional[CommandLexer] = None,
    comp_style: Optional[PromptStyle] = None,
    kb: Optional[KeyBindings] = None,
    auto_suggest: Optional[AutoSuggest] = None,
) -> Optional[str]:
    """
    处理多行输入（增强版，支持 heredoc #语法 切换，支持 CMD 多行块）

    修复：确保多行输入模式下语法高亮正确应用，补全功能正常工作。
    新增：忽略以 # 开头的注释行的结构化语法检测。
    新增：CMD 的 IF/FOR 块结构支持。
    修复：语法切换时正确使用 ml_input 的补全器和词法分析器。
    修复：heredoc 中 #语法 切换在终止检查之前执行。
    修复：终止符优先级高于语法切换，防止 EOF 被 continue 跳过。
    修复：使用 SmartSyntaxDetector 重新评估语法，提升准确性。
    修复（AST 语法检查专项）：多行模式不再依赖 Pygments，
    统一由外层状态机判定 Enter 语义。
    修复（多行续行累积 · P2）：把每行 append 到 state.lines，
    使 unclosed_quote / bracket_open 等终止检查能看到完整上下文。
    修复（Enter 语义专项 · P3）：
    - 不再把 ml_input.kb 传给 prompt()。MultiLineInput 自带的 kb 把 Enter
      绑定为「插入换行 + 缩进」，那是给「单 buffer 多行编辑」场景的；
      而本函数是「每次 prompt() 取一行 → 外层累积 → 状态机判终止」，
      Enter 必须保持默认语义（提交本行），否则：
        1) EOF 永远不会作为独立一行被提交 → heredoc 无法终止
        2) 提示符只画第一行，后续行变成 buffer 内容 → 看起来不刷新
        3) buffer 卡在同一个 prompt 里 → Tab 补全/取消都退不出去
    """
    global _MULTILINE_STATE, _MULTILINE_BUFFER, _MULTILINE_ACTIVE, _MULTILINE_ABORTED

    _MULTILINE_ABORTED = False

    # 新增：CMD 多行处理
    cmd_type = _detect_cmd_multiline(user_input)
    if cmd_type and _is_cmd():
        return _process_cmd_multiline_input(
            user_input, cmd_type,
            virtual_root=virtual_root,
            completer=completer,
            lexer=lexer,
            comp_style=comp_style,
            kb=kb,
            auto_suggest=auto_suggest,
        )

    # 使用 SmartSyntaxDetector 检测首行可能的语法
    detected_syntax = detect_syntax_from_command(user_input)
    if detected_syntax == 'bash' or detected_syntax == 'unknown':
        # 进一步使用内容特征检测
        content_syntax, confidence = SmartSyntaxDetector.detect_with_confidence(user_input)
        if confidence > 0.6 and content_syntax != SyntaxType.BASH:
            detected_syntax = content_syntax.value

    state = MultiLineDetector.detect(user_input, detected_syntax or "bash")

    if state is None:
        return None

    # 修复：输入可能已是完整块（整块粘贴，或单行自闭合如 'if x; then y; fi'）。
    # 此时不应进入续行模式——否则会要求用户重复输入终止符，破坏命令。
    if _multiline_text_complete(user_input, detected_syntax or "bash"):
        return None

    # 如果检测到非 bash 语法，更新 state.syntax
    if detected_syntax and detected_syntax != 'bash' and state.syntax == 'bash':
        state.syntax = detected_syntax

    _MULTILINE_ACTIVE = True
    _MULTILINE_STATE = state
    _MULTILINE_BUFFER = [user_input]
    # P2 修复：让 state.lines 从第一行起就参与累积，供续行检测使用
    state.lines = [user_input]

    ml_input = MultiLineInput(
        syntax=state.syntax,
        virtual_root=virtual_root,
    )

    # 设置初始的 lexer 和 completer（基于 ml_input）
    current_lexer = ml_input.lexer if ml_input.lexer is not None else lexer
    # heredoc 类型不启用补全
    use_completer = None if state.type in ('heredoc',) else (ml_input.completer if ml_input.completer is not None else completer)

    prompt_text = ml_input._get_prompt_text(state)

    while _MULTILINE_ACTIVE:
        try:
            # ── P3 修复：不再传 key_bindings=ml_input.kb ──
            # ml_input.kb 的 Enter 是「插入换行 + 缩进」，会让 prompt() 永远
            # 不返回，导致 heredoc 的 EOF 无法被单独提交、提示符不再刷新、
            # buffer 卡死退不出去。这里用 ptk 默认绑定：
            #   Enter → 提交本行；Ctrl+C → KeyboardInterrupt；Ctrl+D → EOFError
            line = prompt(
                prompt_text,
                lexer=current_lexer,
                style=comp_style,
                auto_suggest=auto_suggest,
                completer=use_completer,
                complete_while_typing=(use_completer is not None),
                # mouse_support 已移除，避免鼠标接管终端滚动
            )

            if line is None or line.strip() == '__CANCEL__':
                _MULTILINE_ACTIVE = False
                _MULTILINE_STATE = None
                if line is None:
                    # Ctrl+D（EOF）结束 heredoc：自动补结束符，避免二次 📥 提示
                    closed = _auto_close_heredoc_on_eof(state)
                    if closed is not None:
                        return closed
                # 取消/中断：首行残片（如 heredoc 起始行）不得写入历史
                _MULTILINE_ABORTED = True
                _MULTILINE_BUFFER = []
                return None

            # ===== 修复后的 heredoc 处理逻辑（终止符优先级最高）=====
            if state.type == 'heredoc':
                # 1. 先检查是否是终止行（EOF 等），优先级最高
                if MultiLineDetector.is_terminated(state, line):
                    _MULTILINE_BUFFER.append(line)
                    state.lines.append(line)
                    _MULTILINE_ACTIVE = False
                    _MULTILINE_STATE = None
                    result = '\n'.join(_MULTILINE_BUFFER)
                    _MULTILINE_BUFFER = []
                    return result

                # 2. 再检查语法切换（#python、#bash 等），仅当不是终止行时
                if not state.heredoc_syntax_locked:
                    new_syntax = MultiLineDetector.detect_heredoc_syntax_switch(line)
                    if new_syntax and new_syntax != state.syntax:
                        # 切换语法
                        state.syntax = new_syntax
                        state.heredoc_syntax_locked = True
                        ml_input.current_syntax = new_syntax
                        ml_input.lexer = ml_input._get_pygments_lexer(new_syntax)
                        ml_input.completer.syntax = new_syntax
                        current_lexer = ml_input.lexer if ml_input.lexer is not None else lexer
                        # heredoc 中不启用补全
                        use_completer = None
                        # 更新提示符以反映新语法
                        prompt_text = ml_input._get_prompt_text(state)
                        # 不将 #语法 行加入缓冲区（它是控制指令，不是内容）
                        continue

                # 3. 普通 heredoc 行
                _MULTILINE_BUFFER.append(line)
                state.lines.append(line)
                prompt_text = ml_input._get_prompt_text(state)
                continue
            # ===== heredoc 处理结束 =====

            # 非 heredoc 的普通多行处理
            is_comment_line = line.strip().startswith('#')

            if is_comment_line:
                _MULTILINE_BUFFER.append(line)
                state.lines.append(line)
                prompt_text = ml_input._get_prompt_text(state)
                continue

            # 重新评估累积内容的语法（仅在非 heredoc 模式下）
            if not state.heredoc_syntax_locked:
                full_code = '\n'.join(_MULTILINE_BUFFER + [line])
                new_detected, confidence = SmartSyntaxDetector.detect_with_confidence(full_code)
                if confidence > 0.6 and new_detected.value != state.syntax:
                    # 自动切换语法
                    state.syntax = new_detected.value
                    ml_input.current_syntax = new_detected.value
                    ml_input.lexer = ml_input._get_pygments_lexer(new_detected.value)
                    ml_input.completer.syntax = new_detected.value
                    current_lexer = ml_input.lexer if ml_input.lexer is not None else lexer
                    if state.type not in ('heredoc',):
                        use_completer = ml_input.completer if ml_input.completer is not None else completer
                    prompt_text = ml_input._get_prompt_text(state)

            # 使用 ml_input 的深度栈更新逻辑
            terminated, new_state = ml_input.process_line(line, state)
            _MULTILINE_BUFFER.append(line)
            # P2 修复：累积当前行到 state.lines，让 unclosed_quote / bracket_open
            # 等基于「全文累积」的终止检查能看到完整的已输入内容
            state.lines.append(line)

            if terminated:
                # 多行输入结束
                _MULTILINE_ACTIVE = False
                _MULTILINE_STATE = None
                result = '\n'.join(_MULTILINE_BUFFER)
                _MULTILINE_BUFFER = []
                return result
            elif new_state is not None:
                # 嵌套新的多行结构
                state = new_state
                # 保持 lines 引用：new_state 里可能复制了旧 lines，统一同步一份最新累积
                state.lines = _MULTILINE_BUFFER.copy()
                ml_input.current_syntax = state.syntax
                ml_input.lexer = ml_input._get_pygments_lexer(state.syntax)
                ml_input.completer.syntax = state.syntax
                # 更新循环变量
                current_lexer = ml_input.lexer if ml_input.lexer is not None else lexer
                if state.type not in ('heredoc',):
                    use_completer = ml_input.completer if ml_input.completer is not None else completer
                else:
                    use_completer = None

            prompt_text = ml_input._get_prompt_text(state)

        except KeyboardInterrupt:
            _MULTILINE_ACTIVE = False
            _MULTILINE_STATE = None
            # 取消/中断：首行残片不得写入历史
            _MULTILINE_ABORTED = True
            _MULTILINE_BUFFER = []
            return None
        except EOFError:
            _MULTILINE_ACTIVE = False
            _MULTILINE_STATE = None
            closed = _auto_close_heredoc_on_eof(state)
            if closed is not None:
                return closed
            result = '\n'.join(_MULTILINE_BUFFER)
            _MULTILINE_BUFFER = []
            return result if result else None

    return None


def _process_cmd_multiline_input(
    user_input: str,
    cmd_type: str,
    virtual_root: str = "",
    completer: Optional[SmartCompleter] = None,
    lexer: Optional[CommandLexer] = None,
    comp_style: Optional[PromptStyle] = None,
    kb: Optional[KeyBindings] = None,
    auto_suggest: Optional[AutoSuggest] = None,
) -> Optional[str]:
    """
    处理 CMD 多行输入（IF/FOR 块结构）

    CMD 的多行语法：
    IF condition (
        command1
        command2
    ) ELSE (
        command3
    )

    FOR %%i IN (set) DO (
        command1
    )

    终止条件：括号完全闭合。
    修复：使用 ml_input.kb 保证 Enter 换行语义一致（原代码用外部 kb，
    其 Enter 绑定是「接受目录补全」，会破坏 CMD 多行块的换行输入）。
    """
    global _MULTILINE_STATE, _MULTILINE_BUFFER, _MULTILINE_ACTIVE, _MULTILINE_ABORTED

    _MULTILINE_ABORTED = False
    _MULTILINE_ACTIVE = True
    _MULTILINE_BUFFER = [user_input]
    _MULTILINE_STATE = MultiLineState(
        type='cmd_block',
        syntax='bash',  # CMD 没有专门的 Pygments lexer，用 bash 近似
        start_line=user_input,
    )
    _MULTILINE_STATE.lines = [user_input]

    ml_input = MultiLineInput(
        syntax='bash',
        virtual_root=virtual_root,
    )

    current_lexer = lexer  # CMD 多行不强制 pygments
    use_completer = None  # 默认禁用补全，避免干扰

    prompt_text = 'more> '

    while _MULTILINE_ACTIVE:
        try:
            # 修复：CMD 多行同样用 ml_input.kb 保证 Enter 语义一致
            line = prompt(
                prompt_text,
                lexer=current_lexer,
                style=comp_style,
                key_bindings=ml_input.kb,
                auto_suggest=auto_suggest,
                completer=use_completer,
                complete_while_typing=False,
                # mouse_support 已移除，避免鼠标接管终端滚动
            )

            if line is None or line.strip() == '__CANCEL__':
                _MULTILINE_ACTIVE = False
                _MULTILINE_STATE = None
                # 取消/中断：首行残片不得写入历史
                _MULTILINE_ABORTED = True
                _MULTILINE_BUFFER = []
                return None

            _MULTILINE_BUFFER.append(line)
            if _MULTILINE_STATE is not None:
                _MULTILINE_STATE.lines.append(line)

            # 检查括号是否闭合
            if _is_cmd_block_terminated([user_input], '\n'.join(_MULTILINE_BUFFER[1:])):
                _MULTILINE_ACTIVE = False
                _MULTILINE_STATE = None
                result = '\n'.join(_MULTILINE_BUFFER)
                _MULTILINE_BUFFER = []
                return result

            # 更新提示符
            if _MULTILINE_STATE.type in ('cmd_if', 'cmd_for'):
                prompt_text = 'more> '

        except KeyboardInterrupt:
            _MULTILINE_ACTIVE = False
            _MULTILINE_STATE = None
            # 取消/中断：首行残片不得写入历史
            _MULTILINE_ABORTED = True
            _MULTILINE_BUFFER = []
            return None
        except EOFError:
            _MULTILINE_ACTIVE = False
            _MULTILINE_STATE = None
            result = '\n'.join(_MULTILINE_BUFFER)
            _MULTILINE_BUFFER = []
            return result if result else None

    return None


# ===================== 主输入函数 =====================
def _consume_pending_multiline_recall(user_input: str, virtual_root: str = "") -> str:
    """
    消费虚影补全接受的待重放多行命令（含 \n）。
    缓冲区为空 → 重放为多行形式并返回完整命令；
    用户已输入其他内容 → 放弃重放，原样返回输入。
    """
    global _PENDING_MULTILINE_RECALL, _NAVIGATION_RAW_COMMAND
    if _PENDING_MULTILINE_RECALL is None:
        return user_input
    pending_raw = _PENDING_MULTILINE_RECALL
    _PENDING_MULTILINE_RECALL = None
    if not user_input.strip():
        _NAVIGATION_RAW_COMMAND = None
        replayed = _replay_multiline_command(pending_raw, virtual_root=virtual_root)
        return replayed if replayed is not None else pending_raw
    return user_input


# ── Alt+Enter：独立全屏多行编辑区（kb.py 的 multiline_editor 键触发）──
MULTILINE_EDITOR_SENTINEL = "\x00__ONYX_MULTILINE_EDITOR__\x00"
_ML_EDITOR_REQUEST = {"text": None}


def _request_multiline_editor(text: str) -> None:
    """记录进入编辑区时的缓冲区内容（供 universal_input 取用）。"""
    _ML_EDITOR_REQUEST["text"] = text or ""


def _editor_lang() -> str:
    """尽力取当前界面语言（失败回退 chinese）。"""
    try:
        from bin.manage import get_current_language
        return get_current_language() or "chinese"
    except Exception:
        return "chinese"


def universal_input(
    prompt_func: Callable[[], Union[FormattedText, str]],
    builtin_commands: Dict = None,
    cmd_mapping_cache: Dict = None,
    sys_type: str = "",
    alias_cache: Dict = None,
    user_home_dir: str = "",
    command_history: List = None,
    max_history_len: int = 1000,
    session_id: str = "",
    save_history_func: Optional[Callable] = None,
    read_config_file_func: Optional[Callable] = None,
    log_info_func: Optional[Callable] = None,
    log_error_func: Optional[Callable] = None,
    graceful_shutdown_func: Optional[Callable] = None,
    Fore: Any = None,
    Style: Any = None,
    language: str = "chinese",
    virtual_root: str = "",
    cmd_config_path: str = "",
    com_cmd_config_path: str = "",
    other_terminal_cmd_path: str = "",
) -> str:
    """主输入函数"""
    global _HISTORY_INITIALIZED, _CURRENT_LANG, _VIRTUAL_ROOT, META_TEXTS, _VALID_COMMANDS, _USER_HOME_DIR, _TERMINAL_TYPE
    global _HISTORY_BUFFER, _NAVIGATION_RAW_COMMAND, _PENDING_MULTILINE_RECALL, _MULTILINE_ABORTED

    _log_info = log_info_func if callable(log_info_func) else (lambda *a, **k: None)
    _log_error = log_error_func if callable(log_error_func) else (lambda *a, **k: None)

    _CURRENT_LANG = language
    set_language(language)
    _VIRTUAL_ROOT = virtual_root
    set_virtual_root(virtual_root)
    _USER_HOME_DIR = user_home_dir
    set_user_home_dir(user_home_dir)

    _ensure_ptk_config()

    _TERMINAL_TYPE = detect_and_set_terminal_type()

    if com_cmd_config_path:
        set_com_cmd_config_path(com_cmd_config_path)
    elif virtual_root:
        default_com_cmd_path = os.path.join(virtual_root, "onyx", "etc", "com_cmd.json")
        set_com_cmd_config_path(default_com_cmd_path)

    if other_terminal_cmd_path:
        set_other_terminal_cmds_path(other_terminal_cmd_path)
    elif virtual_root:
        vpath = os.path.join(virtual_root, "onyx", "etc", "other_terminal_cmd.json")
        if os.path.exists(vpath):
            set_other_terminal_cmds_path(vpath)

    if not _HISTORY_INITIALIZED:
        init_history_navigation()

    if builtin_commands is None:
        builtin_commands = {}
    if cmd_mapping_cache is None:
        cmd_mapping_cache = {}
    if alias_cache is None:
        alias_cache = {}

    try:
        completion_items = set()
        completion_items.update(builtin_commands.keys())

        if sys_type and sys_type in cmd_mapping_cache:
            mapping = cmd_mapping_cache[sys_type].get("mapping", {})
            completion_items.update(mapping.get("tools", {}).keys())
            completion_items.update(mapping.get("system", []))

        completion_items.update(alias_cache.keys())

        msgpack_path = os.path.join(
            user_home_dir, ".cache", "onyx", "onyx", "cmd_mapping.msgpack"
        ) if user_home_dir else ""
        if msgpack_path and os.path.exists(msgpack_path):
            try:
                msgpack_cmds = CommandConfigLoader.get_commands(msgpack_path)
                completion_items.update(msgpack_cmds)
            except Exception:
                pass

        if cmd_config_path and os.path.exists(cmd_config_path):
            try:
                json_cmds = CommandConfigLoader.get_commands(cmd_config_path)
                completion_items.update(json_cmds)
            except Exception:
                pass

        actual_com_cmd_path = com_cmd_config_path
        if not actual_com_cmd_path and virtual_root:
            actual_com_cmd_path = os.path.join(virtual_root, "onyx", "etc", "com_cmd.json")
        if actual_com_cmd_path and os.path.exists(actual_com_cmd_path):
            try:
                com_json_cmds = CommandConfigLoader.get_commands(actual_com_cmd_path)
                completion_items.update(com_json_cmds)
            except Exception:
                pass

        terminal_commands = _get_terminal_specific_commands()
        if terminal_commands:
            completion_items.update(terminal_commands)

        completion_items.add('sudo')
        completion_items.add('sado')

        set_valid_commands(completion_items)

        try:
            _vc = _VALID_COMMANDS
            _vhash = hash(frozenset(_vc.keys() if isinstance(_vc, dict) else _vc))
        except Exception:
            _vhash = -1

        _ptk_mtime = 0.0
        try:
            _cfg_path = os.path.expanduser(PTK_CONFIG_PATH)
            if os.path.exists(_cfg_path):
                _ptk_mtime = os.path.getmtime(_cfg_path)
        except Exception:
            pass

        _comp_cfg = (_ptk_config.get("completion") or {})
        _use_dropdown = bool(_comp_cfg.get("use_dropdown_menu", True))
        _reserve_space = int(_comp_cfg.get("reserve_space_for_menu", 6)) if _use_dropdown else 0

        _cache_key = (
            virtual_root, sys_type, get_terminal_type(), _vhash, _ptk_mtime,
            user_home_dir,
            repr(sorted((_ptk_config.get("colors") or {}).items())),
            repr(sorted((_ptk_config.get("key_bindings") or {}).items())),
            _use_dropdown,
        )

        _bundle = _SESSION_CACHE.get("bundle")
        if _SESSION_CACHE.get("key") == _cache_key and _bundle is not None:
            session, completer, lexer, kb, auto_suggest, comp_style = _bundle
            _log_info(
                f"复用 PromptSession（终端={get_terminal_type()}，根={virtual_root}）",
                session_id,
            )
        else:
            completer = SmartCompleter(
                list(completion_items),
                show_hidden=True,
                cmd_config_path=cmd_config_path,
                com_cmd_config_path=actual_com_cmd_path or "",
                virtual_root=virtual_root,
                user_home_dir=user_home_dir,
                history_buffer=_HISTORY_BUFFER,
            )

            # ── 新增：动态命令补全加载统计（脚本式扩展）──
            # SmartCompleter 构造时会自动发现并加载 cmd_com/*.py 与 ~/.cmd_com/*.py，
            # 这里把结果汇总成一行日志，方便排查「为什么某个命令没动态补全」。
            try:
                _dyn_stats = completer.dynamic_manager.stats()
                _dyn_cmds = _dyn_stats.get("commands", []) or []
                _dyn_scripts = _dyn_stats.get("scripts_loaded", []) or []
                _dyn_failed = _dyn_stats.get("failed", []) or []
                _shell_cmds = _dyn_stats.get("shell_commands", []) or []
                _shell_on = _dyn_stats.get("shell_enabled", False)
                _msg = (
                    f"动态补全：Python {len(_dyn_cmds)} 命令 "
                    f"({', '.join(_dyn_cmds[:8])}{'...' if len(_dyn_cmds) > 8 else ''})"
                    f" · 已加载脚本 {len(_dyn_scripts)}"
                    f" · Shell 登记 {len(_shell_cmds)}"
                    f"{'（已启用执行）' if _shell_on else '（仅探测）'}"
                )
                if _dyn_failed:
                    _msg += f" · 失败 {len(_dyn_failed)}"
                _log_info(_msg, session_id)
                if _dyn_failed:
                    for _p, _err in _dyn_failed[:3]:
                        _log_error(f"动态脚本加载失败: {_p} → {_err}", session_id)
            except Exception:
                pass

            if virtual_root and os.path.isdir(virtual_root):
                cache = get_path_cache()
                warm_paths = [virtual_root]
                if user_home_dir and os.path.isdir(user_home_dir):
                    warm_paths.append(user_home_dir)
                cache.warm_up(warm_paths, virtual_root=virtual_root, show_hidden=True)

            lexer = CommandLexer(valid_commands=_VALID_COMMANDS, virtual_root=virtual_root)

            from .com import SmartAutoSuggest
            auto_suggest = SmartAutoSuggest(completer)

            ptk_colors = _ptk_config.get("colors", {})
            default_comp_style = {
                "completion-menu": "bg:#2d2d30 #cccccc",
                "completion-menu.completion": "bg:#2d2d30 #aaaaaa",
                "completion-menu.completion.current": "bg:#007acc #ffffff",
                "completion-menu.meta": "bg:#3d3d40 #888888",
                "completion-menu.meta.current": "bg:#007acc #cccccc",
                "scrollbar.background": "bg:#1e1e1e",
                "scrollbar.button": "bg:#555555",
                "bottom-toolbar": "bg:#007acc #ffffff",
            }
            for key, value in ptk_colors.items():
                if key in default_comp_style:
                    default_comp_style[key] = value
            comp_style = PromptStyle.from_dict(default_comp_style)

            kb = create_key_bindings(
                sys_type=sys_type,
                terminal_type=get_terminal_type(),
                ptk_config=_ptk_config,
                use_dropdown_menu=_use_dropdown,
            )

            from .kb import is_completion_locked

            @Condition
            def completion_typing_filter():
                return not is_completion_locked()

            _editor = _detect_editor()
            if _editor and not (os.environ.get("VISUAL") or os.environ.get("EDITOR")):
                os.environ["EDITOR"] = _editor

            session = PromptSession(
                completer=completer,
                lexer=lexer,
                complete_while_typing=completion_typing_filter if _use_dropdown else False,
                style=comp_style,
                key_bindings=kb,
                complete_in_thread=True,
                reserve_space_for_menu=_reserve_space,
                auto_suggest=auto_suggest,
                history=_PTK_HISTORY,
                search_ignore_case=True,
                enable_open_in_editor=bool(_editor),
            )

            _SESSION_CACHE["key"] = _cache_key
            _SESSION_CACHE["session"] = session
            _SESSION_CACHE["bundle"] = (session, completer, lexer, kb, auto_suggest, comp_style)

            _log_info(
                f"新建 PromptSession（终端={get_terminal_type()}，根={virtual_root}）",
                session_id,
            )

        prompt_text = prompt_func()
        user_input = session.prompt(prompt_text)

        if user_input == MULTILINE_EDITOR_SENTINEL:
            try:
                from .mul_line import MultiLineEditor
                _init_text = _ML_EDITOR_REQUEST.get("text") or ""
                _ML_EDITOR_REQUEST["text"] = None
                _edited = MultiLineEditor(syntax=get_terminal_type(),
                                          lang=_editor_lang()).edit(_init_text)
            except Exception:
                _edited = None
            if _edited is None:
                reset_history_index()
                return ""
            user_input = _edited

        user_input = _consume_pending_multiline_recall(user_input, virtual_root)

        if _NAVIGATION_RAW_COMMAND is not None:
            display_form = _format_history_for_display(_NAVIGATION_RAW_COMMAND)
            if user_input == display_form:
                user_input = _NAVIGATION_RAW_COMMAND
            _NAVIGATION_RAW_COMMAND = None

        user_input_stripped = user_input.strip()

        user_input_stripped = _strip_control_noise(user_input_stripped)

        if user_input_stripped:
            user_input_stripped = _clean_display_text(user_input_stripped, decode_escapes=False)

        if user_input_stripped:
            multiline_result = _process_multiline_input(
                user_input_stripped,
                virtual_root=virtual_root,
                completer=completer,
                lexer=lexer,
                comp_style=comp_style,
                kb=kb,
                auto_suggest=auto_suggest,
            )

            if multiline_result is not None:
                # 多行输入正常完成
                user_input_stripped = multiline_result.strip()
                user_input_stripped = _clean_display_text(user_input_stripped, decode_escapes=False)
                if user_input_stripped:
                    add_to_history(user_input_stripped)
                    reset_history_index()
                    return user_input_stripped
            elif _MULTILINE_ABORTED:
                # ── 修复（取消后重复触发 heredoc 专项 · P3）──
                # 多行输入被取消（Ctrl+C / Ctrl+D / 显式 __CANCEL__）时，
                # 首行残片（如 `cat > a.sh << EOF`）绝不能交给主程序。
                # 此前会落到函数底部 `return user_input_stripped`，把原始
                # 首行原样返回 → 主程序再检测一次 heredoc → 出现第二次
                # 「📥 等待Here Document输入」提示，用户必须再取消一次。
                reset_history_index()
                return ""
            elif _MULTILINE_ACTIVE:
                reset_history_index()
                return ""

        # 走到这里只有两种情况：
        #   1. 普通单行命令（_process_multiline_input 返回 None 且未进多行）
        #   2. 多行结果为空
        if user_input_stripped and not _MULTILINE_ABORTED:
            add_to_history(user_input_stripped)

        reset_history_index()
        return user_input_stripped

    except KeyboardInterrupt:
        if Fore:
            print(Fore.YELLOW + "\n^C" + (Style.RESET_ALL if Style else ""))
        else:
            print("\n^C")
        reset_history_index()
        return ""
    except EOFError:
        if Fore:
            print(Fore.YELLOW + "\n^D" + (Style.RESET_ALL if Style else ""))
        else:
            print("\n^D")
        if graceful_shutdown_func:
            graceful_shutdown_func(session_id)
        sys.exit(0)
        return ""
    except Exception as e:
        try:
            _log_error(f"输入处理异常: {type(e).__name__}: {e}\n{traceback.format_exc()}", session_id)
        except Exception:
            pass
        if Fore:
            print(Fore.RED + f"Input error: {e}" + (Style.RESET_ALL if Style else ""))
        reset_history_index()
        return ""


__all__ = [
    'universal_input',
    'set_language',
    'get_path_cache',
    'PathCache',
    'SmartCompleter',
    'set_virtual_root',
    'get_virtual_root',
    'CommandConfigLoader',
    'CommandLexer',
    'set_valid_commands',
    'handle_multiline_input',
    'detect_syntax_from_command',
    'MultiLineInput',
    'MultiLineDetector',
    'MultiLineState',
    'MultiLineFormatter',
    'HAS_PYGMENTS',
    'get_terminal_type',
    'detect_and_set_terminal_type',
    'set_other_terminal_cmds_path',
    'get_other_terminal_cmds',
    'set_com_cmd_config_path',
    'get_com_cmd_config',
    'set_com_cmd_config_path_from_root',
    'get_com_cmd_config_path',
    '_get_nav_match_info',
]