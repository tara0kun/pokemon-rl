"""state.py / io.py の決定的コアのテスト。

mGBA 不要。RAM bridge の「壊すと静かに劣化する」部分
(INVARIANTS.md B 節) を機械的に守る。
"""
from __future__ import annotations

import unittest

from generic_agent.io import EmulatorError, MGBAClient, RawResponse
from generic_agent.state import (
    BATTLE_TYPE_TRAINER,
    GameState,
    _read_saveblock1_ptr,
    _signed16,
)


class FakeSendClient(MGBAClient):
    """_send を差し替えて socket なしで parse 系を検証する。"""

    def __init__(self, replies: list[str]) -> None:
        super().__init__()
        self.replies = list(replies)
        self.sent: list[str] = []

    def _send(self, payload: str) -> RawResponse:
        self.sent.append(payload)
        if not self.replies:
            raise AssertionError("no more canned replies")
        return RawResponse(text=self.replies.pop(0))


class Read32Stub:
    """_read_saveblock1_ptr 用: read32 のみを持つ duck-typed stub。"""

    def __init__(self, values: list[int | Exception]) -> None:
        self.values = list(values)

    def read32(self, addr: int) -> int:
        v = self.values.pop(0)
        if isinstance(v, Exception):
            raise v
        return v


class TestSigned16(unittest.TestCase):
    def test_positive_passthrough(self) -> None:
        self.assertEqual(_signed16(7), 7)

    def test_negative_wraps(self) -> None:
        self.assertEqual(_signed16(0xFFFF), -1)
        self.assertEqual(_signed16(0x8000), -0x8000)


class TestSaveBlock1PtrGuard(unittest.TestCase):
    """DMA 再配置ガード: 連続2回一致 + EWRAM 範囲のみ採用 (state.py)。"""

    def test_stable_ewram_pointer_accepted(self) -> None:
        ptr = 0x02025A00
        self.assertEqual(_read_saveblock1_ptr(Read32Stub([ptr, ptr])), ptr)

    def test_relocating_pointer_rejected(self) -> None:
        vals = [0x02020000, 0x02021000, 0x02022000, 0x02023000]
        self.assertIsNone(_read_saveblock1_ptr(Read32Stub(vals)))

    def test_out_of_range_pointer_rejected(self) -> None:
        # ROM 領域を指す「安定した」値でも EWRAM 外なら不採用
        vals = [0x08000000] * 4
        self.assertIsNone(_read_saveblock1_ptr(Read32Stub(vals)))

    def test_emulator_error_returns_none(self) -> None:
        self.assertIsNone(
            _read_saveblock1_ptr(Read32Stub([EmulatorError("boom")]))
        )


class TestGameStateProperties(unittest.TestCase):
    def _gs(self, **kw) -> GameState:
        base = dict(
            map_group=0, map_num=11, x=8, y=11, saveblock1_valid=True,
        )
        base.update(kw)
        return GameState(**base)

    def test_hp_frac_clamped(self) -> None:
        self.assertEqual(self._gs(party0_hp=0, party0_max_hp=0).party0_hp_frac, 1.0)
        self.assertEqual(self._gs(party0_hp=50, party0_max_hp=100).party0_hp_frac, 0.5)
        self.assertEqual(self._gs(party0_hp=200, party0_max_hp=100).party0_hp_frac, 1.0)

    def test_critical_threshold_is_26_percent(self) -> None:
        # INVARIANTS D-23: <26% で wild RUN
        self.assertTrue(self._gs(party0_hp=25, party0_max_hp=100).party0_critical)
        self.assertFalse(self._gs(party0_hp=26, party0_max_hp=100).party0_critical)
        # max_hp 不明 (0) のときは critical 扱いしない
        self.assertFalse(self._gs(party0_hp=0, party0_max_hp=0).party0_critical)

    def test_trainer_battle_flag_bit(self) -> None:
        gs = self._gs(in_battle=True, battle_flags=BATTLE_TYPE_TRAINER)
        self.assertTrue(gs.is_trainer_battle)
        self.assertFalse(gs.is_wild_battle)
        gs2 = self._gs(in_battle=True, battle_flags=0)
        self.assertFalse(gs2.is_trainer_battle)
        self.assertTrue(gs2.is_wild_battle)
        gs3 = self._gs(in_battle=False, battle_flags=BATTLE_TYPE_TRAINER)
        self.assertFalse(gs3.is_trainer_battle)


