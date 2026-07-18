# lib/parse.py
"""
命令解析与展开模块
负责：引号检查、变量/算术/命令替换、通配符、路径解析、命令提取
不包含命令执行逻辑

支持终端类型：bash, zsh, fish, sh, cmd, powershell

注意：变量扩展由底层 Shell 进程完成，Python 层不主动解析变量值
"""

import os
import re
import glob
import uuid
import shlex
import stat
from typing import List, Tuple, Dict, Any, Optional, Callable
from lib.terminal.colors import Fore, Style


# 全局缓存，用于命令替换（避免重复执行）
_COMMAND_SUBST_CACHE = {}

# 路径映射缓存：展开后的路径 -> 原始路径
_PATH_MAPPING_CACHE = {}


def clear_path_mapping_cache():
    """清空路径映射缓存"""
    global _PATH_MAPPING_CACHE
    _PATH_MAPPING_CACHE = {}


def add_path_mapping(original_path: str, expanded_path: str):
    """添加路径映射关系"""
    if original_path and expanded_path and original_path != expanded_path:
        _PATH_MAPPING_CACHE[expanded_path] = original_path


def get_original_path(expanded_path: str) -> str:
    """获取展开前的原始路径"""
    return _PATH_MAPPING_CACHE.get(expanded_path, expanded_path)


def restore_paths_in_text(text: str) -> str:
    """将文本中的展开路径还原为原始路径"""
    if not text or not _PATH_MAPPING_CACHE:
        return text
    
    result = text
    for expanded, original in sorted(_PATH_MAPPING_CACHE.items(), key=lambda x: len(x[0]), reverse=True):
        result = result.replace(expanded, original)
    
    return result


# 快速路径判定用的字符集（模块级，避免每次构造）
_FAST_SPLIT_SKIP_CHARS = frozenset({'"', "'", '\\', '|', '&', ';', '<', '>', '$', '(', ')', '{', '}', '`', '!'})


def smart_shlex_split(text: str, sys_type: str = 'bash') -> List[str]:
    """
    智能解析命令，正确处理嵌套引号、转义和命令替换 $()
    
    重要：管道符 | 和逻辑操作符 &、;、<、> 等元字符应该作为独立 token
    """
    if not text or not text.strip():
        return []
    
    # 快速路径：绝大多数命令不含引号/转义/元字符，直接用 str.split()
    if not _FAST_SPLIT_SKIP_CHARS.intersection(text):
        return text.split()
    
    rules = _get_terminal_escape_rules(sys_type)
    escape_char = rules['escape_char']
    has_single_quotes = rules['has_single_quotes']
    
    # 元字符集合（这些字符应该作为独立 token）
    meta_chars = set('|&;<>')
    
    parts = []
    current_part = []
    in_single = False
    in_double = False
    escaped = False
    paren_depth = 0
    
    i = 0
    n = len(text)
    
    while i < n:
        char = text[i]
        
        if escaped:
            current_part.append(char)
            escaped = False
            i += 1
            continue
            
        if char == escape_char and sys_type in ('cmd', 'powershell') and not in_single:
            escaped = True
            current_part.append(char)
            i += 1
            continue
            
        if char == '\\' and sys_type not in ('cmd',):
            escaped = True
            current_part.append(char)
            i += 1
            continue
            
        if char == "'" and not in_double and has_single_quotes:
            in_single = not in_single
            current_part.append(char)
            i += 1
            continue
            
        if char == '"' and not in_single:
            in_double = not in_double
            current_part.append(char)
            i += 1
            continue
        
        # 处理 $() 命令替换 - 不在引号内时跟踪括号深度
        if sys_type not in ('cmd',) and not in_single and not in_double:
            if char == '$' and i + 1 < n and text[i + 1] == '(':
                paren_depth += 1
                current_part.append(char)
                i += 1
                continue
            elif char == '(' and paren_depth > 0:
                paren_depth += 1
                current_part.append(char)
                i += 1
                continue
            elif char == ')' and paren_depth > 0:
                paren_depth -= 1
                current_part.append(char)
                i += 1
                continue
        
        # 处理元字符 - 只在不在引号内且不在 $() 内部时生效
        if not (in_single or in_double) and paren_depth == 0:
            # 检查复合操作符 &&, ||, 2>, >>, 2>> 等
            if char == '&' and i + 1 < n and text[i + 1] == '&':
                if current_part:
                    parts.append(''.join(current_part))
                    current_part = []
                parts.append('&&')
                i += 2
                continue
            
            if char == '|' and i + 1 < n and text[i + 1] == '|':
                if current_part:
                    parts.append(''.join(current_part))
                    current_part = []
                parts.append('||')
                i += 2
                continue
            
            if char == '|':
                if current_part:
                    parts.append(''.join(current_part))
                    current_part = []
                parts.append('|')
                i += 1
                continue
            
            if char == '&':
                if current_part:
                    parts.append(''.join(current_part))
                    current_part = []
                parts.append('&')
                i += 1
                continue
            
            if char == ';':
                if current_part:
                    parts.append(''.join(current_part))
                    current_part = []
                parts.append(';')
                i += 1
                continue
            
            # 处理重定向操作符
            if char == '>' and i + 1 < n and text[i + 1] == '>':
                if current_part:
                    parts.append(''.join(current_part))
                    current_part = []
                parts.append('>>')
                i += 2
                continue
            
            if char == '2' and i + 2 < n and text[i + 1] == '>' and text[i + 2] == '>':
                if current_part:
                    parts.append(''.join(current_part))
                    current_part = []
                parts.append('2>>')
                i += 3
                continue
            
            if char == '2' and i + 1 < n and text[i + 1] == '>':
                if current_part:
                    parts.append(''.join(current_part))
                    current_part = []
                parts.append('2>')
                i += 2
                continue
            
            if char == '>' and (i + 1 >= n or text[i + 1] != '>'):
                if current_part:
                    parts.append(''.join(current_part))
                    current_part = []
                parts.append('>')
                i += 1
                continue
            
            if char == '<' and i + 1 < n and text[i + 1] == '<':
                if current_part:
                    parts.append(''.join(current_part))
                    current_part = []
                parts.append('<<')
                i += 2
                continue
            
            if char == '<' and (i + 1 >= n or text[i + 1] != '<'):
                if current_part:
                    parts.append(''.join(current_part))
                    current_part = []
                parts.append('<')
                i += 1
                continue
        
        # 普通字符或引号内的内容
        current_part.append(char)
        
        # 空格分割（只在不在引号内且不在 $() 内部时生效）
        if char.isspace() and not (in_single or in_double) and paren_depth == 0:
            if current_part:
                # 检查是否只有空白字符
                if ''.join(current_part).strip():
                    parts.append(''.join(current_part).strip())
                current_part = []
        
        i += 1
    
    # 处理最后一个 token
    if current_part:
        last_token = ''.join(current_part).strip()
        if last_token:
            parts.append(last_token)
    
    return parts


