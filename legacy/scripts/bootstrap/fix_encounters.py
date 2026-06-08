"""
Fix wild encounters by doing an in-game save then soft reset.
This clears all RAM variables (like sWildEncountersDisabled) while
preserving the game save data.

Plan:
1. Load save state 8 (on Route 101)
2. Set flags via memory
3. Save in-game (Start menu -> Save)
4. Soft reset via A+B+Start+Select
5. Continue game from title screen
6. Walk on Route 101 to verify encounters work
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


def hold(button, frames=10):
    """Hold a button for multiple frames."""
    try:
        session.post(f"{MGBA_URL}/mgba-http/button/hold", params={"button": button}, timeout=3)
    except:
        pass
    time.sleep(frames * 0.017)  # ~60fps
    try:
        session.post(f"{MGBA_URL}/mgba-http/button/release", params={"button": button}, timeout=3)
    except:
        pass


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


print("=" * 50)
print("  Fix Encounters via In-Game Save + Soft Reset")
print("=" * 50)

# Step 1: Load slot 8 (Route 101)
print("\n[1] Loading slot 8...")
session.post(f"{MGBA_URL}/core/loadstateslot", params={"slot": 8}, timeout=5)
time.sleep(1)

x, y = get_pos()
mg, mn = get_map()
print(f"    At ({x},{y}) Map({mg},{mn})")

# Set flags
sb1 = read_val("read32", 0x03005AEC)
set_minimal_flags(sb1)
print("    Flags set")

# Dismiss any dialogue
for _ in range(20):
    tap("A", 0.08)
    tap("B", 0.08)

# Step 2: In-game save
print("\n[2] Saving in-game...")
# Press Start to open menu
tap("Start", 0.5)
screenshot("menu_start")

# The save option in Emerald's menu:
# Menu items (JP): ずかん / ポケモン / バッグ / プレイヤー名 / レポート / オプション
# "レポート" (Report/Save) is typically the 5th option
# Navigate down to Save option
for _ in range(4):
    tap("Down", 0.3)

tap("A", 0.5)  # Select Save
screenshot("save_prompt")

# Confirm save (press A on "はい"/Yes)
tap("A", 0.5)
time.sleep(1)
screenshot("saving")

# Wait for save to complete
time.sleep(2)
tap("A", 0.5)
tap("A", 0.5)
screenshot("save_done")

# Close menu
tap("B", 0.3)
tap("B", 0.3)

print("    In-game save complete (hopefully)")

# Step 3: Soft reset via A+B+Start+Select
print("\n[3] Soft reset (A+B+Start+Select)...")
# Hold all four buttons simultaneously
# The mGBA-http API might need buttons pressed at same time
# Let's try holding them all
try:
    for btn in ["A", "B", "Start", "Select"]:
        session.post(f"{MGBA_URL}/mgba-http/button/hold", params={"button": btn}, timeout=3)
    time.sleep(0.5)  # Hold for half a second
    for btn in ["A", "B", "Start", "Select"]:
        session.post(f"{MGBA_URL}/mgba-http/button/release", params={"button": btn}, timeout=3)
except:
    pass

time.sleep(3)  # Wait for reset
screenshot("after_reset")

# Check what's on screen
x, y = get_pos()
mg, mn = get_map()
print(f"    After reset: ({x},{y}) Map({mg},{mn})")

# Step 4: Navigate title screen
print("\n[4] Navigating title screen...")
# Press A/Start repeatedly to get past title
for i in range(30):
    tap("A", 0.3)
    if i % 5 == 4:
        tap("Start", 0.3)

    # Check if we're in-game
    sb1 = read_val("read32", 0x03005AEC)
    if sb1 > 0x02000000 and sb1 < 0x0203FFFF:
        mg, mn = get_map()
        if mg >= 0:
            x, y = get_pos()
            print(f"    In game at ({x},{y}) Map({mg},{mn})")
            break

screenshot("after_title")
x, y = get_pos()
mg, mn = get_map()
print(f"    Current: ({x},{y}) Map({mg},{mn})")

# If in a building, exit
if mg != 0:
    print("    In building, exiting...")
    for _ in range(50):
        tap("Down", 0.04)
        if get_map()[0] == 0:
            break
    x, y = get_pos()
    mg, mn = get_map()
    print(f"    Now: ({x},{y}) Map({mg},{mn})")

# Step 5: Set flags again (they're in save data now)
sb1 = read_val("read32", 0x03005AEC)
set_minimal_flags(sb1)

# Step 6: Navigate to Route 101
mg, mn = get_map()
if (mg, mn) != (0, 16):
    print("\n[5] Walking to Route 101...")
    for i in range(500):
        r = random.random()
        if r < 0.6:
            tap("Up", 0.03)
        elif r < 0.75:
            tap("Left", 0.03)
        elif r < 0.90:
            tap("Right", 0.03)
        else:
            tap("Down", 0.03)

        mg, mn = get_map()
        if (mg, mn) == (0, 16):
            x, y = get_pos()
            print(f"    Entered Route 101 at ({x},{y})")
            break
        if mg != 0:
            for _ in range(20):
                tap("Down", 0.04)
                if get_map()[0] == 0:
                    break

mg, mn = get_map()
x, y = get_pos()
print(f"\n[6] At ({x},{y}) Map({mg},{mn})")

if (mg, mn) != (0, 16):
    print("    Not on Route 101, but let's try walking anyway...")

# Save current state
save_slot(7)
print("    Saved slot 7")

# Step 7: Walk and check for encounters
print("\n[7] Walking to test encounters (5000 steps)...")
last_pos = get_pos()
static_count = 0
battle_count = 0

for i in range(5000):
    # Walk in all directions to cover area
    d = random.choice(["Up", "Down", "Left", "Right", "Up", "Down"])
    tap(d, 0.025)

    cur_pos = get_pos()
    if cur_pos == last_pos:
        static_count += 1
    else:
        static_count = 0
        last_pos = cur_pos

    if static_count >= 15:
        screenshot(f"enc_static_{i}")
        print(f"    [{i}] STATIC at {cur_pos}!")

        # A-spam for battle
        pre_hp = read_val("read16", 0x020241E6)
        for j in range(400):
            tap("A", 0.05)
            if j % 80 == 79:
                tap("B", 0.05)

        post_hp = read_val("read16", 0x020241E6)
        new_pos = get_pos()
        new_map = get_map()

        if new_pos != cur_pos or post_hp != pre_hp:
            battle_count += 1
            hp_max = read_val("read16", 0x020241E8)
            level = read_val("read8", 0x020241E4)
            print(f"    BATTLE #{battle_count}! HP:{post_hp}/{hp_max} Lv{level} at {new_pos}")

            if battle_count >= 2:
                print(f"\n    {battle_count} battles confirmed!")
                for slot in [1, 2, 3]:
                    save_slot(slot)
                screenshot("encounters_working")
                print("    Saved to slots 1,2,3. BOOTSTRAP COMPLETE!")
                sys.exit(0)
        else:
            # Try to unstick
            for _ in range(50):
                tap("A", 0.06)
                tap("B", 0.06)

        static_count = 0

    # Keep on current outdoor map
    if i % 100 == 99:
        mg, mn = get_map()
        if mg != 0:
            for _ in range(30):
                tap("Down", 0.04)
                if get_map()[0] == 0:
                    break

    if i % 1000 == 999:
        x, y = get_pos()
        mg, mn = get_map()
        screenshot(f"enc_progress_{i+1}")
        print(f"    [{i+1}] ({x},{y}) Map({mg},{mn}) battles={battle_count}")

print(f"\n    Done: {battle_count} battles in 5000 steps")
screenshot("enc_final")

if battle_count == 0:
    print("    Still no encounters! The issue may be:")
    print("    - Need to scan for sWildEncountersDisabled address")
    print("    - Need to find correct grass tiles")
    print("    - JP ROM may have different encounter system")
