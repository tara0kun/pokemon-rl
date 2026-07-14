"""Claude-authored record of what the SakanaAI/Codex implementer did each cycle.

Deliberately NOT written by Codex about itself: the record is rendered from
VERIFIED, objective outcomes — the task Claude assigned, the files that actually
changed, the deterministic gate result, Claude's own review verdict, and whether
the change was committed. Codex's self-narration (implementation.text) is not
used, so the log is the reviewer's (Claude's) account, not the worker's claim.

Idempotent: regenerates SAKANA_LOG.md from handoffs/*.json each call, so it can
be run standalone or hooked after every cycle.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from . import config

LOG_FILE = config.DUAL_DEV_DIR / "SAKANA_LOG.md"


def _load_cycle_handoffs() -> list[dict[str, Any]]:
    """Real per-cycle handoffs (skip the reaper's *_aborted.json stubs)."""
    out = []
    for p in sorted(config.HANDOFF_DIR.glob("*_cycle*.json")):
        if p.stem.endswith("_aborted"):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "gates" in d or "implementation" in d or "review" in d:
            d["_file"] = p.name
            out.append(d)
    return out


def _commit_for(task: str, cycle: int) -> str:
    """Actual [dual-dev] commit hash for a cycle, or '' if none (objective)."""
    title = (task or "").strip().splitlines()[0][:40] if task else ""
    try:
        out = subprocess.run(
            ["git", "log", "--oneline", "-20", "--grep", f"cycle {cycle})"],
            cwd=str(config.ROOT), capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        if "[dual-dev]" in line and (not title or title[:12] in line):
            return line.split()[0]
    return ""


def _fmt_ts(ts: str) -> str:
    # handoff timestamp is YYYYMMDD_HHMMSS
    if len(ts) == 15 and "_" in ts:
        d, t = ts.split("_")
        return f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:4]}"
    return ts


def _changed(h: dict[str, Any]) -> str:
    """The code files Codex actually touched — filtered so early cycles (whose
    gate saw the whole dirty tree before the isolation fix) don't drown the row
    in scratch screenshots and unrelated files."""
    gates = h.get("gates") or {}
    files = [
        f for f in (gates.get("changed_files") or [])
        if not f.startswith("scratch/")
        and not f.endswith(".png")
        and "/handoffs/" not in f
        and not f.endswith(("daily_progress/2026-07-06.md", ".gitignore"))
    ]
    allowed = [a.rstrip("/") for a in (h.get("allowed_paths") or [])]
    if allowed:  # scope to the files Codex was actually assigned
        scoped = [f for f in files if any(f.startswith(a) for a in allowed)]
        files = scoped or files
    if not files:
        return "—"
    if len(files) > 3:
        return "`" + "`, `".join(files[:3]) + f"` +{len(files) - 3}"
    return "`" + "`, `".join(files) + "`"


def _outcome(h: dict[str, Any]) -> str:
    if h.get("committed"):
        return "✅ committed"
    if h.get("aborted"):
        return f"⛔ aborted ({h['aborted']})"
    verdict = (h.get("review") or {}).get("verdict")
    gate = (h.get("gates") or {}).get("passed")
    if gate is False:
        return "❌ gate failed (reverted)"
    if verdict == "FAIL":
        return "❌ review FAIL (reverted)"
    if verdict == "PASS" and not h.get("committed"):
        return "🟡 PASS, dry-run (not committed)"
    return "🟡 no commit"


def regenerate() -> Path:
    handoffs = _load_cycle_handoffs()
    lines = [
        "# SakanaAI (Codex/fugu) 作業記録",
        "",
        "> **記録者: Claude(dual_dev のレビュー担当)。** 各行は Codex の自己申告ではなく、",
        "> 実際に変更されたファイル・決定的ゲートの結果・Claude のレビュー判定・commit の",
        "> 有無という **検証済みの客観データ** から Claude が記述している。自動再生成: `worklog.py`。",
        "",
    ]
    committed = sum(1 for h in handoffs if h.get("committed"))
    lines += [
        f"- 総サイクル: **{len(handoffs)}** / commit 成立: **{committed}**",
        "",
        "| cycle | 日時 | タスク | Codex が変更 | ゲート | Claude 判定 | 結果 | commit |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for h in handoffs:
        cyc = h.get("cycle", "?")
        ts = _fmt_ts(h.get("timestamp", ""))
        task = (h.get("task") or "").strip().splitlines()[0][:44]
        gates = h.get("gates") or {}
        changed = _changed(h)
        gate_s = ("pass" if gates.get("passed") else "fail") if gates else "—"
        if gates.get("diff_lines") is not None:
            gate_s += f" ({gates['diff_lines']}行)"
        verdict = (h.get("review") or {}).get("verdict") or "—"
        outcome = _outcome(h)
        commit = _commit_for(h.get("task", ""), cyc) if h.get("committed") else ""
        lines.append(
            f"| {cyc} | {ts} | {task} | {changed} | {gate_s} | {verdict} "
            f"| {outcome} | {commit or '—'} |"
        )
    lines.append("")
    LOG_FILE.write_text("\n".join(lines), encoding="utf-8")
    return LOG_FILE


if __name__ == "__main__":
    p = regenerate()
    print(f"wrote {p}")
