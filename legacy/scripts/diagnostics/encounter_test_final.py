"""
Final encounter test - walk extensively on Route 101 with all 4 directions.
Don't rely on tileTransitionState address (it's wrong for JP).
Just walk and check if gBattleTypeFlags becomes nonzero.
"""
import requests
import time
import random
import sys

BASE_URL = "http://localhost:5000"
session = requests.Session()
sys.stdout.reconfigure(line_buffering=True)

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

def tap(button, delay=0.35):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/tap", params={"button": button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

SB1_PTR = 0x03005AEC
ADDR_BATTLE_FLAGS = 0x02022E90

# Both candidate addresses for sWildEncountersDisabled
WILD_DISABLED_CANDIDATES = [0x02038AA4, 0x020397E4]

print("=" * 60, flush=True)
print("ENCOUNTER TEST - Extended Walk on Route 101", flush=True)
print("=" * 60, flush=True)

# Load slot 1 (user's clean save state)
print("Loading slot 1...", flush=True)
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

sb1 = read32(SB1_PTR)
x = read16(sb1) if sb1 else 0
y = read16(sb1 + 2) if sb1 else 0
mg = read8(sb1 + 4) if sb1 else 0
mn = read8(sb1 + 5) if sb1 else 0
hp = read16(0x020241E6)
lv = read8(0x020241E4)
print(f"Start: ({x},{y}) Map({mg},{mn}) Lv{lv} HP={hp}", flush=True)

# Clear wild disabled at both candidate addresses
for addr in WILD_DISABLED_CANDIDATES:
    write8(addr, 0)

# Also scan a wider range around the candidates and clear any nonzero bytes
# that could be sWildEncountersDisabled
print("\nScanning EWRAM 0x02038A00-0x02039800 for nonzero bytes...", flush=True)
nonzero_count = 0
for addr in range(0x02038A00, 0x02039900, 1):
    v = read8(addr)
    if v and v > 0:
        nonzero_count += 1
        if nonzero_count <= 20:
            print(f"  0x{addr:08X}: {v} (0x{v:02X})", flush=True)
print(f"Total nonzero bytes: {nonzero_count}", flush=True)

# Walk test with all 4 directions
print(f"\n=== Walking 500 taps (all directions) ===", flush=True)
prev_x, prev_y = x, y
moved = 0
encounters = 0

for i in range(500):
    # Clear wild disabled every 5 steps (at both addresses + old address)
    if i % 5 == 0:
        for addr in WILD_DISABLED_CANDIDATES:
            write8(addr, 0)

    # Random direction - all 4 to avoid getting stuck
    d = random.choice(["Up", "Down", "Left", "Right"])
    tap(d, 0.3)

    # Check position
    sb1 = read32(SB1_PTR)
    cx = read16(sb1) if sb1 else 0
    cy = read16(sb1 + 2) if sb1 else 0
    if cx != prev_x or cy != prev_y:
        moved += 1
        prev_x, prev_y = cx, cy

    # Battle check
    btl = read32(ADDR_BATTLE_FLAGS)
    if btl and 0 < btl < 0x10000:
        encounters += 1
        mg2 = read8(sb1 + 4) if sb1 else 0
        mn2 = read8(sb1 + 5) if sb1 else 0
        print(f"\n  *** WILD ENCOUNTER at tap {i}! ***", flush=True)
        print(f"  btlFlags=0x{btl:08X} pos=({cx},{cy}) Map({mg2},{mn2})", flush=True)
        print(f"  Actual moves: {moved}", flush=True)
        break

    if i % 50 == 0:
        mg2 = read8(sb1 + 4) if sb1 else 0
        mn2 = read8(sb1 + 5) if sb1 else 0
        print(f"  tap={i:3d}: ({cx},{cy}) Map({mg2},{mn2}) moved={moved}", flush=True)

print(f"\n{'=' * 60}", flush=True)
print(f"FINAL RESULTS", flush=True)
print(f"{'=' * 60}", flush=True)
print(f"  Total taps: {min(i+1, 500)}", flush=True)
print(f"  Actual moves: {moved}", flush=True)
print(f"  Encounters: {encounters}", flush=True)

if encounters > 0:
    print(f"\n*** ENCOUNTERS WORK! ***", flush=True)
    # Save this working state
    print(f"  Exiting battle (run away)...", flush=True)
    # Wait a moment for battle to start fully
    time.sleep(1)
    # Press B to cancel any animation, then navigate to Run
    tap("B", 0.3)
    tap("Down", 0.3)
    tap("Right", 0.3)
    tap("A", 0.5)
    time.sleep(2)
    # Press A through any post-battle text
    for _ in range(10):
        tap("A", 0.3)

    # Save state
    print(f"  Saving clean working state...", flush=True)
    session.post(f"{BASE_URL}/core/savestateslot", params={"slot": 1}, timeout=5)
    session.post(f"{BASE_URL}/core/savestateslot", params={"slot": 3}, timeout=5)
    print(f"  Saved to slots 1 and 3!", flush=True)
else:
    # Check encounter rate: with Route 101 rate of ~11%,
    # P(0 encounters in N grass steps) = 0.889^N
    if moved > 0:
        p_zero = 0.889 ** moved
        print(f"\n  P(0 encounters in {moved} grass steps) = {p_zero:.6f}", flush=True)
        if p_zero < 0.001:
            print(f"  This is statistically impossible (<0.1%). Encounters are BROKEN.", flush=True)
        elif p_zero < 0.05:
            print(f"  Very unlikely (<5%). Encounters may be broken.", flush=True)
        else:
            print(f"  Could be bad luck. Try more steps.", flush=True)