def check_quotes_balanced(text: str, sys_type: str = 'bash') -> Tuple[bool, str]:
    """
    检查引号是否匹配（支持多行）
    
    不同终端的引号规则：
    - bash/zsh/sh: 单引号 ' 和双引号 "
    - powershell: 单引号 ' 和双引号 "（单引号内不转义）
    - cmd: 只有双引号 "（cmd 没有单引号概念）
    """
    if not text:
        return True, ""
    
    # 快速路径：不含任何引号的命令直接通过
    if '"' not in text and "'" not in text:
        return True, ""
    
    # 根据终端类型确定转义字符
    if sys_type == 'cmd':
        escape_char = '^'
        has_single_quotes = False
    elif sys_type == 'powershell':
        escape_char = '`'
        has_single_quotes = True
    else:
        escape_char = '\\'
        has_single_quotes = True
        
    in_single = False
    in_double = False
    escaped = False
    
    for i, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        
        # 转义字符检测
        if char == escape_char:
            escaped = True
        elif char == '\\' and sys_type not in ('cmd',):
            escaped = True
        elif char == "'" and not in_double and has_single_quotes:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
    
    if in_single:
        return False, "未闭合的单引号"
    if in_double:
        return False, "未闭合的双引号"
    return True, ""


# ========== 获取终端类型对应的转义字符和引号规则 ==========
def _get_terminal_escape_rules(sys_type: str) -> Dict[str, Any]:
    """
    获取终端类型的转义和引号规则
    
    返回字典包含：
    - escape_char: 转义字符
    - has_single_quotes: 是否有单引号
    - var_dollar_suffix: 变量前缀标识
    - var_percent_style: 是否支持 %VAR% 风格
    """
    rules = {
        'bash': {'escape_char': '\\', 'has_single_quotes': True, 'var_dollar_suffix': True, 'var_percent_style': False},
        'zsh': {'escape_char': '\\', 'has_single_quotes': True, 'var_dollar_suffix': True, 'var_percent_style': False},
        'fish': {'escape_char': '\\', 'has_single_quotes': True, 'var_dollar_suffix': True, 'var_percent_style': False},
        'sh': {'escape_char': '\\', 'has_single_quotes': True, 'var_dollar_suffix': True, 'var_percent_style': False},
        'dash': {'escape_char': '\\', 'has_single_quotes': True, 'var_dollar_suffix': True, 'var_percent_style': False},
        'cmd': {'escape_char': '^', 'has_single_quotes': False, 'var_dollar_suffix': False, 'var_percent_style': True},
        'powershell': {'escape_char': '`', 'has_single_quotes': True, 'var_dollar_suffix': True, 'var_percent_style': False},
    }
    return rules.get(sys_type, rules['bash'])


def _is_inside_quotes(text: str, position: int) -> Tuple[bool, bool, bool]:
    """
    检查指定位置是否在引号内
    返回 (in_single, in_double, is_escaped)
    """
    in_single = False
    in_double = False
    escaped = False
    
    for i in range(position):
        ch = text[i]
        if escaped:
            escaped = False
            continue
        if ch == '\\':
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
    
    return in_single, in_double, False


def _try_resolve_variable_path(var_name: str, var_value: str, resolve_path_func=None, original_var_ref: str = None) -> str:
    """
    变量路径解析（已废弃 - 变量由 Shell 处理）
    直接返回原始变量引用，不进行路径解析
    """
    return original_var_ref or f"${var_name}"


def _get_var_value_from_shell(var_name: str, get_var_from_shell_func=None) -> Optional[str]:
    """
    从持久化 Shell 进程中读取变量值（已废弃，保留用于兼容）
    """
    return None


def expand_variables(text: str, os_environ: dict, globals_dict: dict, 
                     sys_type: str = 'bash',
                     get_var_from_shell_func=None,
                     resolve_path_func=None) -> str:
    """
    扩展变量
    
    规则：
    - 不再主动解析变量值，直接返回原始变量引用（$VAR 或 %VAR%）
    - 变量展开由底层 Shell 进程完成，Python 层只负责标记变量位置
    
    注意：
    - cmd: %VAR% 格式保持不变
    - Unix/PowerShell: $VAR, ${VAR} 格式保持不变
    """
    if not text:
        return text
    
    # 直接返回原文本，不进行变量展开
    # 变量由底层 Shell 进程处理
    return text


def expand_tilde(text: str, user_home_dir: str, sys_type: str = 'bash') -> str:
    """
    扩展 ~ 为用户主目录
    
    注意：
    - cmd 不支持 ~，%USERPROFILE% 已通过变量扩展处理
    - PowerShell 支持 ~ 但仅在路径上下文中
    - 跳过引号内的 ~
    """
    if not text or '~' not in text:
        return text
    
    # cmd 不支持 ~ 扩展
    if sys_type == 'cmd':
        return text
        
    def tilde_replacer(match):
        tilde_part = match.group(0)
        # 检查是否在引号内
        start_pos = match.start()
        in_single, in_double, _ = _is_inside_quotes(text, start_pos)
        if in_single or in_double:
            return tilde_part
        
        if tilde_part == '~':
            return user_home_dir
        elif tilde_part.startswith('~/'):
            return os.path.join(user_home_dir, tilde_part[2:])
        return tilde_part
    
    pattern = r'~(?:/[\w/.-]*)?'
    return re.sub(pattern, tilde_replacer, text)


def expand_braces(text: str, sys_type: str = 'bash') -> str:
    """
    扩展花括号 {a,b,c}
    
    注意：
    - cmd 不支持花括号扩展
    - PowerShell 的 {} 用于脚本块，不是路径扩展
    - 跳过引号内的花括号
    """
    if not text or '{' not in text:
        return text
    
    # cmd 和 PowerShell 不进行花括号扩展
    if sys_type in ('cmd', 'powershell'):
        return text
        
    def brace_replacer(match):
        content = match.group(1)
        start_pos = match.start()
        in_single, in_double, _ = _is_inside_quotes(text, start_pos)
        
        # 引号内的花括号不扩展
        if in_single or in_double:
            return match.group(0)
        
        if not content:
            return match.group(0)
        
        # 函数定义 hello() { ... } 或 function name { ... } — 不展开
        before = text[:start_pos].rstrip()
        if re.search(r'(\)|function\s+\w+)\s*$', before):
            return match.group(0)
            
        try:
            if '{' in content or '}' in content:
                return match.group(0)
                
            items = [item.strip() for item in content.split(',') if item.strip()]
            if not items:
                return match.group(0)
            return ' '.join(items)
        except Exception:
            return match.group(0)
    
    pattern = r'\{([^{}]+)\}'
    return re.sub(pattern, brace_replacer, text)


