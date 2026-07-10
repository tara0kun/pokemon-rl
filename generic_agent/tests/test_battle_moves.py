"""battle_moves の決定的テスト(mGBA 不要・fake client)。

H6a(opening メニュー誤発火)修正で導入した active_hp と、既存の
best_move_index / effectiveness / move_select_sequence の不変条件を守る。
"""
from __future__ import annotations

import unittest

from generic_agent import battle_moves as bm


class FakeBattleClient:
    """read8/16/32 を dict から返す stub。未登録アドレスは 0。"""

    def __init__(self, mem8=None, mem16=None):
        self.mem8 = dict(mem8 or {})
        self.mem16 = dict(mem16 or {})
        self.reads: list[tuple[str, int]] = []

    def read8(self, addr: int) -> int:
        self.reads.append(("r8", addr))
        return self.mem8.get(addr, 0)

    def read16(self, addr: int) -> int:
        self.reads.append(("r16", addr))
        return self.mem16.get(addr, 0)

    def read32(self, addr: int) -> int:
        self.reads.append(("r32", addr))
        return 0


class EffectivenessTest(unittest.TestCase):
    def test_super_effective_and_resist_and_immune(self):
        # grass(12) vs water(11) = 2x; grass vs grass = 0.5x
        self.assertEqual(bm.effectiveness(12, 11, 11), 2.0)
        self.assertEqual(bm.effectiveness(12, 12, 12), 0.5)
        # fighting(1) vs normal(0) = 2x; fighting vs ghost(7) = 0x (immune)
        self.assertEqual(bm.effectiveness(1, 0, 0), 2.0)
        self.assertEqual(bm.effectiveness(1, 7, 7), 0.0)
        # dual-type multiply: ground(4) vs fire(10)/flying(2) = 2 * 0 = 0
        self.assertEqual(bm.effectiveness(4, 10, 2), 0.0)
        # neutral default
        self.assertEqual(bm.effectiveness(0, 11, 11), 1.0)


class MoveSelectSequenceTest(unittest.TestCase):
    def test_each_slot_ends_with_confirm(self):
        for slot in range(4):
            seq = bm.move_select_sequence(slot)
            self.assertEqual(seq[-1], "A", f"slot {slot} must end with A")
            # leading B,B escapes any wrong submenu, then resets to FIGHT/slot0
            self.assertEqual(
                seq[:8], ("B", "B", "Up", "Up", "Left", "A", "Up", "Left"))

    def test_slot_navigation(self):
        self.assertEqual(bm.move_select_sequence(0)[8:], ("A",))
        self.assertEqual(bm.move_select_sequence(1)[8:], ("Right", "A"))
        self.assertEqual(bm.move_select_sequence(2)[8:], ("Down", "A"))
        self.assertEqual(bm.move_select_sequence(3)[8:], ("Down", "Right", "A"))


class ActiveHpTest(unittest.TestCase):
    def test_active_hp_reads_gbattlemons0(self):
        c = FakeBattleClient(mem16={bm.GBATTLEMONS + bm.BATTLEMON_HP: 42})
        self.assertEqual(bm.active_hp(c), 42)
        self.assertIn(("r16", bm.GBATTLEMONS + bm.BATTLEMON_HP), c.reads)

    def test_enemy_hp_reads_gbattlemons1(self):
        a = bm.GBATTLEMONS + bm.BATTLEMON_SIZE
        c = FakeBattleClient(mem16={a + bm.BATTLEMON_HP: 26, a + bm.BATTLEMON_MAXHP: 50})
        self.assertEqual(bm.enemy_hp(c), (26, 50))

    def test_active_zero_signals_send_out(self):
        # 0 HP on the active battler is the send-out signal used by H6a.
        c = FakeBattleClient(mem16={bm.GBATTLEMONS + bm.BATTLEMON_HP: 0})
        self.assertEqual(bm.active_hp(c), 0)


