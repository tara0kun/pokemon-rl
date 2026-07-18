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

# Canon-seed schema version. Bump when the seeding tables/logic change so
# stale persisted files re-derive on next load (empirical data is carried
# over in _reseed). v2 = per-tileset behavior tables: v1 applied ONE fixed
# secondary attr table (rustboro) to every map, so e.g. Route114 (secondary
# gTileset_Fallarbor) read behavior 0x00 for all 51 of its ledge metatiles
# -> ledge_jumps empty, grass/water wrong outside the rustboro cluster.
SEED_VERSION = 2

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
# tile's elevation. Canonical table lives in map_data (also used for the
# region graph's ledge component edges); aliased here for existing users.
LEDGE_JUMP_BEHAVIORS = md.LEDGE_JUMP_BEHAVIORS

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
    # blocked_triggers: coord_event tiles that PUSH THE PLAYER BACK (a story/item
    # gate we can't pass), e.g. Route111's Route111_EventScript_ViciousSandstorm-
    # Trigger* which checkitem GO_GOGGLES and shove you off the tile. BFS must
    # treat them as walls so it routes AROUND the desert to the pre-desert exit,
    # instead of dead-ending against the invisible push-back (the static
    # collision shows these tiles walkable). Seeded from canon coord_events.
    blocked_triggers: set[tuple[int, int]] = field(default_factory=set)
    warps: dict[tuple[int, int], dict] = field(default_factory=dict)
    npcs: list[dict] = field(default_factory=list)
    encounters_seen: list[dict] = field(default_factory=list)
    exits: dict[str, set[tuple[int, int]]] = field(default_factory=dict)
    canon_loaded: bool = False
    # Version of the canon-seed schema this object was derived with. Fresh
    # seeds get the current SEED_VERSION; legacy files (no field) load as 1
    # and are re-derived by MapKnowledgeStore.get().
    seed_version: int = SEED_VERSION

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
            "blocked_triggers": sorted(list(self.blocked_triggers)),
            "warps": {f"{x},{y}": v for (x, y), v in self.warps.items()},
            "npcs": self.npcs,
            "encounters_seen": self.encounters_seen,
            "exits": {d: sorted(list(t)) for d, t in self.exits.items()},
            "canon_loaded": self.canon_loaded,
            "seed_version": self.seed_version,
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
        mk.blocked_triggers = {
            tuple(t) for t in data.get("blocked_triggers", [])
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
        try:
            mk.seed_version = int(data.get("seed_version", 1))
        except (TypeError, ValueError):
            mk.seed_version = 1
        return mk


class MapKnowledgeStore:
    """Loads / saves per-map knowledge files and seeds them from canon."""

    def __init__(self) -> None:
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        self._cache: dict[tuple[int, int], MapKnowledge] = {}
        self._mc = md.get_cache()
        # behavior tables memoized per (primary, secondary) tileset pair
        self._beh_cache: dict[tuple[str, str | None], dict[int, int]] = {}

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
                if mk.seed_version < SEED_VERSION:
                    fresh = self._reseed(mk)
                    if fresh is not mk:  # only persist a SUCCESSFUL re-derive
                        mk = fresh
                        self.save(mk)
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

    def _reseed(self, old: MapKnowledge) -> MapKnowledge:
        """Re-derive canon fields with the current tables, keeping empirical
        data. Canon sets (grass/water/elevation/ledges/warps/npcs/exits/
        triggers) are replaced wholesale — the old ones may come from a wrong
        secondary tileset table (pre-SEED_VERSION 2). Empirical carry-over:
        encounters_seen (plus the grass tiles those encounters proved) and
        trainer_los additions from record_trainer_battle (canon LOS is a
        subset of both old and new, so the union only re-adds empirical).
        Returns `old` unchanged when canon is unavailable (offline)."""
        fresh = MapKnowledge(map_g=old.map_g, map_n=old.map_n)
        self._seed_from_canon(fresh)
        if not fresh.canon_loaded:
            return old
        fresh.encounters_seen = old.encounters_seen
        fresh.grass_tiles |= {
            (e["x"], e["y"]) for e in old.encounters_seen
            if "x" in e and "y" in e
        }
        fresh.trainer_los |= old.trainer_los
        return fresh

    def _load_behavior_table(self, map_name: str | None = None) -> dict[int, int]:
        """Metatile id → behavior byte for THIS map's tileset pair.

        Metatile ids < 0x200 index the layout's PRIMARY tileset attribute
        table; ids >= 0x200 index its SECONDARY table (at id - 0x200). Every
        layout names its own pair in layouts.json, so resolve per map and
        memoize per (primary, secondary) pair. Missing attr files download
        from pokeemerald into map_cache (md.tileset_attr_path). When the pair
        can't be resolved, fall back to General-primary-only: UNDER-
        classifying (no grass/water/ledges in the 0x200+ range) is safer than
        applying another tileset's table, which is exactly the v1 bug this
        replaces (rustboro attrs applied to Route114 hid all 51 of its
        ledges)."""
        prim, sec = "gTileset_General", None
        if map_name:
            try:
                jp = (config.MEMORY_DIR / "map_cache"
                      / f"{map_name}.map.json")
                layout_id = json.loads(
                    jp.read_text(encoding="utf-8")
                ).get("layout")
                pair = md.layout_tilesets(layout_id) if layout_id else None
                if pair:
                    prim, sec = pair
            except (OSError, json.JSONDecodeError):
                pass
        key = (prim, sec)
        cached = self._beh_cache.get(key)
        if cached is not None:
            return cached
        cache: dict[int, int] = {}
        pp = md.tileset_attr_path("primary", prim)
        if pp is not None:
            try:
                prim_data = pp.read_bytes()
                for meta in range(min(0x200, len(prim_data) // 2)):
                    cache[meta] = struct.unpack_from(
                        "<H", prim_data, meta * 2,
                    )[0] & 0xFF
            except OSError:
                pass
        sp = md.tileset_attr_path("secondary", sec) if sec else None
        if sp is not None:
            try:
                sec_data = sp.read_bytes()
                for i in range(len(sec_data) // 2):
                    cache[0x200 + i] = struct.unpack_from(
                        "<H", sec_data, i * 2,
                    )[0] & 0xFF
            except OSError:
                pass
        self._beh_cache[key] = cache
        return cache

    def _seed_from_canon(self, mk: MapKnowledge) -> None:
        """Populate grass / trainer_los / warps / npcs / exits from
        pokeemerald decomp via map_data.MapCache."""
        info = self._mc.get(mk.map_g, mk.map_n)
        if info is None:
            return
        beh_table = self._load_behavior_table(info.name)
        mk.name = info.name
        mk.width = info.width
        mk.height = info.height
        # Water-blocking is for OUTDOOR surfable water. Indoor AND underground
        # (cave) tilesets reuse the water behavior byte for walkable floor, so
        # blocking it there carves real floor into unreachable islands:
        #  - Dewford Gym (INDOOR): 47 maze-floor tiles flagged water -> BFS to
        #    Brawly returned NONE.
        #  - Granite Cave (UNDERGROUND): the 1F entrance floor was split from the
        #    (17,11) B1F ladder by ~misclassified water, so the Steven-letter
        #    trek (entrance -> B1F -> ... -> StevensRoom) was unreachable. Surf
        #    is not obtainable until well after Granite Cave, so any "water" the
        #    story requires crossing here is necessarily walkable floor (H4a).
        # Only OUTDOOR maps keep water blocking (genuine surfable ponds/sea).
        block_water = True
        try:
            _jp = config.MEMORY_DIR / "map_cache" / f"{info.name}.map.json"
            if _jp.exists():
                _mt = json.loads(_jp.read_text(encoding="utf-8")).get("map_type")
                block_water = _mt not in (
                    "MAP_TYPE_INDOOR", "MAP_TYPE_UNDERGROUND",
                )
        except (OSError, json.JSONDecodeError):
            pass
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
                        if block_water and beh in WATER_BEHAVIORS:
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
                # Push-back coord_event triggers (item/story gates that shove
                # the player off the tile — e.g. the Route111 Go-Goggles desert
                # ViciousSandstorm gate). BFS must treat these as walls. We match
                # by script keyword (canon-name reference, not a hardcoded coord),
                # and ONLY the "Vicious" variants push back — the plain
                # SandstormTrigger just starts the weather and is harmless.
                for ce in map_json.get("coord_events") or []:
                    cx = ce.get("x", -1)
                    cy = ce.get("y", -1)
                    cs = str(ce.get("script", "") or "")
                    if cx >= 0 and cy >= 0 and "ViciousSandstorm" in cs:
                        mk.blocked_triggers.add((cx, cy))
        except (OSError, json.JSONDecodeError):
            pass
        # Exits per direction from canon connections (no empirical fix here;
        # heur or map_data layer can override at decision time).
        # Coarse per-direction exit hint = whole walkable edge (offset/dest
        # filtering happens in map_data.exit_tiles_toward, not here). A side
        # may hold >1 connection now; the whole-edge union is the right hint.
        for direction in info.connections:
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
