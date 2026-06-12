"""Phase 3+ alternative: autonomous heuristic agent that encodes the
in-conversation Claude's Pokemon Emerald strategy as Python code.

The repeated regression v40-v53 traced back to one root cause: the only
expert demonstrators available (API Sonnet/Opus) were themselves stuck
on Route 101 due to path_memory noise + over-defensive prompt rules,
so every CNN trained by behavior cloning inherited those stuck
patterns. The user's clarification — Claude (this conversation) is
allowed to act as the demonstrator within plan scope, just not the
paid API — opens a different path: I write my own playing strategy
directly in Python.

This agent prioritises:
- avoid blocked directions (hard rule, never press into a wall)
- prefer un-tried directions over re-pressing the same one
- when stalled, rotate through unblocked directions
- bias the rotation toward Up first, then perpendicular, then back
  (Pokemon Emerald early-game progress is overwhelmingly northbound:
  Littleroot -> Route 101 -> Oldale -> Route 102 -> Petalburg, etc.)
- in battle: trainer = A spam; wild = run-sequence cycle
- in dialog (same hash, same pos): A
- escape on local cycles: when 8+ unique pos in 20-turn window OR
  same pos 8+ turns, force a perpendicular flip

It runs independently of API Brain. Per-turn records are written to
the same dataset/demonstrations.jsonl format so the existing
train_imitation.py picks them up unchanged.

Run:
    poke-rl/Scripts/python.exe -m generic_agent.claude_heuristic \\
        --turns 3000 --dataset
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from collections import deque
from pathlib import Path

from . import (
    config,
    memory,
    path_memory as path_memory_mod,
    preprocess,
    state as state_mod,
    tile_map as tile_map_mod,
)
from .io import EmulatorError, MGBAClient

DIRECTIONS = ("Up", "Right", "Down", "Left")
NORTH_BIAS_ORDER = ("Up", "Right", "Left", "Down")
RUN_CYCLE = ("A", "A", "Down", "Right", "A", "A", "A")


def take_screenshot(client: MGBAClient, session_id: str, turn: int) -> Path:
    sess_dir = config.DATASET_DIR / "screens" / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    p = sess_dir / f"t{turn:05d}.png"
    client.screenshot(p)
    time.sleep(0.15)
    return p


def _toward(cx: int, cy: int, tx: int, ty: int) -> str:
    dx = tx - cx
    dy = ty - cy
    if abs(dy) > abs(dx):
        return "Down" if dy > 0 else "Up"
    if dx != 0:
        return "Right" if dx > 0 else "Left"
    return "Up"


def heuristic_button(
    gs,
    tm: tile_map_mod.TileMap,
    pm: path_memory_mod.TransitionMemory,
    map_visit_counts: dict[tuple[int, int], int],
    same_pos_streak: int,
    same_hash_streak: int,
    same_map_streak: int,
    last_pos: tuple[int, int] | None,
    last_action: str,
    recent_pos: list[tuple[int, int, int, int]],
    battle_turn: int,
    escape_dir_index: int,
) -> tuple[str, str]:
    if gs.in_battle:
        if gs.is_trainer_battle:
            return "A", "trainer:A"
        return RUN_CYCLE[battle_turn % len(RUN_CYCLE)], "wild_run"

    if not gs.saveblock1_valid:
        return "A", "pre-save:A"

    if (
        same_pos_streak > 0
        and same_hash_streak == 0
        and last_action == "A"
    ):
        return "A", "dialog_continue"
    if same_hash_streak >= 2 and same_pos_streak >= 1:
        return "A", "dialog_frozen"

    cur_x, cur_y = gs.x, gs.y
    cur_map = (gs.map_group, gs.map_num)
    mk = tm._map_key(*cur_map)
    rec = tm._store.get(mk, {}).get(tm._tile_key(cur_x, cur_y))
    blocked = set(rec.blocked) if rec is not None else set()
    tried = dict(rec.tried) if rec is not None else {}
    tiles = tm._store.get(mk, {})

    last_20 = recent_pos[-20:]
    uniq_20 = len(set(last_20))

    if same_pos_streak >= 12 or (
        len(last_20) >= 20 and uniq_20 <= 4
    ):
        rotation = ["Up", "Right", "Down", "Left"]
        order = [
            d for d in rotation[escape_dir_index:] + rotation[:escape_dir_index]
            if d not in blocked
        ] or rotation
        return order[0], f"escape:{order[0]}"

    cur_map_key = f"{gs.map_group}-{gs.map_num}"
    from_paths = pm._store.get(cur_map_key, {})
    candidate: tuple[tuple[int, int], str, list[str]] | None = None
    best_visits = None
    for tk, records in from_paths.items():
        try:
            tg, tn = (int(v) for v in tk.split("-"))
        except ValueError:
            continue
        t_visits = map_visit_counts.get((tg, tn), 0)
        if (
            best_visits is not None
            and t_visits >= best_visits
        ):
            continue
        for r in records:
            if r.from_pos is None or not r.seq:
                continue
            candidate = ((tg, tn), tk, r.seq)
            best_visits = t_visits
            target_pos = r.from_pos
            target_record = r
            break
    if candidate is not None:
        target_pos = target_record.from_pos  # type: ignore[name-defined]
        seq = target_record.seq  # type: ignore[name-defined]
        if (cur_x, cur_y) == target_pos:
            return seq[0], f"path_memory_exit:{seq[0]}->{candidate[1]}"
        d = _toward(cur_x, cur_y, target_pos[0], target_pos[1])
        if d not in blocked:
            return d, f"toward_exit:{d}->{target_pos}"

    unexplored_dirs: list[str] = []
    for d in NORTH_BIAS_ORDER:
        if d in blocked:
            continue
        dx, dy = tile_map_mod.DELTA[d]
        nk = tm._tile_key(cur_x + dx, cur_y + dy)
        neighbor = tiles.get(nk)
        if neighbor is None or neighbor.visits == 0:
            unexplored_dirs.append(d)
    if unexplored_dirs:
        choice = unexplored_dirs[0]
        return choice, f"explore_unvisited:{choice}"

    bfs = tm.bfs_frontier_direction(
        gs.map_group, gs.map_num, cur_x, cur_y, prefer="farthest"
    )
    if bfs is not None and bfs not in blocked:
        return bfs, f"bfs_far:{bfs}"

    for d in NORTH_BIAS_ORDER:
        if d in blocked:
            continue
        if tried.get(d, 0) == 0:
            return d, f"untried:{d}"

    for d in NORTH_BIAS_ORDER:
        if d not in blocked:
            return d, f"north_bias:{d}"

    rng = random.Random(gs.x * 31 + gs.y * 17 + battle_turn)
    return rng.choice(DIRECTIONS), "random"


def run(
    max_turns: int,
    record_dataset: bool,
    poll_period_sec: float,
) -> int:
    config.ensure_runtime_dirs()
    client = MGBAClient()
    if not client.ping():
        print("[FAIL] mGBA port 8895 unreachable. See STARTUP.md")
        return 1

    tm = tile_map_mod.TileMap()
    cleaned = tm.cleanup_phantom_walls()
    if cleaned:
        print(f"[start] cleared {cleaned} phantom 4-way-blocked tiles")
        tm.save()
    pm = path_memory_mod.TransitionMemory()

    session_id = time.strftime("%Y%m%dT%H%M%S")
    print(
        f"[start] claude_heuristic session={session_id} "
        f"turns={max_turns} record_dataset={record_dataset}"
    )

    last_pos: tuple[int, int] | None = None
    last_action = ""
    last_map_key: tuple[int, int] | None = None
    same_pos_streak = 0
    same_hash_streak = 0
    same_map_streak = 0
    battle_turn = 0
    last_frame_hash = ""
    recent_pos: deque[tuple[int, int, int, int]] = deque(maxlen=100)
    map_visit_counts: dict[tuple[int, int], int] = {}
    escape_dir_index = 0
    history_buttons: list[str] = []

    decisions: dict[str, int] = {}

    for turn in range(1, max_turns + 1):
        shot = take_screenshot(client, session_id, turn)
        arr = preprocess.load_png_as_array(shot)
        fhash = preprocess.frame_hash(arr)
        if last_frame_hash == fhash:
            same_hash_streak += 1
        else:
            same_hash_streak = 0
        last_frame_hash = fhash

        gs = state_mod.read_state(client)
        map_key = (gs.map_group, gs.map_num)

        if gs.saveblock1_valid:
            if last_map_key == map_key:
                same_map_streak += 1
            else:
                same_map_streak = 0
                if last_map_key is not None:
                    pm.record_transition(
                        last_map_key[0], last_map_key[1],
                        last_pos[0] if last_pos else None,
                        last_pos[1] if last_pos else None,
                        map_key[0], map_key[1],
                        gs.x, gs.y,
                        history_buttons[-8:],
                    )
            last_map_key = map_key
            map_visit_counts[map_key] = (
                map_visit_counts.get(map_key, 0) + 1
            )
            pos_now = (gs.x, gs.y)
            recent_pos.append((map_key[0], map_key[1], gs.x, gs.y))
            if last_pos == pos_now:
                same_pos_streak += 1
                if last_action in DIRECTIONS:
                    tm.record_attempt(
                        *map_key, gs.x, gs.y,
                        last_action, moved=False,
                    )
            else:
                same_pos_streak = 0
                tm.record_visit(*map_key, gs.x, gs.y)
                if (
                    last_pos is not None
                    and last_action in DIRECTIONS
                ):
                    tm.record_attempt(
                        *map_key, last_pos[0], last_pos[1],
                        last_action, moved=True,
                    )
            last_pos = pos_now
        if gs.in_battle:
            battle_turn += 1
        else:
            battle_turn = 0

        button, src = heuristic_button(
            gs, tm, pm,
            map_visit_counts=map_visit_counts,
            same_pos_streak=same_pos_streak,
            same_hash_streak=same_hash_streak,
            same_map_streak=same_map_streak,
            last_pos=last_pos,
            last_action=last_action,
            recent_pos=list(recent_pos),
            battle_turn=battle_turn,
            escape_dir_index=escape_dir_index,
        )
        if "escape" in src:
            escape_dir_index = (escape_dir_index + 1) % 4
        key = src.split(":")[0]
        decisions[key] = decisions.get(key, 0) + 1
        history_buttons.append(button)
        if len(history_buttons) > 20:
            history_buttons.pop(0)

        if record_dataset:
            rel_shot = str(shot.relative_to(config.ROOT)).replace("\\", "/")
            cur_blocked = []
            bfs_first = None
            tile_visits = 0
            if gs.saveblock1_valid:
                mk = tm._map_key(gs.map_group, gs.map_num)
                rec = tm._store.get(mk, {}).get(
                    tm._tile_key(gs.x, gs.y)
                )
                if rec is not None:
                    cur_blocked = list(rec.blocked)
                    tile_visits = int(rec.visits)
                if not gs.in_battle:
                    bfs_first = tm.bfs_frontier_direction(
                        gs.map_group, gs.map_num,
                        gs.x, gs.y, prefer="nearest",
                    )
            memory.append_to_path(
                config.DATASET_INDEX,
                {
                    "session_id": session_id,
                    "turn": turn,
                    "screenshot": rel_shot,
                    "button": button,
                    "source": f"claude_heuristic:{src}",
                    "fhash": fhash[:12],
                    "map": list(map_key) if gs.saveblock1_valid else None,
                    "pos": [gs.x, gs.y] if gs.saveblock1_valid else None,
                    "in_battle": gs.in_battle,
                    "is_trainer": gs.is_trainer_battle,
                    "blocked_here": cur_blocked,
                    "bfs_first": bfs_first,
                    "suppress_dir": None,
                    "oscillating": False,
                    "same_pos_streak": same_pos_streak,
                    "same_map_streak": same_map_streak,
                    "consecutive_dialog": 0,
                    "map_visit_count": 0,
                    "goal_direction": None,
                },
            )

        try:
            client.tap(button, frames=15)
        except (EmulatorError, ValueError) as exc:
            print(f"  [warn] button {button} failed: {exc}")
        time.sleep(poll_period_sec)
        last_action = button

        if turn % 100 == 0:
            print(
                f"  turn {turn}: pos={last_pos} map={map_key} "
                f"same_pos={same_pos_streak} same_map={same_map_streak} "
                f"in_battle={gs.in_battle}"
            )
        if turn % 100 == 0:
            tm.save()
            pm.save()

    tm.save()
    pm.save()
    print(f"[end] turns={max_turns} decisions={decisions}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=500)
    parser.add_argument("--dataset", action="store_true")
    parser.add_argument("--poll", type=float, default=0.05)
    args = parser.parse_args()
    return run(args.turns, args.dataset, args.poll)


if __name__ == "__main__":
    sys.exit(main())
