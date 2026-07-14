# dual_dev: Claude + Codex semi-autonomous development loop

This directory contains a conservative orchestrator for running two local AI
CLIs against the same repository:

- **Claude Code** (`claude -p`) acts as architect and reviewer.
- **Codex CLI** (`codex exec`) acts as implementer.
- **Deterministic gates** decide pass/fail before any optional commit.

Claude is invoked through the local Claude Code CLI, not the Anthropic
`messages` API. `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` are stripped from
the Claude subprocess environment so this loop uses the Claude Code login path
instead of accidentally consuming API credits.

Default mode is **dry-run**. It never commits unless `--commit` is passed and
both deterministic gates and Claude review pass.

## Quick start

Design only (no repository edits):

```powershell
poke-rl/Scripts/python.exe -m generic_agent.dual_dev.orchestrate `
  --task "Explain the safest implementation plan for X" `
  --no-apply
```

One dry-run implementation cycle, scoped to specific files:

```powershell
poke-rl/Scripts/python.exe -m generic_agent.dual_dev.orchestrate `
  --task "Implement X with minimal changes" `
  --allow-path generic_agent/local_brain.py `
  --allow-path generic_agent/tests/
```

Longer autonomous run with a wall-clock cap:

```powershell
poke-rl/Scripts/python.exe -m generic_agent.dual_dev.orchestrate `
  --task-file generic_agent/dual_dev/tasks/today.txt `
  --allow-path generic_agent/local_brain.py `
  --hours 6 `
  --sleep-seconds 30
```

Add `--commit` only after dry-run behavior is trusted. Push remains manual.

## Resume after usage limits

Every run creates a state file under `runs/` and prints a run id such as
`run_20260703_140000`.

Resume the same run later:

```powershell
poke-rl/Scripts/python.exe -m generic_agent.dual_dev.orchestrate `
  --resume run_20260703_140000 `
  --hours 6 `
  --sleep-seconds 30
```

The state file stores:

- original task and allowed write paths
- initial git tree and initial dirty files
- next cycle number
- current phase (`design`, `implementation`, `review`, or `idle`)
- pending review data if a limit hit after Codex finished but before Claude review

If Claude/Codex returns a usage-limit style message, the run is marked
`paused_usage_limit` and the command exits cleanly. The next invocation with
`--resume` continues from the current working tree.

## Safety gates

A cycle fails if any of these happen:

- Diff exceeds `DUAL_DEV_MAX_DIFF_LINES` (default: 400 changed lines).
- Codex modifies files outside `--allow-path`.
- Codex modifies a file that was already dirty before the run started.
- Added code contains direct RAM write calls, save-state load calls, or old
  branch imports such as `pokemon_env`.
- Changed Python files fail `py_compile`.

## Notes

- Logs, handoff JSON, and run state are written under `logs/`, `handoffs/`, and
  `runs/`, all ignored.
- Keep tasks small. Use one task per cycle, with explicit `--allow-path` values.
- Do not use this loop on `main`; the project workflow keeps active work on `dev`.
