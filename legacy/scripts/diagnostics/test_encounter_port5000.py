"""
Test encounters on port 5000 where player can move freely.
Walk to grass on Route 101 and check for wild encounters.
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

base = "http://localhost:5000"

print(f"{'='*60}", flush=True)
print(f"ENCOUNTER TEST on port 5000", flush=True)
print(f"{'='*60}", flush=True)

# Load the working state
session.post(f'{base}/core/loadstateslot', params={'slot': 1}, timeout=5)
time.sleep(0.5)

sb1 = rval(base, SB1_PTR, 32)
x = rval(base, sb1, 16)
y = rval(base, sb1 + 2, 16)
mg = rval(base, sb1 + 4, 8)
mn = rval(base, sb1 + 5, 8)
print(f"Start: ({x},{y}) Map({mg},{mn})", flush=True)

# Clear wild encounters disabled
w8(base, ADDR_WILD_DISABLED_1, 0)
w8(base, ADDR_WILD_DISABLED_2, 0)

# Walk randomly
prev_x, prev_y = x, y
moved = 0
encounters = 0

for i in range(600):
    if i % 10 == 0:
        w8(base, ADDR_WILD_DISABLED_1, 0)
        w8(base, ADDR_WILD_DISABLED_2, 0)

    # Mix: mostly movement, some A/B
    r = random.random()
    if r < 0.03:
        tap(base, "A", 0.15)
    elif r < 0.05:
        tap(base, "B", 0.15)
    else:
        d = random.choice(["Up", "Down", "Left", "Right"])
        tap(base, d, 0.2)

    cx = rval(base, sb1, 16)
    cy = rval(base, sb1 + 2, 16)
    if cx != prev_x or cy != prev_y:
        moved += 1
        prev_x, prev_y = cx, cy

    btl = rval(base, ADDR_BATTLE_FLAGS, 32)
    if btl and 0 < btl < 0x10000:
        encounters += 1
        cmg = rval(base, sb1 + 4, 8)
        cmn = rval(base, sb1 + 5, 8)
        print(f"\n  *** WILD ENCOUNTER at tap {i}! ***", flush=True)
        print(f"  btl=0x{btl:08X} pos=({cx},{cy}) Map({cmg},{cmn}) moved={moved}", flush=True)

        # Save pre-battle position state
        session.post(f'{base}/core/savestateslot', params={'slot': 2}, timeout=5)
        print(f"  Saved encounter state to slot 2", flush=True)
        break

    if i % 100 == 0:
        cmg = rval(base, sb1 + 4, 8)
        cmn = rval(base, sb1 + 5, 8)
        print(f"  tap={i:3d}: ({cx},{cy}) Map({cmg},{cmn}) moved={moved}", flush=True)

print(f"\n{'='*60}", flush=True)
print(f"RESULT: {moved} moves, {encounters} encounters", flush=True)
print(f"{'='*60}", flush=True)

if encounters > 0:
    print("*** ENCOUNTERS WORK ON PORT 5000! ***", flush=True)
elif moved > 10:
    p = 0.889 ** moved
    print(f"P(0 enc in {moved} grass steps) = {p:.10f}", flush=True)
    if p < 0.001:
        print("Encounters are BROKEN (statistically impossible).", flush=True)
    elif p < 0.05:
        print("Very unlikely. Encounters may be broken.", flush=True)
    else:
        print("Could be bad luck.", flush=True)
else:
    print(f"Only {moved} moves. Player might not be in grass.", flush=True)
