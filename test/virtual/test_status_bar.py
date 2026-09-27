#!/usr/bin/env python3
"""离线验证状态栏：余额 TTL 缓存 + ai_cmd/ai_tui 接线。

覆盖：
  1. cost.get_cached_balance 同步/后台/TTL 语义（monkeypatch 掉真实网络请求）；
  2. ai_cmd 每轮推送状态（_push_status_bar + set_status 调用点）；
  3. ai_tui 状态栏控件与适配器 set_status / _render_status 接线。

运行: python3 test/virtual/test_status_bar.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from bin.ai_lib import cost  # noqa: E402


def test_cached_balance_sync_and_ttl():
    calls = []

    def fake(platform, api_key):
        calls.append(platform)
        return 12.34, "CNY", "success"

    orig = cost.get_balance
    cost.get_balance = fake
    cost._BALANCE_CACHE.clear()
    try:
        r1 = cost.get_cached_balance("deepseek", "k", ttl=600, background=False)
        assert r1 == (12.34, "CNY", "success"), r1
        r2 = cost.get_cached_balance("deepseek", "k", ttl=600, background=False)
        assert r2 == (12.34, "CNY", "success")
        assert len(calls) == 1, f"TTL 内应命中缓存，实际调用 {len(calls)} 次"
    finally:
        cost.get_balance = orig
        cost._BALANCE_CACHE.clear()
    print("PASS 同步查询 + TTL 缓存")


def test_cached_balance_background():
    def fake(platform, api_key):
        return 5.0, "USD", "success"

    orig = cost.get_balance
    cost.get_balance = fake
    cost._BALANCE_CACHE.clear()
    try:
        # 首次后台：返回 pending 占位
        b, cur, st = cost.get_cached_balance("openai", "k", ttl=600, background=True)
        assert st == "pending", f"首次后台应返回 pending，实际 {st}"
        # 等后台线程写入缓存
        for _ in range(60):
            if cost._BALANCE_CACHE.get("openai"):
                break
            time.sleep(0.05)
        b2, cur2, st2 = cost.get_cached_balance("openai", "k", ttl=600, background=True)
        assert (b2, cur2, st2) == (5.0, "USD", "success"), (b2, cur2, st2)
    finally:
        cost.get_balance = orig
        cost._BALANCE_CACHE.clear()
    print("PASS 后台刷新")


def test_cached_balance_force_and_on_update():
    """force=True 忽略 TTL 强制刷新；on_update 在刷新落地后回调。

    状态栏「余额实时更新」就靠这两个：每轮结束 force 刷一次，落地即回调再推 UI。
    """
    import threading

    calls = []

    def fake(platform, api_key):
        calls.append(platform)
        return 7.5, "CNY", "success"

    orig = cost.get_balance
    cost.get_balance = fake
    cost._BALANCE_CACHE.clear()
    try:
        # 先同步填一次缓存（TTL 很长 → 普通调用命中缓存，不再发请求）
        cost.get_cached_balance("deepseek", "k", ttl=600, background=False)
        cost.get_cached_balance("deepseek", "k", ttl=600, background=False)
        assert len(calls) == 1, f"TTL 内应命中缓存，实际请求 {len(calls)} 次"

        got = []
        done = threading.Event()

        def on_upd(r):
            got.append(r)
            done.set()

        cost.get_cached_balance("deepseek", "k", ttl=600, force=True, on_update=on_upd)
        assert done.wait(5.0), "on_update 应被回调"
        assert len(calls) == 2, f"force 应强制刷新，实际请求 {len(calls)} 次"
        assert got and got[0][0] == 7.5, got
    finally:
        cost.get_balance = orig
        cost._BALANCE_CACHE.clear()
    print("PASS force 强制刷新 + on_update 回调（余额实时更新）")


def test_ai_cmd_pushes_status():
    src = open(os.path.join(ROOT, "bin", "ai_cmd.py"), encoding="utf-8").read()
    assert "def _push_status_bar(" in src
    # 每轮结束强制刷新余额（否则要等下一轮 / 等 600s TTL 过期才更新）
    assert "_push_status_bar(_cache_supported, _force_balance=True)" in src
    assert "_force_balance: bool = False" in src
    assert "on_update=_on_upd" in src, "刷新落地后应回调再推一次状态栏"
    assert '"cache_pct"' in src and '"balance"' in src and '"cwd"' in src
    print("PASS ai_cmd 推送状态（含余额强制刷新 + 落地回调）")


def test_tui_status_wiring():
    src = open(os.path.join(ROOT, "bin", "ai_tui.py"), encoding="utf-8").read()
    for needle in ('id="status-bar"', "def set_status(self, status)",
                   "def _render_status(self, status)", "#status-bar {"):
        assert needle in src, f"ai_tui 缺少：{needle}"
    print("PASS TUI 状态栏接线")


def test_status_push_thread_safe():
    """回归：余额刷新的 on_update 跑在 cost.py 的后台线程，那里没有 thread-local。

    旧实现回调里只传 `_cache_supported` → 回推时 ctx=0 / cache_pct=None，
    把刚推上去的「ctx / cache」两段整段抹掉（用户看到「回复完闪一下，然后没了」）。
    """
    src = open(os.path.join(ROOT, "bin", "ai_cmd.py"), encoding="utf-8").read()
    assert "_ctx: Optional[int] = None" in src, "缺少显式 ctx 入参"
    assert "_cache_pct: Optional[float] = None" in src, "缺少显式 cache_pct 入参"
    assert "_c=_ctx" in src and "_p=_cache_pct" in src, "on_update 回调必须把 ctx/cache 闭包带过去"
    assert "if _ctx is None:" in src and "if _cache_pct is None:" in src, "显式值应优先于 thread-local"
    print("PASS 状态栏推送线程安全（后台回推不丢 ctx/cache）")


def test_status_bar_compact():
    """窄屏紧凑化：分隔符省 2 列、cache 整数百分比、长路径截尾。"""
    src = open(os.path.join(ROOT, "bin", "ai_tui.py"), encoding="utf-8").read()
    assert 'sep = " · "' in src, "分隔符应紧凑化"
    assert "cache_pct']:.0f}%" in src, "cache 应用整数百分比"
    assert "_cwd_cap" in src, "长路径应截尾"
    print("PASS 状态栏紧凑格式")


def test_background_balance_callback_thread():
    """on_update 确实在后台线程执行 —— 这正是必须显式传 ctx/cache 的原因。"""
    import threading

    main_tid = threading.get_ident()
    seen = {}
    done = threading.Event()

    def fake(platform, api_key):
        return 1.0, "CNY", "success"

    def on_upd(r):
        seen["tid"] = threading.get_ident()
        done.set()

    orig = cost.get_balance
    cost.get_balance = fake
    cost._BALANCE_CACHE.clear()
    try:
        cost.get_cached_balance("deepseek", "k", force=True, on_update=on_upd)
        assert done.wait(5.0), "on_update 应被回调"
        assert seen["tid"] != main_tid, "on_update 应在后台线程执行"
    finally:
        cost.get_balance = orig
        cost._BALANCE_CACHE.clear()
    print("PASS 余额回调在后台线程（thread-local 为空 → 必须显式传 ctx/cache）")


def _bar_text(bar) -> str:
    """取 Static 当前内容（Static 把内容存在 _Static__content）。"""
    r = getattr(bar, "_Static__content", None)
    return str(r) if r is not None else ""


def test_tui_balance_updates_and_keeps_last():
    """状态栏余额：新值立刻显示；刷新中/查询失败（None）时沿用上次成功值。"""
    import asyncio

    from bin.ai_tui import _build_tui

    App = _build_tui()
    app = App(session_kwargs={}, ctx={"lang": "chinese"})

    async def _run():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            bar = app.query_one("#status-bar")

            app._render_status({"cwd": "/tmp", "ctx": 1000, "balance": "12.34 CNY",
                                "balance_platform": "deepseek"})
            await pilot.pause()
            assert "12.34 CNY" in _bar_text(bar), _bar_text(bar)

            # 余额刷新中 / 查询失败 → 不能把已有余额抹掉
            app._render_status({"cwd": "/tmp", "ctx": 1000, "balance": None,
                                "balance_platform": "deepseek"})
            await pilot.pause()
            assert "12.34 CNY" in _bar_text(bar), f"应沿用上次成功值：{_bar_text(bar)}"

            # 新值落地 → 立刻更新（实时）
            app._render_status({"cwd": "/tmp", "ctx": 1000, "balance": "9.99 CNY",
                                "balance_platform": "deepseek"})
            await pilot.pause()
            txt = _bar_text(bar)
            assert "9.99 CNY" in txt and "12.34 CNY" not in txt, txt

    asyncio.run(_run())
    print("PASS 状态栏余额实时更新 + 失败时保留上次值")


if __name__ == "__main__":
    test_cached_balance_sync_and_ttl()
    test_cached_balance_background()
    test_cached_balance_force_and_on_update()
    test_ai_cmd_pushes_status()
    test_tui_status_wiring()
    test_status_push_thread_safe()
    test_status_bar_compact()
    test_background_balance_callback_thread()
    test_tui_balance_updates_and_keeps_last()
    print("\nALL PASS")
