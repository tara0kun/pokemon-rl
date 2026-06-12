"""Long-term memory: append-only JSONL plus simple read API.

Files:
  memory/notes.jsonl     ← agent-recorded observations
  memory/run_log.jsonl   ← per-turn action + cost log
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import config

NOTES_FILE = config.MEMORY_DIR / "notes.jsonl"
RUN_LOG_FILE = config.MEMORY_DIR / "run_log.jsonl"


def append_note(note: str, turn: int) -> None:
    NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with NOTES_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(
            {"turn": turn, "ts": time.time(), "note": note},
            ensure_ascii=False,
        ) + "\n")


def recent_notes(limit: int = 8) -> list[str]:
    if not NOTES_FILE.exists():
        return []
    lines = NOTES_FILE.read_text(encoding="utf-8").splitlines()
    notes: list[str] = []
    for line in lines[-limit:]:
        try:
            notes.append(json.loads(line)["note"])
        except (json.JSONDecodeError, KeyError):
            continue
    return notes


def append_run_log(entry: dict[str, Any]) -> None:
    RUN_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.time(), **entry}
    with RUN_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def append_to_path(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.time(), **entry}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
