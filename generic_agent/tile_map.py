"""Persistent tile-level collision map.

Stores per-map (x, y) visit history and which direction attempts
succeeded or were blocked. Lets the Brain prompt include explicit
"you have already tried Up from (7,7) but stayed put" facts.

Schema (JSON file at memory/tile_map.json):
{
  "<map_group>-<map_num>": {
    "tiles": {
      "<x>,<y>": {
        "visits": int,
        "tried": {"Up": int, "Down": int, ...},
        "blocked": ["Up", ...]   # directions confirmed no movement
      }
    }
  }
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import config

DIRECTIONS = ("Up", "Down", "Left", "Right")
DELTA = {"Up": (0, -1), "Down": (0, 1), "Left": (-1, 0), "Right": (1, 0)}


@dataclass
class TileRecord:
    visits: int = 0
    tried: dict[str, int] = field(default_factory=dict)
    blocked: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "visits": self.visits,
            "tried": dict(self.tried),
            "blocked": list(self.blocked),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TileRecord":
        return cls(
            visits=int(d.get("visits", 0)),
            tried=dict(d.get("tried", {})),
            blocked=list(d.get("blocked", [])),
        )


class TileMap:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config.MEMORY_DIR / "tile_map.json")
        self._store: dict[str, dict[str, TileRecord]] = {}
        self._load()

    def _map_key(self, map_group: int, map_num: int) -> str:
        return f"{map_group}-{map_num}"

    def _tile_key(self, x: int, y: int) -> str:
        return f"{x},{y}"

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for mk, tiles in raw.items():
            inner = {}
            for tk, td in tiles.get("tiles", {}).items():
                inner[tk] = TileRecord.from_dict(td)
            self._store[mk] = inner

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        out = {}
        for mk, tiles in self._store.items():
            out[mk] = {
                "tiles": {tk: rec.to_dict() for tk, rec in tiles.items()}
            }
        self.path.write_text(
            json.dumps(out, ensure_ascii=False), encoding="utf-8"
        )

    def _get_record(
        self, map_group: int, map_num: int, x: int, y: int
    ) -> TileRecord:
        mk = self._map_key(map_group, map_num)
        tiles = self._store.setdefault(mk, {})
        tk = self._tile_key(x, y)
        if tk not in tiles:
            tiles[tk] = TileRecord()
        return tiles[tk]

    def record_visit(self, map_group: int, map_num: int, x: int, y: int) -> None:
        rec = self._get_record(map_group, map_num, x, y)
        rec.visits += 1

    def record_attempt(
        self,
        map_group: int,
        map_num: int,
        x: int,
        y: int,
        direction: str,
        moved: bool,
    ) -> None:
        if direction not in DIRECTIONS:
            return
        rec = self._get_record(map_group, map_num, x, y)
        rec.tried[direction] = rec.tried.get(direction, 0) + 1
        if not moved and rec.tried[direction] >= 3 and direction not in rec.blocked:
            rec.blocked.append(direction)

    def summary_for(
        self,
        map_group: int,
        map_num: int,
        cur_x: int,
        cur_y: int,
        radius: int = 3,
    ) -> str:
        """Compact text for the Brain prompt.

        Includes:
        - Tile count visited on this map.
        - Blocked directions at current tile.
        - Unvisited frontier tiles within `radius` Manhattan distance.
        """
        mk = self._map_key(map_group, map_num)
        tiles = self._store.get(mk, {})
        if not tiles:
            return "No tile data yet for this map."

        cur_key = self._tile_key(cur_x, cur_y)
        cur_rec = tiles.get(cur_key)
        cur_blocked = cur_rec.blocked if cur_rec else []

        frontier: list[tuple[int, int]] = []
        for tk, rec in tiles.items():
            try:
                x, y = (int(v) for v in tk.split(","))
            except ValueError:
                continue
            if rec.visits == 0:
                continue
            for d, (dx, dy) in DELTA.items():
                nx, ny = x + dx, y + dy
                if abs(nx - cur_x) + abs(ny - cur_y) > radius:
                    continue
                nk = self._tile_key(nx, ny)
                if nk not in tiles or tiles[nk].visits == 0:
                    if d not in rec.blocked:
                        frontier.append((nx, ny))
        frontier_uniq = sorted(set(frontier))[:6]

        parts = [
            f"tiles_seen={len(tiles)}",
            f"cur=({cur_x},{cur_y})",
            f"blocked_here={cur_blocked or 'none'}",
        ]
        if frontier_uniq:
            parts.append(f"unexplored_nearby={frontier_uniq}")
        else:
            parts.append("no_nearby_unexplored")
        return " ".join(parts)

    def bfs_frontier_direction(
        self,
        map_group: int,
        map_num: int,
        cur_x: int,
        cur_y: int,
    ) -> str | None:
        """BFS through visited tiles to the nearest unvisited frontier.

        Returns the first-step direction (Up/Down/Left/Right) toward that
        frontier, or None if no reachable frontier exists in the current
        map's visited subgraph.

        Walks only along edges NOT in blocked[]. Frontier = neighbor that
        has no record or visits==0.
        """
        mk = self._map_key(map_group, map_num)
        tiles = self._store.get(mk, {})
        if not tiles:
            return None

        start = (cur_x, cur_y)
        start_rec = tiles.get(self._tile_key(cur_x, cur_y))
        if start_rec is None:
            return None

        parent: dict[tuple[int, int], tuple[tuple[int, int], str] | None] = {
            start: None,
        }
        queue: list[tuple[int, int]] = [start]

        while queue:
            cur = queue.pop(0)
            cx, cy = cur
            rec = tiles.get(self._tile_key(cx, cy))
            if rec is None:
                continue

            for d, (dx, dy) in DELTA.items():
                if d in rec.blocked:
                    continue
                n_pos = (cx + dx, cy + dy)
                if n_pos in parent:
                    continue
                parent[n_pos] = (cur, d)
                n_rec = tiles.get(self._tile_key(*n_pos))
                if n_rec is None or n_rec.visits == 0:
                    if cur == start:
                        return d
                    back = cur
                    while True:
                        link = parent.get(back)
                        if link is None:
                            return d
                        p_pos, p_dir = link
                        if p_pos == start:
                            return p_dir
                        back = p_pos
                queue.append(n_pos)

        return None
