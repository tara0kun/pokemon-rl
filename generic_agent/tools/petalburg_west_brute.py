"""Brute-force west traversal: walk Left until Route 104 (0,19).

Key insight from earlier failures: the chronic stuck wasn't really a
navigation problem - it was the combination of (a) Gym sign / NPC
dialog triggers and (b) low-HP/PP wild encounters whiteout-warping
the agent back to PC. With FULL HP+PP and Lv 20 Grovyle, both are
easy:
- POUND 1-shots every Route 104-tier wild
- A-spam advances any dialog
- Wild encounter loss per battle: ~5-8 HP. 56 HP buffer = ~10 battles
  worth of margin
- Petalburg west traversal = ~25 tiles, ~3-5 expected encounters

So we just press Left + A spam on stuck.

Exits when map changes to Route 104 (0,19) or HP drops below 12 (one
hit from whiteout).
"""
from __future__ import annotations

import sys
import time

from generic_agent import io as io_mod, state as state_mod


PETALBURG = (0, 0)
ROUTE_104 = (0, 19)
BUDGET_S = 600


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


def tap(c, btn: str, ms: float = 0.25):
    c.tap(btn, frames=10)
    time.sleep(ms)


def fight_spam(c, count: int = 15) -> None:
    """A x N to advance any dialog / FIGHT-POUND a wild battle."""
    for _ in range(count):
        tap(c, "A", ms=0.3)


def main() -> int:
    c = io_mod.MGBAClient()
    start = time.time()

    g0 = stable_read(c)
    print(f"start: pos=({g0.x},{g0.y}) m=({g0.map_group},{g0.map_num}) "
          f"hp={g0.party0_hp}/{g0.party0_max_hp} lv={g0.party0_level}")

    if (g0.map_group, g0.map_num) != PETALBURG:
        print(f"[err] not in Petalburg")
        return 1

    last_pos = None
    stuck = 0
    hp_last = g0.party0_hp
    moves_total = 0

    while time.time() - start < BUDGET_S:
        g = stable_read(c)
        if not g.saveblock1_valid:
            tap(c, "A")
            continue

        cur_map = (g.map_group, g.map_num)
        if cur_map == ROUTE_104:
            print(f"SUCCESS: reached Route 104 at ({g.x},{g.y})!")
            print(f"total moves: {moves_total}, time: {time.time()-start:.0f}s")
            return 0
        if cur_map != PETALBURG:
            print(f"[warn] left Petalburg to {cur_map} at ({g.x},{g.y})")
            # If we entered a building, exit south
            for _ in range(8):
                step(c, "Down")
                g = stable_read(c)
                if (g.map_group, g.map_num) == PETALBURG:
                    print(f"  re-exited to Petalburg at ({g.x},{g.y})")
                    break
            continue

        hp_now = g.party0_hp
        if hp_now < hp_last:
            print(f"  HP dropped {hp_last}->{hp_now} (battle resolved)")
        hp_last = hp_now

        # HP critical guard
        if hp_now < 12 and g.party0_max_hp > 0:
            print(f"  HP critical {hp_now}/{g.party0_max_hp} - abort west, return to PC")
            return 3

        cur_pos = (g.x, g.y)
        if cur_pos == last_pos:
            stuck += 1
        else:
            stuck = 0
            print(f"  walk: ({cur_pos[0]},{cur_pos[1]}) hp={hp_now}")
        last_pos = cur_pos

        if stuck >= 3:
            print(f"  stuck x{stuck} -> A spam")
            fight_spam(c, count=12)
            stuck = 0
            continue

        step(c, "Left", ms=0.5)
        moves_total += 1

    g = stable_read(c)
    print(f"BUDGET EXPIRED at ({g.x},{g.y}) m=({g.map_group},{g.map_num}) "
          f"hp={g.party0_hp}/{g.party0_max_hp} moves={moves_total}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
