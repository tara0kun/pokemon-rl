"""Per-map semantic understanding layer.

The agent's prior memory layer (tile_map.py) only records which (x, y)
tiles were visited and which pressed-direction got blocked. That's
mechanical — it does not capture WHAT the tile is. The agent walks a
grass tile but does not "know" it is grass. It steps near a trainer
but does not "know" the trainer's LOS. It enters a map for the first
time but has no record of NPCs, warps, or canonical layout.

MapKnowledge fixes this by building, for each visited map, a semantic
description:

  - grass_tiles: tiles that can trigger wild encounters (canon metatile
    parse + reinforced by empirical encounter observation).
  - trainer_los: trainer position + sight cells (canon object_events
    parse, accounting for movement_type direction and sight value).
  - warps: in-map warp tiles → destination (map_g, map_n, warp_id).
  - npcs: object_events that aren't trainers (scripts give hints).
  - encounters_seen: species + level history of wild battles here.
  - exits: edge boundary tiles per direction (canon connections +
    empirical fallback when canon and game disagree).
  - understanding_score: 0.0-1.0 self-rating used to decide whether
    the agent has enough info to act with confidence here.

Persisted under generic_agent/memory/map_knowledge/<g>-<n>.json, one
file per map. Loaded on demand. Re-derives from canon (pokeemerald
decomp via map_data.MapCache) if missing.

Anti-hardcode discipline: this module reads canon at runtime; the
agent's *decisions* in heur don't read constants from here, they read
this object so they can act on the same understanding for any map
including ones we haven't manually annotated.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import config, map_data as md

KNOWLEDGE_DIR = config.MEMORY_DIR / "map_knowledge"

# 🎯 灯台下暗し fix (06-28): GRASS_METATILES = {0x208, 0x209} was wrong
# — those are MB_POND_WATER (behavior 0x10) in Rustboro tileset.
# Real encounter grass is determined by BEHAVIOR byte from metatile
# attribute table (primary/secondary tileset metatile_attributes.bin).
# Grass behaviors per pokeemerald metatile_behaviors.h:
#   0x02 = MB_TALL_GRASS (wild encounter)
#   0x03 = MB_LONG_GRASS (wild encounter)
#   0x07 = MB_SHORT_GRASS (encounter)
#   0x09 = MB_LONG_GRASS_SOUTH_EDGE
GRASS_BEHAVIORS = {0x02, 0x03, 0x07, 0x09}
# Retain old name for compatibility; new code uses behavior path below.
GRASS_METATILES: set[int] = set()

# Deep-water behaviors (pokeemerald metatile_behaviors.h) that are IMPASSABLE
# on foot — you need Surf. The overworld collision layer alone marks these
# tiles "walkable", so without this the BFS routes straight through ponds/sea
# and the agent walks into water and freezes (chronic Route104 pond stall).
# 0x16 PUDDLE / 0x17 SHALLOW_WATER / 0x1B SHOAL_EDGE stay walkable (shore).
WATER_BEHAVIORS = {
    0x10,  # MB_POND_WATER
    0x11,  # MB_INTERIOR_DEEP_WATER
    0x12,  # MB_DEEP_WATER
    0x13,  # MB_WATERFALL
    0x14,  # MB_SOOTOPOLIS_DEEP_WATER
    0x15,  # MB_OCEAN_WATER
}

# Ledge JUMP behavior IDs from pokeemerald metatile_behaviors.h, mapped
# to (dx, dy) direction the agent jumps when stepping onto the tile.
# Ledges are 1-way edges: stepping onto a JUMP tile from the OPPOSITE
# direction lands the agent on (x+dx, y+dy) regardless of the next
# tile's elevation.
LEDGE_JUMP_BEHAVIORS = {
    0x39: (1, 0),    # JUMP_EAST
    0x3A: (-1, 0),   # JUMP_WEST
    0x3B: (0, -1),   # JUMP_NORTH
    0x3C: (0, 1),    # JUMP_SOUTH
    0x3D: (1, -1),   # JUMP_NORTHEAST
    0x3E: (-1, -1),  # JUMP_NORTHWEST
    0x3F: (1, 1),    # JUMP_SOUTHEAST
    0x40: (-1, 1),   # JUMP_SOUTHWEST
}

# LOS direction deltas. trainer_los expansion walks `sight` cells in
# the trainer's facing direction(s).
_LOS_FROM_FACE: dict[str, list[tuple[int, int]]] = {
    "MOVEMENT_TYPE_FACE_DOWN":  [(0, +1)],
    "MOVEMENT_TYPE_FACE_UP":    [(0, -1)],
    "MOVEMENT_TYPE_FACE_LEFT":  [(-1, 0)],
    "MOVEMENT_TYPE_FACE_RIGHT": [(+1, 0)],
    # Diagonal "look here AND there"
    "MOVEMENT_TYPE_FACE_UP_AND_LEFT":    [(0, -1), (-1, 0)],
    "MOVEMENT_TYPE_FACE_UP_AND_RIGHT":   [(0, -1), (+1, 0)],
    "MOVEMENT_TYPE_FACE_DOWN_AND_LEFT":  [(0, +1), (-1, 0)],
    "MOVEMENT_TYPE_FACE_DOWN_AND_RIGHT": [(0, +1), (+1, 0)],
    # Rotating trainers see in all 4 cardinal directions
    "MOVEMENT_TYPE_ROTATE_CLOCKWISE":         [(0, -1), (+1, 0), (0, +1), (-1, 0)],
    "MOVEMENT_TYPE_ROTATE_COUNTERCLOCKWISE":  [(0, -1), (-1, 0), (0, +1), (+1, 0)],
    # Walk-sequence trainers effectively see in all 4 directions over time
    "MOVEMENT_TYPE_WALK_SEQUENCE_UP_RIGHT_DOWN_LEFT":  [(0, -1), (+1, 0), (0, +1), (-1, 0)],
    "MOVEMENT_TYPE_WALK_SEQUENCE_DOWN_RIGHT_UP_LEFT":  [(0, -1), (+1, 0), (0, +1), (-1, 0)],
    "MOVEMENT_TYPE_WALK_SEQUENCE_RIGHT_LEFT":          [(+1, 0), (-1, 0)],
    "MOVEMENT_TYPE_WALK_SEQUENCE_LEFT_RIGHT":          [(+1, 0), (-1, 0)],
    "MOVEMENT_TYPE_WALK_SEQUENCE_UP_DOWN":             [(0, -1), (0, +1)],
    "MOVEMENT_TYPE_WALK_SEQUENCE_DOWN_UP":             [(0, -1), (0, +1)],
    "MOVEMENT_TYPE_LOOK_AROUND":                       [(0, -1), (+1, 0), (0, +1), (-1, 0)],
}


@dataclass
class MapKnowledge:
    map_g: int
    map_n: int
    name: str = ""
    width: int = 0
    height: int = 0
    grass_tiles: set[tuple[int, int]] = field(default_factory=set)
    water_tiles: set[tuple[int, int]] = field(default_factory=set)
    trainer_los: set[tuple[int, int]] = field(default_factory=set)
    tile_elevation: dict[tuple[int, int], int] = field(default_factory=dict)
    # ledge_jumps: {(x,y): (dx,dy)} = JUMP behavior tiles, 1-way edge
    # in (dx,dy) direction (e.g. (0,1)=south). Pokemon Emerald ledges
    # let agent step OFF bridge layer to ground layer in jump direction.
    ledge_jumps: dict[tuple[int, int], tuple[int, int]] = field(
        default_factory=dict,
    )
    warps: dict[tuple[int, int], dict] = field(default_factory=dict)
    npcs: list[dict] = field(default_factory=list)
    encounters_seen: list[dict] = field(default_factory=list)
    exits: dict[str, set[tuple[int, int]]] = field(default_factory=dict)
    canon_loaded: bool = False

    def understanding_score(self) -> float:
        """0.0-1.0 self-rating.

        canon load (0.5) + has grass info (0.15) + has trainer LOS info
        (0.15) + has warps (0.1) + empirical encounter observations
        (0.1).
        """
        s = 0.0
        if self.canon_loaded:
            s += 0.5
        if self.grass_tiles:
            s += 0.15
        if self.trainer_los:
            s += 0.15
        if self.warps:
            s += 0.10
        if self.encounters_seen:
            s += 0.10
        return min(s, 1.0)

    def to_json(self) -> dict:
        return {
            "map_g": self.map_g,
            "map_n": self.map_n,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "grass_tiles": sorted(list(self.grass_tiles)),
            "water_tiles": sorted(list(self.water_tiles)),
            "trainer_los": sorted(list(self.trainer_los)),
            "tile_elevation": {
                f"{x},{y}": e for (x, y), e in self.tile_elevation.items()
            },
            "ledge_jumps": {
                f"{x},{y}": list(d) for (x, y), d in self.ledge_jumps.items()
            },
            "warps": {f"{x},{y}": v for (x, y), v in self.warps.items()},
            "npcs": self.npcs,
            "encounters_seen": self.encounters_seen,
            "exits": {d: sorted(list(t)) for d, t in self.exits.items()},
            "canon_loaded": self.canon_loaded,
            "understanding_score": self.understanding_score(),
        }

    @classmethod
    def from_json(cls, data: dict) -> "MapKnowledge":
        mk = cls(
            map_g=data["map_g"],
            map_n=data["map_n"],
            name=data.get("name", ""),
            width=data.get("width", 0),
            height=data.get("height", 0),
        )
        mk.grass_tiles = {tuple(t) for t in data.get("grass_tiles", [])}
        mk.water_tiles = {tuple(t) for t in data.get("water_tiles", [])}
        mk.trainer_los = {tuple(t) for t in data.get("trainer_los", [])}
        mk.tile_elevation = {
            tuple(map(int, k.split(","))): v
            for k, v in data.get("tile_elevation", {}).items()
        }
        mk.ledge_jumps = {
            tuple(map(int, k.split(","))): tuple(v)
            for k, v in data.get("ledge_jumps", {}).items()
        }
        mk.warps = {
            tuple(map(int, k.split(","))): v
            for k, v in data.get("warps", {}).items()
        }
        mk.npcs = data.get("npcs", [])
        mk.encounters_seen = data.get("encounters_seen", [])
        mk.exits = {
            d: {tuple(t) for t in tiles}
            for d, tiles in data.get("exits", {}).items()
        }
        mk.canon_loaded = data.get("canon_loaded", False)
        return mk


class MapKnowledgeStore:
    """Loads / saves per-map knowledge files and seeds them from canon."""

    def __init__(self) -> None:
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        self._cache: dict[tuple[int, int], MapKnowledge] = {}
        self._mc = md.get_cache()

    def _path(self, map_g: int, map_n: int) -> Path:
        return KNOWLEDGE_DIR / f"{map_g}-{map_n}.json"

    def get(self, map_g: int, map_n: int) -> MapKnowledge:
        key = (map_g, map_n)
        if key in self._cache:
            return self._cache[key]
        p = self._path(map_g, map_n)
        if p.exists():
            try:
                mk = MapKnowledge.from_json(json.loads(p.read_text()))
                self._cache[key] = mk
                return mk
            except (json.JSONDecodeError, KeyError):
                pass
        # First visit: seed from canon
        mk = MapKnowledge(map_g=map_g, map_n=map_n)
        self._seed_from_canon(mk)
        self._cache[key] = mk
        self.save(mk)
        return mk

    def save(self, mk: MapKnowledge) -> None:
        p = self._path(mk.map_g, mk.map_n)
        p.write_text(json.dumps(mk.to_json(), indent=2, ensure_ascii=False))

    def record_encounter(
        self, map_g: int, map_n: int, x: int, y: int, species: int, level: int,
    ) -> None:
        mk = self.get(map_g, map_n)
        # Tile we encountered on is a grass tile by definition; reinforce.
        mk.grass_tiles.add((x, y))
        mk.encounters_seen.append({
            "x": x, "y": y, "species": species, "level": level,
        })
        # Trim history
        if len(mk.encounters_seen) > 200:
            mk.encounters_seen = mk.encounters_seen[-200:]
        self.save(mk)

    def record_trainer_battle(
        self, map_g: int, map_n: int, x: int, y: int,
    ) -> None:
        """Mark the tile we triggered a trainer battle on as in trainer LOS.
        Reinforces canon LOS, fills in trainers we missed in canon parse."""
        mk = self.get(map_g, map_n)
        mk.trainer_los.add((x, y))
        self.save(mk)

    def _load_behavior_table(self) -> dict[int, int]:
        """Cache primary tileset metatile → behavior mapping (2-byte/entry)."""
        if hasattr(self, "_beh_cache"):
            return self._beh_cache
        cache: dict[int, int] = {}
        try:
            prim = (config.MEMORY_DIR / "map_cache"
                    / "primary_general_attr.bin").read_bytes()
            for meta in range(min(0x200, len(prim) // 2)):
                cache[meta] = struct.unpack_from(
                    "<H", prim, meta * 2,
                )[0] & 0xFF
        except OSError:
            pass
        # Secondary tileset metatiles are indexed 0x200.. (meta - 0x200 into
        # the secondary attr table). Needed to classify water/grass that the
        # primary table (0..0x1FF) doesn't cover — e.g. Route104 pond edges.
        # NOTE: only maps using this secondary tileset are correct; with a
        # single secondary_*.bin extracted (rustboro cluster) this is fine.
        try:
            cache_dir = config.MEMORY_DIR / "map_cache"
            for sec in sorted(cache_dir.glob("secondary_*_attr.bin")):
                sd = sec.read_bytes()
                for i in range(len(sd) // 2):
                    cache[0x200 + i] = struct.unpack_from(
                        "<H", sd, i * 2,
                    )[0] & 0xFF
        except OSError:
            pass
        self._beh_cache = cache
        return cache

    def _seed_from_canon(self, mk: MapKnowledge) -> None:
        """Populate grass / trainer_los / warps / npcs / exits from
        pokeemerald decomp via map_data.MapCache."""
        info = self._mc.get(mk.map_g, mk.map_n)
        if info is None:
            return
        beh_table = self._load_behavior_table()
        mk.name = info.name
        mk.width = info.width
        mk.height = info.height
        mk.warps = {
            (w["x"], w["y"]): {
                "dest_map_g": w.get("dest_map_g"),
                "dest_map_n": w.get("dest_map_n"),
                "dest_warp_id": w.get("dest_warp_id"),
            }
            for w in info.warps if "x" in w and "y" in w
        }
        # Grass tiles from raw map.bin (metatile ID parse)
        try:
            bin_path = config.MEMORY_DIR / "map_cache" / f"{info.name}.map.bin"
            if bin_path.exists():
                data = bin_path.read_bytes()
                W, H = info.width, info.height
                for y in range(H):
                    for x in range(W):
                        off = (y * W + x) * 2
                        if off + 2 > len(data):
                            continue
                        b = struct.unpack_from("<H", data, off)[0]
                        meta = b & 0x3FF
                        elev = (b >> 12) & 0xF
                        mk.tile_elevation[(x, y)] = elev
                        beh = beh_table.get(meta)
                        if beh in GRASS_BEHAVIORS:
                            mk.grass_tiles.add((x, y))
                        if beh in WATER_BEHAVIORS:
                            mk.water_tiles.add((x, y))
                        if beh in LEDGE_JUMP_BEHAVIORS:
                            mk.ledge_jumps[(x, y)] = (
                                LEDGE_JUMP_BEHAVIORS[beh]
                            )
        except OSError:
            pass
        # NPCs and trainer LOS from object_events
        try:
            json_path = config.MEMORY_DIR / "map_cache" / f"{info.name}.map.json"
            if json_path.exists():
                map_json = json.loads(json_path.read_text(encoding="utf-8"))
                for oe in map_json.get("object_events") or []:
                    x = oe.get("x", -1)
                    y = oe.get("y", -1)
                    if x < 0 or y < 0:
                        continue
                    sight_raw = oe.get("trainer_sight_or_berry_tree_id", 0)
                    try:
                        sight = int(sight_raw)
                    except (TypeError, ValueError):
                        sight = 0
                    movement = oe.get("movement_type", "")
                    script = oe.get("script", "")
                    if sight and isinstance(sight, int) and sight > 0:
                        # Trainer with LOS
                        mk.trainer_los.add((x, y))
                        for dx, dy in _LOS_FROM_FACE.get(movement, []):
                            for d in range(1, sight + 1):
                                mk.trainer_los.add((x + dx * d, y + dy * d))
                    mk.npcs.append({
                        "x": x, "y": y,
                        "movement": movement,
                        "sight": sight,
                        "script": script,
                    })
        except (OSError, json.JSONDecodeError):
            pass
        # Exits per direction from canon connections (no empirical fix here;
        # heur or map_data layer can override at decision time).
        for direction, conn in info.connections.items():
            offset = conn.get("offset", 0) if isinstance(conn, dict) else 0
            tiles: set[tuple[int, int]] = set()
            if direction == "up":
                y_edge = 0
                tiles = {(x, y_edge) for x in range(info.width) if info.walkable(x, y_edge)}
            elif direction == "down":
                y_edge = info.height - 1
                tiles = {(x, y_edge) for x in range(info.width) if info.walkable(x, y_edge)}
            elif direction == "left":
                x_edge = 0
                tiles = {(x_edge, y) for y in range(info.height) if info.walkable(x_edge, y)}
            elif direction == "right":
                x_edge = info.width - 1
                tiles = {(x_edge, y) for y in range(info.height) if info.walkable(x_edge, y)}
            if tiles:
                mk.exits[direction] = tiles
            _ = offset
        mk.canon_loaded = True

    def all_maps(self) -> list[tuple[int, int]]:
        return [
            tuple(map(int, p.stem.split("-")))
            for p in KNOWLEDGE_DIR.glob("*-*.json")
        ]

    def understanding_report(self) -> str:
        lines = ["map_g-map_n  name  score  grass  trainer  warps  npcs  encounters"]
        for g, n in sorted(self.all_maps()):
            mk = self.get(g, n)
            lines.append(
                f"  {g}-{n}  {mk.name[:20]:20s}  "
                f"{mk.understanding_score():.2f}  "
                f"{len(mk.grass_tiles):4d}  "
                f"{len(mk.trainer_los):4d}  "
                f"{len(mk.warps):4d}  "
                f"{len(mk.npcs):4d}  "
                f"{len(mk.encounters_seen):4d}"
            )
        return "\n".join(lines)


_STORE: MapKnowledgeStore | None = None


def get_store() -> MapKnowledgeStore:
    global _STORE
    if _STORE is None:
        _STORE = MapKnowledgeStore()
    return _STORE
