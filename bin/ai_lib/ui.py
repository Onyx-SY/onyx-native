"""
bin/ai_lib/ui.py — Onyx AI 终端 UI 增强模块

基于 Rich + InquirerPy 的美化交互组件。
InquirerPy 未安装时自动回退到 prompt_toolkit 原始实现。

设计原则:
  - 所有函数返回与原始实现相同的类型和语义
  - 优雅降级：不因缺少依赖而崩溃
  - 双语支持：中/英文界面自动适配
"""

import os
import sys
import contextlib
from typing import List, Optional, Dict, Tuple

from rich.console import Console as RichConsole
from rich.panel import Panel
from rich.table import Table

from rich.box import ROUNDED, HEAVY, DOUBLE
from rich.text import Text
from rich.rule import Rule


def _markdown(text, **kw):
    """惰性导入 rich.markdown（启动不加载 markdown_it/pygments）。"""
    from rich.markdown import Markdown as _M
    return _M(text, **kw)

console = RichConsole()


# ============================================================
# UI 适配器接缝（REPL / TUI 解耦点）
# ============================================================
# 未设置（REPL）：所有交互走本模块默认实现，行为与历史版本逐字一致。
# 已设置（TUI）：交互委托给适配器（Textual ModalScreen 等），后端引擎无感。
_UI_ADAPTER = None


def set_ui_adapter(adapter) -> None:
    """注入 UI 适配器（传 None 恢复默认 REPL 行为）。"""
    global _UI_ADAPTER
    _UI_ADAPTER = adapter


def get_ui_adapter():
    """返回当前 UI 适配器（None=REPL 默认）。"""
    return _UI_ADAPTER


# ── 实时引导桥（AI 运行中输入排队 → 每轮边界注入）──
# TUI 在 on_mount 注册 provider（非阻塞排空输入队列），引擎在每一轮 API 调用前
# 调用 drain_pending_input()，把用户排队输入作为「引导」注入下一轮。REPL 不注册
# provider → 行为与历史版本完全一致。
_PENDING_INPUT_PROVIDER = None


def set_pending_input_provider(provider) -> None:
    """注册排队输入提供者（传 None 注销）。provider() 应非阻塞返回 list[str]。"""
    global _PENDING_INPUT_PROVIDER
    _PENDING_INPUT_PROVIDER = provider


def drain_pending_input() -> List[str]:
    """非阻塞取走当前排队的用户输入（无 provider / 异常一律返回空列表）。"""
    provider = _PENDING_INPUT_PROVIDER
    if provider is None:
        return []
    try:
        items = provider() or []
    except Exception:
        return []
    out = []
    for x in items:
        if x is None:
            continue
        s = str(x)
        if s.strip():
            out.append(s)
    return out


def _ui_lang() -> str:
    """获取当前语言（延迟导入避免与 config 循环依赖）。"""
    try:
        from .config import get_current_lang
        return get_current_lang()
    except Exception:
        return "chinese"


# ============================================================
# InquirerPy 优雅降级
# ============================================================
_INQUIRERPY_AVAILABLE = False
_inquirer = None
_INQUIRERPY_TRIED = False


def _ensure_inquirer():
    """延迟加载 InquirerPy（避免模块级导入拉入 prompt_toolkit ~1s）。行为与原来完全一致。"""
    global _inquirer, _INQUIRERPY_AVAILABLE, _INQUIRERPY_TRIED
    if not _INQUIRERPY_TRIED:
        _INQUIRERPY_TRIED = True
        try:
            from InquirerPy import inquirer as _inquirer
            _INQUIRERPY_AVAILABLE = True
        except ImportError:
            _INQUIRERPY_AVAILABLE = False
    return _inquirer