def expand_wildcards(text: str, get_virtual_path_func=None, sys_type: str = 'bash') -> str:
    """
    扩展通配符 *, ?, []（跳过引号内的）
    
    注意：
    - 所有终端都支持通配符，但语法略有不同
    - PowerShell 的 [] 用于数组，但在路径上下文中仍是通配符
    - 带引号的通配符字符串不展开
    """
    if not text or ('*' not in text and '?' not in text and '[' not in text):
        return text

    rules = _get_terminal_escape_rules(sys_type)
    escape_char = rules['escape_char']
    has_single_quotes = rules['has_single_quotes']

    in_single = False
    in_double = False
    escaped = False
    result = []
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]
        if escaped:
            escaped = False
            result.append(ch)
            i += 1
            continue
        
        if ch == escape_char:
            escaped = True
            result.append(ch)
            i += 1
            continue
        
        if ch == '\\' and sys_type not in ('cmd',):
            escaped = True
            result.append(ch)
            i += 1
            continue
        
        if ch == "'" and not in_double and has_single_quotes:
            in_single = not in_single
            result.append(ch)
            i += 1
            continue
        
        if ch == '"' and not in_single:
            in_double = not in_double
            result.append(ch)
            i += 1
            continue

        # 只有在不在引号内时才展开通配符
        if not in_single and not in_double and ch in ('*', '?', '['):
            start = i
            # 找到单词边界
            while start > 0 and not text[start-1].isspace():
                start -= 1
            end = i
            while end < n and not text[end].isspace():
                end += 1
            word = text[start:end]
            
            if '*' in word or '?' in word or '[' in word:
                try:
                    # 注意：glob 不支持引号，所以需要临时去除引号进行匹配
                    clean_word = word.strip('\'"')
                    matches = glob.glob(clean_word, recursive=False)
                    if not matches:
                        result.append(word)
                    else:
                        if get_virtual_path_func:
                            rel_matches = [get_virtual_path_func(m) for m in matches]
                        else:
                            rel_matches = matches
                        
                        # 保存路径映射关系：展开后的路径 -> 原始通配符模式
                        for rel_match in rel_matches:
                            if rel_match != word:
                                add_path_mapping(word, rel_match)
                        
                        result.append(' '.join(rel_matches))
                except Exception:
                    result.append(word)
                i = end
                continue
        
        result.append(ch)
        i += 1

    return ''.join(result)


def remove_comments(text: str, sys_type: str = 'bash') -> str:
    """
    移除注释
    
    注释规则：
    - bash/zsh/sh: # 开头到行尾（但不能移除引号内的 #）
    - PowerShell: # 开头到行尾，但也支持 <# 块注释 #>
    - cmd: rem 命令或 :: 开头
    
    注意：here-doc 内容中的 # 不应该被当作注释
    """
    if not text:
        return text
    
    # 检测是否在 here-doc 内容中
    def is_in_heredoc_content(content: str, pos: int) -> bool:
        """检查位置是否在 here-doc 内容中"""
        # 简化实现：查找 << 标记
        heredoc_pattern = r'<<\s*[\'\"]?([A-Za-z0-9_]+)[\'\"]?\s*\n(.*?)\n\s*\1'
        matches = list(re.finditer(heredoc_pattern, content, re.DOTALL))
        for match in matches:
            if match.start() < pos < match.end():
                return True
        return False
    
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        stripped = line.rstrip('\n')
        # cmd 的 rem 和 :: 注释
        if sys_type == 'cmd':
            # 检查是否在引号内
            in_quote = False
            new_line = []
            i = 0
            n = len(stripped)
            while i < n:
                ch = stripped[i]
                if ch == '"':
                    in_quote = not in_quote
                    new_line.append(ch)
                    i += 1
                    continue
                if not in_quote and stripped[i:].lower().startswith('rem'):
                    # 遇到 rem 注释，截断
                    break
                if not in_quote and stripped[i:i+2] == '::':
                    # 遇到 :: 注释，截断
                    break
                new_line.append(ch)
                i += 1
            line = ''.join(new_line).rstrip()
        else:
            # Unix/PowerShell: 处理 # 注释，但跳过引号内的
            in_single = False
            in_double = False
            escaped = False
            escape_char = '\\'
            new_line = []
            i = 0
            n = len(stripped)
            while i < n:
                ch = stripped[i]
                if escaped:
                    escaped = False
                    new_line.append(ch)
                    i += 1
                    continue
                if ch == '\\' and sys_type not in ('cmd',):
                    escaped = True
                    new_line.append(ch)
                    i += 1
                    continue
                if ch == "'" and not in_double and sys_type != 'cmd':
                    in_single = not in_single
                    new_line.append(ch)
                    i += 1
                    continue
                if ch == '"' and not in_single:
                    in_double = not in_double
                    new_line.append(ch)
                    i += 1
                    continue
                if ch == '#' and not in_single and not in_double:
                    # 检查是否在 here-doc 中
                    if not is_in_heredoc_content(text, i):
                        # 遇到注释，直接结束这一行
                        break
                new_line.append(ch)
                i += 1
            line = ''.join(new_line).rstrip()
        
        if line or cleaned_lines:
            cleaned_lines.append(line)
    
    # 保留空行结构（但删除完全空的行可能会影响多行字符串，这里谨慎处理）
    result = '\n'.join(cleaned_lines)
    # 如果结果以空行开头，移除第一个空行
    if result.startswith('\n'):
        result = result.lstrip('\n')
    return result


def resolve_path_to_absolute(path_str: str, root_dir: str = None) -> str:
    """
    将路径解析为绝对物理路径（用于路径检查，不修改原始命令）
    注意：此函数只用于路径存在性判断，不改变命令字符串
    """
    if not path_str:
        return path_str
    
    # 只在判断路径时临时去除引号，不修改原始值
    clean_path = path_str.strip('\'"')
    
    # ~ 扩展
    if clean_path.startswith('~') and root_dir:
        clean_path = os.path.expanduser(clean_path)
    
    try:
        if os.path.isabs(clean_path):
            return os.path.abspath(clean_path)
        else:
            return os.path.abspath(os.path.join(os.getcwd(), clean_path))
    except Exception:
        return path_str


