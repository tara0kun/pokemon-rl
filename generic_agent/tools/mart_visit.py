"""One-shot Petalburg Mart visitor: buy 5 Pokeballs.

The agent's catch policy needs balls in the bag. The cheapest source on
the early-game route is PetalburgCity_Mart (Clerk sells POKE_BALL at
200 ¥). Initial allowance is ~3000 ¥, so 5 balls = 1000 ¥ leaves the
agent enough for a Potion or two.

Run when agent is on Petalburg City map (0, 0) post-Pokedex. The script
navigates to the Mart warp at canonical (3,5) Petalburg, enters, walks
to the Clerk, opens the BUY menu, selects POKE BALL, sets quantity to 5,
confirms, then exits the Mart.

Safe to abort at any time — only button taps and reads.
"""
from __future__ import annotations

import sys
import time

from generic_agent import io as io_mod, map_data, state as state_mod


PETALBURG_MAP = (0, 0)
PETALBURG_MART_MAP = (5, 0)  # PetalburgCity_Mart (approximate map_group/num)


def stable_read(c, retries: int = 5):
    for _ in range(retries):
        g = state_mod.read_state(c)
        if g.saveblock1_valid and (g.x, g.y) != (0, 0):
            return g
        time.sleep(0.3)
    return g


def step(c, btn: str, ms: float = 0.4):
    c.tap(btn, frames=18)
    time.sleep(ms)


def walk_path(c, path):
    for b in path:
        step(c, b)


def main() -> int:
    c = io_mod.MGBAClient()
    mc = map_data.get_cache()

    g = stable_read(c)
    if (g.map_group, g.map_num) != PETALBURG_MAP:
        print(f"[err] not on Petalburg City: m=({g.map_group},{g.map_num})")
        return 1

    print(f"start: pos=({g.x},{g.y}) m={PETALBURG_MAP} balls={g.bag_pokeball_count}")
    balls_before = g.bag_pokeball_count

    # 1. Navigate to Mart entrance via BFS to canonical (3,5)
    info = mc.get(*PETALBURG_MAP)
    mart_warps = [
        (w["x"], w["y"]) for w in info.warps
        if "Mart" in w.get("dest_map", "")
    ]
    if not mart_warps:
        print("[err] no Mart warp in Petalburg map.json")
        return 1
    mart_warp = mart_warps[0]
    print(f"Mart warp at {mart_warp}")
    path = mc.bfs_to_tile(*PETALBURG_MAP, (g.x, g.y), {mart_warp})
    if not path:
        # try adjacency
        targets = {(mart_warp[0] + dx, mart_warp[1] + dy)
                   for dx, dy in [(0, 1), (0, -1), (-1, 0), (1, 0)]
                   if info.walkable(mart_warp[0] + dx, mart_warp[1] + dy)}
        path = mc.bfs_to_tile(*PETALBURG_MAP, (g.x, g.y), targets)
    if path is None:
        print(f"[err] no BFS path to Mart from ({g.x},{g.y})")
        return 1
    print(f"BFS to Mart: {len(path)} steps")
    walk_path(c, path + ["Up"])  # last Up to enter door

    time.sleep(1.0)
    g = stable_read(c)
    if (g.map_group, g.map_num) == PETALBURG_MAP:
        print(f"[err] didn't enter Mart, still on Petalburg ({g.x},{g.y})")
        return 1
    print(f"inside Mart: pos=({g.x},{g.y}) m=({g.map_group},{g.map_num})")

    # 2. Walk Up to Clerk counter, press A
    for _ in range(8):
        step(c, "Up")
    for _ in range(20):
        c.tap("A", frames=10)
        time.sleep(0.25)

    # 3. Clerk menu: BUY (top) → A
    c.tap("A", frames=10); time.sleep(0.4)
    # 4. Item list: POKE BALL is first item → A
    for _ in range(3):
        c.tap("A", frames=10); time.sleep(0.4)
    # 5. Quantity prompt: Up 4 times to reach 5, then A
    for _ in range(4):
        c.tap("Up", frames=10); time.sleep(0.3)
    c.tap("A", frames=10); time.sleep(0.4)
    # 6. Confirm purchase: A
    for _ in range(5):
        c.tap("A", frames=10); time.sleep(0.3)
    # 7. Exit shop menu: B
    for _ in range(5):
        c.tap("B", frames=10); time.sleep(0.3)

    g = stable_read(c)
    balls_after = g.bag_pokeball_count
    print(f"after buy: pos=({g.x},{g.y}) balls {balls_before} -> {balls_after}")

    # 8. Walk Down to exit Mart
    for _ in range(10):
        step(c, "Down")
    g = stable_read(c)
    print(f"FINAL: pos=({g.x},{g.y}) m=({g.map_group},{g.map_num}) "
          f"balls={g.bag_pokeball_count} party={g.party_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