class TestMGBAClientParsing(unittest.TestCase):
    """lua 応答の parse 規約 (INVARIANTS B-12)。"""

    def test_read8_parses_decimal(self) -> None:
        c = FakeSendClient(["42"])
        self.assertEqual(c.read8(0x1000), 42)

    def test_empty_reply_raises_emulator_error(self) -> None:
        c = FakeSendClient([""])
        with self.assertRaises(EmulatorError):
            c.read8(0x1000)

    def test_non_numeric_reply_raises(self) -> None:
        c = FakeSendClient(["<|ERROR|>"])
        with self.assertRaises(EmulatorError):
            c.read16(0x1000)

    def test_read_range_parses_hex_tokens(self) -> None:
        c = FakeSendClient(["0a,ff,00"])
        self.assertEqual(c.read_range(0x1000, 3), b"\x0a\xff\x00")

    def test_read_range_bad_token_raises(self) -> None:
        c = FakeSendClient(["0a,zz"])
        with self.assertRaises(EmulatorError):
            c.read_range(0x1000, 2)

    def test_read_range_empty_reply_is_empty_bytes(self) -> None:
        c = FakeSendClient([""])
        self.assertEqual(c.read_range(0x1000, 4), b"")

    def test_tap_rejects_invalid_button(self) -> None:
        c = FakeSendClient(["<|SUCCESS|>"])
        with self.assertRaises(ValueError):
            c.tap("X")


class BadgeLatchTest(unittest.TestCase):
    """The badge latch must never let a SaveBlock1 DMA drop-flicker (a frame
    that reads 0 for a set badge flag) reduce the earned badge count."""

    def setUp(self) -> None:
        import generic_agent.state as st
        self.st = st
        st.reset_flag_latches()

    def tearDown(self) -> None:
        self.st.reset_flag_latches()

    def _count(self, bits_true: set[int]) -> int:
        # mirrors the read_state badge loop: latch the bits read set this frame,
        # then count from the latch (decoupled from the reads, so a mid-loop
        # read failure can't drop the count).
        st = self.st
        for bi in range(8):
            if bi in bits_true:
                st._BADGE_BITS_LATCHED.add(bi)
        return len(st._BADGE_BITS_LATCHED)

    def test_drop_flicker_does_not_lower_count(self) -> None:
        self.assertEqual(self._count({0, 1, 2}), 3)   # Badge 3 earned
        self.assertEqual(self._count(set()), 3)       # DMA flicker -> still 3
        self.assertEqual(self._count({0, 1, 2, 3}), 4)  # Badge 4 earned
        self.assertEqual(self._count(set()), 4)       # still 4, never dips

    def test_read_failure_frame_keeps_latched_count(self) -> None:
        # The real bug: a corrupted badge read8 raised mid-loop under button-
        # press load, aborting the loop with a PARTIAL count even though the
        # latch held {0,1,2}. Counting from len(latch) makes an all-fail frame
        # (no bits read this frame) still report the full earned count.
        self.assertEqual(self._count({0, 1, 2}), 3)
        self.assertEqual(self._count(set()), 3)  # every read failed -> still 3

    def test_reset_clears_latch(self) -> None:
        self._count({0, 1, 2})
        self.st.reset_flag_latches()
        self.assertEqual(self._count(set()), 0)


class RiseConfirmTest(unittest.TestCase):
    """Future-event gate flags (0x333/0x8B) must survive a spurious single True:
    a garbage read once false-latched the theft and skipped Meteor Falls."""

    def setUp(self) -> None:
        import generic_agent.state as st
        self.st = st
        st.reset_flag_latches()

    def tearDown(self) -> None:
        self.st.reset_flag_latches()

    def test_single_true_not_confirmed(self) -> None:
        # One True, or True interrupted by a False, never confirms.
        self.assertFalse(self.st._rise_confirmed("k", True))   # 1
        self.assertFalse(self.st._rise_confirmed("k", False))  # reset
        self.assertFalse(self.st._rise_confirmed("k", True))   # 1
        self.assertFalse(self.st._rise_confirmed("k", True))   # 2

    def test_n_consecutive_true_confirms(self) -> None:
        res = False
        for _ in range(self.st._RISE_CONFIRM_N):
            res = self.st._rise_confirmed("k", True)
        self.assertTrue(res)
        # stays confirmed while True
        self.assertTrue(self.st._rise_confirmed("k", True))
        # a False resets it (state read; goals._latched holds the disk latch)
        self.assertFalse(self.st._rise_confirmed("k", False))

    def test_flags_are_independent(self) -> None:
        for _ in range(self.st._RISE_CONFIRM_N):
            self.st._rise_confirmed("a", True)
        self.assertFalse(self.st._rise_confirmed("b", True))


if __name__ == "__main__":
    unittest.main()
