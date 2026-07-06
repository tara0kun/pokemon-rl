"""Claude -> Codex -> deterministic gates -> Claude review orchestrator."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import clients, config, gates
from .rate_limit import CodexRateLimiter
from .queue import TaskQueue

CONSTRAINTS = """Repository constraints:
- ROM controls only; no direct RAM writes; no saveStateLoad as progress bypass.
- Do not hard-code game-specific coordinates or map_id values in code.
- Do not import old branch code, pokemon_env, or legacy rule-based modules.
- Keep changes minimal and scoped to the assigned files.
- Never touch main; normal work is on dev.
"""

DESIGN_SYSTEM = (
    "You are the architect/reviewer. Do not edit files or run shell commands. "
    "Give concise implementation instructions and strict acceptance criteria."
)


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _run_path(run_id: str) -> Path:
    return config.RUN_DIR / f"{run_id}.json"


def _save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = _now()
    _run_path(state["run_id"]).write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_state(run_id: str) -> dict[str, Any]:
    path = _run_path(run_id)
    if not path.exists():
        raise FileNotFoundError(f"run state not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _new_run_id() -> str:
    return "run_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _pid_alive(pid: Any) -> bool:
    """Best-effort liveness check for an orchestrate process id.

    On Windows, os.kill(pid, 0) routes to TerminateProcess and would KILL the
    target — never use it here. Query tasklist and confirm the pid is still a
    python image (guards against pid recycling to an unrelated program). When
    we genuinely can't tell, return True so a live run is never reaped.
    """
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if sys.platform.startswith("win"):
        # tasklist output is locale-encoded (cp932 on JP Windows) and crashes
        # subprocess's UTF-8 reader thread; use the Win32 API directly instead.
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.restype = ctypes.c_void_p
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
            )
            if not handle:
                return False  # no such process
            try:
                code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True  # can't tell — assume alive, don't reap
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _reap_stale_runs() -> list[str]:
    """Abort any run left 'running'/'initialized' whose orchestrate process is
    gone (e.g. killed by a broad `Stop-Process python` sweep). Without this a
    dead design phase pins the run-state and the Codex side waits forever."""
    reaped: list[str] = []
    for rp in sorted(config.RUN_DIR.glob("run_*.json")):
        try:
            st = json.loads(rp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if st.get("status") not in {"running", "initialized"}:
            continue
        if _pid_alive(st.get("pid")):
            continue
        st["status"] = "aborted"
        st["current_phase"] = "idle"
        st["updated_at"] = _now()
        st["aborted_reason"] = (
            "stale run reaped: orchestrate process not alive"
        )
        rp.write_text(
            json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        ts = _stamp()
        cyc = int(st.get("next_cycle", 1))
        ho = {
            "task": st.get("task", ""), "cycle": cyc, "timestamp": ts,
            "run_id": st.get("run_id"), "completed": False,
            "aborted": "orchestrate process died (reaped at startup)",
        }
        try:
            (config.HANDOFF_DIR / f"{ts}_cycle{cyc}_aborted.json").write_text(
                json.dumps(ho, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except OSError:
            pass
        reaped.append(st.get("run_id", rp.stem))
    return reaped


def _design_prompt(task: str, allowed_paths: list[str]) -> str:
    allowed = "\n".join(f"- {path}" for path in allowed_paths) or "- Not specified; keep the diff minimal."
    return f"""You are architect for the Pokemon RL generic_agent project.
Codex will implement your plan in the local working tree.

Task:
{task}

Allowed write paths:
{allowed}

{CONSTRAINTS}

Return exactly these sections:
1. Plan
2. Files to change
3. Instructions for Codex
4. Acceptance criteria
5. Risks / unknowns
"""


def _impl_prompt(task: str, design: str, allowed_paths: list[str], state: dict[str, Any]) -> str:
    allowed = "\n".join(f"- {path}" for path in allowed_paths) or "- No explicit allow list; still keep changes minimal."
    resume_note = ""
    if state.get("status") == "paused_usage_limit":
        resume_note = (
            "\nResume note: this run previously paused because a CLI usage limit was hit. "
            "Continue from the current working tree; do not discard existing partial work.\n"
        )
    return f"""You are the implementation agent (Codex) in a dual-AI workflow.
