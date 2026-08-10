"""TeamRegistry — 团队管理"""

from __future__ import annotations
import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional


class TeamStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    DELETED = "deleted"


@dataclass
class Team:
    team_id: str
    name: str
    task_ids: list[str]
    status: TeamStatus = TeamStatus.CREATED
    created_at: float = 0.0
    updated_at: float = 0.0


def _now() -> float:
    return time.time()


class TeamRegistry:
    """线程安全的团队注册表 — 团队创建/查询/删除 + JSON 持久化"""

    def __init__(self, storage_dir: str):
        self._lock = threading.Lock()
        self._teams: dict[str, Team] = {}
        self._counter = 0
        self._storage_dir = storage_dir
        self._load()

    def _storage_path(self) -> str:
        return os.path.join(self._storage_dir, "teams.json")

    def _save(self):
        os.makedirs(self._storage_dir, exist_ok=True)
        data = []
        for t in self._teams.values():
            td = asdict(t)
            td["status"] = t.status.value
            data.append(td)
        with open(self._storage_path(), "w", encoding="utf-8") as f:
            json.dump({"teams": data, "counter": self._counter}, f, ensure_ascii=False, indent=2)

    def _load(self):
        path = self._storage_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._counter = data.get("counter", 0)
            for td in data.get("teams", []):
                td["status"] = TeamStatus(td["status"])
                self._teams[td["team_id"]] = Team(**td)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    def create(self, name: str, task_ids: Optional[list[str]] = None) -> Team:
        with self._lock:
            self._counter += 1
            ts = _now()
            team_id = f"team_{int(ts):08x}_{self._counter}"
            team = Team(
                team_id=team_id,
                name=name,
                task_ids=task_ids or [],
                status=TeamStatus.CREATED,
                created_at=ts,
                updated_at=ts,
            )
            self._teams[team_id] = team
            self._save()
            return team

    def get(self, team_id: str) -> Optional[Team]:
        with self._lock:
            return self._teams.get(team_id)

    def list(self) -> list[Team]:
        with self._lock:
            return list(self._teams.values())

    def delete(self, team_id: str) -> Team:
        with self._lock:
            team = self._teams.get(team_id)
            if not team:
                raise KeyError(f"team not found: {team_id}")
            team.status = TeamStatus.DELETED
            team.updated_at = _now()
            self._save()
            return team

    def remove(self, team_id: str) -> Optional[Team]:
        with self._lock:
            team = self._teams.pop(team_id, None)
            if team:
                self._save()
            return team

    def add_task(self, team_id: str, task_id: str):
        with self._lock:
            team = self._teams.get(team_id)
            if not team:
                raise KeyError(f"team not found: {team_id}")
            if task_id not in team.task_ids:
                team.task_ids.append(task_id)
                team.updated_at = _now()
                self._save()

    def len(self) -> int:
        with self._lock:
            return len(self._teams)

    def is_empty(self) -> bool:
        return self.len() == 0
