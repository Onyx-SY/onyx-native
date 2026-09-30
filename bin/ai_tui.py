# -*- coding: utf-8 -*-
"""AI TUI 模式（Textual 实现）。

与 REPL 模式（bin/ai_interactive.py）的解耦边界：
  - 共享 headless 后端：引擎 bin.ai_cmd.handle_ai，以及 ai_interactive 里不含 UI 状态的
    会话辅助（_call_ai_engine / _dispatch_slash / _auto_memory_mode）。
  - 本模块独占：输入 / 渲染 / 交互（Textual 控件 + 模态框）。
  - 所有交互统一经 bin/ai_lib/ui.py 的适配器接缝（set_ui_adapter），后端无感。

依赖 textual（由 bin/ai_lib/tui_deps.py 自举；缺失时回退 REPL）。
"""
import io
import os
import queue
import sys
import threading
import time
import uuid
import weakref

from bin.ai_lib import mode as _mode


# ──────────────────────────── 双语 / 历史 辅助 ────────────────────────────

def _t(key: str, lang: str = "chinese", **fmt) -> str:
    """双语取词（委托 bin.ai_lib.i18n）。

    取词失败时回退为键名本身，保证 TUI 不会因为文案问题整体崩溃。
    """
    try:
        from bin.ai_lib.i18n import I18n
        return I18n.get_instance().t(key, lang, **fmt)
    except Exception:
        return key


def _slash_commands(lang: str = "chinese"):
    """当前语言的 AI 斜杠命令表（复用 REPL 的命令表，保证两边一致）。"""
    try:
        from bin.ai_interactive import _SLASH_COMMANDS_CN, _SLASH_COMMANDS_EN
        cmds = _SLASH_COMMANDS_EN if lang == "english" else _SLASH_COMMANDS_CN
        return list(cmds.keys())
    except Exception:
        return []


# 模态框等待上限（秒）：回调丢失时不能永久阻塞 worker 线程（否则 _busy 永远卡 True）
_MODAL_TIMEOUT = 120.0
# Esc 取消的哨兵值：让「明确取消」与「交互不可用（空串）」可区分
_CANCEL = "__cancel__"

# 「转义序列泄漏」窗口（秒）：终端把未识别的鼠标/控制序列当普通字节送来时，会先到一个
# Escape，随后紧跟一串可打印字节（如 '[' '<' '3' '5' ';' 'M'）。窗口内到达的可打印
# 字节一律丢弃，避免滑动/滚轮时把一堆字节灌进输入框。
# ⚠️ 只过滤「可打印字符」：Enter(\r) / 退格(\x7f) / Tab(\t) / Ctrl+X 等控制键必须放行，
#    否则输入框会整体失效（历史 bug：Enter 被当成控制字节吞掉 → 完全无法发送）。
# 窗口取 30ms：泄漏字节与 Esc 在**同一次 read** 里到达（微秒级相邻），30ms 足够覆盖；
# 窗口越长，AI 执行期间（终端持续吐输出、触摸/滚轮报文频繁）误吞真实按键的概率越大
# （用户反馈「AI 执行时输入有时不灵」）。
_ESC_LEAK_WINDOW = 0.03

# Markdown 主题：默认主题的标题只有「粗体+下划线」，观感上和白字几乎一样
# （用户反馈「整屏都是一堆白字」）。这里给标题/代码/列表/引用都上真颜色。
_ONYX_MD_THEME = None


def _md_theme():
    global _ONYX_MD_THEME
    if _ONYX_MD_THEME is None:
        from rich.theme import Theme
        _ONYX_MD_THEME = Theme({
            "markdown.h1": "bold bright_cyan",
            "markdown.h2": "bold cyan",
            "markdown.h3": "bold blue",
            "markdown.h4": "bold magenta",
            "markdown.code": "bright_yellow",
            "markdown.item.bullet": "bright_cyan",
            "markdown.link": "bright_blue underline",
            "markdown.block_quote": "italic grey62",
            "markdown.hr": "grey42",
        })
    return _ONYX_MD_THEME


# ── Onyx 品牌主题（调色板与消息语言同源，见 bin/ai_lib/ui.py）──
_ONYX_THEME = None


def _onyx_theme():
    """注册用的 Textual 主题（深色宝石调；懒加载避免拖慢启动）。"""
    global _ONYX_THEME
    if _ONYX_THEME is None:
        from textual.theme import Theme
        from bin.ai_lib.ui import ONYX_PALETTE as _P, ONYX_VARS as _V
        _ONYX_THEME = Theme(
            name="onyx",
            dark=True,
            primary=_P["primary"],
            secondary=_P["secondary"],
            accent=_P["accent"],
            foreground=_P["foreground"],
            background=_P["background"],
            surface=_P["surface"],
            panel=_P["panel"],
            success=_P["success"],
            warning=_P["warning"],
            error=_P["error"],
            variables=dict(_V),
        )
    return _ONYX_THEME


def _fmt_compact(n) -> str:
    """紧凑数字：12345 → 12.3k（状态栏省宽度）。"""
    try:
        n = int(n)
    except Exception:
        return str(n)
    if n >= 1000000:
        return f"{n / 1000000:.1f}M"
    if n >= 10000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}"


# 进程内 Rich Console 注册表（弱引用，不阻碍回收）。
# 由下面的构造器钩子填充 —— 比每轮 gc.get_objects() 全堆扫描便宜几个数量级
# （TUI 里模块级 console 数量固定且只增不减，注册表就是它们的全集）。
_RICH_CONSOLE_REGISTRY = weakref.WeakSet()


def _install_console_color_hook(app) -> None:
    """TUI：让「之后创建」的每个 Rich Console 都带颜色并对齐日志区宽度。

    为什么必须从构造器入手：各模块的 `console = Console()` 是**模块级**对象，而引擎
    模块（ai_cmd / tool_executors / mcp_exec / …）是在 `sys.stdout` 已被换成
    `_QueueStream`（非终端）之后才惰性 import 的 —— Rich 在构造时就把 color_system
    判成 None，之后 `console.print` 一律不产生 ANSI，日志区里工具名、参数、结果、
    diff 全部退化成纯白文本，与 REPL 的彩色输出完全脱节（用户观感：糊成一片白）。

    `_sync_rich_width` 的 gc 收集只能补救「已加载」的 console，而 ai_cmd 是在
    `_call_ai_engine` **内部**才 import 的，同步时机永远追不上 → 这里直接拦构造器，
    从源头保证首轮就正确。

    排除 textual 自身创建的 Console：它们的终端/颜色由 Textual 管理，强行改写会影响
    截图与渲染。
    """
    try:
        from rich.console import Console as _C, ColorSystem as _CS
    except Exception:
        return
    if getattr(_C, "_onyx_tui_hooked", False):
        return
    _orig_init = _C.__init__

    def _hooked_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        try:
            # 跳过 Textual 自己的 Console（截图 / 渲染用，不能动）
            if str(sys._getframe(1).f_globals.get("__name__", "")).startswith("textual"):
                return
            # 注册表：给 _sync_rich_width 提供「本进程所有业务 Console」的 O(1) 全集，
            # 取代每轮 gc.get_objects() 全堆扫描（大进程里那是几十毫秒级开销）。
            _RICH_CONSOLE_REGISTRY.add(self)
            if not _mode.is_tui_render():
                return
            if kwargs.get("force_terminal") is None:
                self._force_terminal = True
            if kwargs.get("color_system") in (None, "auto") and getattr(self, "_color_system", None) is None:
                self._color_system = _CS.TRUECOLOR
            # 宽度：模块级 console 默认按「整屏」算，日志区比整屏窄（宽屏还要减侧栏），
            # 不同步会换行错乱。只读缓存值（主线程 _measure 写入），worker 线程安全。
            if kwargs.get("width") is None:
                _w = int(getattr(app, "_log_w", 0) or 0)
                if _w > 8:
                    self._width = _w
                    self._height = int(getattr(app, "_log_h", 24) or 24)
        except Exception:
            pass

    try:
        _C.__init__ = _hooked_init
        _C._onyx_tui_hooked = True
    except Exception:
        pass


def _ai_history_path(user_home_dir: str) -> str:
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

    注意：这个补丁**救不了 ESC+CR**（解析器内部就把 alt 丢了）。Alt+Enter 的实际通路是
    字节层净化器把 ESC+CR / ESC+LF 改写成 CSI-u shift+enter（见 _ALT_ENTER_CSI_U）。
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


# ──────────────────────── 输入层净化（防崩溃 / 防信号泄露）────────────────────────
# 背景：Textual 的 LinuxDriver 用**严格**增量 UTF-8 解码器读 fd 0：
#     decode(read(fileno, 1024 * 4), final=...)
# 而 Termux 触摸滑动会送 legacy X10 鼠标报文 `ESC [ M b x y`，其中 x/y 是
# `0x20 + 坐标`，坐标 > 95 时该字节 >= 0x80（例如 0xBC）——不是合法 UTF-8 →
# UnicodeDecodeError 直接把输入线程打死（界面从此再也收不到任何输入）。
# 同一批报文还会被 XTermParser 拆成 ESC + 一串可打印字节，灌进输入框（「信号泄露」）。
#
# 净化器在**字节层**做三件事（跨 read 保持状态，能处理被切开的序列）：
#   1) 处理鼠标报文：X10 一律拦下（关闭鼠标→剔除；开启鼠标→转写成 SGR，见 _x10_to_sgr），
#      SGR 在关闭鼠标时剔除、开启鼠标时放行（Textual 靠它做点击 / 滚轮）；
#   2) 丢弃非法 UTF-8 字节（否则容错解码会把它们变成 U+FFFD 灌进输入框）；
#   3) 其余字节原样透传（中文、Enter、方向键、Ctrl+X 等一律不受影响）。

_MOUSE_X10 = b"\x1b[M"
_MOUSE_SGR = b"\x1b[<"
# 括号粘贴标记：内容里的 \n 是**真实换行**（多行粘贴），必须原样保留，
# 不能参与下面的「LF→CR 归一化」，否则粘贴会被逐行当成 Enter 提交。
_PASTE_START = b"\x1b[200~"
_PASTE_END = b"\x1b[201~"
# Alt+Enter 的可靠载体：CSI-u 形式的 shift+enter。
# 为什么不用原生的 ESC+CR：Textual 的 XTermParser 解析 ESC+CR 时会**丢掉 alt 修饰符**，
# 最终只产出一个裸 `enter`（实测 feed("\x1b\r") 返回空，下一次 feed 才吐出 "enter"）
# → 在单行框里等价于「直接发送」，多行模式永远进不去。CSI-u 则被稳定解析成 shift+enter。
_ALT_ENTER_CSI_U = b"\x1b[13;2u"
_MAX_PENDING = 64          # 未闭合序列的最大缓存（超出即视为普通字节放行）
# 单次刷进日志区的最大条目数：命令一次性吐几千行时，若把积压全部在一次
# RichLog.write 里写完，主线程会被占住几百毫秒（期间按键无响应 = 「输入不灵」）。
# 分片刷（下一轮立刻接着刷）让出主线程，保证输入始终跟手。
_FLUSH_MAX_ITEMS = 200


def _utf8_len(b: int) -> int:
    """该字节作为 UTF-8 首字节时的完整长度；非法首字节返回 0。"""
    if b < 0x80:
        return 1
    if 0xC2 <= b <= 0xDF:
        return 2
    if 0xE0 <= b <= 0xEF:
        return 3
    if 0xF0 <= b <= 0xF4:
        return 4
    return 0


class _InputSanitizer:
    """字节流净化器：鼠标报文处理（X10 剔除/SGR 转写）+ 非法 UTF-8 丢弃 + 半截序列缓存 + 换行归一化。"""

    def __init__(self, strip_mouse: bool = True):
        self.strip_mouse = bool(strip_mouse)
        self._pending = bytearray()
        self._in_paste = False   # 括号粘贴（\x1b[200~ … \x1b[201~）内：内容原样透传
        self._last_cr = False    # 上一字节是否 CR（用于折叠 CRLF）
        self._last_mouse_btn = 0  # 最近按下的鼠标键（X10→SGR 转写「释放」报文时要用）

    def _x10_to_sgr(self, seq: bytes) -> bytes:
        """把 legacy X10 鼠标报文 `ESC [ M Cb Cx Cy` 就地转写成 SGR 报文。

        Textual 的解析器只认 SGR（`ESC [ < b ; x ; y M|m`），X10 会被整段丢掉；
        而 X10 的 Cb/Cx/Cy 都是 `0x20 + 值`，与 SGR 的 b/x/y 一一对应：
            b = Cb - 32,  x = Cx - 32,  y = Cy - 32
        「释放」在 X10 里用键码 3，在 SGR 里必须写成小写 `m` 并带上刚按下的键，
        所以这里记忆 self._last_mouse_btn。任何异常都返回 b""（= 当垃圾字节丢弃）。
        """
        try:
            if len(seq) < 6:
                return b""
            cb = seq[3] - 32
            cx = seq[4] - 32
            cy = seq[5] - 32
            if cb < 0 or cx <= 0 or cy <= 0:
                return b""
            if cb & 96:                     # 滚轮(64) / 拖动(32)：位语义与 SGR 相同，按 M 上报
                return b"\x1b[<%d;%d;%dM" % (cb, cx, cy)
            if (cb & 3) == 3:               # 释放：SGR 用小写 m + 原按键
                return b"\x1b[<%d;%d;%dm" % (self._last_mouse_btn, cx, cy)
            self._last_mouse_btn = cb & 3   # 按下：记住按键，供随后的释放报文使用
            return b"\x1b[<%d;%d;%dM" % (cb, cx, cy)
        except Exception:
            return b""

    def feed(self, data: bytes) -> bytes:
        if not data:
            return b""
        buf = bytes(self._pending) + bytes(data)
        self._pending = bytearray()
        out = bytearray()
        i, n = 0, len(buf)
        while i < n:
            b = buf[i]
            if b == 0x1B:
                self._last_cr = False
                _tail = buf[i:]
                # 半截标记（如 "\x1b[20"）暂存，等下一批补全（至少 2 字节才暂存，
                # 避免把用户单独按下的 Esc 也吞掉；严格短于标记长度才算半截）。
                if len(_tail) >= 2 and len(_tail) < len(_PASTE_START) and (
                        _PASTE_START.startswith(_tail) or _PASTE_END.startswith(_tail)):
                    self._pending = bytearray(_tail)
                    break
                if _tail.startswith(_PASTE_START):
                    self._in_paste = True
                    out += _PASTE_START
                    i += len(_PASTE_START)
                    continue
                if _tail.startswith(_PASTE_END):
                    self._in_paste = False
                    out += _PASTE_END
                    i += len(_PASTE_END)
                    continue
                # Alt+Enter：终端发的是 ESC+CR（部分终端因 ICRNL 变成 ESC+LF）。
                # 统一重写为 CSI-u shift+enter —— 否则 Textual 只会给出裸 enter（= 直接发送），
                # 「Alt+Enter 进多行」这个手势在真机上永远不生效。
                if i + 1 < n and buf[i + 1] in (0x0D, 0x0A):
                    out += _ALT_ENTER_CSI_U
                    i += 2
                    continue
                # X10 鼠标：ESC [ M + 3 字节（坐标字节可 >= 0x80，会打死严格 UTF-8 解码器）。
                # 一律不让它原样进 Textual：关闭鼠标时剔除，开启鼠标时转写成 SGR
                # （Textual 只认 SGR；转写后全是 ASCII，既点得中按钮也不会崩解码器）。
                if buf.startswith(_MOUSE_X10, i):
                    if i + 6 <= n:
                        if not self.strip_mouse:
                            out += self._x10_to_sgr(buf[i:i + 6])
                        i += 6
                        continue
                    self._pending = bytearray(buf[i:])
                    break
                if self.strip_mouse:
                    # SGR 鼠标：ESC [ < … 直到 M / m 收尾
                    if buf.startswith(_MOUSE_SGR, i):
                        j = i + 3
                        while j < n and buf[j] not in (0x4D, 0x6D):
                            j += 1
                        if j < n:
                            i = j + 1
                            continue
                        if n - i <= _MAX_PENDING:
                            self._pending = bytearray(buf[i:])
                            break
                out += buf[i:i + 1]
                i += 1
                continue
            # ── 换行归一化（括号粘贴内容除外）──
            # 不同终端把 Enter 编码成 CR(\r) / LF(\n) / CRLF(\r\n)：
            #   CR   → Textual "enter"（发送 / 换行，正确）
            #   LF   → Textual "ctrl+j"（单行框无绑定 → Enter 完全失灵；
            #                          多行框无绑定 → 无法插入换行）
            #   CRLF → enter + ctrl+j（多行框会多插一个换行）
            # 统一归一化成 CR，Enter 才能在各终端一致可用。
            if not self._in_paste:
                if b == 0x0A:
                    if self._last_cr:
                        i += 1              # 折叠 CRLF 里的 LF
                        continue
                    out.append(0x0D)
                    i += 1
                    continue
                self._last_cr = (b == 0x0D)
            need = _utf8_len(b)
            if need == 0:
                i += 1                      # 非法首字节 → 丢弃
                continue
            if need == 1:
                out.append(b)
                i += 1
                continue
            if i + need > n:                # 半截多字节字符 → 留到下一批
                self._pending = bytearray(buf[i:])
                break
            chunk = buf[i:i + need]
            try:
                chunk.decode("utf-8")
            except UnicodeDecodeError:
                i += 1                      # 非法组合 → 丢弃首字节，继续扫描
                continue
            out += chunk
            i += need
        return bytes(out)


