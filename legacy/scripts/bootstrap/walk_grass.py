"""
Walk extensively on Route 101 to trigger wild encounters.
Strategy:
1. Leave Route 101 (go south to Littleroot)
2. Re-enter Route 101 (reload encounter tables)
3. Walk 5000+ steps covering the entire route
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


def tap(button, delay=0.04):
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


x, y = get_pos()
mg, mn = get_map()
print(f"Start: ({x},{y}) Map({mg},{mn})")

# Set flags
sb1 = read_val("read32", 0x03005AEC)
set_minimal_flags(sb1)
print("Flags set")

# Step 1: Walk south to exit Route 101 (enter Littleroot)
print("\n[1] Walking south to Littleroot Town...")
for i in range(200):
    tap("Down", 0.03)
    mg, mn = get_map()
    if (mg, mn) != (0, 16):
        x, y = get_pos()
        print(f"    Left Route 101 at step {i}: ({x},{y}) Map({mg},{mn})")
        break

# Dismiss any dialogue
for _ in range(20):
    tap("A", 0.08)
    tap("B", 0.08)

mg, mn = get_map()
x, y = get_pos()
print(f"    Now at ({x},{y}) Map({mg},{mn})")

# Re-set flags (may have been cleared by map transition)
sb1 = read_val("read32", 0x03005AEC)
set_minimal_flags(sb1)

# Step 2: Walk north back to Route 101
print("\n[2] Walking north to re-enter Route 101...")
for i in range(200):
    tap("Up", 0.03)
    mg, mn = get_map()
    if (mg, mn) == (0, 16):
        x, y = get_pos()
        print(f"    Entered Route 101 at step {i}: ({x},{y})")
        break
    # If entered building, go down
    if mg != 0:
        for _ in range(20):
            tap("Down", 0.04)
            if get_map()[0] == 0:
                break

mg, mn = get_map()
if (mg, mn) != (0, 16):
    print(f"    Could not re-enter Route 101, at Map({mg},{mn})")
    # Try from different position
    for _ in range(200):
        d = random.choice(["Up", "Up", "Up", "Left", "Right"])
        tap(d, 0.03)
        mg, mn = get_map()
        if (mg, mn) == (0, 16):
            x, y = get_pos()
            print(f"    Found Route 101 at ({x},{y})")
            break

mg, mn = get_map()
if (mg, mn) != (0, 16):
    print(f"    FAILED: at Map({mg},{mn})")
    screenshot("fail_walk")
    sys.exit(1)

# Re-set flags again
sb1 = read_val("read32", 0x03005AEC)
set_minimal_flags(sb1)

# Dismiss any dialogue from re-entry
for _ in range(30):
    tap("A", 0.08)
    tap("B", 0.08)

x, y = get_pos()
print(f"    On Route 101 at ({x},{y})")
screenshot("route101_reenter")

# Save safe point
save_slot(8)
print("    Saved slot 8")

# Step 3: Walk extensively on Route 101
print("\n[3] Extensive walking on Route 101 (5000 steps)...")
last_pos = get_pos()
static_count = 0
battle_count = 0
positions_visited = set()

for i in range(5000):
    # Walk pattern: cover the full route systematically
    # Route 101 X: 11-18, Y: 14-26
    # Walk in zigzag patterns to cover all tiles
    phase = (i // 100) % 4
    if phase == 0:
        # Walk up
        tap("Up", 0.025)
    elif phase == 1:
        # Walk right then up
        tap(random.choice(["Right", "Up", "Up"]), 0.025)
    elif phase == 2:
        # Walk down
        tap("Down", 0.025)
    elif phase == 3:
        # Walk left then down
        tap(random.choice(["Left", "Down", "Down"]), 0.025)

    # Add some randomness every 20 steps
    if i % 20 == 19:
        tap(random.choice(["Left", "Right", "Up", "Down"]), 0.025)

    cur_pos = get_pos()
    positions_visited.add(cur_pos)

    if cur_pos == last_pos:
        static_count += 1
    else:
        static_count = 0
        last_pos = cur_pos

    # Battle detection: position static for 12+ steps
    if static_count >= 12:
        print(f"\n    [{i}] STATIC at {cur_pos} for {static_count} steps!")
        screenshot(f"battle_detect_{i}")

        # A-spam to handle battle
        pre_hp = read_val("read16", 0x020241E6)
        for j in range(400):
            tap("A", 0.05)
            if j % 80 == 79:
                tap("B", 0.05)

        new_pos = get_pos()
        post_hp = read_val("read16", 0x020241E6)
        hp_max = read_val("read16", 0x020241E8)
        level = read_val("read8", 0x020241E4)

        if new_pos != cur_pos or post_hp != pre_hp:
            battle_count += 1
            print(f"    BATTLE #{battle_count}! HP:{post_hp}/{hp_max} Lv{level}")
            print(f"    Now at {new_pos}")
            screenshot(f"after_battle_{battle_count}")

            if battle_count >= 2:
                print(f"\n[4] {battle_count} battles! Saving to slots 1,2,3...")
                for slot in [1, 2, 3]:
                    save_slot(slot)
                screenshot("success")
                print("    BOOTSTRAP COMPLETE!")
                sys.exit(0)
        else:
            # Might be dialogue or menu
            for _ in range(100):
                tap("A", 0.06)
                tap("B", 0.06)
            # Check if still stuck
            tap("Up", 0.1)
            test = get_pos()
            if test == cur_pos:
                print("    Still stuck! Loading slot 8...")
                session.post(f"{MGBA_URL}/core/loadstateslot", params={"slot": 8}, timeout=5)
                time.sleep(0.5)
                sb1 = read_val("read32", 0x03005AEC)
                set_minimal_flags(sb1)

        static_count = 0

    # Map check: if left Route 101, return
    if i % 100 == 99:
        mg, mn = get_map()
        if (mg, mn) != (0, 16):
            if mg != 0:
                for _ in range(30):
                    tap("Down", 0.04)
                    if get_map()[0] == 0:
                        break
            # Walk north to return
            for _ in range(100):
                tap("Up", 0.03)
                if get_map() == (0, 16):
                    break

    if i % 500 == 499:
        x, y = get_pos()
        mg, mn = get_map()
        hp = read_val("read16", 0x020241E6)
        screenshot(f"walk_{i+1}")
        print(f"    [{i+1}] ({x},{y}) Map({mg},{mn}) HP:{hp} tiles:{len(positions_visited)} battles:{battle_count}")

print(f"\n    Done: {battle_count} battles in 5000 steps")
print(f"    Unique tiles: {len(positions_visited)}")
if positions_visited:
    xs = [p[0] for p in positions_visited]
    ys = [p[1] for p in positions_visited]
    print(f"    X: {min(xs)}-{max(xs)}, Y: {min(ys)}-{max(ys)}")
screenshot("final_walk")

if battle_count > 0:
    for slot in [1, 2, 3]:
        save_slot(slot)
    print("    Saved. BOOTSTRAP COMPLETE!")
else:
    print("    NO ENCOUNTERS! Possible issues:")
    print("    - Wrong grass tile locations")
    print("    - Missing encounter table flag")
    print("    - JP-specific flag not set")
