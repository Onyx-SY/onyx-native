# -*- coding: utf-8 -*-
"""step-4a：把 AI REPL 的补全候选逻辑抽成纯函数，供 REPL 与 TUI 共用。

- 新增 `completion_candidates(text, lang, slash_cmds) -> [(插入文本, 描述, 替换长度)]`
- 新增 `_path_candidates(word)`；`_iter_path_completions` 改为其包装（保持兼容）
- `_AICompleter.get_completions` 改为调用共用函数（行为不变：斜杠头/参数枚举/路径三类）

用法：python3 tmp/patch_shared_completion.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bin", "ai_interactive.py")

OLD = '''def _iter_path_completions(word: str):
    """文件系统路径补全：目录追加 '/'，隐藏项仅在前缀为 '.' 时给出；空词 → 列出当前目录。"""
    expanded = os.path.expanduser(word) if word.startswith("~") else word
    if expanded.endswith(os.sep) or expanded.endswith("/"):
        search_dir, base = expanded, ""
    else:
        search_dir, base = os.path.split(expanded)
    if not search_dir:
        search_dir = "."
    try:
        entries = os.listdir(search_dir)
    except OSError:
        return
    # 回写前缀：把展开后的目录部分映射回用户原始输入形式（保留 ~ 写法）
    if word.endswith("/"):
        pass
    show_hidden = base.startswith(".")
    for name in sorted(entries):
        if not show_hidden and name.startswith("."):
            continue
        if not name.startswith(base):
            continue
        full = os.path.join(search_dir, name)
        is_dir = os.path.isdir(full)
        yield Completion(
            name + ("/" if is_dir else ""),
            start_position=-len(base),
            display=name + ("/" if is_dir else ""),
            display_meta="dir" if is_dir else "",
        )


class _AICompleter(Completer):
    """AI REPL 复合补全器：`/` 开头补斜杠命令，否则补文件系统路径。

    对齐 lib/terminal 的 SmartCompleter 效果（命令 + 路径统一补全、带 meta 描述）。
    """

    # 参数位置需要路径补全的斜杠命令
    _PATH_ARG_CMDS = ("/cd", "/resume", "/save", "/export")

    def __init__(self, slash_cmds: Dict[str, str], lang: str = "chinese"):
        self._slash = slash_cmds
        self._lang = lang

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        stripped = text.lstrip()
        word = _current_word(text)

        # 1) 斜杠命令头补全（尚未输入空格时，大小写不敏感）
        if stripped.startswith("/") and " " not in stripped:
            low = stripped.lower()
            for cmd, desc in self._slash.items():
                if cmd.lower().startswith(low):
                    yield Completion(cmd, start_position=-len(word),
                                     display=cmd, display_meta=desc)
            return

        # 2) 斜杠命令的参数位置（如 /cd <path>、/lang cn|en）→ 路径 / 枚举补全
        if stripped.startswith("/") and " " in stripped:
            parts = stripped.split()
            head = parts[0].lower()
            ends_space = stripped[-1].isspace()
            arg_idx = len(parts) if ends_space else len(parts) - 1
            low = word.lower()

            # 2a) 固定枚举参数（/lang cn|en、/mode normal|plan、/param <名称> <值> …）
            if head in _SLASH_ARG_ENUMS:
                if arg_idx == 1:
                    cands = _SLASH_ARG_ENUMS[head]
                elif arg_idx == 2:
                    cands = _SLASH_ARG2_ENUMS.get((head, parts[1].lower()), [])
                else:
                    cands = []
                for val in cands:
                    if val.lower().startswith(low):
                        yield Completion(val, start_position=-len(word), display=val)
                return

            # 2b) /help <命令> → 补全斜杠命令名
            if head == "/help":
                for cmd in self._slash:
                    if cmd.lower().startswith(low):
                        yield Completion(cmd, start_position=-len(word), display=cmd)
                return

            # 2c) 路径参数（/cd <路径>）
            if head in self._PATH_ARG_CMDS:
                yield from _iter_path_completions(word)
            return

        # 3) 普通输入：词看起来像路径才补（对齐 shell 的路径补全）
        if word and (word.startswith(("~", ".", "/")) or "/" in word):
            yield from _iter_path_completions(word)
'''

NEW = '''# 参数位置需要路径补全的斜杠命令
_PATH_ARG_CMDS = ("/cd", "/resume", "/save", "/export")


def _path_candidates(word: str):
    """路径候选：[(插入文本, 描述, 需替换掉末尾的字符数)]。目录追加 '/'。"""
    expanded = os.path.expanduser(word) if word.startswith("~") else word
    if expanded.endswith(os.sep) or expanded.endswith("/"):
        search_dir, base = expanded, ""
    else:
        search_dir, base = os.path.split(expanded)
    if not search_dir:
        search_dir = "."
    try:
        entries = os.listdir(search_dir)
    except OSError:
        return []
    show_hidden = base.startswith(".")
    out = []
    for name in sorted(entries):
        if not show_hidden and name.startswith("."):
            continue
        if not name.startswith(base):
            continue
        is_dir = os.path.isdir(os.path.join(search_dir, name))
        out.append((name + ("/" if is_dir else ""), "dir" if is_dir else "", len(base)))
    return out


def _iter_path_completions(word: str):
    """文件系统路径补全（prompt_toolkit 用）。"""
    for value, meta, replace_len in _path_candidates(word):
        yield Completion(value, start_position=-replace_len, display=value, display_meta=meta)


def completion_candidates(text: str, lang: str = "chinese", slash_cmds=None):
    """补全候选（AI REPL 补全器与 TUI 下拉菜单共用，单一来源避免两边漂移）。

    Args:
        text: 光标前的整段输入
        lang: chinese | english（决定斜杠命令描述用哪份表）
        slash_cmds: 可选的命令表覆盖（默认按 lang 取 _SLASH_COMMANDS_*）

    Returns:
        [(插入文本, 描述, 需替换掉末尾的字符数)]；TUI 侧按
        `text[:-replace_len] + 插入文本` 得到补全后的整行。
    """
    cmds = slash_cmds
    if cmds is None:
        cmds = _SLASH_COMMANDS_EN if lang == "english" else _SLASH_COMMANDS_CN
    stripped = text.lstrip()
    word = _current_word(text)

    # 1) 斜杠命令头补全（尚未输入空格时，大小写不敏感）
    if stripped.startswith("/") and " " not in stripped:
        low = stripped.lower()
        return [(cmd, desc, len(word)) for cmd, desc in cmds.items() if cmd.lower().startswith(low)]

    # 2) 斜杠命令的参数位置（/cd <path>、/lang cn|en …）
    if stripped.startswith("/") and " " in stripped:
        parts = stripped.split()
        head = parts[0].lower()
        ends_space = stripped[-1].isspace()
        arg_idx = len(parts) if ends_space else len(parts) - 1
        low = word.lower()

        # 2a) 固定枚举参数
        if head in _SLASH_ARG_ENUMS:
            if arg_idx == 1:
                cands = _SLASH_ARG_ENUMS[head]
            elif arg_idx == 2:
                cands = _SLASH_ARG2_ENUMS.get((head, parts[1].lower()), [])
            else:
                cands = []
            return [(v, "", len(word)) for v in cands if v.lower().startswith(low)]

        # 2b) /help <命令> → 补全命令名
        if head == "/help":
            return [(cmd, "", len(word)) for cmd in cmds if cmd.lower().startswith(low)]

        # 2c) 路径参数
        if head in _PATH_ARG_CMDS:
            return _path_candidates(word)
        return []

    # 3) 普通输入：词看起来像路径才补
    if word and (word.startswith(("~", ".", "/")) or "/" in word):
        return _path_candidates(word)
    return []


class _AICompleter(Completer):
    """AI REPL 复合补全器：`/` 开头补斜杠命令，否则补文件系统路径。

    候选逻辑在 completion_candidates()（与 TUI 下拉菜单共用）。
    """

    def __init__(self, slash_cmds: Dict[str, str], lang: str = "chinese"):
        self._slash = slash_cmds
        self._lang = lang

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        for value, meta, replace_len in completion_candidates(text, self._lang, self._slash):
            yield Completion(value, start_position=-replace_len, display=value, display_meta=meta)
'''


def main() -> int:
    with io.open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()
    if src.count(OLD) != 1:
        print(f"❌ 匹配 {src.count(OLD)} 次（要求 1 次）")
        return 1
    src = src.replace(OLD, NEW)
    with io.open(TARGET + ".tmp", "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(TARGET + ".tmp", TARGET)
    print("✅ 已抽出 completion_candidates / _path_candidates，_AICompleter 改为复用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
