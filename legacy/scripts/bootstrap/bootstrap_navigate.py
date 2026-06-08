"""
Pokemon Emerald Auto-Navigator v2
===================================
ゲームを自動操作して、野生ポケモンが出現するルートまでナビゲーションする。

戦略:
  Phase 1: 建物から出る（Down方向を優先、doormatを踏む）
  Phase 2: 町から出る（草むらがあるルートへ移動）
  Phase 3: 草むらで野生バトルを経験

完了後、スロット1にセーブして学習の良い開始地点を作る。
"""

import requests
import time
import random
import sys

MGBA_URL = "http://localhost:5000"
session = requests.Session()


def read8(addr):
    r = session.get(f"{MGBA_URL}/core/read8", params={"address": hex(addr)}, timeout=1)
    v = r.json()
    return int(v["value"]) if isinstance(v, dict) else int(v)


def read16(addr):
    r = session.get(f"{MGBA_URL}/core/read16", params={"address": hex(addr)}, timeout=1)
    v = r.json()
    return int(v["value"]) if isinstance(v, dict) else int(v)


def read32(addr):
    r = session.get(f"{MGBA_URL}/core/read32", params={"address": hex(addr)}, timeout=1)
    v = r.json()
    return int(v["value"]) if isinstance(v, dict) else int(v)


def tap(button, delay=0.08):
    session.post(f"{MGBA_URL}/mgba-http/button/tap", params={"button": button}, timeout=1)
    time.sleep(delay)


def get_state():
    x = read16(0x02037000)
    y = read16(0x02037002)
    sb1 = read32(0x03005AEC)
    mg = read8(sb1 + 4) if sb1 and 0x02000000 <= sb1 <= 0x0203FFFF else -1
    mn = read8(sb1 + 5) if sb1 and 0x02000000 <= sb1 <= 0x0203FFFF else -1
    hp = read16(0x020241E6)
    hp_max = read16(0x020241E8)
    party = read8(0x0202418D)
    level = read8(0x020241E4)
    battle = read32(0x02022E90)
    in_battle = battle is not None and 0 < battle < 0x10000
    return {
        "x": x, "y": y, "mg": mg, "mn": mn,
        "hp": hp, "hp_max": hp_max, "party": party, "level": level,
        "in_battle": in_battle
    }


def save_slot(slot):
    r = session.post(f"{MGBA_URL}/core/savestateslot", params={"slot": slot}, timeout=5)
    return r.status_code == 200


def load_slot(slot):
    r = session.post(f"{MGBA_URL}/core/loadstateslot", params={"slot": slot}, timeout=5)
    time.sleep(0.5)
    return r.status_code == 200


def handle_battle():
    """バトル中にA連打で戦闘を終了させる。"""
    print("    Fighting...", end="", flush=True)
    for i in range(300):
        tap("A", 0.08)
        if i % 50 == 49:
            # Also press B to skip battle text
            tap("B", 0.08)
        s = get_state()
        if not s["in_battle"]:
            print(f" Done! HP:{s['hp']}/{s['hp_max']} Lv{s['level']}")
            return s
    print(" Timed out")
    return get_state()


def walk_sequence(directions, repeat=1):
    """指定された方向に順番に歩く。"""
    for _ in range(repeat):
        for d in directions:
            tap(d, 0.1)


def try_exit_building(max_steps=300):
    """建物から出る。Down方向を優先し、出口を探す。"""
    print("\n  Phase 1: Exiting building...")
    state = get_state()
    start_map = (state["mg"], state["mn"])
    prev_x, prev_y = state["x"], state["y"]
    stuck = 0

    for step in range(max_steps):
        state = get_state()
        cur_map = (state["mg"], state["mn"])

        # Map changed = we exited!
        if cur_map != start_map:
            print(f"    Exited! Now on Map({state['mg']}, {state['mn']}) at ({state['x']}, {state['y']})")
            return True

        if state["in_battle"]:
            handle_battle()
            continue

        # Track stuck
        if state["x"] == prev_x and state["y"] == prev_y:
            stuck += 1
        else:
            stuck = 0
        prev_x, prev_y = state["x"], state["y"]

        # Strategy: walk down to find exit (doormat)
        if stuck > 20:
            # Try different approaches
            actions = ["Left", "Down", "Right", "Down", "A", "B"]
            tap(actions[step % len(actions)], 0.1)
            if stuck > 50:
                stuck = 0  # Reset and try again
        elif step % 5 < 3:
            tap("Down", 0.1)  # Mostly go down
        elif step % 5 == 3:
            tap(random.choice(["Left", "Right"]), 0.1)  # Some lateral movement
        else:
            tap("A", 0.1)  # Sometimes interact

        if step % 100 == 99:
            print(f"    Step {step+1}: ({state['x']}, {state['y']}) stuck={stuck}")

    print("    Could not exit building in time")
    return False


