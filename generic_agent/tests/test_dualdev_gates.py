"""dual_dev の決定論ゲートのテスト (INVARIANTS A / F 節の機械強制部分)。

git / subprocess を呼ばない純粋パスのみ検証する
(py_compile は存在しないファイルパスを使うことでスキップさせる)。
"""
from __future__ import annotations

import unittest

from generic_agent.dual_dev import gates

FAKE = "generic_agent/__nonexistent_test_file__.py"


def _diff(*added_lines: str, path: str = FAKE) -> str:
    body = "\n".join(f"+{line}" for line in added_lines)
    return f"+++ b/{path}\n{body}\n"


class TestHardPatterns(unittest.TestCase):
    def test_ram_write_rejected(self) -> None:
        r = gates.run_gates(
            _diff("client.write8(0x02000000, 1)"), [FAKE],
            allowed_paths=[FAKE],
        )
        self.assertFalse(r.passed)
        self.assertTrue(any("ram_write_call" in h for h in r.hard_hits))

    def test_savestate_load_rejected(self) -> None:
        r = gates.run_gates(
            _diff('emu.loadState("x.ss1")'), [FAKE], allowed_paths=[FAKE],
        )
        self.assertFalse(r.passed)
        self.assertTrue(any("savestate_load_call" in h for h in r.hard_hits))

    def test_old_branch_import_rejected(self) -> None:
        r = gates.run_gates(
            _diff("from pokemon_env import PokemonEmeraldEnv"), [FAKE],
            allowed_paths=[FAKE],
        )
        self.assertFalse(r.passed)
        self.assertTrue(any("old_branch_import" in h for h in r.hard_hits))

    def test_clean_diff_passes(self) -> None:
        r = gates.run_gates(
            _diff("x = 1  # harmless"), [FAKE], allowed_paths=[FAKE],
        )
        self.assertTrue(r.passed, msg=f"unexpected failure: {r}")


class TestPathAndDirtyGates(unittest.TestCase):
    def test_path_outside_allowlist_rejected(self) -> None:
        r = gates.run_gates(
            _diff("x = 1"), [FAKE],
            allowed_paths=["generic_agent/tests/"],
        )
        self.assertFalse(r.passed)
        self.assertIn(FAKE, r.path_violations)

    def test_dirty_directory_overlap_detected(self) -> None:
        # untracked ディレクトリ表記 (trailing slash) の中のファイルも
        # dirty 衝突として扱う (gates._path_touches_dirty)
        r = gates.run_gates(
            _diff("x = 1"), [FAKE],
            base_dirty={"generic_agent/"},
            allowed_paths=[FAKE],
        )
        self.assertFalse(r.passed)
        self.assertTrue(r.dirty_conflicts)

    def test_diff_line_cap(self) -> None:
        many = _diff(*[f"x{i} = {i}" for i in range(gates.config.MAX_DIFF_LINES + 1)])
        r = gates.run_gates(many, [FAKE], allowed_paths=[FAKE])
        self.assertFalse(r.passed)


class TestNonCodeFilesIgnoredByPatterns(unittest.TestCase):
    def test_markdown_lines_not_pattern_scanned(self) -> None:
        # HARD_PATTERNS は CODE_EXTENSIONS のみ対象 — docs に write8 と
        # 書いてもゲートは落ちない
        doc = "generic_agent/__nonexistent_doc__.md"
        r = gates.run_gates(
            _diff("this doc mentions write8(addr) safely", path=doc),
            [doc], allowed_paths=[doc],
        )
        self.assertTrue(r.passed, msg=f"unexpected failure: {r}")


if __name__ == "__main__":
    unittest.main()
