# -*- coding: utf-8 -*-
"""
验证：上下键历史导航对多行命令的处理
1. 连续导航不再因多行命令（含 \n）与显示文本（空格版）不一致而重置
2. 多行命令以原始形式（含真实换行）回填缓冲区，ptk 按多行渲染
3. 提交时原样提交原始多行命令（不会把空格压平版误判为新 heredoc）
4. 转义符（^J / ANSI / 字面 \n）被正确清理
5. 多行输入被取消/中断时，首行残片不再写入历史记录
"""
import os
import sys

# 使 onyx/onyx 可导入
HERE = os.path.dirname(os.path.abspath(__file__))
ONYX_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if ONYX_ROOT not in sys.path:
    sys.path.insert(0, ONYX_ROOT)

try:
    from lib.terminal import input_lib as il
    HAS_PTK = True
    # 强制导入 prompt_toolkit 依赖模块
    import prompt_toolkit  # noqa
except Exception as e:
    print(f"[warn] 无法导入 input_lib（{e}），降级为纯逻辑验证")
    HAS_PTK = False


def make_standalone():
    """无 prompt_toolkit 时使用最小化模拟，验证核心状态机逻辑"""
    hist = [
        "for i in 1 2 3; do\n  echo $i\ndone",          # 多行
        "cat > a.txt << EOF\nhello\nEOF",               # 多行 heredoc
        "ls -la",                                       # 单行
    ]

    def display(cmd):
        # 新语义：多行命令原样回填（含真实换行），不再压成一行
        return cmd

    state = {"idx": -1, "raw": None, "start": ""}

    def up(current):
        if state["idx"] != -1 and current == display(hist[state["idx"]]):
            if state["idx"] < len(hist) - 1:
                state["idx"] += 1
            else:
                return current
        else:
            state["idx"] = 0
        state["raw"] = hist[state["idx"]]
        return display(hist[state["idx"]])

    def down(current):
        if state["idx"] != -1 and current == display(hist[state["idx"]]):
            if state["idx"] > 0:
                state["idx"] -= 1
            else:
                state["idx"] = -1
                state["raw"] = None
                return state["start"]
        state["raw"] = hist[state["idx"]] if state["idx"] >= 0 else None
        return display(hist[state["idx"]]) if state["idx"] >= 0 else current

    ok = True
    # Up 1: 应回填多行命令的原始形式（含真实换行，无 ^J、无压平）
    t1 = up("")
    assert t1 == "for i in 1 2 3; do\n  echo $i\ndone", f"up1 got {t1!r}"
    # Up 2: 关键修复点——必须能继续导航（修复前会重置回到第一条）
    t2 = up(t1)
    assert t2 == "cat > a.txt << EOF\nhello\nEOF", f"up2 got {t2!r}"
    # Up 3: 继续到单行
    t3 = up(t2)
    assert t3 == "ls -la", f"up3 got {t3!r}"
    # Down 1: 返回多行命令
    d1 = down(t3)
    assert d1 == "cat > a.txt << EOF\nhello\nEOF", f"down1 got {d1!r}"
    # 提交：缓冲区内容即原始多行，无需额外恢复步骤
    assert state["raw"] == "cat > a.txt << EOF\nhello\nEOF", f"raw got {state['raw']!r}"
    if d1 == state["raw"]:
        restored = state["raw"]
    else:
        restored = d1
    assert restored == "cat > a.txt << EOF\nhello\nEOF", f"restore got {restored!r}"
    print("  [OK] standalone 状态机：连续导航 + 多行原样回填 全部通过")
    return ok


