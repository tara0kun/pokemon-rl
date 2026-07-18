"""Region-aware routing (H4a) tests.

map_path collapses each map to a single graph node, so it targets a warp that
may be tile-unreachable when a map is split into disconnected walkable
components (Granite Cave floors). region_route_targets routes over a
(map, component) warp graph instead. Two layers:

- Synthetic fixtures (dependency-zero, always run): a hand-built 3-map graph
  where the goal warp sits behind an intermediate map, proving the multi-hop
  region BFS + first-hop extraction + the single-component no-op.
- Granite Cave integration (skipUnless the git-ignored map cache is present):
  the real entrance -> B1F -> B2F -> B1F -> Steven-region -> StevensRoom chain.
"""
from __future__ import annotations

import unittest

from generic_agent import map_data as md
from generic_agent.map_data import MapCache, MapInfo


def _mapinfo(g, n, name, collision, warps):
    """collision: list of rows (0=walkable). warps: [(x,y,dest_name,dest_id)]."""
    h = len(collision)
    w = len(collision[0]) if h else 0
    return MapInfo(
        map_g=g, map_n=n, name=name, layout_id=f"LAYOUT_{name}",
        width=w, height=h, collision=collision,
        connections={},
        warps=[
            {"x": x, "y": y, "dest_map": dm, "dest_warp_id": wid}
            for (x, y, dm, wid) in warps
        ],
        object_events=[],
    )


def _synthetic_cache():
    """MapA is split into comp0 {(0,0),(1,0)} and comp1 {(3,0),(4,0)} by a wall
    at (2,0). comp0 warps to MapB; MapB warps back into MapA's comp1; comp1
    warps to the goal MapC. So MapA-comp0 can only reach MapC via MapB."""
    cache = MapCache()
    mapA = _mapinfo(0, 0, "MapA", [[0, 0, 1, 0, 0]],
                    [(0, 0, "MapB", 0), (4, 0, "MapC", 0)])
    mapB = _mapinfo(0, 1, "MapB", [[0]], [(0, 0, "MapA", 1)])  # -> MapA warp[1]=(4,0)=comp1
    mapC = _mapinfo(0, 2, "MapC", [[0]], [(0, 0, "MapB", 0)])
    cache._maps = {(0, 0): mapA, (0, 1): mapB, (0, 2): mapC}
    cache._map_groups = [["MapA", "MapB", "MapC"]]
    cache._loaded_index = True
    return cache


class SyntheticRegionTests(unittest.TestCase):
    def test_multi_component_gate(self):
        c = _synthetic_cache()
        self.assertTrue(c.has_multiple_warp_components(0, 0))   # MapA split
        self.assertFalse(c.has_multiple_warp_components(0, 1))  # MapB single
        self.assertFalse(c.has_multiple_warp_components(0, 2))

    def test_components_split_by_wall(self):
        c = _synthetic_cache()
        t2c, comps = c._components(0, 0)
        self.assertEqual(len(comps), 2)
        self.assertNotEqual(t2c[(1, 0)], t2c[(3, 0)])  # wall separates them

    def test_first_hop_toward_goal_behind_intermediate_map(self):
        # From MapA comp0, the only route to MapC is via MapB; the first hop
        # must be MapA's (0,0)->MapB warp, not the tile-unreachable (4,0).
        c = _synthetic_cache()
        tiles, dest = c.region_route_targets(0, 0, (1, 0), (0, 2), [(0, 2)])
        self.assertEqual(tiles, {(0, 0)})
        self.assertEqual(dest.replace("_", "").lower(), "mapb")

    def test_same_component_direct_goal(self):
        # Standing already in comp1, the goal warp (4,0)->MapC is right here.
        c = _synthetic_cache()
        tiles, dest = c.region_route_targets(0, 0, (3, 0), (0, 2), [(0, 2)])
        self.assertEqual(tiles, {(4, 0)})
        self.assertEqual(dest.replace("_", "").lower(), "mapc")

    def test_unresolvable_goal_returns_empty_for_fallback(self):
        # A goal map with no warp route -> (set(), None) so the caller falls
        # back to legacy connection/warp routing.
        c = _synthetic_cache()
        tiles, dest = c.region_route_targets(0, 0, (1, 0), (9, 9), [(9, 9)])
        self.assertEqual(tiles, set())
        self.assertIsNone(dest)


