"""goals.py の goal 選択ロジックのテスト。

実 memory/ を汚さないよう、モジュールの永続パスを一時 dir に差し替える。
ストーリー進行のリグレッション (Dewford chain / peeko latch / visited 抑制)
を守るのが目的。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from generic_agent import goals as goals_mod
from generic_agent.state import GameState


def make_gs(**kw) -> GameState:
    base = dict(
        map_group=0, map_num=10, x=5, y=5, saveblock1_valid=True,
        party_count=1, badge_count=0, total_event_flags=0,
        event_flag_bytes_hex="",
    )
    base.update(kw)
    return GameState(**base)


class GoalsTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._orig = (
            goals_mod.GOALS_FILE,
            goals_mod.VISITED_MAPS_FILE,
            goals_mod.PEEKO_DONE_MARKER,
            goals_mod.LETTER_DONE_MARKER,
            goals_mod.DEVON_DELIVERED_MARKER,
        )
        goals_mod.GOALS_FILE = tmp / "goal_notes.jsonl"
        goals_mod.VISITED_MAPS_FILE = tmp / "visited_maps.json"
        goals_mod.PEEKO_DONE_MARKER = tmp / "peeko_done.marker"
        goals_mod.LETTER_DONE_MARKER = tmp / "steven_letter_done.marker"
        goals_mod.DEVON_DELIVERED_MARKER = tmp / "devon_delivered.marker"

    def tearDown(self) -> None:
        (
            goals_mod.GOALS_FILE,
            goals_mod.VISITED_MAPS_FILE,
            goals_mod.PEEKO_DONE_MARKER,
            goals_mod.LETTER_DONE_MARKER,
            goals_mod.DEVON_DELIVERED_MARKER,
        ) = self._orig
        self._tmp.cleanup()


class TestEarlyGame(GoalsTestBase):
    def test_no_party_targets_birch_lab(self) -> None:
        g = goals_mod.current_goal(make_gs(party_count=0))
        self.assertIsNotNone(g)
        self.assertEqual(g.name, "get_starter_via_lab")
        self.assertEqual(g.target_map, (1, 4))

    def test_visited_map_suppression_skips_cleared_waypoint(self) -> None:
        # Oldale (0,10) を訪問済みにすると reach_oldale は抑制され、
        # 次の waypoint (Route103 rival) が選ばれる
        gs = make_gs(map_group=0, map_num=17)  # Route102 にいる
        g_before = goals_mod.current_goal(gs)
        self.assertEqual(g_before.name, "reach_oldale")
        goals_mod.record_map_visit(0, 10)
        g_after = goals_mod.current_goal(gs)
        self.assertEqual(g_after.name, "reach_route_103_rival")


class TestPeekoLatch(GoalsTestBase):
    """DMA flicker 対策の disk latch (INVARIANTS B-6)。"""

    def test_flag_true_creates_marker_and_latches(self) -> None:
        gs_flag_on = make_gs(flag_devon_goods_recovered=True)
        self.assertTrue(goals_mod._peeko_done(gs_flag_on))
        self.assertTrue(goals_mod.PEEKO_DONE_MARKER.exists())
        # 以降 flag が flicker で False に見えても True のまま
        gs_flag_off = make_gs(flag_devon_goods_recovered=False)
        self.assertTrue(goals_mod._peeko_done(gs_flag_off))

    def test_no_flag_no_marker_is_false(self) -> None:
        self.assertFalse(goals_mod._peeko_done(make_gs()))


class TestDewfordChain(GoalsTestBase):
    def setUp(self) -> None:
        super().setUp()
        goals_mod.PEEKO_DONE_MARKER.write_text("1", encoding="utf-8")

    def test_in_dewford_town_targets_brawly_gym(self) -> None:
        # L30+ precondition (stat lead): only then is Brawly the goal.
        gs = make_gs(map_group=0, map_num=11, badge_count=1, party0_level=30)
        g = goals_mod.current_goal(gs)
        self.assertEqual(g.name, "dewford_gym_brawly")
        self.assertEqual(g.target_map, (3, 3))

    def test_inside_gym_targets_brawly_tile(self) -> None:
        gs = make_gs(map_group=3, map_num=3, badge_count=1, party0_level=30)
        g = goals_mod.current_goal(gs)
        self.assertEqual(g.name, "dewford_gym_brawly")
        self.assertEqual(g.target_pos, (4, 3))

    def test_gym_goal_survives_visited_suppression(self) -> None:
        # gym map を一度訪問しても badge を取るまで goal は生き続ける
        goals_mod.record_map_visit(3, 3)
        gs = make_gs(map_group=0, map_num=11, badge_count=1, party0_level=30)
        g = goals_mod.current_goal(gs)
        self.assertEqual(g.name, "dewford_gym_brawly")

    def test_under_l30_grinds_instead_of_brawly(self) -> None:
        # H6b: below L30 the grind goal owns nav, NOT Brawly —
        # else Grovyle throws itself at Brawly's Bulk Up Makuhita and loses.
        at_town = make_gs(map_group=0, map_num=11, badge_count=1, party0_level=24)
        g_town = goals_mod.current_goal(at_town)
        self.assertEqual(g_town.name, "grind_granite_cave")
        # grind pins to Granite Cave 1F, NOT Route106: Route106 has no land
        # encounters (water-only), so its grass pin never triggered a battle.
        self.assertEqual(g_town.target_map, (24, 7))
        # inside the cave it must STAY and pace on the encounter tile (target_pos
        # on the current map), not route back out.
        in_cave = make_gs(map_group=24, map_num=7, badge_count=1, party0_level=24)
        g_cave = goals_mod.current_goal(in_cave)
        self.assertEqual(g_cave.name, "grind_granite_cave")
        self.assertEqual(g_cave.target_pos, (27, 7))

    def test_fall_to_b1f_routes_back_up_to_grind(self) -> None:
        # A fall down the ladder to the dark B1F (24,8) must keep the grind goal
        # matching so mapbfs routes the lead back up to 1F, instead of leaving
        # it goal-less and wandering the requires_flash sublevel.
        in_b1f = make_gs(map_group=24, map_num=8, badge_count=1, party0_level=24)
        g = goals_mod.current_goal(in_b1f)
        self.assertEqual(g.name, "grind_granite_cave")
        self.assertEqual(g.target_map, (24, 7))

    def test_low_hp_routes_to_pc_nurse(self) -> None:
        # A hurt lead heals at the Dewford PC before resuming the grind.
        gs = make_gs(
            map_group=24, map_num=7, badge_count=1, party0_level=24,
            party0_hp=10, party0_max_hp=66,
        )
        g = goals_mod.current_goal(gs)
        self.assertEqual(g.name, "heal_at_dewford_pc")
        self.assertEqual(g.target_map, (3, 1))

    def test_badge2_delivers_steven_letter(self) -> None:
        # Post-Brawly (H4): with the Letter undelivered, the goal is to bring it
        # to Steven in GraniteCave_StevensRoom (24,10) — the hard gate for the
        # Slateport sail. Reached directly from cave 1F, both bright maps.
        gs = make_gs(map_group=0, map_num=11, badge_count=2,
                     flag_steven_letter_delivered=False)
        g = goals_mod.current_goal(gs)
        self.assertEqual(g.name, "deliver_steven_letter")
        self.assertEqual(g.target_map, (24, 10))
        self.assertEqual(g.target_pos, (7, 8))  # Steven's NPC tile (interact + face)

    def test_letter_delivered_sails_to_slateport(self) -> None:
        # Letter delivered (Slateport gate open) but Devon Goods not yet handed
        # over -> talk to Mr.Briney at Dewford (12,9) for the Slateport sail.
        gs = make_gs(map_group=0, map_num=11, badge_count=2,
                     flag_steven_letter_delivered=True,
                     flag_devon_goods_delivered=False)
        g = goals_mod.current_goal(gs)
        self.assertEqual(g.name, "sail_to_slateport")
        self.assertEqual(g.target_map, (0, 11))
        self.assertEqual(g.target_pos, (12, 9))

    def test_route109_landing_reaches_slateport(self) -> None:
        # The sail lands on Route109 (0,24); the sail goal is gated to the
        # Dewford side so it goes silent and reach_slateport takes over north.
        gs = make_gs(map_group=0, map_num=24, badge_count=2,
                     flag_steven_letter_delivered=True,
                     flag_devon_goods_delivered=False)
        g = goals_mod.current_goal(gs)
        self.assertEqual(g.name, "reach_slateport")
        self.assertEqual(g.target_map, (0, 1))

    def test_slateport_devon_dock_first(self) -> None:
        # At Slateport, letter delivered, Devon Goods not handed over and the Dock
        # not yet talked to -> go to the Shipyard Dock (9,0)/(5,5) first.
        gs = make_gs(map_group=0, map_num=1, badge_count=2,
                     flag_steven_letter_delivered=True,
                     flag_devon_goods_delivered=False,
                     flag_dock_rejected_devon=False)
        g = goals_mod.current_goal(gs)
        self.assertEqual(g.name, "deliver_devon_dock")
        self.assertEqual(g.target_map, (9, 0))
        self.assertEqual(g.target_pos, (5, 5))

    def test_slateport_devon_goods_after_dock(self) -> None:
        # After the Dock redirect (flag 0x94 set), deliver to Capt.Stern on the
        # Oceanic Museum 2F (9,8)/(13,6).
        gs = make_gs(map_group=0, map_num=1, badge_count=2,
                     flag_steven_letter_delivered=True,
                     flag_dock_rejected_devon=True,
                     flag_devon_goods_delivered=False)
        g = goals_mod.current_goal(gs)
        self.assertEqual(g.name, "deliver_devon_goods")
        self.assertEqual(g.target_map, (9, 8))
        self.assertEqual(g.target_pos, (13, 6))

    def test_devon_goods_delivered_retires_slateport_chain(self) -> None:
        # After the Devon Goods are delivered the whole Slateport chain (sail /
        # reach / dock / deliver) deactivates (Mauville chain unimplemented -> None).
        gs = make_gs(map_group=0, map_num=1, badge_count=2,
                     flag_steven_letter_delivered=True,
                     flag_dock_rejected_devon=True,
                     flag_devon_goods_delivered=True)
        self.assertIsNone(goals_mod.current_goal(gs))

    def test_badge2_low_hp_heals_before_cave_trek(self) -> None:
        # A near-dead L30 lead (post-Brawly 2/86) must heal at the Dewford PC
        # before walking the encounter-filled cave to Steven — else it faints on
        # a wild step and whiteouts. heal goal now covers badge>=2.
        gs = make_gs(map_group=24, map_num=7, badge_count=2,
                     party0_hp=2, party0_max_hp=86,
                     flag_steven_letter_delivered=False)
        self.assertEqual(goals_mod.current_goal(gs).name, "heal_at_dewford_pc")

    def test_route104_north_south_split(self) -> None:
        # 同一 map (0,19) でも y 座標で goal が分岐 (INVARIANTS C-18)
        north = make_gs(map_group=0, map_num=19, y=10, badge_count=1)
        south = make_gs(map_group=0, map_num=19, y=40, badge_count=1)
        self.assertEqual(goals_mod.current_goal(north).name, "dewford_to_woods")
        self.assertEqual(goals_mod.current_goal(south).name, "dewford_to_briney")


if __name__ == "__main__":
    unittest.main()