def should_try_path_resolution(token: str) -> bool:
    """
    判断一个 token 是否应该尝试路径解析
    
    跳过以下情况：
    - 长度为 0 或 1 的单个字符（除了单独的根目录 '/'）
    - 纯特殊符号（如 ?、*、/ 等）
    - 以 - 开头的选项参数
    - 纯粹的路径分隔符
    - 带引号的 token（由外部处理）
    
    需要解析的情况：
    - 包含路径相关字符（/、.）的 token
    - 长度大于 1 且不完全是特殊符号的 token
    """
    if not token:
        return False
    
    # 单独的根目录 / 允许路径解析
    if token == '/':
        return True
    
    # 长度小于2（且不是 /）的跳过
    if len(token) <= 1:
        return False
    
    # 跳过以 - 开头的选项参数
    if token.startswith('-'):
        return False
    
    # 跳过纯特殊符号组合
    special_only_pattern = re.compile(r'^[\/\.\;\:\,\!\@\#\$\%\^\&\*\(\)\[\]\{\}\|\~\`\\]+$')
    if special_only_pattern.match(token):
        return False
    
    # 包含路径相关字符（/ 或 .）则尝试解析
    if '/' in token or ('.' in token and len(token) > 1):
        return True
    
    # 其他情况：可能是文件名，也尝试解析
    if re.search(r'[a-zA-Z0-9_]', token):
        return True
    
    return False


def is_executable_binary(file_path: str) -> bool:
    """
    检查文件是否是二进制可执行文件
    
    Args:
        file_path: 文件路径
    
    Returns:
        如果是二进制可执行文件返回 True
    """
    if not file_path or not os.path.exists(file_path):
        return False
    
    try:
        # 检查是否是文件
        if not os.path.isfile(file_path):
            return False
        
        # 检查是否有执行权限
        if not os.access(file_path, os.X_OK):
            return False
        
        # 尝试读取文件头判断是否是二进制
        try:
            with open(file_path, 'rb') as f:
                header = f.read(1024)
                # 检查常见的二进制文件头
                if header.startswith(b'\x7fELF'):  # ELF
                    return True
                if header.startswith(b'MZ'):  # Windows PE
                    return True
                if header.startswith(b'\xca\xfe\xba\xbe'):  # Mach-O
                    return True
                if header.startswith(b'\xcf\xfa\xed\xfe'):  # Mach-O 64-bit
                    return True
                # 检查是否有空字节（通常表示二进制文件）
                if b'\x00' in header[:100]:
                    return True
        except Exception:
            pass
        
        return False
    except Exception:
        return False


def resolve_token_path(token: str, resolve_path_func=None) -> str:
    """
    尝试将 token 解析为虚拟路径
    
    Args:
        token: 待解析的字符串单元
        resolve_path_func: 路径解析函数
    
    Returns:
        解析后的路径（如果解析成功），否则返回原 token
    """
    if not should_try_path_resolution(token):
        return token
    
    if resolve_path_func is None:
        return token
    
    # 保留引号，只对引号内的内容进行解析
    stripped = token.strip('\'"')
    if stripped == token:
        # 没有引号，直接解析
        try:
            resolved = resolve_path_func(token)
            if resolved and resolved != token:
                return resolved
        except Exception:
            pass
    else:
        # 有引号，解析引号内的内容，然后重新加回引号
        try:
            resolved = resolve_path_func(stripped)
            if resolved and resolved != stripped:
                # 保持原始引号类型
                quote_char = token[0]
                return f"{quote_char}{resolved}{quote_char}"
        except Exception:
            pass
    
    return token


def resolve_paths_in_multiline_text(text: str, resolve_path_func=None) -> str:
    """
    在多行文本中，对每个以空格分隔的单位进行虚拟路径转换
    
    规则：
    - 跳过长度为 0 或 1 的单位（除 / 外）
    - 跳过纯特殊符号单位
    - 跳过以 - 开头的选项参数
    - 其他单位尝试进行路径解析
    
    Args:
        text: 多行文本
        resolve_path_func: 路径解析函数
    
    Returns:
        处理后的文本
    """
    if not text or not resolve_path_func:
        return text
    
    # 快速路径：不含路径分隔符的文本无需解析
    if '/' not in text and '~' not in text:
        return text
    
    lines = text.split('\n')
    processed_lines = []
    
    for line in lines:
        if not line.strip():
            processed_lines.append(line)
            continue
        
        # 处理每一行中的 token
        tokens = line.split(' ')
        processed_tokens = []
        
        for token in tokens:
            # 保留空字符串（连续空格的情况）
            if not token:
                processed_tokens.append(token)
                continue
            
            # 尝试解析路径
            resolved = resolve_token_path(token, resolve_path_func)
            processed_tokens.append(resolved)
        
        processed_lines.append(' '.join(processed_tokens))
    
    return '\n'.join(processed_lines)


def handle_executable_path(command_token: str, resolve_path_func=None, 
                           virtual_root_dir: str = None) -> str:
    """
    处理可执行文件路径
    
    - 如果路径指向二进制可执行文件 → 返回解析后的路径
    - 如果路径指向非二进制文件 → 返回 'python <虚拟根目录>/onyx/cmd.py source <原始路径>'
    
    Args:
        command_token: 命令 token（可能是文件路径）
        resolve_path_func: 路径解析函数
        virtual_root_dir: 虚拟根目录
    
    Returns:
        处理后的命令字符串
    """
    if not resolve_path_func:
        return command_token
    
    try:
        # 保留原始命令的引号
        has_quotes = command_token.startswith(('"', "'")) and command_token.endswith(('"', "'"))
        stripped_token = command_token.strip('\'"')
        
        # 尝试解析路径
        resolved = resolve_path_func(stripped_token)
        
        if resolved and resolved != stripped_token:
            # 检查是否是二进制可执行文件
            if is_executable_binary(resolved):
                # 保持原始引号格式
                if has_quotes:
                    quote_char = command_token[0]
                    return f"{quote_char}{resolved}{quote_char}"
                return resolved
            elif os.path.isfile(resolved):
                # 非二进制文件，转换为 source 调用
                if virtual_root_dir:
                    cmd_py_path = os.path.join(virtual_root_dir, 'onyx', 'cmd.py')
                    if has_quotes:
                        return f"python {cmd_py_path} -c \"source {stripped_token}\""
                    else:
                        return f"python {cmd_py_path} -c \"source {command_token}\""
                else:
                    if has_quotes:
                        return f"python cmd.py -c \"source {stripped_token}\""
                    else:
                        return f"python cmd.py -c \"source {command_token}\""
        
        return command_token
    except Exception:
        return command_token


