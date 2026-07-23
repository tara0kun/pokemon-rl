"""tile_map / path_memory / reward_state の永続ストアのテスト。

すべて一時ファイルで動く (実 memory/ を汚さない)。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from generic_agent.path_memory import TransitionMemory
from generic_agent.reward_state import NEW_TILE_BONUS, RewardState
from generic_agent.tile_map import TileMap, TileRecord


class TestTileMap(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tm = TileMap(path=Path(self._tmp.name) / "tile_map.json")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _rec(self, x: int = 5, y: int = 5) -> TileRecord:
        return self.tm._store["0-11"][self.tm._tile_key(x, y)]

    def test_blocked_after_three_failures(self) -> None:
        for _ in range(3):
            self.tm.record_attempt(0, 11, 5, 5, "Up", moved=False)
        self.assertIn("Up", self._rec().blocked)

    def test_two_failures_not_blocked(self) -> None:
        for _ in range(2):
            self.tm.record_attempt(0, 11, 5, 5, "Up", moved=False)
        self.assertNotIn("Up", self._rec().blocked)

    def test_success_unblocks_and_resets_streak(self) -> None:
        # Route112 fix: a single success clears a block AND the fail streak.
        # (24,39)-Right had tried=140, nearly all success, yet stayed blocked
        # under the old lifetime-cumulative rule; a road tile must self-heal.
        for _ in range(3):
            self.tm.record_attempt(0, 11, 5, 5, "Up", moved=False)
        self.assertIn("Up", self._rec().blocked)
        self.tm.record_attempt(0, 11, 5, 5, "Up", moved=True)
        self.assertNotIn("Up", self._rec().blocked)
        self.assertEqual(self._rec().fail_streak.get("Up", 0), 0)

    def test_non_consecutive_failures_never_block(self) -> None:
        # Blocking is on the CONSECUTIVE streak, not lifetime tries: a transient
        # NPC/battle bump between successes must not seal the tile.
        for moved in (False, False, True, False, False, True, False, False):
            self.tm.record_attempt(0, 11, 5, 5, "Up", moved=moved)
        self.assertNotIn("Up", self._rec().blocked)

    def test_non_overworld_attempts_ignored(self) -> None:
        # INVARIANTS C-16: dialog/battle 中の方向キーは封鎖記録を汚染しない
        for _ in range(5):
            self.tm.record_attempt(0, 11, 5, 5, "Up", moved=False, overworld=False)
        self.assertEqual(self.tm._store.get("0-11", {}), {})

    def test_fourth_direction_seal_is_refused(self) -> None:
        # 4方向封鎖は「そのタイルに立てた」事実と矛盾する記録バグなので、
        # 4方向目が封鎖される直前に tried/blocked がリセットされる
        for d in ("Up", "Down", "Left"):
            for _ in range(3):
                self.tm.record_attempt(0, 11, 5, 5, d, moved=False)
        for _ in range(3):
            self.tm.record_attempt(0, 11, 5, 5, "Right", moved=False)
        rec = self._rec()
        self.assertEqual(rec.blocked, [])
        self.assertEqual(rec.tried, {})

    def test_cleanup_phantom_walls(self) -> None:
        rec = TileRecord(visits=1, blocked=["Up", "Down", "Left", "Right"])
        self.tm._store["0-11"] = {"5,5": rec}
        self.assertEqual(self.tm.cleanup_phantom_walls(), 1)
        self.assertEqual(rec.blocked, [])

    def test_save_load_roundtrip(self) -> None:
        self.tm.record_visit(0, 11, 5, 5)
        for _ in range(3):
            self.tm.record_attempt(0, 11, 5, 5, "Up", moved=False)
        self.tm.save()
        tm2 = TileMap(path=self.tm.path)
        rec = tm2._store["0-11"]["5,5"]
        self.assertEqual(rec.visits, 1)
        self.assertIn("Up", rec.blocked)

    def test_bfs_frontier_prefers_farthest(self) -> None:
        self.tm.record_visit(0, 11, 0, 0)
        self.tm.record_visit(0, 11, 1, 0)
        # (1,0) 経由の frontier (dist 2) が (0,0) 直近の frontier (dist 1)
        # より優先される → 第一歩は Right
        d = self.tm.bfs_frontier_direction(0, 11, 0, 0, prefer="farthest")
        self.assertEqual(d, "Right")


class TestTransitionMemory(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.pm = TransitionMemory(path=Path(self._tmp.name) / "pm.json")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_find_path_single_hop(self) -> None:
        self.pm.record_transition(0, 10, 5, 5, 0, 17, 3, 3, ["Up", "Up"])
        self.assertEqual(self.pm.find_path_to_map(0, 10, 0, 17), [(0, 17)])

    def test_find_path_multi_hop(self) -> None:
        self.pm.record_transition(0, 10, 5, 5, 0, 17, 3, 3, ["Up"])
        self.pm.record_transition(0, 17, 1, 1, 0, 0, 9, 9, ["Left"])
        self.assertEqual(
            self.pm.find_path_to_map(0, 10, 0, 0), [(0, 17), (0, 0)],
        )

    def test_find_path_unreachable_is_none(self) -> None:
        self.assertIsNone(self.pm.find_path_to_map(0, 10, 24, 4))

    def test_first_transition_record_prefers_nearest(self) -> None:
        self.pm.record_transition(0, 10, 5, 5, 0, 17, 3, 3, ["Up"])
        self.pm.record_transition(0, 10, 20, 20, 0, 17, 3, 3, ["Down"])
        r = self.pm.first_transition_record(0, 10, 0, 17, prefer_pos=(19, 19))
        self.assertEqual(r.from_pos, (20, 20))

    def test_duplicate_records_not_stored(self) -> None:
        for _ in range(3):
            self.pm.record_transition(0, 10, 5, 5, 0, 17, 3, 3, ["Up"])
        self.assertEqual(len(self.pm._store["0-10"]["0-17"]), 1)


class TestRewardState(unittest.TestCase):
    def test_unvisited_direction_gets_new_tile_bonus(self) -> None:
        rs = RewardState()
        score = rs.score_direction("Up", 0, 11, 5, 5, {}, set(), 0)
        self.assertEqual(score, NEW_TILE_BONUS)

    def test_blocked_direction_is_neg_inf(self) -> None:
        rs = RewardState()
        score = rs.score_direction("Up", 0, 11, 5, 5, {}, {"Up"}, 0)
        self.assertEqual(score, float("-inf"))

    def test_same_map_streak_decays_score(self) -> None:
        rs = RewardState()
        s0 = rs.score_direction("Up", 0, 11, 5, 5, {}, set(), 0)
        s100 = rs.score_direction("Up", 0, 11, 5, 5, {}, set(), 100)
        self.assertLess(s100, s0)

    def test_badge_delta_rewarded_once(self) -> None:
        rs = RewardState()
        self.assertGreater(rs.record_badge_delta(1, prev_badges=1, cur_badges=2), 0)
        self.assertEqual(rs.record_badge_delta(2, prev_badges=2, cur_badges=2), 0.0)


if __name__ == "__main__":
    unittest.main()
