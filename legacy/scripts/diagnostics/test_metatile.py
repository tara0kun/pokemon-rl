"""
Check if player is actually on grass tiles + try a completely fresh approach
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

def write16(addr, val):
    try:
        session.post(f"{BASE_URL}/core/write16", params={"address": hex(addr), "value": val}, timeout=1)
    except:
        pass

def tap(button, delay=0.3):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/tap", params={"button": button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

WILD_DISABLED_ADDR = 0x020397E4
SB1_PTR = 0x03005AEC

# === Approach: Load the ORIGINAL slot 1 (before our modifications) ===
# The original save state was backed up as slot 2 or we can check slot numbers

# Let's first check what state each slot is in
print("=== Checking all save state slots ===")
for slot in [1, 2, 3, 4, 5]:
    try:
        session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": slot}, timeout=5)
        time.sleep(0.3)
        sb1 = read32(SB1_PTR)
        if sb1 and sb1 > 0x02000000:
            x = read16(sb1)
            y = read16(sb1 + 2)
            mg = read8(sb1 + 4)
            mn = read8(sb1 + 5)
            wild = read8(WILD_DISABLED_ADDR)
            hp = read16(0x020241E6)
            lv = read8(0x020241E4)
            print(f"  Slot {slot}: ({x},{y}) Map({mg},{mn}) wild={wild} Lv{lv} HP={hp}")
        else:
            print(f"  Slot {slot}: Invalid SB1 ({sb1})")
    except Exception as e:
        print(f"  Slot {slot}: Failed ({e})")

# === Try: load original slot and check available mGBA-http endpoints ===
print("\n=== Checking available mGBA-http API endpoints ===")
try:
    r = session.get(f"{BASE_URL}", timeout=2)
    print(f"Root: {r.status_code}")
except:
    pass

# Check if there's a frame advance API
endpoints_to_check = [
    "/core/runFrame",
    "/core/step",
    "/core/advance",
    "/core/advance-frame",
    "/emu/runFrame",
]
for ep in endpoints_to_check:
    try:
        r = session.post(f"{BASE_URL}{ep}", timeout=1)
        print(f"  POST {ep}: {r.status_code} - {r.text[:100]}")
    except Exception as e:
        print(f"  POST {ep}: {e}")

# === Try loading slot 1 and checking the exact Wild Encounter related addresses ===
print("\n=== Detailed Wild Encounter State ===")
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

sb1 = read32(SB1_PTR)

# Check all bytes around the sWildEncountersDisabled address
print(f"Bytes around 0x020397E4 (sWildEncountersDisabled candidate):")
for addr in range(0x020397D0, 0x020397F0):
    v = read8(addr)
    marker = " <<<" if addr == WILD_DISABLED_ADDR else ""
    if v is not None and v != 0:
        print(f"  0x{addr:08X}: {v} (0x{v:02X}){marker}")

# Check sWildEncounterImmunitySteps (should be near sWildEncountersDisabled)
print(f"\nBytes around +/- 16 of candidate:")
for offset in range(-16, 17):
    addr = WILD_DISABLED_ADDR + offset
    v = read8(addr)
    if v is not None:
        marker = " <<< sWildEncountersDisabled?" if offset == 0 else ""
        print(f"  0x{addr:08X} (+{offset:+d}): {v} (0x{v:02X}){marker}")

# === Try: Load original state, then save clean state using in-game save ===
# Use the in-game save system to create a clean starting point
print("\n=== Approach: Use in-game continue to reset script state ===")
print("Loading slot 1 (original), then triggering in-game continue...")

# Load original save state
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(1)

# Check the gMain.callback state
# In pokeemerald, during overworld, gMain.callback2 = CB2_Overworld
# After a title screen/continue, the game reinitializes the overworld

# Let's try soft-resetting the game (A+B+Start+Select)
print("Attempting soft reset (A+B+Start+Select)...")
# We need to hold all 4 buttons simultaneously
# mGBA-http tap sends one button. We need hold.
try:
    session.post(f"{BASE_URL}/mgba-http/button/hold", params={"button": "A"}, timeout=0.5)
    session.post(f"{BASE_URL}/mgba-http/button/hold", params={"button": "B"}, timeout=0.5)
    session.post(f"{BASE_URL}/mgba-http/button/hold", params={"button": "Start"}, timeout=0.5)
    session.post(f"{BASE_URL}/mgba-http/button/hold", params={"button": "Select"}, timeout=0.5)
    time.sleep(2)  # Hold for 2 seconds
    session.post(f"{BASE_URL}/mgba-http/button/releaseall", timeout=0.5)
except Exception as e:
    print(f"  Hold/release error: {e}")

time.sleep(3)

sb1 = read32(SB1_PTR)
if sb1 and sb1 > 0x02000000:
    x = read16(sb1)
    y = read16(sb1 + 2)
    mg = read8(sb1 + 4)
    mn = read8(sb1 + 5)
    wild = read8(WILD_DISABLED_ADDR)
    print(f"After soft reset: ({x},{y}) Map({mg},{mn}) wild={wild}")
else:
    wild = read8(WILD_DISABLED_ADDR)
    print(f"After soft reset: SB1={sb1} wild={wild}")

# Now try pressing A through the title screen
print("\nAdvancing through title screen...")
for i in range(30):
    tap("A", 0.5)
    if i % 10 == 9:
        sb1 = read32(SB1_PTR)
        if sb1 and sb1 > 0x02000000:
            x = read16(sb1)
            y = read16(sb1 + 2)
            mg = read8(sb1 + 4)
            mn = read8(sb1 + 5)
            wild = read8(WILD_DISABLED_ADDR)
            print(f"  Press {i+1}: ({x},{y}) Map({mg},{mn}) wild={wild}")
        else:
            print(f"  Press {i+1}: SB1={sb1}")

# Check final state
sb1 = read32(SB1_PTR)
if sb1 and sb1 > 0x02000000:
    x = read16(sb1)
    y = read16(sb1 + 2)
    mg = read8(sb1 + 4)
    mn = read8(sb1 + 5)
    wild = read8(WILD_DISABLED_ADDR)
    hp = read16(0x020241E6)
    lv = read8(0x020241E4)
    print(f"\nFinal state: ({x},{y}) Map({mg},{mn}) wild={wild} Lv{lv} HP={hp}")

    if wild == 0:
        print("\n*** ENCOUNTERS ENABLED! Testing walk... ***")
        prev_x, prev_y = x, y
        moved = 0
        encounter = False
        for i in range(100):
            d = random.choice(["Up", "Down", "Left", "Right"])
            tap(d, 0.25)
            cx = read16(sb1)
            cy = read16(sb1 + 2)
            if cx != prev_x or cy != prev_y:
                moved += 1
                prev_x, prev_y = cx, cy
            btl = read32(0x02022E90)
            if btl and btl > 0:
                encounter = True
                print(f"  ENCOUNTER at step {i}! moved={moved}")
                break
            if i % 25 == 0:
                mg2 = read8(sb1 + 4)
                mn2 = read8(sb1 + 5)
                print(f"  Step {i}: ({cx},{cy}) Map({mg2},{mn2}) moved={moved}")

        if encounter:
            print("\n*** ENCOUNTERS WORK AFTER CONTINUE! ***")
            print("Saving to slot 1...")
            # Navigate to Route 101 first if not there
        else:
            print(f"\nStill no encounters after {moved} steps")