def extract_all_argument_paths(cmd_str: str, resolve_path_func=None, sys_type: str = 'bash') -> List[str]:
    """从命令字符串中提取所有参数路径（非选项参数）"""
    paths = []
    
    if not cmd_str:
        return paths
    
    parts = smart_shlex_split(cmd_str, sys_type)
    # 跳过命令本身和选项
    for part in parts[1:]:
        if part and not part.startswith('-') and ('/' in part or '.' in part):
            # 只在提取时解析路径，不修改原始值
            clean_part = part.strip('\'"')
            resolved = resolve_path_func(clean_part) if resolve_path_func else resolve_path_to_absolute(clean_part)
            if resolved:
                paths.append(resolved)
    
    return paths


def extract_redirect_paths(redirect_config: Dict) -> List[str]:
    """从重定向配置中提取所有文件路径"""
    paths = []
    
    for key in ['stdout', 'stderr', 'stdin']:
        val = redirect_config.get(key)
        if val:
            if isinstance(val, tuple):
                file_path = val[0]
            else:
                file_path = val
            if file_path and file_path != 'STDOUT':
                paths.append(file_path)
    
    return paths


def has_pipeline(text: str, sys_type: str = 'bash') -> bool:
    """
    检查是否有不在引号内的管道符
    
    管道符规则：
    - bash/zsh/sh: |
    - cmd: | 也是管道符
    - PowerShell: | 是管道符
    """
    if not text or '|' not in text:
        return False
    
    rules = _get_terminal_escape_rules(sys_type)
    escape_char = rules['escape_char']
    has_single_quotes = rules['has_single_quotes']
        
    in_single = False
    in_double = False
    escaped = False
    
    for i, char in enumerate(text):
        if escaped:
            escaped = False
            continue
            
        if char == escape_char:
            escaped = True
        elif char == '\\' and sys_type not in ('cmd',):
            escaped = True
        elif char == "'" and not in_double and has_single_quotes:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == '|' and not in_single and not in_double:
            return True
    
    return False


def has_logical_operators(text: str, sys_type: str = 'bash') -> bool:
    """
    检查是否有不在引号内的逻辑操作符
    
    逻辑操作符规则：
    - bash/zsh/sh: &&, ||
    - cmd: &&, ||
    - PowerShell: -and, -or（不区分大小写），也兼容 &&, ||（PowerShell 7+）
    """
    if not text:
        return False
    
    rules = _get_terminal_escape_rules(sys_type)
    escape_char = rules['escape_char']
    has_single_quotes = rules['has_single_quotes']
    
    in_single = False
    in_double = False
    escaped = False
    
    for i, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == escape_char:
            escaped = True
        elif char == '\\' and sys_type not in ('cmd',):
            escaped = True
        elif char == "'" and not in_double and has_single_quotes:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            # 检查 && 和 ||
            if i + 1 < len(text):
                if text[i:i+2] in ('&&', '||'):
                    return True
            
            # 检查 PowerShell 的 -and 和 -or（不区分大小写）
            if sys_type == 'powershell':
                remaining = text[i:].lower()
                if remaining.startswith('-and') and (i + 4 >= len(text) or text[i+4].isspace() or text[i+4] in '();{}'):
                    return True
                if remaining.startswith('-or') and (i + 3 >= len(text) or text[i+3].isspace() or text[i+3] in '();{}'):
                    return True
    
    return False


def is_shell_logic_structure(cmd_str: str, sys_type: str = 'bash') -> bool:
    """
    检测是否是当前 shell 的逻辑结构（if/for/while/case 等多行命令块）
    
    支持多种终端类型：
    - bash/zsh/fish/sh: if/then/do/done/fi/esac 等，包括 { } 块和 ( ) 子shell
    - cmd: if/else/for 等
    - powershell: if/foreach/while/switch 等
    
    Args:
        cmd_str: 命令字符串
        sys_type: 终端类型 (bash, zsh, fish, sh, cmd, powershell)
    
    Returns:
        是否为多行逻辑结构
    """
    if not cmd_str or not cmd_str.strip():
        return False
    
    stripped = cmd_str.strip().lower()
    
    # 根据终端类型定义开始关键词
    if sys_type in ('bash', 'zsh', 'fish', 'sh', 'dash', 'ash'):
        start_keywords = [
            'if ', 'for ', 'while ', 'case ', 'until ', 'select ',
            'if\n', 'for\n', 'while\n', 'case\n', 'until\n', 'select\n',
            'do\n', 'then\n'
        ]
        structure_keywords = [
            '\nthen', '\ndo', '\nfi', '\ndone', '\nesac',
            ' then', ' do', ' fi', ' done', ' esac',
            'then\n', 'do\n', 'fi\n', 'done\n', 'esac\n'
        ]
        # 检测 () 子shell 和 {} 块（多行）
        if stripped.startswith('(') or stripped.startswith('{'):
            return True
        if '\n{' in stripped or '\n(' in stripped:
            return True
        
    elif sys_type == 'cmd':
        start_keywords = [
            'if ', 'for ', 'else ',
            'if\n', 'for\n', 'else\n',
            'if exist ', 'if not exist ', 'if defined',
            'if exist\n', 'if not exist\n', 'if defined\n'
        ]
        structure_keywords = [
            '\ndo', '\nelse', '\n)',
            ' do', ' else', '(',
            'do\n', 'else\n',
            '\nif ', '\nfor '
        ]
    elif sys_type == 'powershell':
        start_keywords = [
            'if ', 'foreach ', 'while ', 'switch ', 'for ', 'try ', 'catch ',
            'if\n', 'foreach\n', 'while\n', 'switch\n', 'for\n', 'try\n', 'catch\n',
            'if(', 'foreach(', 'while(', 'switch('
        ]
        structure_keywords = [
            '\n{', '\n}',
            ' {', ' }',
            '{', '}',
            '\nelse', '\nelseif', '\nfinally',
            ' else', ' elseif', ' finally'
        ]
    else:
        # 未知终端类型，使用 bash 兼容模式
        start_keywords = [
            'if ', 'for ', 'while ', 'case ', 'until ', 'select ',
            'if\n', 'for\n', 'while\n', 'case\n', 'until\n', 'select\n',
            'do\n', 'then\n'
        ]
        structure_keywords = [
            '\nthen', '\ndo', '\nfi', '\ndone', '\nesac',
            ' then', ' do', ' fi', ' done', ' esac',
            'then\n', 'do\n', 'fi\n', 'done\n', 'esac\n'
        ]
        if stripped.startswith('(') or stripped.startswith('{'):
            return True
        if '\n{' in stripped or '\n(' in stripped:
            return True
    
    # 检查是否以开始关键词开头
    for keyword in start_keywords:
        if stripped.startswith(keyword):
            return True
    
    # 检查是否包含结构关键词
    for keyword in structure_keywords:
        if keyword in stripped:
            return True
    
    return False


