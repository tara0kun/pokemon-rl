"""path_memory.py の未カバー純粋ロジックの単体テスト。

test_memory_stores.py が扱う find_path / first_transition_record /
duplicate とは重複しない範囲のみを検証する。すべて一時ファイルで動き
実 memory/ を汚さない。map key は '0-1' 等の合成値、x/y は非 canonical
な任意値のみを使い、実ゲーム座標や map_id は埋め込まない。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from generic_agent.path_memory import (
    TransitionMemory,
    TransitionRecord,
    _coerce_pos,
)


class TestCoercePos(unittest.TestCase):
    def test_none_returns_none(self) -> None:
        self.assertIsNone(_coerce_pos(None))

    def test_valid_pair_coerces_to_int_tuple(self) -> None:
        self.assertEqual(_coerce_pos([12, 7]), (12, 7))

    def test_wrong_shape_returns_none(self) -> None:
        # 要素数が 2 でない → unpack で ValueError → None
        self.assertIsNone(_coerce_pos([1, 2, 3]))
        self.assertIsNone(_coerce_pos([1]))

    def test_non_iterable_returns_none(self) -> None:
        # unpack 不能 → TypeError → None
        self.assertIsNone(_coerce_pos(5))

    def test_non_numeric_returns_none(self) -> None:
        # int() 変換失敗 → ValueError → None
        self.assertIsNone(_coerce_pos(["a", "b"]))


class TestTransitionRecordRoundtrip(unittest.TestCase):
    def test_roundtrip_with_positions(self) -> None:
        rec = TransitionRecord(from_pos=(12, 7), to_pos=(3, 9), seq=["Up", "Left"])
        self.assertEqual(TransitionRecord.from_dict(rec.to_dict()), rec)

    def test_roundtrip_with_none_positions(self) -> None:
        rec = TransitionRecord(from_pos=None, to_pos=None, seq=["Down"])
        self.assertEqual(TransitionRecord.from_dict(rec.to_dict()), rec)


class TestLegacyLoad(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "pm.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_legacy_list_entry_loads_via_from_legacy(self) -> None:
        # 旧スキーマ: エントリが dict でなく bare list[str]
        legacy = {"0-1": {"0-2": [["Up", "Up", "Left"]]}}
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        pm = TransitionMemory(path=self.path)  # __post_init__ → _load
        recs = pm._store["0-1"]["0-2"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0], TransitionRecord(None, None, ["Up", "Up", "Left"]))


class TestDistanceFilter(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.pm = TransitionMemory(path=Path(self._tmp.name) / "pm.json")
        # record_transition の副作用に依存せず _store を直接構築。
        # 非 canonical な任意座標。
        self.pm._store["0-1"] = {
            "0-2": [TransitionRecord((10, 10), (3, 3), ["Up"])],    # near (dist 0)
            "0-3": [TransitionRecord((10, 40), (3, 3), ["Down"])],  # far  (dist 30)
            "0-5": [TransitionRecord(None, None, ["Left"])],        # from_pos=None
        }

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_only_within_max_distance(self) -> None:
        near = self.pm.nearby_records(0, 1, 10, 10, max_distance=5)
        self.assertEqual([tk for tk, _rec, _d in near], ["0-2"])
        self.assertEqual(near[0][2], 0)

    def test_none_from_pos_excluded_even_at_large_distance(self) -> None:
        got = self.pm.nearby_records(0, 1, 10, 10, max_distance=1000)
        keys = [tk for tk, _rec, _d in got]
        self.assertEqual(keys, ["0-2", "0-3"])  # 0-5 (from_pos=None) は常に除外・距離昇順
        self.assertEqual(got[1][2], 30)

    def test_summary_position_aware_filters_by_distance(self) -> None:
        s = self.pm.summary_for(0, 1, 10, 10, max_distance=5)
        self.assertIn("0-2", s)
        self.assertNotIn("0-3", s)
        self.assertNotIn("0-5", s)

    def test_summary_returns_marker_when_none_near_tile(self) -> None:
        s = self.pm.summary_for(0, 1, 999, 999, max_distance=5)
        self.assertEqual(s, "no known transitions near this tile")


if __name__ == "__main__":
    unittest.main()
