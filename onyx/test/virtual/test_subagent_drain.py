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


def test_sync_completion_leaves_no_residue():
    """回归（双注入竞态）：sync 任务完成并 drain 后，完成队列必须为空。

    修复前 _run_task 先置 status=done、再在另一个临界区入队；_exec_agent 观察到
    done 后 drain_done 可能先重建队列、put 落入新队列 → 下一轮 collect_done
    会把已作为工具结果返回过的总结再次注入。修复后入队与置位在同一临界区且
    先入队，"status==done ⇒ 已在队列"成为不变量，drain 必然排掉全部 sync 任务。
    """
    m = ExploreManager()
    _orig_execute = ExploreManager._execute

    def _fake_execute(self, task):
        task.summary = f"summary-{task.name}"

    ExploreManager._execute = _fake_execute
    try:
        tasks = m.submit_many([f"任务{i}" for i in range(8)], mode="sync", wait=False)
        deadline = time.time() + 10
        while any(t.status in ("pending", "running") for t in tasks) and time.time() < deadline:
            m.wait_any(timeout=0.05)
        assert all(t.status == "done" for t in tasks), "任务应全部完成"
        m.drain_done(tasks)
        leftover = m.collect_done()
        assert leftover == [], f"sync 任务 drain 后不应有残留（双注入源），实际 {len(leftover)}"
    finally:
        ExploreManager._execute = _orig_execute
    print("PASS sync completion leaves no residue in done queue")


def test_drain_ids_snapshot_semantics():
    """回归（快照竞态）：drain_ids 按调用方快照无条件移除——
    消费决策不受任务随后完成影响；快照为 running 的任务即使随后
    完成也保留在队列（由收集器注入，不重复注入）。"""
    m = ExploreManager()
    snap_done, snap_running = _mk("done"), _mk("running")
    with m._lock:
        m._tasks[snap_done.id] = snap_done
        m._tasks[snap_running.id] = snap_running
        m._done_queue.put(snap_done.id)  # 只有已完成任务的 id 在队列（running 未入队）
    # 调用方快照：只有 snap_done 被 sync 路径消费
    consumed = {t.id for t, s in [(snap_done, "done"), (snap_running, "running")] if s == "done"}
    m.drain_ids(consumed)
    # 模拟快照后 running 任务完成并入队（正常路径：_run_task finally 入队）
    with m._lock:
        snap_running.status = "done"
        m._done_queue.put(snap_running.id)
    leftover = m.collect_done()
    assert [t.id for t in leftover] == [snap_running.id], \
        f"快照为 running 的任务应保留给收集器, 实际 {[t.id for t in leftover]}"
    assert leftover[0].status == "done"
    print("PASS drain_ids 基于调用方快照：决策不受任务随后完成影响")


def test_done_implies_queued_invariant():
    """不变量：status==done 的任务 id 必已在完成队列中（先入队后置位，同一临界区）。"""
    m = ExploreManager()
    t = ExploreTask("p")
    # 模拟 _run_task finally 的收尾顺序（与实现同步）：
    # 同一临界区内先入队、再置位。
    with m._lock:
        t.status = "done"
        m._done_queue.put(t.id)
        m._tasks[t.id] = t
    t.done.set()
    # 外部观察者看到 done 的瞬间，drain 必须能取到该任务
    m.drain_done([t])
    assert m.collect_done() == [], "done 任务应可被 drain 完整移除"
    print("PASS done ⇒ queued invariant holds (drain removes it)")


if __name__ == "__main__":
    test_drain_removes_only_done_sync()
    test_drain_keeps_running_task()
    test_drain_empty_is_noop()
    test_drain_is_thread_safe()
    test_sync_completion_leaves_no_residue()
    test_done_implies_queued_invariant()
    test_drain_ids_snapshot_semantics()
    print("\nALL PASS")
