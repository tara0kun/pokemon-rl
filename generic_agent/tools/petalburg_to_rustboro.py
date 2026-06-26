"""One-shot escort: current pos → Rustboro City via Petalburg/Route 104/Woods.

Designed to be run during continuous_train's idle windows. Uses
map_path BFS + per-map bfs_to_tile to step from the agent's current
location through the natural progression chain to Rustboro City.
Handles wild encounters with RUN (over-leveled Treecko shouldn't grind)
and trainer encounters with FIGHT-spam.

Exits when:
- agent reaches Rustboro City (0, 3)
- 15-minute budget elapsed
- whiteout (party fainted)

Safe to abort: only button taps, nothing persistent beyond in-game save.
"""
from __future__ import annotations

import sys
import time

from generic_agent import io as io_mod, map_data, state as state_mod


BUDGET_S = 900
RUSTBORO_MAP = (0, 3)


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


def fight_clear(c, rounds: int = 8) -> None:
    """Standard FIGHT loop for trainer battles."""
    for _ in range(rounds):
        c.tap("A", frames=10); time.sleep(0.3)
        c.tap("A", frames=10); time.sleep(0.3)
        for _ in range(10):
            c.tap("A", frames=8); time.sleep(0.2)
        g = stable_read(c)
        if g.saveblock1_valid and (g.x, g.y) != (0, 0):
            return


def run_from_wild(c, rounds: int = 6) -> None:
    """Battle menu (2x2): cursor starts on FIGHT. Move to RUN (BR) → A.
    Spam to clear the post-run text."""
    for _ in range(rounds):
        c.tap("Right", frames=10); time.sleep(0.2)
        c.tap("Down", frames=10); time.sleep(0.2)
        c.tap("A", frames=12); time.sleep(0.35)
        for _ in range(8):
            c.tap("A", frames=8); time.sleep(0.2)
        g = stable_read(c)
        if g.saveblock1_valid and (g.x, g.y) != (0, 0):
            return


def handle_battle(c, g) -> bool:
    """If RAM/screen says we're in battle, resolve it. Returns True if
    a battle was handled, False otherwise.

    When the starter is over-leveled (Lv >= 14), FIGHT one-shots every
    wild Pokemon on Route 102-104, so we just spam A on FIGHT instead
    of risking a RUN sequence with an unknown cursor position. RUN
    sequences fail when the cursor isn't on FIGHT (which happens after
    the first action of the battle).
    """
    if g.in_battle:
        if g.is_trainer_battle:
            print(f"  battle: TRAINER → fight_clear")
            fight_clear(c, rounds=15)
        elif g.party0_level >= 14:
            print(f"  battle: WILD (overleveled lv{g.party0_level}) → fight_clear")
            fight_clear(c, rounds=8)
        else:
            print(f"  battle: WILD → run_from_wild")
            run_from_wild(c, rounds=8)
        return True
    return False


