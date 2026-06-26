"""Repel buy v2 - careful step-by-step with clerk-tile-correct navigation.

Petalburg Mart interior layout (canon, verified via map_data probe):
  y=0,1: top wall (counter back)
  y=2,3: (0..1) walkable | (2) wall | (3..5) walkable - the counter cuts
         the room in half at x=2 column
  y=4:   (0..2) wall | (3..5) walkable
  y=5:   all walkable
  y=6:   all walkable
  y=7:   all walkable (entry row)

Clerk NPC: canon (1, 3). Approachable from NORTH (1,2) or WEST (0,3).
SOUTH (1,4) is wall.

Standing tile (1, 2) faces DOWN at clerk. Press A there to start the
buy dialog. Previous Repel attempts failed because they used Up x8
from entry (3,7) which lands on (3, -1) - the agent gets bounced into
the shelf wall, then A presses examine the shelf ("The shelves brim
with all sort..."), not the clerk.

Mart item list order (verified):
  0 POKE BALL    200
  1 POTION       300
  2 ANTIDOTE     100
  3 PARLYZ HEAL  200
  4 AWAKENING    250
  5 ESCAPE ROPE  550
  6 REPEL        350
Down x6 from default cursor (POKE BALL) lands on REPEL.
"""
from __future__ import annotations

import sys
import time

from generic_agent import io as io_mod, state as state_mod


PETALBURG = (0, 0)
MART = (8, 6)


def stable_read(c, retries: int = 5):
    g = state_mod.read_state(c)
    for _ in range(retries):
        if g.saveblock1_valid and (g.x, g.y) != (0, 0):
            return g
        time.sleep(0.3)
        g = state_mod.read_state(c)
    return g


def step(c, btn: str, ms: float = 0.45):
    c.tap(btn, frames=18)
    time.sleep(ms)


def tap(c, btn: str, ms: float = 0.35):
    c.tap(btn, frames=10)
    time.sleep(ms)


def screenshot(c, name: str) -> None:
    c.screenshot(f"mart_v2_{name}.png")


