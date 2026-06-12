"""Phase 3+: Claude-in-conversation as the autonomous expert demonstrator.

Instead of having the API Sonnet/Opus model decide each turn (which has
two problems: it costs per-token, and v34-v50 showed the API Brain
itself gets stuck in repeatable patterns), this module lets THIS Claude
Code conversation act as the expert.

Per turn the human-facing Claude reads the latest screenshot, applies
its Pokemon Emerald domain knowledge, and supplies a button. The button
is logged together with the screenshot + 28-d RAM state vector in the
same `demonstrations.jsonl` format consumed by `train_imitation.py`.

Wrapper interface (one call per game turn):
    poke-rl/Scripts/python.exe -m generic_agent.claude_play step --button Up
    poke-rl/Scripts/python.exe -m generic_agent.claude_play snapshot
        → writes the current screenshot to dataset/screens/<session_id>/
        and prints the path + RAM state JSON. Use this to know what to
        decide before calling `step`.

State file `dataset/claude_play_state.json` carries the session id,
turn counter, and last screenshot path so a Claude conversation can
resume between tool calls without globals.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import config, memory, state as state_mod, tile_map as tile_map_mod
from .io import EmulatorError, MGBAClient

STATE_FILE = config.DATASET_DIR / "claude_play_state.json"
VALID_BUTTONS = {"Up", "Down", "Left", "Right", "A", "B", "Start", "Select"}


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "session_id": time.strftime("%Y%m%dT%H%M%S"),
        "turn": 0,
        "last_screenshot": None,
        "last_state": None,
    }


def _save_state(s: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")


def _ram_state_dict(client: MGBAClient, tm: tile_map_mod.TileMap) -> dict:
    gs = state_mod.read_state(client)
    out: dict[str, object] = {
        "map": [gs.map_group, gs.map_num] if gs.saveblock1_valid else None,
        "pos": [gs.x, gs.y] if gs.saveblock1_valid else None,
        "in_battle": bool(gs.in_battle),
        "is_trainer": bool(gs.is_trainer_battle),
        "battle_flags": int(gs.battle_flags),
        "saveblock1_valid": bool(gs.saveblock1_valid),
    }
    if gs.saveblock1_valid:
        mk = tm._map_key(gs.map_group, gs.map_num)
        rec = tm._store.get(mk, {}).get(tm._tile_key(gs.x, gs.y))
        if rec is not None:
            out["blocked_here"] = list(rec.blocked)
            out["tile_visits"] = int(rec.visits)
        if not gs.in_battle:
            out["bfs_first"] = tm.bfs_frontier_direction(
                gs.map_group, gs.map_num, gs.x, gs.y, prefer="nearest",
            )
            out["bfs_first_farthest"] = tm.bfs_frontier_direction(
                gs.map_group, gs.map_num, gs.x, gs.y, prefer="farthest",
            )
            out["tile_summary"] = tm.summary_for(
                gs.map_group, gs.map_num, gs.x, gs.y,
            )
    return out


def cmd_snapshot(client: MGBAClient, tm: tile_map_mod.TileMap) -> int:
    s = _load_state()
    s["turn"] += 1
    sess_dir = config.DATASET_DIR / "screens" / s["session_id"]
    sess_dir.mkdir(parents=True, exist_ok=True)
    shot = sess_dir / f"t{s['turn']:05d}.png"
    client.screenshot(shot)
    time.sleep(0.15)

    ram = _ram_state_dict(client, tm)
    s["last_screenshot"] = str(shot.relative_to(config.ROOT)).replace("\\", "/")
    s["last_state"] = ram
    _save_state(s)

    print(json.dumps({
        "session_id": s["session_id"],
        "turn": s["turn"],
        "screenshot": s["last_screenshot"],
        "ram": ram,
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_step(
    client: MGBAClient,
    tm: tile_map_mod.TileMap,
    button: str,
    frames: int,
) -> int:
    if button not in VALID_BUTTONS:
        print(f"[err] unknown button '{button}'. Valid: {sorted(VALID_BUTTONS)}")
        return 1
    s = _load_state()
    if s.get("last_screenshot") is None:
        print(
            "[err] no snapshot recorded yet. "
            "Run `claude_play snapshot` first."
        )
        return 1

    rel_shot = s["last_screenshot"]
    rec_state = s.get("last_state") or {}

    record = {
        "session_id": s["session_id"],
        "turn": s["turn"],
        "screenshot": rel_shot,
        "button": button,
        "source": "claude_play",
        "fhash": None,
        "map": rec_state.get("map"),
        "pos": rec_state.get("pos"),
        "in_battle": bool(rec_state.get("in_battle", False)),
        "is_trainer": bool(rec_state.get("is_trainer", False)),
        "blocked_here": rec_state.get("blocked_here", []),
        "bfs_first": rec_state.get("bfs_first"),
        "suppress_dir": None,
        "oscillating": False,
        "same_pos_streak": 0,
        "same_map_streak": 0,
        "consecutive_dialog": 0,
        "map_visit_count": 0,
        "goal_direction": None,
    }
    memory.append_to_path(config.DATASET_INDEX, record)

    try:
        client.tap(button, frames=frames)
    except (EmulatorError, ValueError) as exc:
        print(f"[err] failed to send button: {exc}")
        return 1
    time.sleep(max(0.05, frames / 60.0 + 0.05))

    s["turn"] += 1
    sess_dir = config.DATASET_DIR / "screens" / s["session_id"]
    sess_dir.mkdir(parents=True, exist_ok=True)
    new_shot = sess_dir / f"t{s['turn']:05d}.png"
    client.screenshot(new_shot)
    time.sleep(0.15)

    ram_after = _ram_state_dict(client, tm)
    s["last_screenshot"] = str(new_shot.relative_to(config.ROOT)).replace("\\", "/")
    s["last_state"] = ram_after
    _save_state(s)

    print(json.dumps({
        "session_id": s["session_id"],
        "turn_pressed": record["turn"],
        "button_pressed": button,
        "next_turn": s["turn"],
        "next_screenshot": s["last_screenshot"],
        "next_ram": ram_after,
        "demos_total": _count_demos(),
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_status() -> int:
    s = _load_state()
    print(json.dumps({
        "session_id": s["session_id"],
        "turn": s["turn"],
        "last_screenshot": s.get("last_screenshot"),
        "demos_total": _count_demos(),
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_reset() -> int:
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    print("[reset] cleared claude_play state — next snapshot starts a new session")
    return 0


def _count_demos() -> int:
    if not config.DATASET_INDEX.exists():
        return 0
    return sum(1 for _ in config.DATASET_INDEX.open(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot", help="capture current frame + RAM state")
    p_step = sub.add_parser("step", help="press one button, log a demo")
    p_step.add_argument("--button", required=True)
    p_step.add_argument("--frames", type=int, default=15)
    sub.add_parser("status", help="show current session info")
    sub.add_parser("reset", help="start a new session id")
    args = parser.parse_args()

    if args.cmd == "reset":
        return cmd_reset()
    if args.cmd == "status":
        return cmd_status()

    config.ensure_runtime_dirs()
    client = MGBAClient()
    if not client.ping():
        print("[FAIL] mGBA port 8895 unreachable. See STARTUP.md")
        return 1
    tm = tile_map_mod.TileMap()

    if args.cmd == "snapshot":
        return cmd_snapshot(client, tm)
    if args.cmd == "step":
        return cmd_step(client, tm, args.button, args.frames)

    parser.error("unknown command")
    return 1


if __name__ == "__main__":
    sys.exit(main())
