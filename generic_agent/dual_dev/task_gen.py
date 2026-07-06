"""Auto-generate a backlog of small, safe improvement tasks (option A).

Keeps a 24/7 loop fed without human refills. Uses the Claude CLI (design pool)
to analyze the codebase and propose tightly-scoped tasks, then enqueues them —
so it consumes ONLY Claude quota, never the scarce Codex/SakanaAI call.

Parsing is defensive: the model is asked for strict JSON, but we still extract
the array bracket-to-bracket and drop malformed items rather than crash a loop.
"""
from __future__ import annotations

import json
from typing import Any

from . import clients, config
from .queue import TaskQueue

_GEN_SYSTEM = (
    "You are a senior engineer curating a backlog of SMALL, SAFE, one-file "
    "code-improvement tasks. Output strict JSON only — no prose, no fences."
)


def _candidate_files() -> list[str]:
    root = config.ROOT / "generic_agent"
    files = []
    for p in sorted(root.glob("*.py")):
        if p.name.startswith("_") or p.name == "__init__.py":
            continue
        rel = p.relative_to(config.ROOT).as_posix()
        files.append(rel)
    return files


def _build_prompt(n: int, existing: list[str]) -> str:
    files = "\n".join(f"- {f}" for f in _candidate_files())
    seen = "\n".join(f"- {t}" for t in existing) or "(none)"
    return f"""Propose exactly {n} SMALL, SAFE, well-scoped improvement tasks for
this Python project (generic_agent/, a Pokemon Emerald automation agent). Each
task is implemented later by a separate agent constrained to its allow_paths.

Hard rules for every task:
- Touches ONE file (or a tiny, tightly-related set) listed in allow_paths.
- No direct RAM writes, no saveStateLoad, no hard-coded game coordinates or
  map_id values, no imports of old-branch / pokemon_env / legacy modules.
- Must be verifiable WITHOUT the emulator: py_compile, a unit test, or static
  reasoning. Avoid anything needing a live game to check.
- Prefer: docstrings, small refactors, input validation, edge-case handling,
  small focused unit tests, dead-code removal, error-message clarity, type
  hints. AVOID large features or broad rewrites.

Candidate files (pick allow_paths from these):
{files}

Do NOT duplicate tasks already queued:
{seen}

Output STRICT JSON only: an array of exactly {n} objects, each
{{"task": "<imperative description, Japanese ok>",
  "allow_paths": ["generic_agent/<file>.py"],
  "rationale": "<one line why it's safe and useful>"}}
No text before or after the JSON array."""


def _extract_json_array(text: str) -> list[Any]:
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < 0 or end < start:
        return []
    try:
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def generate_tasks(
    n: int,
    *,
    now: str = "",
    queue: TaskQueue | None = None,
    log_fn=print,
) -> list[int]:
    """Ask Claude for n candidate tasks and enqueue the valid, non-duplicate
    ones. Returns the list of new task ids (empty on model/parse failure)."""
    tq = queue or TaskQueue()
    existing = [t["task"] for t in tq._load() if t.get("status") == "pending"]
    result = clients.run_claude(_build_prompt(n, existing), system=_GEN_SYSTEM)
    if not result.ok:
        log_fn(f"[task-gen] Claude failed: {result.stderr[:200]}")
        return []
    items = _extract_json_array(result.text)
    if not items:
        log_fn("[task-gen] no parseable tasks in model output")
        return []
    seen = {t["task"].strip() for t in tq._load()}
    added: list[int] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        task = str(it.get("task", "")).strip()
        paths = it.get("allow_paths") or []
        if not task or not isinstance(paths, list) or not paths:
            continue
        if task in seen:
            continue  # dedup against everything already queued
        tid = tq.add(task, [str(p) for p in paths], created_at=now)
        added.append(tid)
        seen.add(task)
    log_fn(f"[task-gen] enqueued {len(added)} task(s); backlog {tq.counts()}")
    return added
