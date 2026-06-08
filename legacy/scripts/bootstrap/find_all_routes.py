"""
From Map(0,9) Littleroot Town, find ALL exits and test which ones allow movement + wild battles.
Uses save state slot 2 as safe return point.
"""
import requests
import time
import random

MGBA_URL = "http://localhost:5000"
session = requests.Session()


def read_val(endpoint, addr):
    r = session.get(f"{MGBA_URL}/core/{endpoint}", params={"address": hex(addr)}, timeout=3)
    v = r.json()
    if isinstance(v, dict):
        return int(v["value"])
    return int(v)


def write_val(endpoint, addr, value):
    session.post(f"{MGBA_URL}/core/{endpoint}", params={"address": hex(addr), "value": value}, timeout=3)


def tap(button, delay=0.06):
    session.post(f"{MGBA_URL}/mgba-http/button/tap", params={"button": button}, timeout=3)
    time.sleep(delay)


def get_pos():
    return read_val("read16", 0x02037000), read_val("read16", 0x02037002)


def get_map():
    sb1 = read_val("read32", 0x03005AEC)
    if not sb1 or sb1 < 0x02000000 or sb1 > 0x0203FFFF:
        return -1, -1
    return read_val("read8", sb1 + 4), read_val("read8", sb1 + 5)


def save_slot(slot):
    session.post(f"{MGBA_URL}/core/savestateslot", params={"slot": slot}, timeout=5)


def load_slot(slot):
    session.post(f"{MGBA_URL}/core/loadstateslot", params={"slot": slot}, timeout=5)
    time.sleep(0.5)


def is_battle():
    bf = read_val("read32", 0x02022E90)
    return bf is not None and 0 < bf < 0x10000


def set_flags():
    sb1 = read_val("read32", 0x03005AEC)
    flag_addr = sb1 + 0x1270 + 256
    cur = read_val("read8", flag_addr)
    write_val("write8", flag_addr, cur | 0x03)


def handle_battle():
    print("    Fighting...", end="", flush=True)
    for k in range(300):
        tap("A", 0.06)
        if k % 50 == 49:
            tap("B", 0.06)
        if not is_battle():
            hp = read_val("read16", 0x020241E6)
            hp_max = read_val("read16", 0x020241E8)
            level = read_val("read8", 0x020241E4)
            print(f" Done! HP:{hp}/{hp_max} Lv{level}")
            return True
    print(" Timeout")
    return False


print("=" * 50)
print("  Find All Routes from Littleroot")
print("=" * 50)

set_flags()
x, y = get_pos()
mg, mn = get_map()
print(f"Start: ({x},{y}) Map({mg},{mn})")

# Save good starting state
save_slot(2)
print("Saved to slot 2")

# Phase 1: Scan Map(0,9) for all exits using save/load
print("\n[Phase 1] Scanning all exits...")
exits = []
target = (0, 9)

for i in range(10000):
    d = random.choice(["Up", "Down", "Down", "Left", "Right"])
    tap(d, 0.025)
    mg, mn = get_map()

    if (mg, mn) != target and mg != -1:
        x, y = get_pos()
        dest = (mg, mn)
        known = any(ex["map"] == dest for ex in exits)
        if not known:
            exits.append({"x": x, "y": y, "dir": d, "map": dest})
            print(f"  EXIT: ({x},{y}) via {d} -> Map{dest}")
        # Reload safe state
        load_slot(2)
        set_flags()

    if i % 2000 == 1999:
        print(f"  [{i+1}] exits={len(exits)}")

print(f"\nFound {len(exits)} unique exits")
for ex in exits:
    print(f"  ({ex['x']},{ex['y']}) via {ex['dir']} -> Map{ex['map']}")

# Phase 2: Test each outdoor exit
print("\n[Phase 2] Testing outdoor exits...")
for idx, ex in enumerate(exits):
    mg, mn = ex["map"]

    # Only test outdoor maps (group 0)
    if mg != 0:
        print(f"\n  Exit {idx}: Map({mg},{mn}) [indoor, skip]")
        continue

    print(f"\n  Exit {idx}: Map({mg},{mn}) - testing...")
    load_slot(2)
    time.sleep(0.3)
    set_flags()

    # Navigate to exit area
    for step in range(300):
        cx, cy = get_pos()
        if abs(cx - ex["x"]) <= 1 and abs(cy - ex["y"]) <= 1:
            break
        if cx > ex["x"]:
            tap("Left", 0.03)
        elif cx < ex["x"]:
            tap("Right", 0.03)
        if cy > ex["y"]:
            tap("Up", 0.03)
        elif cy < ex["y"]:
            tap("Down", 0.03)

    # Try to cross into the exit map
    for _ in range(10):
        tap(ex["dir"], 0.05)
    cmg, cmn = get_map()
    cx, cy = get_pos()

    if (cmg, cmn) != (mg, mn):
        # Try again from a slightly different angle
        load_slot(2)
        time.sleep(0.2)
        set_flags()
        for _ in range(300):
            tap(random.choice(["Up", "Down", "Left", "Right"]), 0.03)
            cmg, cmn = get_map()
            if (cmg, cmn) == (mg, mn):
                cx, cy = get_pos()
                break
        if (cmg, cmn) != (mg, mn):
            print(f"    Could not reach Map({mg},{mn})")
            continue

    print(f"    Entered Map({cmg},{cmn}) at ({cx},{cy})")

    # Test movement
    can_move = False
    positions = set()
    positions.add((cx, cy))
    for _ in range(30):
        tap(random.choice(["Up", "Down", "Left", "Right"]), 0.04)
        pos = get_pos()
        positions.add(pos)
    if len(positions) > 1:
        can_move = True
        print(f"    CAN MOVE ({len(positions)} tiles)")
    else:
        print(f"    FROZEN!")
        continue

    # Walk around on this map to find wild battles
    print(f"    Walking to find wild encounters...")
    for j in range(1000):
        tap(random.choice(["Up", "Down", "Left", "Right"]), 0.025)

        if is_battle():
            cx, cy = get_pos()
            print(f"    WILD BATTLE at ({cx},{cy}) after {j} steps!")
            handle_battle()

            # This is our winner!
            print(f"\n  SUCCESS! Route with wild battles: Map({mg},{mn})")
            print("  Saving to all slots...")
            save_slot(1)
            save_slot(2)
            save_slot(3)

            cx, cy = get_pos()
            cmg, cmn = get_map()
            hp = read_val("read16", 0x020241E6)
            hp_max = read_val("read16", 0x020241E8)
            level = read_val("read8", 0x020241E4)
            party = read_val("read8", 0x0202418D)
            print(f"\n  Final: ({cx},{cy}) Map({cmg},{cmn})")
            print(f"  HP:{hp}/{hp_max} Lv{level} Party:{party}")
            exit(0)

        # If entered a different map, note it
        cmg2, cmn2 = get_map()
        if (cmg2, cmn2) != (mg, mn) and cmg2 != 0:
            # Went indoors, skip
            break

    print(f"    No battles in 1000 steps")

# If we get here, no route worked
load_slot(2)
print("\nNo working route with wild battles found.")
print("May need to set more event flags or find the Birch rescue completion flag.")