You are not alone in the repository: do not revert or overwrite unrelated work.
{resume_note}
Task:
{task}

Architect plan:
{design}

Allowed write paths:
{allowed}

{CONSTRAINTS}

Implementation rules:
- Edit only the files needed for this task.
- If allowed write paths are provided, do not modify files outside them.
- Continue from any existing partial work in the allowed paths.
- Do not run broad tests; deterministic gates run after you finish.
- Do not commit, push, or change branches.
- Final response must list changed files and any unverified items.
"""


def _review_prompt(task: str, diff: str, gate_summary: str, allowed_paths: list[str]) -> str:
    allowed = "\n".join(f"- {path}" for path in allowed_paths) or "- Not specified."
    shown_diff = diff[:20000]
    return f"""Review the cumulative diff for this task.

Task:
{task}

Allowed write paths:
{allowed}

Gate summary:
{gate_summary}

{CONSTRAINTS}

Diff:
```diff
{shown_diff}
```

Return:
- Good points
- Problems
- Required fixes
- Final line exactly: VERDICT: PASS or VERDICT: FAIL
"""


def _parse_verdict(text: str) -> str:
    upper = text.upper()
    if "VERDICT: PASS" in upper:
        return "PASS"
    if "VERDICT: FAIL" in upper:
        return "FAIL"
    return "UNKNOWN"


def _gate_summary(report: gates.GateReport) -> str:
    lines = [
        f"passed={report.passed}",
        f"diff_lines={report.diff_lines} (limit {config.MAX_DIFF_LINES})",
        f"py_compile_ok={report.py_compile_ok}",
        f"changed_files={report.changed_files}",
    ]
    if report.path_violations:
        lines.append("path_violations=" + "; ".join(report.path_violations))
    if report.dirty_conflicts:
        lines.append("dirty_conflicts=" + "; ".join(report.dirty_conflicts))
    if report.hard_hits:
        lines.append("hard_hits=" + "; ".join(report.hard_hits))
    if report.soft_hits:
        lines.append("soft_warnings=" + "; ".join(report.soft_hits))
    if report.notes:
        lines.append("notes=" + "; ".join(report.notes))
    if not report.py_compile_ok:
        lines.append("py_compile_output=" + report.py_compile_output[:800])
    return "\n".join(lines)


def _handoff_failed(handoff: dict[str, Any]) -> bool:
    """Return True when a completed implementation cycle failed review/gates."""
    gate = handoff.get("gates") or {}
    if not gate:
        return False
    verdict = (handoff.get("review") or {}).get("verdict")
    return not gate.get("passed", False) or verdict != "PASS"


def _result_dict(result: clients.CliResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "text": result.text,
        "stderr": result.stderr,
        "returncode": result.returncode,
        "elapsed_s": result.elapsed_s,
        "usage_limited": result.usage_limited,
    }


def _pause_for_limit(state: dict[str, Any], phase: str, cycle_idx: int, handoff: dict[str, Any]) -> None:
    state["status"] = "paused_usage_limit"
    state["paused_phase"] = phase
    state["next_cycle"] = cycle_idx
    state["last_handoff"] = handoff.get("handoff_file")
    state["message"] = (
        "Usage limit detected. Re-run with: "
        f"python -m generic_agent.dual_dev.orchestrate --resume {state['run_id']}"
    )
    _save_state(state)


def _current_cumulative_diff(state: dict[str, Any]) -> tuple[str, list[str]]:
    post_tree = gates.snapshot_tree()
    diff = gates.diff_between(state["initial_tree"], post_tree)
    changed_files = gates.changed_files_between(state["initial_tree"], post_tree)
    return diff, changed_files


def _write_text_artifact(prefix: str, text: str) -> str:
    path = config.LOG_DIR / f"{_stamp()}_{prefix}.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def run_cycle(
    task: str,
    cycle_idx: int,
    *,
    commit: bool,
    apply: bool,
    allowed_paths: list[str],
    state: dict[str, Any],
) -> dict[str, Any]:
    ts = _stamp()
    handoff: dict[str, Any] = {
        "task": task,
        "cycle": cycle_idx,
        "timestamp": ts,
        "branch": gates.current_branch(),
        "dry_run": not commit,
        "allowed_paths": allowed_paths,
        "run_id": state["run_id"],
    }

    state["status"] = "running"
    state["current_cycle"] = cycle_idx
    state["current_phase"] = "design"
    _save_state(state)

    print(f"\n=== cycle {cycle_idx}: design (Claude) ===")
    design = clients.run_claude(_design_prompt(task, allowed_paths), system=DESIGN_SYSTEM)
    handoff["design"] = _result_dict(design)
    print(design.text[:1500] if design.ok else f"[claude error] {design.stderr[:800]}")
    if design.usage_limited:
        handoff["usage_limited"] = True
        handoff["aborted"] = "usage limit during design"
        _pause_for_limit(state, "design", cycle_idx, handoff)
        return handoff
    if not design.ok:
        handoff["aborted"] = "design failed"
        return handoff

    if not apply:
        handoff["note"] = "no-apply: design only"
        print("\n[no-apply] design only; Codex was not run.")
        return handoff

    state["current_phase"] = "implementation"
    _save_state(state)

    print(f"\n=== cycle {cycle_idx}: implementation (Codex) ===")
    # Throttle the ONLY quota-consuming call. Reserve happens before the call
    # (crash-safe: can't burst on the next run), interval persists across runs.
    min_interval = float(state.get("codex_min_interval_s", 0.0))
    limiter = CodexRateLimiter(min_interval)
    limiter.wait_and_reserve(log_fn=print)
    last_msg = config.LOG_DIR / f"{ts}_cycle{cycle_idx}_codex_last.txt"
    impl = clients.run_codex(_impl_prompt(task, design.text, allowed_paths, state), last_message_file=last_msg)
    limiter.note_last_result(usage_limited=bool(impl.usage_limited))
    handoff["implementation"] = _result_dict(impl)
    print(impl.text[:1500] if impl.text else f"[codex] rc={impl.returncode}")

    diff, changed_files = _current_cumulative_diff(state)
    report = gates.run_gates(
        diff,
        changed_files,
        base_dirty=set(state.get("initial_dirty", [])),
        allowed_paths=allowed_paths,
    )
    handoff["gates"] = asdict(report)
    summary = _gate_summary(report)
    print(f"\n=== cycle {cycle_idx}: deterministic gates ===")
    print(summary)

    if impl.usage_limited:
        handoff["usage_limited"] = True
        handoff["aborted"] = "usage limit during implementation"
        handoff["partial_diff_file"] = _write_text_artifact(f"cycle{cycle_idx}_partial_diff", diff)
        _pause_for_limit(state, "implementation", cycle_idx, handoff)
        return handoff
    if not impl.ok:
        handoff["aborted"] = "implementation failed"
        return handoff

    state["current_phase"] = "review"
    _save_state(state)

    print(f"\n=== cycle {cycle_idx}: review (Claude) ===")
    review = clients.run_claude(_review_prompt(task, diff, summary, allowed_paths), system=DESIGN_SYSTEM)
    verdict = _parse_verdict(review.text) if review.ok else "UNKNOWN"
    handoff["review"] = _result_dict(review)
    handoff["review"]["verdict"] = verdict
    print(review.text[:1500] if review.ok else f"[claude error] {review.stderr[:800]}")
    print(f"\nVERDICT={verdict} GATE_PASSED={report.passed}")

    if review.usage_limited:
        diff_file = _write_text_artifact(f"cycle{cycle_idx}_pending_review_diff", diff)
        pending = {
            "cycle": cycle_idx,
            "diff_file": diff_file,
            "gate_summary": summary,
            "gate_report": asdict(report),
        }
        state["pending_review"] = pending
        handoff["pending_review"] = pending
        handoff["usage_limited"] = True
        handoff["aborted"] = "usage limit during review"
        _pause_for_limit(state, "review", cycle_idx, handoff)
        return handoff

    committed = False
    if commit:
        committed = _maybe_commit(task, cycle_idx, report, verdict)
    else:
        print("\n[dry-run] not committing. Changes remain in the working tree.")
    handoff["committed"] = committed
    handoff["completed"] = True
    state.pop("pending_review", None)
    state["completed_cycles"] = max(int(state.get("completed_cycles", 0)), cycle_idx)
    state["next_cycle"] = cycle_idx + 1
    state["current_phase"] = "idle"
    _save_state(state)
    return handoff


def complete_pending_review(state: dict[str, Any]) -> dict[str, Any] | None:
    pending = state.get("pending_review")
    if not pending:
        return None
    task = state["task"]
    allowed_paths = list(state.get("allowed_paths", []))
    cycle_idx = int(pending["cycle"])
    diff = Path(pending["diff_file"]).read_text(encoding="utf-8")
    summary = pending["gate_summary"]

    print(f"\n=== cycle {cycle_idx}: resume pending review (Claude) ===")
    state["status"] = "running"
    state["current_phase"] = "review"
    _save_state(state)

    review = clients.run_claude(_review_prompt(task, diff, summary, allowed_paths), system=DESIGN_SYSTEM)
    verdict = _parse_verdict(review.text) if review.ok else "UNKNOWN"
    handoff: dict[str, Any] = {
        "task": task,
        "cycle": cycle_idx,
        "timestamp": _stamp(),
        "branch": gates.current_branch(),
        "run_id": state["run_id"],
        "resumed_pending_review": True,
        "review": _result_dict(review) | {"verdict": verdict},
        "gates": pending.get("gate_report"),
    }
    print(review.text[:1500] if review.ok else f"[claude error] {review.stderr[:800]}")
    if review.usage_limited:
        handoff["usage_limited"] = True
        handoff["aborted"] = "usage limit during pending review"
        _pause_for_limit(state, "review", cycle_idx, handoff)
        return handoff

    report = gates.GateReport(**pending["gate_report"])
    committed = False
    if state.get("commit"):
        committed = _maybe_commit(task, cycle_idx, report, verdict)
    else:
        print("\n[dry-run] not committing. Changes remain in the working tree.")
    handoff["committed"] = committed
    handoff["completed"] = True
    state.pop("pending_review", None)
    state["completed_cycles"] = max(int(state.get("completed_cycles", 0)), cycle_idx)
    state["next_cycle"] = cycle_idx + 1
    state["current_phase"] = "idle"
    _save_state(state)
    return handoff


def _maybe_commit(task: str, cycle_idx: int, report: gates.GateReport, verdict: str) -> bool:
    branch = gates.current_branch()
    if branch in config.FORBIDDEN_COMMIT_BRANCHES or branch not in config.ALLOWED_COMMIT_BRANCHES:
        print(f"\n[commit skip] branch '{branch}' is not allowed; use dev.")
        return False
    if not report.passed:
        print("\n[commit skip] deterministic gates failed.")
        return False
    if verdict != "PASS":
        print(f"\n[commit skip] review verdict is {verdict}.")
        return False
    if not report.changed_files:
        print("\n[commit skip] no changed files.")
        return False
    title = task.strip().splitlines()[0][:60]
    msg = f"[dual-dev] {title} (cycle {cycle_idx})"
    subprocess.run(["git", "add", "--", *report.changed_files], cwd=str(config.ROOT), check=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=str(config.ROOT), check=True)
    print(f"\n[committed] {msg}; push is still manual.")
    return True


def _read_task(args: argparse.Namespace) -> str:
    if args.task is not None:
        return args.task
    if args.task_file is not None:
        return Path(args.task_file).read_text(encoding="utf-8")
    if getattr(args, "from_queue", False):
        return "(tasks pulled from backlog queue)"
    raise SystemExit("--task or --task-file is required unless --resume/--from-queue is used")


def _init_or_resume_state(args: argparse.Namespace) -> dict[str, Any]:
    if args.resume:
        state = _load_state(args.resume)
        # Claim the run for THIS process so the stale-run reaper (which checks
        # pid liveness) doesn't later mistake a live resume for a dead run.
        state["pid"] = os.getpid()
        # Keep the persisted throttle unless this invocation overrides it (>0).
        if float(args.codex_min_interval) > 0:
            state["codex_min_interval_s"] = float(args.codex_min_interval)
        print(f"[resume] loaded run {state['run_id']} status={state.get('status')} next_cycle={state.get('next_cycle')}")
        return state

    run_id = args.run_id or _new_run_id()
    state = {
        "run_id": run_id,
        "created_at": _now(),
        "updated_at": _now(),
        "pid": os.getpid(),
        "status": "initialized",
        "task": _read_task(args),
        "allowed_paths": list(args.allow_path),
        "commit": bool(args.commit),
        "apply": not args.no_apply,
        "codex_min_interval_s": float(args.codex_min_interval),
        "initial_tree": gates.snapshot_tree(),
        "initial_dirty": sorted(gates.dirty_files()),
        "next_cycle": 1,
        "completed_cycles": 0,
        "current_phase": "idle",
    }
    _save_state(state)
    print(f"[run] created {run_id}; resume with --resume {run_id}")
    return state


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Claude + Codex semi-autonomous dev loop")
    parser.add_argument("--task", help="Task text")
    parser.add_argument("--task-file", help="Path to a UTF-8 task file")
    parser.add_argument("--run-id", help="Explicit run id for new runs")
    parser.add_argument("--resume", help="Resume a saved run id from generic_agent/dual_dev/runs")
    parser.add_argument("--cycles", type=int, default=1, help="Maximum cycles for this invocation")
    parser.add_argument("--hours", type=float, default=0.0, help="Optional wall-clock limit for this invocation")
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Sleep between cycles")
    parser.add_argument("--codex-min-interval", type=float, default=0.0, help="Min seconds between Codex calls, persisted across runs (0=off). e.g. 1800=48/day")
    parser.add_argument("--allow-path", action="append", default=[], help="Allowed write path; repeatable")
    parser.add_argument("--commit", action="store_true", help="Commit only if gates and review pass")
    parser.add_argument("--no-apply", action="store_true", help="Run Claude design only")
    parser.add_argument("--continue-on-fail", action="store_true", help="Do not stop after a failed gate/review")
    parser.add_argument("--reap-stale", action="store_true", help="Abort dead 'running' runs and exit (unblocks a stuck Codex)")
    parser.add_argument("--enqueue", metavar="TASK", help="Add TASK (with --allow-path) to the backlog and exit")
    parser.add_argument("--from-queue", action="store_true", help="Pull one task per cycle from the backlog instead of --task")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    # Always reap first: a run whose orchestrate was killed mid-design leaves
    # status=running, which the Codex side reads as "Claude still designing".
    reaped = _reap_stale_runs()
    if reaped:
        print(f"[reap] cleared {len(reaped)} stale run(s): {reaped}")
    if args.reap_stale:
        return 0
    if args.enqueue:
        tq = TaskQueue()
        tid = tq.add(args.enqueue, list(args.allow_path), created_at=_now())
        print(f"[queue] added task#{tid}; backlog now {tq.counts()}")
        return 0
    state = _init_or_resume_state(args)
    task = state["task"]
    allowed_paths = list(state.get("allowed_paths", []))
    commit = bool(state.get("commit", False))
    apply = bool(state.get("apply", True))

    tq = TaskQueue()
    if args.from_queue:
        # A task left "running" means a prior cycle was killed mid-flight; no
        # cycle is active at startup, so return it to the backlog.
        for t in tq._load():
            if t.get("status") == "running":
                tq.mark(t["id"], "pending")
        print(f"[queue] backlog at start: {tq.counts()}")

    pending_handoff = complete_pending_review(state)
    if pending_handoff is not None:
        out = config.HANDOFF_DIR / f"{pending_handoff['timestamp']}_cycle{pending_handoff['cycle']}_resume_review.json"
        pending_handoff["handoff_file"] = str(out)
        out.write_text(json.dumps(pending_handoff, ensure_ascii=False, indent=2), encoding="utf-8")
        state["last_handoff"] = str(out)
        _save_state(state)
        if pending_handoff.get("usage_limited"):
            print(f"[pause] usage limit; resume with --resume {state['run_id']}")
            return 0
        if _handoff_failed(pending_handoff) and not args.continue_on_fail:
            state["status"] = "stopped_failed"
            state["next_cycle"] = int(pending_handoff["cycle"]) + 1
            _save_state(state)
            print("[stop] failed resumed review/gates; stopping loop.")
            return 0

    deadline = time.monotonic() + args.hours * 3600 if args.hours > 0 else None
    cycles_left = args.cycles
    if args.hours > 0 and args.cycles == 1:
        cycles_left = 10_000

    start_cycle = int(state.get("next_cycle", 1))
    for offset in range(cycles_left):
        cycle_idx = start_cycle + offset
        if deadline is not None and time.monotonic() >= deadline:
            state["status"] = "paused_time_limit"
            state["next_cycle"] = cycle_idx
            state["message"] = f"Wall-clock limit reached. Resume with --resume {state['run_id']}"
            _save_state(state)
            print("[stop] wall-clock limit reached before next cycle.")
            break

        cur_task, cur_allowed, queue_tid = task, allowed_paths, None
        if args.from_queue:
            qt = tq.next_pending()
            if qt is None:
                state["status"] = "completed"
                state["next_cycle"] = cycle_idx
                _save_state(state)
                print("[queue] backlog drained; stopping loop.")
                break
            cur_task = qt["task"]
            cur_allowed = list(qt.get("allow_paths", []))
            queue_tid = qt["id"]
            tq.mark(queue_tid, "running")
            print(f"[queue] cycle {cycle_idx}: task#{queue_tid}: {cur_task[:70]}")

        handoff = run_cycle(
            cur_task,
            cycle_idx,
            commit=commit,
            apply=apply,
            allowed_paths=cur_allowed,
            state=state,
        )
        if args.from_queue and queue_tid is not None:
            outcome = "failed" if _handoff_failed(handoff) else "done"
            tq.mark(queue_tid, outcome, result={
                "verdict": handoff.get("review", {}).get("verdict"),
                "committed": handoff.get("committed"),
                "aborted": handoff.get("aborted"),
            })
        out = config.HANDOFF_DIR / f"{handoff['timestamp']}_cycle{cycle_idx}.json"
        handoff["handoff_file"] = str(out)
        out.write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")
        state["last_handoff"] = str(out)
        _save_state(state)
        print(f"\nhandoff saved: {out}")

        if handoff.get("usage_limited"):
            print(f"[pause] usage limit; resume with --resume {state['run_id']}")
            break

        gate = handoff.get("gates", {})
        verdict = handoff.get("review", {}).get("verdict")
        if _handoff_failed(handoff) and not args.continue_on_fail:
            state["status"] = "stopped_failed"
            state["next_cycle"] = cycle_idx + 1
            _save_state(state)
            print("[stop] failed gate/review; stopping loop.")
            break

        state["status"] = "completed" if offset == cycles_left - 1 else "running"
        state["next_cycle"] = cycle_idx + 1
        _save_state(state)
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
