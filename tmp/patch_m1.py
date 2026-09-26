# -*- coding: utf-8 -*-
"""M1：修 TUI 多行输入不可用。

根因（实测）：Textual 的 XTermParser 对 ESC+CR（Alt+Enter）会丢掉 alt 修饰符 ——
`feed("\x1b\r")` 先返回空，下一次 feed 才吐出**裸 `enter`** → 在单行框里等价于「直接发送」，
多行模式永远进不去；若终端把 ESC 与 CR/LF 分开送达（ICRNL 场景）还会先冒出一个 `^`。
而 CSI-u 的 `\x1b[13;2u` 能被可靠解析成 `shift+enter`。

修法：在**字节层**净化器里把 ESC+CR / ESC+LF 统一重写成 CSI-u，再给两个输入框绑 shift+enter。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUI = os.path.join(ROOT, "bin", "ai_tui.py")

P = [
    # 1) 常量
    (
        """_MOUSE_X10 = b"\\x1b[M"
_MOUSE_SGR = b"\\x1b[<"
_MAX_PENDING = 64          # 未闭合序列的最大缓存（超出即视为普通字节放行）""",
        """_MOUSE_X10 = b"\\x1b[M"
_MOUSE_SGR = b"\\x1b[<"
# Alt+Enter 的可靠载体：CSI-u 形式的 shift+enter。
# 为什么不用原生的 ESC+CR：Textual 的 XTermParser 解析 ESC+CR 时会**丢掉 alt 修饰符**，
# 最终只产出一个裸 `enter`（实测 feed("\\x1b\\r") 返回空，下一次 feed 才吐出 "enter"）
# → 在单行框里等价于「直接发送」，多行模式永远进不去。CSI-u 则被稳定解析成 shift+enter。
_ALT_ENTER_CSI_U = b"\\x1b[13;2u"
_MAX_PENDING = 64          # 未闭合序列的最大缓存（超出即视为普通字节放行）""",
    ),
    # 2) 字节层改写（必须放在鼠标分支之前）
    (
        """        while i < n:
            b = buf[i]
            if b == 0x1B and self.strip_mouse:""",
        """        while i < n:
            b = buf[i]
            # Alt+Enter：终端发的是 ESC+CR（部分终端因 ICRNL 变成 ESC+LF）。
            # 统一重写为 CSI-u shift+enter —— 否则 Textual 只会给出裸 enter（= 直接发送），
            # 「Alt+Enter 进多行」这个手势在真机上永远不生效。
            if b == 0x1B and i + 1 < n and buf[i + 1] in (0x0D, 0x0A):
                out += _ALT_ENTER_CSI_U
                i += 2
                continue
            if b == 0x1B and self.strip_mouse:""",
    ),
    # 3) 多行框：shift+enter = 整体发送
    (
        """        BINDINGS = [
            Binding("alt+enter", "submit_all", "Send", priority=True, show=False),
            Binding("alt+ctrl+j", "submit_all", "Send", priority=True, show=False),
            Binding("ctrl+alt+j", "submit_all", "Send", priority=True, show=False),
        ]""",
        """        BINDINGS = [
            Binding("alt+enter", "submit_all", "Send", priority=True, show=False),
            # 字节层把 ESC+CR / ESC+LF 改写成了 CSI-u shift+enter（见 _ALT_ENTER_CSI_U）
            Binding("shift+enter", "submit_all", "Send", priority=True, show=False),
            Binding("alt+ctrl+j", "submit_all", "Send", priority=True, show=False),
            Binding("ctrl+alt+j", "submit_all", "Send", priority=True, show=False),
        ]""",
    ),
    # 4) 单行框：shift+enter = 进多行
    (
        """                    Binding("alt+enter", "to_multiline", "Multiline", show=False),
                    Binding("alt+ctrl+j", "to_multiline", "Multiline", show=False),
                    Binding("ctrl+alt+j", "to_multiline", "Multiline", show=False)]""",
        """                    Binding("alt+enter", "to_multiline", "Multiline", show=False),
                    # 字节层把 ESC+CR / ESC+LF 改写成了 CSI-u shift+enter（见 _ALT_ENTER_CSI_U）
                    Binding("shift+enter", "to_multiline", "Multiline", show=False),
                    Binding("alt+ctrl+j", "to_multiline", "Multiline", show=False),
                    Binding("ctrl+alt+j", "to_multiline", "Multiline", show=False)]""",
    ),
    # 5) 补丁说明同步（避免后人以为 ESC+CR 已足够）
    (
        """    这里做一次最小补丁：alt 标记存在时，给特殊键也补 \"alt+\" 前缀。
    仅影响本进程（`ai -tui` 独占进程），任何异常都静默回退（退回原行为）。
    \"\"\"""",
        """    这里做一次最小补丁：alt 标记存在时，给特殊键也补 \"alt+\" 前缀。
    仅影响本进程（`ai -tui` 独占进程），任何异常都静默回退（退回原行为）。

    注意：这个补丁**救不了 ESC+CR**（解析器内部就把 alt 丢了）。Alt+Enter 的实际通路是
    字节层净化器把 ESC+CR / ESC+LF 改写成 CSI-u shift+enter（见 _ALT_ENTER_CSI_U）。
    \"\"\"""",
    ),
]


def main():
    with open(TUI, encoding="utf-8") as f:
        text = f.read()
    for old, new in P:
        n = text.count(old)
        if n != 1:
            print(f"FAIL: 命中 {n} 次\n{old[:200]}")
            return 1
        text = text.replace(old, new, 1)
    with open(TUI, "w", encoding="utf-8") as f:
        f.write(text)
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
