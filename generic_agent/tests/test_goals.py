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
        )
        goals_mod.GOALS_FILE = tmp / "goal_notes.jsonl"
        goals_mod.VISITED_MAPS_FILE = tmp / "visited_maps.json"
        goals_mod.PEEKO_DONE_MARKER = tmp / "peeko_done.marker"
        goals_mod.LETTER_DONE_MARKER = tmp / "steven_letter_done.marker"
        goals_mod.DEVON_DELIVERED_MARKER = tmp / "devon_delivered.marker"
        goals_mod.ROCK_SMASH_TAUGHT_MARKER = tmp / "rock_smash_taught.marker"
        goals_mod.THEFT_DONE_MARKER = tmp / "meteor_theft_done.marker"
        goals_mod.MTCHIMNEY_DONE_MARKER = tmp / "mtchimney_done.marker"

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

    def test_badge4_retires_lavaridge_arc(self) -> None:
        # Once Flannery is beaten (FLAG_BADGE04_GET) the Lavaridge arc retires
        # (Petalburg/Norman is the next unimplemented step -> None).
        gs = make_gs(map_group=0, map_num=13, badge_count=4,
                     flag_steven_letter_delivered=True,
                     flag_dock_rejected_devon=True,
                     flag_devon_goods_delivered=True,
                     flag_badge04_get=True)
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
    """H17: sub-target lead grinds the Jagged Pass grass before Flannery.

    Mirror of the Brawly grind loop (grind_granite_cave / heal_at_dewford_pc),
    but the grass is enterable on foot only from the TOP of the pass (07-22:
    bumpy-slope walls), so the full cycle is: town/PC/pocket ->
    grind_reboard_cable_car / ride_cable_car (station) -> Mt.Chimney ->
    descend -> grind_pre_flannery pins the grass ON the pass; the heal cycle
    is descend (walk home) -> pocket -> heal_at_lavaridge -> PC; at the
    target level both loop goals retire and the descend/reach/gym goals
    resume the Flannery push."""

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

    def test_under_target_town_routes_to_cable_car(self) -> None:
        # Healthy sub-target lead in Lavaridge -> re-board the cable car
        # (07-22: the grass is enterable only from the TOP of the pass —
        # bumpy-slope walls — so town must NOT aim at JaggedPass directly),
        # and NOT the gym (the goal sits above lavaridge_gym_flannery).
        g = goals_mod.current_goal(self._gs(map_group=0, map_num=12, x=5, y=10))
        self.assertEqual(g.name, "grind_reboard_cable_car")
        self.assertEqual(g.target_map, (19, 0))

    def test_under_target_pins_grass_on_jagged_pass(self) -> None:
        # On JaggedPass itself the grind goal must outrank descend_jagged_pass
        # (also matching there) and pin the canon grass tile.
        g = goals_mod.current_goal(
            self._gs(map_group=24, map_num=13, x=14, y=39))
        self.assertEqual(g.name, "grind_pre_flannery")
        self.assertEqual(g.target_pos, (19, 32))

    def test_under_target_pocket_pc_gym_route_to_cable_car(self) -> None:
        # Healed at the PC / standing in the pocket / walked into the gym
        # under-level -> all take the road leg toward the station.
        self.assertEqual(
            self._name(map_group=0, map_num=27, x=7, y=47),
            "grind_reboard_cable_car")
        self.assertEqual(
            self._name(map_group=4, map_num=5, x=7, y=4),
            "grind_reboard_cable_car")
        self.assertEqual(
            self._name(map_group=4, map_num=1, x=5, y=10),
            "grind_reboard_cable_car")

    def test_under_target_south_blob_keeps_ride_cable_car(self) -> None:
        # Outside the pocket on Route112 the pre-existing ride_cable_car
        # (earlier in GOAL_TABLE) owns the same station target — the road
        # leg hands over seamlessly once the lead hops down the ledges.
        self.assertEqual(
            self._name(map_group=0, map_num=27, x=26, y=36),
            "ride_cable_car")

    def test_at_target_flannery_push_resumes(self) -> None:
        # The retire boundary: at the target level the grind goes silent and
        # the pre-existing arc goals drive to Flannery from every loop map.
        L = dict(party0_level=self.TARGET)
        self.assertEqual(
            self._name(map_group=0, map_num=12, x=5, y=10, **L),
            "lavaridge_gym_flannery")
        self.assertEqual(
            self._name(map_group=24, map_num=13, x=19, y=32, **L),
            "descend_jagged_pass")
        self.assertEqual(
            self._name(map_group=0, map_num=27, x=7, y=47, **L),
            "reach_lavaridge")

    def test_hurt_no_potions_walks_home_and_heals(self) -> None:
        # Grind yields on its hp gate (< 0.5): on the pass the descend goal is
        # the walk home; in the pocket / town heal_at_lavaridge (with the new
        # (0,27) cur entry) routes on to the PC nurse.
        H = dict(party0_hp=40, bag_heal_qty=0)
        self.assertEqual(
            self._name(map_group=24, map_num=13, x=19, y=32, **H),
            "descend_jagged_pass")
        self.assertEqual(
            self._name(map_group=0, map_num=27, x=7, y=47, **H),
            "heal_at_lavaridge")
        self.assertEqual(
            self._name(map_group=0, map_num=12, x=5, y=10, **H),
            "heal_at_lavaridge")

    def test_hurt_with_potions_field_heals_in_place(self) -> None:
        # With restores in the bag, field_heal_potion (above the grind goal)
        # heals on the spot instead of abandoning the grind for the PC.
        self.assertEqual(
            self._name(map_group=24, map_num=13, x=19, y=32,
                       party0_hp=40, bag_heal_qty=5),
            "field_heal_potion")

    def test_pre_mtchimney_never_grinds(self) -> None:
        # Before the Mt.Chimney Magma defeat the grind must not fire — the
        # badge-4 gauntlet goals still own JaggedPass and Route112 south is
        # crossed en route to Fallarbor pre-theft.
        goals_mod.MTCHIMNEY_DONE_MARKER.unlink()
        self.assertEqual(
            self._name(map_group=24, map_num=13, x=14, y=39,
                       flag_mtchimney_magma_defeated=False),
            "mtchimney_defeat_magma")

    def test_badge04_retires_grind(self) -> None:
        # Heat Badge won -> the whole arc (grind included) retires.
        self.assertIsNone(goals_mod.current_goal(self._gs(
            map_group=24, map_num=13, x=19, y=32, flag_badge04_get=True)))

    def test_visited_bypass_keeps_retargeting_the_loop(self) -> None:
        # JaggedPass AND the cable-car station are long-visited from the
        # first ascent/descent; the loop goals must keep re-targeting them
        # every heal cycle.
        goals_mod.record_map_visit(24, 13)
        goals_mod.record_map_visit(19, 0)
        self.assertEqual(
            self._name(map_group=0, map_num=12, x=5, y=10),
            "grind_reboard_cable_car")
        self.assertEqual(
            self._name(map_group=24, map_num=13, x=14, y=39),
            "grind_pre_flannery")

    def test_grind_pin_is_canon_grass_and_reboard_mirrors_ride(self) -> None:
        # No hardcoded-coord drift: (a) the pin must be a canon
        # wild-encounter grass tile (behavior in GRASS_BEHAVIORS) — the old
        # "same walkable component as the bottom warp pair" assertion is
        # gone: raw-collision components glue regions together across
        # MB_BUMPY_SLOPE tiles, so it never proved walk reachability (the
        # walk-model facts live in test_jagged_nav) — and (b) the road-leg
        # goal must mirror ride_cable_car's station target exactly so the
        # pocket->south-blob handover stays seamless. Reads only cached
        # map_cache files, like the other canon tests.
        import json as _json
        import struct as _struct

        from generic_agent import config as _config, map_data as _md
        from generic_agent.map_knowledge import GRASS_BEHAVIORS

        goal = next(g for g in goals_mod.GOAL_TABLE
                    if g.name == "grind_pre_flannery")
        self.assertEqual(goal.target_map, (24, 13))
        reboard = next(g for g in goals_mod.GOAL_TABLE
                       if g.name == "grind_reboard_cable_car")
        ride = next(g for g in goals_mod.GOAL_TABLE
                    if g.name == "ride_cable_car")
        self.assertEqual(reboard.target_map, ride.target_map)
        self.assertEqual(reboard.target_pos, ride.target_pos)
        mc = _md.get_cache()
        info = mc.get(24, 13)
        self.assertIsNotNone(info)
        # behavior byte of the pin tile via the layout's tileset pair
        cache_dir = _config.MEMORY_DIR / "map_cache"
        layout_id = _json.loads(
            (cache_dir / "JaggedPass.map.json").read_text(encoding="utf-8")
        )["layout"]
        pair = _md.layout_tilesets(layout_id)
        self.assertIsNotNone(pair)
        prim, sec = pair
        table: dict[int, int] = {}
        pdata = _md.tileset_attr_path("primary", prim).read_bytes()
        for meta in range(min(0x200, len(pdata) // 2)):
            table[meta] = _struct.unpack_from("<H", pdata, meta * 2)[0] & 0xFF
        sdata = _md.tileset_attr_path("secondary", sec).read_bytes()
        for i in range(len(sdata) // 2):
            table[0x200 + i] = _struct.unpack_from("<H", sdata, i * 2)[0] & 0xFF
        raw = (cache_dir / "JaggedPass.map.bin").read_bytes()
        x, y = goal.target_pos
        block = _struct.unpack_from("<H", raw, (y * info.width + x) * 2)[0]
        self.assertIn(table.get(block & 0x3FF), GRASS_BEHAVIORS)


if __name__ == "__main__":
    unittest.main()