class BestMoveIndexTest(unittest.TestCase):
    def _client(self, move_ids, enemy_types, move_table, pp=None):
        """move_table: {move_id: (power, type)}. pp: per-slot PP (default 30)."""
        mem8, mem16 = {}, {}
        # active battler moves (read16)
        for i, mid in enumerate(move_ids):
            mem16[bm.GBATTLEMONS + bm.BATTLEMON_MOVES + i * 2] = mid
        # active battler PP (read8), default full so moves aren't skipped
        pp = pp or [30, 30, 30, 30]
        for i, p in enumerate(pp):
            mem8[bm.GBATTLEMONS + bm.BATTLEMON_PP + i] = p
        # enemy types (read8) at gBattleMons[1]
        a = bm.GBATTLEMONS + bm.BATTLEMON_SIZE
        mem8[a + bm.BATTLEMON_TYPE1] = enemy_types[0]
        mem8[a + bm.BATTLEMON_TYPE2] = enemy_types[1]
        # move table (read8) power@+1 type@+2
        for mid, (power, mtype) in move_table.items():
            base = bm.GBATTLEMOVES + mid * 12
            mem8[base + 1] = power
            mem8[base + 2] = mtype
        return FakeBattleClient(mem8=mem8, mem16=mem16)

    def test_skips_depleted_pp_move(self) -> None:
        # slot0 = Pound (best score) but 0 PP -> must fall to slot3 Quick Attack
        # (this was the Brawly stall: best_move kept picking the 0-PP move).
        c = self._client(
            move_ids=[1, 43, 228, 98],
            enemy_types=(1, 1),  # Fighting
            move_table={1: (40, 0), 43: (0, 0), 228: (40, 17), 98: (40, 0)},
            pp=[0, 29, 18, 26],
        )
        self.assertEqual(bm.best_move_index(c), 3)

    def test_picks_highest_power_times_effectiveness(self):
        # slot0 = weak grass (Absorb 20) vs water enemy -> 40; slot1 = Tackle
        # 35 normal -> 35; slot2 = status (power 0) -> skipped.
        c = self._client(
            move_ids=[71, 33, 43, 0],
            enemy_types=(11, 11),  # water
            move_table={71: (20, 12), 33: (35, 0), 43: (0, 0)},
        )
        # Absorb: 20 * 2.0(grass vs water) = 40 > Tackle 35 -> slot 0
        self.assertEqual(bm.best_move_index(c), 0)

    def test_skips_status_and_returns_neg1_when_no_damage(self):
        c = self._client(
            move_ids=[43, 0, 0, 0],
            enemy_types=(0, 0),
            move_table={43: (0, 0)},  # only a status move
        )
        self.assertEqual(bm.best_move_index(c), -1)

    def test_avoids_immune_move(self):
        # slot0 normal(Tackle 35) vs ghost -> 0; slot1 dark(Pursuit 40) -> 2x
        c = self._client(
            move_ids=[33, 228, 0, 0],
            enemy_types=(7, 7),  # ghost
            move_table={33: (35, 0), 228: (40, 17)},
        )
        self.assertEqual(bm.best_move_index(c), 1)


class EffectivenessChartDerivedTest(unittest.TestCase):
    """effectiveness() の期待倍率を bm._CHART から機械的に導出して検証する。
    チャート値をテスト側に再ハードコードしない(4.0/1.0/0.0 は構造上の帰結)。"""

    def test_immunity_is_zero(self):
        checked = 0
        for atk, row in bm._CHART.items():
            for dft, mult in row.items():
                if mult == 0.0:
                    self.assertEqual(bm.effectiveness(atk, dft, dft), 0.0)
                    checked += 1
        self.assertGreater(checked, 0)

    def test_single_type_not_squared(self):
        for atk, row in bm._CHART.items():
            for dft, mult in row.items():
                got = bm.effectiveness(atk, dft, dft)
                self.assertEqual(got, mult)
                if mult not in (0.0, 1.0):
                    self.assertNotEqual(got, mult * mult)

    def test_double_super_effective_is_4(self):
        checked = 0
        for atk, row in bm._CHART.items():
            supers = [t for t, m in row.items() if m == 2]
            for i in range(len(supers)):
                for j in range(len(supers)):
                    d1, d2 = supers[i], supers[j]
                    if d1 == d2:
                        continue
                    expected = row[d1] * row[d2]
                    self.assertEqual(expected, 4.0)
                    self.assertEqual(bm.effectiveness(atk, d1, d2), expected)
                    checked += 1
        self.assertGreater(checked, 0)

    def test_unregistered_pair_defaults_to_1(self):
        atk = 0
        row = bm._CHART[atk]
        missing = next(t for t in range(18) if t not in row)
        self.assertEqual(bm.effectiveness(atk, missing, missing),
                         row.get(missing, 1.0))
        self.assertEqual(bm.effectiveness(atk, missing, missing), 1.0)
        absent = next(t for t in range(64) if t not in bm._CHART)
        self.assertEqual(bm.effectiveness(absent, 0, 5), 1.0)


class MoveSelectSequenceNavDerivedTest(unittest.TestCase):
    """末尾ナビを bm._SLOT_NAV から導出して照合。範囲外 slot は nav なし fallback。"""

    def _prefix(self):
        return bm.move_select_sequence(0)[:-1]

    def _tail_nav(self, slot):
        seq = bm.move_select_sequence(slot)
        return seq[len(self._prefix()):-1]

    def test_tail_nav_matches_slot_nav_table(self):
        for slot, nav in bm._SLOT_NAV.items():
            seq = bm.move_select_sequence(slot)
            self.assertEqual(seq[-1], "A")
            self.assertEqual(self._tail_nav(slot), nav)

    def test_out_of_range_slot_is_no_nav_fallback(self):
        for bad in (-1, 4, 99):
            self.assertNotIn(bad, bm._SLOT_NAV)
            seq = bm.move_select_sequence(bad)
            self.assertEqual(seq[-1], "A")
            self.assertEqual(self._tail_nav(bad), ())

if __name__ == "__main__":
    unittest.main()