def _has_tty() -> bool:
    """检测是否有可用的 TTY（InquirerPy 需要）。

    2026-09 修复：改用真实 fd 判断（os.isatty(0)/os.isatty(1)），
    免疫 capture_command_output() 把 sys.stdout 换成 RealTimeOutputCatcher
    （isatty() 恒 False）导致交互确认框被判定为无 TTY 而跳过/消失。
    """
    try:
        return os.isatty(0) and os.isatty(1)
    except Exception:
        try:
            return sys.stdin.isatty() and sys.stdout.isatty()
        except Exception:
            return False


@contextlib.contextmanager
def real_terminal_io():
    """临时把 sys.stdout/stderr 恢复为真实终端（防确认框被捕获流吞掉）。

    capture_command_output() 会把 sys.stdout 换成输出收集器：此时 input() 的
    提示词与 InquirerPy 的部分输出会写进捕获缓冲区，用户看不到确认框。
    包住确认逻辑后，提示/输入一律直达真实终端。
    """
    # TUI 模式：sys.__stdout__ 归 Textual 驱动所有，抢回会毁掉画面 → no-op
    try:
        from bin.ai_lib.mode import is_tui_render
        if is_tui_render():
            yield
            return
    except Exception:
        pass
    _out, _err = sys.stdout, sys.stderr
    try:
        if sys.stdout is not sys.__stdout__:
            sys.stdout = sys.__stdout__
        if sys.stderr is not sys.__stderr__:
            sys.stderr = sys.__stderr__
        yield
    finally:
        sys.stdout, sys.stderr = _out, _err


# ============================================================
# 选择器 — 上下键选一项
# ============================================================

def select_option(
    message: str,
    options: List[str],
    default: str = "",
    lang: str = "chinese",
    blocking: bool = False,
    body: str = "",
) -> str:
    """
    箭头键选择菜单。
    
    参数:
      message: 提示语
      options: 选项列表（按顺序，第一项为默认）
      default: 默认选项（为空则取 options[0]）
      lang: 语言
      blocking: 阻塞式（人工决策，不设超时）
      body: 可选的正文（长文本）：TUI 下与选项渲染在**同一个弹窗**里并可滚动
            （计划确认用；REPL 下调用方自行 print，此参数被忽略）
    
    返回: 用户选择的选项字符串
    """
    _adapter = get_ui_adapter()
    if _adapter is not None and hasattr(_adapter, "select_option"):
        try:
            return _adapter.select_option(message, options, default, lang,
                                          blocking=blocking, body=body)
        except TypeError:
            try:
                # 旧适配器不认 body → 退回仅 blocking 的签名
                return _adapter.select_option(message, options, default, lang, blocking=blocking)
            except TypeError:
                # 再旧的适配器 → 原始签名（仅兜底）
                return _adapter.select_option(message, options, default, lang)

    if not options:
        return ""

    default = default or options[0]

    if _ensure_inquirer() is not None and _has_tty():
        try:
            choices = options  # InquirerPy select 直接接受字符串列表
            result = _inquirer.select(
                message=message,
                choices=choices,
                default=default,
                vi_mode=False,
            ).execute()
            return result
        except (KeyboardInterrupt, EOFError):
            console.print()
            # 中断 = 取消。绝不返回 default：确认类调用（如计划确认）的 default 是
            # 「确认」，Ctrl+C 被当成确认会让 AI 未经许可直接执行。
            return ""
        except Exception:
            pass  # 回退到 prompt_toolkit

    # ── 回退: prompt_toolkit 原始实现 ──
    return _fallback_select(message, options, default)


def _fallback_select(message: str, options: List[str], default: str) -> str:
    """prompt_toolkit 回退选择器"""
    from prompt_toolkit import prompt as pt_prompt
    from prompt_toolkit.key_binding import KeyBindings

    selected = [options.index(default) if default in options else 0]
    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        selected[0] = (selected[0] - 1) % len(options)

    @kb.add("down")
    def _(event):
        selected[0] = (selected[0] + 1) % len(options)

    @kb.add("enter")
    def _(event):
        event.app.exit(result=options[selected[0]])

    def toolbar():
        lines = []
        for i, opt in enumerate(options):
            prefix = "→" if i == selected[0] else " "
            lines.append(f"  {prefix} {opt}")
        return "\n".join(lines)

    console.print(message, style="bold yellow")
    try:
        choice = pt_prompt(
            "",
            key_bindings=kb,
            bottom_toolbar=toolbar,
        )
    except (KeyboardInterrupt, EOFError):
        console.print()
        return ""

    return choice if choice in options else ""


