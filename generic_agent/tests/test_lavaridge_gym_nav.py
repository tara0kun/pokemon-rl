"""Lavaridge Gym hole/geyser autonomous-nav regression (2026-07-24).

The 07-24 session oscillated 1F <-> B1F for ~70 turns and never reached
Flannery (1F (13,9), comp6). Three interacting defects, root-caused offline
from the decision log + cached canon data:

1. RIDE LOOP (turns 228-252): stepping the region route's own hole 1F (8,9)
   reads as still-on-1F mid-fall; BFS-to-Flannery is None, the region
   fallback's target IS the tile we stand on (empty path), the stale
   `(gs.x,gs.y) in target_tiles` check misses (target_tiles = Flannery
   neighbours, not the region tiles) and control fell to goal_map_explore,
   whose stray Up landed the B1F (8,6-8) dead-end pocket -> path_memory rode
   the geyser straight back up. Fix: region_warp_settle ("B") when standing
   on a region first-hop warp tile.
2. GOAL FLICKER: every backward `mapbfs:Down->...B1f` turn in the log carried
   lvl=0 (DMA misread) which resurrected the retired grind_fiery_path goal.
   Fix: state._flicker_guarded_level (tested in test_state).
3. PHANTOM MENU: menu_open() white-ratio false-positive on the gym's pale
   floor B-spammed ~45 turns (cb2 stayed CB2_Overworld throughout). Fix:
   dark-text requirement (tested in test_screen_features).

These tests pin the canon-derived warp chain (entrance -> Flannery through 8
hole/geyser rides + the comp4->comp6 ledge) and the settle behavior through
the REAL heuristic_button. Offline: reads only cached map_cache /
map_knowledge files; skips without the cache.
"""
from __future__ import annotations

import unittest

from generic_agent import (
    claude_heuristic as ch,
    config,
    goals as goals_mod,
    map_data as md,
    map_knowledge as mk_mod,
    path_memory as path_memory_mod,
    tile_map as tile_map_mod,
)
from generic_agent.state import GameState

G1 = (4, 1)   # LavaridgeTown_Gym_1F
GB = (4, 2)   # LavaridgeTown_Gym_B1F
FLANNERY = (13, 9)


def _have_cache() -> bool:
    return all(
        (config.MEMORY_DIR / "map_cache" / n).exists()
        for n in (
            "LavaridgeTown_Gym_1F.map.bin", "LavaridgeTown_Gym_1F.map.json",
            "LavaridgeTown_Gym_B1F.map.bin", "LavaridgeTown_Gym_B1F.map.json",
            "secondary_lavaridge_gym_attr.bin",
        )
    )


_HAVE = _have_cache()


