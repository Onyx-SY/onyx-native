"""TaskRegistry — 6态任务状态机 + JSON 持久化 + LaneBoard"""

from __future__ import annotations
import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

from .task_packet import TaskPacket, validate_packet, packet_to_dict, dict_to_packet


class TaskStatus(str, Enum):
    """任务状态（6态）"""
    CREATED = "created"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"

    @classmethod
    def is_terminal(cls, status: str | TaskStatus) -> bool:
        return status in (cls.COMPLETED, cls.FAILED, cls.STOPPED)


class LaneFreshness(str, Enum):
    """心跳新鲜度"""
    HEALTHY = "healthy"
    STALLED = "stalled"
    TRANSPORT_DEAD = "transport_dead"
    UNKNOWN = "unknown"


@dataclass
class TaskMessage:
    role: str
    content: str
    timestamp: float


@dataclass
class LaneHeartbeat:
    observed_at: float
    transport_alive: bool
    status: str

    def freshness_at(self, now: float, stalled_after_secs: float) -> LaneFreshness:
        if not self.transport_alive:
            return LaneFreshness.TRANSPORT_DEAD
        if now - self.observed_at > stalled_after_secs:
            return LaneFreshness.STALLED
        return LaneFreshness.HEALTHY


@dataclass
class Task:
    task_id: str
    prompt: str
    description: Optional[str] = None
    task_packet: Optional[TaskPacket] = None
    status: TaskStatus = TaskStatus.CREATED
    created_at: float = 0.0
    updated_at: float = 0.0
    messages: list[TaskMessage] = field(default_factory=list)
    output: str = ""
    team_id: Optional[str] = None
    heartbeat: Optional[LaneHeartbeat] = None


@dataclass
class LaneBoardEntry:
    task_id: str
    prompt: str
    status: TaskStatus
    team_id: Optional[str]
    heartbeat: Optional[LaneHeartbeat]
    freshness: LaneFreshness


@dataclass
class LaneBoard:
    generated_at: float
    active: list[LaneBoardEntry]
    blocked: list[LaneBoardEntry]
    finished: list[LaneBoardEntry]


def _now() -> float:
    return time.time()


