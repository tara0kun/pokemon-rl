"""Deterministic safety gates for a dual_dev cycle."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field

from . import config

HARD_PATTERNS: dict[str, re.Pattern[str]] = {
    "ram_write_call": re.compile(
        r"\b(write8|write16|write32|writeram|poke_ram|set_ram|ram_write|"
        r"memory\.write|bus\.write|emu[:.]write)\s*\(",
        re.IGNORECASE,
    ),
    "savestate_load_call": re.compile(
        r"\b(savestateload|loadstate|load_state|save_state_load|loadsavestate)\s*\(",
        re.IGNORECASE,
    ),
    "old_branch_import": re.compile(
        r"^\+.*\b(from|import)\s+(old[\. ]|.*\bpokemon_env\b)",
        re.IGNORECASE,
    ),
}

SOFT_PATTERNS: dict[str, re.Pattern[str]] = {
    "hardcoded_map_id": re.compile(r"\bmap_id\s*=\s*\d+", re.IGNORECASE),
}

CODE_EXTENSIONS = {".py", ".lua", ".js", ".ts", ".tsx", ".jsx", ".sh", ".ps1"}


@dataclass
class GateReport:
    passed: bool = False
    diff_lines: int = 0
    changed_files: list[str] = field(default_factory=list)
    py_compile_ok: bool = True
    py_compile_output: str = ""
    hard_hits: list[str] = field(default_factory=list)
    soft_hits: list[str] = field(default_factory=list)
    path_violations: list[str] = field(default_factory=list)
    dirty_conflicts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _git(*args: str, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(config.ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return proc.stdout


def current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD").strip()


def dirty_files() -> set[str]:
    """Return currently dirty/untracked paths from git status."""
    out = _git("status", "--porcelain")
    paths: set[str] = set()
    for line in out.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"').replace("\\", "/")
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.add(path)
    return paths


def _path_touches_dirty(path: str, dirty_path: str) -> bool:
    """Return True when ``path`` overlaps an initially dirty git path.

    ``git status --porcelain`` reports untracked directories as entries such as
    ``generic_agent/dual_dev/``. A later changed file inside that directory must
    still be treated as touching pre-existing dirty work; exact-only matching
    would miss that case and could let one agent overwrite another agent's
    untracked files.
    """
    norm = path.replace("\\", "/").strip("/")
    dirty = dirty_path.replace("\\", "/").strip("/")
    if not norm or not dirty:
        return False
    return norm == dirty or norm.startswith(dirty + "/") or dirty.startswith(norm + "/")


def snapshot_tree() -> str:
    """Write a temporary git tree for the current working tree.

    The real index is not touched. Ignored files are excluded by git add.
    """
    fd, tmp_index = tempfile.mkstemp(prefix="dualdev_idx_")
    os.close(fd)
    os.unlink(tmp_index)
    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = tmp_index
    try:
        _git("add", "-A", env=env)
        return _git("write-tree", env=env).strip()
    finally:
        if os.path.exists(tmp_index):
            os.unlink(tmp_index)


def diff_between(base_tree: str, post_tree: str) -> str:
    return _git("--no-pager", "diff", base_tree, post_tree)


def changed_files_between(base_tree: str, post_tree: str) -> list[str]:
    out = _git("diff", "--name-only", base_tree, post_tree)
    return [line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()]


def _diff_line_count(diff: str) -> int:
    count = 0
    for line in diff.splitlines():
        if (line.startswith("+") and not line.startswith("+++")) or (
            line.startswith("-") and not line.startswith("---")
        ):
            count += 1
    return count


def _code_added_lines(diff: str) -> list[tuple[str, str]]:
    """Return (path, line) for added lines in code files only."""
    current_path = ""
    results: list[tuple[str, str]] = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_path = line.removeprefix("+++ b/")
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        if any(current_path.endswith(ext) for ext in CODE_EXTENSIONS):
            results.append((current_path, line))
    return results


def _is_allowed(path: str, allowed_paths: list[str]) -> bool:
    if not allowed_paths:
        return True
    norm = path.replace("\\", "/").strip("/")
    for allowed in allowed_paths:
        prefix = allowed.replace("\\", "/").strip("/")
        if norm == prefix or norm.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


def run_gates(
    diff: str,
    changed_files: list[str],
    *,
    base_dirty: set[str] | None = None,
    allowed_paths: list[str] | None = None,
) -> GateReport:
    report = GateReport()
    report.diff_lines = _diff_line_count(diff)
    report.changed_files = list(changed_files)
    allowed_paths = allowed_paths or []
    base_dirty = base_dirty or set()

    for path in report.changed_files:
        if not _is_allowed(path, allowed_paths):
            report.path_violations.append(path)
        dirty_hits = sorted(dirty for dirty in base_dirty if _path_touches_dirty(path, dirty))
        if dirty_hits:
            report.dirty_conflicts.append(f"{path} touches pre-existing dirty path(s): {', '.join(dirty_hits)}")

    for name, pattern in HARD_PATTERNS.items():
        for path, line in _code_added_lines(diff):
            if pattern.search(line):
                report.hard_hits.append(f"{name}: {path}: {line.strip()[:120]}")
    for name, pattern in SOFT_PATTERNS.items():
        for path, line in _code_added_lines(diff):
            if pattern.search(line):
                report.soft_hits.append(f"{name}: {path}: {line.strip()[:120]}")

    over_limit = report.diff_lines > config.MAX_DIFF_LINES
    if over_limit:
        report.notes.append(f"diff {report.diff_lines} lines > limit {config.MAX_DIFF_LINES}")

    py_files = [
        path
        for path in report.changed_files
        if path.endswith(".py") and (config.ROOT / path).exists()
    ]
    if py_files:
        proc = subprocess.run(
            [config.python_exe(), "-m", "py_compile", *py_files],
            cwd=str(config.ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        report.py_compile_ok = proc.returncode == 0
        report.py_compile_output = (proc.stdout + proc.stderr).strip()

    report.passed = (
        not report.hard_hits
        and not report.path_violations
        and not report.dirty_conflicts
        and not over_limit
        and report.py_compile_ok
    )
    return report
