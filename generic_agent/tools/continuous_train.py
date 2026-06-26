"""Long-running self-improvement loop — collect / train / validate / log.

The user explicitly asked for semi-permanent autonomous work. To avoid
the "repeat the same thing for 6 cycles in a row" trap that this
project already fell into, every iteration:

1. Picks a slightly different exclude-source filter from a rotating
   pool so the next CNN isn't trained on the exact same input
   distribution as the previous one.
2. Runs the heuristic for 1500 turns to grow demonstrations.jsonl.
3. Re-trains the multi-modal CNN if the dataset grew by N demos OR
   every Kth iteration regardless.
4. Runs a short (500-turn) CNN-only validation pass and records
   coverage metrics (unique pos, maps reached, button balance).
5. Tracks the best validation outcome by a composite score (more
   unique positions, more maps, lower max-button-fraction); saves the
   winning checkpoint under models/brain_cnn_best.pt.
6. Logs everything to a JSONL ledger so progress is auditable later.

API spend: zero. Heuristic + training + CNN inference all run locally.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

from .. import config

LEDGER = config.LOG_DIR / "continuous_train.jsonl"
BEST_CHECKPOINT = config.MODEL_DIR / "brain_cnn_best.pt"
STATE_FILE = config.LOG_DIR / "continuous_state.json"


def _log(entry: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.time(), **entry}
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(json.dumps(entry, ensure_ascii=False))


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"iter": 0, "best_score": 0.0, "best_at_iter": 0, "history_demos": []}


def _save_state(s: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")


def _demo_count() -> int:
    if not config.DATASET_INDEX.exists():
        return 0
    return sum(1 for _ in config.DATASET_INDEX.open(encoding="utf-8"))


def _run(cmd: list[str], log_path: Path, timeout: int) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as out:
        try:
            p = subprocess.run(
                cmd, stdout=out, stderr=subprocess.STDOUT,
                timeout=timeout, check=False,
            )
            return p.returncode
        except subprocess.TimeoutExpired:
            return -1


def _score_run(analyzer_output: str) -> tuple[float, dict]:
    metrics: dict[str, float] = {"unique_pos": 0, "maps": 0, "max_button_frac": 1.0}
    for line in analyzer_output.splitlines():
        line = line.strip()
        if line.startswith("unique positions:"):
            try:
                metrics["unique_pos"] = float(line.split(":")[1].strip())
            except (IndexError, ValueError):
                pass
        elif line.startswith("unique maps:"):
            try:
                v = line.split(":")[1].strip().split()[0]
                metrics["maps"] = float(v)
            except (IndexError, ValueError):
                pass
        elif "(" in line and "%" in line and not line.startswith("==="):
            try:
                # button breakdown rows look like "  Up    :  4768 ( 95.4%)"
                pct_token = line.split("(")[-1].split("%")[0].strip()
                pct = float(pct_token) / 100.0
                if 0 < pct <= 1.0:
                    metrics["max_button_frac"] = max(
                        metrics["max_button_frac"], pct,
                    ) if metrics["max_button_frac"] > 0 else pct
            except (IndexError, ValueError):
                pass
    score = (
        metrics["unique_pos"] * 1.0
        + metrics["maps"] * 50.0
        - metrics["max_button_frac"] * 100.0
    )
    return score, metrics


def iterate(state: dict, args: argparse.Namespace) -> None:
    it = state["iter"] + 1
    state["iter"] = it
    tag = time.strftime("%Y%m%dT%H%M%S")

    # Always-exclude noise sources (PWhiddy lesson: noisy demos
    # produce noisy CNNs). The optional rotation only adds extra
    # filters on top.
    ALWAYS_EXCLUDE = [
        "anomaly_escape", "escape", "force_explore",
        "menu_close", "npc_sweep", "pre-save",
    ]
    filter_pool = [
        [],
        ["dialog_continue"],
        ["fallback"],
        ["recovery"],
        ["dialog_continue", "fallback"],
    ]
    excl = list(ALWAYS_EXCLUDE) + filter_pool[it % len(filter_pool)]

    demos_before = _demo_count()
    log_heur = config.LOG_DIR / f"continuous_heur_{tag}.log"
    code = _run(
        [
            sys.executable, "-X", "utf8", "-u",
            "-m", "generic_agent.claude_heuristic",
            "--turns", str(args.collect_turns), "--dataset",
        ],
        log_heur, timeout=args.collect_timeout,
    )
    demos_after = _demo_count()
    new_demos = demos_after - demos_before

    train_done = False
    val_done = False
    score = 0.0
    metrics: dict[str, float] = {}
    val_log = ""

    should_train = (
        new_demos >= args.train_threshold
        or it % args.force_train_every == 0
    )
    if should_train and demos_after >= 500:
        log_train = config.LOG_DIR / f"continuous_train_{tag}.log"
        try:
            import torch as _t
            _gpu = _t.cuda.is_available()
        except ImportError:
            _gpu = False
        train_cmd = [
            sys.executable, "-X", "utf8", "-u",
            "-m", "generic_agent.tools.train_imitation",
            "--arch", "multimodal",
            "--epochs", str(args.epochs),
            "--batch", "128" if _gpu else "32",
            "--lr", "1e-4",
            "--max-samples", "20000",
        ]
        for s in excl:
            train_cmd.extend(["--exclude-sources", s])
        rc = _run(train_cmd, log_train, timeout=args.train_timeout)
        train_done = rc == 0

        if train_done:
            log_val = config.LOG_DIR / f"continuous_val_{tag}.log"
            val_cmd = [
                sys.executable, "-X", "utf8", "-u",
                "-m", "generic_agent.auto_loop",
                "--turns", str(args.val_turns),
                "--budget", "0.01",
                "--cnn", str(config.MODEL_DIR / "brain_cnn_latest.pt"),
            ]
            _run(val_cmd, log_val, timeout=args.val_timeout)

            log_anal = config.LOG_DIR / f"continuous_anal_{tag}.log"
            _run(
                [
                    sys.executable, "-X", "utf8", "-u",
                    "-m", "generic_agent.tools.analyze_run",
                ],
                log_anal, timeout=60,
            )
            val_log = log_anal.read_text(encoding="utf-8", errors="replace")
            score, metrics = _score_run(val_log)
            val_done = True

            if score > state["best_score"]:
                state["best_score"] = score
                state["best_at_iter"] = it
                latest = config.MODEL_DIR / "brain_cnn_latest.pt"
                if latest.exists():
                    BEST_CHECKPOINT.write_bytes(latest.read_bytes())

    state["history_demos"].append(
        {"iter": it, "demos_total": demos_after, "new_demos": new_demos}
    )
    if len(state["history_demos"]) > 200:
        state["history_demos"] = state["history_demos"][-200:]

    _log({
        "iter": it,
        "exclude_filter": excl,
        "demos_before": demos_before,
        "demos_after": demos_after,
        "new_demos": new_demos,
        "heur_rc": code,
        "trained": train_done,
        "validated": val_done,
        "score": score,
        "metrics": metrics,
        "best_score": state["best_score"],
        "best_at_iter": state["best_at_iter"],
    })
    _save_state(state)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-iters", type=int, default=0,
                        help="0 = run forever")
    parser.add_argument("--collect-turns", type=int, default=1500)
    parser.add_argument("--collect-timeout", type=int, default=3600)
    parser.add_argument("--train-threshold", type=int, default=800)
    parser.add_argument("--force-train-every", type=int, default=4)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--train-timeout", type=int, default=3600)
    parser.add_argument("--val-turns", type=int, default=500)
    parser.add_argument("--val-timeout", type=int, default=900)
    parser.add_argument("--reset-state", action="store_true")
    parser.add_argument(
        "--early-stop-after", type=int, default=5,
        help="PufferLib-style: stop run if N iters in a row have new_demos < 50",
    )
    args = parser.parse_args()

    if args.reset_state and STATE_FILE.exists():
        STATE_FILE.unlink()

    state = _load_state()
    _log({"event": "start", "args": vars(args), "resume_iter": state["iter"]})

    iter_count = 0
    no_progress_streak = 0
    while args.max_iters == 0 or iter_count < args.max_iters:
        try:
            iterate(state, args)
            last_entry = state["history_demos"][-1] if state["history_demos"] else None
            if last_entry and last_entry.get("new_demos", 0) < 50:
                no_progress_streak += 1
                _log({
                    "event": "no_progress_warn",
                    "streak": no_progress_streak,
                    "limit": args.early_stop_after,
                })
                if no_progress_streak >= args.early_stop_after:
                    _log({
                        "event": "stop",
                        "reason": f"early_stop: {no_progress_streak} iters < 50 new_demos",
                    })
                    return 0
            else:
                no_progress_streak = 0
        except KeyboardInterrupt:
            _log({"event": "stop", "reason": "keyboard interrupt"})
            return 0
        except Exception as exc:  # pragma: no cover
            _log({"event": "iter_error", "error": repr(exc)})
            time.sleep(10)
        iter_count += 1
    _log({"event": "stop", "reason": "max_iters reached"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
