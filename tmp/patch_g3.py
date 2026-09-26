# -*- coding: utf-8 -*-
"""G3：补回「信任窗口」第一级（可收紧），并同步文档。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "bin", "ai_lib", "helpers.py")

OLD1 = '''    # ── 第二级：300k ≤ 上下文 ≤ 600k → 弹窗询问，超时**默认拒绝**（fail-closed）──
    if context_tokens <= _CTX_TIMEOUT_BELOW:'''

NEW1 = '''    # ── 第一级：上下文 < 信任窗口 → 直接放行 ──
    # 这是**既有策略**（"用户恢复信任区间"：小上下文信任 AI，硬安全由沙盒边界提供），
    # 保留但可收紧：ONYX_DANGER_TRUST_TOKENS=0 即「任何上下文都要人工确认」。
    # 注意 context_tokens <= 0 表示估算失败 → 按最高档强制确认（安全方向），不走信任放行。
    try:
        _trust_below = int(os.environ.get("ONYX_DANGER_TRUST_TOKENS") or _CTX_TRUST_BELOW)
    except Exception:
        _trust_below = _CTX_TRUST_BELOW
    if context_tokens > 0 and context_tokens < _trust_below:
        if log_info:
            log_info(f"AI dangerous command trusted (ctx {context_tokens // 1000}k < "
                     f"{_trust_below // 1000}k): {cmd_str}", session_id)
        return True, "auto", ""

    # ── 第二级：信任窗口 ≤ 上下文 ≤ 600k → 弹窗询问，超时**默认拒绝**（fail-closed）──
    if context_tokens <= _CTX_TIMEOUT_BELOW:'''

OLD2 = '''      - 300k ≤ 上下文 ≤ 600k：弹窗询问，10 秒无操作默认放行（用户可能离开）'''
NEW2 = '''      - 300k ≤ 上下文 ≤ 600k：弹窗询问，超时**默认拒绝**（fail-closed；旧实现是超时自动放行）'''

OLD3 = '''_CTX_TRUST_BELOW = 300_000        # 上下文 < 300k：完全信任 AI，不弹任何危险提示'''
NEW3 = '''_CTX_TRUST_BELOW = 300_000        # 上下文 < 300k：信任 AI 不弹提示（可用 ONYX_DANGER_TRUST_TOKENS 收紧）'''


def main():
    text = open(P, encoding="utf-8").read()
    for old, new in ((OLD1, NEW1), (OLD2, NEW2), (OLD3, NEW3)):
        n = text.count(old)
        if n != 1:
            print(f"FAIL: 命中 {n} 次\n{old[:120]}")
            return 1
        text = text.replace(old, new, 1)
    open(P, "w", encoding="utf-8").write(text)
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
