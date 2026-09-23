"""Synchronous exporter boundary for adapters and local analytics."""
import json
from pathlib import Path
from threading import Lock
from typing import Protocol
from .model import TaskRecord


class Exporter(Protocol):
    def export(self, record: TaskRecord) -> None:
        """Accept one immutable completed attempt; raise on delivery failure."""
        ...


class InMemoryExporter:
    """Thread-safe process-local storage. Retains records until clear()."""
    def __init__(self):
        self._records: list[TaskRecord] = []
        self._lock = Lock()

    @property
    def records(self) -> tuple[TaskRecord, ...]:
        with self._lock:
            return tuple(self._records)

    def export(self, record: TaskRecord) -> None:
        with self._lock:
            self._records.append(record)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


class JSONLExporter:
    """Append one record per line; synchronized within this exporter instance."""
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = Lock()

    def export(self, record: TaskRecord) -> None:
        line = json.dumps(record.to_dict(), allow_nan=False) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as output:
                output.write(line)
