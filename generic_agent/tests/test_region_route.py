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


if __name__ == "__main__":
    unittest.main()