# ============================================================
# 确认器 — Y/n
# ============================================================

def confirm_dangerous(
    title: str,
    command: str,
    reason: str,
    lang: str = "chinese",
    timeout: Optional[float] = None,
    timeout_default: bool = False,
) -> Tuple[bool, str, str]:
    """
    危险命令确认对话框。

    参数:
      timeout: 等待用户输入的秒数。None=一直等待；>0 超时后按 timeout_default 返回。
      timeout_default: 超时时的默认确认结果（True=放行 / False=拒绝）。

    返回: (confirmed: bool, user_response: str, refuse_reason: str)
    """
    # ── 适配器优先（TUI → ModalScreen）；无适配器时保持原 REPL 行为 ──
    _adapter = get_ui_adapter()
    if _adapter is not None and hasattr(_adapter, "confirm_dangerous"):
        return _adapter.confirm_dangerous(title, command, reason, lang, timeout, timeout_default)

    # ── 2026-09：确认框全程走真实终端（防 sys.stdout 被捕获流替换导致框不可见）──
    with real_terminal_io():
        # 显示警告面板（两种路径共用）
        _cmd_label = "命令" if lang == "chinese" else "Command"
        _risk_label = "风险" if lang == "chinese" else "Risk"
        panel = Panel(
            f"[bold yellow]{_cmd_label}:[/bold yellow]\n  {command}\n\n"
            f"[bold red]{_risk_label}:[/bold red]\n  {reason}",
            title=title,
            border_style="red",
            box=HEAVY,
        )
        console.print(panel)
        try:
            sys.stdout.flush()
        except Exception:
            pass

        # 超时支持：等待输入最多 timeout 秒；无输入按 timeout_default 返回
        if timeout is not None and timeout > 0:
            if not _wait_for_input(timeout):
                if timeout_default:
                    console.print("(等待超时，已自动放行)" if lang == "chinese" else "(timed out, auto-allowed)")
                else:
                    console.print("(等待超时，已默认拒绝)" if lang == "chinese" else "(timed out, auto-denied)")
                return timeout_default, "timeout", ""

        if _ensure_inquirer() is not None and _has_tty():
            try:
                confirmed = _inquirer.confirm(
                    message="确认执行此命令？" if lang == "chinese" else "Confirm executing this command?",
                    default=False,
                ).execute()

                if confirmed:
                    return True, "y", ""
                else:
                    refuse = _inquirer.text(
                        message="拒绝原因（可选，回车跳过）:" if lang == "chinese" else "Reason to refuse (optional, Enter to skip):",
                    ).execute()
                    refuse = refuse or ("用户拒绝执行" if lang == "chinese" else "User refused")
                    return False, "n", refuse
            except (KeyboardInterrupt, EOFError):
                console.print()
                return False, "interrupt", "用户中断" if lang == "chinese" else "User interrupted"
            except Exception:
                pass  # 回退

        # ── 回退: console.print + prompt ──
        return _fallback_confirm_dangerous(title, command, reason, lang)


def _wait_for_input(seconds: float) -> bool:
    """等待用户输入最多 seconds 秒。有输入返回 True，超时返回 False。

    跨平台：POSIX 用 select，Windows 用 msvcrt 轮询。
    无法检测时保守返回 True（不自动放行，等待用户）。
    """
    if seconds <= 0:
        return True
    import time as _time
    if sys.platform == "win32":
        try:
            import msvcrt
            _deadline = _time.time() + seconds
            while _time.time() < _deadline:
                if msvcrt.kbhit():
                    return True
                _time.sleep(0.05)
            return False
        except Exception:
            return True
    try:
        import select
        _r, _, _ = select.select([sys.stdin], [], [], seconds)
        return bool(_r)
    except Exception:
        return True