@unittest.skipUnless(_HAVE, "Lavaridge gym canon cache not present")
class TestLavaridgeRegionChain(unittest.TestCase):
    """The (map, component) warp graph must emit the real gym solution:
    each landing's first-hop target is the NEXT ride of the chain, so
    per-landing re-planning strictly progresses (no oscillation)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mc = md.get_cache()

    def _first_hop(self, key, pos):
        tiles, _ = self.mc.region_route_targets(
            key[0], key[1], pos, G1, [G1], target_tile=FLANNERY,
        )
        return tiles

    def test_flannery_room_is_a_separate_component(self) -> None:
        t2c, _ = self.mc._components(*G1)
        self.assertNotEqual(t2c.get(FLANNERY), t2c.get((13, 18)))

    def test_chain_hops_progress(self) -> None:
        # (map, landing tile) -> expected next ride (the canon warp anchor).
        # Landings are the dest_warp anchors from the cached map.json; the
        # expected targets are what the region BFS derives from the same
        # data, pinned so a router regression that re-oscillates fails here.
        chain = [
            (G1, (13, 18), {(8, 9)}),    # entrance comp5 -> hole
            (GB, (8, 9), {(0, 17)}),     # corridor comp3 -> west geyser
            (G1, (1, 17), {(0, 10)}),    # NW comp3 -> hole
            (GB, (0, 10), {(0, 6)}),     # comp1 -> geyser
            (G1, (1, 6), {(2, 3)}),      # comp0 -> hole
            (GB, (2, 3), {(7, 2)}),      # comp0 -> geyser
            (G1, (8, 2), {(10, 6)}),     # comp1 -> hole
            (GB, (10, 6), {(12, 12)}),   # comp4 -> (ledge) -> comp6 geyser
        ]
        for key, pos, want in chain:
            self.assertEqual(self._first_hop(key, pos), want, (key, pos))

    def test_each_first_hop_walk_reachable_with_ledges(self) -> None:
        # bfs_to_tile must reach every first-hop tile from its landing with
        # OTHER warp tiles blocked (decide()'s other_warps model), using the
        # canon ledges — the comp4 -> comp6 leg only exists via the
        # (10-12,10) JUMP_SOUTH row.
        chain = [
            (G1, (13, 18)), (GB, (8, 9)), (G1, (1, 17)), (GB, (0, 10)),
            (G1, (1, 6)), (GB, (2, 3)), (G1, (8, 2)), (GB, (10, 6)),
        ]
        for key, pos in chain:
            tiles = self._first_hop(key, pos)
            self.assertTrue(tiles, (key, pos))
            info = self.mc.get(*key)
            ledges = {
                t: md.LEDGE_JUMP_BEHAVIORS[bv]
                for t, bv in self.mc.behavior_grid(*key).items()
                if bv in md.LEDGE_JUMP_BEHAVIORS
            }
            other_warps = {
                (w["x"], w["y"]) for w in info.warps
                if (w["x"], w["y"]) not in tiles
            }
            path = self.mc.bfs_to_tile(
                key[0], key[1], pos, tiles,
                blocked_tiles=other_warps, ledge_jumps=ledges,
            )
            self.assertIsNotNone(path, (key, pos))

    def test_final_room_reaches_flannery_neighbour(self) -> None:
        # After the comp6 geyser the player lands inside Flannery's room;
        # her interaction tile below her must be plain-walk reachable.
        info = self.mc.get(*G1)
        self.assertTrue(info.walkable(13, 10))
        path = self.mc.bfs_to_tile(
            G1[0], G1[1], (13, 12), {(13, 10)},
            blocked_tiles={(12, 12)},  # the room's own hole
        )
        self.assertIsNotNone(path)


@unittest.skipUnless(_HAVE, "Lavaridge gym canon cache not present")
class TestRegionWarpSettle(unittest.TestCase):
    """Standing ON the region route's hole mid-fall must settle with B —
    through the REAL heuristic_button. Pre-fix this fell through to
    goal_map_explore (stray Up -> the B1F pocket -> geyser back up: the
    07-24 ride loop)."""

    def test_settle_on_region_hole(self) -> None:
        gs = GameState(
            G1[0], G1[1], 8, 9,
            saveblock1_valid=True,
            in_battle=False,
            party_count=1,
            party0_level=46,
            party0_hp=143,
            party0_max_hp=149,
            badge_count=3,
        )
        goal = goals_mod.Goal(
            name="lavaridge_gym_flannery",
            target_map=G1,
            target_pos=FLANNERY,
            condition="lavaridge_gym_flannery",
            desc="test",
        )
        button, src = ch.heuristic_button(
            gs,
            tile_map_mod.TileMap(),
            path_memory_mod.TransitionMemory(),
            map_visit_counts={},
            same_pos_streak=1,
            same_hash_streak=0,
            same_map_streak=5,
            last_pos=(9, 9),
            last_action="Left",
            recent_pos=[],
            battle_turn=0,
            escape_dir_index=0,
            screen_signals={},
            current_goal=goal,
            client=None,
        )
        self.assertEqual((button, src), ("B", "region_warp_settle"))


if __name__ == "__main__":
    unittest.main()
