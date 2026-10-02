#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""哨兵（命令完成标记）鲁棒性测试 —— 真实 PTY + 真实 PersistentShell。

覆盖（每条都是「旧实现会永久阻塞」或「旧实现误判」的场景）：
  1. 基础命令返回 + 输出正确
  2. 用户/AI 改写 PS1（`PS1='$ '`）后仍能返回
  3. PS1 清空
  4. 彩色 PS1
  5. PROMPT_COMMAND 被清（只剩 PS1 兜底哨兵）
  6. PROMPT_COMMAND + PS1 都被清（靠空闲自愈恢复）
  7. 嵌套子 shell（`sh -c '...'`）
  8. 命令里含哨兵串 → 自动重生成，不误判
  9. ONYX_CMD_TIMEOUT 硬超时生效（绝不永久阻塞）
 10. is_alive 对「僵尸进程 / PID 复用」的判定（单元）

运行：python3 test/test_sentinel_robustness.py
"""
import os
import sys
import time
import threading
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.terminal import exe   # noqa: E402

# ── 把终端 I/O 接到内存，避免污染测试输出 ──
_ORIG_OUT = sys.__stdout__
_ORIG_IN = sys.stdin


class _FakeBuf:
    def __init__(self):
        self.data = bytearray()

    def write(self, b):
        self.data.extend(b)

    def flush(self):
        pass


class _FakeStdout:
    def __init__(self):
        self.buffer = _FakeBuf()


class _StdinShim:
    def __init__(self, fd):
        self._fd = fd

    def fileno(self):
        return self._fd


_FAKE_OUT = _FakeStdout()
# 管道读端：无数据 → select 永不就绪（不会忙循环）
_PIPE_R, _PIPE_W = os.pipe()

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}  {detail}")
        FAILS.append(name)


def run(shell, cmd, timeout=15.0):
    """在工作线程里执行（防挂死）。返回 (rc, output)。超时返回 ('TIMEOUT', 已收集输出)。"""
    buf = []
    res = {}

    def _job():
        try:
            res["rc"], _ = shell.execute(cmd, buf)
        except Exception as e:                      # noqa: BLE001
            res["rc"] = -99
            buf.append(f"[exc] {type(e).__name__}: {e}")

    t = threading.Thread(target=_job, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return "TIMEOUT", "".join(buf)
    return res.get("rc"), "".join(buf)


def unit_is_alive():
    print("[10] is_alive：僵尸 / PID 复用")
    s = exe.PersistentShell.__new__(exe.PersistentShell)
    s._dead = False
    s.pid = os.getpid()
    s._start_ticks = 0
    check("活进程 → True", s.is_alive() is True)

    s.pid = 999999
    s._dead = False
    check("不存在的 PID → False", s.is_alive() is False)

    # 僵尸：子进程退出但不 wait → /proc/<pid> 仍在，旧实现会误判为存活
    z = subprocess.Popen(["true"])
    time.sleep(0.3)
    s.pid = z.pid
    s._dead = False
    _st = s._read_proc_stat()
    s._start_ticks = _st[1] if _st else 0
    check("僵尸进程 → False", s.is_alive() is False,
          f"stat={_st}")
    z.wait()

    # PID 复用：starttime 与记录不符
    s.pid = os.getpid()
    s._dead = False
    s._start_ticks = 123456789
    check("starttime 不符（PID 复用）→ False", s.is_alive() is False)


def main():
    print("=" * 68)
    print("哨兵鲁棒性测试（真实 PTY）")
    print("=" * 68)

    # 单元部分不依赖 PTY，先跑
    unit_is_alive()

    sys.__stdout__ = _FAKE_OUT
    sys.stdin = _StdinShim(_PIPE_R)
    exe.DEBUG_ENABLED = False

    shell = None
    try:
        shell = exe.PersistentShell(cwd=ROOT)
        check("shell 启动", shell.is_alive(), f"shell={shell.shell}")

        print("[1] 基础命令")
        rc, out = run(shell, "echo HELLO_SENTINEL")
        check("rc==0", rc == 0, f"rc={rc}")
        check("输出含 HELLO_SENTINEL", "HELLO_SENTINEL" in out, repr(out[-120:]))

        print("[2] 改写 PS1 后仍能返回（旧实现永久阻塞）")
        run(shell, "PS1='$ '")
        rc, out = run(shell, "echo AFTER_PS1_CHANGE")
        check("rc==0（未挂死）", rc == 0, f"rc={rc}")
        check("输出含 AFTER_PS1_CHANGE", "AFTER_PS1_CHANGE" in out, repr(out[-120:]))

        print("[3] PS1 清空")
        run(shell, "PS1=''")
        rc, out = run(shell, "echo AFTER_EMPTY_PS1")
        check("rc==0", rc == 0, f"rc={rc}")
        check("输出含 AFTER_EMPTY_PS1", "AFTER_EMPTY_PS1" in out, repr(out[-120:]))

        print("[4] 彩色 PS1")
        run(shell, "PS1='\\[\\033[32m\\]\\w\\[\\033[0m\\]$ '")
        rc, out = run(shell, "echo AFTER_COLOR_PS1")
        check("rc==0", rc == 0, f"rc={rc}")
        check("输出含 AFTER_COLOR_PS1", "AFTER_COLOR_PS1" in out, repr(out[-120:]))

        print("[5] 清掉 PROMPT_COMMAND（只剩 PS1 兜底哨兵）")
        run(shell, "PROMPT_COMMAND=''")
        rc, out = run(shell, "echo AFTER_PC_CLEARED")
        check("rc==0", rc == 0, f"rc={rc}")
        check("输出含 AFTER_PC_CLEARED", "AFTER_PC_CLEARED" in out, repr(out[-120:]))

        print("[6] PROMPT_COMMAND + PS1 全清（靠空闲自愈恢复）")
        run(shell, "PROMPT_COMMAND=''; PS1=''")
        t0 = time.time()
        rc, out = run(shell, "echo AFTER_BOTH_CLEARED", timeout=20.0)
        dt = time.time() - t0
        check("rc==0（自愈成功，未挂死）", rc == 0, f"rc={rc} 用时{dt:.1f}s")
        check("输出含 AFTER_BOTH_CLEARED", "AFTER_BOTH_CLEARED" in out, repr(out[-120:]))

        print("[7] 嵌套子 shell")
        rc, out = run(shell, "sh -c 'echo FROM_SUBSHELL'")
        check("rc==0", rc == 0, f"rc={rc}")
        check("输出含 FROM_SUBSHELL", "FROM_SUBSHELL" in out, repr(out[-120:]))

        print("[8] 命令含哨兵串 → 自动重生成，不误判")
        marker = shell._done_marker
        old = marker
        rc, out = run(shell, f"echo {marker}")
        check("rc==0", rc == 0, f"rc={rc}")
        check("哨兵已重生成", shell._done_marker != old,
              f"old={old} new={shell._done_marker}")
        # 重生成后仍能正常执行下一条
        rc, out = run(shell, "echo AFTER_MARKER_REGEN")
        check("重生成后仍能返回", rc == 0, f"rc={rc}")

        print("[9] ONYX_CMD_TIMEOUT 硬超时（绝不永久阻塞）")
        os.environ["ONYX_CMD_TIMEOUT"] = "4"
        t0 = time.time()
        rc, out = run(shell, "sleep 30", timeout=20.0)
        dt = time.time() - t0
        os.environ.pop("ONYX_CMD_TIMEOUT", None)
        check("在 4~12s 内中止（未挂死）", rc != "TIMEOUT" and dt < 12.0,
              f"rc={rc} 用时{dt:.1f}s")
        # 收拾 sleep 残留：Ctrl+C 后重建
        try:
            os.killpg(shell.pid, 9)
        except Exception:
            pass
        try:
            shell.cleanup()
        except Exception:
            pass
        shell = None

    finally:
        sys.__stdout__ = _ORIG_OUT
        sys.stdin = _ORIG_IN
        if shell is not None:
            try:
                shell.cleanup()
            except Exception:
                pass

    print()
    if FAILS:
        print(f"❌ FAILED: {len(FAILS)} 项 -> {FAILS}")
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