# === 向后兼容：保留旧函数名 ===
def is_bash_logic_structure(cmd_str: str) -> bool:
    """
    [已弃用] 请使用 is_shell_logic_structure(cmd_str, sys_type)
    为保持向后兼容，默认使用 bash 类型
    """
    return is_shell_logic_structure(cmd_str, 'bash')


def _is_single_line_block(cmd_str: str, sys_type: str = 'bash') -> bool:
    """
    检测是否是单行花括号块 { command; }
    
    Args:
        cmd_str: 命令字符串
        sys_type: 终端类型
    
    Returns:
        如果是单行花括号块返回 True
    """
    if sys_type in ('cmd', 'powershell'):
        return False
    
    stripped = cmd_str.strip()
    if stripped.startswith('{') and stripped.endswith('}'):
        return True
    
    return False


def _is_subshell(cmd_str: str, sys_type: str = 'bash') -> bool:
    """
    检测是否是子shell (command)
    
    Args:
        cmd_str: 命令字符串
        sys_type: 终端类型
    
    Returns:
        如果是子shell返回 True
    """
    if sys_type in ('cmd',):
        return False
    
    stripped = cmd_str.strip()
    if stripped.startswith('(') and stripped.endswith(')'):
        return True
    
    return False


def extract_sub_commands_from_pipeline(cmd_str: str, sys_type: str = 'bash') -> List[str]:
    """从管道命令中提取子命令列表"""
    if not cmd_str:
        return []
    
    rules = _get_terminal_escape_rules(sys_type)
    escape_char = rules['escape_char']
    has_single_quotes = rules['has_single_quotes']
    
    commands = []
    current = []
    in_single = False
    in_double = False
    escaped = False
    i = 0
    n = len(cmd_str)
    
    while i < n:
        char = cmd_str[i]
        
        if escaped:
            escaped = False
            current.append(char)
            i += 1
            continue
            
        if char == escape_char:
            escaped = True
            current.append(char)
            i += 1
            continue
        
        if char == '\\' and sys_type not in ('cmd',):
            escaped = True
            current.append(char)
            i += 1
            continue
            
        if char == "'" and not in_double and has_single_quotes:
            in_single = not in_single
            current.append(char)
            i += 1
            continue
            
        if char == '"' and not in_single:
            in_double = not in_double
            current.append(char)
            i += 1
            continue
            
        if char == '|' and not in_single and not in_double:
            if current:
                commands.append(''.join(current).strip())
                current = []
            i += 1
        else:
            current.append(char)
            i += 1
    
    if current:
        commands.append(''.join(current).strip())
    
    return commands


def split_by_semicolon(cmd_str: str, sys_type: str = 'bash') -> List[str]:
    """
    按 ; 分号分割命令（保留引号内的分号）
    
    注意：
    - cmd 中 ; 不是标准分隔符，但可以作为命令分隔符
    - PowerShell 中 ; 是语句分隔符
    """
    if not cmd_str:
        return []
    
    rules = _get_terminal_escape_rules(sys_type)
    escape_char = rules['escape_char']
    has_single_quotes = rules['has_single_quotes']
    
    commands = []
    current = []
    in_single = False
    in_double = False
    escaped = False
    i = 0
    n = len(cmd_str)
    
    while i < n:
        char = cmd_str[i]
        
        if escaped:
            escaped = False
            current.append(char)
            i += 1
            continue
            
        if char == escape_char:
            escaped = True
            current.append(char)
            i += 1
            continue
            
        if char == '\\' and sys_type not in ('cmd',):
            escaped = True
            current.append(char)
            i += 1
            continue
            
        if char == "'" and not in_double and has_single_quotes:
            in_single = not in_single
            current.append(char)
            i += 1
            continue
            
        if char == '"' and not in_single:
            in_double = not in_double
            current.append(char)
            i += 1
            continue
            
        if char == ';' and not in_single and not in_double:
            if current:
                commands.append(''.join(current).strip())
                current = []
            i += 1
        else:
            current.append(char)
            i += 1
    
    if current:
        commands.append(''.join(current).strip())
    
    return commands


def split_by_logical_operators(cmd_str: str, sys_type: str = 'bash') -> List[Tuple[str, str]]:
    """
    按逻辑操作符分割命令
    
    支持的操作符：
    - bash/zsh/sh/cmd: &&, ||
    - PowerShell: -and, -or（也兼容 &&, || in PS7+）
    
    返回 [(子命令, 操作符), ...]
    最后一项的操作符为空字符串
    """
    if not cmd_str:
        return []
    
    rules = _get_terminal_escape_rules(sys_type)
    escape_char = rules['escape_char']
    has_single_quotes = rules['has_single_quotes']
    
    parts = []
    current = []
    in_single = False
    in_double = False
    escaped = False
    i = 0
    n = len(cmd_str)
    
    while i < n:
        char = cmd_str[i]
        
        if escaped:
            escaped = False
            current.append(char)
            i += 1
            continue
            
        if char == escape_char:
            escaped = True
            current.append(char)
            i += 1
            continue
            
        if char == '\\' and sys_type not in ('cmd',):
            escaped = True
            current.append(char)
            i += 1
            continue
            
        if char == "'" and not in_double and has_single_quotes:
            in_single = not in_single
            current.append(char)
            i += 1
            continue
            
        if char == '"' and not in_single:
            in_double = not in_double
            current.append(char)
            i += 1
            continue
        
        if not (in_single or in_double):
            # 检查 && 和 ||
            if i + 1 < n and cmd_str[i:i+2] == '&&':
                if current:
                    parts.append((''.join(current).strip(), '&&'))
                    current = []
                else:
                    parts.append(('', '&&'))
                i += 2
                continue
            
            if i + 1 < n and cmd_str[i:i+2] == '||':
                if current:
                    parts.append((''.join(current).strip(), '||'))
                    current = []
                else:
                    parts.append(('', '||'))
                i += 2
                continue
            
            # 检查 PowerShell 的 -and 和 -or（不区分大小写）
            if sys_type == 'powershell':
                remaining = cmd_str[i:].lower()
                if remaining.startswith('-and') and (i + 4 >= n or cmd_str[i+4].isspace() or cmd_str[i+4] in '();{}'):
                    if current:
                        parts.append((''.join(current).strip(), '-and'))
                        current = []
                    else:
                        parts.append(('', '-and'))
                    i += 4
                    continue
                
                if remaining.startswith('-or') and (i + 3 >= n or cmd_str[i+3].isspace() or cmd_str[i+3] in '();{}'):
                    if current:
                        parts.append((''.join(current).strip(), '-or'))
                        current = []
                    else:
                        parts.append(('', '-or'))
                    i += 3
                    continue
        
        current.append(char)
        i += 1
    
    if current:
        parts.append((''.join(current).strip(), ''))
    
    return parts