class TaskRegistry:
    """线程安全的任务注册表 — 管理任务生命周期 + JSON 持久化"""

    def __init__(self, storage_dir: str):
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._counter = 0
        self._storage_dir = storage_dir
        self._load()

    # ── 持久化 ──

    def _storage_path(self) -> str:
        return os.path.join(self._storage_dir, "tasks.json")

    def _save(self):
        os.makedirs(self._storage_dir, exist_ok=True)
        data = []
        for t in self._tasks.values():
            td = asdict(t)
            td["status"] = t.status.value
            td["messages"] = [asdict(m) for m in t.messages]
            if t.heartbeat:
                td["heartbeat"] = asdict(t.heartbeat)
            else:
                td["heartbeat"] = None
            if t.task_packet:
                td["task_packet"] = packet_to_dict(t.task_packet)
            else:
                td["task_packet"] = None
            data.append(td)
        with open(self._storage_path(), "w", encoding="utf-8") as f:
            json.dump({"tasks": data, "counter": self._counter}, f, ensure_ascii=False, indent=2)

    def _load(self):
        path = self._storage_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._counter = data.get("counter", 0)
            for td in data.get("tasks", []):
                td["status"] = TaskStatus(td["status"])
                td["messages"] = [TaskMessage(**m) for m in td.get("messages", [])]
                if td.get("heartbeat"):
                    td["heartbeat"] = LaneHeartbeat(**td["heartbeat"])
                if td.get("task_packet"):
                    td["task_packet"] = dict_to_packet(td["task_packet"])
                self._tasks[td["task_id"]] = Task(**td)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"[TaskRegistry] 加载数据失败: {e}")

    # ── CRUD ──

    def create(self, prompt: str, description: Optional[str] = None) -> Task:
        with self._lock:
            self._counter += 1
            ts = _now()
            task_id = f"task_{int(ts):08x}_{self._counter}"
            task = Task(
                task_id=task_id,
                prompt=prompt,
                description=description,
                status=TaskStatus.CREATED,
                created_at=ts,
                updated_at=ts,
            )
            self._tasks[task_id] = task
            self._save()
            return task

    def create_from_packet(self, packet: TaskPacket) -> Task:
        validate_packet(packet)  # 验证，失败抛异常
        description = packet.scope_path or packet.scope.value
        with self._lock:
            self._counter += 1
            ts = _now()
            task_id = f"task_{int(ts):08x}_{self._counter}"
            task = Task(
                task_id=task_id,
                prompt=packet.objective,
                description=description,
                task_packet=packet,
                status=TaskStatus.CREATED,
                created_at=ts,
                updated_at=ts,
            )
            self._tasks[task_id] = task
            self._save()
            return task

    def list(self, status_filter: Optional[str | TaskStatus] = None) -> list[Task]:
        if isinstance(status_filter, str):
            status_filter = TaskStatus(status_filter)
        with self._lock:
            return [
                t for t in self._tasks.values()
                if status_filter is None or t.status == status_filter
            ]

    def get(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(task_id)

    def update(self, task_id: str, message: str) -> Task:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(f"task not found: {task_id}")
            task.messages.append(TaskMessage(role="user", content=message, timestamp=_now()))
            task.updated_at = _now()
            self._save()
            return task

    def set_status(self, task_id: str, status: str | TaskStatus) -> Task:
        if isinstance(status, str):
            status = TaskStatus(status)
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(f"task not found: {task_id}")
            task.status = status
            task.updated_at = _now()
            self._save()
            return task

    def stop(self, task_id: str) -> Task:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(f"task not found: {task_id}")
            if TaskStatus.is_terminal(task.status):
                raise ValueError(
                    f"task {task_id} is already in terminal state: {task.status.value}"
                )
            task.status = TaskStatus.STOPPED
            task.updated_at = _now()
            self._save()
            return task

    def append_output(self, task_id: str, output: str):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(f"task not found: {task_id}")
            task.output += output
            task.updated_at = _now()
            self._save()

    def output(self, task_id: str) -> str:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(f"task not found: {task_id}")
            return task.output

    def assign_team(self, task_id: str, team_id: str):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(f"task not found: {task_id}")
            task.team_id = team_id
            task.updated_at = _now()
            self._save()

    def update_heartbeat(self, task_id: str, heartbeat: LaneHeartbeat):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(f"task not found: {task_id}")
            task.heartbeat = heartbeat
            task.updated_at = _now()
            self._save()

    def remove(self, task_id: str) -> Optional[Task]:
        with self._lock:
            task = self._tasks.pop(task_id, None)
            if task:
                self._save()
            return task

    # ── Lane Board ──

    def lane_board(self, stalled_after_secs: float = 300) -> LaneBoard:
        return self.lane_board_at(_now(), stalled_after_secs)

    def lane_board_at(self, now: float, stalled_after_secs: float) -> LaneBoard:
        with self._lock:
            board = LaneBoard(generated_at=now, active=[], blocked=[], finished=[])
            for task in self._tasks.values():
                freshness = LaneFreshness.UNKNOWN
                if task.heartbeat:
                    freshness = task.heartbeat.freshness_at(now, stalled_after_secs)
                entry = LaneBoardEntry(
                    task_id=task.task_id,
                    prompt=task.prompt,
                    status=task.status,
                    team_id=task.team_id,
                    heartbeat=task.heartbeat,
                    freshness=freshness,
                )
                if task.status in (TaskStatus.RUNNING, TaskStatus.CREATED):
                    board.active.append(entry)
                elif task.status == TaskStatus.BLOCKED:
                    board.blocked.append(entry)
                else:
                    board.finished.append(entry)
            return board

    def lane_board_json(self, stalled_after_secs: float = 300) -> dict:
        board = self.lane_board(stalled_after_secs)
        return self._board_to_dict(board)

    @staticmethod
    def _board_to_dict(board: LaneBoard) -> dict:
        def entry_dict(e: LaneBoardEntry) -> dict:
            return {
                "task_id": e.task_id,
                "prompt": e.prompt,
                "status": e.status.value,
                "team_id": e.team_id,
                "freshness": e.freshness.value,
            }
        return {
            "generated_at": board.generated_at,
            "active": [entry_dict(e) for e in board.active],
            "blocked": [entry_dict(e) for e in board.blocked],
            "finished": [entry_dict(e) for e in board.finished],
        }

    def len(self) -> int:
        with self._lock:
            return len(self._tasks)

    def is_empty(self) -> bool:
        return self.len() == 0

    # ── 统计摘要（给 TodoWrite 兼容用） ──

    def summary(self) -> dict:
        """返回 {total, pending, in_progress, completed, failed, blocked, stopped}"""
        with self._lock:
            total = len(self._tasks)
            counts = {s: 0 for s in TaskStatus}
            for t in self._tasks.values():
                counts[t.status] += 1
            return {
                "total": total,
                "created": counts[TaskStatus.CREATED],
                "running": counts[TaskStatus.RUNNING],
                "blocked": counts[TaskStatus.BLOCKED],
                "completed": counts[TaskStatus.COMPLETED],
                "failed": counts[TaskStatus.FAILED],
                "stopped": counts[TaskStatus.STOPPED],
            }
