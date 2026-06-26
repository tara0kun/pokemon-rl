"""Narrow escort: heal at Petalburg PC, then exit west to Route 104.

Two-phase escort:
1. From any tile in Petalburg City (0, 0), walk to PC warp at (15, 7)
   on the south wall (canon), enter, talk to nurse, exit.
2. After heal, walk to west edge tiles (0, 12-13) -> cross to Route 104.

Wild battle in transit: RUN (over-leveled Grovyle vs. nothing here).

Exits when agent is on Route 104 or 7-min budget elapses.
"""
from __future__ import annotations

import sys
import time

from generic_agent import io as io_mod, map_data, state as state_mod


BUDGET_S = 420
PETALBURG = (0, 0)
ROUTE_104 = (0, 19)


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


def run_from_battle(c, rounds: int = 4) -> None:
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


def heal_at_pc(c, mc) -> bool:
    """Walk to PC warp, enter, talk to nurse, exit. Returns True if HP
    increased."""
    info = mc.get(*PETALBURG)
    pc_warps = {
        (w["x"], w["y"]) for w in info.warps
        if "PokemonCenter" in (w.get("dest_map") or "")
    }
    if not pc_warps:
        print("[err] no PC warp on Petalburg map.json")
        return False
    print(f"PC warps at: {pc_warps}")
    g0 = stable_read(c)
    hp_before = g0.party0_hp

    # 1. BFS to PC entrance tile (door at the south of building)
    for _attempt in range(20):
        g = stable_read(c)
        if (g.map_group, g.map_num) != PETALBURG:
            break
        if (g.x, g.y) in pc_warps:
            step(c, "Up")  # enter door
            time.sleep(1.0)
            break
        npc_tiles = {(nx, ny) for (nx, ny, _gid) in g.npcs_on_map}
        path = mc.bfs_to_tile(
            g.map_group, g.map_num, (g.x, g.y), pc_warps,
            blocked_tiles=npc_tiles,
        )
        if path is None:
            path = mc.bfs_to_tile(
                g.map_group, g.map_num, (g.x, g.y), pc_warps
            )
        if path is None or not path:
            print(f"[err] no BFS to PC door from ({g.x},{g.y})")
            return False
        step(c, path[0])

    # 2. Inside PC: walk Up to nurse, A spam to heal
    time.sleep(0.5)
    g = stable_read(c)
    print(f"inside? pos=({g.x},{g.y}) m=({g.map_group},{g.map_num})")
    for _ in range(6):
        step(c, "Up")
    for _ in range(25):
        c.tap("A", frames=10); time.sleep(0.3)

    # 3. Exit PC: walk Down to door warp
    for _ in range(10):
        step(c, "Down")

    g2 = stable_read(c)
    healed = g2.party0_hp > hp_before
    print(f"heal result: hp {hp_before}->{g2.party0_hp} (healed={healed})")
    return healed