def parse_redirects_from_command(cmd_str: str, sys_type: str = 'bash') -> Tuple[str, Dict]:
    """
    解析命令中的重定向和Here Document
    
    重定向规则：
    - bash/zsh/sh: >, >>, <, <<, 2>, 2>>, &>
    - cmd: >, >>, <, 2>, 2>>&1（不支持 << here-doc）
    - PowerShell: >, >>, 2>, 2>>, *>, 4>, 5>, 6>（不支持 << here-doc）
    
    返回 (去除重定向的命令字符串, 重定向配置字典)
    
    配置字典包含：
    - stdout: (file_path, mode) 或 None
    - stderr: (file_path, mode) 或 None 或 'STDOUT'
    - stdin: file_path 或 None
    - here_delimiter: str 或 None
    - here_doc: str 或 None (如果已提取内容)
    
    注意：不修改文件路径的引号，保持原样
    """
    redirect_config = {
        'stdout': None,
        'stderr': None,
        'stdin': None,
        'here_delimiter': None,
        'here_doc': None
    }
    
    if not cmd_str:
        return cmd_str, redirect_config
    
    # 快速路径：绝大多数命令不含重定向操作符
    if '>' not in cmd_str and '<' not in cmd_str:
        return cmd_str, redirect_config
    
    # === Here Document 处理（仅 Unix shell）===
    if sys_type not in ('cmd', 'powershell'):
        # 首先处理Here Document（完整模式：<<DELIM\ncontent\nDELIM）
        here_doc_pattern = r'(?:^|\s+)<<\s*[\'\"]?([A-Za-z0-9_]+)[\'\"]?\s*\n(.*?)\n\s*\1\s*$'
        here_doc_matches = list(re.finditer(here_doc_pattern, cmd_str, re.DOTALL | re.MULTILINE))
        
        if here_doc_matches:
            # 只处理最后一个Here Document
            match = here_doc_matches[-1]
            delimiter = match.group(1)
            content = match.group(2)
            redirect_config['here_delimiter'] = delimiter
            redirect_config['here_doc'] = content
            start, end = match.span()
            cmd_str = cmd_str[:start] + cmd_str[end:]
        else:
            # 检查是否有未完成的Here Document（只有<<后面没有内容）
            here_start_pattern = r'(?:^|\s+)<<\s*[\'\"]?([A-Za-z0-9_]+)[\'\"]?\s*$'
            match = re.search(here_start_pattern, cmd_str)
            if match:
                delimiter = match.group(1)
                redirect_config['here_delimiter'] = delimiter
                # 标记需要等待输入
                cmd_str = cmd_str[:match.start()]
    
    rules = _get_terminal_escape_rules(sys_type)
    escape_char = rules['escape_char']
    has_single_quotes = rules['has_single_quotes']
    
    # 辅助函数：提取文件名（保留引号）
    def extract_filename(s: str, pos: int) -> Tuple[str, int]:
        while pos < len(s) and s[pos].isspace():
            pos += 1
        if pos >= len(s):
            return "", pos
        
        if s[pos] in '"\'':
            quote = s[pos]
            pos += 1
            end_pos = s.find(quote, pos)
            if end_pos != -1:
                filename = s[pos:end_pos]
                # 返回带引号的文件名
                return f"{quote}{filename}{quote}", end_pos + 1
            else:
                return s[pos:], len(s)
        else:
            end_pos = pos
            while end_pos < len(s) and not s[end_pos].isspace() and s[end_pos] not in '|;&<>':
                end_pos += 1
            return s[pos:end_pos], end_pos
    
    # 解析重定向符号
    result_parts = []
    i = 0
    n = len(cmd_str)
    in_single = False
    in_double = False
    escaped = False
    
    while i < n:
        char = cmd_str[i]
        
        if escaped:
            escaped = False
            result_parts.append(char)
            i += 1
            continue
            
        if char == escape_char:
            escaped = True
            result_parts.append(char)
            i += 1
            continue
            
        if char == '\\' and sys_type not in ('cmd',):
            escaped = True
            result_parts.append(char)
            i += 1
            continue
            
        if char == "'" and not in_double and has_single_quotes:
            in_single = not in_single
            result_parts.append(char)
            i += 1
            continue
            
        if char == '"' and not in_single:
            in_double = not in_double
            result_parts.append(char)
            i += 1
            continue
        
        if not (in_single or in_double):
            # 2>&1（Unix）或 2>&1（cmd）
            if (i + 2 < n and i + 3 <= n and 
                cmd_str[i:i+2] == '2>' and cmd_str[i+2] == '&' and 
                (i + 3 >= n or cmd_str[i+3] == '1')):
                redirect_config['stderr'] = 'STDOUT'
                i += 4 if i + 3 < n and cmd_str[i+3] == '1' else 2
                continue
            
            # 2>>
            if i + 2 < n and cmd_str[i:i+3] == '2>>':
                filename, new_i = extract_filename(cmd_str, i + 3)
                if filename:
                    redirect_config['stderr'] = (filename, 'a')
                    i = new_i
                    continue
            
            # 2>
            if i + 1 < n and cmd_str[i:i+2] == '2>':
                filename, new_i = extract_filename(cmd_str, i + 2)
                if filename:
                    redirect_config['stderr'] = (filename, 'w')
                    i = new_i
                    continue
            
            # PowerShell 的特殊重定向：*>, 3>, 4>, 5>, 6>
            if sys_type == 'powershell':
                if i + 1 < n and cmd_str[i] in '*3456' and cmd_str[i+1] == '>':
                    filename, new_i = extract_filename(cmd_str, i + 2)
                    if filename:
                        redirect_config['stdout'] = (filename, 'w')
                        i = new_i
                        continue
            
            # >>
            if i + 1 < n and cmd_str[i:i+2] == '>>':
                filename, new_i = extract_filename(cmd_str, i + 2)
                if filename:
                    redirect_config['stdout'] = (filename, 'a')
                    i = new_i
                    continue
            
            # >
            if char == '>' and (i + 1 >= n or cmd_str[i+1] != '>'):
                filename, new_i = extract_filename(cmd_str, i + 1)
                if filename:
                    redirect_config['stdout'] = (filename, 'w')
                    i = new_i
                    continue
            
            # <
            if char == '<' and (i + 1 >= n or cmd_str[i+1] != '<'):
                filename, new_i = extract_filename(cmd_str, i + 1)
                if filename:
                    redirect_config['stdin'] = filename
                    i = new_i
                    continue
        
        result_parts.append(char)
        i += 1
    
    return ''.join(result_parts), redirect_config


