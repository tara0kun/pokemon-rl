"""Connections clobber fix (2026-07-17). A map side can hold more than one
connection (Route111 left = Route113 offset0 + Route112 offset20); a
dict[direction] kept only the last and dropped Route113 -> the Fallarbor route
never planned. These tests lock in the dict[direction]->list[conn] model, the
per-dest exit strip, and the banned-first-hop replan. mGBA/cache-free.
"""
from __future__ import annotations

import unittest

from generic_agent import map_data as md
from generic_agent.map_data import MapCache, MapInfo


def _grid(width, height, blocked):
    return [
        [1 if (x, y) in blocked else 0 for x in range(width)]
        for y in range(height)
    ]


class ParseConnectionsTest(unittest.TestCase):
    def test_same_direction_keeps_all(self):
        raw = [
            {"map": "MAP_MAUVILLE_CITY", "offset": 0, "direction": "down"},
            {"map": "MAP_ROUTE113", "offset": 0, "direction": "left"},
            {"map": "MAP_ROUTE112", "offset": 20, "direction": "left"},
        ]
        conns = md._parse_connections(raw)
        # the whole point: BOTH left connections survive, in canonical order
        self.assertEqual([c["map_name"] for c in conns["left"]],
                         ["Route113", "Route112"])
        self.assertEqual(conns["left"][0]["offset"], 0)
        self.assertEqual(conns["left"][1]["offset"], 20)
        self.assertEqual([c["map_name"] for c in conns["down"]], ["MauvilleCity"])

    def test_empty_and_none(self):
        self.assertEqual(md._parse_connections(None), {})
        self.assertEqual(md._parse_connections([]), {})

    def test_norm_map_name(self):
        self.assertEqual(md._norm_map_name("MAP_ROUTE_111"), "Route111")
        self.assertEqual(md._norm_map_name("MAP_MAUVILLE_CITY"), "MauvilleCity")


class ExitStripTest(unittest.TestCase):
    """Two left connections whose destination strips are offset-disjoint; each
    dest_name must yield only its own strip, never the union."""

    def _cache(self):
        # src 6 wide x 40 tall. left edge x=0 walkable at y in {5..8} and {25..28}.
        walk_ys = {5, 6, 7, 8, 25, 26, 27, 28}
        src = MapInfo(
            map_g=0, map_n=0, name="Src", layout_id="L",
            width=6, height=40,
            collision=_grid(6, 40, {(0, y) for y in range(40)} - {(0, y) for y in walk_ys}),
            connections={"left": [
                {"map_name": "DestA", "offset": 0},   # dest_y = src_y
                {"map_name": "DestB", "offset": 20},  # dest_y = src_y - 20
            ]},
        )
        # DestA: right column walkable at y 5..8 (matches src 5..8 at offset 0)
        destA = MapInfo(
            map_g=0, map_n=1, name="DestA", layout_id="L",
            width=6, height=40,
            collision=_grid(6, 40, {(5, y) for y in range(40)} - {(5, y) for y in {5, 6, 7, 8}}),
        )
        # DestB: right column walkable at y 5..8 (== src 25..28 minus offset 20)
        destB = MapInfo(
            map_g=0, map_n=2, name="DestB", layout_id="L",
            width=6, height=40,
            collision=_grid(6, 40, {(5, y) for y in range(40)} - {(5, y) for y in {5, 6, 7, 8}}),
        )
        mc = MapCache.__new__(MapCache)
        mc._maps = {(0, 0): src, (0, 1): destA, (0, 2): destB}
        mc._layouts = {}
        mc._map_groups = [["Src", "DestA", "DestB"]]
        mc._loaded_index = True
        mc._EMPIRICAL_EXIT_TILES = {}
        return mc

    def test_dest_name_selects_its_own_strip(self):
        mc = self._cache()
        a = mc.exit_tiles_toward(0, 0, "left", dest_name="DestA")
        b = mc.exit_tiles_toward(0, 0, "left", dest_name="DestB")
        self.assertEqual(a, {(0, 5), (0, 6), (0, 7), (0, 8)})
        self.assertEqual(b, {(0, 25), (0, 26), (0, 27), (0, 28)})
        self.assertTrue(a.isdisjoint(b))

    def test_union_without_dest_name(self):
        mc = self._cache()
        both = mc.exit_tiles_toward(0, 0, "left")
        self.assertEqual(
            both,
            {(0, 5), (0, 6), (0, 7), (0, 8), (0, 25), (0, 26), (0, 27), (0, 28)},
        )

    def test_neighbor_maps_sees_both(self):
        mc = self._cache()
        self.assertEqual(mc.neighbor_maps(0, 0), {"DestA", "DestB"})


class MapPathBanTest(unittest.TestCase):
    """banned_first_hops must skip a neighbour only at the start node, so a
    connection-lie / unreachable first hop can be replanned around."""

    def _cache(self):
        def edge(name, g, n, neighbors):
            return MapInfo(
                map_g=g, map_n=n, name=name, layout_id="L",
                width=4, height=4, collision=_grid(4, 4, set()),
                connections={"left": [{"map_name": m, "offset": 0} for m in neighbors]},
            )
        # Start -> {A, B}; A -> Goal (2 hops); B -> Goal (2 hops)
        maps = {
            (0, 0): edge("Start", 0, 0, ["A", "B"]),
            (0, 1): edge("A", 0, 1, ["Goal"]),
            (0, 2): edge("B", 0, 2, ["Goal"]),
            (0, 3): edge("Goal", 0, 3, []),
        }
        mc = MapCache.__new__(MapCache)
        mc._maps = maps
        mc._layouts = {}
        mc._map_groups = [["Start", "A", "B", "Goal"]]
        mc._loaded_index = True
        return mc

    def test_ban_forces_alternate_first_hop(self):
        mc = self._cache()
        # unbanned: A or B first (both length 2) — just assert reachable
        self.assertEqual(len(mc.map_path(0, 0, 0, 3)), 2)
        # ban A -> must go through B
        path = mc.map_path(0, 0, 0, 3, banned_first_hops={"A"})
        self.assertEqual(path[0], (0, 2))  # B
        # ban both first hops -> no path
        self.assertIsNone(
            mc.map_path(0, 0, 0, 3, banned_first_hops={"A", "B"})
        )

    def test_ban_only_applies_to_start(self):
        # Goal reachable only if the ban does NOT block A when A is a 2nd hop.
        # Here Start->B->A->Goal; banning A as a first hop must still allow A
        # as B's neighbour.
        def edge(name, g, n, neighbors):
            return MapInfo(
                map_g=g, map_n=n, name=name, layout_id="L",
                width=4, height=4, collision=_grid(4, 4, set()),
                connections={"left": [{"map_name": m, "offset": 0} for m in neighbors]},
            )
        maps = {
            (0, 0): edge("Start", 0, 0, ["A", "B"]),
            (0, 1): edge("A", 0, 1, ["Goal"]),
            (0, 2): edge("B", 0, 2, ["A"]),
            (0, 3): edge("Goal", 0, 3, []),
        }
        mc = MapCache.__new__(MapCache)
        mc._maps = maps
        mc._layouts = {}
        mc._map_groups = [["Start", "A", "B", "Goal"]]
        mc._loaded_index = True
        path = mc.map_path(0, 0, 0, 3, banned_first_hops={"A"})
        self.assertEqual(path, [(0, 2), (0, 1), (0, 3)])  # Start->B->A->Goal


if __name__ == "__main__":
    unittest.main()
