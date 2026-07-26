"""Catch must be intent-gated: traversal wild battles must never throw balls.

The 07-26 Rusturf Tunnel stall lost the bag's only Great Ball to a Whismur the
agent was merely walking past: every catch site was gated on OPPORTUNITY
(balls in bag + party size / HP), never on intent, and the catch_ready leg had
no party-size gate at all. These tests pin the new contract through the REAL
heuristic_button: without a goal named catch* no catch path fires; with one,
the same states still catch.

Offline: map_data.get_cache is forced to fail so indoor_battle resolves False
(the production except-Exception path), keeping the tests independent of the
on-disk map cache.
"""
from __future__ import annotations

import unittest
from unittest import mock

from generic_agent import claude_heuristic as ch
from generic_agent import goals as goals_mod
from generic_agent import path_memory as path_memory_mod
from generic_agent import tile_map as tile_map_mod
from generic_agent.state import GameState


def _wild_gs(**over) -> GameState:
    kw = dict(
        saveblock1_valid=True,
        in_battle=True,
        battle_flags=0x4,  # wild: trainer bit 0x8 clear
        party_count=2,
        party0_level=47,
        party0_hp=152,
        party0_max_hp=152,
        bag_pokeball_count=1,
    )
    kw.update(over)
    return GameState(24, 4, 26, 4, **kw)


def _catch_goal() -> goals_mod.Goal:
    return goals_mod.Goal(
        name="catch_whismur",
        target_map=(24, 4),
        condition="test",
        desc="test catch intent",
    )


def _call(gs, *, screen_signals, battle_turn=0, goal=None):
    return ch.heuristic_button(
        gs,
        tile_map_mod.TileMap(),
        path_memory_mod.TransitionMemory(),
        map_visit_counts={},
        same_pos_streak=1,
        same_hash_streak=0,
        same_map_streak=5,
        last_pos=(26, 4),
        last_action="Down",
        recent_pos=[],
        battle_turn=battle_turn,
        escape_dir_index=0,
        screen_signals=screen_signals,
        current_goal=goal,
        client=None,
        ram_battle_recent=True,
    )


class TestCatchIntentGate(unittest.TestCase):
    def setUp(self) -> None:
        p = mock.patch.object(
            ch.map_data_mod, "get_cache",
            side_effect=RuntimeError("offline"),
        )
        p.start()
        self.addCleanup(p.stop)

    def test_battle_menu_no_goal_does_not_throw(self) -> None:
        """battle_menu visible, balls in bag, party of 2 — the exact state
        that used to return wild_catch_try_screen:init during traversal."""
        button, src = _call(
            _wild_gs(), screen_signals={"battle_menu": True},
        )
        self.assertFalse(src.startswith("wild_catch"), src)
        self.assertEqual(src, "battle_menu_visible:fight_cursor_reset")

    def test_battle_menu_with_catch_goal_still_throws(self) -> None:
        button, src = _call(
            _wild_gs(), screen_signals={"battle_menu": True},
            goal=_catch_goal(),
        )
        self.assertEqual((button, src), ("Right", "wild_catch_try_screen:init"))

    def test_catch_ready_no_goal_fights_instead(self) -> None:
        """The catch_ready leg (dialog UI, battle_turn%8==0, full party!) had
        no party gate — with balls it threw on ANY wild battle. Without a
        catch goal it must fall through to wild_fight_safe."""
        button, src = _call(
            _wild_gs(party_count=5),
            screen_signals={"dialog": True},
            battle_turn=8,
        )
        self.assertEqual((button, src), ("A", "wild_fight_safe"))

    def test_catch_ready_with_catch_goal_still_throws(self) -> None:
        button, src = _call(
            _wild_gs(party_count=5),
            screen_signals={"dialog": True},
            battle_turn=8,
            goal=_catch_goal(),
        )
        self.assertEqual(src, "wild_catch_try")

    def test_catch_priority_no_goal_mono_party(self) -> None:
        """Even the mono-party turn-1 throw (the early-game path) is
        intent-gated now: a fresh run needs an explicit catch goal."""
        button, src = _call(
            _wild_gs(party_count=1),
            screen_signals={"dialog": True},
            battle_turn=1,
        )
        self.assertNotEqual(src, "wild_catch_try")

    def test_intent_helper_prefix_only(self) -> None:
        self.assertFalse(ch.catch_intent_active(None))
        self.assertFalse(
            ch.catch_intent_active(
                goals_mod.Goal(
                    name="grind_fiery_path", target_map=None,
                    condition="c", desc="d",
                )
            )
        )
        self.assertTrue(ch.catch_intent_active(_catch_goal()))

    def test_full_party_room_still_throws(self) -> None:
        # party gate relaxed <=2 -> <6 (Water-catch project): a 5-mon party
        # with a free slot must still pre-empt throw under a catch goal.
        button, src = _call(
            _wild_gs(party_count=5), screen_signals={"battle_menu": True},
            goal=_catch_goal(),
        )
        self.assertEqual((button, src), ("Right", "wild_catch_try_screen:init"))
        # ...but a FULL party (no room — the catch would go to the box and the
        # party_count retire could fire on the wrong mon) must not.
        button, src = _call(
            _wild_gs(party_count=6), screen_signals={"battle_menu": True},
            goal=_catch_goal(),
        )
        self.assertNotEqual(src, "wild_catch_try_screen:init")


