"""Route 104 grass area escort + wild battle grind.

深層 root cause: agent が bridge area (31,15-16) で chronic stuck。
正規 path は Route 104 → Petalburg Woods → Route 104 south の 3-hop。
heur autonomous goal (enter_rustboro_gym, target=11,3) が north pull
する一方、 真の grass tile は south y=42+。

実装:
- Phase 1: bridge → y=21 huge corridor 経由 west へ (10,21)
- Phase 2: x=10 column south → (10,30) warp to Petalburg Woods
- Phase 3: Woods → (16,38) warp back to Route 104 south
- Phase 4: 南 Route 104 y=42-43 grass area で random walk + wild battle
- Phase 5: battle = Up x2 + A x2 pattern (FIGHT cursor 強制) で 攻撃
"""
from __future__ import annotations

import sys
import time

from generic_agent import io as io_mod, state as state_mod
from generic_agent import map_data as md


ROUTE_104 = (0, 19)
PETALBURG_WOODS = (24, 11)


def stable_read(c, retries: int = 4):
    g = state_mod.read_state(c)
    for _ in range(retries):
        if g.saveblock1_valid and (g.x, g.y) != (0, 0):
            return g
        time.sleep(0.3)
        g = state_mod.read_state(c)
    return g


def step(c, btn: str, ms: float = 0.4) -> None:
    c.tap(btn, frames=18)
    time.sleep(ms)


def fight_attack(c) -> None:
    """Up x2 + A x2 pattern - FIGHT cursor reset + POUND."""
    # damage text
    for _ in range(4):
        c.tap("A", frames=12); time.sleep(0.3)
    # cursor to FIGHT
    c.tap("Up", frames=15); time.sleep(0.3)
    c.tap("Up", frames=15); time.sleep(0.3)
    # A FIGHT, A first move
    c.tap("A", frames=15); time.sleep(0.8)
    c.tap("A", frames=15); time.sleep(2.0)


def reach_via_greedy(
    c, target: tuple[int, int], in_map: tuple[int, int],
    budget: int = 60,
) -> bool:
    last_pos = None; stuck = 0
    for _ in range(budget):
        g = stable_read(c)
        if not g.saveblock1_valid:
            c.tap("A", frames=10); time.sleep(0.3); continue
        if (g.map_group, g.map_num) != in_map:
            return False
        if (g.x, g.y) == target:
            return True
        if g.party0_hp < g.party0_max_hp:
            for _ in range(8):
                fight_attack(c)
                gg = stable_read(c)
                if gg.party0_hp == gg.party0_max_hp:
                    break
            continue
        cur = (g.x, g.y)
        if cur == last_pos: stuck += 1
        else: stuck = 0
        last_pos = cur
        dx = target[0] - cur[0]; dy = target[1] - cur[1]
        if stuck > 3:
            d = ["Up", "Right", "Down", "Left"][stuck % 4]
        elif abs(dy) >= abs(dx):
            d = "Up" if dy < 0 else "Down"
        else:
            d = "Right" if dx > 0 else "Left"
        step(c, d)
    return False


def main() -> int:
    c = io_mod.MGBAClient()
    g = stable_read(c)
    print(f"START: pos=({g.x},{g.y}) m=({g.map_group},{g.map_num})")

    # Phase 1: bridge (31,15) → y=21 corridor (31,21)
    print("Phase 1: south to y=21 corridor")
    if not reach_via_greedy(c, (31, 21), ROUTE_104, budget=30):
        g = stable_read(c)
        print(f"  failed at ({g.x},{g.y})")

    # Phase 2: west to (10,21)
    print("Phase 2: west to (10,21)")
    if not reach_via_greedy(c, (10, 21), ROUTE_104, budget=50):
        g = stable_read(c)
        print(f"  failed at ({g.x},{g.y})")

    # Phase 3: south to (10,30) warp
    print("Phase 3: south x=10 column to warp")
    last_pos = None; stuck = 0
    for _ in range(40):
        g = stable_read(c)
        if not g.saveblock1_valid: continue
        if (g.map_group, g.map_num) == PETALBURG_WOODS:
            print(f"  WOODS at ({g.x},{g.y})")
            break
        cur = (g.x, g.y)
        if cur == last_pos: stuck += 1
        else: stuck = 0
        last_pos = cur
        if stuck > 2:
            d = ["Left", "Down", "Right", "Down"][stuck % 4]
        else:
            d = "Down"
        step(c, d)

    g = stable_read(c)
    if (g.map_group, g.map_num) != PETALBURG_WOODS:
        print(f"[FAIL] not in Woods, at ({g.x},{g.y}) m=({g.map_group},{g.map_num})")
        return 1

    # Phase 4: through Woods to south exit (16,38) or (17,38)
    print("Phase 4: Woods south exit (16,38)")
    if not reach_via_greedy(c, (16, 38), PETALBURG_WOODS, budget=60):
        g = stable_read(c)
        print(f"  partial at ({g.x},{g.y})")

    # Walk down to trigger warp
    for _ in range(8):
        step(c, "Down")
        g = stable_read(c)
        if (g.map_group, g.map_num) == ROUTE_104:
            print(f"  back in R104 at ({g.x},{g.y})")
            break

    # Phase 5: grass area (15, 42) and grind
    print("Phase 5: grass area (15,42) + grind")
    if not reach_via_greedy(c, (15, 45), ROUTE_104, budget=50):
        g = stable_read(c)
        print(f"  at ({g.x},{g.y})")

    # Random walk for encounters
    encs = 0; battles_won = 0
    last_lv = stable_read(c).party0_level
    for step_n in range(150):
        g = stable_read(c)
        if not g.saveblock1_valid: continue
        if g.badge_count > 0:
            print(f"🏆 BADGE at step {step_n}!")
            break
        if g.party0_hp < g.party0_max_hp:
            encs += 1
            print(f"  ENC #{encs} at ({g.x},{g.y})")
            for _ in range(15):
                fight_attack(c)
                gg = stable_read(c)
                if gg.party0_hp == gg.party0_max_hp:
                    break
            gg = stable_read(c)
            if gg.party0_level > last_lv:
                battles_won += 1
                last_lv = gg.party0_level
                print(f"  LV UP! now lv={last_lv}")
            continue
        # Random direction
        d = ["Up", "Down", "Left", "Right"][step_n % 4]
        step(c, d)

    g = stable_read(c)
    print(f"FINAL: pos=({g.x},{g.y}) m=({g.map_group},{g.map_num}) "
          f"lv={g.party0_level} party={g.party_count} badges={g.badge_count} "
          f"encs={encs} won={battles_won}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
