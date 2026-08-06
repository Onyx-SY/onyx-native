#!/usr/bin/env python3
"""离线验证 ExploreManager.drain_done：sync 任务总结已直接返回后，
从完成队列移除，避免 handle_ai 下一轮收集器重复注入。

运行: python3 test/virtual/test_subagent_drain.py
"""
import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bin.ai_lib.subagent import ExploreManager, ExploreTask  # noqa: E402


def _mk(status: str):
    t = ExploreTask("p", name="t")
    t.status = status
    if status == "done":
        t.summary = "ok"
    return t


def _queue_ids(m: ExploreManager):
    out = []
    while True:
        try:
            out.append(m._done_queue.get_nowait())
        except queue.Empty:
            break
    return out


def test_drain_removes_only_done_sync():
    m = ExploreManager()
    done1, done2, async_done, running = _mk("done"), _mk("done"), _mk("done"), _mk("running")
    with m._lock:
        for t in (done1, done2, async_done, running):
            m._tasks[t.id] = t
            m._done_queue.put(t.id)
    # sync 模式排空自己等到的两个
    m.drain_done([done1, done2])
    collected = m.collect_done()
    got = {t.id for t in collected}
    # 队列中剩下的应是 async_done（未排空）+ running（未完成不移除）
    assert got == {async_done.id, running.id}, f"期望剩 async+running, 实际 {got}"
    print("PASS drain removes only the done sync tasks (async+running kept)")


def test_drain_keeps_running_task():
    m = ExploreManager()
    running = _mk("running")
    with m._lock:
        m._tasks[running.id] = running
        m._done_queue.put(running.id)
    m.drain_done([running])  # 超时仍在运行的 sync 任务不应被移除
    collected = m.collect_done()
    assert [t.id for t in collected] == [running.id]
    print("PASS drain does not remove a still-running task")


def test_drain_empty_is_noop():
    m = ExploreManager()
    t = _mk("done")
    with m._lock:
        m._tasks[t.id] = t
        m._done_queue.put(t.id)
    m.drain_done([])
    assert len(m.collect_done()) == 1
    print("PASS drain with empty list is a no-op")


def test_drain_is_thread_safe():
    """排空期间并发 put 不丢任务。"""
    m = ExploreManager()
    done = _mk("done")
    with m._lock:
        m._tasks[done.id] = done
        m._done_queue.put(done.id)

    def _putter():
        for i in range(50):
            t = _mk("done")
            with m._lock:
                m._tasks[t.id] = t
                m._done_queue.put(t.id)
            time.sleep(0.001)

    th = threading.Thread(target=_putter, daemon=True)
    th.start()
    m.drain_done([done])
    th.join()
    collected = m.collect_done()
    assert len(collected) == 50, f"并发 put 的任务应全部保留, 实际 {len(collected)}"
    print("PASS drain is safe under concurrent puts")


if __name__ == "__main__":
    test_drain_removes_only_done_sync()
    test_drain_keeps_running_task()
    test_drain_empty_is_noop()
    test_drain_is_thread_safe()
    print("\nALL PASS")
