"""vlm_screen tiebreaker: parse robustness + frame_hash cache, no API/mGBA.

The live classification accuracy is measured separately (docs H11); here we
lock the response parsing (fenced / bare / trailing prose) and the cache so a
static stuck frame costs exactly one API call.
"""
from __future__ import annotations

import unittest
from unittest import mock

import numpy as np

from generic_agent import vlm_screen


class _Resp:
    def __init__(self, text):
        self.content = [mock.Mock(text=text)]


def _fake_frame(monkeyval):
    """Patch load_png_as_array + frame_hash so no file/cv2 is needed."""
    return mock.patch.multiple(
        vlm_screen.preprocess,
        load_png_as_array=mock.Mock(return_value=np.zeros((2, 2, 3), np.uint8)),
        frame_hash=mock.Mock(return_value=monkeyval),
    )


class ParseTest(unittest.TestCase):
    def setUp(self):
        vlm_screen._verdict_cache.clear()

    def _run(self, api_text, fhash="h1"):
        with _fake_frame(fhash), mock.patch.object(
            vlm_screen.rescue_brain, "_call_haiku",
            return_value=(_Resp(api_text), 0, ""),
        ):
            return vlm_screen.is_battle_screen("x.png")

    def test_plain_true_false(self):
        self.assertIs(self._run('{"battle": true}'), True)
        vlm_screen._verdict_cache.clear()
        self.assertIs(self._run('{"battle": false}'), False)

    def test_fenced_and_trailing_prose(self):
        self.assertIs(
            self._run('```json\n{"battle": false}\n```  clearly the bag'), False)
        vlm_screen._verdict_cache.clear()
        self.assertIs(self._run('{"battle":true}'), True)

    def test_cache_hits_avoid_second_api_call(self):
        with _fake_frame("same"), mock.patch.object(
            vlm_screen.rescue_brain, "_call_haiku",
            return_value=(_Resp('{"battle": false}'), 0, ""),
        ) as call:
            self.assertIs(vlm_screen.is_battle_screen("a.png"), False)
            self.assertIs(vlm_screen.is_battle_screen("a.png"), False)
            call.assert_called_once()  # second query served from cache

    def test_api_error_returns_none(self):
        with _fake_frame("h2"), mock.patch.object(
            vlm_screen.rescue_brain, "_call_haiku",
            side_effect=RuntimeError("boom"),
        ):
            self.assertIsNone(vlm_screen.is_battle_screen("x.png"))

    def test_unreadable_frame_returns_none(self):
        with mock.patch.object(
            vlm_screen.preprocess, "load_png_as_array",
            side_effect=OSError("missing"),
        ):
            self.assertIsNone(vlm_screen.is_battle_screen("nope.png"))


if __name__ == "__main__":
    unittest.main()