def main() -> int:
    c = io_mod.MGBAClient()
    mc = map_data.get_cache()
    start = time.time()

    g0 = stable_read(c)
    print(f"start: pos=({g0.x},{g0.y}) map=({g0.map_group},{g0.map_num}) "
          f"lv={g0.party0_level} hp={g0.party0_hp}/{g0.party0_max_hp} "
          f"balls={g0.bag_pokeball_count}")

    last_pos = (g0.x, g0.y)
    last_map = (g0.map_group, g0.map_num)
    stuck_count = 0

    while time.time() - start < BUDGET_S:
        g = stable_read(c)
        if not g.saveblock1_valid:
            # mid-transition / dialog — press A and retry
            c.tap("A", frames=10); time.sleep(0.3)
            continue

        cur_map = (g.map_group, g.map_num)
        cur_pos = (g.x, g.y)

        if cur_map == RUSTBORO_MAP:
            print(f"DONE: reached Rustboro City at ({g.x},{g.y})")
            return 0

        if handle_battle(c, g):
            stuck_count = 0
            continue

        # whiteout = party fainted → game auto-warps to PC
        if g.party_count >= 1 and g.party0_hp == 0:
            print(f"WHITEOUT detected at ({g.x},{g.y}) m={cur_map}")
            for _ in range(30):
                c.tap("A", frames=10); time.sleep(0.25)
            continue

        # Movement
        if cur_pos == last_pos and cur_map == last_map:
            stuck_count += 1
        else:
            stuck_count = 0
            print(f"  moved: m={cur_map} pos={cur_pos}")
        last_pos = cur_pos
        last_map = cur_map

        if stuck_count >= 6:
            # Probably wild battle with RAM false-negative. When
            # over-leveled, FIGHT is safer than RUN (no cursor-position
            # gamble + Lv 14+ Treecko one-shots all early-route wilds).
            if g.party0_level >= 14:
                print(f"  stuck x{stuck_count} → suspect hidden battle, fight")
                fight_clear(c, rounds=4)
            else:
                print(f"  stuck x{stuck_count} → suspect hidden battle, run")
                run_from_wild(c, rounds=3)
            stuck_count = 0
            continue

        # Find inter-map chain
        chain = mc.map_path(*cur_map, *RUSTBORO_MAP, max_hops=10)
        if not chain:
            # Likely on an indoor map with no chain to Rustboro. Step
            # onto the first available warp (door) to leave this map.
            cur_info = mc.get(*cur_map)
            warp_set: set[tuple[int, int]] = set()
            if cur_info:
                for w in cur_info.warps:
                    if w.get("dest_map"):
                        warp_set.add((int(w["x"]), int(w["y"])))
            if warp_set:
                wpath = mc.bfs_to_tile(
                    g.map_group, g.map_num, cur_pos, warp_set
                )
                if wpath:
                    print(f"  indoor escape: BFS to warp {len(wpath)} steps")
                    step(c, wpath[0])
                    continue
                if cur_pos in warp_set:
                    step_btn = mc.warp_step_direction(
                        g.map_group, g.map_num, g.x, g.y
                    ) or "Down"
                    print(f"  indoor escape: on warp, step {step_btn}")
                    step(c, step_btn)
                    continue
            print(f"[stuck] no inter-map path from {cur_map} to {RUSTBORO_MAP}")
            step(c, "Down")
            continue

        next_map = chain[0]
        next_name = mc.name_for(*next_map)
        cur_info = mc.get(*cur_map)

        # Find target tiles: either connection edges or warps to next_name
        target_tiles: set[tuple[int, int]] = set()
        if cur_info:
            for direction, conn in cur_info.connections.items():
                if conn["map_name"] == next_name:
                    target_tiles |= mc.exit_tiles_toward(
                        g.map_group, g.map_num, direction
                    )
            if not target_tiles:
                target_tiles |= mc.warp_tiles_for(
                    g.map_group, g.map_num, next_name
                )

        if not target_tiles:
            print(f"[stuck] no target tiles for {next_name} from {cur_map}")
            step(c, "Left")
            continue

        npc_tiles = {(nx, ny) for (nx, ny, _gid) in g.npcs_on_map}
        path = mc.bfs_to_tile(
            g.map_group, g.map_num, cur_pos, target_tiles,
            blocked_tiles=npc_tiles,
        )
        if path is None:
            # NPC blocked the only path — retry without that constraint.
            path = mc.bfs_to_tile(
                g.map_group, g.map_num, cur_pos, target_tiles
            )

        if path is None:
            print(f"[stuck] no BFS path to {next_name} edge from {cur_pos}")
            step(c, "Left")
            continue

        if path == []:
            # Already standing on a target tile (warp / edge). Step into
            # it via the warp direction; for edge transitions any step
            # toward the connection direction should fire the warp.
            step_btn = mc.warp_step_direction(
                g.map_group, g.map_num, g.x, g.y
            )
            if step_btn is None:
                step_btn = "Down"  # safe default for indoor doors
            print(f"  on warp tile, step {step_btn}")
            step(c, step_btn)
            continue

        next_btn = path[0]
        step(c, next_btn)

    g = stable_read(c)
    print(f"BUDGET EXPIRED at pos=({g.x},{g.y}) m=({g.map_group},{g.map_num}) "
          f"lv={g.party0_level} hp={g.party0_hp}/{g.party0_max_hp}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
