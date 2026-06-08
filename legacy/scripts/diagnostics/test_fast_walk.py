"""
Test: match training conditions (fast taps, no A/B)
Then try creating a clean save state
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

def tap(button, delay=0.0):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/tap", params={"button": button}, timeout=0.5)
    except:
        pass
    if delay > 0:
        time.sleep(delay)

WILD_DISABLED_ADDR = 0x020397E4
SB1_PTR = 0x03005AEC

# === Test 1: Fast taps (matching training conditions) ===
print("=== Test 1: Fast taps (step_delay=0, directions only) ===")
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

sb1 = read32(SB1_PTR)
x = read16(sb1)
y = read16(sb1 + 2)
wild = read8(WILD_DISABLED_ADDR)
print(f"Start: ({x},{y}) wild={wild}")

# Walk with zero delay (like training)
prev_x, prev_y = x, y
moved = 0
directions = ["Up", "Down", "Left", "Right"]
for i in range(200):
    d = random.choice(directions)
    tap(d, 0.0)  # NO delay between taps (like training)

    # Read state occasionally
    if i % 50 == 49:
        cx = read16(sb1)
        cy = read16(sb1 + 2)
        mg = read8(sb1 + 4)
        mn = read8(sb1 + 5)
        w = read8(WILD_DISABLED_ADDR)
        if cx != prev_x or cy != prev_y:
            moved += 1
            prev_x, prev_y = cx, cy
        print(f"  After {i+1} taps: ({cx},{cy}) Map({mg},{mn}) "
              f"moved={moved} wild={w}")

# Wait a few seconds then check
print("\nWaiting 2 seconds for game to process...")
time.sleep(2)
cx = read16(sb1)
cy = read16(sb1 + 2)
mg = read8(sb1 + 4)
mn = read8(sb1 + 5)
w = read8(WILD_DISABLED_ADDR)
print(f"After wait: ({cx},{cy}) Map({mg},{mn}) wild={w}")

# === Test 2: Fast taps then pause to let encounter check fire ===
print("\n=== Test 2: Fast walk then periodic pauses for encounter checks ===")
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

# Clear the wild encounters disabled flag
write8(WILD_DISABLED_ADDR, 0)

sb1 = read32(SB1_PTR)
prev_x = read16(sb1)
prev_y = read16(sb1 + 2)
moved = 0
encounters = 0

for cycle in range(20):
    # Fast walking phase (10 taps, ~0ms each)
    for i in range(10):
        d = random.choice(["Up", "Left", "Right", "Down"])
        tap(d, 0.0)

    # Clear flag
    write8(WILD_DISABLED_ADDR, 0)

    # Pause to let game process (500ms = 30 frames)
    time.sleep(0.5)

    # Read state
    cx = read16(sb1)
    cy = read16(sb1 + 2)
    if cx != prev_x or cy != prev_y:
        moved += 1
        prev_x, prev_y = cx, cy

    btl = read32(0x02022E90)
    if btl and btl > 0:
        encounters += 1
        mg = read8(sb1 + 4)
        mn = read8(sb1 + 5)
        print(f"  *** ENCOUNTER at cycle {cycle}! btl=0x{btl:08X} "
              f"({cx},{cy}) Map({mg},{mn}) ***")
        break

    if cycle % 5 == 0:
        mg = read8(sb1 + 4)
        mn = read8(sb1 + 5)
        w = read8(WILD_DISABLED_ADDR)
        print(f"  Cycle {cycle}: ({cx},{cy}) Map({mg},{mn}) "
              f"moved={moved} wild={w}")

print(f"Result: {moved} moves, {encounters} encounters")

# === Test 3: Create clean state using write8 to force-clear script state ===
print("\n=== Test 3: Force-clear script state + save ===")
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

# Clear sWildEncountersDisabled
write8(WILD_DISABLED_ADDR, 0)

# Try to find and clear the script engine context
# In pokeemerald, gScriptContext1 and gScriptContext2 have mode/stackDepth fields
# Let's scan EWRAM for the script context
# The script context is typically near the beginning of EWRAM allocated variables
# Key pattern: mode byte should be non-zero (1 or 2) with nearby stack depth

# Try known patterns: scan for consecutive non-zero bytes that look like script state
# gScriptContext1.stackDepth (u8), gScriptContext1.mode (u8), etc
print("Scanning for script context patterns...")
for addr in range(0x02038000, 0x0203A000, 1):
    mode = read8(addr)
    if mode in [1, 2]:
        depth = read8(addr - 1) if addr > 0x02038000 else None
        next_b = read8(addr + 1)
        if depth is not None and 0 < depth <= 20 and next_b is not None:
            # Might be a script context
            # Read a few more bytes
            b2 = read8(addr + 2)
            b3 = read8(addr + 3)
            if b2 is not None:
                print(f"  Candidate at 0x{addr:08X}: "
                      f"prev={depth} mode={mode} next={next_b} {b2} {b3}")
                # Try clearing mode to 0
                write8(addr, 0)  # mode = 0 (inactive)

# After clearing script state, check if player can move
time.sleep(1)
write8(WILD_DISABLED_ADDR, 0)

# Test walking with medium delay
prev_x = read16(sb1)
prev_y = read16(sb1 + 2)
moved = 0
for i in range(30):
    tap("Up", 0.2)
    cx = read16(sb1)
    cy = read16(sb1 + 2)
    if cx != prev_x or cy != prev_y:
        moved += 1
        prev_x, prev_y = cx, cy

mg = read8(sb1 + 4)
mn = read8(sb1 + 5)
w = read8(WILD_DISABLED_ADDR)
print(f"After script clear + walk: ({prev_x},{prev_y}) Map({mg},{mn}) "
      f"moved={moved} wild={w}")

if moved > 0 and w == 0:
    print("\n*** Script cleared! Player can move! ***")
    # Save to slot 1
    print("Saving clean state to slot 1...")
    session.post(f"{BASE_URL}/core/savestateslot", params={"slot": 1}, timeout=5)

    # Verify
    session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
    time.sleep(0.5)
    w2 = read8(WILD_DISABLED_ADDR)
    x2 = read16(sb1)
    y2 = read16(sb1 + 2)
    print(f"Verified slot 1: ({x2},{y2}) wild={w2}")
else:
    print("\nScript clearing didn't work. Need manual save state.")
