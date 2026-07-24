"""menu_open() phantom-menu regression (2026-07-24 Lavaridge gym B-spam).

The white-ratio-only detector read the gym's pale-cream floor (player pinned
at x=0, void left, floor filling the right strip) as an open START menu:
w=0.56-0.68 with dark<=0.021 for ~45 straight turns while cb2 stayed
CB2_Overworld — provably no menu. menu_visible:B preempted all nav at the
exact puzzle hole. A real menu is white panels WITH black text rows
(measured dark>=0.081 across 47 text-screen frames), so menu_open now also
requires dark>0.04.

Synthetic frames, no emulator; skips if cv2/numpy are unavailable.
"""
from __future__ import annotations

import unittest

try:
    import numpy as np

    from generic_agent import screen_features as sf
    _HAVE_CV2 = True
except ImportError:  # pragma: no cover
    _HAVE_CV2 = False


def _frame(fill: int) -> "np.ndarray":
    return np.full((sf.GBA_H, sf.GBA_W, 3), fill, dtype=np.uint8)


@unittest.skipUnless(_HAVE_CV2, "cv2/numpy not installed")
class MenuOpenTests(unittest.TestCase):
    def test_pale_floor_without_text_is_not_a_menu(self) -> None:
        # Lavaridge gym floor: the whole right strip bright, zero dark text.
        img = _frame(120)
        img[:, sf.MENU_LEFT_X:] = 240
        self.assertFalse(sf.menu_open(img))

    def test_white_panel_with_text_rows_is_a_menu(self) -> None:
        # START menu: white panel + horizontal black text rows (~6 entries).
        img = _frame(120)
        img[:, sf.MENU_LEFT_X:] = 240
        for row_y in range(16, 152, 20):
            img[row_y:row_y + 7, sf.MENU_LEFT_X + 8:sf.MENU_RIGHT_X - 8] = 20
        self.assertTrue(sf.menu_open(img))

    def test_dark_scene_is_not_a_menu(self) -> None:
        self.assertFalse(sf.menu_open(_frame(40)))


if __name__ == "__main__":
    unittest.main()
