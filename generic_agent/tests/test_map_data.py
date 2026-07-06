"""map_data.warp_step_direction の決定的テスト。

ネットワーク / map cache 不要: 合成 MapInfo を注入して pure ロジックを検証する。
INVARIANTS: door の進入方向は「マップ上の y 位置」ではなく collision(歩ける
接近側 → 塞がれたドア側)から導く。旧「y*2>height→Down」中点ルールは下半分の
建物ドア(Dewford Gym (8,17) を含む監査済み 127 warp)で誤っていた回帰を防ぐ。
"""
from __future__ import annotations

import unittest

from generic_agent.map_data import MapCache, MapInfo


def _grid(width: int, height: int, blocked: set[tuple[int, int]]) -> list[list[int]]:
    """collision[y][x]: 0=walkable, 1=wall。blocked の (x,y) のみ壁。"""
    return [
        [1 if (x, y) in blocked else 0 for x in range(width)]
        for y in range(height)
    ]


def _cache_with(info: MapInfo) -> MapCache:
    """__init__(mkdir/network) を回避して 1 マップだけ注入した MapCache。"""
    mc = MapCache.__new__(MapCache)
    mc._maps = {(info.map_g, info.map_n): info}
    mc._layouts = {}
    mc._map_groups = []
    mc._loaded_index = True
    return mc


class WarpStepDirectionTest(unittest.TestCase):
    def _info(self, width, height, warps, blocked):
        return MapInfo(
            map_g=0, map_n=0, name="Synthetic", layout_id="L",
            width=width, height=height,
            collision=_grid(width, height, blocked),
            warps=warps,
        )

    def test_building_door_lower_half_returns_up(self):
        # Regression: Dewford Gym door (8,17) in a 20-tall map. Wall above,
        # walkable below → enter with Up. Old midpoint rule said "Down".
        info = self._info(
            20, 20,
            warps=[{"x": 8, "y": 17, "dest_map": "SomeGym"}],
            blocked={(8, 16), (8, 17)},  # door tile + wall above blocked
        )
        mc = _cache_with(info)
        self.assertEqual(mc.warp_step_direction(0, 0, 8, 17), "Up")

    def test_building_door_upper_half_returns_up(self):
        info = self._info(
            20, 20,
            warps=[{"x": 8, "y": 3, "dest_map": "SomeMart"}],
            blocked={(8, 2), (8, 3)},
        )
        mc = _cache_with(info)
        self.assertEqual(mc.warp_step_direction(0, 0, 8, 3), "Up")

    def test_forest_south_exit_still_returns_down(self):
        # Petalburg-Woods-style south exit: walkable above, blocked below →
        # you LEAVE by pressing Down. Must not regress.
        info = self._info(
            25, 40,
            warps=[{"x": 16, "y": 38, "dest_map": "Route102"}],
            blocked={(16, 39)},  # nothing walkable below the exit
        )
        mc = _cache_with(info)
        self.assertEqual(mc.warp_step_direction(0, 0, 16, 38), "Down")

    def test_ambiguous_both_open_falls_back_to_midpoint(self):
        # Stairs/pad with both vertical neighbours walkable: keep the old
        # deterministic midpoint fallback (direction is harmless for step-on
        # warps but must not crash / must be stable).
        info = self._info(
            20, 20,
            warps=[{"x": 5, "y": 5, "dest_map": "Upstairs"}],
            blocked=set(),  # everything walkable
        )
        mc = _cache_with(info)
        # y=5, y*2=10 !> height 20 → "Up"
        self.assertEqual(mc.warp_step_direction(0, 0, 5, 5), "Up")

    def test_approach_tile_below_door_returns_up(self):
        # Standing on the walkable approach tile directly below a door warp
        # (the tile warp_tiles_for routes to) → step Up into the door.
        info = self._info(
            20, 20,
            warps=[{"x": 8, "y": 17, "dest_map": "SomeGym"}],
            blocked={(8, 16), (8, 17)},
        )
        mc = _cache_with(info)
        self.assertEqual(mc.warp_step_direction(0, 0, 8, 18), "Up")

    def test_edge_warp_unaffected(self):
        # Bottom-edge warp (map boundary) always "Down" regardless of collision.
        info = self._info(
            20, 20,
            warps=[{"x": 8, "y": 19, "dest_map": "Route"}],
            blocked=set(),
        )
        mc = _cache_with(info)
        self.assertEqual(mc.warp_step_direction(0, 0, 8, 19), "Down")


if __name__ == "__main__":
    unittest.main()
