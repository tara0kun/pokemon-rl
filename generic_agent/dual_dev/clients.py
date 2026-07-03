"""Thin subprocess clients for Claude Code and Codex CLI."""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import config

LIMIT_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"usage limit",
        r"rate limit",
        r"limit reached",
        r"quota exceeded",
        r"exceeded.*quota",
        r"too many requests",
        r"429",
        r"try again later",
        r"come back later",
        r"daily limit",
    )
]


@dataclass
class CliResult:
    ok: bool
    text: str
    stderr: str
    returncode: int
    elapsed_s: float
    usage_limited: bool = False


def looks_like_usage_limit(text: str) -> bool:
    """Best-effort detection for subscription/API usage-limit messages."""
    return any(pattern.search(text) for pattern in LIMIT_PATTERNS)


def _result(*, proc: subprocess.CompletedProcess[str], start: float, text: str = "") -> CliResult:
    stdout = text or (proc.stdout or "")
    stderr = proc.stderr or ""
    combined = f"{stdout}\n{stderr}"
    return CliResult(
        ok=proc.returncode == 0,
        text=stdout.strip(),
        stderr=stderr.strip(),
        returncode=proc.returncode,
        elapsed_s=time.monotonic() - start,
        usage_limited=looks_like_usage_limit(combined),
    )


def _error_result(*, start: float, stderr: str, returncode: int = 1) -> CliResult:
    return CliResult(
        ok=False,
        text="",
        stderr=stderr,
        returncode=returncode,
        elapsed_s=time.monotonic() - start,
        usage_limited=looks_like_usage_limit(stderr),
    )


def run_claude(prompt: str, *, system: str | None = None) -> CliResult:
    """Run Claude Code in non-interactive text mode.

    Claude is used as architect/reviewer only. File and shell tools are
    explicitly disallowed so this call cannot edit the repository.
    """
    args = [
        config.latest_claude_exe(),
        "-p",
        "--output-format",
        "text",
        "--disallowedTools",
        "Edit",
        "Write",
        "Bash",
    ]
    if system:
        args += ["--append-system-prompt", system]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.CLAUDE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        return _error_result(
            start=start,
            stderr=f"Claude timed out after {config.CLAUDE_TIMEOUT_S}s: {exc}",
            returncode=124,
        )
    except (FileNotFoundError, OSError) as exc:
        return _error_result(start=start, stderr=f"Claude failed to start: {exc}")
    return _result(proc=proc, start=start)


def run_codex(prompt: str, *, last_message_file: Path) -> CliResult:
    """Run Codex non-interactively as the implementation agent."""
    args = [
        config.codex_exe(),
        "exec",
        "-",
        "-C",
        str(config.WORK_ROOT),
        "-s",
        "workspace-write",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--output-last-message",
        str(last_message_file),
    ]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.CODEX_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        last = ""
        if last_message_file.exists():
            last = last_message_file.read_text(encoding="utf-8", errors="replace")
        result = _error_result(
            start=start,
            stderr=f"Codex timed out after {config.CODEX_TIMEOUT_S}s: {exc}",
            returncode=124,
        )
        result.text = last.strip()
        return result
    except (FileNotFoundError, OSError) as exc:
        return _error_result(start=start, stderr=f"Codex failed to start: {exc}")
    last = ""
    if last_message_file.exists():
        last = last_message_file.read_text(encoding="utf-8", errors="replace")
    return _result(proc=proc, start=start, text=last or proc.stdout or "")