def _fallback_confirm_dangerous(
    title: str, command: str, reason: str, lang: str
) -> Tuple[bool, str, str]:
    """prompt_toolkit 回退确认器（异常时再退化为纯 input，保证框一定可见可答）"""
    _cmd_label = "命令" if lang == "chinese" else "Command"
    _risk_label = "风险" if lang == "chinese" else "Risk"
    console.print(Panel(
        f"{_cmd_label}: {command}\n{_risk_label}: {reason}",
        title=title,
        border_style="red",
        box=HEAVY,
    ))
    try:
        sys.stdout.flush()
    except Exception:
        pass

    label = "Confirm? (y/N): " if lang == "english" else "确认执行？(y/N): "
    try:
        from prompt_toolkit import prompt as pt_prompt
        user_input = pt_prompt(label).lower().strip()
    except (KeyboardInterrupt, EOFError):
        console.print()
        return False, "interrupt", "User interrupted" if lang == "english" else "用户中断"
    except Exception:
        # prompt_toolkit 异常（无 TTY / 终端状态异常）→ 纯 input 兜底
        try:
            user_input = input(label).strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return False, "interrupt", "User interrupted" if lang == "english" else "用户中断"

    if user_input == "y":
        return True, "y", ""

    # 收集拒绝原因
    reason_label = "Reason to refuse (optional): " if lang == "english" else "拒绝原因（可选）: "
    try:
        from prompt_toolkit import prompt as pt_prompt
        refuse = pt_prompt(reason_label).strip()
    except (KeyboardInterrupt, EOFError):
        refuse = ""
    except Exception:
        try:
            refuse = input(reason_label).strip()
        except (KeyboardInterrupt, EOFError):
            refuse = ""
    return False, "n", refuse or ("User refused" if lang == "english" else "用户拒绝执行")


# ============================================================
# 文本输入
# ============================================================

def text_input(
    message: str,
    default: str = "",
    lang: str = "chinese",
) -> str:
    """
    单行文本输入。
    """
    _adapter = get_ui_adapter()
    if _adapter is not None and hasattr(_adapter, "text_input"):
        return _adapter.text_input(message, default, lang)

    if _ensure_inquirer() is not None and _has_tty():
        try:
            result = _inquirer.text(
                message=message,
                default=default,
            ).execute()
            # 与回退分支保持一致：统一 strip，避免首尾空白/换行混入
            return (result or default).strip() or default
        except (KeyboardInterrupt, EOFError):
            console.print()
            return default
        except Exception:
            pass

    # ── 回退 ──
    from prompt_toolkit import prompt
    try:
        return prompt(f"{message} ", default=default).strip() or default
    except (KeyboardInterrupt, EOFError):
        console.print()
        return default
    except Exception:
        # 无 TTY（管道/非交互）时 prompt_toolkit 不可用 → 退回内置 input()，
        # 与改造前「裸 input()」的行为保持一致，避免把非交互调用方弄坏。
        try:
            return input(f"{message} ").strip() or default
        except (KeyboardInterrupt, EOFError):
            return default


def capture_key(message: str = "", lang: str = "chinese") -> Optional[str]:
    """捕获一次按键组合，返回 Textual 风格键名（如 ctrl+r / alt+enter / f2）。

    - TUI：走适配器的 capture_key（弹出「按键捕获」框，按哪个键就是哪个键，最直观）；
    - REPL：prompt_toolkit 抓不到 alt 等组合的可靠编码 → 改为文本输入，用户直接键入
      组合名（如 alt+enter / ctrl+r），再由 keymap.normalize_combo 校验。
    取消 / 非法 → None。
    """
    _adapter = get_ui_adapter()
    if _adapter is not None and hasattr(_adapter, "capture_key"):
        try:
            return _adapter.capture_key(message, lang) or None
        except Exception:
            return None
    try:
        from bin.ai_lib import keymap as _km
        raw = text_input(message, "", lang=lang)
        return _km.normalize_combo(raw)
    except Exception:
        return None


