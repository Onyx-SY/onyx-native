# -*- coding: utf-8 -*-
"""step-3：TUI 三处加固（+1 处同区域兜底）。

1. `select_option` 模态失败/取消 → 返回 ""（取消），不再回退成 default（会被 /config
   当成"用户选了第一项"，静默触发平台切换等副作用）。
2. `_modal` 的 `ev.wait()` 改为轮询等待：App 退出（_stop 置位）立即返回，避免 worker
   永久阻塞在模态框上（线程泄漏 + _busy 卡 True）。
3. `_worker_loop` 异常分支里的 `call_from_thread` 再包一层 try/except：App 已退出时它会抛
   RuntimeError，异常从 except 逃逸会直接杀死 worker、队列永久堵死。
4. `_run_one` 还原 sys.stdout/stderr 前判断"是否仍是我们替换的那个流"：App 退出后
   Textual 会把 stdout 换回真实流，此时写回旧的捕获对象会把后续输出永久丢进死流。

用法：python3 tmp/patch_tui_harden.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bin", "ai_tui.py")

PAIRS = [
    # 1) _modal：退出感知的轮询等待
    (
        "        def _modal(self, screen, timeout=None, on_timeout=None):\n"
        "            ev = threading.Event()\n"
        "            box = {}\n"
        "\n"
        "            def _cb(res):\n"
        "                box[\"r\"] = res\n"
        "                ev.set()\n"
        "\n"
        "            try:\n"
        "                self.app.call_from_thread(self.app.push_screen, screen, _cb)\n"
        "            except Exception:\n"
        "                return None\n"
        "            if timeout is not None and timeout > 0:\n"
        "                if not ev.wait(timeout):\n"
        "                    try:\n"
        "                        self.app.call_from_thread(self.app.pop_screen)\n"
        "                    except Exception:\n"
        "                        pass\n"
        "                    return on_timeout\n"
        "                return box.get(\"r\")\n"
        "            ev.wait()\n"
        "            return box.get(\"r\")\n",
        "        def _wait(self, ev, timeout=None):\n"
        "            \"\"\"等待模态结果。\n"
        "\n"
        "            不用裸 ev.wait()：App 退出（_stop 置位）时 push_screen 的回调永远不会\n"
        "            触发，裸等会让 worker 线程永久阻塞（线程泄漏 + _busy 卡 True、队列堵死）。\n"
        "            这里轮询等待，App 一退出就返回 False。\n"
        "            \"\"\"\n"
        "            deadline = None if timeout is None else (time.monotonic() + timeout)\n"
        "            while True:\n"
        "                if ev.wait(0.2):\n"
        "                    return True\n"
        "                stop = getattr(self.app, \"_stop\", None)\n"
        "                if stop is not None and stop.is_set():\n"
        "                    return False\n"
        "                if deadline is not None and time.monotonic() >= deadline:\n"
        "                    return False\n"
        "\n"
        "        def _modal(self, screen, timeout=None, on_timeout=None):\n"
        "            ev = threading.Event()\n"
        "            box = {}\n"
        "\n"
        "            def _cb(res):\n"
        "                box[\"r\"] = res\n"
        "                ev.set()\n"
        "\n"
        "            try:\n"
        "                self.app.call_from_thread(self.app.push_screen, screen, _cb)\n"
        "            except Exception:\n"
        "                return None\n"
        "            if timeout is not None and timeout > 0:\n"
        "                if not self._wait(ev, timeout):\n"
        "                    try:\n"
        "                        self.app.call_from_thread(self.app.pop_screen)\n"
        "                    except Exception:\n"
        "                        pass\n"
        "                    return on_timeout\n"
        "                return box.get(\"r\")\n"
        "            if not self._wait(ev):\n"
        "                return None\n"
        "            return box.get(\"r\")\n",
    ),
    # 2) select_option：失败 → 取消（空串）
    (
        "        def select_option(self, message, options, default=\"\", lang=\"chinese\"):\n"
        "            r = self._modal(SelectScreen(message, options, default))\n"
        "            if r is None:\n"
        "                return default or (list(options)[0] if options else \"\")\n"
        "            return r\n",
        "        def select_option(self, message, options, default=\"\", lang=\"chinese\"):\n"
        "            r = self._modal(SelectScreen(message, options, default))\n"
        "            if r is None:\n"
        "                # 模态未完成（App 正在退出 / 推送失败）→ 返回空串表示「取消」。\n"
        "                # 不能回退成 default：调用方（/config 菜单）会把返回值当成用户选择，\n"
        "                # 从而静默执行「切换平台」这类有副作用的动作。\n"
        "                return \"\"\n"
        "            return r\n",
    ),
    # 3) worker 异常日志兜底
    (
        "                except Exception as e:\n"
        "                    self.call_from_thread(self._log, f\"⚠️ {type(e).__name__}: {e}\")\n"
        "                finally:\n"
        "                    self._busy = False\n",
        "                except Exception as e:\n"
        "                    # App 已退出时 call_from_thread 会抛 RuntimeError；必须再兜一层，\n"
        "                    # 否则异常从 except 逃逸 → worker 线程死亡、输入队列永久堵死。\n"
        "                    try:\n"
        "                        self.call_from_thread(self._log, f\"⚠️ {type(e).__name__}: {e}\")\n"
        "                    except Exception:\n"
        "                        pass\n"
        "                finally:\n"
        "                    self._busy = False\n",
    ),
    # 4) 还原 stdout/stderr 前确认仍是自己的流
    (
        "            finally:\n"
        "                out.flush()\n"
        "                sys.stdout, sys.stderr = old_out, old_err\n",
        "            finally:\n"
        "                out.flush()\n"
        "                # 只在仍是我们替换的那两个流时还原：App 退出后 Textual 会把 sys.stdout\n"
        "                # 换回真实流，此时再写回旧的捕获对象，会把之后的输出永久丢进死流。\n"
        "                if sys.stdout is out:\n"
        "                    sys.stdout = old_out\n"
        "                if sys.stderr is out:\n"
        "                    sys.stderr = old_err\n",
    ),
]


def main() -> int:
    with io.open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()
    for i, (old, new) in enumerate(PAIRS, 1):
        cnt = src.count(old)
        if cnt != 1:
            print(f"❌ 第 {i} 处：匹配 {cnt} 次（要求 1 次）")
            return 1
        src = src.replace(old, new)
    tmp = TARGET + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    os.replace(tmp, TARGET)
    print(f"✅ 已应用 {len(PAIRS)} 处加固 → {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