def extract_all_paths_from_pipeline(pipeline_cmd_str: str, resolve_path_func=None, sys_type: str = 'bash') -> List[str]:
    """从管道命令中提取所有子命令的所有路径"""
    all_paths = []
    
    sub_commands = extract_sub_commands_from_pipeline(pipeline_cmd_str, sys_type)
    
    for sub_cmd in sub_commands:
        # 去除重定向后提取参数路径
        clean_cmd, redirect_config = parse_redirects_from_command(sub_cmd, sys_type)
        
        # 提取参数路径
        arg_paths = extract_all_argument_paths(clean_cmd, resolve_path_func, sys_type)
        all_paths.extend(arg_paths)
        
        # 提取重定向路径
        redirect_paths = extract_redirect_paths(redirect_config)
        all_paths.extend(redirect_paths)
    
    return all_paths


def resolve_alias_in_cmd(cmd_str: str, alias_cache: Dict, log_info_func=None, request_id: str = None, sys_type: str = 'bash') -> str:
    """
    解析命令中的别名
    
    修复：避免使用 shlex.join 转义管道符等 shell 元字符，改为直接拼接原始参数。
    """
    if not cmd_str or not alias_cache:
        return cmd_str
    
    parts = smart_shlex_split(cmd_str, sys_type)
    if not parts:
        return cmd_str
    
    cmd_head = parts[0]
    
    if cmd_head in alias_cache:
        alias_info = alias_cache.get(cmd_head, {})
        target_cmd = alias_info.get("target", "")
        
        if target_cmd:
            if log_info_func:
                log_info_func(f"别名解析：{cmd_head} → {target_cmd}", request_id)
            
            # 提取原命令中命令头后面的原始字符串（保留引号、管道等）
            stripped = cmd_str.lstrip()
            first_space = stripped.find(' ')
            if first_space != -1:
                args_str = stripped[first_space:].lstrip()
            else:
                args_str = ''
            
            return f"{target_cmd} {args_str}".strip()
    
    return cmd_str


def has_redirect(cmd_str: str, sys_type: str = 'bash') -> bool:
    """
    检查命令是否包含重定向符号
    
    重定向符号：
    - bash/zsh/sh: >, >>, <, <<, 2>, 2>>
    - cmd: >, >>, <
    - PowerShell: >, >>, <, 2>, 2>>, *>, 3>, 4>, 5>, 6>
    """
    if not cmd_str:
        return False
    
    rules = _get_terminal_escape_rules(sys_type)
    escape_char = rules['escape_char']
    has_single_quotes = rules['has_single_quotes']
    
    in_single = False
    in_double = False
    escaped = False
    
    for i, char in enumerate(cmd_str):
        if escaped:
            escaped = False
            continue
        if char == escape_char:
            escaped = True
        elif char == '\\' and sys_type not in ('cmd',):
            escaped = True
        elif char == "'" and not in_double and has_single_quotes:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if char in ('>', '<'):
                return True
            if i + 1 < len(cmd_str):
                if cmd_str[i:i+2] in ('2>', '>>'):
                    return True
                if i + 2 < len(cmd_str) and cmd_str[i:i+3] in ('2>>',):
                    return True
                # PowerShell 特殊重定向
                if sys_type == 'powershell' and cmd_str[i] in '*3456' and cmd_str[i+1] == '>':
                    return True
    
    return False


def has_here_doc(cmd_str: str, sys_type: str = 'bash') -> bool:
    """
    检查命令是否包含 Here Document（<<）
    
    注意：cmd 和 PowerShell 不支持 Here Document
    """
    if not cmd_str or '<<' not in cmd_str:
        return False
    
    # cmd 和 PowerShell 没有 Here Document
    if sys_type in ('cmd', 'powershell'):
        return False
    
    rules = _get_terminal_escape_rules(sys_type)
    escape_char = rules['escape_char']
    has_single_quotes = rules['has_single_quotes']
    
    in_single = False
    in_double = False
    escaped = False
    
    for i, char in enumerate(cmd_str):
        if escaped:
            escaped = False
            continue
        if char == escape_char:
            escaped = True
        elif char == '\\' and sys_type not in ('cmd',):
            escaped = True
        elif char == "'" and not in_double and has_single_quotes:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if i + 1 < len(cmd_str) and cmd_str[i:i+2] == '<<':
                # 确保不是 <<< （here string）
                if i + 2 >= len(cmd_str) or cmd_str[i+2] != '<':
                    return True
    
    return False


def check_advanced_syntax(cmd_str: str, redirect_config: Dict = None, sys_type: str = 'bash') -> Dict[str, bool]:
    """
    检查命令是否使用了高级语法
    返回包含各种高级语法检测结果的字典
    """
    result = {
        'has_pipeline': has_pipeline(cmd_str, sys_type),
        'has_redirect': False,
        'has_here_doc': False,
        'has_logical_operators': has_logical_operators(cmd_str, sys_type),
        'has_any_advanced': False
    }
    
    if redirect_config:
        result['has_redirect'] = bool(
            redirect_config.get('stdout') or 
            redirect_config.get('stderr') or 
            redirect_config.get('stdin')
        )
        result['has_here_doc'] = bool(redirect_config.get('here_delimiter'))
    else:
        result['has_redirect'] = has_redirect(cmd_str, sys_type)
        result['has_here_doc'] = has_here_doc(cmd_str, sys_type)
    
    result['has_any_advanced'] = (
        result['has_pipeline'] or 
        result['has_redirect'] or 
        result['has_here_doc'] or 
        result['has_logical_operators']
    )
    
    return result


def expand_command_with_path_tracking(cmd: str, get_virtual_path_func=None, sys_type: str = 'bash') -> Tuple[str, Dict[str, str]]:
    """
    展开命令并追踪路径变化
    返回 (展开后的命令, 路径映射字典)
    """
    clear_path_mapping_cache()
    
    # 执行各种展开操作
    expanded_cmd = remove_comments(cmd, sys_type)
    expanded_cmd = expand_tilde(expanded_cmd, os.path.expanduser('~'), sys_type)
    expanded_cmd = expand_variables(expanded_cmd, os.environ, globals(), sys_type)
    expanded_cmd = expand_braces(expanded_cmd, sys_type)
    expanded_cmd = expand_wildcards(expanded_cmd, get_virtual_path_func, sys_type)
    
    return expanded_cmd, dict(_PATH_MAPPING_CACHE)


def restore_command_paths(expanded_cmd: str) -> str:
    """
    将展开后的命令中的路径还原为原始路径
    """
    return restore_paths_in_text(expanded_cmd)