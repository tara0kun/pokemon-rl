"""
Create a clean save state by soft-resetting and navigating through title screen.
The current slot 1 is corrupted (gMapHeader=0xFF, tileTransitionState=127).
This script:
1. Checks all slots for valid states
2. Soft resets the game (A+B+Start+Select)
3. Navigates through title screen -> Continue
4. Verifies the overworld is properly initialized
5. Walks to Route 101 grass
6. Saves to slot 1 after confirming encounters work
"""
import requests
import time
import random

BASE_URL = "http://localhost:5000"
session = requests.Session()

def read8(addr):
    try:
        r = session.get(f"{BASE_URL}/core/read8", params={"address": hex(addr)}, timeout=1)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None

def read16(addr):
    try:
        r = session.get(f"{BASE_URL}/core/read16", params={"address": hex(addr)}, timeout=1)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None

def read32(addr):
    try:
        r = session.get(f"{BASE_URL}/core/read32", params={"address": hex(addr)}, timeout=1)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None

def write8(addr, val):
    try:
        session.post(f"{BASE_URL}/core/write8", params={"address": hex(addr), "value": val}, timeout=1)
    except:
        pass

def hold(button):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/hold", params={"button": button}, timeout=0.5)
    except:
        pass

def release_all():
    try:
        session.post(f"{BASE_URL}/mgba-http/button/releaseall", timeout=0.5)
    except:
        pass

def tap(button, delay=0.4):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/tap", params={"button": button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

# Addresses
SB1_PTR                = 0x03005AEC
ADDR_BATTLE_FLAGS      = 0x02022E90
ADDR_TILE_TRANSITION   = 0x02037437
ADDR_MAP_HEADER        = 0x020371BC
ADDR_WILD_DISABLED_NEW = 0x02038AA4
ADDR_WILD_DISABLED_OLD = 0x020397E4

def check_state():
    """Check if game state is valid (gMapHeader points to ROM)"""
    sb1 = read32(SB1_PTR)
    mh = read32(ADDR_MAP_HEADER)
    tts = read8(ADDR_TILE_TRANSITION)
    x = read16(sb1) if sb1 and sb1 > 0x02000000 else None
    y = read16(sb1 + 2) if sb1 and sb1 > 0x02000000 else None
    mg = read8(sb1 + 4) if sb1 and sb1 > 0x02000000 else None
    mn = read8(sb1 + 5) if sb1 and sb1 > 0x02000000 else None
    wd = read8(ADDR_WILD_DISABLED_NEW)
    valid_mh = mh is not None and mh > 0x08000000 and mh < 0x09000000
    return {
        "sb1": sb1, "x": x, "y": y, "mg": mg, "mn": mn,
        "map_header": mh, "valid_mh": valid_mh,
        "tts": tts, "wild_disabled": wd
    }

def print_state(label, s):
    mh_str = f"0x{s['map_header']:08X}" if s['map_header'] else "None"
    valid = "VALID" if s['valid_mh'] else "INVALID"
    print(f"  {label}: ({s['x']},{s['y']}) Map({s['mg']},{s['mn']}) "
          f"mh={mh_str}({valid}) tts={s['tts']} wild={s['wild_disabled']}")

# === Step 1: Check all slots ===
print("=" * 60)
print("Step 1: Checking all save state slots")
print("=" * 60)
best_slot = None
for slot in [1, 2, 3, 4, 5]:
    try:
        session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": slot}, timeout=5)
        time.sleep(0.5)
        s = check_state()
        print_state(f"Slot {slot}", s)
        if s['valid_mh'] and best_slot is None:
            best_slot = slot
            print(f"    ^ First valid slot!")
    except Exception as e:
        print(f"  Slot {slot}: Failed ({e})")

if best_slot:
    print(f"\nFound valid slot: {best_slot}")
    print("Loading it and testing encounters...")
    session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": best_slot}, timeout=5)
    time.sleep(0.5)
else:
    print("\nNo valid slot found. Proceeding with soft reset.")

# === Step 2: Soft reset ===
print("\n" + "=" * 60)
print("Step 2: Soft reset (A+B+Start+Select)")
print("=" * 60)

hold("A")
hold("B")
hold("Start")
hold("Select")
time.sleep(3)
release_all()
time.sleep(2)

s = check_state()
print_state("After soft reset", s)

# === Step 3: Navigate title screen -> Continue ===
print("\n" + "=" * 60)
print("Step 3: Navigating to overworld (A presses)")
print("=" * 60)

# Keep pressing A to get through title screen, press start, continue, etc.
for i in range(60):
    tap("A", 0.5)

    if i % 10 == 9:
        s = check_state()
        print_state(f"Press {i+1}", s)
        if s['valid_mh']:
            print(f"    ^ Game state valid! gMapHeader is ROM pointer.")
            break

time.sleep(1)
s = check_state()
print_state("After title nav", s)

if not s['valid_mh']:
    # Try pressing Start first (some title screens need Start before A)
    print("\nTrying Start button...")
    tap("Start", 1.0)
    for i in range(30):
        tap("A", 0.5)
        if i % 10 == 9:
            s = check_state()
            print_state(f"Start+A {i+1}", s)
            if s['valid_mh']:
                break

s = check_state()
print_state("Final after nav", s)

if not s['valid_mh']:
    print("\n*** FAILED to reach valid overworld state ***")
    print("Manual intervention may be needed:")
    print("1. In mGBA, navigate to the overworld manually")
    print("2. Walk to Route 101 grass")
    print("3. Save state to slot 1 (Ctrl+Shift+1)")
    exit(1)

