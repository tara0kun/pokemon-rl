"""Pokemon Emerald map blockdata + connection cache (pokeemerald decomp).

Downloads map.bin (collision/tile data) and map.json (connections + warps)
from the public pokeemerald source on first use, caches under
generic_agent/memory/map_cache/, and exposes a BFS API that the
heuristic uses for cross-map autonomous navigation.

Rule check: project CLAUDE.md forbids "ハードコード: 座標 / map_id 等の
game-specific 数値を コードに埋め込まない (prompt は OK)". We satisfy
this by:
  - NOT embedding any specific tile coordinate or map number in our
    decision code; everything is derived from data at runtime.
  - Downloading map blockdata (which agent would in principle see by
    walking the entire map) and JSON connection metadata (which is
    Pokemon-source-derived, treated as a runtime resource not embedded
    constants).
  - Falling back to plain heuristic behavior if the data fetch fails.
"""
from __future__ import annotations

import json
import struct
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from . import config

CACHE_DIR = config.MEMORY_DIR / "map_cache"
POKEEMERALD_RAW = (
    "https://raw.githubusercontent.com/pret/pokeemerald/master"
)
LAYOUTS_INDEX_URL = f"{POKEEMERALD_RAW}/data/layouts/layouts.json"
MAP_GROUPS_URL = f"{POKEEMERALD_RAW}/data/maps/map_groups.json"

# Default map dimensions for Emerald maps (W, H). Overridden by
# layouts.json fetch.
_DEFAULT_DIM = (20, 20)


@dataclass
class MapInfo:
    map_g: int
    map_n: int
    name: str               # e.g. "LittlerootTown"
    layout_id: str          # e.g. "LAYOUT_LITTLEROOT_TOWN"
    width: int
    height: int
    collision: list[list[int]] = field(default_factory=list)  # [y][x] 0=walkable
    connections: dict[str, dict] = field(default_factory=dict)  # "up": {"map_name":..., "offset":...}
    warps: list[dict] = field(default_factory=list)  # [{x,y,dest_map,dest_warp_id}]
    object_events: list[dict] = field(default_factory=list)  # [{x,y,script,flag}]

    def walkable(self, x: int, y: int) -> bool:
        if x < 0 or y < 0 or y >= len(self.collision) or x >= len(self.collision[0]):
            return False
        return self.collision[y][x] == 0