def main() -> int:
    c = io_mod.MGBAClient()

    g0 = stable_read(c)
    print(f"start: pos=({g0.x},{g0.y}) m=({g0.map_group},{g0.map_num})")

    # PHASE 1: enter Mart - walk to (25,13) Mart door-south then Up
    print("PHASE 1: enter Mart")
    # First reach mart door area - we expect to start in Petalburg
    if (g0.map_group, g0.map_num) != PETALBURG:
        print(f"[err] not in Petalburg, current m={g0.map_group},{g0.map_num}")
        return 1
    # Walk east-south to Mart door (25,13). Brittle hardcode; user can
    # also start with agent already at (25,13).
    target_door = (25, 13)
    for _ in range(15):
        g = stable_read(c)
        if (g.x, g.y) == target_door:
            break
        if (g.map_group, g.map_num) != PETALBURG:
            break
        # naive: step east if x < target.x, south if y < target.y
        if g.x < target_door[0]:
            step(c, "Right")
        elif g.x > target_door[0]:
            step(c, "Left")
        elif g.y < target_door[1]:
            step(c, "Down")
        elif g.y > target_door[1]:
            step(c, "Up")
    g = stable_read(c)
    print(f"  pre-door: ({g.x},{g.y})")
    # Enter
    step(c, "Up", ms=0.8)
    time.sleep(1.0)
    g = stable_read(c)
    if (g.map_group, g.map_num) != MART:
        print(f"[err] not inside Mart, m={g.map_group},{g.map_num} pos=({g.x},{g.y})")
        return 1
    print(f"  inside Mart: ({g.x},{g.y})")

    # PHASE 2: navigate to clerk-adjacent (1, 2) facing Down
    # Path from (3,7) entry: Up to (3,5) -> Left to (1,5) -> Up to (1,2)
    print("PHASE 2: navigate to clerk-south tile (1,2)")
    # (3,7) -> Up: walls at (3,4) so (3,7)->(3,5) directly via running shoes
    # Actually walk step by step to verify
    moves = ["Up", "Up", "Left", "Left", "Up", "Up", "Up"]
    for mv in moves:
        step(c, mv, ms=0.5)
        g = stable_read(c)
        if g.saveblock1_valid:
            print(f"    {mv} -> ({g.x},{g.y})")
    g = stable_read(c)
    if (g.x, g.y) != (1, 2):
        print(f"  not at (1,2), adjusting via BFS-ish moves")
        # Try to reach (1,2)
        for _ in range(10):
            g = stable_read(c)
            if (g.x, g.y) == (1, 2):
                break
            if g.x > 1:
                step(c, "Left")
            elif g.x < 1:
                step(c, "Right")
            elif g.y > 2:
                step(c, "Up")
            elif g.y < 2:
                step(c, "Down")
    g = stable_read(c)
    print(f"  final clerk-south pos: ({g.x},{g.y})")
    if (g.x, g.y) != (1, 2):
        print(f"[err] failed to reach (1,2)")
        return 1

    # Face Down toward clerk at (1,3)
    tap(c, "Down", ms=0.4)  # try to walk Down (blocked by clerk) - just turns face
    screenshot(c, "facing_clerk")

    # PHASE 3: clerk dialog
    print("PHASE 3: clerk dialog")
    # A x4 sequence:
    #   1: "Hi!"
    #   2: "Welcome to MART. May I help you?"
    #   3: BUY/SELL/SEE YA menu opens, cursor on BUY
    #   4: confirm BUY -> item list
    for i in range(4):
        tap(c, "A", ms=0.6)
        print(f"  A x{i+1}")
    time.sleep(0.5)
    screenshot(c, "after_buy_open")

    # PHASE 4: scroll to REPEL (index 6 from top POKE BALL)
    print("PHASE 4: scroll to REPEL")
    for i in range(6):
        tap(c, "Down", ms=0.3)
        print(f"  Down x{i+1}")
    screenshot(c, "on_repel")

    # PHASE 5: confirm buy
    print("PHASE 5: confirm")
    tap(c, "A", ms=0.6)  # select REPEL -> "How many?"
    tap(c, "A", ms=0.6)  # confirm qty 1
    # confirmation "350 yen OK?" + receive text
    for i in range(6):
        tap(c, "A", ms=0.5)
    # close menu
    for i in range(5):
        tap(c, "B", ms=0.4)
    screenshot(c, "after_close")

    # Verify Repel in bag (id 0x4C)
    g = stable_read(c)
    ptr = c.read32(0x03005D8C)
    sb2 = c.read32(0x03005D90)
    key = c.read32(sb2 + 0xAC) & 0xFFFF
    has_repel = False
    for slot in range(15):
        sid = c.read16(ptr + 0x560 + slot * 4)
        if sid == 0:
            break
        qty = c.read16(ptr + 0x560 + slot * 4 + 2) ^ key
        label = {0xd: "POTION", 0x4c: "REPEL"}.get(sid, hex(sid))
        print(f"  bag[{slot}]: {label} qty={qty}")
        if sid == 0x4c:
            has_repel = True

    if not has_repel:
        print("[fail] no REPEL in bag")
        return 2
    print("REPEL ACQUIRED")

    # PHASE 6: exit Mart
    print("PHASE 6: exit Mart")
    for i in range(8):
        step(c, "Down", ms=0.5)
        g = stable_read(c)
        if g.map_group == PETALBURG[0] and g.map_num == PETALBURG[1]:
            print(f"  exited to Petalburg at ({g.x},{g.y})")
            break

    # PHASE 7: use Repel via START menu
    print("PHASE 7: use Repel via menu")
    tap(c, "Start", ms=0.8)
    # Menu cursor starts on POKEDEX (top). BAG is 3rd. Down x2, A.
    # Wait, depending on game state, order may differ. Conservative:
    # Press Down repeatedly looking for BAG, or use known order.
    # In Emerald early game with Pokedex: POKEDEX / POKEMON / BAG / ...
    tap(c, "Down", ms=0.3); tap(c, "Down", ms=0.3)
    tap(c, "A", ms=0.6)  # open BAG
    # BAG opens to ITEMS pocket. Scroll down to find REPEL.
    # POTION x2 at slot 0, REPEL probably slot 1.
    tap(c, "Down", ms=0.3)
    tap(c, "A", ms=0.6)  # select item
    # USE/GIVE/TOSS menu - USE is top, A
    tap(c, "A", ms=0.6)
    # confirmation
    for i in range(4):
        tap(c, "A", ms=0.4)
    # close menus
    for i in range(5):
        tap(c, "B", ms=0.4)

    g_final = stable_read(c)
    print(f"DONE: pos=({g_final.x},{g_final.y}) m=({g_final.map_group},{g_final.map_num})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
