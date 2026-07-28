"""Party-grind mode tests (2026-07-28).

Three layers:
  1. plan_action / marker plumbing (pure logic).
  2. The reorder state machine against a scripted fake game model --
     happy path, press-drops, battle interruption, and the wrong-offset
     degradation (gives up cleanly, never scrambles blindly).
     NB: the fake encodes the DESIGN model of the menu topology (START
     overlay semantics, party-menu cursor graph, switch mode). It proves
     the SM is drop-tolerant and bounded GIVEN that model; whether the
     model matches the live game is exactly what the small-step live plan
     verifies (field party cb2 == 0x081B01B1, slotId2/action offsets).
  3. Goal gating: the pgrind chain owns the Petalburg->Rusturf corridor
     while the marker is on; the Norman-grind (Fiery/Mauville) arc can
     NEVER fire for a weak trainee lead (marker gate + lead floor) -- the
     regression that would re-create the 07-25 Mauville trap.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from generic_agent import goals as goals_mod
from generic_agent import party_grind as pg
from generic_agent.state import GameState

CB2_OVERWORLD = pg.CB2_OVERWORLD
CB2_PARTY = pg.CB2_PARTY_MENU
CB2_BATTLE = pg.CB2_BATTLE_MAIN
CB2_OTHER = 0x0BADF00D


class TestPlanAction(unittest.TestCase):
    def test_slot0_below_target_keeps_grinding(self) -> None:
        slots = [(0xA, 5), (0xB, 48), (0xC, 16)]
        self.assertEqual(pg.plan_action(slots, 18), ("grind", None))

    def test_rotation_picks_lowest_below_target(self) -> None:
        # slot0 done (18), backups at 16/5/11 -> promote the 5 (slot 2)
        slots = [(0xA, 18), (0xB, 16), (0xC, 5), (0xD, 48), (0xE, 11)]
        self.assertEqual(pg.plan_action(slots, 18), ("swap", 2))

    def test_all_done_restores_strongest(self) -> None:
        slots = [(0xA, 18), (0xB, 19), (0xC, 48)]
        self.assertEqual(pg.plan_action(slots, 18), ("restore", 2))

    def test_strongest_leading_retires(self) -> None:
        slots = [(0xC, 48), (0xA, 18), (0xB, 19)]
        self.assertEqual(pg.plan_action(slots, 18), ("retire", None))

    def test_strong_lead_with_weak_backups_swaps(self) -> None:
        # The mode's very first action: Sceptile leads, five trainees behind.
        slots = [(0x5CE, 48), (0x1, 16), (0x2, 11), (0x3, 17), (0x4, 5), (0x5, 5)]
        self.assertEqual(pg.plan_action(slots, 18), ("swap", 4))


class TestMarker(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = pg.PARTY_GRIND_MARKER
        pg.PARTY_GRIND_MARKER = Path(self._tmp.name) / "party_grind.on"

    def tearDown(self) -> None:
        pg.PARTY_GRIND_MARKER = self._orig
        self._tmp.cleanup()

    def test_marker_toggles_active(self) -> None:
        self.assertFalse(pg.party_grind_active())
        pg.PARTY_GRIND_MARKER.write_text("", encoding="utf-8")
        self.assertTrue(pg.party_grind_active())
        pg.retire_party_grind()
        self.assertFalse(pg.party_grind_active())

    def test_target_level_default_and_override(self) -> None:
        pg.PARTY_GRIND_MARKER.write_text("", encoding="utf-8")
        self.assertEqual(pg.party_grind_target_level(), pg.PARTY_GRIND_DEFAULT_TARGET)
        pg.PARTY_GRIND_MARKER.write_text("23", encoding="utf-8")
        self.assertEqual(pg.party_grind_target_level(), 23)
        pg.PARTY_GRIND_MARKER.write_text("garbage", encoding="utf-8")
        self.assertEqual(pg.party_grind_target_level(), pg.PARTY_GRIND_DEFAULT_TARGET)
        pg.PARTY_GRIND_MARKER.write_text("999", encoding="utf-8")
        self.assertEqual(pg.party_grind_target_level(), pg.PARTY_GRIND_DEFAULT_TARGET)


# ---------------------------------------------------------------------------
# Fake game model for the reorder SM.
# ---------------------------------------------------------------------------

# START menu items at badge 4: index 1 == POKEMON (the fast path presses
# Start, Down, A from a top-reset cursor).
_ITEM_PARTY = 1
_ITEM_SAVE = 5
_ITEM_EXIT = 7


class FakeGame:
    """Scripted Emerald menu model (field <-> START overlay <-> party menu).

    Encodes the design assumptions: START is a cb2-invisible overlay whose
    cursor persists; the party menu is cb2 0x081B01B1 with slotId at the
    measured byte; SWITCH mode moves slotId2 and swaps on A at slot 0.
    """

    def __init__(self, party, drop_every: int = 0, battle_at_press: int | None = None):
        self.party = list(party)          # [(pv, level)]
        self.mode = "field"
        self.start_cursor = 0
        self.slot = 0
        self.slot2 = 0
        self.action = 0
        self.act_cursor = 0
        self.presses = 0
        self.drop_every = drop_every
        self.battle_at_press = battle_at_press
        self.in_battle = False
        self.field_steps = 0

    # -- cb2 -------------------------------------------------------------
    def _cb2(self) -> int:
        if self.in_battle:
            return CB2_BATTLE
        if self.mode in ("field", "start_menu", "save_dialog"):
            return CB2_OVERWORLD
        if self.mode in ("party", "action_menu", "switch", "item_menu"):
            return CB2_PARTY
        return CB2_OTHER  # summary / pokedex / bag / pokenav / options

    # -- MGBAClient surface ---------------------------------------------
    def read32(self, addr: int) -> int:
        from generic_agent.state import GMAIN_CB2_ADDR, PLAYER_PARTY_ADDR, \
            POKEMON_STRUCT_SIZE
        if addr == GMAIN_CB2_ADDR:
            return self._cb2()
        if addr >= PLAYER_PARTY_ADDR:
            idx = (addr - PLAYER_PARTY_ADDR) // POKEMON_STRUCT_SIZE
            off = (addr - PLAYER_PARTY_ADDR) % POKEMON_STRUCT_SIZE
            if off == 0 and idx < len(self.party):
                return self.party[idx][0]
        return 0

    def read8(self, addr: int) -> int:
        from generic_agent.state import PLAYER_PARTY_ADDR, \
            PLAYER_PARTY_COUNT_ADDR, POKEMON_LEVEL_OFFSET, POKEMON_STRUCT_SIZE
        if addr == PLAYER_PARTY_COUNT_ADDR:
            return len(self.party)
        if addr == pg.GPARTY_MENU_SLOT_ADDR:
            return self.slot
        if addr == pg.GPARTY_MENU_SLOT2_ADDR:
            return self.slot2
        if addr == pg.GPARTY_MENU_ACTION_ADDR:
            return self.action
        if addr >= PLAYER_PARTY_ADDR:
            idx = (addr - PLAYER_PARTY_ADDR) // POKEMON_STRUCT_SIZE
            off = (addr - PLAYER_PARTY_ADDR) % POKEMON_STRUCT_SIZE
            if off == POKEMON_LEVEL_OFFSET and idx < len(self.party):
                return self.party[idx][1]
        return 0

    def read16(self, addr: int) -> int:
        return 0

    def tap(self, button: str, frames: int = 15) -> None:
        self.presses += 1
        if self.battle_at_press is not None and self.presses >= self.battle_at_press:
            self.in_battle = True
            return
        if self.drop_every and self.presses % self.drop_every == 0:
            return  # dropped press
        self._apply(button)

    # -- button semantics -------------------------------------------------
    def _nav_slot(self, cur: int, button: str) -> int:
        # slot0 = big left panel; 1..5 right column; 6 = CANCEL strip.
        n = len(self.party)
        if button == "Right":
            return 1 if cur == 0 and n > 1 else cur
        if button == "Left":
            return 0 if cur == 1 else cur
        if button == "Down":
            if cur == 0:
                return 6
            if cur < n - 1:
                return cur + 1
            return 6
        if button == "Up":
            if cur == 6:
                return n - 1
            if 2 <= cur <= 6:
                return cur - 1
            return cur
        return cur

    def _apply(self, b: str) -> None:
        m = self.mode
        if m == "field":
            if b == "Start":
                self.mode = "start_menu"
            elif b in ("Up", "Down", "Left", "Right"):
                self.field_steps += 1
            return
        if m == "start_menu":
            if b in ("Start", "B"):
                self.mode = "field"
            elif b == "Down":
                self.start_cursor = (self.start_cursor + 1) % 8
            elif b == "Up":
                self.start_cursor = (self.start_cursor - 1) % 8
            elif b == "A":
                if self.start_cursor == _ITEM_PARTY:
                    self.mode = "party"
                    self.slot = 0
                    self.action = 0
                elif self.start_cursor == _ITEM_SAVE:
                    self.mode = "save_dialog"
                elif self.start_cursor == _ITEM_EXIT:
                    self.mode = "field"
                else:
                    self.mode = "other_menu"
            return
        if m == "save_dialog":
            if b == "B":
                self.mode = "field"
            return
        if m == "other_menu":
            if b == "B":
                self.mode = "start_menu"
            return
        if m == "party":
            if b == "B":
                self.mode = "start_menu"
            elif b == "A":
                if self.slot == 6:
                    self.mode = "start_menu"
                else:
                    self.mode = "action_menu"
                    self.act_cursor = 0
            else:
                self.slot = self._nav_slot(self.slot, b)
            return
        if m == "action_menu":
            if b == "B":
                self.mode = "party"
            elif b == "Down":
                self.act_cursor = min(3, self.act_cursor + 1)
            elif b == "Up":
                self.act_cursor = max(0, self.act_cursor - 1)
            elif b == "A":
                if self.act_cursor == 0:
                    self.mode = "summary"
                elif self.act_cursor == 1:
                    self.mode = "switch"
                    self.action = 8   # any nonzero "mode engaged" value
                    self.slot2 = self.slot
                elif self.act_cursor == 2:
                    self.mode = "item_menu"
                else:
                    self.mode = "party"
            return
        if m == "summary":
            if b == "B":
                self.mode = "party"
            return
        if m == "item_menu":
            if b == "B":
                self.mode = "action_menu"
            return
        if m == "switch":
            if b == "B":
                self.mode = "party"
                self.action = 0
            elif b == "A":
                if self.slot2 != self.slot:
                    p = self.party
                    p[self.slot], p[self.slot2] = p[self.slot2], p[self.slot]
                self.mode = "party"
                self.action = 0
            else:
                self.slot2 = self._nav_slot(self.slot2, b)
            return


class ReorderSMTestBase(unittest.TestCase):
    PARTY = [(0x5CE, 48), (0x101, 16), (0x102, 11), (0x103, 17),
             (0x104, 5), (0x105, 5)]

    def setUp(self) -> None:
        self._orig_sleep = pg._sleep
        pg._sleep = lambda s: None

    def tearDown(self) -> None:
        pg._sleep = self._orig_sleep


class TestReorderSM(ReorderSMTestBase):
    def test_happy_path_swaps_target_to_slot0(self) -> None:
        game = FakeGame(self.PARTY)
        ok = pg.run_reorder(game, 0x104)
        self.assertTrue(ok)
        self.assertEqual(game.party[0][0], 0x104)
        self.assertEqual(game.party[4][0], 0x5CE)  # displaced lead
        self.assertEqual(game.mode, "field")       # unwound cleanly
        self.assertEqual(game.field_steps, 0)      # no stray overworld walk

    def test_restore_slot1(self) -> None:
        # After training: strongest at slot 3 -> restore to front.
        party = [(0x104, 18), (0x101, 18), (0x102, 19), (0x5CE, 48)]
        game = FakeGame(party)
        ok = pg.run_reorder(game, 0x5CE)
        self.assertTrue(ok)
        self.assertEqual(game.party[0][0], 0x5CE)

    def test_press_drops_still_converge(self) -> None:
        # Every 5th press silently dropped: read-verify-retry must converge.
        game = FakeGame(self.PARTY, drop_every=5)
        ok = pg.run_reorder(game, 0x104)
        self.assertTrue(ok)
        self.assertEqual(game.party[0][0], 0x104)

    def test_heavy_drops_never_scramble_blindly(self) -> None:
        # Aggressive drops may make the SM give up -- that's allowed -- but
        # the party must remain a permutation of itself and the fake must not
        # be left mid-battle/menu-limbo by the unwind.
        game = FakeGame(self.PARTY, drop_every=3)
        pg.run_reorder(game, 0x104)
        self.assertEqual(sorted(p for p, _ in game.party),
                         sorted(p for p, _ in self.PARTY))

    def test_battle_interruption_aborts(self) -> None:
        game = FakeGame(self.PARTY, battle_at_press=4)
        ok = pg.run_reorder(game, 0x104)
        self.assertFalse(ok)
        # party untouched
        self.assertEqual([p for p, _ in game.party],
                         [p for p, _ in self.PARTY])

    def test_wrong_slot2_offset_gives_up_cleanly(self) -> None:
        # If the derived slotId2 address is wrong on real hardware, its reads
        # won't track presses. The SM must give up (False) without swapping.
        class BadSlot2(FakeGame):
            def read8(self, addr):
                if addr == pg.GPARTY_MENU_SLOT2_ADDR:
                    return 255
                return super().read8(addr)
        game = BadSlot2(self.PARTY)
        ok = pg.run_reorder(game, 0x104)
        self.assertFalse(ok)
        self.assertEqual([p for p, _ in game.party],
                         [p for p, _ in self.PARTY])

    def test_start_cursor_desync_recovers(self) -> None:
        # Session left the START cursor on SAVE: fast path opens the save
        # dialog; the probe walk must still find POKEMON.
        game = FakeGame(self.PARTY)
        game.start_cursor = _ITEM_SAVE
        ok = pg.run_reorder(game, 0x104)
        self.assertTrue(ok)
        self.assertEqual(game.party[0][0], 0x104)

    def test_target_already_leading_is_noop_success(self) -> None:
        game = FakeGame(self.PARTY)
        ok = pg.run_reorder(game, 0x5CE)
        self.assertTrue(ok)
        self.assertEqual(game.presses, 0)


class TestEnsureLead(ReorderSMTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_marker = pg.PARTY_GRIND_MARKER
        pg.PARTY_GRIND_MARKER = Path(self._tmp.name) / "party_grind.on"
        pg.PARTY_GRIND_MARKER.write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        pg.PARTY_GRIND_MARKER = self._orig_marker
        self._tmp.cleanup()
        super().tearDown()

    def test_first_call_swaps_lowest_backup_in(self) -> None:
        game = FakeGame(self.PARTY)
        acted, why = pg.ensure_lead(game)
        self.assertTrue(acted)
        self.assertEqual(why, "swapped")
        self.assertEqual(game.party[0][0], 0x104)  # the L5 at slot 4

    def test_trainee_leading_is_noop(self) -> None:
        party = [(0x104, 7), (0x5CE, 48), (0x101, 16)]
        game = FakeGame(party)
        acted, why = pg.ensure_lead(game)
        self.assertFalse(acted)
        self.assertEqual(why, "noop")
        self.assertEqual(game.presses, 0)

    def test_all_done_restores_then_retires(self) -> None:
        party = [(0x104, 18), (0x101, 19), (0x5CE, 48)]
        game = FakeGame(party)
        acted, why = pg.ensure_lead(game)
        self.assertTrue(acted)
        self.assertEqual(why, "restored")
        self.assertEqual(game.party[0][0], 0x5CE)
        self.assertTrue(pg.party_grind_active())   # not yet retired
        acted, why = pg.ensure_lead(game)
        self.assertTrue(acted)
        self.assertEqual(why, "retired")
        self.assertFalse(pg.party_grind_active())

    def test_marker_off_is_off(self) -> None:
        pg.retire_party_grind()
        game = FakeGame(self.PARTY)
        acted, why = pg.ensure_lead(game)
        self.assertFalse(acted)
        self.assertEqual(why, "off")
        self.assertEqual(game.presses, 0)


# ---------------------------------------------------------------------------
# Goal gating.
# ---------------------------------------------------------------------------

def make_gs(**kw) -> GameState:
    base = dict(
        map_group=0, map_num=0, x=5, y=5, saveblock1_valid=True,
        party_count=6, badge_count=4, total_event_flags=0,
        event_flag_bytes_hex="",
        # Badge-4 era raw flags (matching the live save): badge04 earned,
        # HM06 received. Without these the earlier badge-3 arc goals
        # (get_rock_smash etc.) win the scan and mask the corridor.
        flag_badge04_get=True, flag_rock_smash_hm=True,
    )
    base.update(kw)
    return GameState(**base)


class PgrindGoalsBase(unittest.TestCase):
    """Hermetic goals harness (mirrors test_goals.GoalsTestBase) with the
    party-grind marker ON and the water-catch latch set (badge-4 era)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._orig = (
            goals_mod.GOALS_FILE, goals_mod.VISITED_MAPS_FILE,
            goals_mod.PEEKO_DONE_MARKER, goals_mod.LETTER_DONE_MARKER,
            goals_mod.DEVON_DELIVERED_MARKER,
            goals_mod.ROCK_SMASH_TAUGHT_MARKER, goals_mod.THEFT_DONE_MARKER,
            goals_mod.MTCHIMNEY_DONE_MARKER,
            goals_mod.WATER_CATCH_DONE_MARKER,
            pg.PARTY_GRIND_MARKER,
        )
        goals_mod.GOALS_FILE = tmp / "goal_notes.jsonl"
        goals_mod.VISITED_MAPS_FILE = tmp / "visited_maps.json"
        goals_mod.PEEKO_DONE_MARKER = tmp / "peeko_done.marker"
        goals_mod.LETTER_DONE_MARKER = tmp / "letter.marker"
        goals_mod.DEVON_DELIVERED_MARKER = tmp / "devon.marker"
        goals_mod.ROCK_SMASH_TAUGHT_MARKER = tmp / "rs.marker"
        goals_mod.THEFT_DONE_MARKER = tmp / "theft.marker"
        goals_mod.MTCHIMNEY_DONE_MARKER = tmp / "mtc.marker"
        goals_mod.WATER_CATCH_DONE_MARKER = tmp / "wc.marker"
        pg.PARTY_GRIND_MARKER = tmp / "party_grind.on"
        # Badge-4 era reality: every earlier story latch is long on disk
        # (without them the uncapped badge>=1 goals like rescue_peeko match).
        for m in (
            goals_mod.PEEKO_DONE_MARKER, goals_mod.LETTER_DONE_MARKER,
            goals_mod.DEVON_DELIVERED_MARKER,
            goals_mod.ROCK_SMASH_TAUGHT_MARKER, goals_mod.THEFT_DONE_MARKER,
            goals_mod.MTCHIMNEY_DONE_MARKER,
            goals_mod.WATER_CATCH_DONE_MARKER,
        ):
            m.write_text("1", encoding="utf-8")
        pg.PARTY_GRIND_MARKER.write_text("", encoding="utf-8")

    def tearDown(self) -> None:
        (
            goals_mod.GOALS_FILE, goals_mod.VISITED_MAPS_FILE,
            goals_mod.PEEKO_DONE_MARKER, goals_mod.LETTER_DONE_MARKER,
            goals_mod.DEVON_DELIVERED_MARKER,
            goals_mod.ROCK_SMASH_TAUGHT_MARKER, goals_mod.THEFT_DONE_MARKER,
            goals_mod.MTCHIMNEY_DONE_MARKER,
            goals_mod.WATER_CATCH_DONE_MARKER,
            pg.PARTY_GRIND_MARKER,
        ) = self._orig
        self._tmp.cleanup()

    def goal(self, **kw):
        return goals_mod.current_goal(make_gs(**kw))