def main() -> int:
    c = io_mod.MGBAClient()
    mc = map_data.get_cache()
    start = time.time()

    g0 = stable_read(c)
    print(f"start: pos=({g0.x},{g0.y}) map=({g0.map_group},{g0.map_num}) "
          f"hp={g0.party0_hp}/{g0.party0_max_hp}")

    if (g0.map_group, g0.map_num) != PETALBURG:
        print(f"[err] not on Petalburg, current={g0.map_group},{g0.map_num}")
        return 1

    hp_frac = (
        g0.party0_hp / g0.party0_max_hp if g0.party0_max_hp > 0 else 1.0
    )
    if hp_frac < 0.5:
        print(f"HP {hp_frac:.1%} < 50% - heal phase")
        heal_at_pc(c, mc)
    else:
        print(f"HP {hp_frac:.1%} OK - skip heal")

    info = mc.get(*PETALBURG)
    west_edge_targets = {
        (0, y) for y in range(info.height) if info.walkable(0, y)
    }
    print(f"west edge targets: {sorted(west_edge_targets)}")

    last_pos = None
    stuck = 0
    hp_last = g0.party0_hp

    # Counter to cycle through alternative directions when BFS direction
    # keeps not moving us. Each retry tries a different perpendicular
    # so we can drift around grass tiles that consistently trigger
    # encounters on the BFS-suggested step.
    detour_attempt = 0

    while time.time() - start < BUDGET_S:
        g = stable_read(c)
        if not g.saveblock1_valid:
            c.tap("A", frames=10); time.sleep(0.3)
            continue

        cur_map = (g.map_group, g.map_num)
        if cur_map == ROUTE_104:
            print(f"DONE: entered Route 104 at ({g.x},{g.y})")
            return 0
        if cur_map != PETALBURG:
            print(f"[warn] left Petalburg to {cur_map} at ({g.x},{g.y})")
            return 2

        # Real battle = RAM in_battle OR HP just dropped. Without screen
        # detection we use HP delta as a reliable proxy: HP only decreases
        # mid-battle (heal at PC restores, but that's not while we're
        # walking the overworld).
        hp_now = g.party0_hp
        hp_dropped = hp_now < hp_last
        hp_last = hp_now
        if g.in_battle or hp_dropped:
            print(f"  battle at ({g.x},{g.y}) hp{hp_now} -> run")
            run_from_battle(c, rounds=3)
            stuck = 0
            detour_attempt = 0
            continue

        cur_pos = (g.x, g.y)
        if cur_pos == last_pos:
            stuck += 1
        else:
            stuck = 0
            detour_attempt = 0
            print(f"  moved: {cur_pos}")
        last_pos = cur_pos

        if stuck >= 4:
            # Most chronic stuck in Petalburg is NOT a wild battle - it's
            # an NPC dialog that auto-triggers when you step adjacent to
            # them (e.g. Petalburg Gym Boy at (8,12) talks at (8,13)).
            # Clear the dialog with A x12 (multi-line text), then push
            # WEST aggressively to escape the NPC's interaction range
            # before BFS recomputes through the same tile.
            detour_attempt += 1
            if detour_attempt <= 2:
                print(f"  stuck x{stuck} attempt={detour_attempt} -> A x12 + Left x6")
                for _ in range(12):
                    c.tap("A", frames=10); time.sleep(0.22)
                for _ in range(2):
                    c.tap("B", frames=10); time.sleep(0.2)
                # Aggressive west push past the talker.
                for _ in range(6):
                    c.tap("Left", frames=18); time.sleep(0.4)
            else:
                detour_dirs = ["Up", "Down", "Right", "Left"]
                d = detour_dirs[(detour_attempt - 3) % 4]
                print(f"  stuck x{stuck} attempt={detour_attempt} -> detour {d}")
                step(c, d)
            stuck = 0
            continue

        # Block NPCs AND their 4-adjacent tiles. Many Petalburg NPCs
        # (e.g. Gym Boy at (8,12)) auto-trigger a dialog when the agent
        # steps adjacent, freezing the agent in place until A clears it.
        # Treating those tiles as walls forces the BFS to route around
        # them entirely - usually via the y=10-11 corridor.
        # IMPORTANT: skip gid=0 entries - that's the player sprite itself,
        # appearing in the same list with no NPC interaction range.
        npc_tiles = set()
        for (nx, ny, gid) in g.npcs_on_map:
            if gid == 0:
                continue
            npc_tiles.add((nx, ny))
            for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                npc_tiles.add((nx + dx, ny + dy))
        # Never block the agent's current tile.
        npc_tiles.discard(cur_pos)
        path = mc.bfs_to_tile(
            g.map_group, g.map_num, cur_pos, west_edge_targets,
            blocked_tiles=npc_tiles,
        )
        if path is None:
            # Fallback: relax to just NPC tiles (no adjacent buffer).
            tight = {(nx, ny) for (nx, ny, _gid) in g.npcs_on_map}
            path = mc.bfs_to_tile(
                g.map_group, g.map_num, cur_pos, west_edge_targets,
                blocked_tiles=tight,
            )
        if path is None:
            path = mc.bfs_to_tile(
                g.map_group, g.map_num, cur_pos, west_edge_targets
            )
        if path is None:
            print(f"[stuck] BFS NULL from {cur_pos}")
            step(c, "Left")
            continue
        if not path:
            print(f"  on west edge {cur_pos}, step Left to warp")
            step(c, "Left")
            continue

        step(c, path[0])

    g = stable_read(c)
    print(f"BUDGET EXPIRED at pos=({g.x},{g.y}) m=({g.map_group},{g.map_num})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
