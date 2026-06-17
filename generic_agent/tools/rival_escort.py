"""One-shot bridge: escort agent to Route 103 Rival and trigger battle.

Designed to be run during continuous_train's idle windows (between heur
collection and CNN training, when mGBA isn't being driven). Uses BFS
over canonical map.bin data, handles wild encounters with FIGHT-spam,
and blasts through Rival's dialog. Exits cleanly when one of:
  - flags increased (Rival defeated, lab state advanced)
  - whiteout happened (party fainted, teleport-heal)
  - 5-minute budget elapsed
  - agent reached Route 103 (10,3) area and pressed A 30 times

Safe to abort: writing to mGBA is just button taps; nothing persistent
is changed beyond the in-game save state.
"""
from __future__ import annotations

import sys
import time

from generic_agent import io as io_mod, map_data, state as state_mod


BUDGET_S = 300
RIVAL_MAP = (0, 18)


def fight_clear(c, max_rounds: int = 8) -> None:
    """Run a FIGHT/POUND sequence to resolve a battle. Exits when overworld
    state reads back stable."""
    for _ in range(max_rounds):
        c.tap("Up", frames=8); time.sleep(0.12)
        c.tap("Left", frames=8); time.sleep(0.12)
        c.tap("A", frames=10); time.sleep(0.3)
        c.tap("A", frames=10); time.sleep(0.3)
        for _ in range(12):
            c.tap("A", frames=8); time.sleep(0.2)
        g = state_mod.read_state(c)
        if g.saveblock1_valid and (g.x, g.y) != (0, 0):
            return


def step(c, btn: str) -> object:
    c.tap(btn, frames=18); time.sleep(0.4)
    return state_mod.read_state(c)


def main() -> int:
    c = io_mod.MGBAClient()
    mc = map_data.get_cache()
    start = time.time()

    g = state_mod.read_state(c)
    flags_start = g.total_event_flags
    print(f"start: pos=({g.x},{g.y}) m=({g.map_group},{g.map_num}) "
          f"hp={g.party0_hp}/{g.party0_max_hp} flags={flags_start}")

    rival = mc.find_npc_by_script_keyword(*RIVAL_MAP, "rival")
    if rival is None:
        print("[err] no Rival NPC found on canonical Route 103")
        return 1
    rx, ry = rival
    target_tiles = {
        (rx - 1, ry), (rx + 1, ry),
        (rx, ry - 1), (rx, ry + 1),
    }
    target_tiles = {
        t for t in target_tiles
        if mc.get(*RIVAL_MAP).walkable(*t)
    }
    print(f"Rival at ({rx},{ry}); approach tiles: {sorted(target_tiles)}")

    last_pos = (g.x, g.y, g.map_group, g.map_num)
    stuck = 0
    while time.time() - start < BUDGET_S:
        g = state_mod.read_state(c)
        if not g.saveblock1_valid:
            fight_clear(c)
            continue
        cur_xy = (g.x, g.y, g.map_group, g.map_num)
        if cur_xy == last_pos:
            stuck += 1
        else:
            stuck = 0
            last_pos = cur_xy
        if stuck >= 4:
            # Pressed direction multiple times with no movement — likely
            # battle UI eating our buttons. Run FIGHT sequence.
            fight_clear(c, max_rounds=10)
            stuck = 0
            continue
        if g.total_event_flags > flags_start:
            print(f">>> FLAG CHANGE {flags_start} -> {g.total_event_flags} "
                  f"at ({g.x},{g.y}) m=({g.map_group},{g.map_num})")
            return 0
        if g.party0_hp == 0 and g.saveblock1_valid and g.party_count >= 1:
            print(f"WHITEOUT — agent fainted, exiting")
            for _ in range(40):
                c.tap("A", frames=10); time.sleep(0.25)
            return 0

        cur_map = (g.map_group, g.map_num)
        # Already on Route 103 — BFS to Rival, A on adjacency
        if cur_map == RIVAL_MAP:
            if (g.x, g.y) in target_tiles:
                # face Rival, A
                if rx > g.x: step(c, "Right")
                elif rx < g.x: step(c, "Left")
                elif ry > g.y: step(c, "Down")
                elif ry < g.y: step(c, "Up")
                for _ in range(20):
                    c.tap("A", frames=12); time.sleep(0.35)
                    g = state_mod.read_state(c)
                    if not g.saveblock1_valid:
                        fight_clear(c, max_rounds=20)
                        break
                    if g.total_event_flags > flags_start:
                        break
                continue
            path = mc.bfs_to_tile(*RIVAL_MAP, (g.x, g.y), target_tiles)
            if not path:
                print(f"[stuck] no BFS path on Route 103 from ({g.x},{g.y})")
                # try a random walk to escape current spot
                for d in ("Up", "Right", "Down", "Left"):
                    step(c, d)
                continue
            step(c, path[0])
            continue

        # Not on Route 103 — navigate via map_path BFS
        chain = mc.map_path(*cur_map, *RIVAL_MAP, max_hops=8)
        if not chain:
            print(f"[stuck] no inter-map path from {cur_map} to {RIVAL_MAP}")
            # just walk Up to try reaching Oldale's north exit
            step(c, "Up")
            continue
        next_map = chain[0]
        next_name = mc.name_for(*next_map)
        cur_info = mc.get(*cur_map)
        # exit tiles toward connected map
        exits: set[tuple[int, int]] = set()
        for direction, conn in (cur_info.connections.items() if cur_info else []):
            if conn["map_name"] == next_name:
                exits |= mc.exit_tiles_toward(*cur_map, direction)
        if not exits:
            exits |= mc.warp_tiles_for(*cur_map, next_name)
        if not exits:
            print(f"[stuck] no exit from {cur_map} toward {next_name}")
            step(c, "Up")
            continue
        path = mc.bfs_to_tile(*cur_map, (g.x, g.y), exits)
        if not path:
            print(f"[stuck] no BFS to {next_name} exit from ({g.x},{g.y})")
            step(c, "Up")
            continue
        step(c, path[0])

    print(f"[budget] {BUDGET_S}s elapsed; final pos=({g.x},{g.y}) "
          f"m=({g.map_group},{g.map_num}) flags={g.total_event_flags}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