class _SanitizingDecoder:
    """伪装成 codecs 增量解码器（LinuxDriver 只用到 .decode 这一个方法）。"""

    def __init__(self, strip_mouse: bool = True):
        from codecs import getincrementaldecoder as _gic
        self._dec = _gic("utf-8")(errors="replace")
        self._san = _InputSanitizer(strip_mouse)

    def decode(self, data: bytes, final: bool = False) -> str:
        if data and _input_debug_enabled():
            _debug_dump_input(data)
        try:
            clean = self._san.feed(data)
        except Exception:
            clean = data
        try:
            return self._dec.decode(clean, final)
        except Exception:
            # 终极兜底：绝不让解码异常打死输入线程
            return clean.decode("utf-8", errors="replace")


def _install_input_sanitizer(strip_mouse: bool = True) -> bool:
    """把净化器挂到 Textual LinuxDriver 的增量解码器上。

    必须在 `app.run()` 之前调用；LinuxDriver 内部是
    `decode = getincrementaldecoder("utf-8")().decode`，替换模块级名字即可生效。
    """
    try:
        import textual.drivers.linux_driver as _ld
    except Exception:
        return False
    if getattr(_ld, "_onyx_sanitized", False):
        return False

    class _Factory:
        __slots__ = ("_strip",)

        def __init__(self, strip):
            self._strip = strip

        def __call__(self, *_a, **_k):
            return _SanitizingDecoder(self._strip)

    def _fake(codec_name, errors=None):   # 签名对齐 codecs.getincrementaldecoder
        return _Factory(strip_mouse)

    try:
        _ld.getincrementaldecoder = _fake
        _ld._onyx_sanitized = True
        return True
    except Exception:
        return False


def _arrow_mode() -> str:
    """箭头键语义：auto（默认，按手势判据区分）| history（永远翻历史）| scroll（永远滚日志）。

    为什么给开关：不同终端把触摸滑动编码成箭头流的**节奏差异极大**，无法用一套阈值
    覆盖所有设备。默认 auto 足够好；若在某个终端上仍误判，用户可以一键切换语义，
    不必改代码。
    """
    try:
        v = (os.environ.get("ONYX_TUI_ARROW_MODE") or "auto").strip().lower()
    except Exception:
        return "auto"
    return v if v in ("auto", "history", "scroll") else "auto"


def _arrow_cfg():
    """箭头手势参数 (N, 窗口秒, 单间隔上限, 空闲结束秒, 延迟应用秒)，可用 env 微调。"""
    def _f(name, default, lo, hi):
        try:
            return max(lo, min(hi, float(os.environ.get(name) or default)))
        except Exception:
            return default
    n = int(_f("ONYX_TUI_ARROW_N", 4, 2, 12))
    return (n,
            _f("ONYX_TUI_ARROW_WINDOW", 1.2, 0.2, 5.0),
            _f("ONYX_TUI_ARROW_GAP", 0.35, 0.02, 2.0),
            _f("ONYX_TUI_ARROW_IDLE", 0.6, 0.1, 5.0),
            # 延迟应用：箭头流「静默」这么久之后才真正改写输入框。
            # 滑动流里箭头一个接一个（间隔 < DEFER）→ 每次都被重置 → 永不改写
            # → 彻底消除「滑动时历史在输入框频闪」（旧实现是「先翻再回滚」，必然闪）。
            _f("ONYX_TUI_ARROW_DEFER", 0.08, 0.02, 1.0))


