"""Narrow escort: heal at Petalburg Pokemon Center.

Self-contained PC visit:
1. From anywhere in Petalburg (0,0), BFS to door-south-adjacent (20,17).
2. Press Up to step onto door (20,16) -> warp inside PC.
3. Inside PC, walk Up to nurse counter, press A 30x to heal.
4. Exit south, verify HP restored.

Door coords are derived from canon (door tile at 20,16 is NOT walkable;
the standing tile is one south at 20,17). The previous escort tried
BFS to (20,16) directly which always returned None.
"""
from __future__ import annotations

import sys
import time

from generic_agent import io as io_mod, map_data, state as state_mod


BUDGET_S = 240
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


def run_from_battle(c, rounds: int = 3) -> None:
    for _ in range(rounds):
        for _ in range(3):
            c.tap("B", frames=8); time.sleep(0.15)
        c.tap("Right", frames=10); time.sleep(0.18)
        c.tap("Down", frames=10); time.sleep(0.18)
        c.tap("A", frames=12); time.sleep(0.4)
        for _ in range(8):
            c.tap("A", frames=8); time.sleep(0.15)
        g = stable_read(c)
        if g.saveblock1_valid and (g.x, g.y) != (0, 0):
            return


def find_door_target(mc) -> tuple[int, int]:
    """Locate the Pokemon Center door's south-adjacent walkable tile."""
    info = mc.get(*PETALBURG)
    for w in info.warps:
        if "PokemonCenter" not in (w.get("dest_map") or ""):
            continue
        wx, wy = int(w["x"]), int(w["y"])
        # Doors trigger when you walk onto the door tile from the south.
        # The standing tile is (wx, wy+1) -> Up steps onto (wx, wy).
        cand = (wx, wy + 1)
        if info.walkable(*cand):
            return cand
    return None


def main() -> int:
    c = io_mod.MGBAClient()
    mc = map_data.get_cache()
    start = time.time()

    g0 = stable_read(c)
    print(f"start: pos=({g0.x},{g0.y}) map=({g0.map_group},{g0.map_num}) "
          f"hp={g0.party0_hp}/{g0.party0_max_hp}")

    if (g0.map_group, g0.map_num) != PETALBURG:
        print(f"[err] not on Petalburg, current map={g0.map_group},{g0.map_num}")
        return 1

    hp_before = g0.party0_hp

    door_south = find_door_target(mc)
    if not door_south:
        print("[err] no Petalburg PC door warp found")
        return 1
    print(f"PC door south-adjacent target: {door_south}")

    # Phase 1: walk to door-south-adjacent
    last_pos = None
    stuck = 0
    inside = False
    while time.time() - start < BUDGET_S:
        g = stable_read(c)
        if not g.saveblock1_valid:
            c.tap("A", frames=10); time.sleep(0.3)
            continue

        cur_map = (g.map_group, g.map_num)
        if cur_map != PETALBURG:
            inside = True
            print(f"INSIDE PC: pos=({g.x},{g.y}) map={cur_map}")
            break

        if g.in_battle:
            print(f"  battle at ({g.x},{g.y}) -> run")
            run_from_battle(c, rounds=3)
            stuck = 0
            continue

        cur_pos = (g.x, g.y)
        if cur_pos == door_south:
            print(f"  at door-south, step Up to warp")
            step(c, "Up")
            time.sleep(1.0)
            continue
        if cur_pos == last_pos:
            stuck += 1
        else:
            stuck = 0
            print(f"  walk: {cur_pos}")
        last_pos = cur_pos

        if stuck >= 5:
            print(f"  stuck x{stuck} -> run")
            run_from_battle(c, rounds=2)
            stuck = 0
            continue

        npc_tiles = {(nx, ny) for (nx, ny, _gid) in g.npcs_on_map}
        path = mc.bfs_to_tile(
            g.map_group, g.map_num, cur_pos, {door_south},
            blocked_tiles=npc_tiles,
        )
        if path is None:
            path = mc.bfs_to_tile(
                g.map_group, g.map_num, cur_pos, {door_south}
            )
        if not path:
            print(f"[stuck] no BFS to door from {cur_pos}")
            step(c, "Down")
            continue
        step(c, path[0])

    if not inside:
        print("[fail] never entered PC")
        return 1

    # Phase 2: heal inside
    print("PHASE 2: heal inside PC")
    for _ in range(6):
        step(c, "Up")
    for _ in range(30):
        c.tap("A", frames=10)
        time.sleep(0.3)

    # Phase 3: exit
    print("PHASE 3: exit PC")
    for _ in range(10):
        step(c, "Down")

    g2 = stable_read(c)
    healed = g2.party0_hp > hp_before
    print(f"DONE: pos=({g2.x},{g2.y}) m=({g2.map_group},{g2.map_num}) "
          f"hp {hp_before} -> {g2.party0_hp} (healed={healed})")
    return 0 if healed else 2


if __name__ == "__main__":
    sys.exit(main())
