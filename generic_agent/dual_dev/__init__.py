"""Semi-autonomous dual-AI development loop.

This package coordinates two local subscription-backed CLIs:

- Claude Code (claude -p) as architect/reviewer.
- Codex CLI (codex exec) as implementer/verifier.

The orchestrator is intentionally conservative: deterministic gates decide
whether a cycle is acceptable, and the default mode is dry-run (no commit).
"""