def secret_input(
    message: str,
    default: str = "",
    lang: str = "chinese",
) -> str:
    """
    掩码单行输入（用于 API Key / 密码等敏感值）。

    - InquirerPy 可用且有 TTY → secret 掩码输入
    - 否则回退 getpass.getpass（同样掩码）
    两分支统一 strip；返回 default 表示取消/留空。
    """
    _adapter = get_ui_adapter()
    if _adapter is not None and hasattr(_adapter, "secret_input"):
        return _adapter.secret_input(message, default, lang)

    if _ensure_inquirer() is not None and _has_tty():
        try:
            result = _inquirer.secret(
                message=message,
                default=default,
            ).execute()
            return (result or default).strip() or default
        except (KeyboardInterrupt, EOFError):
            console.print()
            return default
        except Exception:
            pass

    # ── 回退：getpass 掩码输入 ──
    import getpass
    try:
        return getpass.getpass(f"{message} ").strip() or default
    except (KeyboardInterrupt, EOFError):
        console.print()
        return default


# ============================================================
# 通用确认 / 验证码（安全层与 plan 确认的统一入口）
# ============================================================

def confirm(
    message: str,
    default: bool = False,
    lang: Optional[str] = None,
) -> bool:
    """通用 Y/N 确认。适配器存在时委托（TUI → 模态框），否则走 REPL 默认实现。"""
    lang = lang or _ui_lang()
    _adapter = get_ui_adapter()
    if _adapter is not None and hasattr(_adapter, "confirm"):
        return bool(_adapter.confirm(message, default, lang))

    _hint = "Y/n" if default else "y/N"
    if _ensure_inquirer() is not None and _has_tty():
        try:
            return bool(_inquirer.confirm(message=message, default=default).execute())
        except (KeyboardInterrupt, EOFError):
            console.print()
            return default
        except Exception:
            pass
    _label = f"{message} [{_hint}]: "
    try:
        from prompt_toolkit import prompt as _pt
        _ans = _pt(_label).strip().lower()
    except (KeyboardInterrupt, EOFError):
        console.print()
        return default
    except Exception:
        try:
            _ans = input(_label).strip().lower()
        except (KeyboardInterrupt, EOFError):
            return default
    if not _ans:
        return default
    return _ans in ("y", "yes", "是", "确认")


def captcha(
    title: str,
    warning: str,
    code: str,
    lang: Optional[str] = None,
) -> bool:
    """危险命令验证码确认（adv 模式）。返回用户输入是否匹配 code。

    适配器存在时委托（TUI → 模态输入框），否则 REPL：打印警告 + 验证码，读一行。
    """
    lang = lang or _ui_lang()
    _adapter = get_ui_adapter()
    if _adapter is not None and hasattr(_adapter, "captcha"):
        return bool(_adapter.captcha(title, warning, code, lang))

    console.print(warning)
    _label = "验证码" if lang == "chinese" else "Captcha"
    _tip = "请输入上方验证码以确认执行" if lang == "chinese" else "enter the captcha above to confirm"
    console.print(f"{_label}: [ {code} ]  — {_tip}")
    try:
        from prompt_toolkit import prompt as _pt
        _resp = _pt("> ").strip()
    except (KeyboardInterrupt, EOFError):
        console.print()
        return False
    except Exception:
        try:
            _resp = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return False
    return _resp.upper() == (code or "").upper()


