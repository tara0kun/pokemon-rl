"""
Clean restart: load slot 8, dismiss menus properly, walk on Route 101.
NO soft reset, NO in-game save. Just clean overworld walking.
"""
import requests
import time
import random
import sys

MGBA_URL = "http://localhost:5000"
session = requests.Session()


def read_val(ep, addr):
    for _ in range(3):
        try:
            v = session.get(f"{MGBA_URL}/core/{ep}", params={"address": hex(addr)}, timeout=5).json()
            return int(v["value"]) if isinstance(v, dict) else int(v)
        except:
            time.sleep(0.3)
    return 0


def write_val(ep, addr, value):
    try:
        session.post(f"{MGBA_URL}/core/{ep}", params={"address": hex(addr), "value": value}, timeout=3)
    except:
        pass


def tap(button, delay=0.06):
    try:
        session.post(f"{MGBA_URL}/mgba-http/button/tap", params={"button": button}, timeout=3)
    except:
        pass
    time.sleep(delay)


def get_pos():
    return read_val("read16", 0x02037000), read_val("read16", 0x02037002)


def get_map():
    sb1 = read_val("read32", 0x03005AEC)
    if not sb1 or sb1 < 0x02000000 or sb1 > 0x0203FFFF:
        return -1, -1
    return read_val("read8", sb1 + 4), read_val("read8", sb1 + 5)


def screenshot(name):
    path = f"C:/pokemon-rl/{name}.png"
    session.post(f"{MGBA_URL}/core/screenshot", params={"path": path}, timeout=5)
    return path


def save_slot(slot):
    session.post(f"{MGBA_URL}/core/savestateslot", params={"slot": slot}, timeout=5)


def load_slot(slot):
    session.post(f"{MGBA_URL}/core/loadstateslot", params={"slot": slot}, timeout=5)
    time.sleep(0.5)


def set_flag(sb1, flag_num):
    byte_idx = flag_num // 8
    bit_idx = flag_num % 8
    addr = sb1 + 0x1270 + byte_idx
    cur = read_val("read8", addr)
    write_val("write8", addr, cur | (1 << bit_idx))


def set_var(sb1, var_num, value):
    index = var_num - 0x4000
    addr = sb1 + 0x139C + (index * 2)
    write_val("write16", addr, value)


def set_minimal_flags(sb1):
    set_flag(sb1, 0x860)
    set_flag(sb1, 0x861)
    set_flag(sb1, 0x800)
    set_flag(sb1, 0x801)
    set_flag(sb1, 0x52)
    set_flag(sb1, 0x2BC)
    set_flag(sb1, 0x2D0)
    set_flag(sb1, 0x2EE)
    set_var(sb1, 0x4060, 3)
    set_var(sb1, 0x4049, 3)
    set_var(sb1, 0x4050, 6)
    set_var(sb1, 0x4084, 4)


print("=" * 50)
print("  Clean Restart from Slot 8")
print("=" * 50)

# Load slot 8
print("\n[1] Loading slot 8...")
load_slot(8)
time.sleep(1)

x, y = get_pos()
mg, mn = get_map()
print(f"    At ({x},{y}) Map({mg},{mn})")
screenshot("clean_start")

# Set flags
sb1 = read_val("read32", 0x03005AEC)
set_minimal_flags(sb1)

# Dismiss dialogue (B only, to avoid triggering anything)
print("[2] Pressing B to clear menus...")
for _ in range(30):
    tap("B", 0.1)

x, y = get_pos()
screenshot("clean_cleared")
print(f"    At ({x},{y})")

# Test movement
print("\n[3] Testing movement...")
before = get_pos()
for _ in range(10):
    tap("Up", 0.04)
after = get_pos()
moved_up = after != before
print(f"    Up: {before} -> {after} {'OK' if moved_up else 'BLOCKED'}")

before = get_pos()
for _ in range(10):
    tap("Down", 0.04)
after = get_pos()
moved_down = after != before
print(f"    Down: {before} -> {after} {'OK' if moved_down else 'BLOCKED'}")

if not moved_up and not moved_down:
    print("    CANNOT MOVE! Game might be in dialogue/cutscene")
    print("    Loading slot 8 again with more B spam...")
    load_slot(8)
    time.sleep(1)
    for _ in range(100):
        tap("B", 0.08)
    screenshot("retry_cleared")

# Now walk south to Littleroot then back north
# This ensures fresh map entry for encounter tables
print("\n[4] Walking south to exit Route 101...")
for i in range(200):
    tap("Down", 0.03)
    mg, mn = get_map()
    if (mg, mn) != (0, 16) and mg == 0:
        x, y = get_pos()
        print(f"    Exited to Map({mg},{mn}) at ({x},{y})")
        break
    if mg != 0:
        # Entered building - go down more
        tap("Down", 0.04)

screenshot("exited_route101")
mg, mn = get_map()
x, y = get_pos()
print(f"    Now at ({x},{y}) Map({mg},{mn})")

