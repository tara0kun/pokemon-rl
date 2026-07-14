"""preprocess.py の純粋関数のテスト。

mGBA / API / ファイル I/O 不要。合成 numpy uint8 BGR 配列のみで
frame_hash / frames_differ / frame_embedding の決定的挙動を検証する。
"""
from __future__ import annotations

import unittest

import numpy as np

from generic_agent.preprocess import frame_embedding, frame_hash, frames_differ


def _solid(value: int) -> np.ndarray:
    """16x16x3 BGR の定数塗りつぶし配列。"""
    return np.full((16, 16, 3), value, dtype=np.uint8)


class FrameHashTests(unittest.TestCase):
    def test_deterministic_and_str(self) -> None:
        h1 = frame_hash(_solid(0))
        h2 = frame_hash(_solid(0))
        self.assertIsInstance(h1, str)
        self.assertEqual(h1, h2)

    def test_visibly_different_frames_hash_differently(self) -> None:
        self.assertNotEqual(frame_hash(_solid(0)), frame_hash(_solid(255)))


class FramesDifferTests(unittest.TestCase):
    def test_none_arg_returns_true(self) -> None:
        arr = _solid(0)
        self.assertTrue(frames_differ(None, arr))
        self.assertTrue(frames_differ(arr, None))

    def test_identical_returns_false(self) -> None:
        self.assertFalse(frames_differ(_solid(30), _solid(30)))

    def test_black_vs_white_returns_true(self) -> None:
        self.assertTrue(frames_differ(_solid(5), _solid(250)))

    def test_threshold_flips_borderline(self) -> None:
        # diff ratio = 13/255 ≈ 0.051: stricter 0.02 は differ, looser 0.10 は同一扱い
        black, gray = _solid(0), _solid(13)
        self.assertTrue(frames_differ(black, gray, threshold=0.02))
        self.assertFalse(frames_differ(black, gray, threshold=0.10))


class FrameEmbeddingTests(unittest.TestCase):
    def test_non_perfect_square_raises(self) -> None:
        with self.assertRaises(ValueError):
            frame_embedding(_solid(0), dim=63)

    def test_perfect_square_returns_len64_float32(self) -> None:
        vec = frame_embedding(_solid(0), dim=64)
        self.assertEqual(vec.shape, (64,))
        self.assertEqual(vec.dtype, np.float32)


if __name__ == "__main__":
    unittest.main()