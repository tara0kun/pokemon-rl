"""story_state.py の純粋関数 infer_flags / hint_for の単体テスト。

visit-count 辞書は story_state._KEY_MAPS / _FLAG_THRESHOLDS から機械的に
構築し、実ゲーム座標や map_id をリテラルで埋め込まない。story_state 本体は
import read-only で変更しない。
"""
from __future__ import annotations

import unittest

from generic_agent import story_state
from generic_agent.story_state import StoryFlags


def _counts_for(flag: str, total: int) -> dict[tuple[int, int], int]:
    """Put ``total`` visits on the flag's first key map, its other maps at 0.

    Why: keys come from _KEY_MAPS so tests never spell out coordinates, and
    all flags' key maps are disjoint, so one flag's counts never flip another.
    """
    keys = story_state._KEY_MAPS[flag]
    counts = {k: 0 for k in keys}
    counts[keys[0]] = total
    return counts


class TestInferFlagsThreshold(unittest.TestCase):
    def test_each_flag_flips_at_exact_threshold(self) -> None:
        for flag, thr in story_state._FLAG_THRESHOLDS.items():
            with self.subTest(flag=flag):
                at = story_state.infer_flags(_counts_for(flag, thr))
                self.assertTrue(getattr(at, flag))
                if thr - 1 >= 0:
                    below = story_state.infer_flags(_counts_for(flag, thr - 1))
                    self.assertFalse(getattr(below, flag))


class TestStarterReceivedDerivation(unittest.TestCase):
    def _merged(self, birch_total: int, r101_total: int) -> dict:
        d: dict[tuple[int, int], int] = {}
        d.update(_counts_for("birch_lab_visited", birch_total))
        d.update(_counts_for("route101_explored", r101_total))
        return d

    def test_starter_is_and_of_birch_and_route101(self) -> None:
        bt = story_state._FLAG_THRESHOLDS["birch_lab_visited"]
        rt = story_state._FLAG_THRESHOLDS["route101_explored"]

        both = story_state.infer_flags(self._merged(bt, rt))
        self.assertTrue(both.starter_received)

        only_birch = story_state.infer_flags(self._merged(bt, rt - 1))
        self.assertTrue(only_birch.birch_lab_visited)
        self.assertFalse(only_birch.route101_explored)
        self.assertFalse(only_birch.starter_received)

        only_r101 = story_state.infer_flags(self._merged(bt - 1, rt))
        self.assertFalse(only_r101.birch_lab_visited)
        self.assertTrue(only_r101.route101_explored)
        self.assertFalse(only_r101.starter_received)

        neither = story_state.infer_flags(self._merged(bt - 1, rt - 1))
        self.assertFalse(neither.starter_received)


class TestHintForBranchOrder(unittest.TestCase):
    def test_intown_hint_precedes_generic_north_hint(self) -> None:
        oldale_map = story_state._KEY_MAPS["oldale_reached"][0]
        flags = StoryFlags(oldale_reached=True)

        intown = story_state.hint_for(flags, current_map=oldale_map, visits_this_map=49)
        boundary = story_state.hint_for(flags, current_map=oldale_map, visits_this_map=50)
        no_map = story_state.hint_for(flags, current_map=None, visits_this_map=0)

        # oldale + visits<50 の in-town 分岐が generic な北上ヒントより優先。
        self.assertNotEqual(intown, boundary)
        # visits>=50 で in-town 分岐が外れ、current_map=None と同じ generic に落ちる。
        self.assertEqual(boundary, no_map)


if __name__ == "__main__":
    unittest.main()