def _water_goal() -> goals_mod.Goal:
    return goals_mod.Goal(
        name="catch_water_route104", target_map=(0, 19),
        condition="test", desc="water catch",
    )


class TestCatchTypeGate(unittest.TestCase):
    """catch_water_* goals throw ONLY at Water-typed opponents (gBattleMons[1]
    via battle_moves.enemy_types; Gen3 TYPE_WATER=11). Fail-closed on RAM
    error; untyped catch* goals and a None client skip the filter."""

    def setUp(self) -> None:
        p = mock.patch.object(
            ch.map_data_mod, "get_cache",
            side_effect=RuntimeError("offline"),
        )
        p.start()
        self.addCleanup(p.stop)
        self.client = object()  # non-None sentinel; only enemy_types sees it

    def _src(self, types, goal):
        if isinstance(types, Exception):
            patcher = mock.patch.object(
                ch.battle_moves_mod, "enemy_types", side_effect=types)
        else:
            patcher = mock.patch.object(
                ch.battle_moves_mod, "enemy_types", return_value=types)
        with patcher:
            _btn, src = ch.heuristic_button(
                _wild_gs(party_count=5),
                tile_map_mod.TileMap(),
                path_memory_mod.TransitionMemory(),
                map_visit_counts={},
                same_pos_streak=1, same_hash_streak=0, same_map_streak=5,
                last_pos=(26, 4), last_action="Down", recent_pos=[],
                battle_turn=1, escape_dir_index=0,
                screen_signals={"battle_menu": True},
                current_goal=goal, client=self.client,
                ram_battle_recent=True,
            )
        return src

    def test_water_opponent_throws(self) -> None:
        # Marill (11,11) and Wingull (11,2) both pass the Water filter.
        self.assertEqual(self._src((11, 11), _water_goal()),
                         "wild_catch_try_screen:init")
        self.assertEqual(self._src((11, 2), _water_goal()),
                         "wild_catch_try_screen:init")

    def test_off_type_opponent_does_not_throw(self) -> None:
        # Poochyena (Dark 17): no ball — the Part-B flee handles the battle.
        self.assertNotEqual(self._src((17, 17), _water_goal()),
                            "wild_catch_try_screen:init")

    def test_read_failure_fails_closed(self) -> None:
        self.assertNotEqual(
            self._src(RuntimeError("dma"), _water_goal()),
            "wild_catch_try_screen:init")

    def test_untyped_catch_goal_unfiltered(self) -> None:
        # A plain catch_* goal keeps the old contract: any species.
        self.assertEqual(self._src((17, 17), _catch_goal()),
                         "wild_catch_try_screen:init")

    def test_helper_none_client_skips_filter(self) -> None:
        self.assertTrue(ch.catch_target_type_ok(_water_goal(), None))
        self.assertTrue(ch.catch_target_type_ok(None, None))


if __name__ == "__main__":
    unittest.main()