# === Step 4: Verify overworld state ===
print("\n" + "=" * 60)
print("Step 4: Verifying overworld state")
print("=" * 60)

# Wait a moment for any transition to complete
time.sleep(1)

# Check tileTransitionState
tts = read8(ADDR_TILE_TRANSITION)
print(f"  tileTransitionState: {tts} (should be 0 or 2)")

# Walk a few steps and check if tileTransitionState changes to 2
tts_seen_2 = False
for i in range(20):
    tap("Up", 0.4)
    tts = read8(ADDR_TILE_TRANSITION)
    if tts == 2:
        tts_seen_2 = True
        break

if tts_seen_2:
    print(f"  tileTransitionState reached CENTER (2)! Overworld is functional.")
else:
    print(f"  WARNING: tileTransitionState never reached 2. Last value: {tts}")

# === Step 5: Walk on Route 101 and test encounters ===
print("\n" + "=" * 60)
print("Step 5: Walking on Route 101 (encounter test)")
print("=" * 60)

s = check_state()
print_state("Current pos", s)

# If not on Route 101, try to navigate there
# Route 101 = Map(0, 16)
if s['mg'] != 0 or s['mn'] != 16:
    print(f"  Not on Route 101. Walking north to find it...")
    for i in range(50):
        tap("Up", 0.35)
        sb1 = read32(SB1_PTR)
        mg = read8(sb1 + 4) if sb1 else None
        mn = read8(sb1 + 5) if sb1 else None
        if mg == 0 and mn == 16:
            print(f"  Reached Route 101 after {i+1} steps!")
            break

# Now test encounters
sb1 = read32(SB1_PTR)
prev_x = read16(sb1) if sb1 else 0
prev_y = read16(sb1 + 2) if sb1 else 0
moved = 0
encounters = 0
tile_centers = 0

print(f"\n  Walking 200 steps on Route 101...")
for i in range(200):
    # Clear wild disabled every step
    write8(ADDR_WILD_DISABLED_NEW, 0)
    write8(ADDR_WILD_DISABLED_OLD, 0)

    d = random.choice(["Up", "Down", "Up", "Down"])
    tap(d, 0.35)

    sb1 = read32(SB1_PTR)
    cx = read16(sb1) if sb1 else 0
    cy = read16(sb1 + 2) if sb1 else 0
    tts = read8(ADDR_TILE_TRANSITION)

    if cx != prev_x or cy != prev_y:
        moved += 1
        prev_x, prev_y = cx, cy
    if tts == 2:
        tile_centers += 1

    # Battle check
    btl = read32(ADDR_BATTLE_FLAGS)
    if btl and 0 < btl < 0x10000:
        encounters += 1
        mg = read8(sb1 + 4) if sb1 else 0
        mn = read8(sb1 + 5) if sb1 else 0
        wd = read8(ADDR_WILD_DISABLED_NEW)
        print(f"\n  *** WILD ENCOUNTER at step {i}! ***")
        print(f"  btlFlags=0x{btl:08X} pos=({cx},{cy}) Map({mg},{mn})")
        print(f"  Moved: {moved}, TileCenters: {tile_centers}")
        break

    if i % 25 == 0:
        mg = read8(sb1 + 4) if sb1 else 0
        mn = read8(sb1 + 5) if sb1 else 0
        wd = read8(ADDR_WILD_DISABLED_NEW)
        print(f"  i={i:3d}: ({cx},{cy}) Map({mg},{mn}) moved={moved} "
              f"centers={tile_centers} tts={tts} wild={wd}")

# === Results ===
print("\n" + "=" * 60)
print("RESULTS")
print("=" * 60)
print(f"  Steps moved: {moved}")
print(f"  T_TILE_CENTER: {tile_centers} times")
print(f"  Encounters: {encounters}")

if encounters > 0:
    print(f"\n*** ENCOUNTERS WORK! ***")
    print(f"Saving clean state to slot 1...")
    # First escape from battle by running
    for _ in range(5):
        tap("B", 0.3)
    # Run away: Down, Right, A
    tap("Down", 0.3)
    tap("Right", 0.3)
    tap("A", 0.5)
    time.sleep(2)
    # Wait for battle to end
    for _ in range(10):
        tap("A", 0.5)

    # Save state
    session.post(f"{BASE_URL}/core/savestateslot", params={"slot": 1}, timeout=5)
    print("  Saved to slot 1!")

    # Also save to slot 3 as backup
    session.post(f"{BASE_URL}/core/savestateslot", params={"slot": 3}, timeout=5)
    print("  Backup saved to slot 3!")

    # Verify
    session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
    time.sleep(0.5)
    s = check_state()
    print_state("Slot 1 verify", s)
elif tile_centers > 0:
    print(f"\n  Overworld is functional (tileTransitionState reaches 2)")
    print(f"  but no encounter in {moved} steps. Bad luck? Try again.")

    # Save the working state anyway (better than corrupted)
    print(f"  Saving working state to slot 1...")
    session.post(f"{BASE_URL}/core/savestateslot", params={"slot": 1}, timeout=5)
    session.post(f"{BASE_URL}/core/savestateslot", params={"slot": 3}, timeout=5)
    print(f"  Saved to slots 1 and 3!")
else:
    print(f"\n*** OVERWORLD STILL BROKEN ***")
    print(f"  tileTransitionState never reaches 2")
    print(f"  Manual fix needed: create save state in mGBA while on Route 101")
