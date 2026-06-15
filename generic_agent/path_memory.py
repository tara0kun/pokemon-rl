"""Position-aware persistent transition memory.

Each entry records the EXACT tiles where a map transition happened:
- from_pos = the tile the agent was standing on while still on the OLD
  map when the transitioning button was pressed
- to_pos   = the tile the agent appeared on when the NEW map loaded
- seq      = the last N directional buttons leading up to the transition

This lets the prompt only surface paths whose `from_pos` is within
Manhattan distance D of the agent's current position. The same map
can have several Up-triggered exits (north town border vs. May's house
door vs. Brendan's house door) and they no longer collapse onto the
same "first=Up" hint — the agent is only shown the recipe matching its
actual tile.

Schema (JSON file at memory/path_memory.json):
{
  "<g_from>-<n_from>": {
    "<g_to>-<n_to>": [
      {"from_pos": [x,y], "to_pos": [x,y], "seq": ["Up","Up","Up"]},
      ...
    ]
  }
}

Backward compatibility: an older entry shape (a list of strings) is
loaded as `{from_pos: None, to_pos: None, seq: list}` so the file is
still readable across the schema bump. Records with from_pos=None will
not appear in summary_for output (their Manhattan distance is treated
as infinite).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config


SEQUENCE_LEN = 8
MAX_PATHS_PER_PAIR = 8
DEFAULT_MAX_DISTANCE = 5
DIRECTIONS = {"Up", "Down", "Left", "Right", "A", "B"}


def _coerce_pos(v: Any) -> tuple[int, int] | None:
    if v is None:
        return None
    try:
        x, y = v
        return (int(x), int(y))
    except (ValueError, TypeError):
        return None


@dataclass
class TransitionRecord:
    from_pos: tuple[int, int] | None
    to_pos: tuple[int, int] | None
    seq: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_pos": list(self.from_pos) if self.from_pos else None,
            "to_pos": list(self.to_pos) if self.to_pos else None,
            "seq": list(self.seq),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TransitionRecord":
        return cls(
            from_pos=_coerce_pos(d.get("from_pos")),
            to_pos=_coerce_pos(d.get("to_pos")),
            seq=[str(s) for s in d.get("seq", [])],
        )

    @classmethod
    def from_legacy(cls, seq: list[str]) -> "TransitionRecord":
        return cls(from_pos=None, to_pos=None, seq=[str(s) for s in seq])


@dataclass
class TransitionMemory:
    path: Path = field(default_factory=lambda: config.MEMORY_DIR / "path_memory.json")
    _store: dict[str, dict[str, list[TransitionRecord]]] = field(default_factory=dict)

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
            self._store[fk] = {}
            for tk, entries in inner.items():
                records: list[TransitionRecord] = []
                for entry in entries:
                    if isinstance(entry, dict):
                        records.append(TransitionRecord.from_dict(entry))
                    elif isinstance(entry, list):
                        records.append(TransitionRecord.from_legacy(entry))
                self._store[fk][tk] = records

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        out: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for fk, inner in self._store.items():
            out[fk] = {tk: [r.to_dict() for r in recs] for tk, recs in inner.items()}
        self.path.write_text(
            json.dumps(out, ensure_ascii=False), encoding="utf-8"
        )

    def neighbors(self, map_g: int, map_n: int) -> list[tuple[int, int]]:
        """All maps directly reachable from (map_g, map_n) via recorded
        transitions. Returns list of (g, n) tuples (dedup'd)."""
        out: set[tuple[int, int]] = set()
        inner = self._store.get(self._key(map_g, map_n), {})
        for tk in inner:
            try:
                g, n = (int(v) for v in tk.split("-"))
                out.add((g, n))
            except ValueError:
                continue
        return list(out)

    def find_path_to_map(
        self,
        start_g: int, start_n: int,
        target_g: int, target_n: int,
        max_hops: int = 8,
    ) -> list[tuple[int, int]] | None:
        """BFS shortest sequence of map transitions from start to target.

        Returns the sequence as [(g1, n1), (g2, n2), ...] STARTING from
        the first hop AFTER `start`, ending at `target`. Returns None
        if no path exists in path_memory within max_hops.
        """
        if (start_g, start_n) == (target_g, target_n):
            return []
        visited: set[tuple[int, int]] = {(start_g, start_n)}
        queue: list[tuple[tuple[int, int], list[tuple[int, int]]]] = [
            ((start_g, start_n), [])
        ]
        while queue:
            (cur_g, cur_n), path = queue.pop(0)
            if len(path) >= max_hops:
                continue
            for nbr in self.neighbors(cur_g, cur_n):
                if nbr in visited:
                    continue
                new_path = path + [nbr]
                if nbr == (target_g, target_n):
                    return new_path
                visited.add(nbr)
                queue.append((nbr, new_path))
        return None

    def find_nearest_unexplored_map(
        self,
        start_g: int, start_n: int,
        recent_visited: list[tuple[int, int]],
        max_hops: int = 8,
    ) -> tuple[tuple[int, int], list[tuple[int, int]]] | None:
        """BFS from start through known transitions, return the first
        reachable map that is NOT in `recent_visited`.

        Returns ((target_g, target_n), [(g1,n1), ..., (target_g, target_n)])
        or None if everything reachable is "too familiar".
        """
        recent_set = set(recent_visited)
        if (start_g, start_n) not in recent_set:
            recent_set.add((start_g, start_n))
        visited: set[tuple[int, int]] = {(start_g, start_n)}
        queue: list[tuple[tuple[int, int], list[tuple[int, int]]]] = [
            ((start_g, start_n), [])
        ]
        while queue:
            (cur_g, cur_n), path = queue.pop(0)
            if len(path) >= max_hops:
                continue
            for nbr in self.neighbors(cur_g, cur_n):
                if nbr in visited:
                    continue
                new_path = path + [nbr]
                if nbr not in recent_set:
                    return nbr, new_path
                visited.add(nbr)
                queue.append((nbr, new_path))
        return None

    def first_transition_record(
        self,
        from_g: int, from_n: int,
        to_g: int, to_n: int,
        prefer_pos: tuple[int, int] | None = None,
    ) -> "TransitionRecord | None":
        """Return the best recorded transition record from (from_g,from_n)
        to (to_g,to_n) — closest from_pos to `prefer_pos` if given."""
        inner = self._store.get(self._key(from_g, from_n), {})
        records = inner.get(self._key(to_g, to_n), [])
        if not records:
            return None
        if prefer_pos is None:
            return records[0]
        def _dist(r):
            if r.from_pos is None:
                return float("inf")
            return abs(r.from_pos[0] - prefer_pos[0]) + abs(r.from_pos[1] - prefer_pos[1])
        return min(records, key=_dist)

    def record_transition(
        self,
        from_g: int,
        from_n: int,
        from_x: int | None,
        from_y: int | None,
        to_g: int,
        to_n: int,
        to_x: int | None,
        to_y: int | None,
        recent_actions: list[str],
    ) -> None:
        seq = [a for a in recent_actions[-SEQUENCE_LEN:] if a in DIRECTIONS]
        if not seq:
            return
        from_pos = (
            (int(from_x), int(from_y))
            if from_x is not None and from_y is not None
            else None
        )
        to_pos = (
            (int(to_x), int(to_y))
            if to_x is not None and to_y is not None
            else None
        )
        fk = self._key(from_g, from_n)
        tk = self._key(to_g, to_n)
        inner = self._store.setdefault(fk, {})
        records = inner.setdefault(tk, [])
        for existing in records:
            if (
                existing.seq == seq
                and existing.from_pos == from_pos
                and existing.to_pos == to_pos
            ):
                return
        records.append(TransitionRecord(from_pos, to_pos, seq))
        if len(records) > MAX_PATHS_PER_PAIR:
            records[:] = records[-MAX_PATHS_PER_PAIR:]

    def nearby_records(
        self,
        cur_g: int,
        cur_n: int,
        cur_x: int,
        cur_y: int,
        max_distance: int = DEFAULT_MAX_DISTANCE,
    ) -> list[tuple[str, TransitionRecord, int]]:
        """Return [(target_key, record, distance)] sorted by distance asc.

        Only records with a known from_pos within Manhattan distance
        `max_distance` of (cur_x, cur_y) are returned. Records with
        from_pos=None are skipped entirely.
        """
        fk = self._key(cur_g, cur_n)
        inner = self._store.get(fk, {})
        candidates: list[tuple[str, TransitionRecord, int]] = []
        for tk, records in inner.items():
            for rec in records:
                if rec.from_pos is None:
                    continue
                fx, fy = rec.from_pos
                d = abs(fx - cur_x) + abs(fy - cur_y)
                if d > max_distance:
                    continue
                candidates.append((tk, rec, d))
        candidates.sort(key=lambda t: (t[2], len(t[1].seq)))
        return candidates

    def summary_for(
        self,
        cur_g: int,
        cur_n: int,
        cur_x: int | None = None,
        cur_y: int | None = None,
        blocked_first_step: list[str] | None = None,
        avoid_first: set[str] | None = None,
        max_distance: int = DEFAULT_MAX_DISTANCE,
    ) -> str:
        """Position-aware exit summary.

        With cur_x/cur_y supplied, only records whose `from_pos` is within
        `max_distance` (Manhattan) are shown. Each target map appears at
        most once, using the closest matching record.
        """
        blocked = set(blocked_first_step or [])
        avoid = set(avoid_first or [])
        fk = self._key(cur_g, cur_n)
        inner = self._store.get(fk, {})
        if not inner:
            return "no known transitions out of this map yet"

        position_aware = cur_x is not None and cur_y is not None
        if position_aware:
            usable_per_target: dict[str, TransitionRecord] = {}
            for tk, rec, _d in self.nearby_records(
                cur_g, cur_n, cur_x, cur_y, max_distance
            ):
                if tk in usable_per_target:
                    continue
                if not rec.seq:
                    continue
                if rec.seq[0] in blocked or rec.seq[0] in avoid:
                    continue
                usable_per_target[tk] = rec
            if not usable_per_target:
                return "no known transitions near this tile"
            pieces = []
            for tk, rec in usable_per_target.items():
                seq_compact = "".join(_compact_token(t) for t in rec.seq)
                fx, fy = rec.from_pos
                pieces.append(
                    f"to {tk} via '{seq_compact}' first={rec.seq[0]} "
                    f"from({fx},{fy})"
                )
            return "known exits near you: " + "; ".join(pieces[:4])

        pieces = []
        for tk, records in inner.items():
            usable = [
                r for r in records
                if r.seq and r.seq[0] not in blocked and r.seq[0] not in avoid
            ]
            if not usable:
                continue
            best = min(usable, key=lambda r: len(r.seq))
            seq_compact = "".join(_compact_token(t) for t in best.seq)
            pieces.append(f"to {tk} via '{seq_compact}' first={best.seq[0]}")
        if not pieces:
            return "no known transitions (all first-steps blocked here)"
        return "known exits: " + "; ".join(pieces[:4])


def _compact_token(t: str) -> str:
    return {"Up": "U", "Down": "D", "Left": "L", "Right": "R", "A": "a", "B": "b"}.get(t, "?")