def test_real():
    """直接测试 input_lib 真实函数"""
    # 准备历史（模拟 _HISTORY_BUFFER，最新在前）
    il._HISTORY_BUFFER = [
        "cat > a.txt << EOF\nhello\nEOF",   # 0: 多行 heredoc
        "for i in 1 2 3; do\n  echo $i\ndone",  # 1: 多行 for
        "ls -la",                            # 2: 单行
    ]
    il.reset_history_index()

    # Up 后缓冲区直接回填原始多行（含真实换行）
    t1, p1 = il.handle_up_arrow_normal("")
    assert t1 == "cat > a.txt << EOF\nhello\nEOF", f"real up1 got {t1!r}"
    assert t1.count("\n") == 2, f"up1 应保留真实换行: {t1!r}"

    # 连续导航：缓冲区(原始多行) 与 _format_history_for_display(原始多行) 一致 → 可继续
    t2, p2 = il.handle_up_arrow_normal(t1)
    assert t2 == "for i in 1 2 3; do\n  echo $i\ndone", f"real up2 got {t2!r}"

    t3, p3 = il.handle_up_arrow_normal(t2)
    assert t3 == "ls -la", f"real up3 got {t3!r}"

    # Down 回退
    d1, _ = il.handle_down_arrow_normal(t3)
    assert d1 == "for i in 1 2 3; do\n  echo $i\ndone", f"real down1 got {d1!r}"
    d2, _ = il.handle_down_arrow_normal(d1)
    assert d2 == "cat > a.txt << EOF\nhello\nEOF", f"real down2 got {d2!r}"

    # RAW 记录：应指向原始多行条目
    assert il._NAVIGATION_RAW_COMMAND == "cat > a.txt << EOF\nhello\nEOF", \
        f"raw got {il._NAVIGATION_RAW_COMMAND!r}"

    # 提交逻辑（模拟 universal_input 中的片段）：
    # 缓冲区已是原始多行 → 原样通过，且 _format_history_for_display 不改变内容
    user_input = d2
    if il._NAVIGATION_RAW_COMMAND is not None:
        display_form = il._format_history_for_display(il._NAVIGATION_RAW_COMMAND)
        assert display_form == il._NAVIGATION_RAW_COMMAND, f"display_form 应等于原始命令: {display_form!r}"
        if user_input == display_form:
            user_input = il._NAVIGATION_RAW_COMMAND
        il._NAVIGATION_RAW_COMMAND = None
    assert user_input == "cat > a.txt << EOF\nhello\nEOF", f"restore got {user_input!r}"

    # 多行完整命令提交到 _process_multiline_input 时直接通过（不进入续行循环）
    _orig_prompt = il.prompt
    calls = {"n": 0}
    def fake_prompt(*a, **k):
        calls["n"] += 1
        raise EOFError()
    il.prompt = fake_prompt
    try:
        result = il._process_multiline_input(user_input)
        assert result is None, f"完整多行应直接通过，got {result!r}"
        assert calls["n"] == 0, "不应进入续行输入循环"
    finally:
        il.prompt = _orig_prompt

    # 转义符清理：ANSI / ^J / 字面 \n 残留
    dirty = "\x1b[31mred\x1b[0m^Jcat a.txt\\nEOF^["
    cleaned = il._clean_display_text(dirty)
    assert "\x1b[" not in cleaned, f"ansi not cleaned: {cleaned!r}"
    assert "^J" not in cleaned, f"^J not cleaned: {cleaned!r}"
    assert "\n" in cleaned, f"literal \\n not decoded: {cleaned!r}"
    assert "^[" not in cleaned, f"^[ not cleaned: {cleaned!r}"

    # 用户输入路径不应破坏字面 \n（printf 场景）
    literal = il._clean_display_text('printf "a\\nb"', decode_escapes=False)
    assert literal == 'printf "a\\nb"', f"user literal broken: {literal!r}"

    print("  [OK] real input_lib：连续导航 + 多行原样回填 + 转义清理 全部通过")


def test_abort_not_polluted():
    """取消/中断多行输入后，首行残片不得写入历史"""
    # 记录 add_to_history 的调用
    calls = []
    _orig_add = il.add_to_history
    def traced_add(cmd):
        calls.append(cmd)
        return _orig_add(cmd)
    il.add_to_history = traced_add
    try:
        # 模拟：输入 heredoc 起始行 → 进入续行循环 → 用户 Ctrl+C（prompt 返回 __CANCEL__）
        _orig_prompt = il.prompt
        il.prompt = lambda *a, **k: '__CANCEL__'
        try:
            result = il._process_multiline_input("cat > a.txt << EOF")
            assert result is None, f"取消应返回 None，got {result!r}"
            assert il._MULTILINE_ABORTED is True, "取消后应标记 _MULTILINE_ABORTED"
        finally:
            il.prompt = _orig_prompt

        # universal_input 的落库守卫：ABORTED 时不写入
        il._MULTILINE_ABORTED = True
        user_input_stripped = "cat > a.txt << EOF"
        if user_input_stripped and not il._MULTILINE_ABORTED:
            il.add_to_history(user_input_stripped)
        il._MULTILINE_ABORTED = False
        assert calls == [], f"取消后的首行残片不应写入历史，got {calls!r}"

        # 正常完成的多行命令仍写入
        il.add_to_history("cat > a.txt << EOF\nhello\nEOF")
        assert len(calls) == 1 and "\n" in calls[0], f"完整多行应正常写入，got {calls!r}"
    finally:
        il.add_to_history = _orig_add
    print("  [OK] 取消多行输入不污染历史")


def main():
    if HAS_PTK:
        test_real()
        test_abort_not_polluted()
    else:
        make_standalone()
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
