#!/usr/bin/env python3
"""输入层净化回归（防 UnicodeDecodeError 崩溃 / 防转义序列泄漏）。

背景：Termux 触摸滑动会发 legacy X10 鼠标报文 `ESC [ M b x y`，坐标字节可 >= 0x80
（如 0xBC）→ Textual LinuxDriver 的严格增量 UTF-8 解码器抛 UnicodeDecodeError，
输入线程死亡；同一批字节还会被拆成 ESC + 可打印字符灌进输入框。

运行: python3 test/virtual/test_tui_input_sanitize.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from bin.ai_tui import (  # noqa: E402
    _InputSanitizer, _SanitizingDecoder, _install_input_sanitizer, _tui_mouse_enabled,
)

# 真实崩溃样本：ESC [ M + 按钮 + 坐标（0xBC = 32 + 156，宽终端滑动时常见）
X10_MOUSE = b"\x1b[M\x20\xbc\x41"
SGR_MOUSE = b"\x1b[<64;120;40M"


def test_x10_mouse_stripped():
    san = _InputSanitizer(strip_mouse=True)
    out = san.feed(X10_MOUSE)
    assert out == b"", f"X10 鼠标报文应被剔除，实际 {out!r}"
    print("PASS X10 鼠标报文（含 0xBC 坐标字节）被剔除")


def test_x10_mouse_split_across_reads():
    san = _InputSanitizer(strip_mouse=True)
    assert san.feed(X10_MOUSE[:3]) == b""
    assert san.feed(X10_MOUSE[3:]) == b""
    print("PASS 被 read 切开的 X10 报文同样被剔除")


def test_sgr_mouse_stripped():
    san = _InputSanitizer(strip_mouse=True)
    assert san.feed(SGR_MOUSE) == b""
    assert san.feed(b"ab" + SGR_MOUSE + b"cd") == b"abcd"
    print("PASS SGR 鼠标报文被剔除且不影响相邻字符")


def test_mouse_kept_when_disabled():
    san = _InputSanitizer(strip_mouse=False)
    assert san.feed(SGR_MOUSE) == SGR_MOUSE, "关闭剔除时鼠标报文应原样透传"
    print("PASS 显式关闭鼠标剔除时原样透传")


def test_valid_utf8_and_controls_pass():
    san = _InputSanitizer()
    payload = "中文测试\r\n".encode("utf-8") + b"\x1b[A\x7f\t"
    # CRLF 会被归一化成单个 CR（Textual 里 LF=ctrl+j 在输入框无绑定 → Enter 失灵/多插换行）
    expect = "中文测试\r".encode("utf-8") + b"\x1b[A\x7f\t"
    assert san.feed(payload) == expect, "合法 UTF-8 / 方向键 / 退格 原样通过，CRLF 归一化为 CR"
    print("PASS 中文、方向键、退格、Tab 原样通过（CRLF 归一化为 CR）")


def test_split_multibyte_char():
    san = _InputSanitizer()
    raw = "中".encode("utf-8")
    assert san.feed(raw[:1]) == b"", "半截多字节字符应先缓存"
    assert san.feed(raw[1:]) == raw, "下一批到达后应拼成完整字符"
    print("PASS 跨批次的半截多字节字符被正确拼接")


def test_invalid_bytes_dropped():
    san = _InputSanitizer()
    # 0xBC 孤立出现（不在鼠标报文里）→ 丢弃，绝不产出 U+FFFD 灌进输入框
    assert san.feed(b"a\xbcb\xffc") == b"abc"
    print("PASS 孤立非法字节被丢弃（不产生 U+FFFD）")


def test_decoder_never_raises():
    dec = _SanitizingDecoder()
    # 复刻崩溃现场：单批 4KB 里塞满 X10 报文与非法字节
    payload = (X10_MOUSE * 100) + b"\xbc\xbc\xbc" + "正常文本".encode("utf-8")
    got = dec.decode(payload, final=False)
    assert isinstance(got, str)
    assert got == "正常文本", f"只有正常文本应保留，实际 {got!r}"
    assert "\ufffd" not in got
    print("PASS 解码器对崩溃样本不抛异常且只保留正常文本")


def test_decoder_tolerant_fallback():
    # 直接喂非法字节（绕过净化器）也不能抛：容错解码兜底
    dec = _SanitizingDecoder()
    dec._san = None  # 模拟净化器内部异常
    try:
        dec.decode(b"\xbc")
    except Exception as e:  # pragma: no cover
        raise AssertionError(f"净化器失效时解码仍不应抛异常：{e!r}")
    print("PASS 净化器异常时解码器仍有兜底")


def test_install_patch_and_idempotent():
    ok = _install_input_sanitizer(strip_mouse=True)
    assert ok is True, "首次安装应成功"
    assert _install_input_sanitizer(strip_mouse=True) is False, "重复安装应直接返回 False"
    import textual.drivers.linux_driver as _ld
    decode = _ld.getincrementaldecoder("utf-8")().decode
    got = decode(X10_MOUSE, final=False)
    assert got == "", f"挂载后驱动解码器应剔除鼠标报文，实际 {got!r}"
    print("PASS 净化器已挂载到 LinuxDriver 且可重复调用（幂等）")


def test_mouse_env_switch():
    old = os.environ.get("ONYX_TUI_MOUSE")
    try:
        os.environ.pop("ONYX_TUI_MOUSE", None)
        assert _tui_mouse_enabled() is False, "默认必须关闭鼠标追踪（Termux 安全默认）"
        os.environ["ONYX_TUI_MOUSE"] = "1"
        assert _tui_mouse_enabled() is True
        os.environ["ONYX_TUI_MOUSE"] = "off"
        assert _tui_mouse_enabled() is False
    finally:
        if old is None:
            os.environ.pop("ONYX_TUI_MOUSE", None)
        else:
            os.environ["ONYX_TUI_MOUSE"] = old
    print("PASS 鼠标追踪默认关闭、可由 ONYX_TUI_MOUSE 打开")


if __name__ == "__main__":
    test_x10_mouse_stripped()
    test_x10_mouse_split_across_reads()
    test_sgr_mouse_stripped()
    test_mouse_kept_when_disabled()
    test_valid_utf8_and_controls_pass()
    test_split_multibyte_char()
    test_invalid_bytes_dropped()
    test_decoder_never_raises()
    test_decoder_tolerant_fallback()
    test_install_patch_and_idempotent()
    test_mouse_env_switch()
    print("\nALL PASS")