# Set flags on new map
sb1 = read_val("read32", 0x03005AEC)
set_minimal_flags(sb1)

# Walk back north to Route 101
print("\n[5] Walking back north to Route 101...")
for i in range(200):
    tap("Up", 0.03)
    mg, mn = get_map()
    if (mg, mn) == (0, 16):
        x, y = get_pos()
        print(f"    Re-entered Route 101 at ({x},{y})!")
        break
    if mg != 0:
        for _ in range(10):
            tap("Down", 0.04)
            if get_map()[0] == 0:
                break

mg, mn = get_map()
if (mg, mn) != (0, 16):
    print(f"    Failed to reach Route 101 (at Map({mg},{mn}))")
    # Try left/up combination
    for i in range(300):
        tap(random.choice(["Up", "Up", "Left", "Right"]), 0.03)
        mg, mn = get_map()
        if (mg, mn) == (0, 16):
            x, y = get_pos()
            print(f"    Found Route 101 at ({x},{y})")
            break
        if mg != 0:
            for _ in range(15):
                tap("Down", 0.04)
                if get_map()[0] == 0:
                    break

mg, mn = get_map()
x, y = get_pos()
screenshot("back_on_route101")
print(f"\n[6] At ({x},{y}) Map({mg},{mn})")

# Re-set flags and save
sb1 = read_val("read32", 0x03005AEC)
set_minimal_flags(sb1)
save_slot(8)
print("    Flags set, saved slot 8")

# Now walk extensively
# Walk in the lower part of Route 101 (y 21-26) where grass might be
print("\n[7] Walking on Route 101 (10000 steps, biased south)...")
last_pos = get_pos()
static_count = 0
battle_count = 0
positions = set()

for i in range(10000):
    # Bias toward the lower part of route (south)
    # and use more variety to cover all tiles
    r = random.random()
    if r < 0.30:
        tap("Down", 0.025)
    elif r < 0.55:
        tap("Up", 0.025)
    elif r < 0.70:
        tap("Left", 0.025)
    elif r < 0.85:
        tap("Right", 0.025)
    elif r < 0.92:
        tap("A", 0.025)
    else:
        tap("B", 0.025)

    cur_pos = get_pos()
    positions.add(cur_pos)

    if cur_pos == last_pos:
        static_count += 1
    else:
        static_count = 0
        last_pos = cur_pos

    # Battle detection
    if static_count >= 12:
        print(f"\n    [{i}] Static at {cur_pos}")

        # Check if it's a battle by reading battle flags
        btf = read_val("read32", 0x02022E90)
        if btf > 0 and btf < 0x10000:
            print(f"    Battle flags: {hex(btf)} - BATTLE!")

        # A-spam
        pre_hp = read_val("read16", 0x020241E6)
        for j in range(300):
            tap("A", 0.05)
            if j % 60 == 59:
                tap("B", 0.05)

        post_hp = read_val("read16", 0x020241E6)
        new_pos = get_pos()

        if new_pos != cur_pos or post_hp != pre_hp:
            battle_count += 1
            hp_max = read_val("read16", 0x020241E8)
            level = read_val("read8", 0x020241E4)
            print(f"    BATTLE #{battle_count}! HP:{post_hp}/{hp_max} Lv{level}")
            screenshot(f"battle_{battle_count}")

            if battle_count >= 2:
                for slot in [1, 2, 3]:
                    save_slot(slot)
                print(f"\n    {battle_count} battles! Saved. BOOTSTRAP COMPLETE!")
                sys.exit(0)
        else:
            # Try B spam to dismiss
            for _ in range(30):
                tap("B", 0.08)
            tap("Up", 0.1)
            if get_pos() == cur_pos:
                tap("Down", 0.1)
                if get_pos() == cur_pos:
                    # Actually stuck, reload
                    print("    Stuck, reloading slot 8...")
                    load_slot(8)
                    sb1 = read_val("read32", 0x03005AEC)
                    set_minimal_flags(sb1)

        static_count = 0

    # Stay on Route 101 or nearby outdoor map
    if i % 200 == 199:
        mg, mn = get_map()
        if mg != 0:
            for _ in range(30):
                tap("Down", 0.04)
                if get_map()[0] == 0:
                    break

    if i % 1000 == 999:
        x, y = get_pos()
        mg, mn = get_map()
        screenshot(f"walk_p_{i+1}")
        print(f"    [{i+1}] ({x},{y}) Map({mg},{mn}) tiles={len(positions)} battles={battle_count}")

print(f"\n    Result: {battle_count} battles in 10000 steps, {len(positions)} tiles")
if battle_count > 0:
    for slot in [1, 2, 3]:
        save_slot(slot)
    print("    SAVED!")
else:
    print("    No encounters found.")
    # Print all visited positions
    if positions:
        xs = sorted(set(p[0] for p in positions))
        ys = sorted(set(p[1] for p in positions))
        print(f"    X range: {min(xs)}-{max(xs)}")
        print(f"    Y range: {min(ys)}-{max(ys)}")
