"""Rustboro Gym → Roxanne 専用 escort.

heur autonomous がトレーナー LOS 連続で Gym 外に押し出される問題への対策。
明示的に 全 trainer に walk-into で battle 発火させ、 ab5716f (cursor reset)
+ wild_fight_safe で攻撃 → 順次撃破 → 道が開けるたびに Roxanne (5,2) へ greedy。

使用法:
    python -m generic_agent.tools.gym_roxanne_escort

事前条件:
    - mGBA + Lua 起動済 (port 8895)
    - agent が m=(11,3) Rustboro Gym 内、 または Rustboro 街中 + warp 接近

実装:
    Phase 1: Rustboro Gym warp (27,19) まで walk + Up で warp
    Phase 2: Gym 内 (5,19) entrance から trainer に順次 walk-in
    Phase 3: battle 中 cursor reset Left+Up+A x3 + POUND/move 選択
    Phase 4: 撃破後 path 再 BFS、 次 trainer or Roxanne へ
    Phase 5: Roxanne 戦 + Stone Badge 取得 verify
"""
from __future__ import annotations

import sys
import time

from generic_agent import io as io_mod, state as state_mod
from generic_agent import map_data as md


GYM_MAP = (11, 3)
RUSTBORO_CITY = (0, 3)
ROUTE_104 = (0, 19)
GYM_WARP_OUTDOOR = (27, 19)
ROXANNE = (5, 2)


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


def battle_attack(c, cycles: int = 4) -> None:
    """ab5716f cursor reset + A spam to fight."""
    for _ in range(cycles):
        step(c, "Left", 0.3)
        step(c, "Up", 0.3)
        for _ in range(5):
            c.tap("A", frames=12)
            time.sleep(0.3)


def reach_pos(
    c, target: tuple[int, int], in_map: tuple[int, int],
    budget: int = 50,
) -> bool:
    """Greedy walk to target tile, A-spam on dialog/battle."""
    last_pos = None
    stuck = 0
    for _ in range(budget):
        g = stable_read(c)
        if not g.saveblock1_valid:
            c.tap("A", frames=10); time.sleep(0.3); continue
        if (g.map_group, g.map_num) != in_map:
            return False
        if (g.x, g.y) == target:
            return True
        if g.party0_hp < g.party0_max_hp:
            battle_attack(c)
            continue
        cur = (g.x, g.y)
        if cur == last_pos:
            stuck += 1
        else:
            stuck = 0
        last_pos = cur
        dx = target[0] - cur[0]
        dy = target[1] - cur[1]
        if stuck > 3:
            d = ["Up", "Right", "Down", "Left"][stuck % 4]
        elif abs(dy) >= abs(dx):
            d = "Up" if dy < 0 else "Down"
        else:
            d = "Right" if dx > 0 else "Left"
        step(c, d)
    return False


def gym_entry(c) -> bool:
    """Get into Rustboro Gym (m=11,3)."""
    g = stable_read(c)
    print(f"gym_entry: pos=({g.x},{g.y}) m=({g.map_group},{g.map_num})")
    if (g.map_group, g.map_num) == GYM_MAP:
        return True
    # If on Route 104, walk to Rustboro first
    if (g.map_group, g.map_num) == ROUTE_104:
        # Walk to y=0 then West to x=19 (verified transition point)
        # First reach y=0
        for _ in range(60):
            g = stable_read(c)
            if not g.saveblock1_valid: c.tap("A", frames=10); time.sleep(0.3); continue
            if g.y == 0: break
            if g.party0_hp < g.party0_max_hp:
                battle_attack(c); continue
            step(c, "Up")
        # Walk west to x=19 → transitions to Rustboro
        for _ in range(40):
            g = stable_read(c)
            if not g.saveblock1_valid: c.tap("A", frames=10); time.sleep(0.3); continue
            if (g.map_group, g.map_num) == RUSTBORO_CITY:
                print(f"  entered Rustboro at ({g.x},{g.y})")
                break
            if g.x <= 19 and g.y == 0:
                step(c, "Left")
            else:
                step(c, "Left")
    # Now in Rustboro - walk to (27,19) Gym warp
    if not reach_pos(c, GYM_WARP_OUTDOOR, RUSTBORO_CITY, budget=80):
        g = stable_read(c)
        print(f"  failed to reach Gym warp, at ({g.x},{g.y})")
        return False
    # Up at warp tile to enter Gym
    for _ in range(5):
        c.tap("Up", frames=25); time.sleep(1.5)
        g = stable_read(c)
        if (g.map_group, g.map_num) == GYM_MAP:
            print(f"  GYM ENTERED at ({g.x},{g.y})")
            return True
    return False


def main() -> int:
    c = io_mod.MGBAClient()
    g = stable_read(c)
    print(f"START: pos=({g.x},{g.y}) m=({g.map_group},{g.map_num}) "
          f"hp={g.party0_hp}/{g.party0_max_hp} lv={g.party0_level} "
          f"badges={g.badge_count}")

    # Phase 1: get into Gym
    if (g.map_group, g.map_num) != GYM_MAP:
        if not gym_entry(c):
            print("[FAIL] can't enter Gym")
            return 1
    g = stable_read(c)
    print(f"  in Gym at ({g.x},{g.y})")

    # Phase 2-4: walk to Roxanne via repeated greedy + battle
    print("walking to Roxanne...")
    if reach_pos(c, ROXANNE, GYM_MAP, budget=200):
        print(f"  reached Roxanne tile")
    else:
        g = stable_read(c)
        print(f"  budget exhausted at ({g.x},{g.y}) m=({g.map_group},{g.map_num})")

    # Phase 5: engage Roxanne (already triggered if we walked into LOS)
    print("Roxanne battle engagement...")
    for cycle in range(30):
        g = stable_read(c)
        if g.badge_count > 0:
            print(f"🏆 STONE BADGE GET! pos=({g.x},{g.y}) hp={g.party0_hp}")
            return 0
        if g.party_count == 0:
            print(f"WHITEOUT pos=({g.x},{g.y})")
            break
        # A spam + cursor reset combo
        battle_attack(c, cycles=3)
        # If dialog blocking
        for _ in range(10):
            c.tap("A", frames=12); time.sleep(0.3)
    g = stable_read(c)
    print(f"FINAL: pos=({g.x},{g.y}) hp={g.party0_hp}/{g.party0_max_hp} "
          f"badges={g.badge_count}")
    return 0 if g.badge_count > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
