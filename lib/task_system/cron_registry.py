"""CronRegistry — 定时任务注册表"""

from __future__ import annotations
import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class CronEntry:
    cron_id: str
    schedule: str
    prompt: str
    description: Optional[str] = None
    enabled: bool = True
    created_at: float = 0.0
    updated_at: float = 0.0
    last_run_at: Optional[float] = None
    run_count: int = 0


def _now() -> float:
    return time.time()


class CronRegistry:
    """线程安全的定时任务注册表 — 创建/禁用/记录执行 + JSON 持久化"""

    def __init__(self, storage_dir: str):
        self._lock = threading.Lock()
        self._entries: dict[str, CronEntry] = {}
        self._counter = 0
        self._storage_dir = storage_dir
        self._load()

    def _storage_path(self) -> str:
        return os.path.join(self._storage_dir, "cron.json")

    def _save(self):
        os.makedirs(self._storage_dir, exist_ok=True)
        data = []
        for e in self._entries.values():
            td = asdict(e)
            data.append(td)
        with open(self._storage_path(), "w", encoding="utf-8") as f:
            json.dump({"entries": data, "counter": self._counter}, f, ensure_ascii=False, indent=2)

    def _load(self):
        path = self._storage_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._counter = data.get("counter", 0)
            for ed in data.get("entries", []):
                self._entries[ed["cron_id"]] = CronEntry(**ed)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    def create(self, schedule: str, prompt: str,
               description: Optional[str] = None) -> CronEntry:
        with self._lock:
            self._counter += 1
            ts = _now()
            cron_id = f"cron_{int(ts):08x}_{self._counter}"
            entry = CronEntry(
                cron_id=cron_id,
                schedule=schedule,
                prompt=prompt,
                description=description,
                enabled=True,
                created_at=ts,
                updated_at=ts,
            )
            self._entries[cron_id] = entry
            self._save()
            return entry

    def get(self, cron_id: str) -> Optional[CronEntry]:
        with self._lock:
            return self._entries.get(cron_id)

    def list(self, enabled_only: bool = False) -> list[CronEntry]:
        with self._lock:
            return [
                e for e in self._entries.values()
                if not enabled_only or e.enabled
            ]

    def delete(self, cron_id: str) -> CronEntry:
        with self._lock:
            entry = self._entries.pop(cron_id, None)
            if not entry:
                raise KeyError(f"cron not found: {cron_id}")
            self._save()
            return entry

    def disable(self, cron_id: str):
        with self._lock:
            entry = self._entries.get(cron_id)
            if not entry:
                raise KeyError(f"cron not found: {cron_id}")
            entry.enabled = False
            entry.updated_at = _now()
            self._save()

    def record_run(self, cron_id: str):
        with self._lock:
            entry = self._entries.get(cron_id)
            if not entry:
                raise KeyError(f"cron not found: {cron_id}")
            entry.last_run_at = _now()
            entry.run_count += 1
            entry.updated_at = _now()
            self._save()

    def len(self) -> int:
        with self._lock:
            return len(self._entries)

    def is_empty(self) -> bool:
        return self.len() == 0