class TestPgrindCorridor(PgrindGoalsBase):
    """Marker ON: the pgrind chain owns the Petalburg->Rusturf corridor.
    Lead level deliberately varies (5..48): the chain is lead-agnostic."""

    def test_petalburg_starts_the_trek(self) -> None:
        g = self.goal(map_group=0, map_num=0, party0_level=48)
        self.assertEqual(g.name, "pgrind_to_woods")
        self.assertEqual(g.target_map, (24, 11))

    def test_petalburg_interior_walks_out(self) -> None:
        g = self.goal(map_group=8, map_num=4, party0_level=12)
        self.assertEqual(g.name, "pgrind_to_woods")

    def test_hurt_at_petalburg_heals_first(self) -> None:
        g = self.goal(map_group=0, map_num=0, party0_level=48,
                      party0_hp=20, party0_max_hp=100)
        self.assertEqual(g.name, "heal_at_petalburg")

    def test_route104_south_crosses_into_woods(self) -> None:
        g = self.goal(map_group=0, map_num=19, y=40, party0_level=48)
        self.assertEqual(g.name, "pgrind_to_woods")

    def test_woods_pins_north_exit(self) -> None:
        g = self.goal(map_group=24, map_num=11, party0_level=5)
        self.assertEqual(g.name, "pgrind_woods_north")
        self.assertEqual(g.target_pos, (14, 5))

    def test_route104_north_heads_to_rusturf(self) -> None:
        g = self.goal(map_group=0, map_num=19, y=10, party0_level=48)
        self.assertEqual(g.name, "pgrind_to_rusturf")
        self.assertEqual(g.target_map, (24, 4))

    def test_rustboro_heads_to_rusturf(self) -> None:
        g = self.goal(map_group=0, map_num=3, party0_level=48)
        self.assertEqual(g.name, "pgrind_to_rusturf")

    def test_tunnel_grinds_at_pin(self) -> None:
        g = self.goal(map_group=24, map_num=4, party0_level=5)
        self.assertEqual(g.name, "grind_party_rusturf")
        self.assertEqual(g.target_pos, (23, 14))

    def test_tunnel_grind_is_lead_agnostic(self) -> None:
        # Pre-reorder frame: Sceptile still leads -- the goal must hold the
        # tile so the hook can run (never exit_rusturf_west's westward pin).
        g = self.goal(map_group=24, map_num=4, party0_level=48)
        self.assertEqual(g.name, "grind_party_rusturf")

    def test_hurt_in_tunnel_pins_west_exit(self) -> None:
        g = self.goal(map_group=24, map_num=4, party0_level=9,
                      party0_hp=10, party0_max_hp=40)
        self.assertEqual(g.name, "pgrind_exit_west")
        self.assertEqual(g.target_pos, (4, 10))

    def test_pp_dry_in_tunnel_pins_west_exit(self) -> None:
        g = self.goal(map_group=24, map_num=4, party0_level=9,
                      party0_damaging_pp=0)
        self.assertEqual(g.name, "pgrind_exit_west")

    def test_hurt_on_route116_heals_at_rustboro(self) -> None:
        g = self.goal(map_group=0, map_num=31, party0_level=9,
                      party0_hp=10, party0_max_hp=40)
        self.assertEqual(g.name, "pgrind_heal_rustboro")
        self.assertEqual(g.target_map, (11, 5))
        self.assertEqual(g.target_pos, (7, 3))

    def test_healed_at_pc_walks_back_out(self) -> None:
        # Inside the Rustboro PC, healthy again -> back toward the tunnel.
        g = self.goal(map_group=11, map_num=5, party0_level=9)
        self.assertEqual(g.name, "pgrind_to_rusturf")

    def test_mauville_drift_comes_home_west(self) -> None:
        # Marker on but agent somehow east at Mauville with a trainee lead:
        # the ngrind arc must NOT fire; the b5 westward leg drives home.
        g = self.goal(map_group=0, map_num=2, party0_level=12)
        self.assertEqual(g.name, "reach_verdanturf_b5")