# ============================================================
# Onyx 视觉系统（单一事实源：TUI 主题 + 消息语言共用）
# ============================================================
# 设计原则：
#   1. 语义色 —— 每个颜色只表达一种含义（AI / 用户 / 工具 / 成功 / 失败 / 警告 / 次要）。
#   2. 角色沟槽 —— 每个内容块以「一个等宽字形 + 语义色」开头，正文统一缩进 2 格，
#      让眼睛沿单一竖轴快速扫描「谁在说 / 在做什么」。
#   3. 安静外壳 —— 少框线、多留白；分隔靠颜色与间距，不靠盒子套盒子。
# 调色板为深色宝石调（低眩光）；hex 同时供 Textual 主题与 Rich 文本使用。
ONYX_PALETTE = {
    "background": "#0B0F14",
    "surface": "#11161D",
    "panel": "#161C24",
    "primary": "#8B7CF6",
    "secondary": "#5A6478",
    "accent": "#8B7CF6",
    "foreground": "#CBD5E1",
    "success": "#5FD39A",
    "warning": "#E3B341",
    "error": "#F07178",
}
# Textual 主题自定义变量（CSS 里用 $onyx-muted 等引用）
ONYX_VARS = {
    "onyx-muted": "#6B7688",
    "onyx-dim": "#4A5361",
    "onyx-rail": "#2A3240",
    "onyx-rule": "#1E2634",
    "onyx-user": "#7FB2FF",
    "onyx-tool": "#63C4E0",
    "onyx-ai-bg": "#121A28",
}
# 角色 → (字形, 颜色)。字形一律单宽，避免 emoji 双宽破坏对齐。
ONYX_ROLE_GLYPH = {
    "ai": ("◆", ONYX_PALETTE["accent"]),
    "plan": ("≡", ONYX_PALETTE["accent"]),
    "reason": ("◇", ONYX_VARS["onyx-muted"]),
    "warn": ("▲", ONYX_PALETTE["warning"]),
    "ok": ("✓", ONYX_PALETTE["success"]),
    "err": ("✗", ONYX_PALETTE["error"]),
    "tool": ("⚙", ONYX_VARS["onyx-tool"]),
    "user": ("❯", ONYX_VARS["onyx-user"]),
    "meta": ("▣", ONYX_VARS["onyx-muted"]),
}
# 标题前导 emoji → 角色（兼容 ai_cmd 等既有调用点，REPL 输出不受影响）
ONYX_EMOJI_ROLE = {
    "🤖": "ai", "💬": "ai", "💕": "ai",
    "📋": "plan", "🧠": "reason",
    "⚠️": "warn", "⚠": "warn", "⏹": "warn",
    "✅": "ok", "❌": "err",
    "🔧": "tool", "🔌": "tool",
    "📦": "meta", "📁": "meta", "📂": "meta", "🔎": "meta",
}
# border_style → 角色（无 emoji 时兜底）
_ONYX_BORDER_ROLE = {
    "red": "err", "yellow": "warn", "green": "ok",
    "cyan": "ai", "blue": "reason", "magenta": "plan",
}


def onyx_role(title: str, border_style: str = "") -> Tuple[str, str]:
    """把「emoji/风格标题」归一为 (角色, 纯文本标题)。"""
    t = (title or "").strip()
    for emo, role in ONYX_EMOJI_ROLE.items():
        if t.startswith(emo):
            return role, t[len(emo):].strip()
    return _ONYX_BORDER_ROLE.get(str(border_style or "").strip(), "meta"), t


def onyx_label(role: str, text: str) -> Text:
    """角色标签行：`◆ 文本`（字形用语义色加粗，文字用前景色加粗）。"""
    glyph, color = ONYX_ROLE_GLYPH.get(role, ONYX_ROLE_GLYPH["meta"])
    out = Text()
    out.append(glyph + " ", style="bold " + color)
    out.append(str(text), style="bold " + ONYX_PALETTE["foreground"])
    return out


# ============================================================
# Rich 渲染组件
# ============================================================

def _tui() -> bool:
    """当前是否 TUI 渲染模式（任何异常都当作 REPL）。"""
    try:
        from bin.ai_lib.mode import is_tui_render
        return is_tui_render()
    except Exception:
        return False


