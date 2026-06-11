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
        overworld: bool = True,
    ) -> None:
        """Increment direction-try counter and mark blocked after 3 fails.

        `overworld=False` short-circuits — direction presses during
        dialogs, battles, recovery or paused-screen states don't move the
        sprite and silently poison the tile's blocked list. Skip them.
        """
        if direction not in DIRECTIONS:
            return
        if not overworld:
            return
        rec = self._get_record(map_group, map_num, x, y)
        rec.tried[direction] = rec.tried.get(direction, 0) + 1
        if not moved and rec.tried[direction] >= 3 and direction not in rec.blocked:
            if len(rec.blocked) >= 3:
                print(
                    f"  [tile-map] refused 4-way seal at ({x},{y}) "
                    f"on map {map_group}-{map_num}, reset tried/blocked"
                )
                rec.tried = {}
                rec.blocked = []
                return
            rec.blocked.append(direction)

    def decay(
        self,
        map_group: int,
        map_num: int,
        max_remove_per_tile: int = 1,
    ) -> int:
        """Periodically relax over-blocked tiles so the agent re-probes.

        For each tile on the map with >=3 blocked directions, remove the
        last entry and zero its tried count, allowing the agent to retry.
        """
        mk = self._map_key(map_group, map_num)
        tiles = self._store.get(mk, {})
        touched = 0
        for rec in tiles.values():
            if len(rec.blocked) >= 3:
                for _ in range(min(max_remove_per_tile, len(rec.blocked))):
                    d = rec.blocked.pop()
                    rec.tried[d] = 0
                touched += 1
        return touched

    def cleanup_phantom_walls(self) -> int:
        """One-shot: any tile with all 4 directions blocked is by
        construction a recording bug (the agent had to stand on it to
        record it, so at least one direction must be reachable). Clear
        them so the next BFS sees real connectivity again."""
        total = 0
        for mk, tiles in self._store.items():
            for rec in tiles.values():
                if {"Up", "Down", "Left", "Right"} <= set(rec.blocked):
                    rec.blocked = []
                    rec.tried = {}
                    total += 1
        return total

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

    def ascii_grid(
        self,
        map_group: int,
        map_num: int,
        cur_x: int | None = None,
        cur_y: int | None = None,
    ) -> str:
        """Compact map dump for debugging / progress visualization.

        Symbols:
          @ current position    . visited tile (no all-direction info)
          # all 4 dirs blocked  v Up blocked   ^ Down blocked
          > Left blocked        < Right blocked
          ? recorded but unvisited (visits==0)
          space  no record yet
        """
        mk = self._map_key(map_group, map_num)
        tiles = self._store.get(mk, {})
        if not tiles:
            return f"[map {mk}] no tiles known"

        coords: list[tuple[int, int]] = []
        for tk in tiles:
            try:
                x, y = (int(v) for v in tk.split(","))
            except ValueError:
                continue
            coords.append((x, y))
        if not coords:
            return f"[map {mk}] no tile coords"
        min_x = min(x for x, _ in coords)
        max_x = max(x for x, _ in coords)
        min_y = min(y for _, y in coords)
        max_y = max(y for _, y in coords)

        lines = [f"[map {mk}] {len(tiles)} tiles, x={min_x}-{max_x} y={min_y}-{max_y}"]
        for y in range(min_y, max_y + 1):
            row_chars: list[str] = []
            for x in range(min_x, max_x + 1):
                if cur_x is not None and (x, y) == (cur_x, cur_y):
                    row_chars.append("@")
                    continue
                rec = tiles.get(self._tile_key(x, y))
                if rec is None:
                    row_chars.append(" ")
                elif rec.visits == 0:
                    row_chars.append("?")
                else:
                    blocked = set(rec.blocked)
                    if {"Up", "Down", "Left", "Right"} <= blocked:
                        row_chars.append("#")
                    elif "Up" in blocked:
                        row_chars.append("v")
                    elif "Down" in blocked:
                        row_chars.append("^")
                    elif "Left" in blocked:
                        row_chars.append(">")
                    elif "Right" in blocked:
                        row_chars.append("<")
                    else:
                        row_chars.append(".")
            lines.append(f"  y={y:3d}: {''.join(row_chars)}")
        return "\n".join(lines)

    def bfs_frontier_direction(
        self,
        map_group: int,
        map_num: int,
        cur_x: int,
        cur_y: int,
        prefer: str = "farthest",
    ) -> str | None:
        """BFS to a chosen unvisited frontier; return first-step direction.

        prefer="nearest"   → stop at first frontier reached (cycle 3 behavior).
        prefer="farthest"  → enumerate all reachable frontiers, return the
                             first-step direction toward the one with the
                             largest Manhattan distance from start. Biases
                             exploration toward the edge of known territory,
                             which on Pokemon routes correlates with map
                             borders and grass tiles.

        Walks only along edges NOT in blocked[]. Frontier = neighbor with
        no record or visits==0.
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

        def first_step_toward(node: tuple[int, int]) -> str | None:
            back = node
            while True:
                link = parent.get(back)
                if link is None:
                    return None
                p_pos, p_dir = link
                if p_pos == start:
                    return p_dir
                back = p_pos

        frontiers: list[tuple[tuple[int, int], int]] = []

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
                is_frontier = n_rec is None or n_rec.visits == 0
                if is_frontier:
                    if prefer == "nearest":
                        if cur == start:
                            return d
                        step = first_step_toward(cur)
                        return step or d
                    dist = abs(n_pos[0] - cur_x) + abs(n_pos[1] - cur_y)
                    frontiers.append((n_pos, dist))
                else:
                    queue.append(n_pos)

        if not frontiers:
            return None

        best_node, _ = max(frontiers, key=lambda fd: fd[1])
        parent_link = parent.get(best_node)
        if parent_link is None:
            return None
        parent_pos, parent_dir = parent_link
        if parent_pos == start:
            return parent_dir
        return first_step_toward(parent_pos)
