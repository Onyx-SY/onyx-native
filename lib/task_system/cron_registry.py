"""CronRegistry — 定时任务注册表 + 后台调度器"""

from __future__ import annotations
import json
import logging
import os
import subprocess
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


# ── cron 表达式匹配（标准 5 段格式） ──

def _cron_field_matches(field: str, value: int, min_v: int, max_v: int) -> bool:
    """单字段匹配：* / N N-M N,M,O"""
    field = field.strip()
    # *
    if field == "*":
        return True
    # */N
    if field.startswith("*/"):
        step = int(field[2:])
        return step > 0 and value % step == 0
    # N-M
    if "-" in field:
        parts = field.split("-", 1)
        low, high = int(parts[0]), int(parts[1])
        return low <= value <= high
    # N,M,O
    if "," in field:
        return any(_cron_field_matches(p, value, min_v, max_v) for p in field.split(","))
    # N
    try:
        return int(field) == value
    except ValueError:
        return False


def _cron_matches(schedule: str, t: "time.struct_time" = None) -> bool:
    """检查当前（或指定）时间是否匹配 cron 表达式。"""
    if t is None:
        t = time.localtime()
    fields = schedule.strip().split()
    if len(fields) != 5:
        return False
    mappings = [
        (fields[0], t.tm_min, 0, 59),
        (fields[1], t.tm_hour, 0, 23),
        (fields[2], t.tm_mday, 1, 31),
        (fields[3], t.tm_mon, 1, 12),
        (fields[4], t.tm_wday, 0, 6),
    ]
    return all(_cron_field_matches(f, v, mn, mx) for f, v, mn, mx in mappings)


def _execute_cron_prompt(prompt: str, cron_id: str) -> None:
    """执行 cron 任务的 prompt。以 shell 命令形式运行，日志记录结果。"""
    try:
        result = subprocess.run(
            prompt, shell=True, capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            logging.info("Cron %s: OK — %s", cron_id, result.stdout.strip()[:200])
        else:
            logging.warning(
                "Cron %s: exit %d — %s", cron_id, result.returncode, result.stderr.strip()[:200]
            )
    except subprocess.TimeoutExpired:
        logging.error("Cron %s: 执行超时（120s）", cron_id)
    except Exception as e:
        logging.error("Cron %s: 执行异常 — %s", cron_id, e)


# ── 默认日志配置（应用层可覆盖） ──
if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [CRON] %(message)s")


class CronRegistry:
    """线程安全的定时任务注册表 — 创建/禁用/记录执行 + 后台调度器"""

    def __init__(self, storage_dir: str, auto_start: bool = True):
        self._lock = threading.Lock()
        self._entries: dict[str, CronEntry] = {}
        self._counter = 0
        self._storage_dir = storage_dir
        self._stop_event = threading.Event()
        self._scheduler: Optional[threading.Thread] = None
        self._last_trigger_minute: int = -1  # 避免同一分钟重复触发
        self._load()
        if auto_start:
            self._start_scheduler()

    def stop_scheduler(self, timeout: float = 3.0) -> None:
        """停止后台调度线程（应用退出时调用）"""
        self._stop_event.set()
        if self._scheduler and self._scheduler.is_alive():
            self._scheduler.join(timeout)

    def _start_scheduler(self) -> None:
        """启动后台调度守护线程"""
        if self._scheduler and self._scheduler.is_alive():
            return
        self._stop_event.clear()
        self._scheduler = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="cron-scheduler",
        )
        self._scheduler.start()

    def _scheduler_loop(self) -> None:
        """调度主循环：每 30 秒检查一次已启用的 cron 条目。"""
        _POLL_INTERVAL = 30  # 秒
        while not self._stop_event.is_set():
            now_t = time.localtime()
            current_minute = (now_t.tm_year, now_t.tm_mon, now_t.tm_mday,
                              now_t.tm_hour, now_t.tm_min)

            # 同一分钟内只触发一次（避免秒级重复）
            if current_minute == getattr(self, "_last_minute_key", None):
                self._stop_event.wait(_POLL_INTERVAL)
                continue

            entries = self.list(enabled_only=True)
            for entry in entries:
                if self._stop_event.is_set():
                    return
                try:
                    if _cron_matches(entry.schedule, now_t):
                        self.record_run(entry.cron_id)
                        _execute_cron_prompt(entry.prompt, entry.cron_id)
                except Exception as e:
                    logging.error("Cron %s: 调度异常 — %s", entry.cron_id, e)

            self._last_minute_key = current_minute
            self._stop_event.wait(_POLL_INTERVAL)

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