def tui_block(title: str, body, border_style: str = ""):
    """TUI 内容块：角色标签行 + 缩进 2 格的正文（无框线）。

    label 落在第 0 列（字形 1 宽 + 1 空格），正文缩进 2 → 正文与标签文字左对齐。
    """
    from rich.console import Group
    from rich.padding import Padding
    role, label = onyx_role(title, border_style)
    if not label:
        return Padding(body, (0, 0, 0, 2))
    return Group(onyx_label(role, label), Padding(body, (0, 0, 0, 2)))


def tui_plain(renderable, title: str = "", **panel_kw):
    """按渲染模式产出「带面板」或「带角色标签的纯文本」。

    - REPL：`Panel(renderable, title=title, **panel_kw)`（原样，行为不变）
    - TUI：**不套面板**，把 `title` 渲染成「角色字形 + 语义色」的标签行，正文缩进 2
      —— 既保住「🤖 AI / 🧠 分析 / ⚠️ 警告 / ✅ 工具名」的信息，又统一成一套视觉语言。

    TUI 不套面板的原因：① 日志区比整屏窄（宽屏还要减侧栏），面板按整屏宽画会落进
    日志区被换行截断、框线错位；② 日志区已有一条左侧发丝线，再套框会显得杂乱。
    """
    if _tui():
        if not title:
            return renderable
        return tui_block(title, renderable, panel_kw.get("border_style") or "")
    from rich.panel import Panel as _Panel
    return _Panel(renderable, title=title, **panel_kw)


def render_plan_panel(plan_text: str) -> Panel:
    """渲染计划内容 Panel"""
    _l = _ui_lang()
    md = _markdown(plan_text.strip()) if plan_text.strip() else Text("(空计划)" if _l == "chinese" else "(empty plan)")
    return tui_plain(
        md,
        title="📋 AI 计划" if _l == "chinese" else "📋 AI Plan",
        border_style="cyan",
        box=ROUNDED,
        padding=(1, 2),
    )


def render_analysis_panel(analysis_text: str) -> Panel:
    """渲染策略分析 Panel"""
    _l = _ui_lang()
    return tui_plain(
        analysis_text.strip(),
        title="🧠 AI 决策分析" if _l == "chinese" else "🧠 AI Decision Analysis",
        border_style="blue",
        box=ROUNDED,
        padding=(1, 2),
    )


def render_warning_panel(title: str, body: str) -> Panel:
    """渲染警告 Panel（红色）"""
    return tui_plain(
        body.strip(),
        title=title,
        border_style="red",
        box=HEAVY,
        padding=(1, 2),
    )


def render_ai_panel(text: str, title: str = None) -> Panel:
    """渲染 AI 回答。

    - REPL：带边框 + 标题的 Panel（原样）
    - TUI：**不套边框**，用「◆ 角色标签 + 缩进正文」的视觉语言，正文整块染上极淡的
      蓝紫底色（`onyx-ai-bg`）—— 即便 Markdown 一句都解析不出，也有一整块底色，
      一眼就能区分「哪些是 AI 说的」。

    ⚠️ 圆点必须**独立成行**（用 Group 组合）。旧实现把 "● " 直接拼进 Markdown 源文本，
    首行会被当成普通段落 —— `## 标题` 会原样显示成 "● ## 标题"，整个 Markdown 解析被破坏
    （标题/列表/引用全部失效，看起来就是一堆纯文本）。
    """
    if title is None:
        title = "AI 回复" if _ui_lang() == "chinese" else "AI Reply"
    content = text.strip()
    from rich.console import Group as _Group
    body = _markdown(content) if content else Text("(无内容)" if _ui_lang() == "chinese" else "(empty)")
    if _tui():
        from rich.panel import Panel as _Panel
        from rich import box as _box
        role, label = onyx_role(title, "cyan")
        # box=MINIMAL 的「边框」全是空格 → 无可见框线，只留整块底色（真正的底色块）
        block = _Panel(body, box=_box.MINIMAL,
                       style="on " + ONYX_VARS["onyx-ai-bg"], padding=(0, 1))
        # 不再额外缩进：底色块自带 1 格边框 + 1 格 padding → 正文正好落在
        # 「◆ 」标签文字的左对齐列（col 2），整块底色与标签左缘齐平。
        return _Group(onyx_label(role, label or "AI"), block)
    return Panel(body, title=title, border_style="dim", box=ROUNDED, padding=(0, 1))


