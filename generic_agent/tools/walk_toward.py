"""Simple walk-toward-target on the current map.

Usage:
  python -m generic_agent.tools.walk_toward TARGET_X TARGET_Y [TARGET_MAP_G TARGET_MAP_N]

Hand-rolled greedy: each step chooses the cardinal direction that reduces
distance to (target_x, target_y). On stuck (same pos 3+ turns) tries the
perpendicular direction. A-spam any dialog/battle detected by HP drop.
Exits when at target or 5-min budget hits.
"""
from __future__ import annotations

import sys
import time

from generic_agent import io as io_mod, state as state_mod


BUDGET_S = 300


def stable_read(c, retries: int = 4):
    g = state_mod.read_state(c)
    for _ in range(retries):
        if g.saveblock1_valid and (g.x, g.y) != (0, 0):
            return g
        time.sleep(0.3)
        g = state_mod.read_state(c)
    return g


def step(c, btn: str, ms: float = 0.4):
    c.tap(btn, frames=18)
    time.sleep(ms)


def fight_spam(c, count: int = 12) -> None:
    for _ in range(count):
        c.tap("A", frames=10)
        time.sleep(0.3)


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: walk_toward TARGET_X TARGET_Y [MAP_G MAP_N]")
        return 1
    tx = int(sys.argv[1])
    ty = int(sys.argv[2])
    tm_g = int(sys.argv[3]) if len(sys.argv) > 3 else None
    tm_n = int(sys.argv[4]) if len(sys.argv) > 4 else None

    c = io_mod.MGBAClient()
    start = time.time()
    g0 = stable_read(c)
    print(f"start: ({g0.x},{g0.y}) m=({g0.map_group},{g0.map_num}) target=({tx},{ty})")
    start_map = (g0.map_group, g0.map_num)

    last_pos = None
    stuck = 0
    stuck_dir_idx = 0
    hp_last = g0.party0_hp

    while time.time() - start < BUDGET_S:
        g = stable_read(c)
        if not g.saveblock1_valid:
            c.tap("A", frames=10); time.sleep(0.3)
            continue

        cur_map = (g.map_group, g.map_num)
        if tm_g is not None and tm_n is not None:
            if cur_map == (tm_g, tm_n):
                print(f"DONE: reached map ({tm_g},{tm_n}) at ({g.x},{g.y})")
                return 0
        if cur_map != start_map and tm_g is None:
            print(f"map changed to {cur_map} at ({g.x},{g.y}) - stopping")
            return 0

        if (g.x, g.y) == (tx, ty):
            print(f"DONE: reached target ({tx},{ty})")
            return 0

        # HP delta -> battle
        if g.party0_hp < hp_last:
            print(f"  HP drop {hp_last}->{g.party0_hp}, A spam")
            fight_spam(c, count=15)
        hp_last = g.party0_hp

        cur_pos = (g.x, g.y)
        if cur_pos == last_pos:
            stuck += 1
        else:
            stuck = 0
            stuck_dir_idx = 0
            print(f"  walk: ({cur_pos[0]},{cur_pos[1]}) hp={g.party0_hp}")
        last_pos = cur_pos

        if stuck >= 3:
            stuck_dir_idx += 1
            alt_dirs = ["Up", "Right", "Down", "Left"]
            d = alt_dirs[stuck_dir_idx % 4]
            print(f"  stuck x{stuck} -> try {d}")
            step(c, d)
            stuck = 0
            # If stuck 3+ times in a row, A spam too (might be dialog)
            if stuck_dir_idx >= 4:
                fight_spam(c, count=10)
            continue

        # Greedy: pick direction reducing manhattan distance
        dx = tx - cur_pos[0]
        dy = ty - cur_pos[1]
        if abs(dx) >= abs(dy):
            d = "Right" if dx > 0 else "Left"
        else:
            d = "Down" if dy > 0 else "Up"
        step(c, d)

    g = stable_read(c)
    print(f"BUDGET EXPIRED at ({g.x},{g.y}) m=({g.map_group},{g.map_num})")
    return 2


if __name__ == "__main__":
    sys.exit(main())
