"""Persistent transition memory: (map_from, map_to) → button sequences.

Each time the agent's map_id actually changes from A to B, we save the
last N actions taken on A. Next time the agent is on A and the Brain
is choosing what to do, the prompt can include "known way to reach B
from this map: Up, Up, Up, A" so the same path can be replayed instead
of rediscovered from scratch.

This is the cycle-9 mechanism that directly attacks the "no successful
path lives in the cache" failure mode logged in daily_progress §17.

Schema (JSON file at memory/path_memory.json):
{
  "<g_from>-<n_from>": {
    "<g_to>-<n_to>": [
      ["Up","Up","A"],           # success path 1 (oldest)
      ["Right","Up","Up","Up","A"]  # success path 2
    ]
  }
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import config


SEQUENCE_LEN = 8   # how many recent actions to remember per transition
MAX_PATHS_PER_PAIR = 5  # cap entries per (from, to) pair
DIRECTIONS = {"Up", "Down", "Left", "Right", "A", "B"}


@dataclass
class TransitionMemory:
    path: Path = field(default_factory=lambda: config.MEMORY_DIR / "path_memory.json")
    _store: dict[str, dict[str, list[list[str]]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._load()

    def _key(self, g: int, n: int) -> str:
        return f"{g}-{n}"

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for fk, inner in raw.items():
            self._store[fk] = {
                tk: [list(seq) for seq in seqs] for tk, seqs in inner.items()
            }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        out = {
            fk: {tk: [list(s) for s in seqs] for tk, seqs in inner.items()}
            for fk, inner in self._store.items()
        }
        self.path.write_text(
            json.dumps(out, ensure_ascii=False), encoding="utf-8"
        )

    def record_transition(
        self,
        from_g: int,
        from_n: int,
        to_g: int,
        to_n: int,
        recent_actions: list[str],
    ) -> None:
        seq = [a for a in recent_actions[-SEQUENCE_LEN:] if a in DIRECTIONS]
        if not seq:
            return
        fk = self._key(from_g, from_n)
        tk = self._key(to_g, to_n)
        inner = self._store.setdefault(fk, {})
        paths = inner.setdefault(tk, [])
        if seq in paths:
            return
        paths.append(seq)
        if len(paths) > MAX_PATHS_PER_PAIR:
            paths[:] = paths[-MAX_PATHS_PER_PAIR:]

    def shortest_path_to(
        self, from_g: int, from_n: int, to_g: int, to_n: int
    ) -> list[str] | None:
        fk, tk = self._key(from_g, from_n), self._key(to_g, to_n)
        paths = self._store.get(fk, {}).get(tk, [])
        if not paths:
            return None
        return min(paths, key=len)

    def summary_for(
        self,
        cur_g: int,
        cur_n: int,
        blocked_first_step: list[str] | None = None,
    ) -> str:
        """One-line prompt fragment of known exits. Paths whose first step is
        currently blocked at this tile are filtered out — Brain must not be
        told to press a button known to fail."""
        blocked = set(blocked_first_step or [])
        fk = self._key(cur_g, cur_n)
        inner = self._store.get(fk, {})
        if not inner:
            return "no known transitions out of this map yet"
        pieces = []
        for tk, paths in inner.items():
            usable = [p for p in paths if p and p[0] not in blocked]
            if not usable:
                continue
            best = min(usable, key=len)
            seq = "".join(_compact_token(t) for t in best)
            first_full = best[0]
            pieces.append(f"to {tk} via '{seq}' first={first_full}")
        if not pieces:
            return "no known transitions (all first-steps blocked here)"
        return "known exits: " + "; ".join(pieces[:4])


def _compact_token(t: str) -> str:
    return {"Up": "U", "Down": "D", "Left": "L", "Right": "R", "A": "a", "B": "b"}.get(t, "?")