def explore_town(max_steps=1000):
    """町を探索し、出口を探す。"""
    print("\n  Phase 2: Exploring town, looking for route exit...")
    state = get_state()
    start_map = (state["mg"], state["mn"])
    town_map = start_map
    visited_tiles = set()
    visited_maps = {start_map}
    prev_x, prev_y = state["x"], state["y"]
    stuck = 0

    for step in range(max_steps):
        state = get_state()
        cur_map = (state["mg"], state["mn"])

        if state["in_battle"]:
            # We found a route with wild Pokemon!
            print(f"    WILD BATTLE on Map({state['mg']}, {state['mn']})!")
            handle_battle()
            return True

        # Track maps
        if cur_map not in visited_maps:
            visited_maps.add(cur_map)
            print(f"    New map: ({state['mg']}, {state['mn']}) at ({state['x']}, {state['y']})")

        # If we entered a building, try to exit
        if cur_map != town_map and state["y"] < 20:  # Small Y = likely indoor
            try_exit_building(100)
            state = get_state()
            cur_map = (state["mg"], state["mn"])

        tile = (cur_map[0], cur_map[1], state["x"], state["y"])
        visited_tiles.add(tile)

        # Track stuck
        if state["x"] == prev_x and state["y"] == prev_y:
            stuck += 1
        else:
            stuck = 0
        prev_x, prev_y = state["x"], state["y"]

        # Town navigation strategy:
        # In Pokemon RSE, routes are typically:
        # - North: Route 101 (to Oldale Town)
        # - South: (ocean, blocked)
        # So we primarily go Up, but also explore to find exits

        if stuck > 20:
            # Try to unstick
            for _ in range(5):
                tap(random.choice(["A", "B", "Up", "Down", "Left", "Right"]), 0.1)
            stuck = 0
        else:
            # Cycle through directions, favoring Up
            cycle = step % 20
            if cycle < 8:
                tap("Up", 0.1)
            elif cycle < 11:
                tap("Right", 0.1)
            elif cycle < 14:
                tap("Left", 0.1)
            elif cycle < 16:
                tap("Down", 0.1)
            elif cycle < 18:
                tap("A", 0.1)
            else:
                tap("B", 0.1)

        if step % 200 == 199:
            print(f"    Step {step+1}: ({state['x']}, {state['y']}) Map({state['mg']}, {state['mn']}) "
                  f"tiles={len(visited_tiles)} maps={len(visited_maps)} stuck={stuck}")

    print(f"    Town explored: {len(visited_tiles)} tiles, {len(visited_maps)} maps")
    return False


def directed_walk(direction, steps=100):
    """ある方向に向かって歩き、マップ変更やバトルを検出する。"""
    state = get_state()
    start_map = (state["mg"], state["mn"])
    maps_found = {start_map}
    battles = 0

    for i in range(steps):
        state = get_state()

        if state["in_battle"]:
            battles += 1
            print(f"    Battle #{battles} at ({state['x']}, {state['y']}) Map({state['mg']}, {state['mn']})")
            handle_battle()
            if battles >= 1:
                return True, maps_found, battles

        cur = (state["mg"], state["mn"])
        if cur not in maps_found:
            maps_found.add(cur)
            print(f"    New map: ({state['mg']}, {state['mn']}) at ({state['x']}, {state['y']})")

        # Walk in direction with occasional A/B for dialogue
        if i % 8 < 5:
            tap(direction, 0.08)
        elif i % 8 == 5:
            tap("A", 0.08)
        elif i % 8 == 6:
            tap("B", 0.08)
        else:
            # Slight variation
            sides = {"Up": ["Left", "Right"], "Down": ["Left", "Right"],
                     "Left": ["Up", "Down"], "Right": ["Up", "Down"]}
            tap(random.choice(sides.get(direction, ["Up"])), 0.08)

    return False, maps_found, battles


