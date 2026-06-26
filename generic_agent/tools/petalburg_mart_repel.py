"""Narrow escort: visit Petalburg Mart, buy 1 Repel, use it.

Mart door warp at (25,12); standing tile (25,13). Sequence:
1. BFS from current Petalburg pos to (25,13).
2. Up onto (25,12) -> warp inside Mart.
3. Inside Mart: walk Up to clerk, A to open menu.
4. Buy menu: pick REPEL (need to scroll to find it - down arrows),
   set qty=1, confirm.
5. Exit Mart south.
6. Open START menu -> BAG -> Items -> select REPEL -> USE.

Repel: 100 steps of no wild encounters (player Lv >= wild Lv).
"""
from __future__ import annotations

import sys
import time

from generic_agent import io as io_mod, map_data, state as state_mod


BUDGET_S = 300
PETALBURG = (0, 0)
MART_DOOR_SOUTH = (25, 13)


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


def tap(c, btn: str, ms: float = 0.3):
    c.tap(btn, frames=10)
    time.sleep(ms)


def main() -> int:
    c = io_mod.MGBAClient()
    mc = map_data.get_cache()
    start = time.time()

    g0 = stable_read(c)
    print(f"start: pos=({g0.x},{g0.y}) m=({g0.map_group},{g0.map_num})")

    if (g0.map_group, g0.map_num) != PETALBURG:
        print(f"[err] not on Petalburg")
        return 1

    # PHASE 1: walk to Mart door south
    print("PHASE 1: walk to Mart")
    last_pos = None
    stuck = 0
    while time.time() - start < BUDGET_S:
        g = stable_read(c)
        if not g.saveblock1_valid:
            tap(c, "A"); continue
        cur_pos = (g.x, g.y)
        cur_map = (g.map_group, g.map_num)
        if cur_map != PETALBURG:
            print(f"  inside building: m={cur_map} pos={cur_pos}")
            break
        if cur_pos == MART_DOOR_SOUTH:
            print(f"  at door-south, step Up to warp")
            step(c, "Up")
            time.sleep(1.0)
            continue
        if cur_pos == last_pos:
            stuck += 1
        else:
            stuck = 0
        last_pos = cur_pos

        if stuck >= 4:
            # Likely hidden wild encounter; spam B then continue
            for _ in range(10):
                tap(c, "B")
            stuck = 0
            continue

        npc_tiles = {(nx, ny) for (nx, ny, _gid) in g.npcs_on_map}
        path = mc.bfs_to_tile(
            g.map_group, g.map_num, cur_pos, {MART_DOOR_SOUTH},
            blocked_tiles=npc_tiles,
        )
        if path is None:
            path = mc.bfs_to_tile(
                g.map_group, g.map_num, cur_pos, {MART_DOOR_SOUTH}
            )
        if not path:
            print(f"[stuck] no BFS to Mart from {cur_pos}")
            step(c, "Right")
            continue
        step(c, path[0])

    # PHASE 2: inside Mart, walk up to clerk
    print("PHASE 2: inside Mart, walk Up to clerk")
    for _ in range(8):
        step(c, "Up")
    # Talk to clerk: in Emerald Pokemon Mart the clerk dialog is
    # "Hi!" -> A -> menu (BUY/SELL/SEE YA). Two A's usually reach the
    # menu; we do three to be safe (a third A on BUY selects it).
    for _ in range(3):
        tap(c, "A", ms=0.5)

    # PHASE 3: buy menu (item list now showing)
    print("PHASE 3: scroll to REPEL, buy 1")
    # Petalburg Mart sells 7 items in order:
    #   0 POKE BALL (200)
    #   1 POTION (300)
    #   2 ANTIDOTE (100)
    #   3 PARLYZ HEAL (200)
    #   4 AWAKENING (250)
    #   5 ESCAPE ROPE (550)
    #   6 REPEL (350)
    # Cursor starts at index 0; Down x6 -> REPEL.
    for _ in range(6):
        tap(c, "Down", ms=0.25)
    tap(c, "A", ms=0.4)  # select REPEL
    # Qty prompt: default 1, A confirms
    tap(c, "A", ms=0.4)
    # "Money X yen" confirm prompt
    for _ in range(4):
        tap(c, "A", ms=0.4)
    # Close menu
    for _ in range(5):
        tap(c, "B", ms=0.3)

    g1 = stable_read(c)
    print(f"after buy: pos=({g1.x},{g1.y}) m=({g1.map_group},{g1.map_num})")

    # PHASE 4: exit Mart south
    print("PHASE 4: exit Mart")
    for _ in range(10):
        step(c, "Down")
        g = stable_read(c)
        if (g.map_group, g.map_num) == PETALBURG:
            print(f"  exited to Petalburg at ({g.x},{g.y})")
            break

    # PHASE 5: open START menu, BAG, Items, find REPEL, USE
    print("PHASE 5: use REPEL via menu")
    tap(c, "Start", ms=0.5)
    time.sleep(0.5)
    # Menu order in Emerald (when no Pokedex yet hands a different order):
    #   POKEMON / BAG / POKENAV / PLAYER / SAVE / OPTION / EXIT
    # BAG is second. Down once + A.
    tap(c, "Down", ms=0.3)
    tap(c, "A", ms=0.5)  # open BAG
    # Bag opens to ITEMS pocket usually.
    # Scroll to REPEL - was last bought, near end of list. Down many.
    for _ in range(10):
        tap(c, "Down", ms=0.18)
    tap(c, "A", ms=0.4)  # select item
    # Item menu: USE / GIVE / TOSS / CANCEL
    tap(c, "A", ms=0.5)  # USE
    # Confirmation
    for _ in range(4):
        tap(c, "A", ms=0.3)
    for _ in range(4):
        tap(c, "B", ms=0.3)

    g_final = stable_read(c)
    print(f"DONE: pos=({g_final.x},{g_final.y}) m=({g_final.map_group},{g_final.map_num}) "
          f"hp={g_final.party0_hp}/{g_final.party0_max_hp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