def _hole_geyser_cache(with_ledge=True):
    """Lavaridge-style hole/geyser pair puzzle in miniature (Segment 4b).

    GymF (0,0): comp0 {(0,0),(1,0)} entrance | wall (2,0) | comp1
    {(3,0),(4,0)} goal room. GymB (0,1): comp0 {(0,0),(1,0)} | ledge tile
    (2,0) JUMP_EAST | comp1 {(3,0),(4,0)}. Active hole GymF(0,0) drops onto
    inert pad GymB(0,0); the ONLY way into GymB comp1 is the one-way ledge;
    active geyser GymB(4,0) launches onto inert pad GymF(3,0) in the goal
    room. Mirrors the real gym's circular-looking static data: the goal
    room's own warp_events entry is inert, so warp-events alone never
    reach it."""
    cache = MapCache()
    gymf = _mapinfo(0, 0, "GymF", [[0, 0, 1, 0, 0]],
                    [(0, 0, "GymB", 0), (3, 0, "GymB", 1)])
    gymb = _mapinfo(0, 1, "GymB", [[0, 0, 1, 0, 0]],
                    [(0, 0, "GymF", 0), (4, 0, "GymF", 1)])
    cache._maps = {(0, 0): gymf, (0, 1): gymb}
    cache._map_groups = [["GymF", "GymB"]]
    cache._loaded_index = True
    b1f_grid = {(0, 0): 0x00, (4, 0): 0x29}  # pad / geyser
    if with_ledge:
        b1f_grid[(2, 0)] = 0x38  # JUMP_EAST over the wall tile
    cache._behavior_grid_cache = {
        (0, 0): {(0, 0): 0x68, (3, 0): 0x00},  # hole / pad
        (0, 1): b1f_grid,
    }
    return cache


class HoleGeyserPuzzleTests(unittest.TestCase):
    def test_inert_warp_filtered_from_warp_tiles(self):
        c = _hole_geyser_cache()
        # GymB -> GymF: only the firing geyser (4,0); the behavior-0 landing
        # pad (0,0) must be excluded.
        self.assertEqual(c.warp_tiles_for(0, 1, "GymF"), {(4, 0)})

    def test_ledge_component_edge_derived(self):
        c = _hole_geyser_cache()
        t2c, _ = c._components(0, 1)
        self.assertEqual(
            c._ledge_component_edges(0, 1),
            [(t2c[(1, 0)], t2c[(3, 0)])],
        )

    def test_same_map_component_target_routes_via_other_floor(self):
        # Standing in GymF comp0, goal tile (4,0) in walled-off comp1: the
        # route is hole (0,0) -> GymB pad -> ledge jump -> geyser (4,0) ->
        # goal room. First hop must be the hole.
        c = _hole_geyser_cache()
        tiles, dest = c.region_route_targets(
            0, 0, (1, 0), (0, 0), None, target_tile=(4, 0))
        self.assertEqual(tiles, {(0, 0)})
        self.assertEqual(dest.replace("_", "").lower(), "gymb")

    def test_same_component_goal_needs_no_warp(self):
        # Already in the goal component -> empty (plain tile BFS covers it);
        # must NOT return a vacuous same-map warp loop.
        c = _hole_geyser_cache()
        tiles, dest = c.region_route_targets(
            0, 0, (3, 0), (0, 0), None, target_tile=(4, 0))
        self.assertEqual(tiles, set())
        self.assertIsNone(dest)

    def test_without_ledge_goal_component_unreachable(self):
        # Removing the ledge closes the only entrance to GymB comp1 — the
        # router must report no route (ledge edges are load-bearing).
        c = _hole_geyser_cache(with_ledge=False)
        tiles, dest = c.region_route_targets(
            0, 0, (1, 0), (0, 0), None, target_tile=(4, 0))
        self.assertEqual(tiles, set())
        self.assertIsNone(dest)


def _cave_cache_ok():
    try:
        c = md.get_cache()
        return c.get(24, 7) is not None and c.get(24, 10) is not None
    except Exception:
        return False


@unittest.skipUnless(_cave_cache_ok(), "Granite Cave map cache not present")
class GraniteCaveIntegrationTests(unittest.TestCase):
    def test_entrance_to_stevens_room_chain(self):
        # Simulate walking each region first-hop warp and teleporting to its
        # landing until StevensRoom (24,10) is reached. Proves the real
        # entrance -> B1F -> B2F -> B1F -> Steven-region -> (5,10) chain and
        # that the region BFS terminates.
        c = md.get_cache()
        cur, pos, goal = (24, 7), (37, 11), (24, 10)
        seq = []
        for _ in range(12):
            mh = c.map_path(cur[0], cur[1], goal[0], goal[1]) or []
            tiles, dest = c.region_route_targets(cur[0], cur[1], pos, goal, mh)
            self.assertTrue(tiles, f"no region route at {cur} {pos}")
            wt = sorted(tiles)[0]
            seq.append((cur, wt))
            info = c.get(*cur)
            warp = next(w for w in info.warps
                        if (w["x"], w["y"]) == wt)
            land = c._warp_landing(warp)
            self.assertIsNotNone(land)
            cur = (land[0], land[1])
            dw = c.get(*cur).warps[warp["dest_warp_id"]]
            pos = (dw["x"], dw["y"])
            if cur == goal:
                break
        self.assertEqual(cur, goal, f"did not reach StevensRoom; seq={seq}")
        # first hop leaves the entrance floor for B1F
        self.assertEqual(seq[0][1], (17, 11))

    def test_cave_floors_gate_on_common_maps_off(self):
        c = md.get_cache()
        for g, n in [(24, 7), (24, 8), (24, 9)]:
            self.assertTrue(c.has_multiple_warp_components(g, n))
        for g, n in [(24, 10), (0, 11), (0, 21), (3, 3)]:
            self.assertFalse(c.has_multiple_warp_components(g, n))


