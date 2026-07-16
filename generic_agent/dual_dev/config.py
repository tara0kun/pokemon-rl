"""Configuration for the dual_dev orchestrator."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK_ROOT = ROOT
DUAL_DEV_DIR = ROOT / "generic_agent" / "dual_dev"
LOG_DIR = DUAL_DEV_DIR / "logs"
HANDOFF_DIR = DUAL_DEV_DIR / "handoffs"
RUN_DIR = DUAL_DEV_DIR / "runs"

CODEX_TIMEOUT_S = int(os.environ.get("DUAL_DEV_CODEX_TIMEOUT", "1800"))
CLAUDE_TIMEOUT_S = int(os.environ.get("DUAL_DEV_CLAUDE_TIMEOUT", "600"))
MAX_DIFF_LINES = int(os.environ.get("DUAL_DEV_MAX_DIFF_LINES", "400"))

# dev = 通常運用。sakana/* = 隔離 worktree 用(並行 production commit と gate の
# cumulative diff が競合するため、SakanaAI は専用 worktree/branch で回す)。
ALLOWED_COMMIT_BRANCHES = {
    b.strip()
    for b in os.environ.get(
        "DUAL_DEV_ALLOWED_BRANCHES", "dev,sakana/dual-dev"
    ).split(",")
    if b.strip()
}
FORBIDDEN_COMMIT_BRANCHES = {"main"}


def latest_claude_exe() -> str:
    """Return the Claude Code executable bundled with the VSCode extension."""
    override = os.environ.get("DUAL_DEV_CLAUDE_EXE")
    if override:
        return override
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    ext_dir = Path(os.environ["USERPROFILE"]) / ".vscode" / "extensions"
    candidates = sorted(
        ext_dir.glob("anthropic.claude-code-*/resources/native-binary/claude.exe"),
        key=lambda path: path.parent.parent.parent.name,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            "claude.exe not found. Install the Claude Code VSCode extension "
            "or set DUAL_DEV_CLAUDE_EXE."
        )
    return str(candidates[0])


def codex_exe() -> str:
    """Return the Codex CLI executable."""
    override = os.environ.get("DUAL_DEV_CODEX_EXE")
    if override:
        return override
    on_path = shutil.which("codex")
    if on_path and "WindowsApps" not in on_path:
        return on_path
    ext_dir = Path(os.environ["USERPROFILE"]) / ".vscode" / "extensions"
    candidates = sorted(
        ext_dir.glob("openai.chatgpt-*/bin/windows-x86_64/codex.exe"),
        key=lambda path: path.parent.parent.parent.name,
        reverse=True,
    )
    if candidates:
        return str(candidates[0])
    if on_path:
        return on_path
    raise FileNotFoundError("codex.exe not found. Set DUAL_DEV_CODEX_EXE.")


def python_exe() -> str:
    """Return the project Python executable, falling back to current Python."""
    exe = WORK_ROOT / "poke-rl" / "Scripts" / "python.exe"
    if exe.exists():
        return str(exe)
    return shutil.which("python") or "python"


def ensure_dirs() -> None:
    for directory in (LOG_DIR, HANDOFF_DIR, RUN_DIR):
        directory.mkdir(parents=True, exist_ok=True)
