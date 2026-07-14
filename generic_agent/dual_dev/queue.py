"""Minimal persistent task backlog for the dual_dev loop.

A continuous (e.g. 24/7) run needs a source of work; this is that source. Tasks
are stored as a JSON list on disk so they survive restarts. The loop pops the
next pending task each cycle and marks the outcome. Kept deliberately small —
enqueue from the CLI or seed the file directly.

Stored under runs/ (gitignored): it is runtime state, not versioned backlog.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config

DEFAULT_QUEUE_FILE = config.RUN_DIR / "task_queue.json"


class TaskQueue:
    def __init__(self, queue_file: Path | None = None) -> None:
        self.queue_file = queue_file or DEFAULT_QUEUE_FILE

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.queue_file.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, tasks: list[dict[str, Any]]) -> None:
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        self.queue_file.write_text(
            json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def add(
        self,
        task: str,
        allow_paths: list[str] | None = None,
        *,
        created_at: str = "",
    ) -> int:
        """Append a pending task. Returns its 1-based id. created_at is passed
        in (no clock here) so callers stamp it; empty string is allowed."""
        tasks = self._load()
        tid = (max((t.get("id", 0) for t in tasks), default=0)) + 1
        tasks.append({
            "id": tid,
            "task": task,
            "allow_paths": list(allow_paths or []),
            "status": "pending",
            "created_at": created_at,
            "result": None,
        })
        self._save(tasks)
        return tid

    def next_pending(self) -> dict[str, Any] | None:
        for t in self._load():
            if t.get("status") == "pending":
                return t
        return None

    def mark(self, task_id: int, status: str, result: Any = None) -> bool:
        tasks = self._load()
        for t in tasks:
            if t.get("id") == task_id:
                t["status"] = status
                if result is not None:
                    t["result"] = result
                self._save(tasks)
                return True
        return False

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self._load():
            out[t.get("status", "?")] = out.get(t.get("status", "?"), 0) + 1
        return out