def _lavaridge_cache_ok():
    try:
        c = md.get_cache()
        if c.get(4, 1) is None or c.get(4, 2) is None:
            return False
        return bool(c.behavior_grid(4, 1)) and bool(c.behavior_grid(4, 2))
    except Exception:
        return False


@unittest.skipUnless(_lavaridge_cache_ok(), "Lavaridge Gym map cache not present")
class LavaridgeGymIntegrationTests(unittest.TestCase):
    """Segment 4b: the real Lavaridge Gym hole/geyser data. Coordinates are
    canon pokeemerald data assertions (map.json / metatile_attributes.bin),
    same style as the Granite Cave suite above."""

    def test_hole_geyser_behavior_split(self):
        c = md.get_cache()
        g1, g2 = c.behavior_grid(4, 1), c.behavior_grid(4, 2)
        i1, i2 = c.get(4, 1), c.get(4, 2)
        # 1F: 20 firing holes (MB 0x68) + 4 inert pads (+2 town-exit arrow
        # warps); B1F: 15 firing geysers (MB 0x29) + 9 inert pads.
        holes = [w for w in i1.warps if g1.get((w["x"], w["y"])) == 0x68]
        pads1 = [w for w in i1.warps if g1.get((w["x"], w["y"])) == 0]
        geysers = [w for w in i2.warps if g2.get((w["x"], w["y"])) == 0x29]
        pads2 = [w for w in i2.warps if g2.get((w["x"], w["y"])) == 0]
        self.assertEqual(len(holes), 20)
        self.assertEqual(len(pads1), 4)
        self.assertEqual(len(geysers), 15)
        self.assertEqual(len(pads2), 9)
        for w in pads1:
            self.assertFalse(c._warp_active(4, 1, w))
        for w in geysers:
            self.assertTrue(c._warp_active(4, 2, w))

    def test_b1f_ledge_row_is_only_entrance_to_final_geyser_room(self):
        # The (12,12) geyser (the only warp landing in Flannery's walled
        # room) sits in a B1F component with no firing inbound warp; the
        # jump row above it must provide the ledge edge into it.
        c = md.get_cache()
        t2c, _ = c._components(4, 2)
        room = t2c[(12, 12)]
        self.assertEqual(
            [w for w in c._warps_in_component(4, 2, room)],
            [w for w in c.get(4, 2).warps if (w["x"], w["y"]) == (12, 12)],
        )
        self.assertIn((t2c[(10, 9)], room), c._ledge_component_edges(4, 2))

    def test_entrance_to_flannery_component_ride_chain(self):
        # Simulate the full solve: from the gym entrance, repeatedly walk to
        # the region router's first-hop warp (tile-BFS with ledges must reach
        # it) and teleport to its landing, until standing in Flannery's
        # component of 1F. Proves holes, geysers, the B1F jump row, and the
        # component-targeted region BFS compose into the real solution.
        c = md.get_cache()
        t2c_1f, _ = c._components(4, 1)
        goal_cid = t2c_1f[(13, 10)]  # walkable tile in front of Flannery
        cur, pos = (4, 1), (13, 18)  # town entrance door tile
        seq = []
        for _ in range(20):
            if cur == (4, 1) and t2c_1f.get(pos) == goal_cid:
                break
            mh = c.map_path(cur[0], cur[1], 4, 1) or []
            tiles, _dest = c.region_route_targets(
                cur[0], cur[1], pos, (4, 1), mh, target_tile=(13, 9))
            self.assertTrue(tiles, f"no region route at {cur} {pos}; {seq}")
            wt = sorted(tiles)[0]
            ledges = {
                (x, y): md.LEDGE_JUMP_BEHAVIORS[bv]
                for (x, y), bv in c.behavior_grid(*cur).items()
                if bv in md.LEDGE_JUMP_BEHAVIORS
            }
            walk = c.bfs_to_tile(cur[0], cur[1], pos, {wt},
                                 ledge_jumps=ledges)
            self.assertIsNotNone(
                walk, f"can't walk {pos}->{wt} on {cur}; {seq}")
            seq.append((cur, pos, wt))
            info = c.get(*cur)
            warp = next(w for w in info.warps if (w["x"], w["y"]) == wt)
            cur = c.find_map_by_name(warp["dest_map"])
            dw = c.get(*cur).warps[warp["dest_warp_id"]]
            pos = (dw["x"], dw["y"])
        self.assertEqual(cur, (4, 1), f"seq={seq}")
        self.assertEqual(t2c_1f.get(pos), goal_cid, f"seq={seq}")
        # sanity: the ride chain is a real multi-warp solve, not a lucky hop
        self.assertGreaterEqual(len(seq), 4)


if __name__ == "__main__":
    unittest.main()
