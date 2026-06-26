"""Narrow escort: agent on Route 102 → Petalburg City west edge.

Single-purpose tool, modeled after the successful mart_visit and
rival_escort patterns. Only handles ONE thing:
- agent already on Route 102 (0, 17)
- walk west to (0, 6-9) edge → cross boundary → Petalburg (0, 0)

Stops when agent is on Petalburg or 7-min budget elapses.

Wild battle handling: at Lv 14+, FIGHT spam (1-shots all Route 102 wilds).
Trainer Calvin (33,14) and others: same FIGHT spam.

Safe: only button taps. No save state mutation.
"""
from __future__ import annotations

import sys
import time

from generic_agent import io as io_mod, map_data, state as state_mod


BUDGET_S = 420
ROUTE_102 = (0, 17)
PETALBURG = (0, 0)


def stable_read(c, retries: int = 4):
    g = state_mod.read_state(c)
    for _ in range(retries):
        if g.saveblock1_valid and (g.x, g.y) != (0, 0):
            return g
        time.sleep(0.3)
        g = state_mod.read_state(c)
    return g


def step(c, btn: str, post_ms: float = 0.35):
    c.tap(btn, frames=18)
    time.sleep(post_ms)
    return stable_read(c)


def fight_clear(c, rounds: int = 6) -> None:
    """A-spam — FIGHT default cursor + Treecko Lv 14+ vs wild Lv 4-7
    one-shots every encounter."""
    for _ in range(rounds):
        for _ in range(12):
            c.tap("A", frames=8)
            time.sleep(0.18)
        g = stable_read(c)
        if g.saveblock1_valid and (g.x, g.y) != (0, 0):
            return


def run_from_battle(c, rounds: int = 5) -> None:
    """Force RUN: reset cursor to FIGHT via B, then Right+Down+A.

    Handles both:
    - Active wild battle (cursor anywhere on the 2x2 menu)
    - Stuck on a 'no PP' / 'X used Y!' dialog (B advances)
    """
    for _ in range(rounds):
        # Cancel any submenu to return to the main battle menu.
        for _ in range(3):
            c.tap("B", frames=8)
            time.sleep(0.15)
        # Cursor home → FIGHT (top-left). Right=BAG, Down=RUN.
        c.tap("Right", frames=10)
        time.sleep(0.18)
        c.tap("Down", frames=10)
        time.sleep(0.18)
        c.tap("A", frames=12)
        time.sleep(0.4)
        # Spam A to clear the "got away" text.
        for _ in range(8):
            c.tap("A", frames=8)
            time.sleep(0.18)
        g = stable_read(c)
        if g.saveblock1_valid and (g.x, g.y) != (0, 0):
            return


def main() -> int:
    c = io_mod.MGBAClient()
    mc = map_data.get_cache()
    start = time.time()

    g0 = stable_read(c)
    print(f"start: pos=({g0.x},{g0.y}) map=({g0.map_group},{g0.map_num}) "
          f"lv={g0.party0_level} hp={g0.party0_hp}/{g0.party0_max_hp}")

    if (g0.map_group, g0.map_num) != ROUTE_102:
        print(f"[err] not on Route 102, current map=({g0.map_group},{g0.map_num})")
        return 1

    info = mc.get(*ROUTE_102)
    west_edge_targets = {
        (0, y) for y in range(info.height) if info.walkable(0, y)
    }
    print(f"west edge targets: {sorted(west_edge_targets)}")

    last_pos = (g0.x, g0.y)
    stuck = 0

    while time.time() - start < BUDGET_S:
        g = stable_read(c)
        if not g.saveblock1_valid:
            c.tap("A", frames=10)
            time.sleep(0.3)
            continue

        cur_map = (g.map_group, g.map_num)
        if cur_map == PETALBURG:
            print(f"DONE: entered Petalburg at ({g.x},{g.y})")
            return 0
        if cur_map != ROUTE_102:
            print(f"[warn] left Route 102 to {cur_map} at ({g.x},{g.y})")
            return 2

        # Handle visible battle / dialog
        if g.in_battle:
            print(f"  battle: pos=({g.x},{g.y}) → fight_clear")
            run_from_battle(c, rounds=4)
            stuck = 0
            continue

        cur_pos = (g.x, g.y)
        if cur_pos == last_pos:
            stuck += 1
        else:
            stuck = 0
            print(f"  moved: ({cur_pos[0]},{cur_pos[1]})")
        last_pos = cur_pos

        if stuck >= 5:
            # Likely hidden wild battle (RAM false neg). FIGHT.
            print(f"  stuck x{stuck} → run_from_battle")
            run_from_battle(c, rounds=3)
            stuck = 0
            continue

        # BFS west, blocking NPCs (Calvin etc) from the path
        npc_tiles = {(nx, ny) for (nx, ny, _gid) in g.npcs_on_map}
        path = mc.bfs_to_tile(
            g.map_group, g.map_num, cur_pos, west_edge_targets,
            blocked_tiles=npc_tiles,
        )
        if path is None:
            path = mc.bfs_to_tile(
                g.map_group, g.map_num, cur_pos, west_edge_targets
            )
        if path is None:
            print(f"[stuck] BFS NULL from {cur_pos}, try Left")
            step(c, "Left")
            continue
        if not path:
            # On west edge tile — step Left to trigger boundary warp
            print(f"  on west edge {cur_pos}, step Left to warp")
            step(c, "Left")
            continue

        next_btn = path[0]
        step(c, next_btn)

    g = stable_read(c)
    print(f"BUDGET EXPIRED at pos=({g.x},{g.y}) m=({g.map_group},{g.map_num})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