class TestNgrindConflictResolved(PgrindGoalsBase):
    """The committed Norman-grind arc must never fire for a trainee lead."""

    NGRIND = ("reach_ngrind_woods", "reach_ngrind_woods_north",
              "reach_ngrind_mauville", "grind_fiery_norman")

    def test_marker_on_silences_ngrind_everywhere(self) -> None:
        for kw in (
            dict(map_group=0, map_num=0, party0_level=48),
            dict(map_group=24, map_num=11, party0_level=48),
            dict(map_group=0, map_num=19, y=10, party0_level=48),
            dict(map_group=0, map_num=2, party0_level=48),
            dict(map_group=24, map_num=14, party0_level=48),
        ):
            g = self.goal(**kw)
            self.assertIsNotNone(g, kw)
            self.assertNotIn(g.name, self.NGRIND, (kw, g.name))

    def test_marker_off_weak_lead_still_never_treks_east(self) -> None:
        # The belt for "marker removed mid-run with a trainee still leading":
        # the lead floor keeps the Fiery arc silent below L40 (the Mauville
        # re-trap regression). The petalburg chain owns the corridor instead.
        pg.retire_party_grind()
        for kw in (
            dict(map_group=0, map_num=0, party0_level=12),
            dict(map_group=24, map_num=11, party0_level=12),
            dict(map_group=0, map_num=2, party0_level=12),
            dict(map_group=24, map_num=14, party0_level=12),
        ):
            g = self.goal(**kw)
            if g is not None:
                self.assertNotIn(g.name, self.NGRIND, (kw, g.name))

    def test_marker_off_real_lead_ngrind_unchanged(self) -> None:
        # Sceptile L48 without the marker: the committed arc still drives
        # (regression guard for the 499c9ad50 behavior).
        pg.retire_party_grind()
        g = self.goal(map_group=0, map_num=0, party0_level=48)
        self.assertEqual(g.name, "reach_ngrind_woods")

    def test_retire_hands_back_to_norman_grind_from_tunnel(self) -> None:
        # Marker deleted while parked in the tunnel with the restored L48
        # lead: the COMMITTED Norman-grind arc resumes on the spot ((24,4)
        # is on its eastbound corridor) — the pending badge-5 task for a
        # sub-52 real lead. At L52+ the same frame hands to the b5 chain's
        # exit_rusturf_west instead.
        pg.retire_party_grind()
        g = self.goal(map_group=24, map_num=4, party0_level=48)
        self.assertEqual(g.name, "reach_ngrind_mauville")
        g = self.goal(map_group=24, map_num=4,
                      party0_level=goals_mod.NORMAN_GRIND_TARGET_LEVEL)
        self.assertEqual(g.name, "exit_rusturf_west")


if __name__ == "__main__":
    unittest.main()
