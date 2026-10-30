"""README 冒頭の「現在地と数値」が、リポジトリの中身と一致していること。

手で書き写した数値は古くなる（2026-10 時点で README は Badge 0〜1 のまま、
台本の行数は 07 月の値だった）。ずれたら CI を赤にして気づけるようにする。
直し方: poke-rl/Scripts/python.exe -m generic_agent.tools.readme_stats --write
"""
from __future__ import annotations

import shutil
import unittest

from generic_agent.tools import readme_stats


class TestReadmeStats(unittest.TestCase):
    def test_readme_block_matches_generated(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git が無い（行数は git の追跡対象から数える）")
        current = readme_stats.current_block(readme_stats.README.read_text(encoding="utf-8"))
        self.assertIsNotNone(current, "README に readme-stats のブロックが無い")
        self.assertEqual(
            current, readme_stats.render(),
            "README の数値が古い。python -m generic_agent.tools.readme_stats --write を実行する",
        )


if __name__ == "__main__":
    unittest.main()