def render_tool_table(tool_results: List[Dict[str, str]]) -> Table:
    """渲染工具执行结果表格"""
    _l = _ui_lang()
    table = Table(
        title="🔧 工具执行" if _l == "chinese" else "🔧 Tool Execution",
        box=ROUNDED,
        border_style="dim",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("工具" if _l == "chinese" else "Tool", style="bold")
    table.add_column("参数" if _l == "chinese" else "Params", style="dim", overflow="fold")
    table.add_column("状态" if _l == "chinese" else "Status")
    table.add_column("输出" if _l == "chinese" else "Output")

    for i, tc in enumerate(tool_results, 1):
        status = tc.get("status", "")
        status_icon = "✅" if "ok" in status else "❌"
        status_style = "green" if "ok" in status else "red"
        table.add_row(
            str(i),
            tc.get("name", "?"),
            tc.get("params", ""),
            f"[{status_style}]{status_icon}[/{status_style}]",
            tc.get("output", "")[:80],
        )
    return table


def render_separator(text: str = "") -> Rule:
    """渲染分隔线（TUI 用发丝色，REPL 保持 dim）。"""
    return Rule(text, style=(ONYX_VARS["onyx-rail"] if _tui() else "dim"))


def render_spinner(text: str = ""):
    """返回 Rich spinner 状态文本"""
    from rich.spinner import Spinner
    if not text:
        text = "思考中..." if _ui_lang() == "chinese" else "Thinking..."
    return Spinner("dots", text=text, style="bold cyan")


# ============================================================
# 流式展示 builder
# ============================================================

class StreamingDisplay:
    """
    流式 AI 回答展示管理器。
    
    用法:
      display = StreamingDisplay()
      with Live(display.panel, ...) as live:
          display.attach(live)
          for chunk in stream:
              display.feed(chunk)
          display.finalize(parsed_txt)
    """

    def __init__(self, lang: str = "chinese"):
        self.lang = lang
        self._live = None
        self._streamed = ""
        self._spinning = True

    @property
    def panel(self):
        """初始 Panel（思考中...）"""
        from rich.spinner import Spinner
        _think = "思考中..." if self.lang == "chinese" else "Thinking..."
        spinner = Spinner("dots", text=f" {_think}", style="bold cyan")
        return tui_plain(spinner, title="🤖 AI", border_style="green", box=ROUNDED)

    def attach(self, live):
        """绑定 Rich Live 对象"""
        self._live = live

    def feed(self, text: str):
        """追加流式文本并刷新"""
        if not self._streamed and text.strip():
            text = "● " + text
        self._streamed += text
        self._spinning = False
        if self._live:
            self._live.update(tui_plain(
                self._streamed,
                title="🤖 AI",
                border_style="green",
                box=ROUNDED,
            ))

    def finalize(self, parsed_text: str):
        """
        用解析后的干净文本替换流式展示。
        流式 buffer 可能因 token 切分而包含格式标记，
        这里用结构化解析后的 txt 覆盖。
        """
        final = parsed_text.strip() if parsed_text else self._streamed
        if final:
            self._spinning = False
            if self._live:
                if not final.startswith("● "):
                    final = "● " + final
                self._live.update(tui_plain(
                    _markdown(final),
                    title="🤖 AI",
                    border_style="green",
                    box=ROUNDED,
                ))
        elif self._spinning and self._live:
            pass  # 无内容时不显示空面板