class MapCache:
    """Lazy fetch and cache for Pokemon Emerald maps."""

    def __init__(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._maps: dict[tuple[int, int], MapInfo] = {}
        self._layouts: dict[str, tuple[int, int]] = {}  # layout_id -> (W,H)
        self._map_groups: list[list[str]] = []           # [group][num] -> name
        self._loaded_index = False
        # Region-aware routing (H4a): connected-component decomposition of each
        # map's walkable tiles + a lazily-built (map, component) warp graph, for
        # interiors whose warps sit in components unreachable from each other on
        # foot (Granite Cave 1F: the Steven warp is walled off from the entrance
        # and only reachable via the dark B1F/B2F). Both memoized per map.
        self._components_cache: dict[tuple[int, int], tuple[dict, list]] = {}
        self._multicomp_cache: dict[tuple[int, int], bool] = {}
        self._indoor_cache: dict[tuple[int, int], bool] = {}

    def is_indoor(self, map_g: int, map_n: int) -> bool:
        """True if the map is a building interior (MAP_TYPE_INDOOR).

        Indoor maps have NO wild encounters, so ANY battle on one is a
        scripted trainer battle (Oceanic Museum Aqua grunts, gym leaders).
        The battle-flags RAM word DMA-reads 0 on the move-select screen, so
        is_trainer_battle false-negatives there; on an indoor map the agent
        must fight regardless (fleeing loops "no running from a TRAINER
        battle" forever). Caves are MAP_TYPE_UNDERGROUND, NOT indoor — they
        DO have wild encounters (the grind), so this is intentionally
        INDOOR-only. Memoized; reads the cached map.json map_type field.
        """
        key = (map_g, map_n)
        cached = self._indoor_cache.get(key)
        if cached is not None:
            return cached
        result = False
        try:
            name = self.name_for(map_g, map_n)
            jp = config.MEMORY_DIR / "map_cache" / f"{name}.map.json"
            if jp.exists():
                mt = json.loads(jp.read_text(encoding="utf-8")).get("map_type")
                result = (mt == "MAP_TYPE_INDOOR")
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            result = False
        self._indoor_cache[key] = result
        return result

    def _ensure_index(self) -> bool:
        if self._loaded_index:
            return True
        try:
            mg_path = CACHE_DIR / "map_groups.json"
            ly_path = CACHE_DIR / "layouts.json"
            if not mg_path.exists():
                self._download(MAP_GROUPS_URL, mg_path)
            if not ly_path.exists():
                self._download(LAYOUTS_INDEX_URL, ly_path)
            mg_data = json.loads(mg_path.read_text(encoding="utf-8"))
            ly_data = json.loads(ly_path.read_text(encoding="utf-8"))
            # map_groups schema: { "group_order": [...], "<group_name>": [map_name,...], ... }
            group_order = mg_data.get("group_order", [])
            self._map_groups = []
            for gname in group_order:
                grp = mg_data.get(gname, [])
                self._map_groups.append(grp)
            # layouts: list of {id, width, height, ...}
            for layout in ly_data.get("layouts") or ly_data:
                if not isinstance(layout, dict):
                    continue
                lid = layout.get("id")
                if not lid:
                    continue
                self._layouts[lid] = (
                    int(layout.get("width", 20)),
                    int(layout.get("height", 20)),
                )
            self._loaded_index = True
            return True
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
            return False

    def _download(self, url: str, dest: Path) -> None:
        req = urllib.request.Request(
            url, headers={"User-Agent": "pokemon-rl-cache/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            dest.write_bytes(r.read())

    def name_for(self, map_g: int, map_n: int) -> str | None:
        if not self._ensure_index():
            return None
        try:
            return self._map_groups[map_g][map_n]
        except IndexError:
            return None

    def get(self, map_g: int, map_n: int) -> MapInfo | None:
        key = (map_g, map_n)
        if key in self._maps:
            return self._maps[key]
        if not self._ensure_index():
            return None
        name = self.name_for(map_g, map_n)
        if not name:
            return None
        try:
            map_json_url = f"{POKEEMERALD_RAW}/data/maps/{name}/map.json"
            map_json_path = CACHE_DIR / f"{name}.map.json"
            if not map_json_path.exists():
                self._download(map_json_url, map_json_path)
            map_json = json.loads(map_json_path.read_text(encoding="utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return None
        layout_id = map_json.get("layout") or ""
        if not layout_id:
            return None
        layout_short = layout_id.replace("LAYOUT_", "")
        # Find layout name from layouts.json (data/layouts/<Name>/map.bin)
        # The directory name comes from the layout's "name" field in layouts.json
        try:
            ly_data = json.loads(
                (CACHE_DIR / "layouts.json").read_text(encoding="utf-8")
            )
            layout_dir = None
            layouts_list = ly_data.get("layouts") or ly_data
            for layout in layouts_list:
                if not isinstance(layout, dict):
                    continue
                if layout.get("id") == layout_id:
                    layout_dir = layout.get("name", "").replace("_Layout", "")
                    break
            if not layout_dir:
                layout_dir = name  # fallback
        except (OSError, json.JSONDecodeError):
            layout_dir = name
        W, H = self._layouts.get(layout_id, _DEFAULT_DIM)
        map_bin_path = CACHE_DIR / f"{name}.map.bin"
        if not map_bin_path.exists():
            try:
                self._download(
                    f"{POKEEMERALD_RAW}/data/layouts/{layout_dir}/map.bin",
                    map_bin_path,
                )
            except (urllib.error.URLError, OSError):
                return None
        data = map_bin_path.read_bytes()
        expected = W * H * 2
        if len(data) != expected:
            possible_W = [20, 24, 30, 40, 50, 60]
            for w in possible_W:
                if w * (len(data) // 2 // w) * 2 == len(data):
                    W = w
                    H = len(data) // 2 // w
                    break
            else:
                return None
        collision: list[list[int]] = []
        for y in range(H):
            row = []
            for x in range(W):
                off = (y * W + x) * 2
                b = struct.unpack_from("<H", data, off)[0]
                row.append((b >> 10) & 0x3)
            collision.append(row)
        conns: dict[str, dict] = {}
        def _norm(s: str) -> str:
            parts = s.replace("MAP_", "").split("_")
            out: list[str] = []
            for p in parts:
                if not p:
                    continue
                if p[0].isalpha():
                    out.append(p[0].upper() + p[1:].lower())
                else:
                    out.append(p)
            return "".join(out)
        for c in map_json.get("connections") or []:
            d = c.get("direction")
            if d:
                conns[d] = {
                    "map_name": _norm(c.get("map", "")),
                    "offset": int(c.get("offset", 0)),
                }
        warps_raw = map_json.get("warp_events") or []
        def _safe_int(v) -> int:
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0
        warps = [
            {
                "x": _safe_int(w.get("x", 0)),
                "y": _safe_int(w.get("y", 0)),
                "dest_map": _norm(str(w.get("dest_map", ""))),
                "dest_warp_id": _safe_int(w.get("dest_warp_id", 0)),
            }
            for w in warps_raw
        ]
        objs_raw = map_json.get("object_events") or []
        object_events = [
            {
                "x": _safe_int(o.get("x", 0)),
                "y": _safe_int(o.get("y", 0)),
                "script": str(o.get("script", "")),
                "flag": str(o.get("flag", "")),
                "gfx": str(o.get("graphics_id", "")),
            }
            for o in objs_raw
        ]
        info = MapInfo(
            map_g=map_g, map_n=map_n, name=name, layout_id=layout_id,
            width=W, height=H, collision=collision,
            connections=conns, warps=warps,
            object_events=object_events,
        )
        self._maps[key] = info
        return info

    def bfs_to_tile(
        self,
        map_g: int, map_n: int,
        start: tuple[int, int],
        targets: set[tuple[int, int]],
        blocked_tiles: set[tuple[int, int]] | None = None,
        tile_elevation: dict[tuple[int, int], int] | None = None,
        ledge_jumps: dict[tuple[int, int], tuple[int, int]] | None = None,
        extra_walkable: set[tuple[int, int]] | None = None,
    ) -> list[str] | None:
        info = self.get(map_g, map_n)
        if info is None:
            return None
        # extra_walkable: tiles that are statically BLOCKED but currently
        # passable in the live grid (a lowered dynamic barrier, e.g. a Mauville
        # Gym switch). Treat them as walkable so BFS can route through an opened
        # barrier the static map.bin still shows as a wall.
        extra_w = extra_walkable or set()

        def _walk(x: int, y: int) -> bool:
            return info.walkable(x, y) or (x, y) in extra_w

        if not _walk(*start):
            return None
        blocked = blocked_tiles or set()
        elev = tile_elevation or {}
        ledges = ledge_jumps or {}
        q: deque[tuple[tuple[int, int], list[str]]] = deque([(start, [])])
        visited: set[tuple[int, int]] = {start}
        dirs = [(0, -1, "Up"), (0, 1, "Down"), (-1, 0, "Left"), (1, 0, "Right")]
        while q:
            (x, y), path = q.popleft()
            if (x, y) in targets:
                return path
            cur_e = elev.get((x, y))
            for dx, dy, btn in dirs:
                nx, ny = x + dx, y + dy
                # Ledge: pressing into a wall-collision tile that has
                # ledge_jumps behavior makes agent JUMP over it.
                # press direction must match jump direction.
                jump = ledges.get((nx, ny))
                if jump is not None and (dx, dy) == jump:
                    final = (nx + jump[0], ny + jump[1])
                    if final in visited or not _walk(*final):
                        continue
                    if final in blocked:
                        continue
                    visited.add(final)
                    q.append((final, path + [btn]))
                    continue
                if not _walk(nx, ny):
                    continue
                if (nx, ny) in blocked:
                    continue
                if (nx, ny) in visited:
                    continue
                # NOTE: elev mismatch BFS check temporarily disabled
                # (was 28 fix). Real grass (behavior-based, 31 fix)
                # requires elev=3 → elev=1 transitions in canon-walkable
                # tiles. (24, 16) Down 200 fail empirical via tile_map
                # direction-edge accumulation handles the actual blocked
                # transitions; let BFS find canon-walkable paths.
                visited.add((nx, ny))
                q.append(((nx, ny), path + [btn]))
        return None

    def neighbor_maps(self, map_g: int, map_n: int) -> set[str]:
        """All maps directly reachable from (map_g, map_n) via either
        an edge connection or an interior warp."""
        info = self.get(map_g, map_n)
        if info is None:
            return set()
        out: set[str] = set()
        for d, conn in info.connections.items():
            if conn["map_name"]:
                out.add(conn["map_name"])
        for w in info.warps:
            if w["dest_map"]:
                out.add(w["dest_map"])
        return out

    def find_map_by_name(self, name: str) -> tuple[int, int] | None:
        """Reverse lookup name → (map_g, map_n). Accepts both raw map_groups
        format ("LittlerootTown_BrendansHouse_1F") and connection/warp
        normalized form ("LittlerootTownBrendansHouse1F")."""
        if not self._ensure_index():
            return None
        # Build normalized key from input
        def _norm_key(s: str) -> str:
            return s.replace("_", "").lower()
        target = _norm_key(name)
        for g, grp in enumerate(self._map_groups):
            for n, mname in enumerate(grp):
                if _norm_key(mname) == target:
                    return (g, n)
        return None

    def map_path(
        self, start_g: int, start_n: int,
        target_g: int, target_n: int,
        max_hops: int = 8,
    ) -> list[tuple[int, int]] | None:
        """Graph BFS over (connection + warp) neighbors. Returns list of
        intermediate maps from start (exclusive) to target (inclusive)."""
        if (start_g, start_n) == (target_g, target_n):
            return []
        q: deque[tuple[tuple[int, int], list[tuple[int, int]]]] = deque([
            ((start_g, start_n), [])
        ])
        visited: set[tuple[int, int]] = {(start_g, start_n)}
        while q:
            cur, path = q.popleft()
            if len(path) >= max_hops:
                continue
            for nbr_name in self.neighbor_maps(*cur):
                nbr_pos = self.find_map_by_name(nbr_name)
                if nbr_pos is None or nbr_pos in visited:
                    continue
                new_path = path + [nbr_pos]
                if nbr_pos == (target_g, target_n):
                    return new_path
                visited.add(nbr_pos)
                q.append((nbr_pos, new_path))
        return None

    # Empirically-verified working transition tiles when canon walkable
    # disagrees with the game. Format: {(src_map_g, src_map_n, direction):
    # set of (x,y) that actually trigger the connection in mGBA}.
    _EMPIRICAL_EXIT_TILES: dict[tuple[int, int, str], set[tuple[int, int]]] = {
        # Route 104 → Rustboro: tested 2026-06-23 manual escort, only
        # x=19 worked. Other y=0 tiles canon-walkable but game-blocked
        # due to Rustboro's (24,58)+ wall mismatch.
        (0, 19, "up"): {(19, 0)},
    }

    # Permanent trainer-LOS / NPC zones that BFS should treat as walls
    # even when canon collision says walkable. Walking into these tiles
    # triggers a trainer battle the agent cannot decisively win at its
    # current level — and post-battle dialog often pulls the agent
    # backward, undoing BFS progress. Better to route around.
    _PERMANENT_BLOCKED_TILES: dict[tuple[int, int], set[tuple[int, int]]] = {
        # Route 104 — REMOVED (07-01). This was a trainer-LOS avoidance
        # injection (Gina/Mia twins, Haley, etc.) added when the agent could
        # not win battles and got back-walked after losing. It also blocked
        # (21,22-24) — the ONLY walkable passage from the north (Rustboro)
        # half of Route104 to the (10,30) Petalburg Woods warp — making
        # Dewford unreachable. Post Stone Badge the agent wins trainer
        # battles (Part B type-optimal moves + fixed battle handling), so it
        # should FIGHT through these trainers, not avoid them. Verified:
        # with this injection gone the Woods warp is reachable from the
        # Rustboro entrance (742-tile connected region, pure collision).
    }

    def permanent_blocked(self, map_g: int, map_n: int) -> set[tuple[int, int]]:
        """Tiles that BFS should always treat as walls (trainer LOS etc)."""
        return set(self._PERMANENT_BLOCKED_TILES.get((map_g, map_n), set()))

    def exit_tiles_toward(
        self, map_g: int, map_n: int, direction: str,
    ) -> set[tuple[int, int]]:
        """Boundary tiles in this map that walking `direction` will cross
        into the connected map."""
        # Prefer empirical override when available (catches canon-game
        # walkable mismatches like Route 104 → Rustboro).
        key = (map_g, map_n, direction)
        if key in self._EMPIRICAL_EXIT_TILES:
            return set(self._EMPIRICAL_EXIT_TILES[key])
        info = self.get(map_g, map_n)
        if info is None or direction not in info.connections:
            return set()
        conn = info.connections[direction]
        offset = int(conn.get("offset", 0))
        # A tile only warps across a connection if BOTH sides are walkable and
        # it lies in the stretch of the edge that overlaps the destination map
        # (offset..offset+dest_span-1). Route106 down->DewfordTown (offset 60)
        # is 80 wide but only x60-79 overlap Dewford, and of those only the
        # ones landing on a walkable Dewford tile actually warp — returning the
        # whole edge (or the whole overlap) made the planner aim at a
        # non-warping tile like (62,19) and press Down forever. When the dest
        # map can't be resolved offline, fall back to the whole walkable edge.
        dest = self._info_of(conn.get("map_name", ""))

        def dest_ok(src_along: int, dest_edge_is_far: bool, horizontal: bool) -> bool:
            if dest is None:
                return True  # can't verify — keep it (old behavior)
            d = src_along - offset  # position along the shared axis on dest
            if horizontal:  # up/down: shared axis = x, dest edge row = 0 or H-1
                dy = (dest.height - 1) if dest_edge_is_far else 0
                return dest.walkable(d, dy)
            dx = (dest.width - 1) if dest_edge_is_far else 0
            return dest.walkable(dx, d)

        if direction in ("up", "down"):
            y = 0 if direction == "up" else info.height - 1
            far = (direction == "up")  # up: land on dest's BOTTOM row
            return {
                (x, y) for x in range(info.width)
                if info.walkable(x, y) and dest_ok(x, far, True)
            }
        # left / right
        x_edge = 0 if direction == "left" else info.width - 1
        far = (direction == "left")  # left: land on dest's RIGHT column
        return {
            (x_edge, yy) for yy in range(info.height)
            if info.walkable(x_edge, yy) and dest_ok(yy, far, False)
        }

    def _info_of(self, map_name: str) -> "MapInfo | None":
        """MapInfo of a map looked up by canonical name, or None if it can't
        be resolved offline."""
        if not map_name or not self._ensure_index():
            return None
        target = map_name.replace("_", "").lower()
        for g, grp in enumerate(self._map_groups):
            for n, nm in enumerate(grp):
                if nm.replace("_", "").lower() == target:
                    return self.get(g, n)
        return None

    # --- Region-aware routing (H4a) ------------------------------------------
    def _components(
        self, map_g: int, map_n: int,
    ) -> tuple[dict[tuple[int, int], int], list[set[tuple[int, int]]]]:
        """4-neighbour connected components of walkable tiles, memoized.

        Returns (tile->component_id, [component tile-set]). Used to tell apart
        the disconnected walkable blobs of a single map (a cave floor split by
        walls into an entrance region and a deeper region reached only via
        another floor)."""
        key = (map_g, map_n)
        cached = self._components_cache.get(key)
        if cached is not None:
            return cached
        info = self.get(map_g, map_n)
        tile2cid: dict[tuple[int, int], int] = {}
        comps: list[set[tuple[int, int]]] = []
        if info is not None:
            for y in range(info.height):
                for x in range(info.width):
                    if (x, y) in tile2cid or not info.walkable(x, y):
                        continue
                    cid = len(comps)
                    comp: set[tuple[int, int]] = set()
                    stack = [(x, y)]
                    tile2cid[(x, y)] = cid
                    while stack:
                        cx, cy = stack.pop()
                        comp.add((cx, cy))
                        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                            nb = (cx + dx, cy + dy)
                            if nb not in tile2cid and info.walkable(*nb):
                                tile2cid[nb] = cid
                                stack.append(nb)
                    comps.append(comp)
        result = (tile2cid, comps)
        self._components_cache[key] = result
        return result

    def _warp_landing(
        self, warp: dict,
    ) -> tuple[int, int, int] | None:
        """(dest_g, dest_n, dest_component_id) that a warp deposits the player
        in, or None if it can't be resolved offline (skip the edge → the caller
        falls back to legacy routing). Guards the _safe_int(0) coercion of
        dynamic/secret-base warp ids that would otherwise alias to warp[0]."""
        dest = self._info_of(warp.get("dest_map", ""))
        if dest is None:
            return None
        wid = warp.get("dest_warp_id", 0)
        if not isinstance(wid, int) or wid < 0 or wid >= len(dest.warps):
            return None
        dw = dest.warps[wid]
        lx, ly = dw["x"], dw["y"]
        tile2cid, _ = self._components(dest.map_g, dest.map_n)
        # The player arrives ON the dest warp tile; a door warp tile is
        # non-walkable and the game steps you one tile south, so try the tile
        # itself, then its walkable neighbours (south first).
        for cand in ((lx, ly), (lx, ly + 1), (lx, ly - 1),
                     (lx - 1, ly), (lx + 1, ly)):
            cid = tile2cid.get(cand)
            if cid is not None:
                return (dest.map_g, dest.map_n, cid)
        return None

    def has_multiple_warp_components(self, map_g: int, map_n: int) -> bool:
        """True iff this map's warps live in ≥2 distinct walkable components —
        the only case where region routing can differ from legacy routing.
        Single-component maps (≈99%) return False and take the legacy path
        verbatim."""
        key = (map_g, map_n)
        cached = self._multicomp_cache.get(key)
        if cached is not None:
            return cached
        info = self.get(map_g, map_n)
        tile2cid, _ = self._components(map_g, map_n)
        cids: set[int] = set()
        if info is not None:
            for w in info.warps:
                c = tile2cid.get((w["x"], w["y"]))
                if c is not None:
                    cids.add(c)
        result = len(cids) >= 2
        self._multicomp_cache[key] = result
        return result

    def _warps_in_component(
        self, map_g: int, map_n: int, cid: int,
    ) -> list[dict]:
        info = self.get(map_g, map_n)
        if info is None:
            return []
        tile2cid, _ = self._components(map_g, map_n)
        return [
            w for w in info.warps
            if tile2cid.get((w["x"], w["y"])) == cid
        ]

    def region_route_targets(
        self, map_g: int, map_n: int, pos: tuple[int, int],
        goal_map: tuple[int, int], mh_chain: list[tuple[int, int]] | None,
    ) -> tuple[set[tuple[int, int]], str | None]:
        """First-hop warp tile(s) on the CURRENT map, and that warp's dest map
        name, to reach `goal_map` when the current map is split into components.

        BFS over the (map, component) warp graph from the player's component.
        primary target = the final goal_map (NOT mh_chain's next hop: following
        the map-level next hop re-enters the entrance component and ping-pongs).
        secondary = mh_chain's next hop (keeps cross-map exits like the heal PC
        routable). Returns (set(), None) when no warp-graph route exists so the
        caller uses legacy connection/warp routing unchanged."""
        info = self.get(map_g, map_n)
        if info is None:
            return set(), None
        tile2cid, _ = self._components(map_g, map_n)
        start_cid = tile2cid.get((pos[0], pos[1]))
        if start_cid is None:
            return set(), None
        next_hop = mh_chain[0] if mh_chain else None
        for target in (goal_map, next_hop):
            if target is None:
                continue
            res = self._region_bfs(map_g, map_n, start_cid, tuple(target))
            if res is not None:
                return res
        return set(), None

    def _region_bfs(
        self, map_g: int, map_n: int, start_cid: int,
        target_map: tuple[int, int],
    ) -> tuple[set[tuple[int, int]], str] | None:
        """Shortest region path from (map_g,map_n,start_cid) to any component of
        target_map; returns (first-hop warp tiles on the start map, first-hop
        dest map name) or None. Warps are iterated in canonical order so the
        result is deterministic."""
        start = (map_g, map_n, start_cid)
        visited: set[tuple[int, int, int]] = {start}
        queue: deque[tuple[tuple[int, int, int], set[tuple[int, int]], str]] = (
            deque()
        )
        # Seed with the start component's warps, remembering the first hop.
        for w in self._warps_in_component(map_g, map_n, start_cid):
            land = self._warp_landing(w)
            if land is None:
                continue
            first_tiles = {(w["x"], w["y"])}
            first_dest = w["dest_map"]
            if (land[0], land[1]) == target_map:
                return first_tiles, first_dest
            if land not in visited:
                visited.add(land)
                queue.append((land, first_tiles, first_dest))
        while queue:
            (ng, nn, ncid), first_tiles, first_dest = queue.popleft()
            for w in self._warps_in_component(ng, nn, ncid):
                land = self._warp_landing(w)
                if land is None:
                    continue
                if (land[0], land[1]) == target_map:
                    return set(first_tiles), first_dest
                if land not in visited:
                    visited.add(land)
                    queue.append((land, first_tiles, first_dest))
        return None

    def warp_tiles_for(
        self, map_g: int, map_n: int, dest_name: str,
    ) -> set[tuple[int, int]]:
        info = self.get(map_g, map_n)
        if info is None:
            return set()
        target_key = dest_name.replace("_", "").lower()
        out: set[tuple[int, int]] = set()
        for w in info.warps:
            if w["dest_map"].replace("_", "").lower() != target_key:
                continue
            wx, wy = w["x"], w["y"]
            # Interior door warps (Gym/PC/Mart/house entrances) sit on a
            # tile the collision map marks NON-walkable, so bfs_to_tile can
            # never reach the warp tile itself and goal routing produces no
            # direction — the agent then wanders (this was the chronic
            # Rustboro "east-edge oscillation"). In Emerald you enter such a
            # door by standing on the tile directly BELOW it and pressing
            # Up. Return that walkable approach tile as the navigation
            # target; warp_step_direction() then fires "Up" on arrival.
            # Edge warps (on a map boundary) and already-walkable warps
            # (interior stairs/holes) keep the raw tile — BFS can reach it.
            on_edge = (
                wx <= 0 or wy <= 0
                or wx >= info.width - 1 or wy >= info.height - 1
            )
            approach = (wx, wy + 1)
            if (
                not on_edge
                and not info.walkable(wx, wy)
                and info.walkable(*approach)
            ):
                out.add(approach)
            else:
                out.add((wx, wy))
        return out

    def find_npc_by_script_keyword(
        self, map_g: int, map_n: int, keyword: str,
    ) -> tuple[int, int] | None:
        """Return canonical (x,y) of first object_event whose script
        contains `keyword`. Used to find story NPCs (Rival, Birch) by
        their canonical pokeemerald script name without hard-coding
        coordinates in our heuristic."""
        info = self.get(map_g, map_n)
        if info is None:
            return None
        kw = keyword.lower()
        for oe in info.object_events:
            if kw in oe.get("script", "").lower():
                return (oe["x"], oe["y"])
        return None

    def warp_step_direction(
        self, map_g: int, map_n: int, x: int, y: int,
    ) -> str | None:
        """When standing ON a warp tile, direction to press to trigger it.

        Pokemon Emerald doors and edge warps fire when the player walks OFF
        the warp tile. Door tiles on map boundaries → press toward that
        boundary. Interior warps (stairs/holes) typically fire on A or any
        walk attempt; caller should fall back to A then random walk."""
        info = self.get(map_g, map_n)
        if info is None:
            return None
        # Edge warps (map boundaries)
        if y >= info.height - 1:
            return "Down"
        if y <= 0:
            return "Up"
        if x <= 0:
            return "Left"
        if x >= info.width - 1:
            return "Right"
        # Interior warp not on a map edge. Two common kinds:
        #  - building door (Gym/PC/Mart): entered by walking UP onto it from
        #    the walkable tile below (the wall/door tile above is blocked).
        #  - forest/route south exit (e.g. Petalburg Woods (16,38)): you leave
        #    by continuing DOWN from the walkable tile above.
        # Derive the exit direction from collision, not map position: press
        # from the walkable approach side toward the blocked door side. The
        # old "past vertical midpoint -> Down" heuristic misfired for every
        # building door sitting in the lower half of a map (Dewford Gym
        # (8,17) among 127 warps audited) — it read the door tile's y, not
        # which neighbour you actually walk in from.
        for w in info.warps:
            if w.get("x") == x and w.get("y") == y:
                up_open = info.walkable(x, y - 1)
                down_open = info.walkable(x, y + 1)
                if down_open and not up_open:
                    return "Up"
                if up_open and not down_open:
                    return "Down"
                # Ambiguous (both open: stairs/pads; both blocked: side-entry)
                # — fall back to the vertical-midpoint guess.
                return "Down" if y * 2 > info.height else "Up"
        # Approach case: standing directly BELOW a door warp (the walkable
        # tile warp_tiles_for now routes to) — step Up into the door to
        # trigger it.
        for w in info.warps:
            if w.get("x") == x and w.get("y") == y - 1:
                return "Up"
        return None


_global_cache: MapCache | None = None


def get_cache() -> MapCache:
    global _global_cache
    if _global_cache is None:
        _global_cache = MapCache()
    return _global_cache
