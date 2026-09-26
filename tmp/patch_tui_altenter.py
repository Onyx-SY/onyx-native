# -*- coding: utf-8 -*-
"""修复：Textual 把 ESC+Enter 解析成普通 enter（丢掉 alt 修饰符）→ Alt+Enter 失效。

- 给 XTermParser 打最小补丁：alt 标记存在时，特殊键也补 "alt+" 前缀；
- App / PromptArea 同时绑定 alt+enter 与 alt+ctrl+j（Termux ICRNL 下 ESC+LF 编码）。

用法：python3 tmp/patch_tui_altenter.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bin", "ai_tui.py")

HELPER = '''def _ai_history_path(user_home_dir: str) -> str:
    """AI 历史文件路径（与 REPL 的 FileHistory 共用同一份，虚影由此产生）。"""
    try:
        return os.path.join(user_home_dir, ".config", "onyx", "ai", "history")
    except Exception:
        return ""


def _enable_alt_enter_keys() -> None:
    """让 Textual 把 ESC+Enter 识别为 alt+enter（Termux/Android 等终端的常见编码）。

    背景：Textual 的 XTermParser 遇到 ESC+回车时只产出 "enter" —— 它的
    `_sequence_to_key_events` 只对「单字符键」补 "alt+" 前缀，特殊键（enter/ctrl+j…）
    的 alt 修饰符被丢弃 → Alt+Enter 与普通回车无法区分，多行切换必然失效。
    这里做一次最小补丁：alt 标记存在时，给特殊键也补 "alt+" 前缀。
    仅影响本进程（`ai -tui` 独占进程），任何异常都静默回退（退回原行为）。
    """
    try:
        from textual import events as _events
        from textual._xterm_parser import XTermParser
    except Exception:
        return
    if getattr(XTermParser, "_onyx_alt_patched", False):
        return
    _orig = XTermParser._sequence_to_key_events

    def _patched(self, sequence, alt=False):
        for key in _orig(self, sequence, alt=alt):
            key_name = getattr(key, "key", "")
            if alt and key_name and not str(key_name).startswith("alt+"):
                yield _events.Key("alt+" + str(key_name), getattr(key, "character", None))
            else:
                yield key

    try:
        XTermParser._sequence_to_key_events = _patched
        XTermParser._onyx_alt_patched = True
    except Exception:
        return


'''

REPLACEMENTS = [
    # 1) 插入补丁函数
    (
        'def _ai_history_path(user_home_dir: str) -> str:\n'
        '    """AI 历史文件路径（与 REPL 的 FileHistory 共用同一份，虚影由此产生）。"""\n'
        '    try:\n'
        '        return os.path.join(user_home_dir, ".config", "onyx", "ai", "history")\n'
        '    except Exception:\n'
        '        return ""\n',
        HELPER,
    ),
    # 2) PromptArea 增加 alt+ctrl+j 绑定（Termux ICRNL：ESC+LF）
    (
        '        BINDINGS = [\n'
        '            Binding("alt+enter", "submit_all", "Send", priority=True, show=False),\n'
        '            Binding("ctrl+j", "submit_all", "Send", priority=True, show=False),\n'
        '        ]\n',
        '        # alt+enter：常规 Alt+Enter；alt+ctrl+j：Termux/Android 下 ICRNL 把 \\r 转成 \\n\n'
        '        # 后 ESC+Enter 的到达形式（两者都要，否则其中一种终端切换失效）\n'
        '        BINDINGS = [\n'
        '            Binding("alt+enter", "submit_all", "Send", priority=True, show=False),\n'
        '            Binding("alt+ctrl+j", "submit_all", "Send", priority=True, show=False),\n'
        '            Binding("ctrl+alt+j", "submit_all", "Send", priority=True, show=False),\n'
        '        ]\n',
    ),
    # 3) App 增加 alt+ctrl+j 绑定
    (
        '                    Binding("alt+enter", "to_multiline", "Multiline", show=False)]\n',
        '                    Binding("alt+enter", "to_multiline", "Multiline", show=False),\n'
        '                    Binding("alt+ctrl+j", "to_multiline", "Multiline", show=False),\n'
        '                    Binding("ctrl+alt+j", "to_multiline", "Multiline", show=False)]\n',
    ),
    # 4) 会话入口：运行前打补丁
    (
        '    _mode.set_render_mode("tui")\n'
        '    try:\n'
        '        AppClass = _build_tui()\n',
        '    _mode.set_render_mode("tui")\n'
        '    _enable_alt_enter_keys()   # Alt+Enter → alt+enter（Textual 会丢掉 ESC+CR 的 alt）\n'
        '    try:\n'
        '        AppClass = _build_tui()\n',
    ),
]


def main() -> int:
    with io.open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()
    for i, (old, new) in enumerate(REPLACEMENTS, 1):
        cnt = src.count(old)
        if cnt != 1:
            print(f"❌ 第 {i} 处：匹配 {cnt} 次（要求 1 次）")
            return 1
        src = src.replace(old, new)
    tmp = TARGET + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(tmp, TARGET)
    print(f"✅ 已应用 {len(REPLACEMENTS)} 处替换 → {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