def main():
    print("=" * 60)
    print("  Pokemon Emerald Auto-Navigator v2")
    print("=" * 60)

    # Load slot 1
    print("\nLoading slot 1...")
    load_slot(1)
    time.sleep(1)

    state = get_state()
    print(f"Start: ({state['x']}, {state['y']}) Map({state['mg']}, {state['mn']})")
    print(f"HP: {state['hp']}/{state['hp_max']} Lv{state['level']} Party:{state['party']}")

    initial_map = (state["mg"], state["mn"])

    # Phase 1: Try to exit if in a building
    # Indoor maps tend to have small coordinate ranges and specific map groups
    # Let's check coordinates: if Y < 20, likely indoor
    if state["y"] < 20:
        exited = try_exit_building()
        if exited:
            state = get_state()
            print(f"  Now at: ({state['x']}, {state['y']}) Map({state['mg']}, {state['mn']})")
        else:
            print("  Trying all directions to exit...")
            for direction in ["Down", "Left", "Right", "Up"]:
                print(f"    Trying {direction}...")
                for _ in range(100):
                    tap(direction, 0.08)
                    s = get_state()
                    if (s["mg"], s["mn"]) != initial_map:
                        print(f"    Exited via {direction}! Map({s['mg']}, {s['mn']})")
                        state = s
                        break
                if (state["mg"], state["mn"]) != initial_map:
                    break

    # Phase 2: Walk in each cardinal direction, looking for routes/battles
    print("\n  Phase 2: Directional exploration from current position...")
    state = get_state()
    current_map = (state["mg"], state["mn"])
    print(f"  Current: ({state['x']}, {state['y']}) Map({current_map[0]}, {current_map[1]})")

    all_maps = {current_map}

    # Try each direction from the current outdoor position
    for direction in ["Up", "Right", "Left", "Down"]:
        print(f"\n  Trying {direction} for 200 steps...")
        # Save current position so we can return
        save_slot(9)  # Temp save

        found, maps, battles = directed_walk(direction, 200)

        all_maps.update(maps)
        if found:
            # Great! We found wild battles
            state = get_state()
            print(f"\n  SUCCESS! Wild battles found going {direction}!")
            print(f"  Position: ({state['x']}, {state['y']}) Map({state['mg']}, {state['mn']})")
            print(f"  HP: {state['hp']}/{state['hp_max']} Lv{state['level']}")

            # Save this good position
            print("\n  Saving to slot 1 and 2...")
            save_slot(1)
            save_slot(2)
            print("  Done!")

            print(f"\n  Total maps discovered: {all_maps}")
            return True

        # Return to saved position for next direction
        load_slot(9)
        time.sleep(0.3)

    # Phase 3: If no battles found, try longer walks
    print(f"\n  Phase 3: Longer exploration...")
    print(f"  Maps found so far: {all_maps}")

    # Try walking Up for a very long time (routes are usually north)
    for direction in ["Up", "Up", "Right", "Left"]:
        print(f"\n  Long walk {direction} (500 steps)...")
        load_slot(9)
        time.sleep(0.3)

        found, maps, battles = directed_walk(direction, 500)
        all_maps.update(maps)

        if found:
            state = get_state()
            print(f"\n  SUCCESS!")
            save_slot(1)
            save_slot(2)
            print("  Saved to slots 1 and 2!")
            return True

    # If nothing works, just save the best position we found
    print(f"\n  No wild battles found after extensive exploration.")
    print(f"  All maps discovered: {all_maps}")
    print(f"  The game may need manual progression past story events.")
    print(f"\n  Saving current state anyway...")
    save_slot(1)
    save_slot(2)
    return False


if __name__ == "__main__":
    try:
        success = main()
        if success:
            print("\n  Bootstrap complete! Ready for training.")
            print("  Run: python train.py")
        else:
            print("\n  Bootstrap incomplete. Manual intervention may be needed.")
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