def _input_debug_enabled() -> bool:
    """ONYX_TUI_INPUT_DEBUG=1 → 把原始输入字节落盘（默认关闭）。"""
    try:
        return (os.environ.get("ONYX_TUI_INPUT_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return False


def _debug_dump_input(raw: bytes) -> None:
    """把一批原始输入字节写进 ~/.config/onyx/tui_input_debug.log（hex + 时间戳）。

    用途：滑动手势判据目前是基于「终端行为推断」的阈值；有了真实字节流就能按事实定标
    （例如确认滑动到底发的是 ESC[A 流、还是 SGR 滚轮、还是别的编码）。
    有界：文件超过 512KB 自动截断重写，避免写爆用户磁盘。
    """
    try:
        path = os.path.join(os.path.expanduser("~"), ".config", "onyx", "tui_input_debug.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) > 512 * 1024:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("# truncated\n")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.6f} {raw.hex()}\n")
    except Exception:
        pass


def _tui_mouse_enabled() -> bool:
    """是否启用鼠标追踪：**默认开启**（TUI 里的按钮 / 选项列表要靠点击才可用）。

    安全性：legacy X10 鼠标报文（`ESC [ M` + 3 字节，坐标字节可 >= 0x80）会被
    _InputSanitizer 拦下 —— 关闭鼠标时直接剔除，开启鼠标时就地转写成 SGR（纯 ASCII），
    因此**不会**再触发严格 UTF-8 解码崩溃，也不会把字节漏进输入框。
    SGR 报文（`ESC [ < … M/m`）在开启鼠标时放行 → Textual 能解析成点击 / 滚轮事件。

    想退回「纯键盘 + 触摸滑动」语义时设 ONYX_TUI_MOUSE=0/off/false/no（此时鼠标报文一律剔除）。
    """
    try:
        v = (os.environ.get("ONYX_TUI_MOUSE") or "").strip().lower()
    except Exception:
        return True
    return v not in ("0", "false", "no", "off")




class _QueueStream(io.TextIOBase):
    """非终端文本流：rich 输出为纯文本（无 ANSI），按行投递到队列。"""

    def __init__(self, q: "queue.Queue", stop_event=None):
        self._q = q
        self._buf = ""
        self._stop = stop_event
        self._lock = threading.Lock()   # 多线程并发写（主/子代理/工具线程）时保护缓冲

    def _put(self, item) -> None:
        """投递一行（队列里保持**裸字符串**契约）。队列满时形成背压（等 drain 消费），
        但每 0.2s 检查退出标志，保证 App 退出后写入方不会永久阻塞。"""
        while True:
            try:
                self._q.put(item, timeout=0.2)
                return
            except queue.Full:
                if self._stop is not None and self._stop.is_set():
                    return

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return "utf-8"

    def write(self, s) -> int:
        if not isinstance(s, str):
            s = str(s)
        with self._lock:
            if "\n" not in s:
                self._buf += s
                return len(s)
            # 一次性切分（旧实现 while + split(1) 在大输出下是 O(n²)）
            parts = (self._buf + s).split("\n")
            self._buf = parts.pop()      # 末尾是未闭合的半行
        for line in parts:
            self._put(line)
        return len(s)

    def flush(self) -> None:
        buf = ""
        with self._lock:
            if self._buf:
                buf, self._buf = self._buf, ""
        if buf:
            self._put(buf)


_TUI_APP_CLASS = None
# 计划确认弹窗类（在 _build_tui 内定义，导出供无头测试直接推屏）
_TUI_PLAN_SCREEN = None
# 通用选项弹窗类（同上：SelectScreen / HistorySearchScreen 也导出，便于无头测试）
_TUI_SELECT_SCREEN = None
_TUI_HIST_SEARCH_SCREEN = None
# 确认框 / 验证码框（同上：导出便于无头测试直接推屏）
_TUI_CONFIRM_SCREEN = None
_TUI_CAPTCHA_SCREEN = None


def _build_tui():
    """延迟导入 Textual 并构造（模态框 + 适配器 + App 类）。返回 App 类。

    结果缓存：重复调用（多次进入 TUI）不再重建整套类 / 绑定。
    """
    global _TUI_APP_CLASS
    if _TUI_APP_CLASS is not None:
        return _TUI_APP_CLASS
    from textual.app import App
    from textual import events
    from textual.screen import ModalScreen
    from textual.widgets import (Header, Input, Static, RichLog, Button, OptionList,
                                 DirectoryTree, TextArea)
    from textual.widgets.option_list import Option
    from textual.containers import Horizontal, Vertical
    from textual.binding import Binding
    from textual.suggester import Suggester
    from textual.strip import Strip
    from textual.geometry import Offset
    from textual.message import Message
    from rich.segment import Segment
    from rich.style import Style
    from rich.cells import cell_len

    # ── AI 按键绑定：全部来自 bin/ai_lib/keymap 注册表（用户可在 /config 里改键）──
    from bin.ai_lib import keymap as _keymap

    def _kb(action_id: str, action: str, description: str, **kw):
        """按注册表生成 Textual Binding 列表（一个动作可绑多个键）。"""
        return [Binding(_k, action, description, **kw)
                for _k in _keymap.combos(action_id)]

    def _kbs(*specs):
        """specs: (action_id, action, description, kwargs) → 合并的 Binding 列表。"""
        out = []
        for action_id, action, description, kw in specs:
            out.extend(_kb(action_id, action, description, **kw))
        return out

    # ── 选项弹窗：上下「还有 N 项」计数 ────────────────────────────────
    # 需求：弹窗里一眼只看到几项时，用户会误以为「就这几个选项」。
    # 现在列表一次显示 _MORE_LIST_MAX 项，并在列表上下各加一行计数
    # （仅当确有隐藏项时才显示该行），随高亮/滚动实时更新。
    _MORE_LIST_MAX = 8

    def _more_counts(ol) -> tuple:
        """返回 (上方隐藏项数, 下方隐藏项数)；按「选项」计（选项可能换行占多行）。"""
        try:
            n = int(ol.option_count or 0)
        except Exception:
            return 0, 0
        if n <= 0:
            return 0, 0
        top = int(getattr(ol.scroll_offset, "y", 0) or 0)
        bottom = top + max(0, int(getattr(ol.size, "height", 0) or 0))
        try:
            i2l = dict(ol._index_to_line)
            heights = dict(ol._heights)
        except Exception:
            i2l = {i: i for i in range(n)}
            heights = {i: 1 for i in range(n)}
        up = 0
        down = 0
        for i in range(n):
            start = int(i2l.get(i, i))
            h = max(1, int(heights.get(i, 1)))
            if start + h <= top:
                up += 1          # 整项都在可视区上方
            elif start >= bottom:
                down += 1        # 整项都在可视区下方
        return up, down

    class MoreHintOptionList(OptionList):
        """OptionList + 视图变化回调：高亮 / 滚动时通知宿主刷新上下计数。"""

        def __init__(self, *args, on_view_change=None, **kwargs):
            # 必须先于 super().__init__：OptionList 构造时就会触发 watch_highlighted
            self._on_view_change = on_view_change
            super().__init__(*args, **kwargs)

        def _view_changed(self) -> None:
            cb = self._on_view_change
            if cb is not None:
                try:
                    cb()
                except Exception:
                    pass

        def watch_scroll_y(self, old_value, new_value) -> None:
            super().watch_scroll_y(old_value, new_value)
            self._view_changed()

        def watch_highlighted(self, highlighted) -> None:
            # 注意签名：OptionList.watch_highlighted 只收「新值」一个参数
            # （Textual 按签名参数个数决定传 1 个还是 2 个），多写一个会 TypeError。
            super().watch_highlighted(highlighted)
            self._view_changed()

    class MoreHintMixin:
        """选项弹窗通用：列表上下显示「↑ 上方还有 N 个选项 / ↓ 下方还有 N 个选项」。

        子类需：① 在 compose 里依次 yield `_more_up_widget()` → 列表 → `_more_down_widget()`；
        ② 用 `_more_list(*options)` 建列表（不要直接用 OptionList）；
        ③ 提供 self.lang（双语取词用）。
        """

        _MORE_LIST_ID = "modal-list"
        _MORE_UP_ID = "modal-more-up"
        _MORE_DOWN_ID = "modal-more-down"
        lang = "chinese"

        def _more_list(self, *options, **kwargs):
            return MoreHintOptionList(*options, id=self._MORE_LIST_ID,
                                      on_view_change=self._sync_more, **kwargs)

        def _more_up_widget(self):
            w = Static("", id=self._MORE_UP_ID)
            w.display = False
            return w

        def _more_down_widget(self):
            w = Static("", id=self._MORE_DOWN_ID)
            w.display = False
            return w

        def _sync_more(self) -> None:
            """刷新上下计数行（无隐藏项则隐藏该行，不占高度）。"""
            try:
                ol = self.query_one("#" + self._MORE_LIST_ID, OptionList)
                up, down = _more_counts(ol)
                wu = self.query_one("#" + self._MORE_UP_ID, Static)
                wd = self.query_one("#" + self._MORE_DOWN_ID, Static)
                wu.display = up > 0
                wd.display = down > 0
                wu.update(_t("tui_more_up", self.lang, n=up))
                wd.update(_t("tui_more_down", self.lang, n=down))
            except Exception:
                pass

        def on_option_list_option_highlighted(self, event) -> None:
            self._sync_more()

        def on_resize(self, event) -> None:
            self._sync_more()

    class KeyCaptureScreen(ModalScreen):
        """按键捕获：按下任意键即返回该组合（Textual 键名），Esc / Ctrl+C 取消。"""

        def __init__(self, title: str = "", hint: str = ""):
            super().__init__()
            self._title = title or "按下要绑定的按键…"
            self._hint = hint or "（Esc 取消）"

        def compose(self):
            with Vertical(id="modal"):
                yield Static(self._title, id="modal-msg")
                yield Static(self._hint, id="modal-hint")

        def on_mount(self) -> None:
            try:
                self.focus()
            except Exception:
                pass

        def on_key(self, event) -> None:
            key = getattr(event, "key", "") or ""
            if not key:
                return
            if key in ("escape", "ctrl+c", "ctrl+q"):
                self.dismiss(None)
                return
            if key in ("shift", "ctrl", "alt", "meta", "super"):
                return          # 单独的修饰键不算
            self.dismiss(key)

    # ────────────────────────── 模态框 ──────────────────────────
    class ConfirmScreen(ModalScreen):
        """确认框（危险命令 / 是否关闭二次确认 等）。

        ⚠️ 安全要点（实测踩过的坑）：**弹窗默认绝不能把焦点放在「是」按钮上** ——
        Textual 的 Button 自带 `enter → press`，于是弹窗瞬间到达的残留回车（或用户
        「按回车关掉弹窗」的习惯动作）会被「是」吃掉，等价于**未经确认就放行**：
        危险命令直接执行完、结果回传 AI。两层防护：
          1. `_MODAL_KEY_GRACE` 宽限窗口（0.3s）内的「是」按键/点击一律不生效 → 挡掉误触；
          2. 默认焦点落在 `default` 对应的一侧：危险确认 `default=False` → 焦点在「否」，
             回车只会「拒绝」，永远不会「确认」。
        只有显式按 `y` 或点击「是」才算确认（`escape`/`n` = 拒绝）。
        """

        BINDINGS = [Binding("y", "yes", "Yes"), Binding("n", "no", "No"),
                    Binding("escape", "no", "No")]

        _MODAL_KEY_GRACE = 0.30

        def __init__(self, message: str, default: bool = False, lang: str = "chinese"):
            super().__init__()
            self.message = message
            self.default = default
            self.lang = lang
            self._grace_t = 0.0

        def compose(self):
            with Vertical(id="modal"):
                yield Static(self.message, id="modal-msg")
                with Horizontal(id="modal-btns"):
                    yield Button(_t("tui_yes", self.lang), variant="success", id="yes")
                    yield Button(_t("tui_no", self.lang), variant="error", id="no")

        def on_mount(self):
            import time as _time
            self._grace_t = _time.monotonic()
            try:
                self.query_one("#yes" if self.default else "#no", Button).focus()
            except Exception:
                pass

        def _grace_blocked(self) -> bool:
            import time as _time
            return (_time.monotonic() - self._grace_t) < self._MODAL_KEY_GRACE

        def action_yes(self):
            if self._grace_blocked():
                return
            self.dismiss(True)

        def action_no(self):
            self.dismiss(False)

        def on_button_pressed(self, event):
            # 宽限期内**一切**按键/点击都不生效：残留回车既不能「确认」也不能「取消」，
            # 弹窗必须等用户真正做出选择（否则一条合法命令会被误发的回车静默否掉）。
            if self._grace_blocked():
                return
            self.dismiss(event.button.id == "yes")

    class CaptchaScreen(ModalScreen):
        BINDINGS = [Binding("escape", "cancel", "Cancel")]

        def __init__(self, title: str, warning: str, code: str, lang: str = "chinese"):
            super().__init__()
            self.title_text = title
            self.warning = warning
            self.code = code or ""
            self.lang = lang

        def compose(self):
            with Vertical(id="modal"):
                yield Static(self.title_text, id="modal-msg")
                yield Static(self.warning, id="modal-warn")
                yield Static(f"{_t('tui_captcha_label', self.lang)}: [ {self.code} ]", id="modal-code")
                yield Input(placeholder=_t("tui_captcha_hint", self.lang), id="modal-input")

        _MODAL_KEY_GRACE = 0.30

        def on_mount(self):
            import time as _time
            self._grace_t = _time.monotonic()
            self.query_one("#modal-input", Input).focus()

        def _grace_blocked(self) -> bool:
            import time as _time
            return (_time.monotonic() - getattr(self, "_grace_t", 0.0)) < self._MODAL_KEY_GRACE

        def on_input_submitted(self, event):
            # 宽限窗口内忽略回车：挡掉「弹窗瞬间到达的残留回车」被当成提交验证码
            if self._grace_blocked():
                return
            self.dismiss((event.value or "").strip().upper() == self.code.upper())

        def action_cancel(self):
            self.dismiss(False)

    class SelectScreen(MoreHintMixin, ModalScreen):
        BINDINGS = [Binding("escape", "cancel", "Cancel")]

        # 弹出后的「键盘防误触」宽限窗口（秒）：发送消息那次回车 / 终端残留按键
        # 可能在新模态弹出的瞬间到达，被 OptionList 当成「选中默认项」→ 未点击即返回。
        _MODAL_KEY_GRACE = 0.30

        def __init__(self, message: str, options, default: str = "", lang: str = "chinese"):
            super().__init__()
            self.message = message
            self.options = list(options)
            self.default = default
            self.lang = lang
            self._grace_t = 0.0

        def _grace_blocked(self) -> bool:
            import time as _time
            return (_time.monotonic() - self._grace_t) < self._MODAL_KEY_GRACE

        def compose(self):
            with Vertical(id="modal"):
                yield Static(self.message, id="modal-msg")
                yield self._more_up_widget()
                yield self._more_list(*self.options)
                yield self._more_down_widget()

        def on_mount(self):
            import time as _time
            self._grace_t = _time.monotonic()
            ol = self.query_one("#modal-list", OptionList)
            ol.focus()
            if self.default in self.options:
                ol.highlighted = self.options.index(self.default)
            self._sync_more()

        def on_option_list_option_selected(self, event):
            if self._grace_blocked():
                return          # 宽限窗口内忽略（不 dismiss）——防「未点击即返回」
            self.dismiss(str(event.option.prompt))

        def action_cancel(self):
            # Esc = 用户明确取消 → 送回哨兵。绝不能 dismiss(self.default)：
            # 计划确认菜单的 default 就是「确认计划」，按 Esc 会被当成确认执行。
            self.dismiss(_CANCEL)

    class PlanConfirmScreen(MoreHintMixin, ModalScreen):
        """计划确认：**计划正文与选项同处一个弹窗**，正文区可上下滚动。

        旧实现先把计划 console.print 到主屏，再压一个 SelectScreen 上去：
          - 计划正文落在 70% 不透明的遮罩下面 → 基本看不清；
          - 弹窗里没有任何滚动容器（Vertical 默认 overflow:hidden）→ 超长计划直接被裁掉；
          - 弹窗宽度/高度写死百分比，手机窄屏下更难看。
        这里把正文搬进弹窗，正文区高度按「屏幕高度 + 计划行数」自适应，超出即可滚动。
        """

        BINDINGS = [
            Binding("escape", "cancel", "Cancel", priority=True),
            Binding("pageup", "body_up", "Scroll up", show=False, priority=True),
            Binding("pagedown", "body_down", "Scroll down", show=False, priority=True),
        ]

        def __init__(self, message: str, options, default: str = "",
                     body: str = "", lang: str = "chinese"):
            super().__init__()
            self.message = message
            self.options = list(options)
            self.default = default
            self.body = body or ""
            self.lang = lang
            self._grace_t = 0.0

        def _grace_blocked(self) -> bool:
            """弹出后极短时间内忽略「键盘选中」：防止发送消息那次回车（或终端残留
            按键）在模态弹出的瞬间被 OptionList 当成选中默认项 —— 默认项就是
            「确认计划」，会导致「未点击即返回并执行」。"""
            import time as _time
            return (_time.monotonic() - self._grace_t) < SelectScreen._MODAL_KEY_GRACE

        def _body_widget(self):
            """正文优先用 Textual 的 Markdown 控件（可滚动、带高亮），失败则退 Rich Markdown。"""
            try:
                from textual.widgets import Markdown as _MD
                return _MD(self.body)
            except Exception:
                try:
                    from rich.markdown import Markdown as _RMD
                    return Static(_RMD(self.body))
                except Exception:
                    return Static(self.body)

        def compose(self):
            cn = self.lang != "english"
            with Vertical(id="modal"):
                yield Static(self.message, id="modal-msg")
                yield Static(("≡ 计划" if cn else "≡ Plan"), id="modal-title")
                from textual.containers import VerticalScroll
                with VerticalScroll(id="modal-body"):
                    yield self._body_widget()
                yield self._more_up_widget()
                yield self._more_list(*self.options)
                yield self._more_down_widget()

        def on_mount(self):
            # 正文区高度自适应：屏幕越大 / 计划越长，展示越多；手机上也不会把选项挤出屏幕。
            # 弹窗整体受 max-height:88% 限制，固定开销 = 边框2 + 内边距2 + 消息1 + 标题1
            # + 正文边框2 + 选项3 = 11 行；正文区 styles.height 含自身上下边框，故再 +2。
            import time as _time
            self._grace_t = _time.monotonic()   # 启动键盘防误触宽限窗口
            try:
                lines = self.body.count("\n") + 1
                screen_h = int(getattr(self.app.size, "height", 24) or 24)
                # 固定开销 = 模态边框2 + 内边距2 + 消息1 + 标题1 + 正文上下边框2
                #           + 选项列表 min(_MORE_LIST_MAX, 选项数)
                #           + 上下「还有 N 项」两行（仅当选项多到需要滚动时才占位）
                n_opt = len(self.options)
                # OptionList 自带 `border: tall` → 露 8 个选项需要 10 行
                list_h = min(_MORE_LIST_MAX, n_opt) + 2
                fixed = 8 + list_h + (2 if n_opt > _MORE_LIST_MAX else 0)
                avail = max(3, int(screen_h * 0.88) - fixed)
                content_h = max(3, min(lines + 1, 18, avail))
                self.query_one("#modal-body").styles.height = content_h + 2
            except Exception:
                pass
            ol = self.query_one("#modal-list", OptionList)
            ol.focus()
            if self.default in self.options:
                ol.highlighted = self.options.index(self.default)
            self._sync_more()

        def action_body_up(self):
            try:
                self.query_one("#modal-body").scroll_page_up(animate=False)
            except Exception:
                pass

        def action_body_down(self):
            try:
                self.query_one("#modal-body").scroll_page_down(animate=False)
            except Exception:
                pass

        def on_option_list_option_selected(self, event):
            if self._grace_blocked():
                return          # 宽限窗口内忽略（不 dismiss）——防「未点击即返回」
            self.dismiss(str(event.option.prompt))

        def action_cancel(self):
            # Esc = 用户明确取消 → 送回哨兵（绝不能 dismiss(default)：计划默认项就是「确认」）
            self.dismiss(_CANCEL)

    class TextScreen(ModalScreen):
        BINDINGS = [Binding("escape", "cancel", "Cancel")]

        def __init__(self, message: str, default: str = "", password: bool = False):
            super().__init__()
            self.message = message
            self.default = default
            self.password = password

        def compose(self):
            with Vertical(id="modal"):
                yield Static(self.message, id="modal-msg")
                yield Input(value=self.default, password=self.password, id="modal-input")

        def on_mount(self):
            self.query_one("#modal-input", Input).focus()

        def on_input_submitted(self, event):
            self.dismiss(event.value if event.value is not None else self.default)

        def action_cancel(self):
            self.dismiss(self.default)

    # ────────────────────────── UI 适配器（跨线程 Event 桥）──────────────────────────
    class TUIAdapter:
        """worker 线程调用 → call_from_thread 弹模态框 → Event 阻塞等待结果。"""

        def __init__(self, app):
            self.app = app

        def _wait(self, ev, timeout=None):
            """等待模态结果。

            不用裸 ev.wait()：App 退出（_stop 置位）时 push_screen 的回调永远不会
            触发，裸等会让 worker 线程永久阻塞（线程泄漏 + _busy 卡 True、队列堵死）。
            这里轮询等待，App 一退出就返回 False。
            """
            deadline = None if timeout is None else (time.monotonic() + timeout)
            while True:
                if ev.wait(0.2):
                    return True
                stop = getattr(self.app, "_stop", None)
                if stop is not None and stop.is_set():
                    return False
                if deadline is not None and time.monotonic() >= deadline:
                    return False

        def _modal(self, screen, timeout=None, on_timeout=None):
            """弹模态并等待回调。

            ⚠️ 必须带超时：回调一旦丢失（推送失败 / App 已退出），`_wait` 会永远等下去，
            worker 线程被永久阻塞、`_busy` 永远卡 True（此后所有输入都被当成「排队引导」）。
            """
            ev = threading.Event()
            box = {}

            def _cb(res):
                box["r"] = res
                ev.set()

            try:
                self.app.call_from_thread(self.app.push_screen, screen, _cb)
            except Exception:
                return None
            if timeout is None:
                t = _MODAL_TIMEOUT
            elif timeout < 0:
                t = None      # 无限等待（_wait 仍会检查 App 是否退出，不会死等）
            else:
                t = timeout or _MODAL_TIMEOUT
            if not self._wait(ev, t):
                # 仅当 App 仍在运行时才收起模态（退出途中 pop 会与 Textual 竞态）
                stop = getattr(self, "_stop", None)
                if stop is not None and not stop.is_set():
                    try:
                        self.app.call_from_thread(self.app.pop_screen)
                    except Exception:
                        pass
                return on_timeout
            return box.get("r")

        def capture_key(self, prompt="", lang="chinese"):
            """TUI：弹出「按键捕获」框，返回按下的键（Textual 键名，如 ctrl+r / alt+enter）。

            Esc / Ctrl+C 取消 → None。供 /config → 按键设置 里自由改键。
            """
            try:
                return self._modal(KeyCaptureScreen(
                    prompt or _t("keymap_press_prompt", lang),
                    _t("keymap_press_hint", lang)))
            except Exception:
                return None

        # ── ui.py 适配器契约 ──
        def select_option(self, message, options, default="", lang="chinese",
                          blocking=False, body=""):
            # blocking=True（计划确认）：不设超时，且把「Esc 取消」原样透出，
            # 让 confirm_plan 能区分「用户取消（重新询问）」与「交互不可用（安全收尾）」。
            # body 非空 → 用「正文 + 选项同图层」的计划弹窗（可滚动）。
            screen = (PlanConfirmScreen(message, options, default, body=body, lang=lang)
                      if body else SelectScreen(message, options, default, lang=lang))
            r = self._modal(screen,
                            timeout=-1 if blocking else None,
                            on_timeout=_CANCEL)
            if r is None:
                # 模态未完成（App 正在退出 / 推送失败）→ 返回空串表示「取消」。
                # 不能回退成 default：调用方（/config 菜单）会把返回值当成用户选择，
                # 从而静默执行「切换平台」这类有副作用的动作。
                return ""
            if r == _CANCEL:
                return _CANCEL if blocking else ""
            return r

        def confirm(self, message, default=False, lang="chinese"):
            r = self._modal(ConfirmScreen(message, default, lang))
            return default if r is None else bool(r)

        def text_input(self, message, default="", lang="chinese"):
            r = self._modal(TextScreen(message, default, password=False))
            return default if r is None else r

        def secret_input(self, message, default="", lang="chinese"):
            r = self._modal(TextScreen(message, default, password=True))
            return default if r is None else r

        def captcha(self, title, warning, code, lang="chinese"):
            return bool(self._modal(CaptchaScreen(title, warning, code, lang)))

        def confirm_dangerous(self, title, command, reason, lang="chinese",
                              timeout=None, timeout_default=False):
            msg = (f"{title}\n\n{_t('tui_cmd_label', lang)}: {command}\n"
                   f"{_t('tui_risk_label', lang)}: {reason}")
            r = self._modal(ConfirmScreen(msg, False, lang), timeout=timeout, on_timeout="__timeout__")
            if r == "__timeout__":
                return bool(timeout_default), "timeout", ""
            if r is True:
                return True, "y", ""
            return False, "n", _t("tui_user_refused", lang)

        def set_todos(self, todos):
            """TodoWrite 更新 → 主线程刷新侧栏 / 窄屏状态条。"""
            try:
                self.app.call_from_thread(self.app._render_todos, list(todos or []))
            except Exception:
                pass

        def set_status(self, status):
            """每轮结束 → 主线程刷新状态栏（路径 / 上下文 / 缓存率 / 余额）。"""
            try:
                self.app.call_from_thread(self.app._render_status, dict(status or {}))
            except Exception:
                pass

        def set_thinking(self, active):
            """AI 思考中 → 主线程驱动 #activity 转圈动画。"""
            try:
                self.app.call_from_thread(self.app._set_thinking, bool(active))
            except Exception:
                pass

        def set_subagents(self, text):
            """子代理活动尾行 → 主线程显示在 #activity。"""
            try:
                self.app.call_from_thread(self.app._set_subagent_activity, str(text or ""))
            except Exception:
                pass

        def write_rich(self, renderable):
            """把 Rich renderable 投递到日志区（保留 Markdown 颜色 / 面板边框）。

            非阻塞：走 _out_q 统一队列（drain 线程按顺序落盘）。
            旧实现用 call_from_thread —— 那是**同步阻塞**的，工具面板一多就把 worker
            线程拖成串行等待，AI 反过来被 UI 限流。
            """
            try:
                self.app._enqueue_rich(renderable)
            except Exception:
                pass

        def update_stream(self, text, kind: str = "reply"):
            """流式文本更新 → 主线程刷新 #stream 实时预览（kind=reply / reason）。"""
            try:
                self.app.call_from_thread(self.app._render_stream, str(text or ""), kind)
            except Exception:
                pass

        def end_stream(self):
            """流式结束 → 主线程清空并隐藏 #stream。"""
            try:
                self.app.call_from_thread(self.app._end_stream)
            except Exception:
                pass

    # ────────────────────────── Ctrl+R 历史搜索 ──────────────────────────
    class HistorySearchScreen(MoreHintMixin, ModalScreen):
        """增量历史搜索：输入即过滤，Enter / 点击选中，Esc 取消。"""

        BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]
        _MORE_LIST_ID = "hist-list"

        def __init__(self, history, lang: str = "chinese"):
            super().__init__()
            self._hist = [h for h in (history or []) if (h or "").strip()]
            self._lang = lang
            self.lang = lang
            self._filtered = []

        def compose(self):
            with Vertical(id="modal"):
                yield Static(_t("tui_hist_search_title", self._lang), id="modal-msg")
                yield Input(placeholder=_t("tui_hist_search_ph", self._lang), id="hist-query")
                yield self._more_up_widget()
                yield self._more_list()
                yield self._more_down_widget()

        def on_mount(self) -> None:
            self._refresh("")
            try:
                self.query_one("#hist-query", Input).focus()
            except Exception:
                pass

        def _refresh(self, q: str) -> None:
            q = (q or "").strip().lower()
            items = [h for h in self._hist if q in h.lower()] if q else list(self._hist)
            self._filtered = items[:100]
            try:
                ol = self.query_one("#hist-list", OptionList)
                ol.clear_options()
                for h in self._filtered:
                    ol.add_option(Option(h.replace("\n", " ")[:160]))
            except Exception:
                pass
            self._sync_more()

        def on_input_changed(self, event) -> None:
            self._refresh(event.value)

        def on_input_submitted(self, event) -> None:
            self.dismiss(self._filtered[0] if self._filtered else None)

        def on_option_list_option_selected(self, event) -> None:
            try:
                idx = int(event.option_index)
            except Exception:
                idx = 0
            self.dismiss(self._filtered[idx] if 0 <= idx < len(self._filtered) else None)

        def action_cancel(self) -> None:
            self.dismiss(None)

    # ────────────────────────── 输入控件 ──────────────────────────
    class HistorySuggester(Suggester):
        """历史虚影：按当前输入给出最近一条历史（或斜杠命令）作为暗色虚影。

        - 候选来源：与 REPL 共用同一份 FileHistory（~/.config/onyx/ai/history）
          + 当前语言的 AI 斜杠命令表；
        - 输入 `/xxx`（尚未打空格）时优先虚影出斜杠命令；
        - 接受方式：→（右方向键）—— Textual Input 原生行为：光标在末尾且存在虚影时
          按 → 直接把虚影并入输入框。
        """

        def __init__(self, history_path: str, slash_commands=None):
            # 历史随使用实时变化 → 关闭缓存，每次重新计算
            super().__init__(use_cache=False, case_sensitive=True)
            self._history_path = history_path or ""
            self._slash = list(slash_commands or ())
            self._items = []
            self._mtime = None

        def _load(self):
            """按 mtime 惰性重载历史（REPL 与本界面共用同一文件）。"""
            try:
                mtime = os.path.getmtime(self._history_path)
            except OSError:
                self._items, self._mtime = [], None
                return
            if mtime == self._mtime:
                return
            items = []
            try:
                with open(self._history_path, "r", encoding="utf-8", errors="replace") as f:
                    for raw in f:
                        # prompt_toolkit FileHistory：每行前缀 '+'，多行条目按行拆开
                        line = raw.rstrip("\n")
                        if line.startswith("+"):
                            line = line[1:]
                        line = line.strip()
                        if line:
                            items.append(line)
            except OSError:
                items = []
            self._items, self._mtime = items, mtime

        async def get_suggestion(self, value: str):
            if not value:
                return None
            if value.startswith("/") and " " not in value:
                low = value.lower()
                for cmd in self._slash:
                    if cmd != value and cmd.lower().startswith(low):
                        return cmd
            self._load()
            for item in reversed(self._items):
                if item != value and item.startswith(value):
                    return item
            return None

    class PromptArea(TextArea):
        """多行输入框：Enter=换行、Alt+Enter=发送；内容超高时折叠中间并显示省略虚影行。

        折叠规则（关闭软换行，一行 = 一行）：
          - 行数 ≤ MAX_ROWS：按内容显示（高度随内容 1~MAX_ROWS 行增长）；
          - 行数 >  MAX_ROWS：显示 上2 行 + 暗色「⋯ 此处省略 N 行 ⋯」 + 下2 行（共 5 行）。
        """

        MAX_ROWS = 4
        TOP_ROWS = 2
        BOTTOM_ROWS = 2

        # alt+enter：常规 Alt+Enter；alt+ctrl+j：Termux/Android 下 ICRNL 把 \r 转成 \n
        # 后 ESC+Enter 的到达形式（两者都要，否则其中一种终端切换失效）
        # 多行框发送键：来自 keymap 注册表（默认 alt+enter / shift+enter / alt+ctrl+j / ctrl+alt+j）
        # 注：字节层把 ESC+CR / ESC+LF 改写成了 CSI-u shift+enter（见 _ALT_ENTER_CSI_U）
        BINDINGS = _kb("multiline.send", "submit_all", "Send", priority=True, show=False)

        class Submitted(Message):
            """Alt+Enter：把多行内容整体提交给 AI。"""

            def __init__(self, text: str) -> None:
                self.text = text
                super().__init__()

        def __init__(self, omitted_template: str = "", **kwargs):
            kwargs.setdefault("soft_wrap", False)
            kwargs.setdefault("compact", True)
            kwargs.setdefault("highlight_cursor_line", False)
            kwargs.setdefault("show_line_numbers", False)
            super().__init__(**kwargs)
            self._omitted_template = omitted_template or "⋯ {n} ⋯"
            self.show_vertical_scrollbar = False
            self.show_horizontal_scrollbar = False

        # ── 行映射 ──
        def _row_map(self):
            """返回 [(显示行, 文档行 | None)]；文档行为 None 表示省略提示行。"""
            n = self.document.line_count
            if n <= self.MAX_ROWS:
                return [(i, i) for i in range(n)]
            rows = [(i, i) for i in range(self.TOP_ROWS)]
            rows.append((self.TOP_ROWS, None))              # 省略提示行
            start = n - self.BOTTOM_ROWS
            rows.extend((self.TOP_ROWS + 1 + k, start + k) for k in range(self.BOTTOM_ROWS))
            return rows

        def _display_row_of(self, doc_line: int) -> int:
            rows = self._row_map()
            for row, dl in rows:
                if dl == doc_line:
                    return row
            # 光标落在被省略的中间区：贴到省略行，避免光标飞出可视区
            return self.TOP_ROWS

        # ── 高度 ──
        def _chrome_height(self) -> int:
            """边框 + 上下内边距占用的行数（CSS 给 #prompt-ml 设了 round 边框 → 2）。

            `styles.height` 是**含边框**的总高：内容区高度 = height - chrome。
            旧实现直接把 height 设成显示行数，边框吃掉 2 行 →
            内容区被压成 0/负数，用户敲进去的字整个看不见（行数多时只看得见最上面几行）。
            """
            rows = 0
            try:
                styles = self.styles
                for edge in (styles.border_top, styles.border_bottom):
                    if edge and edge[0]:
                        rows += 1
                padding = styles.padding
                rows += int(getattr(padding, "top", 0) or 0)
                rows += int(getattr(padding, "bottom", 0) or 0)
            except Exception:
                rows = 2   # 兜底：#prompt-ml 的 round 边框
            return rows

        def _sync_height(self) -> None:
            try:
                self.styles.height = len(self._row_map()) + self._chrome_height()
            except Exception:
                pass
            self.refresh()

        # ── 生命周期 ──
        def on_mount(self) -> None:
            self._sync_height()

        def on_text_area_changed(self, event) -> None:
            self._sync_height()

        # ── 渲染 ──
        def render_line(self, y: int) -> Strip:
            rows = self._row_map()
            if y >= len(rows):
                return Strip.blank(self.size.width, self.rich_style)
            _row, doc_line = rows[y]
            if doc_line is None:
                return self._render_omitted()
            if not self.text and self.placeholder and doc_line == 0:
                return super().render_line(y)
            # _render_line 内部按 y + scroll_y 取文档行 → 反推让目标文档行正好命中
            return self._render_line(doc_line - self.scroll_offset.y)

        def _render_omitted(self) -> Strip:
            width = max(1, self.size.width)
            n = max(0, self.document.line_count - self.MAX_ROWS)
            try:
                label = self._omitted_template.format(n=n)
            except Exception:
                label = f"⋯ {n} ⋯"
            pad = max(0, (width - cell_len(label)) // 2)
            return Strip([Segment(" " * pad + label, Style(dim=True, italic=True))])

        @property
        def cursor_screen_offset(self) -> Offset:
            """把光标所在文档行映射到显示行（折叠时两者不同）。"""
            region_x, region_y, _w, _h = self.content_region
            cursor_x, cursor_y = self._cursor_offset
            scroll_x, _scroll_y = self.scroll_offset
            row = self._display_row_of(cursor_y)
            return Offset(region_x + cursor_x - scroll_x + self.gutter_width, region_y + row)

        # ── 键位 ──
        def action_submit_all(self) -> None:
            self.post_message(self.Submitted(self.text))

    # ────────────────────────── 输入控件（续） ──────────────────────────
    class PromptInput(Input):
        """单行输入框 + 补全菜单键位。

        priority=True 盖过 Screen 默认的 tab=焦点切换 / Input 自带的 up/down，
        动作统一委托给 App（菜单状态在 App 里维护）。
        """

        # 补全菜单键：接受 / 上下选择来自 keymap 注册表（可在 /config 改键）；
        # escape 关闭属于菜单语义，固定不改。
        BINDINGS = (
            _kb("complete.accept", "menu_accept", "Complete", priority=True, show=False)
            + _kb("complete.next", "menu_down", "Next", priority=True, show=False)
            + _kb("complete.prev", "menu_up", "Prev", priority=True, show=False)
            + [Binding("escape", "menu_close", "Close", priority=True, show=False)]
        )

        def action_menu_accept(self) -> None:
            self.app.action_complete_accept()

        def action_menu_down(self) -> None:
            self.app.action_menu_down()

        def action_menu_up(self) -> None:
            self.app.action_menu_up()

        def action_menu_close(self) -> None:
            self.app.action_menu_close()

        def _on_paste(self, event) -> None:
            """粘贴：含换行的内容交给多行模式。

            ⚠️ Textual 的 Input._on_paste 只取 `event.text.splitlines()[0]` ——
            粘贴多行内容会**丢掉除首行外的全部文本**（实测源码）。
            """
            text = getattr(event, "text", "") or ""
            if "\n" in text or "\r" in text:
                event.stop()
                try:
                    self.app._paste_multiline(text)
                except Exception:
                    pass
                return
            super()._on_paste(event)

        # 注：「转义序列泄漏」防护已上移到 App 层（见 OnyxTUI.on_event）。
        # 早前在控件 _on_key 里做，有两个致命问题：
        #   1) Enter(\r) / 退格(\x7f) 也满足 ch < " " → 被一并吞掉，输入框彻底失灵；
        #   2) Esc 被 PromptInput 的 priority 绑定提前消费，事件根本到不了 _on_key
        #      → 泄漏窗口永远没被打开，防护形同虚设。

    class OnyxDirectoryTree(DirectoryTree):
        """文件树：用几何字形替代 emoji（等宽对齐，避免缩进错位）。"""

        ICON_NODE = "▸ "
        ICON_NODE_EXPANDED = "▾ "
        ICON_FILE = "· "

    class CompletionMenu(OptionList):
        """输入框上方的补全下拉菜单（默认隐藏）。"""

    # ────────────────────────── 主 App ──────────────────────────
    class OnyxTUI(App):
        CSS = """
#body { height: 1fr; }
        #log-wrap { width: 1fr; height: 1fr; }

        /* 阅读区：一条左侧发丝线，去掉四边框 → 少盒子、多留白 */
        #log { width: 1fr; height: 1fr; border-left: solid $onyx-rail; padding: 0 2;
               background: $background; overflow-x: hidden;
               scrollbar-size-vertical: 1; scrollbar-color: $onyx-rail;
               scrollbar-color-hover: $accent; scrollbar-background: $background; }
        /* 活动行：左对齐、accent 转圈，不再是一条居中色带 */
        #thinking { height: 1; display: none; padding: 0 2 0 3; color: $accent;
                    background: $background; }
        /* 流式预览：顶部一条发丝线，与主日志同宽同缩进 */
        #stream { height: auto; max-height: 8; display: none; padding: 0 2 0 3;
                  border-top: solid $onyx-rule; background: $background;
                  overflow-x: hidden; }

        /* 侧栏：左分隔线 + 小节标题（无边框） */
        #sidebar { width: 34; display: none; border-left: solid $onyx-rail;
                   padding: 0 2; background: $background; }
        Screen.wide #sidebar { display: block; }
        .panel-title { text-style: bold; color: $onyx-muted; padding-top: 1; }
        #todo-body { color: $foreground; }
        #files { background: $background; padding: 0; }

        /* 底部输入区 */
        #prompt-wrap { dock: bottom; height: auto; background: $background; }
        #todo-strip { height: auto; display: none; padding: 0 2; color: $onyx-muted; }
        /* 状态栏：窄屏放不下时自动折成第二行（不丢信息），故 height: auto；
           但最多两行，且只在行数变化时才重新布局（见 _render_status），避免频闪 */
        #status-bar { height: auto; max-height: 2; color: $onyx-muted; padding: 0 2; }
        #prompt { width: 100%; background: $surface; border: round $onyx-rail;
                  color: $foreground; }
        #prompt:focus { border: round $accent; }
        #prompt-ml { width: 100%; display: none; background: $surface;
                     border: round $onyx-rail;
                     /* 折叠由 PromptArea 自己做（行映射 + 省略提示行），
                        不显示滚动条：它既会吃掉 2 列宽，又会在文字区里画出一条
                        悬空的滑块。scrollbar-size 归零 = 不占位、不渲染。 */
                     scrollbar-size-vertical: 0; scrollbar-size-horizontal: 0; }
        #prompt-ml:focus { border: round $accent; }
        #prompt-hint { height: 1; color: $onyx-dim; padding: 0 2; }
        #complete-menu { display: none; height: auto; max-height: 8;
                         border: round $accent; background: $panel; }

        /* 模态框：遮罩压暗但不遮内容（55%）；宽度按窄屏优化（手机 40 列也能看全）*/
        ModalScreen { align: center middle; background: $background 55%; }
        #modal { width: 90%; max-width: 96; height: auto; max-height: 88%;
                 padding: 1 2; background: $panel; border: round $accent; }
        /* 计划确认：正文与选项同处一个弹窗；正文区可滚动（高度在 on_mount 自适应）*/
        #modal-title { color: $onyx-muted; text-style: bold; padding: 0 0 0 1; }
        #modal-body { height: 6; margin: 0 0 1 0; padding: 0 1; background: $surface;
                      border-top: solid $onyx-rule; border-bottom: solid $onyx-rule;
                      scrollbar-size-vertical: 1; scrollbar-color: $onyx-rail;
                      scrollbar-background: $surface; }
        /* 选项弹窗：一次显示 _MORE_LIST_MAX(8) 个选项。
           ⚠️ OptionList 自带 `border: tall`（上下各 1 行）+ `padding: 0 1`，
           所以 max-height 要写成 8 + 2 = 10 才能露出 8 个选项。 */
        #modal-list { height: auto; max-height: 10; }
        /* 选项弹窗的上下计数行：仅在有隐藏项时显示（display 由 MoreHintMixin 控制） */
        #modal-more-up, #modal-more-down { height: 1; color: $onyx-muted; padding: 0 1; }
        #modal-msg { padding-bottom: 1; color: $foreground; }
        #modal-warn { color: $warning; padding-bottom: 1; }
        #modal-code { color: $error; text-style: bold; padding-bottom: 1; }
        #modal-btns { height: auto; }
        #modal-btns Button { margin-right: 2; }
        # 历史搜索：同样一次显示 _MORE_LIST_MAX(8) 项（+2 = OptionList 自带边框）
        #hist-list { max-height: 10; background: $surface; }
        """

        # 主界面键位全部来自 keymap 注册表（用户可在 /config → 按键设置 里改）。
        # 默认值与历史行为完全一致。
        # 注：字节层把 ESC+CR / ESC+LF 改写成了 CSI-u shift+enter（见 _ALT_ENTER_CSI_U）
        BINDINGS = (
            _kb("tui.cancel", "cancel", "Cancel", show=False, priority=True)
            + _kb("tui.quit", "quit", "Quit")
            + _kb("tui.eof_quit", "eof_quit", "Quit", priority=True)
            + _kb("tui.history_prev", "menu_up", "History prev", show=False, priority=True)
            + _kb("tui.history_next", "menu_down", "History next", show=False, priority=True)
            + _kb("tui.history_search", "hist_search", "History search", show=False, priority=True)
            + _kb("tui.clear_log", "clear_log", "Clear log", show=False, priority=True)
            + _kb("tui.word_left", "word_left", "Word left", show=False)
            + _kb("tui.word_right", "word_right", "Word right", show=False)
            + _kb("tui.scroll_up", "log_page_up", "Scroll up", show=False, priority=True)
            + _kb("tui.scroll_down", "log_page_down", "Scroll down", show=False, priority=True)
            + _kb("tui.multiline", "to_multiline", "Multiline", show=False)
        )

        def get_theme_variable_defaults(self):
            """CSS 引用的 $onyx-* 变量默认值。

            样式表在 App 构造时就解析，而 onyx 主题要到 on_mount 才注册 ——
            没有这个回退，CSS 会因 `$onyx-rail` 未定义而整表解析失败（界面直接崩）。
            """
            try:
                from bin.ai_lib.ui import ONYX_VARS
                return dict(ONYX_VARS)
            except Exception:
                return {}

        def __init__(self, session_kwargs, ctx):
            super().__init__()
            self._sk = dict(session_kwargs or {})
            self._ctx = ctx
            self._in_q = queue.Queue(maxsize=256)   # 有界：防 AI 长忙时输入堆积爆内存
            # 输出队列同样有界：drain 线程追不上时对写入方形成背压，而不是无限吃内存
            self._out_q = queue.Queue(maxsize=8192)
            self._busy = False
            self._stop = threading.Event()
            self._todos = []       # 最近一次 TodoWrite 的列表（TUI 侧栏 / 状态条）
            self._rich_consoles = []   # 进程内 Rich Console 缓存（_sync_rich_width 用）
            self._threads = []         # 后台线程（退出时 join）
            self._log_w = 0            # 日志区尺寸缓存（主线程量取，worker 线程只读）
            self._log_h = 24
            self._hist = []            # 输入历史（与 REPL 共用同一份 FileHistory）
            self._hist_file = None
            self._hist_idx = 0
            self._hist_draft = ""
            self._hist_setting = False  # 正在以程序方式改写输入框（勿当成用户编辑）
            self._stream_t = 0.0       # 流式渲染节流时间戳
            self._stream_prev = ""     # 已渲染的流式文本快照（增量追加用）
            self._stream_kind = ""     # 当前流式类型（reply/reason，切换时重建）
            self._gc_scanned = False   # 是否已做过一次全堆 Console 扫描（只做一次）
            # ── 触摸滑动（箭头键风暴）识别 ──
            self._arrow_t = 0.0          # 上一次 ↑/↓ 的时间戳
            self._arrow_ts = []          # 最近 N 次箭头的时间戳（滑动窗口判据）
            self._arrow_gesture = False  # 当前是否处于「滑动流」中
            # 手势参数与语义（env 可调，见 _arrow_cfg / _arrow_mode）
            self._arrow_mode = _arrow_mode()
            (_an, _aw, _ag, _ai, _ad) = _arrow_cfg()
            self._ARROW_N, self._ARROW_WINDOW = _an, _aw
            self._ARROW_GAP, self._ARROW_IDLE = _ag, _ai
            self._ARROW_DEFER = _ad
            self._hist_timer = None      # 延迟应用历史步进的定时器
            self._hist_pending_dir = 0   # 待应用方向（-1 上翻 / +1 下翻 / 0 无）
            self._hist_pending_n = 0     # 同方向待应用步数（连按 ↑↑↑ 不丢步）
            self._hist_snap = None       # 箭头流开始前的 (idx, draft, value) 快照（滑动回滚用）
            self._menu_cache = {}        # 补全候选缓存（避免同一文本重复扫盘）
            # 最近若干次「程序化写入」的值（见 _set_input_value / on_input_changed）：
            # Textual 的 Changed 是异步投递的，可能晚到好几拍，单值比对会漏。
            self._hist_prog_values = []
            self._esc_until = 0.0      # 转义序列泄漏窗口截止时间（见 on_event）
            self._hint_key = "tui_single_hint"   # 当前底部提示的 i18n key
            self._capture_stream = None  # 当前替换 sys.stdout/stderr 的捕获流
            self._is_wide = False  # 宽屏显示侧栏；窄屏在输入框上方显示任务状态条
            self._last_balance = None  # (platform, 余额文本)：查询失败时沿用上次成功值
            self._last_status = {}     # 最近一次状态栏数据（窗口 resize 后按新宽度重排）
            self._status_lines = -1    # 状态栏当前行数（只在行数变化时才重新布局，避免频闪）
            self._thinking = False      # AI 是否正在思考（驱动 #activity 转圈）
            self._subagent_text = ""    # 子代理活动尾行
            self._spin_i = 0
            self._spin_timer = None
            self._adapter = TUIAdapter(self)
            self._lang = (ctx or {}).get("lang", "chinese") or "chinese"
            self._menu_cands = []      # 当前补全候选 [(插入文本, 描述, 替换长度)]
            self._menu_base_text = None  # Tab 循环补全的基准文本（None=尚未开始循环）
            self._menu_cycle_idx = -1    # 当前已应用的候选下标（-1=还没选过）
            self._suggester = HistorySuggester(
                _ai_history_path(self._sk.get("user_home_dir") or ""),
                _slash_commands(self._lang),
            )
            self._engine_kwargs = {
                k: v for k, v in self._sk.items()
                if k not in ("user_home_dir", "onyx_module", "global_config",
                             "user_info", "user_mode", "parse_and_execute")
            }

        # ── 全局键事件闸门：转义序列泄漏防护（必须在绑定派发/焦点转发之前）──
        async def on_event(self, event) -> None:
            """在 Textual 把按键分发给绑定/控件之前拦一道，专门过滤「转义序列泄漏」。

            背景：终端把未识别的鼠标/控制序列（滑动、滚轮、特殊按键）当普通字节送来时，
            解析器会先吐一个 Escape，随后紧跟一串可打印字节（'[' '<' '3' '5' ';' 'M'…）。
            这些字节会被聚焦控件当成用户输入灌进输入框。

            为什么放在 App 层（而不是控件 _on_key）：
              - Esc 被 PromptInput 的 priority 绑定（menu_close）提前消费，事件根本到不了
                控件的 _on_key → 控件里记「窗口起点」永远记不上，防护形同虚设；
              - 控件层只覆盖单行框，多行框 / 其他聚焦控件仍然漏。

            放行规则：只丢弃「Esc 之后窗口内到达的可打印字符」，其余一律放行 ——
            Enter(\\r)、退格(\\x7f)、Tab、方向键、Ctrl+X 等控制键绝不能被拦（否则输入框失灵）。
            """
            try:
                if isinstance(event, events.Key) and not event.is_forwarded:
                    ch = getattr(event, "character", None)
                    now = time.monotonic()
                    if getattr(event, "key", "") == "escape":
                        self._esc_until = now + _ESC_LEAK_WINDOW
                    elif ch and len(ch) == 1 and ch.isprintable() and now < self._esc_until:
                        event.stop()
                        event.prevent_default()
                        return
            except Exception:
                pass
            await super().on_event(event)

        def action_eof_quit(self) -> None:
            """Ctrl+D：输入框为空 → 退出 TUI；有内容 → 删除光标右侧字符（对齐常规编辑器）。

            priority=True 绑定，保证盖过 Textual Input / TextArea 自带的 ctrl+d。
            """
            ml = inp = None
            try:
                ml = self.query_one("#prompt-ml", PromptArea)
            except Exception:
                pass
            try:
                inp = self.query_one("#prompt", Input)
            except Exception:
                pass
            if ml is not None and ml.display and ml.text:
                try:
                    ml.action_delete_right()
                except Exception:
                    pass
                return
            if inp is not None and inp.display and inp.value:
                try:
                    inp.action_delete_right()
                except Exception:
                    pass
                return
            self.exit()

        # ── 布局 ──
        def compose(self):
            yield Header(show_clock=True)
            with Horizontal(id="body"):
                with Vertical(id="log-wrap"):
                    yield RichLog(id="log", wrap=True, markup=False, highlight=False,
                                  max_lines=5000)
                    yield RichLog(id="stream", wrap=True, markup=False, highlight=False,
                                  max_lines=400)
                    yield Static("", id="thinking")
                with Vertical(id="sidebar"):
                    # 侧栏面板顶部：本 Python 进程的 PID（os.getpid()，非 shell 子进程）
                    yield Static(f"PID {os.getpid()}", classes="panel-title", id="pid-line")
                    yield Static(_t("tui_todo_title", self._lang), classes="panel-title")
                    yield Static(_t("tui_tasks_none", self._lang), id="todo-body")
                    yield Static(_t("tui_files_title", self._lang), classes="panel-title")
                    yield OnyxDirectoryTree(os.getcwd(), id="files")
            with Vertical(id="prompt-wrap"):
                yield Static("", id="todo-strip")
                yield CompletionMenu(id="complete-menu")
                yield Static("", id="status-bar")
                yield PromptInput(placeholder=_t("tui_placeholder", self._lang), id="prompt",
                                  suggester=self._suggester)
                yield PromptArea(id="prompt-ml",
                                 placeholder=_t("tui_placeholder", self._lang),
                                 omitted_template=_t("tui_omitted", self._lang))
                yield Static(_t("tui_single_hint", self._lang), id="prompt-hint")

        def on_mount(self):
            from bin.ai_lib.ui import set_ui_adapter, set_pending_input_provider
            set_ui_adapter(self._adapter)
            set_pending_input_provider(self._drain_inputs)
            _mode.set_render_mode("tui")
            try:
                self.title = _t("tui_title", self._lang)
            except Exception:
                pass
            # 品牌主题：注册并启用 onyx（调色板与消息语言同源，见 ai_lib/ui.py）
            try:
                self.register_theme(_onyx_theme())
                self.theme = "onyx"
            except Exception:
                pass
            # 顶栏图标换成品牌菱形（默认是「⭘」；用类型选择器避免引入私有 API）
            try:
                for _hi in self.query("HeaderIcon"):
                    _hi.icon = "◆"
            except Exception:
                pass
            # Markdown 主题：RichLog 是用 **Textual 自己的 console** 渲染的，
            # 必须推给它，否则标题只有「粗体+下划线」→ 看起来跟白字没区别。
            try:
                self.console.push_theme(_md_theme())
            except Exception:
                pass
            # 尽早拦 Console 构造器：引擎模块（ai_cmd / tool_executors / …）都是
            # 惰性 import 的，晚装钩子会漏掉「钩子之前」创建的那批 console。
            _install_console_color_hook(self)
            self._banner()
            self._load_history()
            self.query_one("#prompt", Input).focus()
            self._set_wide(self.size.width >= 100)
            # 状态栏：启动即渲染一次（空 Static 高度为 0 → 平时看不见、只有 AI 跑完才「闪一下」）
            try:
                self._render_status({"cwd": os.getcwd()})
            except Exception:
                pass
            # 启动即在面板里显示**本 Python 进程**的 PID（os.getpid()，不是 shell 子进程），
            # 便于在系统里定位 / 结束 Onyx 自己。
            try:
                self.sub_title = f"PID {os.getpid()}"
            except Exception:
                pass
            self._measure()
            self._sync_rich_width(full=True)   # 收集并同步进程内所有 Rich Console
            # 首帧 content_size 常为 0 → 稍后补量一次，保证宽度真的对齐
            try:
                self.set_timer(0.3, self._remeasure)
            except Exception:
                pass
            self._threads = [
                threading.Thread(target=self._worker_loop, daemon=True),
                threading.Thread(target=self._drain_loop, daemon=True),
            ]
            for _th in self._threads:
                _th.start()

        def on_click(self, event) -> None:
            """手机友好：点日志 / 状态栏 / 空白处 → 焦点回到输入框（软键盘随之弹出）。

            之前「点屏幕任意位置都能唤出输入框」靠的是 Textual 的默认焦点规则，但点非可
            聚焦区域时焦点会留在原处甚至丢失 → 软键盘不再弹出（用户反馈）。这里显式兜底：
              - 模态框打开时不抢焦点（弹窗自己管）；
              - 点在可交互控件上（按钮 / 输入框 / 选项列表 / 目录树）时不抢，保持原行为。
            """
            try:
                from textual.screen import ModalScreen
                if isinstance(self.screen, ModalScreen):
                    return
                from textual.widgets import Button, Input, OptionList, TextArea, DirectoryTree
                if isinstance(getattr(event, "widget", None),
                              (Button, Input, TextArea, OptionList, DirectoryTree)):
                    return
                ml = None
                try:
                    ml = self.query_one("#prompt-ml", PromptArea)
                except Exception:
                    ml = None
                if ml is not None and getattr(ml, "display", False):
                    ml.focus()
                else:
                    self.query_one("#prompt", Input).focus()
            except Exception:
                pass

        def on_unmount(self):
            """退出清理：停线程 / 停定时器 / abort 在途请求 / 还原 stdout / 注销钩子。

            ⚠️ 只 `_stop.set()` 是不够的：worker 可能正阻塞在 HTTP 读取上，退出后仍会
            继续跑工具、继续写被替换的全局 stdout（退回 shell 后会污染终端）。
            """
            self._stop.set()

            # 1) 取消在途生成（关闭底层连接，解锁阻塞中的 iter_lines）
            try:
                from bin.ai_lib import mcp_state as _ms
                _ms._AI_INTERRUPTED = True
            except Exception:
                pass
            try:
                from bin.ai_lib.api import abort_active_response
                abort_active_response(self._ctx.get("session_id", ""))
            except Exception:
                pass

            # 2) 停定时器（转圈等）
            try:
                if self._spin_timer is not None:
                    self._spin_timer.stop()
                    self._spin_timer = None
            except Exception:
                pass

            # 3) join 后台线程（带超时，避免卡住退出）
            for _t in list(getattr(self, "_threads", []) or []):
                try:
                    if _t.is_alive():
                        _t.join(timeout=1.0)
                except Exception:
                    pass

            # 4) 还原 stdout/stderr（若仍是本 App 替换的捕获流）
            try:
                out = getattr(self, "_capture_stream", None)
                if out is not None:
                    if sys.stdout is out:
                        sys.stdout = sys.__stdout__
                    if sys.stderr is out:
                        sys.stderr = sys.__stderr__
                    self._capture_stream = None
            except Exception:
                pass

            # 5) 注销适配器 / 输入桥
            try:
                from bin.ai_lib.ui import set_ui_adapter, set_pending_input_provider
                set_ui_adapter(None)
                set_pending_input_provider(None)
            except Exception:
                pass
            _mode.set_render_mode("repl")

        def on_resize(self, event):
            self._set_wide(event.size.width >= 100)
            self._measure()
            self._sync_rich_width()
            self._set_hint()   # 窄屏自动切短版提示
            # 宽度变化 → 状态栏按新宽度重排（折行/展开），避免沿用旧布局
            if getattr(self, "_last_status", None):
                self._render_status(self._last_status)

        def _banner(self):
            """开场横幅：品牌行 + 一行引导（比一整段欢迎语更安静、更有辨识度）。"""
            try:
                from rich.text import Text as _T
                from bin.ai_lib.ui import ONYX_PALETTE as _P, ONYX_VARS as _V
                art = _T()
                art.append("◆ ", style="bold " + _P["accent"])
                art.append("ONYX", style="bold " + _P["foreground"])
                art.append("  " + _t("tui_banner_tag", self._lang),
                           style=_V["onyx-muted"])
                self._log(art)
                self._log(_T(_t("tui_welcome", self._lang), style=_V["onyx-muted"]))
                self._log("")
            except Exception:
                pass

        def _turn_rule(self):
            """轮次分隔线：一条发丝色横线，宽度跟随日志区。"""
            from rich.text import Text as _T
            from bin.ai_lib.ui import ONYX_VARS as _V
            w = max(8, int(self._log_w or 0) - 4)
            return _T("─" * w, style=_V["onyx-rule"])

        def _measure(self):
            """在主线程量取日志区尺寸并缓存（worker 线程不得访问 widget）。"""
            try:
                self._log_w = int(self.query_one("#log").content_size.width)
            except Exception:
                self._log_w = 0
            try:
                self._log_h = max(10, int(self.size.height))
            except Exception:
                self._log_h = 24

        def _remeasure(self):
            """延迟补量一次（on_mount 首帧 content_size 常为 0，会让宽度同步空转）。"""
            self._measure()
            self._sync_rich_width()

        def _collect_rich_consoles(self, full: bool = False):
            """收集进程内所有 Rich Console 实例。

            各模块的 console 是分散的（ui / ai_cmd / tool_executors / helpers / …），
            且 ai_cmd 等是**惰性 import** 的 —— 只按 sys.modules 白名单同步，会漏掉尚未
            加载的模块，导致它们的 console 永远拿不到 force_terminal（TUI 里工具输出、
            状态行、diff 预览全是纯文本）。这里直接按类型收集，覆盖所有已加载的 console。
            """
            try:
                from rich.console import Console as _C
                # 排除 Textual 自己的 App.console：强制它的宽度/终端会干扰 Textual 渲染
                mine = getattr(self, "console", None)
                found = [o for o in list(_RICH_CONSOLE_REGISTRY)
                         if isinstance(o, _C) and o is not mine]
                if full or not self._gc_scanned:
                    # 兜底：TUI 之前就 import 的模块（ui.py 等）其 console 早于钩子安装，
                    # 注册表里没有 → 只在**首次**做一次全堆扫描，之后由钩子增量维护。
                    # 旧实现每轮 AI 调用都 gc.get_objects()（大进程里几十毫秒），
                    # 且强引用所有 Console 阻碍回收。
                    import gc
                    for o in gc.get_objects():
                        if isinstance(o, _C) and o is not mine and o not in found:
                            found.append(o)
                    self._gc_scanned = True
                self._rich_consoles = found
            except Exception:
                self._rich_consoles = []

        def _sync_rich_width(self, full: bool = False):
            """把 AI 输出的 Rich 绘制宽度/着色对齐到日志区。

            ⚠️ 只改 Console 的宽度，**绝不改 os.environ["COLUMNS"]**：
            Textual 驱动用 shutil.get_terminal_size() 判断终端尺寸，而它会读
            COLUMNS/LINES 环境变量 —— 改环境变量等于骗 Textual「终端只有日志区那么窄」，
            侧栏/日志会按错误宽度排版，整个画面错位糊掉（真踩过这个坑）。

            同时强制 force_terminal：TUI 的 stdout 是非 tty 的 _QueueStream，Rich 会判
            非终端而丢弃颜色；强制后输出 ANSI，再由 _log 用 Text.from_ansi 还原颜色。
            ⚠️ Live/Status 必须改用静默 Console（见 ai_cmd._live_console），否则它们会
            往日志里吐光标控制序列把输出冲乱。

            full=True 时重新收集 Console（on_mount / 每轮 AI 调用前各一次）。
            """
            try:
                if full or not getattr(self, "_rich_consoles", None):
                    self._collect_rich_consoles(full=full)
                # 只读缓存尺寸（主线程 _measure() 写入）——worker 线程可安全调用
                w = int(getattr(self, "_log_w", 0) or 0)
                if w <= 8:
                    return
                h = int(getattr(self, "_log_h", 24) or 24)
                try:
                    from rich.console import ColorSystem as _CS
                except Exception:
                    _CS = None
                for console in (self._rich_consoles or []):
                    try:
                        # 与 Rich Console(width=…, height=…) 构造参数等价
                        console._width, console._height = w, h
                        console._force_terminal = True
                        if getattr(console, "_color_system", None) is None and _CS is not None:
                            console._color_system = _CS.TRUECOLOR
                        # Markdown 主题（只推一次，push_theme 会叠加栈）
                        if not getattr(console, "_onyx_md_theme", False):
                            console.push_theme(_md_theme())
                            console._onyx_md_theme = True
                    except Exception:
                        pass
            except Exception:
                pass

        def _set_wide(self, wide: bool):
            self._is_wide = bool(wide)
            try:
                self.screen.set_class(self._is_wide, "wide")
            except Exception:
                pass
            self._render_todos(self._todos)

        # ── 任务列表（TodoWrite）渲染 ──
        @staticmethod
        def _todo_line(idx: int, t: dict):
            """一行任务：序号置灰 + 状态字形（✓/▸/○）+ 语义色文本。"""
            from rich.text import Text as _T
            from bin.ai_lib.ui import ONYX_PALETTE as _P, ONYX_VARS as _V
            st = (t or {}).get("status", "pending")
            glyph, color = {
                "completed": ("✓", _P["success"]),
                "in_progress": ("▸", _P["accent"]),
            }.get(st, ("○", _V["onyx-dim"]))
            content = (t or {}).get("content", "")
            if st == "in_progress":
                content = (t or {}).get("activeForm") or content
            if st == "in_progress":
                text_style = "bold " + _P["foreground"]
            elif st == "completed":
                text_style = _V["onyx-muted"]
            else:
                text_style = _P["foreground"]
            out = _T()
            out.append(f"{idx:>2} ", style=_V["onyx-dim"])
            out.append(glyph + " ", style="bold " + color)
            out.append(str(content), style=text_style)
            return out

        @staticmethod
        def _todo_window(todos):
            """取「上一条 / 当前 / 下一条」窗口（≤3 条，含全局序号）供窄屏状态条显示。"""
            if not todos:
                return []
            cur = None
            for i, t in enumerate(todos):
                if t.get("status") == "in_progress":
                    cur = i
                    break
            if cur is None:
                for i, t in enumerate(todos):
                    if t.get("status") == "pending":
                        cur = i
                        break
            if cur is None:
                lo, hi = max(0, len(todos) - 3), len(todos)
            else:
                lo, hi = max(0, cur - 1), cur + 2
            return [(i + 1, todos[i]) for i in range(lo, hi)]

        def _render_todos(self, todos):
            """刷新侧栏全文与窄屏状态条（worker 线程经 call_from_thread 调用）。"""
            self._todos = list(todos or [])
            try:
                body = self.query_one("#todo-body", Static)
                from rich.text import Text as _T
                if self._todos:
                    body.update(_T("\n").join(
                        [self._todo_line(i, t) for i, t in enumerate(self._todos, 1)]))
                else:
                    body.update(_t("tui_tasks_none", self._lang))
            except Exception:
                pass
            try:
                strip = self.query_one("#todo-strip", Static)
                win = self._todo_window(self._todos)
                if win and not self._is_wide:
                    strip.update(_T("\n").join([self._todo_line(i, t) for i, t in win]))
                    strip.display = True
                else:
                    strip.display = False
            except Exception:
                pass

        # 状态栏最多折成几行（窄屏兜底；行数超出才丢弃最低优先级段）
        _STATUS_MAX_LINES = 2

        def _render_status(self, status):
            """刷新状态栏：◆ 路径 │ 上下文 │ 缓存率 │ 余额。

            - 左侧一个 accent 菱形作视觉锚点，各段用发丝色 `│` 分隔；
            - 数字紧凑化（17,214 → 17.2k）；
            - **一行放不下就折行**（最多 _STATUS_MAX_LINES 行），而不是直接丢弃 ——
              修复窄屏只显示 path / 丢掉 cache 的问题；折行仍放不下时按优先级保留
              （path > ctx > cache > 余额），余额最低且**绝不单独占一行**。
            """
            try:
                from bin.ai_lib.ui import ONYX_PALETTE as _PAL
                s = dict(status or {})
                self._last_status = s   # 供 on_resize 按新宽度重排
                wide = self.size.width >= 90
                segs = []   # [(文本, 颜色)]
                cwd = s.get("cwd") or ""
                if cwd:
                    try:
                        home = os.path.expanduser("~")
                        if home and cwd.startswith(home):
                            cwd = "~" + cwd[len(home):]
                    except Exception:
                        pass
                    if not wide:
                        cwd = os.path.basename(cwd.rstrip("/")) or cwd
                    # 紧凑化：路径段过长时截尾（保留信息量更大的尾部），
                    # 否则它会先把 ctx / cache 挤出可见区（窄屏最常见）
                    _cwd_cap = max(10, min(24, self.size.width - 24))
                    if len(cwd) > _cwd_cap:
                        cwd = "…" + cwd[-(_cwd_cap - 1):]
                    segs.append((cwd, _PAL["primary"]))
                ctx = s.get("ctx") or 0
                if ctx:
                    segs.append(("ctx " + _fmt_compact(ctx), _PAL["primary"]))
                if s.get("cache_supported", True) and s.get("cache_pct") is not None:
                    segs.append(("cache " + f"{s['cache_pct']:.0f}%", _PAL["warning"]))
                bal = s.get("balance")
                _plat = s.get("balance_platform") or ""
                if bal:
                    self._last_balance = (_plat, str(bal))
                elif self._last_balance and self._last_balance[0] == _plat:
                    # 刷新中 / 查询失败 → 沿用上一次成功值，避免余额段忽隐忽现
                    bal = self._last_balance[1]
                try:
                    from rich.cells import cell_len as _cl
                except Exception:
                    _cl = len
                # 紧凑分隔符：' · ' 比 '  │  ' 省 2 列 —— 窄屏一行放不下时会先丢余额，
                # 省下的宽度能让 ctx / cache 稳定留在可见区（用户反馈的重点）
                sep = " · "
                sep_w = _cl(sep)
                # 每行前缀「◆ 」/「  」占 2 列，左右 padding 各 2 列
                avail = max(12, self.size.width - 6)
                # ── 贪心折行：path / ctx / cache 优先，放不下就换行而不是丢弃 ──
                lines = [[]]
                line_w = 0
                for seg in segs:
                    w = _cl(seg[0])
                    add = w if not lines[-1] else w + sep_w
                    if lines[-1] and line_w + add > avail:
                        if len(lines) < self._STATUS_MAX_LINES:
                            lines.append([seg])
                            line_w = w
                        else:
                            break   # 已到行数上限 → 丢弃剩余
                    else:
                        lines[-1].append(seg)
                        line_w += add
                # 余额：最低优先级 —— 只允许「追加到已有行尾」，绝不为它单开一行
                # （窄屏一行放不下 path+ctx+cache+余额时，宁可省掉余额也不多占一行）
                if bal:
                    _seg = (str(bal), _PAL["success"])
                    _w = _cl(_seg[0])
                    if not lines[-1]:
                        lines[-1].append(_seg)              # 前面没有任何段
                    elif line_w + sep_w + _w <= avail:
                        lines[-1].append(_seg)
                lines = [ln for ln in lines if ln]
                bar = self.query_one("#status-bar", Static)
                if not lines:
                    bar.display = False
                else:
                    from rich.text import Text as _T
                    from bin.ai_lib.ui import ONYX_PALETTE as _P, ONYX_VARS as _V
                    out = _T()
                    for li, line in enumerate(lines):
                        if li:
                            out.append("\n")
                        # 首行锚点用菱形，续行缩进对齐（保持左侧视觉轴线）
                        out.append("◆ " if li == 0 else "  ",
                                   style="bold " + _P["accent"])
                        for i, (txt, color) in enumerate(line):
                            if i:
                                out.append(sep, style=_V["onyx-rail"])
                            out.append(txt, style=color)
                    bar.update(out, layout=(len(lines) != self._status_lines))
                    self._status_lines = len(lines)
                    bar.display = True
            except Exception:
                pass

        # ── 活动行（AI 思考转圈 / 子代理活动尾行）──
        _SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

        def _render_activity(self):
            """刷新输出框内的 #thinking 状态行：思考中转圈，否则子代理尾行；皆无则隐藏。"""
            try:
                act = self.query_one("#thinking", Static)
                if self._thinking:
                    fr = self._SPIN_FRAMES[self._spin_i % len(self._SPIN_FRAMES)]
                    txt = f"{fr} {_t('tui_thinking', self._lang)}"
                    if self._subagent_text:
                        txt += "   " + self._subagent_text
                    # layout=False：本行高度恒为 1，默认的 layout=True 会让
                    # #log-wrap 每 0.12s 重排一次（日志区整块重绘 = 「全面渲染」观感）
                    act.update(txt, layout=False)
                    act.display = True
                elif self._subagent_text:
                    act.update(self._subagent_text, layout=False)
                    act.display = True
                else:
                    act.display = False
            except Exception:
                pass

        def _tick_spinner(self):
            if not self._thinking:
                return
            self._spin_i += 1
            self._render_activity()

        def _set_thinking(self, active: bool):
            self._thinking = bool(active)
            if self._thinking:
                if self._spin_timer is None:
                    try:
                        self._spin_timer = self.set_interval(0.12, self._tick_spinner)
                    except Exception:
                        self._spin_timer = None
            else:
                if self._spin_timer is not None:
                    try:
                        self._spin_timer.stop()
                    except Exception:
                        pass
                    self._spin_timer = None
            self._render_activity()

        def _set_subagent_activity(self, text: str):
            self._subagent_text = (text or "").strip()
            self._render_activity()

        # ── 输入 ──
        # ── 补全菜单（候选与 REPL 共用 completion_candidates）──
        def _menu_label(self, value: str, meta: str):
            from rich.text import Text as _T
            t = _T(value, style="bold")
            if meta:
                t.append("  " + meta, style="dim")
            return t

        def _menu_hide(self) -> None:
            try:
                self.query_one("#complete-menu", CompletionMenu).display = False
            except Exception:
                pass
            self._menu_cands = []
            self._menu_reset_cycle()

        def _menu_reset_cycle(self) -> None:
            """补全循环复位：下次按 Tab 从「只打开菜单、不插入」重新开始。"""
            self._menu_base_text = None
            self._menu_cycle_idx = -1

        def _menu_cycle_text(self) -> str:
            """当前循环下标对应的完整输入文本（尚未开始循环返回 ""）。"""
            base = self._menu_base_text
            if base is None or not (0 <= self._menu_cycle_idx < len(self._menu_cands)):
                return ""
            value, _meta, replace_len = self._menu_cands[self._menu_cycle_idx]
            cut = max(0, len(base) - replace_len)
            return base[:cut] + value

        def _menu_update(self, text: str) -> None:
            """按当前输入刷新补全菜单（输入 / 或路径时才出现）。"""
            try:
                menu = self.query_one("#complete-menu", CompletionMenu)
            except Exception:
                return
            # Tab 循环补全写入输入框会异步触发 on_input_changed → 这里必须原样保留候选与
            # 循环下标。旧实现每次重算都把 menu.highlighted 复位 0，于是连按 Tab 永远停在
            # 第一项（用户反馈「只做了一次完整补全，手感很怪」）。
            if text and text == self._menu_cycle_text():
                return
            self._menu_reset_cycle()
            if not (text or "").strip():
                self._menu_hide()
                return
            # 同一文本不重复算（路径补全要 listdir，退格/重复输入时省掉大量扫盘）
            key = text or ""
            if key in self._menu_cache:
                cands = self._menu_cache[key]
            else:
                try:
                    from bin.ai_interactive import completion_candidates
                    cands = completion_candidates(key, self._lang)
                except Exception:
                    cands = []
                if len(self._menu_cache) > 48:
                    self._menu_cache.clear()
                self._menu_cache[key] = cands
            if not cands:
                self._menu_hide()
                return
            self._menu_cands = cands
            try:
                menu.clear_options()
                for i, (value, meta, _rl) in enumerate(cands):
                    menu.add_option(Option(self._menu_label(value, meta), id=str(i)))
                menu.highlighted = 0
                menu.display = True
            except Exception:
                self._menu_hide()

        def on_input_changed(self, event) -> None:
            """输入变化：刷新补全菜单（仅单行框）+ 历史游标回到「最新」。

            ⚠️ 旧实现把本方法定义了**两次**（后面的覆盖前面）→ 打字时补全菜单永远不弹
            （只有按 Tab 才出现），前一份实现沦为死代码。这里合并成一份，两个行为都保留。
            """
            try:
                # 程序化写入（翻历史 / 滑动回滚）触发的异步 Changed：值与我们刚写入的
                # 完全一致 → 不是用户编辑，绝不能复位历史游标（否则连按 ↑ 永远停在最新一条）。
                val = getattr(event, "value", None)
                _prog = val is not None and val in getattr(self, "_hist_prog_values", ())
                if not _prog and not getattr(self, "_hist_setting", False) \
                        and self._hist_idx != len(self._hist):
                    self._hist_idx = len(self._hist)
            except Exception:
                pass
            try:
                if getattr(getattr(event, "input", None), "id", "") == "prompt":
                    self._menu_update(event.value)
            except Exception:
                pass

        def action_complete_accept(self) -> None:
            """Tab：打开补全菜单 → 再按 Tab 依次向下循环选择候选（对齐主 REPL 手感）。

            旧实现是「直接把第 0 个候选整词填进去」，而且填完又调 _menu_update 把
            highlighted 复位成 0 → 连按 Tab 永远停在第一项，等于只做了一次完整补全。

            现行为与 shell REPL（lib/terminal/kb.py 的 completion_next）及 AI REPL
            （bin/ai_interactive.py 的 _complete_next）一致：
              ① 菜单未开 → 只打开菜单、**不插入**（≈ start_completion(select_first=False)）；
              ② 菜单已开 → 选中并插入**下一项**，到末尾回到第一项（≈ complete_next 环绕）。
            每次插入都基于「循环开始时的基准文本」替换，保证在候选间来回切换不叠加。
            """
            try:
                menu = self.query_one("#complete-menu", CompletionMenu)
                inp = self.query_one("#prompt", PromptInput)
            except Exception:
                return
            text = inp.value or ""

            # ① 菜单未开：只打开菜单（不插入任何内容）
            if not (menu.display and self._menu_cands):
                self._menu_update(text)
                if menu.display and self._menu_cands:
                    self._menu_base_text = text      # 记下基准文本，后续 Tab 基于它替换
                    self._menu_cycle_idx = -1
                return

            # ② 菜单已开：循环到下一项并插入
            if self._menu_base_text is None:
                self._menu_base_text = text
                self._menu_cycle_idx = -1
            n = len(self._menu_cands)
            if n <= 0:
                return
            self._menu_cycle_idx = (self._menu_cycle_idx + 1) % n
            value, _meta, replace_len = self._menu_cands[self._menu_cycle_idx]
            base = self._menu_base_text or ""
            cut = max(0, len(base) - replace_len)
            new_text = base[:cut] + value
            try:
                inp.value = new_text
                inp.cursor_position = len(new_text)
            except Exception:
                pass
            try:
                menu.highlighted = self._menu_cycle_idx   # 高亮跟着走，看得见选中了哪一项
            except Exception:
                pass

        def _menu_sync_cycle_from_menu(self, menu) -> None:
            """↓/↑ 手动移动高亮后，让 Tab 的循环下标跟上（否则下次 Tab 会跳回原处）。"""
            try:
                idx = menu.highlighted
                if isinstance(idx, int) and 0 <= idx < len(self._menu_cands):
                    if self._menu_base_text is None:
                        try:
                            self._menu_base_text = self.query_one("#prompt", PromptInput).value or ""
                        except Exception:
                            self._menu_base_text = ""
                    self._menu_cycle_idx = idx
            except Exception:
                pass

        def action_menu_down(self) -> None:
            """↓（菜单未显示时）→ 历史下一条。"""
            try:
                menu = self.query_one("#complete-menu", CompletionMenu)
                if menu.display:
                    menu.action_cursor_down()
                    self._menu_sync_cycle_from_menu(menu)
                    return
            except Exception:
                pass
            self._hist_nav(1)

        def action_menu_up(self) -> None:
            """↑（菜单未显示时）→ 历史上一条。"""
            try:
                menu = self.query_one("#complete-menu", CompletionMenu)
                if menu.display:
                    menu.action_cursor_up()
                    self._menu_sync_cycle_from_menu(menu)
                    return
            except Exception:
                pass
            self._hist_nav(-1)

        def action_menu_close(self) -> None:
            self._menu_hide()

        def action_cancel(self) -> None:
            """Ctrl+C：忙 → 取消当前生成；闲 → 清空输入框（不再直接退出 TUI）。

            取消机制复用引擎已有的中断标志：SSE 循环在下一 chunk 看到
            mcp_state._AI_INTERRUPTED 就会返回 _interrupted 并丢弃半截内容；
            abort_active_response 关闭底层连接，解锁阻塞中的 iter_lines。
            """
            if self._busy:
                # ── 优先：把中断转发给「AI 正在执行的命令」──
                # 与 REPL 语义一致：有命令在跑时，Ctrl+C 只杀命令、不打断 AI 循环；
                # 重复按逐级升级（SIGINT → SIGTERM → SIGKILL），解决「命令忽略 SIGINT /
                # 卡在不可中断状态 → Ctrl+C 杀不掉」的老问题。
                # （TUI 里终端处于 raw 模式，Ctrl+C 不会变成信号，只能在这里主动转发。）
                try:
                    from lib.terminal import exe as _exe_mod
                    if _exe_mod.interrupt_active_ai_cmds():
                        self._log(_t("tui_cmd_interrupted", self._lang))
                        return
                except Exception:
                    pass
                try:
                    from bin.ai_lib import mcp_state as _ms
                    _ms._AI_INTERRUPTED = True
                except Exception:
                    pass
                try:
                    from bin.ai_lib.api import abort_active_response
                    abort_active_response(self._ctx.get("session_id", ""))
                except Exception:
                    pass
                self._log(_t("tui_cancelling", self._lang))
                return
            # 闲：清空当前输入（单行框 + 多行框都覆盖）
            cleared = False
            for sel in ("#prompt", "#prompt-ml"):
                try:
                    w = self.query_one(sel)
                except Exception:
                    continue
                if not getattr(w, "display", False):
                    continue
                cur = getattr(w, "value", None)
                if cur is None:
                    cur = getattr(w, "text", "")
                if not (cur or "").strip():
                    continue
                try:
                    if isinstance(w, PromptArea):
                        w.text = ""
                    else:
                        w.value = ""
                except Exception:
                    pass
                cleared = True
            if cleared:
                self._menu_hide()
            else:
                self._log(_t("tui_ctrlc_hint", self._lang))

        def _set_hint(self, key: str = None) -> None:
            """刷新底部提示；窄屏自动切短版，避免被裁切。"""
            try:
                if key:
                    self._hint_key = key
                key = getattr(self, "_hint_key", "tui_single_hint")
                txt = _t(key, self._lang)
                if self.size.width < 72:
                    short = _t(key + "_short", self._lang)
                    if short and short != key + "_short":
                        txt = short
                self.query_one("#prompt-hint", Static).update(txt)
            except Exception:
                pass

        def _apply_lang(self, lang: str) -> None:
            """同步界面语言（/lang 切换后调用）：刷新所有依赖 self._lang 的控件。

            面板标题（▣ TODO /storage/emulated/0/abPython/PythonProject/工具/Hacker--V1.00.1/src/Hacker/onyx-test ▤ FILES）与状态栏均为语言无关内容，无需刷新。
            """
            lang = "english" if str(lang).lower() in ("en", "english") else "chinese"
            if lang == getattr(self, "_lang", None):
                return
            self._lang = lang
            try:
                self.title = _t("tui_title", self._lang)
            except Exception:
                pass
            for _wid in ("#prompt", "#prompt-ml"):
                try:
                    self.query_one(_wid).placeholder = _t("tui_placeholder", self._lang)
                except Exception:
                    pass
            try:
                self.query_one("#prompt-ml").omitted_template = _t("tui_omitted", self._lang)
            except Exception:
                pass
            try:
                if getattr(self, "_suggester", None) is not None:
                    self._suggester._slash = _slash_commands(self._lang)
            except Exception:
                pass
            self._set_hint()

        def _drain_inputs(self):
            """非阻塞取走排队输入（供引擎在每轮 API 调用前注入为实时引导）。

            仅在 worker 线程执行 handle_ai 期间被调用（此时 worker 自身阻塞在
            _run_one 内，不会消费 _in_q），因此不会与 worker 抢输入。
            """
            items = []
            while True:
                try:
                    items.append(self._in_q.get_nowait())
                except queue.Empty:
                    break
            return items

        # ── 其余输入能力（对齐 REPL）──
        def _delegate(self, *names) -> None:
            """把动作委托给当前聚焦的输入控件（单行/多行动作名可能不同）。"""
            w = self._focused_input()
            if w is None:
                return
            for n in names:
                fn = getattr(w, n, None)
                if callable(fn):
                    try:
                        fn()
                        return
                    except Exception:
                        pass

        def action_word_left(self) -> None:
            """Alt+B：按词左移。"""
            self._delegate("action_cursor_left_word", "action_cursor_word_left")

        def action_word_right(self) -> None:
            """Alt+F：按词右移。"""
            self._delegate("action_cursor_right_word", "action_cursor_word_right")

        def action_log_page_up(self) -> None:
            """PageUp：输出框上翻一页（鼠标滚动之外的可选方式）。"""
            try:
                self.query_one("#log", RichLog).scroll_page_up(animate=False)
            except Exception:
                pass

        def action_log_page_down(self) -> None:
            """PageDown：输出框下翻一页。"""
            try:
                self.query_one("#log", RichLog).scroll_page_down(animate=False)
            except Exception:
                pass

        def action_clear_log(self) -> None:
            """Ctrl+L：清空日志区（对齐 REPL 的 Ctrl+L 清屏）。"""
            try:
                self.query_one("#log", RichLog).clear()
            except Exception:
                pass

        def action_hist_search(self) -> None:
            """Ctrl+R：增量历史搜索，选中项填回输入框。"""
            if not self._hist:
                return

            def _on_pick(picked):
                if picked:
                    w = self._focused_input()
                    if w is not None:
                        self._set_input_value(w, picked)

            try:
                self.push_screen(HistorySearchScreen(self._hist, self._lang), _on_pick)
            except Exception:
                pass

        def _paste_multiline(self, text: str) -> None:
            """把含换行的粘贴内容放进多行框（单行框只能容纳一行）。"""
            try:
                inp = self.query_one("#prompt", Input)
                ml = self.query_one("#prompt-ml", PromptArea)
            except Exception:
                return
            if not ml.display:
                cur = inp.value or ""
                inp.display = False
                inp.value = ""
                ml.display = True
                ml.text = cur
            ml.text = (ml.text or "") + text
            try:
                ml.focus()
            except Exception:
                pass
            self._set_hint("tui_ml_hint")

        # ── 输入历史（与 REPL 共用同一份 prompt_toolkit FileHistory）──
        def _load_history(self) -> None:
            """载入历史（新→旧）。用 prompt_toolkit 的 FileHistory 保证与 REPL 格式一致。"""
            self._hist, self._hist_file = [], None
            try:
                path = _ai_history_path(self._sk.get("user_home_dir") or "")
                if not path:
                    raise ValueError("no history path")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                from prompt_toolkit.history import FileHistory
                self._hist_file = FileHistory(path)
                self._hist = [s for s in self._hist_file.load_history_strings() if (s or "").strip()]
            except Exception:
                self._hist_file = None
            self._hist_idx = len(self._hist)
            self._hist_draft = ""

        def _hist_add(self, text: str) -> None:
            """写入历史（同一文件、同一格式 → 与 REPL 跨模式/跨会话共享）。"""
            text = (text or "").strip()
            if not text:
                return
            try:
                if self._hist_file is not None:
                    self._hist_file.append_string(text)
            except Exception:
                pass
            if not self._hist or self._hist[0] != text:
                self._hist.insert(0, text)
            self._hist_idx = len(self._hist)
            self._hist_draft = ""

        def _focused_input(self):
            """当前可见的输入控件（单行优先）。"""
            for sel in ("#prompt", "#prompt-ml"):
                try:
                    w = self.query_one(sel)
                except Exception:
                    continue
                if getattr(w, "display", False):
                    return w
            return None

        def _set_input_value(self, w, value: str) -> None:
            """以程序方式改写输入框（标记 _hist_setting，避免被当成用户编辑）。"""
            self._hist_setting = True
            try:
                if isinstance(w, PromptArea):
                    w.text = value
                else:
                    w.value = value
                try:
                    w.cursor_position = len(value)
                except Exception:
                    pass
            except Exception:
                pass
            finally:
                self._hist_setting = False
            # Textual 的 Changed 是异步投递的 → 标志位挡不住，必须靠「值比对」
            # （见 on_input_changed）：记下这次程序化写入的值，供异步事件识别。
            self._hist_prog_values.append(value)
            if len(self._hist_prog_values) > 16:
                del self._hist_prog_values[:-16]

        # ── 触摸滑动 vs 真实按键 ──
        # 终端把「上下滑动」编码成**一连串** ↑/↓（触摸帧驱动），人类按键则是零散的几下。
        # 早期判据（间隔 <30ms 且连续 2 次）在真机上仍会误判 —— 说明实际滑动流的节奏
        # 因终端而异（可能 40~150ms 一个）。这里改成**滑动窗口计数**：
        #   「N 次箭头落在 WINDOW 秒内，且相邻间隔 < GAP」→ 判定为滑动流。
        # 参数可用 env 调（ONYX_TUI_ARROW_N / _WINDOW / _GAP / _IDLE），语义还可用
        # ONYX_TUI_ARROW_MODE 直接切成 history / scroll。
        def _arrow_gesture_check(self) -> bool:
            """True = 这次 ↑/↓ 属于滑动流（应滚动日志而不是翻历史）。"""
            now = time.monotonic()
            gap = now - self._arrow_t
            self._arrow_t = now

            if self._arrow_mode == "history":
                return False
            if self._arrow_mode == "scroll":
                return True

            if gap >= self._ARROW_IDLE:
                # 空闲够久 → 上一段结束
                self._arrow_gesture = False
                self._arrow_ts = []
            self._arrow_ts.append(now)
            if len(self._arrow_ts) > self._ARROW_N:
                del self._arrow_ts[:-self._ARROW_N]
            if self._arrow_gesture:
                return True
            if (len(self._arrow_ts) >= self._ARROW_N
                    and (self._arrow_ts[-1] - self._arrow_ts[0]) <= self._ARROW_WINDOW
                    and gap < self._ARROW_GAP):
                self._arrow_gesture = True
                return True
            return False

        def _scroll_log(self, delta: int) -> None:
            """滚动消息日志（触摸滑动被识别为箭头流时走这里）。"""
            try:
                self.query_one("#log").scroll_relative(y=delta, animate=False)
            except Exception:
                pass

        def _hist_apply(self, direction: int) -> None:
            """真正把一次历史步进写入输入框（direction: -1 上翻 / +1 下翻）。

            索引约定：`_hist` 为「新→旧」，`_hist_idx == len(_hist)` 表示「未在翻历史」。
            """
            w = self._focused_input()
            if w is None or not self._hist:
                return
            if direction < 0:
                if self._hist_idx >= len(self._hist):
                    cur = getattr(w, "value", None)
                    if cur is None:
                        cur = getattr(w, "text", "")
                    self._hist_draft = cur or ""
                    self._hist_idx = 0
                elif self._hist_idx < len(self._hist) - 1:
                    self._hist_idx += 1
                self._set_input_value(w, self._hist[self._hist_idx])
            else:
                if self._hist_idx >= len(self._hist):
                    return
                if self._hist_idx == 0:
                    self._hist_idx = len(self._hist)
                    self._set_input_value(w, self._hist_draft)
                else:
                    self._hist_idx -= 1
                    self._set_input_value(w, self._hist[self._hist_idx])

        def _hist_cancel_pending(self) -> None:
            """取消挂起的历史步进（滑动流被识别后调用，避免残留改写）。"""
            self._hist_pending_dir = 0
            self._hist_pending_n = 0
            if self._hist_timer is not None:
                try:
                    self._hist_timer.stop()
                except Exception:
                    pass
                self._hist_timer = None

        def _hist_snapshot(self) -> None:
            """记录「箭头流开始前」的历史状态（供滑动判定成立时一次性回滚）。"""
            w = self._focused_input()
            cur = ""
            if w is not None:
                cur = getattr(w, "value", None)
                if cur is None:
                    cur = getattr(w, "text", "")
            self._hist_snap = (self._hist_idx, self._hist_draft, cur or "")

        def _hist_restore_snap(self) -> None:
            """滑动判定成立 → 把历史游标/输入框一次性还原到流开始前（幂等）。

            为什么需要：箭头间隔落在 DEFER 与 GAP 之间时（如 120ms/个的慢速滑动），
            第一次箭头已经到点提交过 → 输入框与游标被改。这里在**判定成立时**还原一次，
            把「误翻」彻底抹掉（旧实现是每个箭头都翻+回滚，必然频闪）。
            """
            snap = self._hist_snap
            if not snap:
                return
            self._hist_snap = None
            idx, draft, value = snap
            self._hist_idx = idx
            self._hist_draft = draft
            w = self._focused_input()
            if w is None:
                return
            cur = getattr(w, "value", None)
            if cur is None:
                cur = getattr(w, "text", "")
            if (cur or "") != value:
                self._set_input_value(w, value)

        def _hist_commit(self) -> None:
            """延迟计时器到点：应用挂起的历史步进（可含多步，见 _hist_nav）。"""
            self._hist_timer = None
            d = self._hist_pending_dir
            n = self._hist_pending_n
            self._hist_pending_dir = 0
            self._hist_pending_n = 0
            if d:
                for _ in range(max(1, n)):
                    self._hist_apply(d)

        def _hist_nav(self, direction: int) -> None:
            """↑/↓ 入口：先判手势；非滑动 → **延迟**应用（消除滑动时的历史频闪）。

            为什么延迟：终端把触摸滑动编码成一串 ↑/↓。旧实现「先翻历史、判到滑动再回滚」，
            输入框必然先闪一下再退回（用户实测）。改成延迟后——只要箭头还在连续到来
            （间隔 < _ARROW_DEFER），计时器就被不断重置，输入框**永不改写**；
            只有真实按键（箭头流结束后静默 _ARROW_DEFER 秒）才生效。

            连按不丢步：同一方向的待应用步数会累加（_hist_pending_n），到点一次性补齐 ——
            否则「快速按三下 ↑」只会走一格（人的按键间隔常在 80ms 以内）。

            延迟时长自适应：上一次箭头若就在眼前（间隔 < _ARROW_GAP）说明可能是滑动流，
            此时用更长的 _ARROW_GAP 作延迟 —— 滑动中「一连串箭头」就永远不会到点提交，
            输入框一个字都不会被改写；孤立按键仍走短的 _ARROW_DEFER（跟手）。
            """
            now = time.monotonic()
            gap = now - self._arrow_t
            if self._arrow_gesture_check():
                # 判定为滑动流 → 取消已排队的延迟应用，并把「误翻」一次性还原到
                # 流开始前（箭头间隔落在 DEFER~GAP 之间时，第一下可能已经提交过）。
                self._hist_cancel_pending()
                self._hist_restore_snap()
                self._scroll_log(-3 if direction < 0 else 3)
                return
            if self._hist_snap is None or gap >= self._ARROW_IDLE:
                self._hist_snapshot()      # 新的一段箭头流开始 → 记录回滚点
            if self._hist_timer is not None:
                try:
                    self._hist_timer.stop()
                except Exception:
                    pass
                self._hist_timer = None
            if self._hist_pending_dir == direction:
                self._hist_pending_n += 1        # 同方向连按 → 累加，到点一次补齐
            else:
                self._hist_pending_dir = direction
                self._hist_pending_n = 1
            _defer = self._ARROW_DEFER
            if gap < self._ARROW_GAP:
                _defer = max(self._ARROW_GAP, self._ARROW_DEFER)
            try:
                self._hist_timer = self.set_timer(_defer, self._hist_commit)
            except Exception:
                self._hist_commit()   # 无事件循环（罕见）→ 立即应用兜底

        def _submit(self, text: str) -> None:
            """统一提交入口：文本进队列（AI 忙时排队 = 实时引导）。"""
            text = (text or "").strip()
            if not text:
                return
            self._hist_add(text)
            try:
                self._in_q.put_nowait(text)
            except queue.Full:
                self._log(_t("tui_queue_full", self._lang))
                return
            if self._busy:
                self._log(_t("tui_queued", self._lang, text=text))

        def _modal_open(self) -> bool:
            """当前是否有模态框占屏（模态期间绝不允许把输入当成 AI 消息）。"""
            try:
                from textual.screen import ModalScreen
                return isinstance(self.screen, ModalScreen)
            except Exception:
                return False

        def on_input_submitted(self, event):
            """单行框：Enter 发送（虚影由 → 接受）。

            ⚠️ Textual 的 Input.Submitted 会从**任意**输入框沿 DOM 冒泡到 App ——
            包括模态框里的 #modal-input（历史搜索 / 文本输入 / 验证码）。不过滤就会出现
            「在弹窗里打完字按回车 → 内容被当成发给 AI 的消息」（用户实测 bug）。
            这里只认主输入框 #prompt，且模态框打开期间一律忽略。
            """
            if getattr(getattr(event, "input", None), "id", "") != "prompt":
                return
            if self._modal_open():
                return
            self._menu_hide()
            try:
                self.query_one("#prompt", Input).value = ""
            except Exception:
                pass
            self._submit(event.value)

        # ── 多行模式：Alt+Enter 进入；多行框内 Enter=换行、Alt+Enter=发送 ──
        def action_to_multiline(self) -> None:
            """单行框按 Alt+Enter：切到多行框，并把已输入内容带过去。"""
            try:
                inp = self.query_one("#prompt", Input)
                ml = self.query_one("#prompt-ml", PromptArea)
            except Exception:
                return
            if ml.display:
                return
            text = inp.value or ""
            ml.text = text + "\n" if text else ""
            try:
                ml.cursor_location = ml.document.end
            except Exception:
                pass
            inp.value = ""
            inp.display = False
            ml.display = True
            ml.focus()
            self._set_hint("tui_ml_hint")

        def _exit_multiline(self) -> None:
            try:
                inp = self.query_one("#prompt", Input)
                ml = self.query_one("#prompt-ml", PromptArea)
            except Exception:
                return
            ml.text = ""
            ml.display = False
            inp.display = True
            inp.value = ""
            inp.focus()
            self._set_hint("tui_single_hint")

        def on_prompt_area_submitted(self, event) -> None:
            """多行框 Alt+Enter：整体发送并退回单行框。"""
            text = event.text
            self._exit_multiline()
            self._submit(text)

        # ── 工作线程：顺序消费输入队列（AI 运行时输入排队 = 实时引导）──
        def _worker_loop(self):
            while not self._stop.is_set():
                try:
                    text = self._in_q.get(timeout=0.2)
                except queue.Empty:
                    continue
                self._busy = True
                try:
                    self._run_one(text)
                except KeyboardInterrupt:
                    pass   # Ctrl+C 语义由 action_cancel 处理；这里只保证不杀线程
                except BaseException as e:
                    # 必须兜到 BaseException：SystemExit（如 plugin_loader 里的 sys.exit）
                    # 一旦从 except 逃逸 → worker 线程死亡、输入队列永久堵死。
                    # App 已退出时 call_from_thread 会抛 RuntimeError，所以这里还要再兜一层。
                    try:
                        self.call_from_thread(self._log, f"⚠️ {type(e).__name__}: {e}")
                    except Exception:
                        pass
                finally:
                    self._busy = False

        def _run_one(self, text):
            ctx = self._ctx
            out = _QueueStream(self._out_q, stop_event=self._stop)
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = out
            sys.stderr = out
            self._capture_stream = out
            try:
                if text.startswith("/") and "\n" not in text:
                    from bin.ai_interactive import _dispatch_slash
                    _alive = _dispatch_slash(text, ctx)


                    try:
                        self.call_from_thread(self._apply_lang, ctx.get("lang") or self._lang)
                    except Exception:
                        pass
                    if not _alive:
                        self.call_from_thread(self.exit)
                    return
                # 引擎模块是惰性导入的，此刻才真正加载 → 先让主线程重新量取日志区宽度
                # （on_mount 首帧 content_size 常为 0），再同步所有 Console；否则
                # ai_cmd/tool_executors 的 console 在本轮会是「非终端 + 全屏宽」→ 换行错乱。
                try:
                    self.call_from_thread(self._measure)
                except Exception:
                    pass
                # 钩子已在 on_mount 装好，引擎模块的 console 会自动进注册表 →
                # 这里走注册表路径（不再每轮 gc.get_objects() 全堆扫描）。
                self._sync_rich_width()
                _install_console_color_hook(self)   # 幂等：兜住极端时序
                try:
                    from rich.text import Text as _T
                    from bin.ai_lib.ui import ONYX_PALETTE as _P, ONYX_VARS as _V
                    self.call_from_thread(self._log, self._turn_rule())
                    line = _T()
                    line.append("❯ ", style="bold " + _V["onyx-user"])
                    line.append(text, style="bold " + _P["foreground"])
                    self.call_from_thread(self._log, line)
                    self.call_from_thread(self._log, "")
                except Exception:
                    self.call_from_thread(self._log, f"❯ {text}")
                from bin.ai_interactive import _call_ai_engine
                _call_ai_engine(
                    text,
                    self._sk.get("user_home_dir"),
                    self._sk.get("onyx_module"),
                    self._sk.get("global_config"),
                    self._sk.get("user_info"),
                    self._sk.get("user_mode"),
                    self._sk.get("parse_and_execute"),
                    ctx,
                    **self._engine_kwargs,
                )
            finally:
                out.flush()
                # 只在仍是我们替换的那两个流时还原：App 退出后 Textual 会把 sys.stdout
                # 换回真实流，此时再写回旧的捕获对象，会把之后的输出永久丢进死流。
                if sys.stdout is out:
                    sys.stdout = old_out
                if sys.stderr is out:
                    sys.stderr = old_err
                if self._capture_stream is out:
                    self._capture_stream = None

        # ── 输出线程：队列 → RichLog ──
        def _drain_loop(self):
            """把 _out_q 的条目**批量**投递到主线程（逐行投递会产生大量跨线程跳转）。

            队列里混装两种条目：str（普通文本输出）与 Rich renderable（结构化面板）。
            统一走队列（而不是各自 call_from_thread）是为了让 worker 永不阻塞等待 UI。
            """
            while not self._stop.is_set():
                try:
                    first = self._out_q.get(timeout=0.1)
                except queue.Empty:
                    continue
                batch = [first]
                while len(batch) < _FLUSH_MAX_ITEMS:   # 取空积压（分片，见 _FLUSH_MAX_ITEMS）
                    try:
                        batch.append(self._out_q.get_nowait())
                    except queue.Empty:
                        break
                try:
                    self.call_from_thread(self._flush_batch, batch)
                except Exception:
                    pass

        def _enqueue_rich(self, renderable) -> None:
            """线程安全：把 Rich renderable 投进输出队列（满时短暂等待，不死等）。"""
            while True:
                try:
                    self._out_q.put(renderable, timeout=0.2)
                    return
                except queue.Full:
                    if self._stop.is_set():
                        return

        def _flush_batch(self, batch):
            """主线程：按原顺序落盘（连续文本合并成一次 write，renderable 单独写）。

            分派规则就是类型本身：str = 普通输出文本；其余 = Rich renderable。
            （保持 _QueueStream 的「队列里是裸字符串」契约，避免破坏既有调用方与测试。）
            """
            buf = []
            for item in batch:
                if isinstance(item, str):
                    buf.append(item)
                    continue
                if buf:
                    self._log(buf)
                    buf = []
                self._log_renderable(item)
            if buf:
                self._log(buf)

        @staticmethod
        def _dim_to_grey(t) -> None:
            """把「只有 dim、没有颜色」的片段显式改成灰色。

            Textual 下 dim 与正常字重几乎看不出差别 → 用户观感是「整屏都是白的」。
            显式换成 grey54 后，辅助信息与正文才分得开。
            """
            try:
                from rich.style import Style as _Style
                from rich.text import Span as _Span
                spans = []
                for sp in t.spans:
                    st = sp.style
                    if isinstance(st, str):
                        if "dim" in st and "color" not in st:
                            st = st.replace("dim", "grey54")
                    elif getattr(st, "dim", False) and getattr(st, "color", None) is None:
                        # from_ansi 产出的是 Style 对象（不是字符串），要显式重建
                        st = _Style(
                            color="grey54",
                            bgcolor=getattr(st, "bgcolor", None),
                            bold=getattr(st, "bold", None),
                            italic=getattr(st, "italic", None),
                            underline=getattr(st, "underline", None),
                            strike=getattr(st, "strike", None),
                            reverse=getattr(st, "reverse", None),
                            blink=getattr(st, "blink", None),
                            conceal=getattr(st, "conceal", None),
                            dim=False,
                        )
                    spans.append(_Span(sp.start, sp.end, st))
                t.spans = spans
            except Exception:
                pass

        def _log(self, text):
            """写入日志区（支持单行 str 或批量 list）；保留 Rich 的 ANSI 颜色。

            - 批量合并成**一次** RichLog.write：逐行 write 每行约 1ms（实测 500 行 0.41s），
              合并后约 2.6x 快，避免大段输出卡住 UI 线程。
            - 无 ESC 的普通行不走 from_ansi；`dim` 显式转灰（见 _dim_to_grey）。
            """
            try:
                log = self.query_one("#log", RichLog)
            except Exception:
                return
            items = text if isinstance(text, (list, tuple)) else (text,)
            if not items:
                return
            from rich.text import Text as _RichText
            parts = []
            for it in items:
                try:
                    if it is None:
                        parts.append(_RichText(""))
                        continue
                    if isinstance(it, _RichText):
                        t = it          # 已是 Text（保留调用方给的颜色）
                    else:
                        s = str(it)
                        t = _RichText.from_ansi(s) if "\x1b" in s else _RichText(s)
                except Exception:
                    t = _RichText(str(it))
                self._dim_to_grey(t)
                parts.append(t)
            try:
                if len(parts) == 1:
                    log.write(parts[0])
                else:
                    log.write(_RichText("\n").join(parts))
            except Exception:
                pass

        def _log_renderable(self, renderable):
            """把 Rich renderable 直接写入日志区（保留 Markdown 颜色 / 面板边框）。"""
            try:
                self.query_one("#log", RichLog).write(renderable)
            except Exception:
                pass

        # ── 流式输出（生成期间在输出框内实时刷新，结束后并入主日志）──
        def _render_stream(self, text: str, kind: str = "reply") -> None:
            """把流式文本**增量追加**到 #stream（只写新增部分）。

            旧实现每 80ms `clear()` + 全文重渲染 + 全文 Markdown 重解析：单条长回复
            总成本 O(n²)（n = 输出字符数），而且每次都在主线程同步做完整渲染。
            改成增量追加后是 O(n)，与输出长度线性相关。

            kind="reply"  → 正文
            kind="reason" → 思考过程（灰字斜体，对齐 REPL 的「🤖 AI 思考中…」）
            """
            try:
                w = self.query_one("#stream", RichLog)
            except Exception:
                return
            text = text or ""
            # 需要重建的三种情况：类型切换 / 源端截断（文本变短）/ 前缀不一致
            if (kind != self._stream_kind or len(text) < len(self._stream_prev)
                    or not text.startswith(self._stream_prev)):
                try:
                    w.clear()
                except Exception:
                    pass
                self._stream_prev = ""
                self._stream_kind = kind
                try:
                    from bin.ai_lib.ui import onyx_label as _ol
                    _cn = self._lang != "english"
                    if kind == "reason":
                        w.write(_ol("reason", "思考中" if _cn else "Thinking"))
                    else:
                        w.write(_ol("ai", "回复" if _cn else "Reply"))
                except Exception:
                    pass
            delta = text[len(self._stream_prev):]
            self._stream_prev = text
            if not delta:
                return
            try:
                from rich.text import Text as _T
                if kind == "reason":
                    w.write(_T(delta, style="grey54 italic"))
                else:
                    w.write(_T(delta))
                w.display = True
            except Exception:
                pass

        def _end_stream(self) -> None:
            """结束流式：清空并隐藏 #stream（正式回复由 write_rich 落进主日志）。"""
            self._stream_prev = ""
            self._stream_kind = ""
            try:
                w = self.query_one("#stream", RichLog)
                w.clear()
                w.display = False
            except Exception:
                pass

    global _TUI_PLAN_SCREEN, _TUI_SELECT_SCREEN, _TUI_HIST_SEARCH_SCREEN
    global _TUI_CONFIRM_SCREEN, _TUI_CAPTCHA_SCREEN
    _TUI_PLAN_SCREEN = PlanConfirmScreen
    _TUI_SELECT_SCREEN = SelectScreen
    _TUI_HIST_SEARCH_SCREEN = HistorySearchScreen
    _TUI_CONFIRM_SCREEN = ConfirmScreen
    _TUI_CAPTCHA_SCREEN = CaptchaScreen
    _TUI_APP_CLASS = OnyxTUI
    return _TUI_APP_CLASS


def ai_tui_session(
    user_home_dir: str,
    onyx_module=None,
    global_config=None,
    user_info=None,
    user_mode=None,
    parse_and_execute=None,
    **kwargs,
) -> None:
    """AI TUI 会话入口。依赖缺失/启动失败时安全回退 REPL。"""
    from bin.ai_lib.tui_deps import ensure_tui_deps

    if not ensure_tui_deps():
        _mode.set_render_mode("repl")
        from bin.ai_interactive import ai_interactive_session
        ai_interactive_session(
            user_home_dir=user_home_dir, onyx_module=onyx_module, global_config=global_config,
            user_info=user_info, user_mode=user_mode, parse_and_execute=parse_and_execute, **kwargs,
        )
        return

    # ── 配置路径跟随运行时 home ──
    try:
        from bin.ai_lib.config import sync_home
        sync_home(user_home_dir)
    except Exception:
        pass

    # 界面语言以「语言文件」(~/.config/onyx/language，get_current_lang) 为准：


    current_lang = "chinese"
    try:
        from bin.ai_lib.config import get_current_lang as _get_current_lang
        current_lang = _get_current_lang() or "chinese"
    except Exception:
        if global_config:
            current_lang = (global_config.get("display_info", {})
                            .get("language", {}).get("current", "chinese"))

    try:
        from bin.ai_interactive import _auto_memory_mode
        _mem_mode = _auto_memory_mode(user_home_dir)
    except Exception:
        _mem_mode = "global"

    ctx = {
        "user_home_dir": user_home_dir,
        "lang": current_lang,
        "session_id": str(uuid.uuid4()),
        "mode": "normal",
        "quiet": False,
        "show_time": True,
        "session_start": time.time(),
        "memory_mode": _mem_mode,
        "cwd": os.getcwd(),
        "_key_changed": False,
        "_chat_changed": False,
    }

    _mode.set_render_mode("tui")
    _enable_alt_enter_keys()   # Alt+Enter → alt+enter（Textual 会丢掉 ESC+CR 的 alt）
    # 输入层加固：默认开启鼠标追踪（按钮 / 选项列表要能点），并在字节层装净化器——
    # X10 报文要么被剔除（关闭鼠标时）要么转写成 SGR（开启鼠标时），因此既点得中、
    # 也不会再出现严格 UTF-8 解码崩溃 / 字节漏进输入框。
    _mouse = _tui_mouse_enabled()
    _install_input_sanitizer(strip_mouse=not _mouse)
    # AI 按键注册表跟随运行时 home（/config → 按键设置 的读写都基于它）
    try:
        from bin.ai_lib import keymap as _keymap_mod
        _keymap_mod.init(user_home_dir)
    except Exception:
        pass
    try:
        AppClass = _build_tui()
        app = AppClass(
            dict(user_home_dir=user_home_dir, onyx_module=onyx_module, global_config=global_config,
                 user_info=user_info, user_mode=user_mode, parse_and_execute=parse_and_execute, **kwargs),
            ctx,
        )
        # 兼容不同 Textual 版本：不支持 mouse 参数时退回默认调用
        _run_kwargs = {}
        try:
            import inspect as _inspect
            if "mouse" in _inspect.signature(AppClass.run).parameters:
                _run_kwargs["mouse"] = _mouse
        except Exception:
            _run_kwargs["mouse"] = _mouse
        app.run(**_run_kwargs)
    except Exception as e:
        # 启动失败 → 回退 REPL（保证 AI 始终可用）
        _mode.set_render_mode("repl")
        try:
            from bin.ai_lib.ui import set_ui_adapter
            set_ui_adapter(None)
        except Exception:
            pass
        try:
            print(_t("tui_start_fail", current_lang, err=f"{type(e).__name__}: {e}"))
        except Exception:
            pass
        from bin.ai_interactive import ai_interactive_session
        ai_interactive_session(
            user_home_dir=user_home_dir, onyx_module=onyx_module, global_config=global_config,
            user_info=user_info, user_mode=user_mode, parse_and_execute=parse_and_execute, **kwargs,
        )
    finally:
        _mode.set_render_mode("repl")
        try:
            from bin.ai_lib.ui import set_ui_adapter
            set_ui_adapter(None)
        except Exception:
            pass
