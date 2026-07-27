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
            goals_mod.ROCK_SMASH_TAUGHT_MARKER,
            goals_mod.THEFT_DONE_MARKER,
            goals_mod.MTCHIMNEY_DONE_MARKER,
            goals_mod.WATER_CATCH_DONE_MARKER,
        )
        goals_mod.GOALS_FILE = tmp / "goal_notes.jsonl"
        goals_mod.VISITED_MAPS_FILE = tmp / "visited_maps.json"
        goals_mod.PEEKO_DONE_MARKER = tmp / "peeko_done.marker"
        goals_mod.LETTER_DONE_MARKER = tmp / "steven_letter_done.marker"
        goals_mod.DEVON_DELIVERED_MARKER = tmp / "devon_delivered.marker"
        goals_mod.ROCK_SMASH_TAUGHT_MARKER = tmp / "rock_smash_taught.marker"
        goals_mod.THEFT_DONE_MARKER = tmp / "meteor_theft_done.marker"
        goals_mod.MTCHIMNEY_DONE_MARKER = tmp / "mtchimney_done.marker"
        goals_mod.WATER_CATCH_DONE_MARKER = tmp / "water_catch_done.marker"

    def tearDown(self) -> None:
        (
            goals_mod.GOALS_FILE,
            goals_mod.VISITED_MAPS_FILE,
            goals_mod.PEEKO_DONE_MARKER,
            goals_mod.LETTER_DONE_MARKER,
            goals_mod.DEVON_DELIVERED_MARKER,
            goals_mod.ROCK_SMASH_TAUGHT_MARKER,
            goals_mod.THEFT_DONE_MARKER,
            goals_mod.MTCHIMNEY_DONE_MARKER,
            goals_mod.WATER_CATCH_DONE_MARKER,
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

    def test_museum_devon_goods_survives_dock_flag_flicker(self) -> None:
        # On the Oceanic Museum floors (9,7)/(9,8) the deliver goal must NOT
        # depend on the 0x94 dock flag: it DMA-flickers ~37% of frames and a
        # None gap at the single-tile 1F<->2F warp ping-pongs the agent across
        # floors (the explore heuristic routes back onto the warp). Reaching a
        # museum map already proves the Dock was talked, so the goal stays on
        # Stern even when 0x94 momentarily reads False.
        for mid in [(9, 7), (9, 8)]:
            gs = make_gs(map_group=mid[0], map_num=mid[1], badge_count=2,
                         flag_steven_letter_delivered=True,
                         flag_dock_rejected_devon=False,   # simulated flicker
                         flag_devon_goods_delivered=False)
            g = goals_mod.current_goal(gs)
            self.assertIsNotNone(g, f"goal went None at {mid}")
            self.assertEqual(g.name, "deliver_devon_goods", f"at {mid}")
            self.assertEqual(g.target_pos, (13, 6), f"at {mid}")

    def test_low_hp_on_route110_heals_at_slateport(self) -> None:
        # A hurt lead on Route110 (or Slateport) routes to the Slateport PC to
        # heal — preventing a whiteout AND re-homing the whiteout point to the
        # mainland instead of Dewford.
        for mid in [(0, 25), (0, 1)]:
            gs = make_gs(map_group=mid[0], map_num=mid[1], badge_count=2,
                         flag_steven_letter_delivered=True,
                         flag_dock_rejected_devon=True,
                         flag_devon_goods_delivered=True,
                         party0_hp=20, party0_max_hp=93)
            g = goals_mod.current_goal(gs)
            self.assertEqual(g.name, "heal_at_slateport", f"at {mid}")
            self.assertEqual(g.target_map, (9, 11))
            self.assertEqual(g.target_pos, (7, 3))

    def test_healthy_on_route110_pushes_to_mauville(self) -> None:
        # A healthy lead does NOT detour to heal — it keeps pushing to Mauville.
        gs = make_gs(map_group=0, map_num=25, badge_count=2,
                     flag_steven_letter_delivered=True,
                     flag_dock_rejected_devon=True,
                     flag_devon_goods_delivered=True,
                     party0_hp=90, party0_max_hp=93)
        self.assertEqual(goals_mod.current_goal(gs).name, "reach_mauville")

    def test_whiteout_to_dewford_sails_back(self) -> None:
        # Post-delivery, a whiteout strands the agent at Dewford across the sea;
        # sail_to_slateport must re-fire (recovery) so it can get back, even
        # though the Devon Goods are already delivered.
        for mid in [(0, 11), (0, 22)]:  # Dewford Town, Route107
            gs = make_gs(map_group=mid[0], map_num=mid[1], badge_count=2,
                         flag_steven_letter_delivered=True,
                         flag_dock_rejected_devon=True,
                         flag_devon_goods_delivered=True)
            g = goals_mod.current_goal(gs)
            self.assertEqual(g.name, "sail_to_slateport", f"at {mid}")

    def test_devon_goods_delivered_advances_to_mauville(self) -> None:
        # After the Devon Goods are delivered the Slateport delivery chain
        # (sail / reach_slateport / dock / deliver) deactivates and the goal
        # advances to reach_mauville — it must NOT loop the delivery goals nor
        # go None (which stranded the agent at Slateport/museum).
        gs = make_gs(map_group=0, map_num=1, badge_count=2,
                     flag_steven_letter_delivered=True,
                     flag_dock_rejected_devon=True,
                     flag_devon_goods_delivered=True)
        g = goals_mod.current_goal(gs)
        self.assertIsNotNone(g)
        self.assertEqual(g.name, "reach_mauville")
        self.assertNotIn(g.name, {"deliver_devon_dock", "deliver_devon_goods",
                                   "reach_slateport", "sail_to_slateport"})

    def test_delivered_devon_heads_to_mauville_from_museum(self) -> None:
        # The delivery is done (0x95). From the Oceanic Museum floors the goal
        # must now pull the agent OUT toward Mauville (reach_mauville), not go
        # None (which stranded it force-exploring the 2F for 2000+ turns).
        for mid in [(9, 8), (9, 7), (0, 1), (0, 25)]:
            gs = make_gs(map_group=mid[0], map_num=mid[1], badge_count=2,
                         flag_steven_letter_delivered=True,
                         flag_dock_rejected_devon=True,
                         flag_devon_goods_delivered=True)
            g = goals_mod.current_goal(gs)
            self.assertIsNotNone(g, f"goal went None at {mid}")
            self.assertEqual(g.name, "reach_mauville", f"at {mid}")
            self.assertEqual(g.target_map, (0, 2))

    def test_low_hp_in_mauville_gym_heals(self) -> None:
        # A worn-down lead at the Mauville Gym / city heals at the Mauville PC
        # before facing Wattson (the gym has 6 trainers and no in-gym heal).
        for mid in [(10, 0), (0, 2)]:
            gs = make_gs(map_group=mid[0], map_num=mid[1], badge_count=2,
                         flag_steven_letter_delivered=True,
                         flag_dock_rejected_devon=True,
                         flag_devon_goods_delivered=True,
                         party0_hp=2, party0_max_hp=98)
            g = goals_mod.current_goal(gs)
            self.assertEqual(g.name, "heal_at_mauville", f"at {mid}")
            self.assertEqual(g.target_map, (10, 5))
            self.assertEqual(g.target_pos, (7, 3))

    def test_at_mauville_targets_wattson_gym(self) -> None:
        # At Mauville City the gym goal wins over reach_mauville (table order)
        # and routes to the Gym; inside the Gym it targets Wattson's tile.
        at_city = make_gs(map_group=0, map_num=2, badge_count=2,
                          flag_steven_letter_delivered=True,
                          flag_dock_rejected_devon=True,
                          flag_devon_goods_delivered=True)
        g_city = goals_mod.current_goal(at_city)
        self.assertEqual(g_city.name, "mauville_gym_wattson")
        self.assertEqual(g_city.target_map, (10, 0))
        in_gym = make_gs(map_group=10, map_num=0, badge_count=2,
                         flag_steven_letter_delivered=True,
                         flag_dock_rejected_devon=True,
                         flag_devon_goods_delivered=True)
        g_gym = goals_mod.current_goal(in_gym)
        self.assertEqual(g_gym.name, "mauville_gym_wattson")
        self.assertEqual(g_gym.target_pos, (5, 2))  # Wattson NPC tile

    def test_badge3_starts_with_rock_smash(self) -> None:
        # Once the Dynamo Badge is won (badge_count 3) the Lavaridge arc begins,
        # and its FIRST step is getting HM06 Rock Smash (Route111 is gated by a
        # breakable rock) — get_rock_smash before reach_fallarbor.
        gs = make_gs(map_group=0, map_num=2, badge_count=3,
                     flag_steven_letter_delivered=True,
                     flag_dock_rejected_devon=True,
                     flag_devon_goods_delivered=True)
        g = goals_mod.current_goal(gs)
        self.assertEqual(g.name, "get_rock_smash")
        self.assertEqual(g.target_map, (10, 2))
        self.assertEqual(g.target_pos, (4, 4))

    def test_rock_smash_chain_serializes(self) -> None:
        # HM received but not taught -> teach_rock_smash; taught -> reach_fallarbor
        # takes over (the smash goal only fires on Route111 with the rock present).
        base = dict(map_group=0, map_num=2, badge_count=3,
                    flag_steven_letter_delivered=True,
                    flag_dock_rejected_devon=True,
                    flag_devon_goods_delivered=True)
        # HM received, no party mon knows Rock Smash -> teach it (UI sub-task)
        gs_teach = make_gs(flag_rock_smash_hm=True,
                           party_moves=[[348, 43, 228, 98]], **base)
        self.assertEqual(goals_mod.current_goal(gs_teach).name, "teach_rock_smash")
        # taught (move 249 present) -> advance to reach_fallarbor
        gs_taught = make_gs(flag_rock_smash_hm=True,
                            party_moves=[[348, 43, 228, 98], [249, 0, 0, 0]],
                            **base)
        self.assertEqual(goals_mod.current_goal(gs_taught).name, "reach_fallarbor")

    def test_smash_goal_fires_on_route111_with_rock(self) -> None:
        # On Route111, knowing Rock Smash, with the rock live at (19,100) ->
        # smash_route111_rock (interact + smash), NOT reach_fallarbor.
        gs = make_gs(map_group=0, map_num=26, badge_count=3,
                     flag_steven_letter_delivered=True,
                     flag_dock_rejected_devon=True,
                     flag_devon_goods_delivered=True,
                     flag_rock_smash_hm=True,
                     party_moves=[[249, 0, 0, 0]],
                     npcs_on_map=[(19, 100, 86)])
        g = goals_mod.current_goal(gs)
        self.assertEqual(g.name, "smash_route111_rock")
        self.assertEqual(g.target_pos, (19, 100))

    def test_fiery_path_cross_fires_on_route112_south(self) -> None:
        # Route112 south blob (holds the higher-y Fiery Path warp) can only
        # reach Fallarbor across Fiery Path -> fiery_path_cross, NOT
        # reach_fallarbor (which would ping-pong Route111<->Route112). Uses the
        # real Route112 collision from the map cache to resolve the component.
        base = dict(map_group=0, map_num=27, badge_count=3,
                    flag_steven_letter_delivered=True,
                    flag_dock_rejected_devon=True,
                    flag_devon_goods_delivered=True,
                    flag_rock_smash_hm=True,        # HM06 received + taught, so the
                    party_moves=[[249, 0, 0, 0]])   # Rock Smash chain is retired
        gs_south = make_gs(x=26, y=44, **base)   # the 5595-turn stall tile
        self.assertEqual(goals_mod.current_goal(gs_south).name,
                         "fiery_path_cross")
        # North blob (after crossing) -> reach_fallarbor takes over.
        gs_north = make_gs(x=22, y=10, **base)
        self.assertEqual(goals_mod.current_goal(gs_north).name,
                         "reach_fallarbor")

    def test_fiery_path_cross_survives_visited_suppression(self) -> None:
        # Fiery Path is a CROSSING: FieryPath (24,14) is marked visited the
        # instant we step in, but fiery_path_cross must keep firing (it's in
        # _GOAL_BYPASS_VISITED) until we exit into the north blob. Without the
        # bypass, one visit dropped it to reach_fallarbor and the agent
        # oscillated Route111<->Route112 south for 1000+ turns (07-17).
        goals_mod.record_map_visit(24, 14)   # FieryPath now "visited"
        base = dict(map_group=0, map_num=27, badge_count=3,
                    flag_steven_letter_delivered=True,
                    flag_dock_rejected_devon=True,
                    flag_devon_goods_delivered=True,
                    flag_rock_smash_hm=True,
                    party_moves=[[249, 0, 0, 0]])
        gs_south = make_gs(x=38, y=46, **base)  # a live oscillation tile
        self.assertEqual(goals_mod.current_goal(gs_south).name,
                         "fiery_path_cross")

    def test_exit_fiery_path_north_fires_inside_the_cave(self) -> None:
        # Once IN Fiery Path (24,14), route to the north warp pad -- neither
        # fiery_path_cross (needs Route112) nor reach_fallarbor (cur-set) fire
        # there, so the agent wandered goal-less for 1243 turns (07-17).
        base = dict(map_group=24, map_num=14, badge_count=3,
                    flag_steven_letter_delivered=True,
                    flag_dock_rejected_devon=True,
                    flag_devon_goods_delivered=True,
                    flag_rock_smash_hm=True, party_moves=[[249, 0, 0, 0]])
        goals_mod.record_map_visit(24, 14)   # visited the instant we step in
        gs = make_gs(x=26, y=21, **base)     # the stuck-at-y21 tile
        g = goals_mod.current_goal(gs)
        self.assertEqual(g.name, "exit_fiery_path_north")
        self.assertEqual(g.target_pos, (26, 4))
        # retires once Flannery is beaten / the magma leg is cleared
        self.assertIsNone(goals_mod.current_goal(
            make_gs(x=26, y=21, flag_badge04_get=True, **base)))

    def test_exit_fiery_path_target_matches_canon_north_warp(self) -> None:
        # No hardcoded-coord drift: the goal's target_pos must be the min-y
        # (northern) FieryPath->Route112 warp read from the map cache.
        from generic_agent import map_data as md
        fp = md.get_cache().get(24, 14)
        r112 = [(w["x"], w["y"]) for w in (fp.warps or [])
                if "Route112" in str(w.get("dest_map", ""))]
        north = min(r112, key=lambda t: t[1])
        goal = next(g for g in goals_mod.GOAL_TABLE
                    if g.name == "exit_fiery_path_north")
        self.assertEqual(goal.target_pos, north)

    def test_meteor_falls_theft_fires_past_fallarbor(self) -> None:
        # Once at/past Fallarbor (reach_fallarbor's cur-set no longer matches),
        # the arc's leg 2 takes over: enter Meteor Falls for the Team Magma
        # theft cutscene. Route114 (0,29) is past Fallarbor and in no cur-set.
        base = dict(badge_count=3,
                    flag_steven_letter_delivered=True,
                    flag_dock_rejected_devon=True,
                    flag_devon_goods_delivered=True,
                    flag_rock_smash_hm=True, party_moves=[[249, 0, 0, 0]])
        gs = make_gs(map_group=0, map_num=29, **base)   # Route114
        g = goals_mod.current_goal(gs)
        self.assertEqual(g.name, "meteor_falls_theft")
        self.assertEqual(g.target_map, (24, 0))         # MeteorFalls1F1R
        # (13,18) = west neighbour of the (14,18) theft coord_event, so BFS from
        # the east stops on the trigger (step-on fires the cutscene).
        self.assertEqual(g.target_pos, (13, 18))
        # Inside Meteor Falls the goal stays live (visited-bypass) until 0x333.
        gs_inside = make_gs(map_group=24, map_num=0, x=20, y=18, **base)
        goals_mod.record_map_visit(24, 0)
        self.assertEqual(goals_mod.current_goal(gs_inside).name,
                         "meteor_falls_theft")

    def test_meteor_falls_theft_advances_to_cable_car(self) -> None:
        # The theft cutscene sets FLAG_HIDE_ROUTE_112_TEAM_MAGMA (0x333). Once
        # set (latched), leg 2 retires and the cable-car leg (3) takes over: from
        # Route114 the agent heads back to the Route112 cable-car station.
        gs = make_gs(map_group=0, map_num=29, badge_count=3,
                     flag_steven_letter_delivered=True,
                     flag_dock_rejected_devon=True,
                     flag_devon_goods_delivered=True,
                     flag_rock_smash_hm=True, party_moves=[[249, 0, 0, 0]],
                     flag_route112_magma_cleared=True)
        self.assertEqual(goals_mod.current_goal(gs).name, "ride_cable_car")

    def test_meteor_falls_target_is_west_of_canon_trigger(self) -> None:
        # No hardcoded-coord drift: target_pos must sit immediately WEST of the
        # canon theft coord_event so the eastward approach steps onto it. Read
        # the trigger from the cached map.json (MagmaStealsMeteoriteScene).
        import json
        from generic_agent import config
        p = config.MEMORY_DIR / "map_cache" / "MeteorFalls_1F_1R.map.json"
        ce = [e for e in json.loads(p.read_text(encoding="utf-8")).get(
            "coord_events", []) if "MagmaSteals" in str(e.get("script", ""))]
        self.assertEqual(len(ce), 1)
        trig = (ce[0]["x"], ce[0]["y"])
        goal = next(g for g in goals_mod.GOAL_TABLE
                    if g.name == "meteor_falls_theft")
        self.assertEqual(goal.target_pos, (trig[0] - 1, trig[1]))  # west nbr

    def test_badge4_advances_to_mauville_hub(self) -> None:
        # Once Flannery is beaten (badge_count 4 latched) the Lavaridge arc
        # retires and Badge5 leg 1 takes over: back to the Mauville hub. The
        # goal chain must NOT go None (the post-badge4 aimless-loop bug).
        gs = make_gs(map_group=0, map_num=13, badge_count=4,
                     flag_steven_letter_delivered=True,
                     flag_dock_rejected_devon=True,
                     flag_devon_goods_delivered=True,
                     flag_badge04_get=True)
        g = goals_mod.current_goal(gs)
        self.assertIsNotNone(g)
        self.assertEqual(g.name, "reach_mauville_b5")
        self.assertEqual(g.target_map, (0, 2))

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


class TestLavaridgeArc(GoalsTestBase):
    """Badge4 (Lavaridge/Flannery) arc goal chain, gated by the two latched
    story flags: theft (0x333) and Mt.Chimney (0x8B). Peeko/letter/devon are
    long done by badge 3, so their markers are pre-written."""

    def setUp(self) -> None:
        super().setUp()
        for m in (goals_mod.PEEKO_DONE_MARKER, goals_mod.LETTER_DONE_MARKER,
                  goals_mod.DEVON_DELIVERED_MARKER):
            m.write_text("1", encoding="utf-8")

    def _gs(self, **kw):
        base = dict(
            badge_count=3, party_count=5,
            # Full HP + stocked by default so nav/routing tests aren't diverted
            # by field_heal_potion (<80%) or buy_potions; the shop/heal tests
            # override party0_hp / bag_heal_qty explicitly.
            party0_hp=128, party0_max_hp=128,
            bag_heal_qty=10, money=20000,
            flag_steven_letter_delivered=True,
            flag_dock_rejected_devon=True,
            flag_devon_goods_delivered=True,
            flag_rock_smash_hm=True, party_moves=[[249, 0, 0, 0]],
        )
        base.update(kw)
        return make_gs(**base)

    def _name(self, **kw):
        g = goals_mod.current_goal(self._gs(**kw))
        return g.name if g else None

    # --- pre-theft (0x333 == 0): the four earlier goals still own the map ---
    def test_pre_theft_route114_is_theft_goal(self) -> None:
        self.assertEqual(
            self._name(map_group=0, map_num=29, x=19, y=20),
            "meteor_falls_theft")

    def test_pre_theft_fiery_heads_north(self) -> None:
        self.assertEqual(
            self._name(map_group=24, map_num=14, x=26, y=20),
            "exit_fiery_path_north")

    # --- post-theft (0x333 == 1, 0x8B == 0): cable-car leg ---
    def test_theft_done_routes_to_cable_car(self) -> None:
        # North blob and south blob both drive to the station (region nav
        # crosses Fiery southward for the north one).
        T = dict(flag_route112_magma_cleared=True)
        for (x, y) in ((22, 10), (26, 36)):
            g = goals_mod.current_goal(
                self._gs(map_group=0, map_num=27, x=x, y=y, **T))
            self.assertEqual(g.name, "ride_cable_car")
            self.assertEqual(g.target_pos, (6, 6))

    def test_theft_done_fiery_flips_south(self) -> None:
        # The SAME FieryPath tile that pre-theft routed north now routes south.
        g = goals_mod.current_goal(self._gs(
            map_group=24, map_num=14, x=26, y=20,
            flag_route112_magma_cleared=True))
        self.assertEqual(g.name, "exit_fiery_path_south")
        self.assertEqual(g.target_pos, (26, 36))

    def test_at_mtchimney_fights_magma_not_cable_car(self) -> None:
        # ride_cable_car goes silent on the Mt.Chimney maps so the battle goal
        # wins there.
        g = goals_mod.current_goal(self._gs(
            map_group=24, map_num=12, x=17, y=37,
            flag_route112_magma_cleared=True))
        self.assertEqual(g.name, "mtchimney_defeat_magma")
        self.assertEqual(g.target_pos, (13, 6))

    # --- magma-done (0x8B == 1): descend, heal, gym ---
    def test_magma_done_descends_then_reaches_lavaridge(self) -> None:
        # party0_level at the grind target: below it grind_pre_flannery owns
        # nav on the pocket (H17) — TestFlanneryGrind covers that branch.
        M = dict(flag_route112_magma_cleared=True,
                 flag_mtchimney_magma_defeated=True,
                 party0_level=goals_mod.FLANNERY_GRIND_TARGET_LEVEL)
        self.assertEqual(
            self._name(map_group=24, map_num=12, x=17, y=37, **M),
            "descend_jagged_pass")
        # In the SW pocket ride/descend/flannery are all silent -> reach_lavaridge
        self.assertEqual(
            self._name(map_group=0, map_num=27, x=7, y=47, **M),
            "reach_lavaridge")

    def test_lavaridge_gym_and_heal_priority(self) -> None:
        # Level at the grind target: below it the H17 grind goal wins in town
        # (TestFlanneryGrind covers that branch).
        M = dict(flag_route112_magma_cleared=True,
                 flag_mtchimney_magma_defeated=True,
                 party0_level=goals_mod.FLANNERY_GRIND_TARGET_LEVEL)
        # Full HP in town -> straight to Flannery.
        g = goals_mod.current_goal(
            self._gs(map_group=0, map_num=12, x=5, y=10, **M))
        self.assertEqual(g.name, "lavaridge_gym_flannery")
        self.assertEqual(g.target_pos, (13, 9))
        # Low HP -> heal first (heal sits above the gym goal).
        self.assertEqual(
            self._name(map_group=0, map_num=12, x=5, y=10,
                       party0_hp=40, **M),
            "heal_at_lavaridge")
        # Dropped into gym B1F mid-puzzle -> gym goal persists (target 1F).
        self.assertEqual(
            self._name(map_group=4, map_num=2, x=5, y=5, **M),
            "lavaridge_gym_flannery")

    def test_badge04_retires_entire_arc(self) -> None:
        # Heat Badge won -> every Lavaridge-arc goal retires (next step
        # unimplemented -> None), even standing in Lavaridge.
        self.assertIsNone(goals_mod.current_goal(self._gs(
            map_group=0, map_num=12, x=5, y=10, flag_badge04_get=True,
            flag_route112_magma_cleared=True,
            flag_mtchimney_magma_defeated=True)))

    def test_buy_potions_fires_low_stock_before_cable_car(self) -> None:
        # H14: post-theft at Mauville with few restores + money -> stock up at
        # the Mart BEFORE heading to the cable car (buy_potions is above
        # ride_cable_car in the table).
        g = goals_mod.current_goal(self._gs(
            map_group=0, map_num=2, flag_route112_magma_cleared=True,
            bag_heal_qty=0, money=20000))
        self.assertEqual(g.name, "buy_potions")
        self.assertEqual(g.target_map, (10, 7))
        self.assertEqual(g.target_pos, (2, 3))

    def test_buy_potions_retires_when_stocked(self) -> None:
        # Enough restores -> skip the shop, proceed to the cable car.
        self.assertEqual(
            self._name(map_group=0, map_num=2, flag_route112_magma_cleared=True,
                       bag_heal_qty=10, money=20000),
            "ride_cable_car")

    def test_buy_potions_no_fire_when_broke(self) -> None:
        # Confirmed-broke wallet (0..699) can't buy -> don't detour to the Mart.
        self.assertEqual(
            self._name(map_group=0, map_num=2, flag_route112_magma_cleared=True,
                       bag_heal_qty=0, money=300),
            "ride_cable_car")

    def test_field_heal_fires_low_hp_with_potions(self) -> None:
        # On Mt.Chimney, low HP + restores in bag -> heal before the next
        # trainer (field_heal_potion is above mtchimney_defeat_magma).
        M = dict(flag_route112_magma_cleared=True)
        self.assertEqual(
            self._name(map_group=24, map_num=12, x=17, y=37,
                       party0_hp=50, party0_max_hp=131, bag_heal_qty=5, **M),
            "field_heal_potion")
        # No restores left -> fall through to fighting (anti-loop guard).
        self.assertEqual(
            self._name(map_group=24, map_num=12, x=17, y=37,
                       party0_hp=50, party0_max_hp=131, bag_heal_qty=0, **M),
            "mtchimney_defeat_magma")
        # Full HP -> no heal, fight.
        self.assertEqual(
            self._name(map_group=24, map_num=12, x=17, y=37,
                       party0_hp=131, party0_max_hp=131, bag_heal_qty=5, **M),
            "mtchimney_defeat_magma")

    def test_fainted_lead_does_not_field_heal(self) -> None:
        # A fainted lead (hp 0) can't be Potion-revived (no Revive in bag), so
        # field_heal must NOT fire — it churned a doomed VLM Potion sub-task
        # every 25 turns and blocked the Jagged Pass descent to a PC (the 07-24
        # deadlock). Fall through to the fight/descent so the loop reaches a PC.
        M = dict(flag_route112_magma_cleared=True)
        self.assertNotEqual(
            self._name(map_group=24, map_num=12, x=17, y=37,
                       party0_hp=0, party0_max_hp=131, bag_heal_qty=5, **M),
            "field_heal_potion")

    def test_theft_latch_survives_flag_flicker(self) -> None:
        # Once 0x333 has read True, a later frame reading it False must NOT
        # revert to the pre-theft goal (the DMA-flicker north-yank guard).
        at = dict(map_group=24, map_num=14, x=26, y=20)
        self.assertEqual(  # observe theft -> latches marker
            self._name(flag_route112_magma_cleared=True, **at),
            "exit_fiery_path_south")
        self.assertTrue(goals_mod.THEFT_DONE_MARKER.exists())
        self.assertEqual(  # flicker back to False -> still south (latched)
            self._name(flag_route112_magma_cleared=False, **at),
            "exit_fiery_path_south")


class TestFlanneryGrind(GoalsTestBase):
    """H17: sub-target lead grinds Fiery Path (a CAVE) before Flannery.

    The Jagged Pass grass grind leaked (frontier wander vaults the y=26
    JUMP_SOUTH ledge out of the grass), so the grind moved to Fiery Path: a
    272-tile MB_CAVE floor with zero ledges where the frontier wander stays on
    the encounter floor every step (grind_granite_cave's proven property). The
    cycle: on Route112 / the Lavaridge side, grind_fiery_path routes toward the
    cave (post-theft fiery_path_cross is off, so this goal drives entry); inside
    the cave it pins (26,23) and grinds; a hurt lead heals at the Mauville PC
    (the low walkable loop) via heal_at_mauville; at the target level the grind
    retires and exit_fiery_path_south -> ride_cable_car -> descend ->
    reach_lavaridge -> gym resume the Flannery push."""

    TARGET = goals_mod.FLANNERY_GRIND_TARGET_LEVEL

    def setUp(self) -> None:
        super().setUp()
        for m in (goals_mod.PEEKO_DONE_MARKER, goals_mod.LETTER_DONE_MARKER,
                  goals_mod.DEVON_DELIVERED_MARKER, goals_mod.THEFT_DONE_MARKER,
                  goals_mod.MTCHIMNEY_DONE_MARKER):
            m.write_text("1", encoding="utf-8")

    def _gs(self, **kw):
        base = dict(
            badge_count=3, party_count=5,
            # one below the grind target so the goal fires (threshold-agnostic)
            party0_level=goals_mod.FLANNERY_GRIND_TARGET_LEVEL - 1,
            party0_hp=128, party0_max_hp=128,
            bag_heal_qty=10, money=20000,
            flag_steven_letter_delivered=True,
            flag_dock_rejected_devon=True,
            flag_devon_goods_delivered=True,
            flag_rock_smash_hm=True, party_moves=[[249, 0, 0, 0]],
        )
        base.update(kw)
        return make_gs(**base)

    def _name(self, **kw):
        g = goals_mod.current_goal(self._gs(**kw))
        return g.name if g else None

    def test_under_target_grinds_inside_fiery(self) -> None:
        # Inside Fiery Path an under-level lead pins the cave-floor grind tile
        # and outranks exit_fiery_path_south (which would otherwise walk it
        # straight back out the south warp post-theft).
        g = goals_mod.current_goal(self._gs(map_group=24, map_num=14, x=26, y=23))
        self.assertEqual(g.name, "grind_fiery_path")
        self.assertEqual(g.target_map, (24, 14))
        self.assertEqual(g.target_pos, (26, 23))

    def test_under_target_routes_in_from_route112(self) -> None:
        # On Route112 (post-theft, so fiery_path_cross is off) the grind goal
        # drives the entry toward Fiery Path.
        self.assertEqual(
            self._name(map_group=0, map_num=27, x=26, y=36), "grind_fiery_path")

    def test_under_target_lavaridge_routes_back_not_flannery(self) -> None:
        # An under-level lead that reached the Lavaridge side must route BACK
        # toward the grind, never into the (losing) Flannery fight.
        for m in ((0, 12), (4, 5), (4, 1)):
            self.assertEqual(
                self._name(map_group=m[0], map_num=m[1], x=5, y=6),
                "grind_fiery_path", m)

    def test_at_target_resumes_flannery_push(self) -> None:
        # At the target level the grind retires: inside Fiery the exit goal
        # walks out south; on the Lavaridge side the gym goal takes over.
        L = dict(party0_level=self.TARGET)
        self.assertEqual(
            self._name(map_group=24, map_num=14, x=26, y=23, **L),
            "exit_fiery_path_south")
        self.assertEqual(
            self._name(map_group=0, map_num=12, x=5, y=6, **L),
            "lavaridge_gym_flannery")

    def test_hurt_routes_up_to_lavaridge_heal(self) -> None:
        # Heal via the CABLE CAR to Lavaridge, not Mauville (Route112->Route111
        # ->Mauville stalls in the boulder maze). A hurt lead on the Route112
        # south blob rides the cable car up; at Lavaridge it heals at the PC.
        self.assertEqual(
            self._name(map_group=0, map_num=27, x=26, y=36,
                       party0_hp=40, party0_max_hp=128),
            "ride_cable_car")
        self.assertEqual(
            self._name(map_group=0, map_num=12, x=5, y=6,
                       party0_hp=40, party0_max_hp=128),
            "heal_at_lavaridge")

    def test_zero_damaging_pp_yields_grind_to_heal(self) -> None:
        # A full-HP lead with 0 damaging PP must NOT keep grinding (it would
        # flee every wild forever) — it yields toward the PC heal (which refills
        # PP): cable car up from the south blob, PC at Lavaridge. Unreadable PP
        # (-1, the default) keeps grinding. And a 0-PP lead at Lavaridge must
        # heal, NOT walk into Flannery.
        self.assertEqual(
            self._name(map_group=0, map_num=27, x=26, y=36,
                       party0_damaging_pp=-1), "grind_fiery_path")
        self.assertEqual(
            self._name(map_group=0, map_num=27, x=26, y=36,
                       party0_damaging_pp=0), "ride_cable_car")
        self.assertEqual(
            self._name(map_group=0, map_num=12, x=5, y=6,
                       party0_damaging_pp=0), "heal_at_lavaridge")

    def test_pre_mtchimney_never_grinds(self) -> None:
        # Before the Mt.Chimney Magma defeat the grind must not fire — the
        # gauntlet goals still own the arc.
        goals_mod.MTCHIMNEY_DONE_MARKER.unlink()
        self.assertNotEqual(
            self._name(map_group=24, map_num=14, x=26, y=23,
                       flag_mtchimney_magma_defeated=False),
            "grind_fiery_path")

    def test_badge04_retires_grind(self) -> None:
        # Heat Badge won -> the whole arc (grind included) retires.
        self.assertIsNone(goals_mod.current_goal(self._gs(
            map_group=24, map_num=14, x=26, y=23, flag_badge04_get=True)))

    def test_visited_bypass_keeps_retargeting_the_cave(self) -> None:
        # FieryPath is marked visited the instant the lead steps in, but the
        # grind must keep re-targeting it every heal cycle until the target
        # level (the fiery_path_cross / grind_granite_cave bypass, mirrored).
        goals_mod.record_map_visit(24, 14)
        self.assertEqual(
            self._name(map_group=0, map_num=27, x=26, y=36), "grind_fiery_path")

    def test_pin_is_canon_cave_floor(self) -> None:
        # No hardcoded-coord drift: the pin must be a canon MB_CAVE (0x08)
        # land-encounter floor tile with all 4 neighbours also cave floor, so
        # the frontier wander stays on the encounter floor every step (the
        # leak-proof property the Jagged Pass grass lacked).
        from generic_agent import config as _config, map_data as _md
        goal = next(g for g in goals_mod.GOAL_TABLE
                    if g.name == "grind_fiery_path")
        self.assertEqual(goal.target_map, (24, 14))
        if not (_config.MEMORY_DIR / "map_cache" / "FieryPath.map.bin").exists():
            self.skipTest("FieryPath canon cache not present")
        bg = _md.get_cache().behavior_grid(24, 14)
        px, py = goal.target_pos
        self.assertEqual(bg.get((px, py)), 0x08)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            self.assertEqual(bg.get((px + dx, py + dy)), 0x08, (dx, dy))


class TestWaterCatch(GoalsTestBase):
    """User-directed Water-catch sub-project (2026-07-26): buy Poke Balls at
    the Rustboro Mart, hunt Marill (ROM-verified 20% land share, L4-5) in
    Route104 NORTH grass. Pins the buy->catch->restock->retire cycle."""

    def setUp(self) -> None:
        super().setUp()
        for m in (goals_mod.PEEKO_DONE_MARKER, goals_mod.LETTER_DONE_MARKER,
                  goals_mod.DEVON_DELIVERED_MARKER, goals_mod.THEFT_DONE_MARKER,
                  goals_mod.MTCHIMNEY_DONE_MARKER):
            m.write_text("1", encoding="utf-8")

    def _gs(self, **kw):
        base = dict(
            badge_count=4, party_count=5, party0_level=47,
            party0_hp=152, party0_max_hp=152,
            bag_heal_qty=2, money=6407,          # live wallet at design time
            flag_badge04_get=True,
            flag_steven_letter_delivered=True,
            flag_dock_rejected_devon=True,
            flag_devon_goods_delivered=True,
            flag_rock_smash_hm=True, party_moves=[[249, 0, 0, 0]],
        )
        base.update(kw)
        return make_gs(**base)

    def _name(self, **kw):
        g = goals_mod.current_goal(self._gs(**kw))
        return g.name if g else None

    def test_no_balls_buys_at_rustboro_mart(self) -> None:
        # The live state: Rustboro, 0 balls, $6407 -> buy first (above catch,
        # which is silent without balls anyway).
        g = goals_mod.current_goal(self._gs(
            map_group=0, map_num=3, x=30, y=30, bag_pokeball_count=0))
        self.assertIsNotNone(g)
        self.assertEqual(g.name, "buy_pokeballs")
        self.assertEqual(g.target_map, (11, 7))
        self.assertEqual(g.target_pos, (2, 3))

    def test_low_balls_restock_before_hunt(self) -> None:
        # < 5 balls at Rustboro -> restock outranks the hunt (table order).
        self.assertEqual(
            self._name(map_group=0, map_num=3, x=30, y=30,
                       bag_pokeball_count=2),
            "buy_pokeballs")

    def test_stocked_hunts_route104_grass(self) -> None:
        # Balls stocked -> the catch goal drives to the Route104 north grass
        # pin; inside the Mart (11,7) too (walks back out, target != cur).
        g = goals_mod.current_goal(self._gs(
            map_group=0, map_num=3, x=30, y=30, bag_pokeball_count=11))
        self.assertIsNotNone(g)
        self.assertEqual(g.name, "catch_water_route104")
        self.assertEqual(g.target_map, (0, 19))
        self.assertEqual(g.target_pos, (3, 11))
        # on Route104 itself the pin is returned (target==cur + target_pos)
        self.assertEqual(
            self._name(map_group=0, map_num=19, x=19, y=0,
                       bag_pokeball_count=11),
            "catch_water_route104")

    def test_balls_out_on_route104_pulls_back_through_rustboro(self) -> None:
        # Balls GENUINELY exhausted mid-hunt (the read-root fall-confirm guard
        # means a reported 0 is a confirmed 0): catch goes silent. Since the
        # Norman grind arc (07-28) the under-level eastbound trek owns this
        # pull — its first map_path hop from Route104 is STILL Rustboro, where
        # buy_pokeballs scans first and re-fires = the same restock cycle.
        # Must NOT be reach_mauville_b5: Route104 is in its exclusion set
        # since 07-27 (a balls flicker-0 frame let it yank the agent off the
        # route — the boundary oscillation).
        self.assertEqual(
            self._name(map_group=0, map_num=19, x=5, y=12,
                       bag_pokeball_count=0),
            "reach_ngrind_mauville")

    def test_broke_and_ballless_abandons_hunt(self) -> None:
        # No balls AND a confirmed wallet below one ball: both project goals
        # silent -> the under-level (47 < NORMAN_GRIND_TARGET_LEVEL) grind
        # trek continues east through Rustboro (was: park at Rustboro, before
        # the 07-28 Norman grind arc).
        self.assertEqual(
            self._name(map_group=0, map_num=3, x=30, y=30,
                       bag_pokeball_count=0, money=100, party_count=6),
            "reach_ngrind_mauville")

    def test_party_full_retires_project(self) -> None:
        # Slot 6 filled (the catch landed): buy and catch both retire, and at
        # the design-time level (47 < NORMAN_GRIND_TARGET_LEVEL) the grind
        # trek owns both positions eastbound (the live pre-Norman state IS
        # party 6 / under-level). The petalburg_to_woods handoff this test
        # used to pin is now the AT-TARGET behavior — pinned by
        # TestNormanGrind.test_retire_resumes_petalburg_chain.
        self.assertEqual(
            self._name(map_group=0, map_num=3, x=30, y=30,
                       bag_pokeball_count=8, party_count=6),
            "reach_ngrind_mauville")
        self.assertEqual(
            self._name(map_group=0, map_num=19, x=5, y=12,
                       bag_pokeball_count=8, party_count=6),
            "reach_ngrind_mauville")

    def test_grass_pin_is_canon_tall_grass(self) -> None:
        # No hardcoded-coord drift: the pin must be canon MB_TALL_GRASS (0x02)
        # with all 4 neighbours also tall grass (the grind-pin discipline:
        # frontier wander stays on encounter tiles), in the NORTH half (y<34).
        from generic_agent import config as _config, map_data as _md
        goal = next(g for g in goals_mod.GOAL_TABLE
                    if g.name == "catch_water_route104")
        self.assertEqual(goal.target_map, (0, 19))
        if not (_config.MEMORY_DIR / "map_cache" / "Route104.map.bin").exists():
            self.skipTest("Route104 canon cache not present")
        bg = _md.get_cache().behavior_grid(0, 19)
        px, py = goal.target_pos
        self.assertLess(py, 34)  # north half — reachable without the Woods
        self.assertEqual(bg.get((px, py)), 0x02)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            self.assertEqual(bg.get((px + dx, py + dy)), 0x02, (dx, dy))


class TestPostBadge4(GoalsTestBase):
    """Post-Badge4 first increment: badge-independent Lavaridge heal + Badge5
    (Norman) leg-1 routing to the Mauville hub. Pins the live 2026-07-24
    post-Flannery state: party 4/5 fainted (lead 0/152) at Lavaridge Town,
    badge_count 4 — the un-gated table previously went goal=None here and the
    fainted party never healed."""

    def setUp(self) -> None:
        super().setUp()
        for m in (goals_mod.PEEKO_DONE_MARKER, goals_mod.LETTER_DONE_MARKER,
                  goals_mod.DEVON_DELIVERED_MARKER, goals_mod.THEFT_DONE_MARKER,
                  goals_mod.MTCHIMNEY_DONE_MARKER):
            m.write_text("1", encoding="utf-8")

    def _gs(self, **kw):
        # party0_level AT the Norman-grind target (2026-07-28): this class
        # pins the b5 westbound chain, which since the grind arc landed is
        # the AT-TARGET-level behavior — under NORMAN_GRIND_TARGET_LEVEL the
        # reach_ngrind_*/grind_fiery_norman goals own the same corridor
        # (pinned by TestNormanGrind below).
        base = dict(
            badge_count=4, party_count=5,
            party0_level=goals_mod.NORMAN_GRIND_TARGET_LEVEL,
            party0_hp=152, party0_max_hp=152,
            bag_heal_qty=2, money=20000,
            flag_badge04_get=True,
            flag_steven_letter_delivered=True,
            flag_dock_rejected_devon=True,
            flag_devon_goods_delivered=True,
            flag_rock_smash_hm=True, party_moves=[[249, 0, 0, 0]],
        )
        base.update(kw)
        return make_gs(**base)

    def _name(self, **kw):
        g = goals_mod.current_goal(self._gs(**kw))
        return g.name if g else None

    def test_fainted_party_heals_at_lavaridge(self) -> None:
        # The live 07-24 state: lead fainted 0/152 in Lavaridge Town. The heal
        # must fire despite flag_badge04_get ("hurt now" is a raw current-state
        # gate, not a latch) and route to the PC nurse counter.
        g = goals_mod.current_goal(self._gs(
            map_group=0, map_num=12, x=5, y=10, party0_hp=0))
        self.assertIsNotNone(g)
        self.assertEqual(g.name, "heal_at_lavaridge")
        self.assertEqual(g.target_map, (4, 5))
        self.assertEqual(g.target_pos, (7, 3))

    def test_inside_pc_keeps_heal_goal(self) -> None:
        # Standing inside the PC map (4,5) the goal must persist (cur-set
        # includes the PC map; target_pos returns it so nav reaches the
        # counter) — the heal_at_slateport PC-bounce lesson.
        g = goals_mod.current_goal(self._gs(
            map_group=4, map_num=5, x=7, y=8, party0_hp=0))
        self.assertIsNotNone(g)
        self.assertEqual(g.name, "heal_at_lavaridge")

    def test_healed_party_heads_to_mauville_hub(self) -> None:
        # Full-HP party at Lavaridge -> Badge5 leg 1 drives to the Mauville
        # hub; the loop must not be aimless.
        self.assertEqual(
            self._name(map_group=0, map_num=12, x=5, y=10),
            "reach_mauville_b5")

    def test_mauville_visited_suppression_bypassed(self) -> None:
        # Mauville (0,2) is long-visited by badge 4; the routing goal must
        # survive visited-map suppression (_GOAL_BYPASS_VISITED).
        goals_mod.record_map_visit(0, 2)
        self.assertEqual(
            self._name(map_group=0, map_num=12, x=5, y=10),
            "reach_mauville_b5")

    def test_hurt_pocket_heals_hurt_south_blob_pushes_on(self) -> None:
        # Route112 post-badge4: the JAGGED POCKET component can still walk
        # into Lavaridge -> heal there; the SOUTH BLOB cannot walk back up
        # (one-way ledges, ride_cable_car retired with the badge) -> head for
        # the Mauville PC instead of stranding on an unreachable Lavaridge
        # target. Since the Norman grind arc (07-28) that walk has an actual
        # heal ENDPOINT: heal_at_mauville_ngrind (level-independent, same
        # direction — was reach_mauville_b5, which walked the whole corridor
        # hurt). (6,46) = the Jagged landing warp (pocket component, canon
        # warp); (26,44) = a south-blob tile (the 5595-turn stall tile from
        # the fiery_path_cross tests).
        self.assertEqual(
            self._name(map_group=0, map_num=27, x=6, y=46, party0_hp=40),
            "heal_at_lavaridge")
        self.assertEqual(
            self._name(map_group=0, map_num=27, x=26, y=44, party0_hp=40),
            "heal_at_mauville_ngrind")

    def test_route111_rock_smashed_southbound(self) -> None:
        # Live 07-26 stall: the southbound Mauville leg wedged at (16,99) —
        # the static cache marks the FLAG_TEMP rock tile (19,100) walkable, so
        # BFS plans through it and the game blocks the step. The smash goal
        # must fire post-badge4 (the badge04-gate bug class, again) and win
        # table order over reach_mauville_b5 so the descent pauses, smashes,
        # then resumes. (16,99) = the live wedge tile.
        # npcs_on_map = the exact live RAM read at the wedge (both canon rocks:
        # (19,100) FLAG_TEMP_12 targeted, (18,101) FLAG_TEMP_11 not — see the
        # condition comment).
        g = goals_mod.current_goal(self._gs(
            map_group=0, map_num=26, x=16, y=99,
            npcs_on_map=[(19, 100, 86), (18, 101, 86)]))
        self.assertIsNotNone(g)
        self.assertEqual(g.name, "smash_route111_rock")
        self.assertEqual(g.target_pos, (19, 100))

    def test_route111_no_rock_pushes_to_mauville(self) -> None:
        # Rock smashed (gone from the object-event list) -> the smash goal
        # retires for the map session and the Mauville leg resumes.
        self.assertEqual(
            self._name(map_group=0, map_num=26, x=19, y=101,
                       npcs_on_map=[]),
            "reach_mauville_b5")

    def test_mauville_hub_heads_west_to_verdanturf(self) -> None:
        # At the Mauville hub (live save pos (20,11)) the westward leg wins the
        # scan — not the parking fallback, not any badge04-arc goal.
        g = goals_mod.current_goal(self._gs(map_group=0, map_num=2, x=20, y=11))
        self.assertIsNotNone(g)
        self.assertEqual(g.name, "reach_verdanturf_b5")
        self.assertEqual(g.target_map, (0, 14))

    def test_badge04_flicker_does_not_pull_east(self) -> None:
        # RAW badge04 drop-flicker frame (flag reads False; badge_count stays 4
        # = the latched read): ride_cable_car / reach_lavaridge must stay
        # silent (their new badge_count<4 gates) — the live 07-26 Mauville
        # park flicker that tugged the agent toward the cable car.
        self.assertEqual(
            self._name(map_group=0, map_num=2, x=20, y=11,
                       flag_badge04_get=False),
            "reach_verdanturf_b5")
        # ...and on the eastern corridor (Route111, no rock in range) the
        # recovery goal wins, not the cur-ungated reach_lavaridge.
        self.assertEqual(
            self._name(map_group=0, map_num=26, x=19, y=110,
                       flag_badge04_get=False, npcs_on_map=[]),
            "reach_mauville_b5")

    def test_route117_continues_west(self) -> None:
        # Mid-corridor on Route117 the leg keeps driving toward Verdanturf,
        # even though (0,32) is a visited map (bypass) and the east goal is
        # silenced there.
        self.assertEqual(
            self._name(map_group=0, map_num=32, x=30, y=8),
            "reach_verdanturf_b5")

    def test_verdanturf_continues_west_to_rustboro(self) -> None:
        # Leg 3 shipped: at Verdanturf the umbrella (above the leg-2 goal)
        # drives on toward Rustboro — no parking here anymore, and the east
        # goal stays silenced (no Verdanturf<->Mauville oscillation).
        g = goals_mod.current_goal(self._gs(map_group=0, map_num=14, x=19, y=10))
        self.assertIsNotNone(g)
        self.assertEqual(g.name, "reach_rustboro_b5")
        self.assertEqual(g.target_map, (0, 3))

    def test_tunnel_rock_present_smashes(self) -> None:
        # East landing (29,16) with the mid-wall rocks live: the smash goal
        # wins (rock-adjacent (25,4) reachable, len 16 probe) and pins the
        # (24,4) rock tile. Rocks carry PERMANENT hide flags — one smash.
        g = goals_mod.current_goal(self._gs(
            map_group=24, map_num=4, x=29, y=16,
            npcs_on_map=[(24, 4, 86), (24, 5, 86)]))
        self.assertIsNotNone(g)
        self.assertEqual(g.name, "smash_rusturf_rock")
        self.assertEqual(g.target_pos, (24, 4))

    def test_tunnel_rock_gone_exits_west(self) -> None:
        # (24,4) smashed (left the object list — permanently, unlike
        # Route111): the inner exit goal pins the WEST warp (4,10). The
        # remaining (24,5) rock must not re-fire the smash goal.
        g = goals_mod.current_goal(self._gs(
            map_group=24, map_num=4, x=25, y=4,
            npcs_on_map=[(24, 5, 86)]))
        self.assertIsNotNone(g)
        self.assertEqual(g.name, "exit_rusturf_west")
        self.assertEqual(g.target_pos, (4, 10))

    def test_route116_pushes_to_rustboro_not_peeko(self) -> None:
        # Route116 at the tunnel landing (47,8): the capped peeko_return must
        # NOT back-pull to Route104; the umbrella drives to Rustboro.
        self.assertEqual(
            self._name(map_group=0, map_num=31, x=47, y=8),
            "reach_rustboro_b5")

    def test_rustboro_parks_no_back_pull(self) -> None:
        # At Rustboro (target==cur) the umbrella is the returned fallback —
        # reach_mauville_b5 is silenced there (exclusion set) or the scan
        # would pick it and drag the agent back east. Same from an indoor
        # group-11 map (walks back out to the town). party_count=6 = the
        # Water-catch project is DONE (at 5 the buy/catch goals own Rustboro
        # — covered by TestWaterCatch).
        g = goals_mod.current_goal(self._gs(
            map_group=0, map_num=3, x=30, y=30, party_count=6))
        self.assertIsNotNone(g)
        self.assertEqual(g.name, "reach_rustboro_b5")
        self.assertEqual(
            self._name(map_group=11, map_num=3, x=4, y=6, party_count=6),
            "reach_rustboro_b5")

    def test_badge1_peeko_return_regression(self) -> None:
        # The `< 2` cap must NOT break the badge-1 era: with Peeko rescued the
        # return journey still owns Rusturf/Route116 at badge 1.
        for mid in [(0, 31), (24, 4)]:
            self.assertEqual(
                self._name(map_group=mid[0], map_num=mid[1], x=20, y=8,
                           badge_count=1, flag_badge04_get=False),
                "peeko_return", mid)

    def test_route104_strand_chain_capped(self) -> None:
        # The audit's STRAND CHAIN: at badge 4 the dewford quartet must be
        # dead on Route104 north AND south (it ended in dewford_sail boarding
        # Briney's boat — an irreversible sea-strand). Since 07-27 Route104 is
        # excluded from reach_mauville_b5 (balls-flicker oscillation), so the
        # coverage goal is the corridor umbrella pulling back to Rustboro —
        # still never the Briney chain.
        for y in (10, 40):
            g = goals_mod.current_goal(self._gs(
                map_group=0, map_num=19, x=15, y=y))
            self.assertIsNotNone(g, y)
            self.assertFalse(g.name.startswith("dewford"), (y, g.name))
            self.assertEqual(g.name, "reach_rustboro_b5", y)

    def test_smash_target_is_canon_rusturf_rock(self) -> None:
        # No hardcoded-coord drift: the smash pin must be one of the canon
        # BREAKABLE_ROCK object_events in RusturfTunnel.map.json.
        import json
        from generic_agent import config
        p = config.MEMORY_DIR / "map_cache" / "RusturfTunnel.map.json"
        if not p.exists():
            self.skipTest("RusturfTunnel canon cache not present")
        rocks = {(e["x"], e["y"])
                 for e in json.loads(p.read_text(encoding="utf-8"))
                 .get("object_events", [])
                 if "BREAKABLE_ROCK" in str(e.get("graphics_id", ""))}
        goal = next(g for g in goals_mod.GOAL_TABLE
                    if g.name == "smash_rusturf_rock")
        self.assertIn(goal.target_pos, rocks)

    def test_exit_target_is_canon_west_warp(self) -> None:
        # The exit pin must be the WEST (min-x) RusturfTunnel->Route116 warp:
        # the other (middle door, higher x) lands in the Route116 cul-de-sac
        # component that cannot reach the Rustboro exits (probe 07-26).
        from generic_agent import map_data as md
        info = md.get_cache().get(24, 4)
        if info is None:
            self.skipTest("RusturfTunnel canon cache not present")
        r116 = [(w["x"], w["y"]) for w in (info.warps or [])
                if "Route116" in str(w.get("dest_map", ""))]
        goal = next(g for g in goals_mod.GOAL_TABLE
                    if g.name == "exit_rusturf_west")
        self.assertEqual(goal.target_pos, min(r116, key=lambda t: t[0]))

    def test_verdanturf_indoor_walks_back_out(self) -> None:
        # Inside the Verdanturf PC (6,4) the goal stays live (group-6 cur-set,
        # group 6 is exclusively Verdanturf interiors) and targets the town so
        # region nav exits the building; the east goal is silent (group gate).
        self.assertEqual(
            self._name(map_group=6, map_num=4, x=7, y=8),
            "reach_verdanturf_b5")

    def test_badge5_retires_leg1_heal_stays_generic(self) -> None:
        # Norman beaten (badge_count 5) -> leg 1 retires (the next increment's
        # seam: healthy at Mauville -> None until the westward legs land), but
        # the badge-independent heal keeps serving a hurt party at Lavaridge.
        self.assertIsNone(goals_mod.current_goal(self._gs(
            map_group=0, map_num=2, badge_count=5)))
        self.assertEqual(
            self._name(map_group=0, map_num=12, badge_count=5, party0_hp=10),
            "heal_at_lavaridge")


class TestPetalburgLeg(GoalsTestBase):
    """Badge5 leg 4: north Route104 -> Woods -> south Route104 -> Petalburg
    City -> Gym. Latch-gated on _water_catch_done (party hit 6). Mirrors the
    badge-1 dewford southward chain but era-capped 4 <= badge < 5."""

    def _gs(self, **kw):
        # Badge5 era, catch done (party 6 -> latches), prior-arc flags set so no
        # earlier goal competes, full HP + stocked so heal/shop don't divert.
        # party0_level AT the Norman-grind target: the petalburg_* chain is the
        # AT-TARGET behavior since 07-28 — under it the reach_ngrind_* trek
        # owns the same corridor (TestNormanGrind).
        base = dict(
            badge_count=4, party_count=6,
            party0_level=goals_mod.NORMAN_GRIND_TARGET_LEVEL,
            party0_hp=128, party0_max_hp=128, party0_damaging_pp=10,
            bag_heal_qty=10, bag_pokeball_count=10, money=20000,
            flag_steven_letter_delivered=True,
            flag_dock_rejected_devon=True,
            flag_devon_goods_delivered=True,
            flag_devon_goods_recovered=True,   # peeko latch source
            flag_route112_magma_cleared=True,
            flag_mtchimney_magma_defeated=True,
            flag_rock_smash_hm=True, party_moves=[[249, 0, 0, 0]],
        )
        base.update(kw)
        return make_gs(**base)

    def _name(self, **kw):
        g = goals_mod.current_goal(self._gs(**kw))
        return g.name if g else None

    def test_north_route104_enters_woods(self) -> None:
        # (0,19) y<34: the catch-retire latch silences catch_water_route104
        # (which also matches (0,19)), so the Woods leg wins.
        self.assertEqual(
            self._name(map_group=0, map_num=19, x=2, y=6),
            "petalburg_to_woods")

    def test_in_woods_targets_south_exit(self) -> None:
        g = goals_mod.current_goal(self._gs(map_group=24, map_num=11, x=14, y=6))
        self.assertEqual(g.name, "petalburg_woods_south")
        self.assertEqual(g.target_pos, (16, 38))  # load-bearing pin

    def test_south_route104_targets_city(self) -> None:
        g = goals_mod.current_goal(
            self._gs(map_group=0, map_num=19, x=10, y=40))
        self.assertEqual(g.name, "petalburg_to_city")
        self.assertEqual(g.target_map, (0, 0))

    def test_city_healthy_enters_gym(self) -> None:
        self.assertEqual(
            self._name(map_group=0, map_num=0, x=0, y=12),
            "petalburg_enter_gym")

    def test_city_hurt_heals_before_gym(self) -> None:
        # hp < 50% -> heal_at_petalburg wins over petalburg_enter_gym.
        self.assertEqual(
            self._name(map_group=0, map_num=0, x=0, y=12,
                       party0_hp=10, party0_max_hp=100),
            "heal_at_petalburg")

    def test_gym_interior_parks_not_east_yank(self) -> None:
        # Inside the gym (8,1): petalburg_enter_gym target==cur parks as the
        # scan fallback. Pins the reach_mauville_b5 group-8 exclusion -- without
        # it the cur-ungated east goal would yank the agent out of the gym.
        self.assertEqual(
            self._name(map_group=8, map_num=1, x=4, y=110),
            "petalburg_enter_gym")

    def test_latch_immune_to_party_count_flicker(self) -> None:
        # THE core of the design: once latched, a transient party_count=0 read
        # frame must NOT drop the chain (else reach_mauville_b5 yanks east).
        goals_mod.WATER_CATCH_DONE_MARKER.write_text("1", encoding="utf-8")
        self.assertEqual(
            self._name(map_group=0, map_num=19, x=2, y=6, party_count=0),
            "petalburg_to_woods")

    def test_catch_goal_retires_under_latch(self) -> None:
        # marker set + raw party_count=5 (a flicker): catch_water must stay
        # retired and the Petalburg chain drive instead.
        goals_mod.WATER_CATCH_DONE_MARKER.write_text("1", encoding="utf-8")
        name = self._name(map_group=0, map_num=19, x=2, y=6, party_count=5)
        self.assertEqual(name, "petalburg_to_woods")
        self.assertNotIn(name, ("catch_water_route104", "buy_pokeballs"))

    def test_badge1_marker_does_not_leak_to_dewford_era(self) -> None:
        # A stale latch marker present during the badge-1 Dewford era must not
        # activate the petalburg chain (era gate 4 <= badge < 5).
        goals_mod.WATER_CATCH_DONE_MARKER.write_text("1", encoding="utf-8")
        goals_mod.PEEKO_DONE_MARKER.write_text("1", encoding="utf-8")
        name = self._name(map_group=0, map_num=19, x=2, y=6,
                          badge_count=1, party_count=5, party0_level=30)
        self.assertIsNotNone(name)
        self.assertFalse(name.startswith("petalburg"))

    def test_party_below_6_no_latch_no_petalburg(self) -> None:
        # Fresh party 5, no marker: the chain is silent, so a Route104 frame
        # falls to the existing catch project (not the Petalburg leg).
        name = self._name(map_group=0, map_num=19, x=2, y=6, party_count=5)
        self.assertFalse((name or "").startswith("petalburg"))


class TestCatchWoods(GoalsTestBase):
    """User-directed Woods-catch sub-project (2026-07-27). The Marill catch
    already latched _water_catch_done (party hit 6); a duplicate Poochyena was
    then boxed, so the party is back to 5 with ONE open slot. Fill it with a
    USEFUL mon: drive to Petalburg Woods (24,11) and catch Shroomish (internal
    306 -> Breloom) or Slakoth (364 -> Slaking), fleeing everything else (the
    SPECIES gate lives in claude_heuristic — see test_catch_intent.
    TestCatchWoodsSpeciesGate). Direct analog of catch_water_route104 but with a
    SPECIES gate instead of a TYPE gate.

    The premise these tests pin: because the latch is set, the petalburg_* story
    chain is ALREADY active at party 5 and (baseline) routes to the gym. So the
    contract is (a) catch_woods must OUT-RANK that chain while a slot is open,
    and (b) hand back to it the instant the party fills. Env mirrors
    TestPetalburgLeg (badge-5 era, prior-arc flags set, latch on), but party 5.

    Contract this fixes on the goals side (validated offline against a prototype
    before these tests were written — all three are load-bearing):
      * gate: 4 <= badge < 5 AND party_count < 6 AND bag_pokeball_count > 0 AND
        cur in {(24,11) Woods, (0,19)&y>=34 Route104-south, (0,0) Petalburg} or
        map_group == 8 (Petalburg interiors). NO _water_catch_done gate — it
        fires WITH the latch set (that is the whole point).
      * table slot: BELOW catch_water_route104 (so it never steals the pre-latch
        Marill hunt) and ABOVE petalburg_to_woods (so it wins the whole
        petalburg corridor while party < 6).
      * catch_woods MUST be added to _GOAL_BYPASS_VISITED and MUST carry a
        target_pos: its target map (the Woods) is always in visited_maps, so
        without the bypass it is visited-suppressed everywhere but at (24,11);
        and without a target_pos the (24,11) scan falls through to
        petalburg_woods_south's pinned target==cur fallback. (This class
        pre-populates visited with the Woods + Route104 so both requirements are
        genuinely exercised, unlike a fresh-temp single-probe test.)

    No new marker: catch_woods gates on no latch, so GoalsTestBase's patch
    tuples need NO change — the existing WATER_CATCH_DONE_MARKER patch already
    covers the latch this class relies on."""

    def setUp(self) -> None:
        super().setUp()
        # Reproduce the LIVE scenario: the Marill catch already landed (latch
        # set) and a box deposit dropped the party to 5. party_count=5 alone
        # would NOT re-latch (only >=6 does), so pin the marker explicitly (the
        # TestPetalburgLeg.test_catch_goal_retires_under_latch idiom).
        goals_mod.WATER_CATCH_DONE_MARKER.write_text("1", encoding="utf-8")
        # Production reality: both the Woods (24,11) and Route104 (0,19) have
        # been visited since the badge-1 era, so catch_woods' Woods target is
        # visited-suppressed unless it is in _GOAL_BYPASS_VISITED. Pre-populate
        # so these tests actually require that bypass (the petalburg/catch_water
        # precedent — else this passes for the wrong reason on a clean disk).
        goals_mod.record_map_visit(24, 11)
        goals_mod.record_map_visit(0, 19)

    def _gs(self, **kw):
        # Same prior-arc flags as TestPetalburgLeg so no earlier arc goal
        # competes; party 5 (open slot), balls stocked, full HP + PP so neither
        # heal_at_petalburg nor buy_pokeballs diverts the scan. Level at the
        # Norman-grind target so the party-6 retire tests exercise the
        # petalburg handoff (catch_woods itself scans ABOVE the grind block,
        # so the catch assertions hold at any level).
        base = dict(
            badge_count=4, party_count=5,
            party0_level=goals_mod.NORMAN_GRIND_TARGET_LEVEL,
            party0_hp=128, party0_max_hp=128, party0_damaging_pp=10,
            bag_heal_qty=10, bag_pokeball_count=10, money=20000,
            flag_steven_letter_delivered=True,
            flag_dock_rejected_devon=True,
            flag_devon_goods_delivered=True,
            flag_devon_goods_recovered=True,   # peeko latch source
            flag_route112_magma_cleared=True,
            flag_mtchimney_magma_defeated=True,
            flag_rock_smash_hm=True, party_moves=[[249, 0, 0, 0]],
        )
        base.update(kw)
        return make_gs(**base)

    def _name(self, **kw):
        g = goals_mod.current_goal(self._gs(**kw))
        return g.name if g else None

    # --- fires at party 5 across the Woods corridor, out-ranking petalburg ---
    def test_in_woods_parks_to_catch_not_south_exit(self) -> None:
        # cur == target (24,11): catch_woods must return HERE (its grass pin) so
        # the loop hunts, out-ranking petalburg_woods_south (which also matches
        # (24,11) as a target==cur + target_pos fallback). This is the assertion
        # that FORCES catch_woods to carry a target_pos of its own.
        self.assertEqual(
            self._name(map_group=24, map_num=11, x=14, y=6),
            "catch_woods")

    def test_route104_south_routes_to_woods_not_city(self) -> None:
        # (0,19) y>=34: baseline here is petalburg_to_city; catch_woods wins and
        # heads back up into the Woods rather than east to the city.
        g = goals_mod.current_goal(
            self._gs(map_group=0, map_num=19, x=10, y=40))
        self.assertEqual(g.name, "catch_woods")
        self.assertEqual(g.target_map, (24, 11))

    def test_petalburg_city_routes_to_woods_not_gym(self) -> None:
        # (0,0): baseline here is petalburg_enter_gym; catch_woods wins.
        self.assertEqual(
            self._name(map_group=0, map_num=0, x=0, y=12),
            "catch_woods")

    def test_petalburg_gym_interior_walks_back_out(self) -> None:
        # Inside the gym (group 8): out-ranks petalburg_enter_gym's target==cur
        # park, so a party-5 wander into the gym is pulled back to the Woods
        # instead of parked at Norman's door. Pins the group-8 cur-set clause.
        self.assertEqual(
            self._name(map_group=8, map_num=1, x=4, y=110),
            "catch_woods")

    # --- retires at party 6 -> the petalburg chain resumes unchanged ---
    def test_party_full_retires_to_petalburg_chain(self) -> None:
        self.assertEqual(
            self._name(map_group=24, map_num=11, x=14, y=6, party_count=6),
            "petalburg_woods_south")
        self.assertEqual(
            self._name(map_group=0, map_num=19, x=10, y=40, party_count=6),
            "petalburg_to_city")
        self.assertEqual(
            self._name(map_group=0, map_num=0, x=0, y=12, party_count=6),
            "petalburg_enter_gym")

    # --- silent without balls: an empty bag must not strand the loop in the
    #     Woods; hand back to the petalburg chain so progress continues ---
    def test_no_balls_hands_back_to_petalburg(self) -> None:
        self.assertEqual(
            self._name(map_group=24, map_num=11, x=14, y=6,
                       bag_pokeball_count=0),
            "petalburg_woods_south")
        self.assertEqual(
            self._name(map_group=0, map_num=0, x=0, y=12,
                       bag_pokeball_count=0),
            "petalburg_enter_gym")

    # --- badge era gate: silent outside 4 <= badge < 5 ---
    def test_badge_era_gate_silent_before_badge4(self) -> None:
        # Badge-1 Dewford era (which ALSO crosses the Woods): even with a stray
        # latch marker + an open slot, catch_woods must stay silent so it never
        # yanks the badge-1 agent into a Petalburg-Woods catch project.
        goals_mod.PEEKO_DONE_MARKER.write_text("1", encoding="utf-8")
        name = self._name(map_group=24, map_num=11, x=14, y=6,
                          badge_count=1, party0_level=30)
        self.assertNotEqual(name, "catch_woods")

    def test_badge_era_gate_silent_after_badge5(self) -> None:
        name = self._name(map_group=24, map_num=11, x=14, y=6, badge_count=5)
        self.assertNotEqual(name, "catch_woods")

    # --- table ordering: must NOT steal the pre-latch Marill water hunt ---
    def test_does_not_steal_prelatch_water_hunt(self) -> None:
        # BEFORE the Marill catch (no latch): the water project owns Route104.
        # catch_woods sits BELOW catch_water_route104, so even where their
        # cur-sets overlap ((0,19) south) the water goal still wins. This is the
        # test that fails if catch_woods is (mis-)ordered above catch_water.
        goals_mod.WATER_CATCH_DONE_MARKER.unlink()  # undo the setUp latch
        self.assertEqual(
            self._name(map_group=0, map_num=19, x=10, y=40, party_count=5),
            "catch_water_route104")


class TestNormanGrind(GoalsTestBase):
    """Norman grind arc (2026-07-28, user decision after Norman loss #1):
    while 4 <= badge < 5 AND lead < NORMAN_GRIND_TARGET_LEVEL, the loop treks
    the lead EASTBOUND Petalburg -> Woods -> Rustboro -> Rusturf Tunnel ->
    Verdanturf -> Route117 -> Mauville -> Route111 -> Route112 -> Fiery Path
    and grinds the cave; at target level all four arc goals retire and the
    UNCHANGED live-proven b5 chain drives home to the gym. Env = the live
    pre-rematch state: badge 4, party 6 (both catch projects done, latch set),
    Sceptile L48 full HP/PP."""

    def setUp(self) -> None:
        super().setUp()
        for m in (goals_mod.PEEKO_DONE_MARKER, goals_mod.LETTER_DONE_MARKER,
                  goals_mod.DEVON_DELIVERED_MARKER, goals_mod.THEFT_DONE_MARKER,
                  goals_mod.MTCHIMNEY_DONE_MARKER,
                  goals_mod.WATER_CATCH_DONE_MARKER):
            m.write_text("1", encoding="utf-8")
        # Production reality: every trek target is long-visited — these tests
        # must genuinely exercise the _GOAL_BYPASS_VISITED entries.
        for mid in ((24, 11), (0, 19), (0, 3), (0, 14), (0, 2), (0, 26),
                    (0, 27), (24, 14), (10, 5), (24, 4), (0, 31), (0, 32)):
            goals_mod.record_map_visit(*mid)

    def _gs(self, **kw):
        base = dict(
            badge_count=4, party_count=6, party0_level=48,
            party0_hp=155, party0_max_hp=155, party0_damaging_pp=15,
            bag_heal_qty=10, bag_pokeball_count=10, money=3575,
            flag_badge04_get=True,
            flag_steven_letter_delivered=True,
            flag_dock_rejected_devon=True,
            flag_devon_goods_delivered=True,
            flag_devon_goods_recovered=True,
            flag_route112_magma_cleared=True,
            flag_mtchimney_magma_defeated=True,
            flag_rock_smash_hm=True, party_moves=[[249, 0, 0, 0]],
        )
        base.update(kw)
        return make_gs(**base)

    def _name(self, **kw):
        g = goals_mod.current_goal(self._gs(**kw))
        return g.name if g else None

    # --- leg 1: Petalburg -> Woods (beats the petalburg gym chain) ---
    def test_city_departs_east_not_gym(self) -> None:
        g = goals_mod.current_goal(self._gs(map_group=0, map_num=0, x=15, y=10))
        self.assertEqual(g.name, "reach_ngrind_woods")
        self.assertEqual(g.target_map, (24, 11))

    def test_gym_interior_walks_out_east(self) -> None:
        # Parked inside the gym (the current live save class): the trek wins
        # over petalburg_enter_gym's target==cur park and walks back out.
        self.assertEqual(
            self._name(map_group=8, map_num=1, x=3, y=106),
            "reach_ngrind_woods")

    def test_city_hurt_heals_first(self) -> None:
        # hp<0.5 / pp==0 are the exact complement of the departure gate:
        # heal_at_petalburg tops the lead up, THEN the trek departs.
        self.assertEqual(
            self._name(map_group=0, map_num=0, x=15, y=10,
                       party0_hp=60), "heal_at_petalburg")
        self.assertEqual(
            self._name(map_group=0, map_num=0, x=15, y=10,
                       party0_damaging_pp=0), "heal_at_petalburg")

    def test_route104_south_heads_to_woods(self) -> None:
        self.assertEqual(
            self._name(map_group=0, map_num=19, x=30, y=50),
            "reach_ngrind_woods")

    # --- leg 2: Woods north pin (beats petalburg_woods_south) ---
    def test_woods_pins_north_exit(self) -> None:
        g = goals_mod.current_goal(self._gs(map_group=24, map_num=11, x=16, y=30))
        self.assertEqual(g.name, "reach_ngrind_woods_north")
        self.assertEqual(g.target_pos, (14, 5))

    def test_woods_north_pin_is_canon_warp(self) -> None:
        # No hardcoded-coord drift: the pin must be a canon Woods->Route104
        # warp on the NORTH edge (min-y row) — the same tile the badge-1
        # peeko_woods_north leg pinned.
        from generic_agent import map_data as md
        info = md.get_cache().get(24, 11)
        if info is None:
            self.skipTest("PetalburgWoods canon cache not present")
        r104 = [(w["x"], w["y"]) for w in (info.warps or [])
                if "Route104" in str(w.get("dest_map", ""))]
        north_row = min(y for _x, y in r104)
        goal = next(g for g in goals_mod.GOAL_TABLE
                    if g.name == "reach_ngrind_woods_north")
        self.assertIn(goal.target_pos, [t for t in r104 if t[1] == north_row])
        peeko = next(g for g in goals_mod.GOAL_TABLE
                     if g.name == "peeko_woods_north")
        self.assertEqual(goal.target_pos, peeko.target_pos)

    # --- leg 3: R104N -> ... -> Mauville umbrella ---
    def test_corridor_maps_head_to_mauville(self) -> None:
        for kw in (
            dict(map_group=0, map_num=19, x=10, y=25),   # R104 north
            dict(map_group=0, map_num=3, x=30, y=30),    # Rustboro
            dict(map_group=11, map_num=3, x=4, y=6),     # Rustboro interior
            dict(map_group=0, map_num=31, x=20, y=8),    # Route116
            dict(map_group=0, map_num=14, x=10, y=8),    # Verdanturf
            dict(map_group=6, map_num=4, x=7, y=8),      # Verdanturf interior
            dict(map_group=0, map_num=32, x=30, y=8),    # Route117
        ):
            g = goals_mod.current_goal(self._gs(**kw))
            self.assertIsNotNone(g, kw)
            self.assertEqual(g.name, "reach_ngrind_mauville", kw)
            self.assertEqual(g.target_map, (0, 2), kw)

    def test_tunnel_crossing_beats_west_exit_pin(self) -> None:
        # LOAD-BEARING ordering: on (24,4) exit_rusturf_west's target==cur +
        # pin returns immediately if scanned first — the trek goal must sit
        # ABOVE it or the eastbound crossing walks back west forever.
        self.assertEqual(
            self._name(map_group=24, map_num=4, x=10, y=5, npcs_on_map=[]),
            "reach_ngrind_mauville")

    def test_tunnel_rocks_still_smash_first(self) -> None:
        # (defensive: the rocks are permanently gone live, but the smash goal
        # must still win its frame if object events report them)
        self.assertEqual(
            self._name(map_group=24, map_num=4, x=29, y=16,
                       npcs_on_map=[(24, 4, 86), (24, 5, 86)]),
            "smash_rusturf_rock")

    # --- leg 4: Mauville -> R111 -> R112 -> Fiery, and the grind itself ---
    def test_mauville_heads_to_fiery_not_verdanturf(self) -> None:
        g = goals_mod.current_goal(self._gs(map_group=0, map_num=2, x=20, y=11))
        self.assertEqual(g.name, "grind_fiery_norman")
        self.assertEqual(g.target_map, (24, 14))
        self.assertEqual(g.target_pos, (26, 23))

    def test_route111_rock_still_smashes_first(self) -> None:
        # The FLAG_TEMP rock respawns every map load; northbound R111 is
        # sealed without the smash (offline probe: None blocked / 91 open).
        self.assertEqual(
            self._name(map_group=0, map_num=26, x=19, y=101,
                       npcs_on_map=[(19, 100, 86), (18, 101, 86)]),
            "smash_route111_rock")

    def test_r111_r112_fiery_grind_owns(self) -> None:
        for kw in (
            dict(map_group=0, map_num=26, x=19, y=110, npcs_on_map=[]),
            dict(map_group=0, map_num=27, x=26, y=44),   # R112 south blob
            dict(map_group=24, map_num=14, x=26, y=23),  # Fiery pin
        ):
            self.assertEqual(self._name(**kw), "grind_fiery_norman", kw)

    def test_grind_pin_matches_flannery_grind_pin(self) -> None:
        # Single source of truth: the proven cave-floor pin (all-0x08
        # neighbours, canon-pinned by TestFlanneryGrind).
        old = next(g for g in goals_mod.GOAL_TABLE
                   if g.name == "grind_fiery_path")
        new = next(g for g in goals_mod.GOAL_TABLE
                   if g.name == "grind_fiery_norman")
        self.assertEqual(new.target_pos, old.target_pos)
        self.assertEqual(new.target_map, old.target_map)

    # --- heal loop: Mauville PC ---
    def test_hurt_or_pp_dry_heals_at_mauville(self) -> None:
        for kw in (
            dict(map_group=24, map_num=14, x=26, y=23, party0_hp=50),
            dict(map_group=24, map_num=14, x=26, y=23, party0_damaging_pp=0),
            dict(map_group=0, map_num=2, x=20, y=11, party0_hp=50),
            dict(map_group=10, map_num=5, x=7, y=8, party0_hp=50),  # in the PC
        ):
            g = goals_mod.current_goal(self._gs(**kw))
            self.assertIsNotNone(g, kw)
            self.assertEqual(g.name, "heal_at_mauville_ngrind", kw)
            self.assertEqual(g.target_map, (10, 5), kw)
            self.assertEqual(g.target_pos, (7, 3), kw)

    def test_grind_band_keeps_grinding_until_040(self) -> None:
        # hp in [0.4, 0.5): the grind (scanned first) keeps the tile — the
        # grind_fiery_path / heal_at_lavaridge band pairing.
        self.assertEqual(
            self._name(map_group=24, map_num=14, x=26, y=23,
                       party0_hp=70),  # 70/155 = 0.45
            "grind_fiery_norman")

    def test_heal_geometry_matches_wattson_era_pc(self) -> None:
        old = next(g for g in goals_mod.GOAL_TABLE
                   if g.name == "heal_at_mauville")
        new = next(g for g in goals_mod.GOAL_TABLE
                   if g.name == "heal_at_mauville_ngrind")
        self.assertEqual(new.target_map, old.target_map)
        self.assertEqual(new.target_pos, old.target_pos)

    # --- retire: at target level the b5 chain resumes, byte-for-byte ---
    def test_retire_resumes_b5_chain_eastside(self) -> None:
        lvl = goals_mod.NORMAN_GRIND_TARGET_LEVEL
        self.assertEqual(
            self._name(map_group=24, map_num=14, x=26, y=23,
                       party0_level=lvl), "reach_mauville_b5")
        self.assertEqual(
            self._name(map_group=0, map_num=27, x=26, y=44,
                       party0_level=lvl), "reach_mauville_b5")
        self.assertEqual(
            self._name(map_group=0, map_num=2, x=20, y=11,
                       party0_level=lvl), "reach_verdanturf_b5")
        self.assertEqual(
            self._name(map_group=0, map_num=14, x=10, y=8,
                       party0_level=lvl), "reach_rustboro_b5")
        self.assertEqual(
            self._name(map_group=24, map_num=4, x=25, y=4,
                       party0_level=lvl, npcs_on_map=[(24, 5, 86)]),
            "exit_rusturf_west")

    def test_retire_resumes_petalburg_chain(self) -> None:
        lvl = goals_mod.NORMAN_GRIND_TARGET_LEVEL
        self.assertEqual(
            self._name(map_group=24, map_num=11, x=16, y=30,
                       party0_level=lvl), "petalburg_woods_south")
        self.assertEqual(
            self._name(map_group=0, map_num=19, x=30, y=50,
                       party0_level=lvl), "petalburg_to_city")
        self.assertEqual(
            self._name(map_group=0, map_num=0, x=15, y=10,
                       party0_level=lvl), "petalburg_enter_gym")

    def test_retire_hurt_still_heals_at_mauville(self) -> None:
        # The heal is deliberately NOT level-gated: a hurt at-target lead on
        # the way home still tops up at the Mauville PC (the westbound chain
        # has no heal between Lavaridge and Petalburg).
        self.assertEqual(
            self._name(map_group=0, map_num=27, x=26, y=44,
                       party0_level=goals_mod.NORMAN_GRIND_TARGET_LEVEL,
                       party0_hp=50),
            "heal_at_mauville_ngrind")

    # --- era + flicker guards ---
    def test_badge3_era_never_fires_grind_arc(self) -> None:
        name = self._name(map_group=0, map_num=2, x=20, y=11,
                          badge_count=3, flag_badge04_get=False,
                          party0_level=40)
        self.assertIsNotNone(name)
        self.assertFalse(
            name.startswith(("reach_ngrind", "grind_fiery_norman",
                             "heal_at_mauville_ngrind")), name)

    def test_badge5_retires_whole_arc(self) -> None:
        for kw in (
            dict(map_group=24, map_num=14, x=26, y=23),
            dict(map_group=0, map_num=0, x=15, y=10),
            dict(map_group=0, map_num=2, x=20, y=11, party0_hp=50),
        ):
            name = self._name(badge_count=5, **kw)
            self.assertFalse(
                (name or "").startswith(
                    ("reach_ngrind", "grind_fiery_norman",
                     "heal_at_mauville_ngrind")), (kw, name))

    def test_party_count_flicker_cannot_drop_trek(self) -> None:
        # No party_count / story-flag terms in the arc gates: a transient
        # party_count=0 read frame must not hand the corridor back to the
        # petalburg chain (the goal=None / east-yank flicker family).
        self.assertEqual(
            self._name(map_group=0, map_num=31, x=20, y=8, party_count=0),
            "reach_ngrind_mauville")

    def test_pp_unreadable_keeps_grinding(self) -> None:
        # party0_damaging_pp == -1 (unreadable) must read as "keep going",
        # not as PP-dry (the grind_fiery_path convention).
        self.assertEqual(
            self._name(map_group=24, map_num=14, x=26, y=23,
                       party0_damaging_pp=-1),
            "grind_fiery_norman")


if __name__ == "__main__":
    unittest.main()
