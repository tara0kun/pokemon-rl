"""Standalone monitor subprocess — truly autonomous, runs without Claude.

Why: Claude-side cron jobs only fire when Claude is idle (no user
message in flight). During active conversation the user sees zero
monitoring because the cron's job IS Claude. This subprocess fixes
that: it polls every 5 min on its own, writes cron_check.jsonl, and
takes corrective action without any Claude involvement.

Responsibilities:
- continuous_train subprocess alive check (via PID file)
- mGBA RAM diff (frozen detection >5 min → kill+restart)
- savestate_autosnap.ss1 mtime freshness (>20 min stale → warn)
- demonstrations.jsonl row delta (no growth >10 min → warn)
- stuck detection (same map+pos for 30 min → touch force_explore.flag;
  new-map reached → unlink it)
- continuous_train state file integrity

Never touches save state load; only verifies progress.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

from .. import config, io as io_mod, state as state_mod


LOG_FILE = config.LOG_DIR / "cron_check.jsonl"
PID_FILE = config.LOG_DIR / "continuous_train.pid"
AUTOSNAP = config.MEMORY_DIR / "savestate_autosnap.ss1"
FORCE_FLAG = config.MEMORY_DIR / "force_explore.flag"
DEMOS_PATH = config.DATASET_DIR / "demonstrations.jsonl"
CONTINUOUS_LEDGER = config.LOG_DIR / "continuous_train.jsonl"
DIAGNOSE_DIR = config.LOG_DIR / "monitor_diag"
SELF_CHECK_LOG = config.LOG_DIR / "self_check.jsonl"
CURRICULUM_INDEX_PATH = config.MEMORY_DIR / "curriculum_index.json"


def _read_recent_iters(n: int = 8) -> list[dict]:
    """Read last N iter entries from continuous_train.jsonl."""
    if not CONTINUOUS_LEDGER.exists():
        return []
    try:
        with CONTINUOUS_LEDGER.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        iters: list[dict] = []
        for line in lines[-30:]:
            try:
                d = json.loads(line)
                if "iter" in d:
                    iters.append(d)
            except (json.JSONDecodeError, ValueError):
                continue
        return iters[-n:]
    except OSError:
        return []


def _count_milestones() -> int:
    if not CURRICULUM_INDEX_PATH.exists():
        return 0
    try:
        data = json.loads(CURRICULUM_INDEX_PATH.read_text(encoding="utf-8"))
        return len(data.get("milestones", []))
    except (OSError, json.JSONDecodeError):
        return 0


def self_check_analyze(state_snapshot: dict) -> dict:
    """Run sophisticated analysis on recent iters + state, return action plan.

    Returns:
        {
            "issues": [str],  # detected problems
            "actions": [str],  # auto-fixes to take
            "metrics": {...},
        }
    """
    issues: list[str] = []
    actions: list[str] = []
    metrics: dict = {}

    iters = _read_recent_iters(8)
    metrics["iter_count"] = len(iters)
    if iters:
        scores = [it.get("score", 0.0) for it in iters]
        metrics["scores_recent"] = scores
        metrics["score_mean"] = sum(scores) / len(scores)
        metrics["best_score"] = max((it.get("best_score", 0) for it in iters), default=0)
        max_button_frac = [
            it.get("metrics", {}).get("max_button_frac", 0)
            for it in iters
        ]
        metrics["max_button_frac_avg"] = (
            sum(max_button_frac) / len(max_button_frac)
            if max_button_frac else 0
        )
        # Degraded CNN: 3+ iters with score < 25
        low_scores = sum(1 for s in scores if s < 25)
        if low_scores >= 5:
            issues.append(f"CNN_degraded:scores<25 in {low_scores}/{len(scores)} iters")
            actions.append("rotate_filter:add escape exclusion")
        # All val collapsed to single button
        if metrics["max_button_frac_avg"] >= 0.99 and len(iters) >= 5:
            issues.append("CNN_val_collapse:max_button_frac=1.0 sustained")
            actions.append("hint:reduce_epochs or lr_half")
    metrics["milestones"] = _count_milestones()
    metrics["flags"] = state_snapshot.get("flags", 0)
    metrics["party"] = state_snapshot.get("party", 0)
    metrics["demos"] = state_snapshot.get("demos", 0)

    return {
        "issues": issues,
        "actions": actions,
        "metrics": metrics,
    }


def _log_self_check(payload: dict) -> None:
    SELF_CHECK_LOG.parent.mkdir(parents=True, exist_ok=True)
    payload["ts"] = time.time()
    payload["iso"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with SELF_CHECK_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _take_diag_screenshot(client: "io_mod.MGBAClient", tag: str) -> Path | None:
    DIAGNOSE_DIR.mkdir(parents=True, exist_ok=True)
    p = DIAGNOSE_DIR / f"diag_{tag}_{int(time.time())}.png"
    try:
        client.screenshot(p)
        return p
    except (io_mod.EmulatorError, OSError):
        return None


def _recent_buttons_from_heur_log(n: int = 50) -> list[str]:
    """Read the latest heuristic log; pull out which buttons were pressed."""
    logs = sorted(
        config.LOG_DIR.glob("continuous_heur_*.log"),
        key=lambda p: p.stat().st_mtime,
    )
    if not logs:
        return []
    try:
        text = logs[-1].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    btns: list[str] = []
    for line in text.splitlines():
        if not line or line.startswith("["):
            continue
        for tok in (
            "Up", "Down", "Left", "Right",
            "A", "B", "Start", "Select",
        ):
            if f">{tok} " in line or f": {tok} " in line:
                btns.append(tok)
    return btns[-n:]


def _diagnose_stuck(
    client: "io_mod.MGBAClient",
    cur_map: tuple[int, int],
    cur_pos: tuple[int, int],
    pos_stuck_for: float,
) -> dict:
    """When stuck >5 min, look at the screen + log to figure out why."""
    diag: dict = {
        "stuck_for_s": int(pos_stuck_for),
        "map": list(cur_map),
        "pos": list(cur_pos),
    }
    recent_buttons = _recent_buttons_from_heur_log(n=30)
    diag["recent_buttons"] = recent_buttons
    if recent_buttons:
        from collections import Counter
        c = Counter(recent_buttons)
        diag["button_freq"] = dict(c.most_common(4))
        dom_btn, dom_n = c.most_common(1)[0]
        if dom_n >= 20:
            diag["pattern"] = f"dominant_button:{dom_btn}({dom_n}/30)"
    shot = _take_diag_screenshot(
        client, f"stuck_{cur_map[0]}_{cur_map[1]}_{cur_pos[0]}_{cur_pos[1]}",
    )
    if shot is not None:
        diag["screenshot"] = str(shot.relative_to(config.ROOT)).replace(
            "\\", "/",
        )
    return diag


def _act_on_diagnosis(
    client: "io_mod.MGBAClient", diag: dict,
) -> list[str]:
    """Translate diagnosis into concrete button injections.

    Conservative: only fires when pattern strongly suggests a known
    failure mode (menu trap, dialog wall). Never overrides what the
    heuristic is doing if heuristic is currently producing variety.
    """
    actions: list[str] = []
    btn_freq = diag.get("button_freq") or {}
    if not btn_freq:
        return actions
    btns = list(btn_freq)

    if "Start" in btns and btn_freq.get("Start", 0) >= 4:
        try:
            for _ in range(6):
                client.tap("B", frames=12)
                time.sleep(0.3)
            actions.append("INJECT B x6 (menu trap suspected)")
        except (io_mod.EmulatorError, ValueError) as exc:
            actions.append(f"INJECT B failed: {exc!r}")

    if btn_freq.get("A", 0) >= 20 and "Up" not in btn_freq and "Down" not in btn_freq:
        try:
            for _ in range(8):
                client.tap("B", frames=10)
                time.sleep(0.25)
            actions.append("INJECT B x8 (dialog loop without movement)")
        except (io_mod.EmulatorError, ValueError) as exc:
            actions.append(f"INJECT B-loop failed: {exc!r}")

    return actions


def _detect_phase() -> str:
    """Return 'collect' (heur active) / 'train_or_val' (mGBA idle by design) / 'idle'."""
    if not CONTINUOUS_LEDGER.exists():
        return "idle"
    try:
        heur_logs = sorted(
            config.LOG_DIR.glob("continuous_heur_*.log"),
            key=lambda p: p.stat().st_mtime,
        )
        train_logs = sorted(
            config.LOG_DIR.glob("continuous_train_*.log"),
            key=lambda p: p.stat().st_mtime,
        )
        val_logs = sorted(
            config.LOG_DIR.glob("continuous_val_*.log"),
            key=lambda p: p.stat().st_mtime,
        )
        candidates = []
        if heur_logs:
            candidates.append(("collect", heur_logs[-1].stat().st_mtime))
        if train_logs:
            candidates.append(("train_or_val", train_logs[-1].stat().st_mtime))
        if val_logs:
            candidates.append(("train_or_val", val_logs[-1].stat().st_mtime))
        if not candidates:
            return "idle"
        candidates.sort(key=lambda t: t[1], reverse=True)
        return candidates[0][0]
    except OSError:
        return "idle"


def _log(payload: dict) -> None:
    payload = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S"), **payload}
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _demos_count() -> int:
    if not DEMOS_PATH.exists():
        return 0
    with DEMOS_PATH.open("rb") as f:
        return sum(1 for _ in f)


def _read_pos() -> tuple[tuple[int, int], tuple[int, int]] | None:
    try:
        c = io_mod.MGBAClient()
        if not c.ping():
            return None
        g = state_mod.read_state(c)
        if not g.saveblock1_valid:
            return ((0, 0), (0, 0))
        return ((g.map_group, g.map_num), (g.x, g.y))
    except (io_mod.EmulatorError, OSError, ValueError):
        return None


def _continuous_train_alive(
    last_demos: int, last_demos_change_ts: float,
    last_ledger_mtime: float, now: float, phase: str,
) -> bool:
    """Behavioral alive check.

    Instead of PID polling (unreliable across DETACHED_PROCESS spawns),
    declare alive if any of:
      - demonstrations.jsonl grew within last 90 s, OR
      - continuous_train.jsonl was written within last 600 s
        (train + val phases legitimately don't write demos), OR
      - phase is train_or_val (training is real work — alive)
    """
    if phase == "train_or_val":
        return True
    demos_recently = (now - last_demos_change_ts) < 90.0
    ledger_recently = False
    if CONTINUOUS_LEDGER.exists():
        ledger_recently = (now - last_ledger_mtime) < 600.0
    return demos_recently or ledger_recently


def _restart_continuous_train() -> int | None:
    """Spawn continuous_train as a detached subprocess; record PID."""
    log = config.LOG_DIR / f"continuous_auto_{time.strftime('%Y%m%dT%H%M%S')}.log"
    cmd = [
        sys.executable, "-X", "utf8", "-u",
        "-m", "generic_agent.tools.continuous_train",
        "--collect-turns", "800",
        "--epochs", "3",
        "--val-turns", "200",
        "--train-threshold", "600",
        "--force-train-every", "2",
        "--val-timeout", "600",
        "--train-timeout", "900",
        "--collect-timeout", "1800",
    ]
    log.parent.mkdir(parents=True, exist_ok=True)
    out = log.open("w", encoding="utf-8", errors="replace")
    creationflags = 0
    if os.name == "nt":
        creationflags = 0x00000008  # DETACHED_PROCESS
    try:
        p = subprocess.Popen(
            cmd, stdout=out, stderr=subprocess.STDOUT,
            cwd=str(config.ROOT), creationflags=creationflags,
        )
    except (OSError, FileNotFoundError) as exc:
        _log({"event": "restart_failed", "error": repr(exc)})
        return None
    PID_FILE.write_text(str(p.pid), encoding="utf-8")
    return p.pid


def _call_rescue_safely(
    cur_map: tuple[int, int], cur_pos: tuple[int, int],
) -> dict | None:
    """One-shot Haiku rescue call when stuck >rescue_after seconds."""
    try:
        from .. import rescue_brain
        from ..io import MGBAClient
        client = MGBAClient()
        snap_dir = config.DATASET_DIR / "rescue_screens"
        snap_dir.mkdir(parents=True, exist_ok=True)
        shot = snap_dir / f"rescue_{int(time.time())}.png"
        client.screenshot(shot)
        state_summary = (
            f"map=({cur_map[0]},{cur_map[1]}) pos=({cur_pos[0]},{cur_pos[1]})"
        )
        decision = rescue_brain.call_rescue(
            shot, state_summary=state_summary, same_map_streak=0,
        )
        if decision.button:
            for btn in decision.button.split(","):
                try:
                    client.tap(btn.strip(), frames=15)
                    time.sleep(0.3)
                except (OSError, ValueError):
                    break
        cost = (
            decision.input_tokens * 1.0 / 1_000_000
            + decision.output_tokens * 5.0 / 1_000_000
        )
        return {"button": decision.button, "cost": cost}
    except (ImportError, OSError, RuntimeError) as exc:
        return {"button": None, "cost": 0.0, "error": repr(exc)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=300.0,
                        help="seconds between checks (default 300 = 5 min)")
    parser.add_argument("--frozen-after", type=float, default=300.0,
                        help="mGBA frozen this long → restart continuous_train")
    parser.add_argument("--stuck-after", type=float, default=1800.0,
                        help="same map+pos for this long → touch force_explore.flag")
    parser.add_argument("--auto-start-train", action="store_true",
                        help="if continuous_train dead at any check, spawn it")
    parser.add_argument("--use-rescue-api", action="store_true",
                        help="enable Haiku rescue calls when stuck")
    parser.add_argument("--rescue-after", type=float, default=300.0,
                        help="stuck this long → 1 rescue Haiku call (~$0.001)")
    parser.add_argument("--rescue-cooldown", type=float, default=600.0,
                        help="min sec between rescue calls — cost cap")
    parser.add_argument("--self-check-interval", type=float, default=1800.0,
                        help="sec between deep self-check + auto-fix (default 30 min)")
    args = parser.parse_args()

    hist: deque[tuple[float, tuple[int, int], tuple[int, int]]] = deque(maxlen=24)
    last_demos = _demos_count()
    last_demos_change = time.time()
    last_pos_change = time.time()
    last_rescue_call_ts = 0.0
    last_self_check_ts = 0.0
    self_check_interval = args.self_check_interval

    _log({"event": "monitor_start", "args": vars(args), "pid": os.getpid()})

    while True:
        now = time.time()
        st = _read_pos()
        demos = _demos_count()
        ledger_mtime = (
            CONTINUOUS_LEDGER.stat().st_mtime
            if CONTINUOUS_LEDGER.exists() else 0.0
        )
        phase_now = _detect_phase()
        alive = _continuous_train_alive(
            last_demos, last_demos_change, ledger_mtime, now, phase_now,
        )

        if demos != last_demos:
            last_demos = demos
            last_demos_change = now

        autosnap_age = None
        if AUTOSNAP.exists():
            autosnap_age = int(now - AUTOSNAP.stat().st_mtime)

        force_flag_active = FORCE_FLAG.exists()
        action_taken: list[str] = []

        if st is None:
            _log({
                "event": "mgba_dead",
                "alive": alive, "demos": demos,
                "autosnap_age": autosnap_age,
                "action": "skip — cannot reach mGBA",
            })
            time.sleep(args.interval)
            continue

        cur_map, cur_pos = st
        if hist and (cur_map, cur_pos) != (hist[-1][1], hist[-1][2]):
            last_pos_change = now
        hist.append((now, cur_map, cur_pos))

        pos_stuck_for = now - last_pos_change
        demos_stuck_for = now - last_demos_change

        phase = phase_now

        if now - last_self_check_ts >= self_check_interval:
            try:
                state_snap = {
                    "pos": list(cur_pos),
                    "map": list(cur_map),
                    "demos": demos,
                }
                gs_now = state_mod.read_state(io_mod.MGBAClient())
                state_snap["party"] = gs_now.party_count
                state_snap["flags"] = gs_now.total_event_flags
            except (io_mod.EmulatorError, OSError, ValueError):
                state_snap = {
                    "pos": list(cur_pos),
                    "map": list(cur_map),
                    "demos": demos,
                }
            try:
                analysis = self_check_analyze(state_snap)
                _log_self_check({
                    "event": "self_check",
                    "state": state_snap,
                    "analysis": analysis,
                })
                action_taken.append(
                    f"self_check ({len(analysis['issues'])} issues, "
                    f"{len(analysis['actions'])} actions)"
                )
            except (OSError, ValueError, KeyError) as exc:
                _log_self_check({
                    "event": "self_check_failed",
                    "error": repr(exc),
                })
            last_self_check_ts = now

        if not alive and args.auto_start_train:
            pid = _restart_continuous_train()
            action_taken.append(f"restart_continuous_train pid={pid}")

        if (
            phase == "collect"
            and pos_stuck_for > args.frozen_after
        ):
            action_taken.append(
                f"WARN heuristic frozen {int(pos_stuck_for)}s during collect"
            )
            if (
                args.use_rescue_api
                and pos_stuck_for > args.rescue_after
                and (now - last_rescue_call_ts) > args.rescue_cooldown
            ):
                rescue_result = _call_rescue_safely(cur_map, cur_pos)
                if rescue_result:
                    action_taken.append(
                        f"RESCUE Haiku -> {rescue_result['button']} "
                        f"(${rescue_result['cost']:.4f})"
                    )
                    last_rescue_call_ts = now

        if (
            phase == "collect"
            and pos_stuck_for > args.stuck_after
            and not force_flag_active
        ):
            FORCE_FLAG.touch()
            action_taken.append("force_explore.flag TOUCHED (long stuck)")
        elif (
            force_flag_active
            and len(hist) >= 4
            and len({h[1] for h in list(hist)[-4:]}) > 1
        ):
            try:
                FORCE_FLAG.unlink()
                action_taken.append("force_explore.flag CLEARED (new map reached)")
            except OSError:
                pass

        _log({
            "event": "tick",
            "phase": phase,
            "alive": alive,
            "map": list(cur_map),
            "pos": list(cur_pos),
            "demos": demos,
            "pos_stuck_for_s": int(pos_stuck_for),
            "demos_stuck_for_s": int(demos_stuck_for),
            "autosnap_age_s": autosnap_age,
            "force_flag": force_flag_active,
            "action": action_taken or None,
        })

        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
