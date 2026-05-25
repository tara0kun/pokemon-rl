"""
Navigate player from current position in Littleroot Town (2,9) Map(0,9)
to Route 101, then test encounters.
Map(0,9) = Littleroot Town, Map(0,16) = Route 101 (north exit)
"""
import requests
import time
import random
import sys

session = requests.Session()
sys.stdout.reconfigure(line_buffering=True)

SB1_PTR = 0x03005AEC
ADDR_BATTLE_FLAGS = 0x02022E90
ADDR_WILD_DISABLED_1 = 0x02038AA4
ADDR_WILD_DISABLED_2 = 0x020397E4

def rval(base, addr, bits=32):
    ep = {8: 'read8', 16: 'read16', 32: 'read32'}[bits]
    try:
        v = session.get(f'{base}/core/{ep}', params={'address': hex(addr)}, timeout=2).json()
        return int(v['value']) if isinstance(v, dict) else int(v)
    except:
        return None

def w8(base, addr, val):
    try:
        session.post(f'{base}/core/write8', params={'address': hex(addr), 'value': val}, timeout=2)
    except:
        pass

def tap(base, button, delay=0.3):
    try:
        session.post(f'{base}/mgba-http/button/tap', params={'button': button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

def get_state(base):
    sb1 = rval(base, SB1_PTR, 32)
    if not sb1 or sb1 < 0x02000000:
        return None
    return {
        'sb1': sb1,
        'x': rval(base, sb1, 16),
        'y': rval(base, sb1 + 2, 16),
        'mg': rval(base, sb1 + 4, 8),
        'mn': rval(base, sb1 + 5, 8),
    }

base = "http://localhost:5000"

print(f"{'='*60}", flush=True)
print(f"NAVIGATE TO ROUTE 101", flush=True)
print(f"{'='*60}", flush=True)

# Load slot 1 (current state after play-through)
session.post(f'{base}/core/loadstateslot', params={'slot': 1}, timeout=5)
time.sleep(0.5)

st = get_state(base)
print(f"Start: ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

# First, let's see if we can move at all
print("\n--- Movement test ---", flush=True)
prev_x, prev_y = st['x'], st['y']
moved = 0
for i in range(20):
    d = random.choice(["Up", "Down", "Left", "Right"])
    tap(base, d, 0.25)
    st = get_state(base)
    if st and (st['x'] != prev_x or st['y'] != prev_y):
        moved += 1
        prev_x, prev_y = st['x'], st['y']

print(f"  20 taps: {moved} moves, at ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

if moved == 0:
    print("  Player can't move. Trying A/B spam...", flush=True)
    for i in range(50):
        tap(base, "A" if i % 2 == 0 else "B", 0.15)
    for i in range(20):
        d = random.choice(["Up", "Down", "Left", "Right"])
        tap(base, d, 0.25)
        st = get_state(base)
        if st and (st['x'] != prev_x or st['y'] != prev_y):
            moved += 1
            prev_x, prev_y = st['x'], st['y']
    print(f"  After A/B: {moved} moves, at ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

if moved == 0:
    print("  STILL CAN'T MOVE. Exiting.", flush=True)
    sys.exit(1)

# Walk north to Route 101
print("\n--- Walking north to Route 101 ---", flush=True)
for i in range(100):
    # Mix: mostly Up, some A for dialogs, some Left/Right to avoid obstacles
    r = random.random()
    if r < 0.7:
        tap(base, "Up", 0.25)
    elif r < 0.8:
        tap(base, "A", 0.15)
    elif r < 0.85:
        tap(base, "B", 0.15)
    elif r < 0.92:
        tap(base, "Left", 0.25)
    else:
        tap(base, "Right", 0.25)

    st = get_state(base)
    if st and (st['x'] != prev_x or st['y'] != prev_y):
        moved += 1
        prev_x, prev_y = st['x'], st['y']

    if i % 20 == 19:
        print(f"  i={i+1}: ({st['x']},{st['y']}) Map({st['mg']},{st['mn']}) moved={moved}", flush=True)

    # Check if we changed maps
    if st and st['mn'] != 9:  # Not Littleroot anymore
        print(f"  MAP CHANGED at i={i}: ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)
        break

st = get_state(base)
print(f"\nCurrent: ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

# Now test encounters with random walk
print(f"\n--- Encounter test (500 taps) ---", flush=True)
prev_x, prev_y = st['x'], st['y']
moved = 0
encounters = 0

for i in range(500):
    if i % 10 == 0:
        w8(base, ADDR_WILD_DISABLED_1, 0)
        w8(base, ADDR_WILD_DISABLED_2, 0)

    d = random.choice(["Up", "Down", "Left", "Right"])
    tap(base, d, 0.2)

    st = get_state(base)
    if st and (st['x'] != prev_x or st['y'] != prev_y):
        moved += 1
        prev_x, prev_y = st['x'], st['y']

    btl = rval(base, ADDR_BATTLE_FLAGS, 32)
    if btl and 0 < btl < 0x10000:
        encounters += 1
        print(f"\n  *** ENCOUNTER at tap {i}! ***", flush=True)
        print(f"  pos=({st['x']},{st['y']}) Map({st['mg']},{st['mn']}) moved={moved}", flush=True)
        break

    if i % 100 == 0:
        print(f"  tap={i:3d}: ({st['x']},{st['y']}) Map({st['mg']},{st['mn']}) moved={moved}", flush=True)

print(f"\n{'='*60}", flush=True)
print(f"RESULT: {moved} moves, {encounters} encounters", flush=True)
print(f"{'='*60}", flush=True)

if encounters > 0:
    print("*** ENCOUNTERS WORK! ***", flush=True)
    # Save this state for all ports
    print("Saving clean state...", flush=True)
    # First run away from battle
    time.sleep(1)
    tap(base, "B", 0.3)
    tap(base, "Down", 0.3)
    tap(base, "Right", 0.3)
    tap(base, "A", 0.5)
    time.sleep(2)
    for _ in range(15):
        tap(base, "A", 0.3)

    session.post(f'{base}/core/savestateslot', params={'slot': 1}, timeout=5)
    session.post(f'{base}/core/savestateslot', params={'slot': 3}, timeout=5)
    print("Saved to slots 1 and 3!", flush=True)
elif moved > 10:
    p = 0.889 ** moved
    print(f"P(0 enc in {moved} steps) = {p:.8f}", flush=True)
    if moved > 20:
        # Save anyway - player can move
        print("Saving movable state...", flush=True)
        session.post(f'{base}/core/savestateslot', params={'slot': 1}, timeout=5)
        session.post(f'{base}/core/savestateslot', params={'slot': 3}, timeout=5)
else:
    print(f"Only {moved} moves - navigation problem.", flush=True)
