"""
Walk NORTH to Route 101 from Littleroot Town, then test encounters in grass.
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

port = 5001
base = f"http://localhost:{port}"

print(f"{'='*60}", flush=True)
print(f"WALK NORTH TO ROUTE 101 TEST on port {port}", flush=True)
print(f"{'='*60}", flush=True)

# Load fixed save state
session.post(f'{base}/core/loadstateslot', params={'slot': 1}, timeout=5)
time.sleep(0.5)

sb1 = rval(base, SB1_PTR, 32)
x = rval(base, sb1, 16)
y = rval(base, sb1 + 2, 16)
mg = rval(base, sb1 + 4, 8)
mn = rval(base, sb1 + 5, 8)
print(f"Start: ({x},{y}) Map({mg},{mn})", flush=True)

# Phase 1: Clear dialogs
print(f"\n--- Phase 1: Clear dialogs ---", flush=True)
for i in range(50):
    tap(base, "A" if i % 2 == 0 else "B", 0.15)

# Phase 2: Walk NORTH (Up) to exit Littleroot Town to Route 101
print(f"\n--- Phase 2: Walk North to Route 101 ---", flush=True)
prev_mg = mg
prev_mn = mn
for i in range(100):
    # Alternate between Up and A/B to handle any dialog triggers
    if i % 5 < 4:
        tap(base, "Up", 0.25)
    else:
        tap(base, "A", 0.15)

    cx = rval(base, sb1, 16)
    cy = rval(base, sb1 + 2, 16)
    cmg = rval(base, sb1 + 4, 8)
    cmn = rval(base, sb1 + 5, 8)

    if i % 10 == 0 or (cmg != prev_mg or cmn != prev_mn):
        print(f"  i={i:3d}: ({cx},{cy}) Map({cmg},{cmn})", flush=True)

    if cmg != prev_mg or cmn != prev_mn:
        print(f"  *** MAP CHANGED! Now on Map({cmg},{cmn}) ***", flush=True)
        prev_mg, prev_mn = cmg, cmn

# Current position
cx = rval(base, sb1, 16)
cy = rval(base, sb1 + 2, 16)
cmg = rval(base, sb1 + 4, 8)
cmn = rval(base, sb1 + 5, 8)
print(f"\nAfter walking north: ({cx},{cy}) Map({cmg},{cmn})", flush=True)

# Phase 3: Random walk for encounters
print(f"\n--- Phase 3: Random walk for encounters ({cmg},{cmn}) ---", flush=True)
w8(base, ADDR_WILD_DISABLED_1, 0)
w8(base, ADDR_WILD_DISABLED_2, 0)

prev_x, prev_y = cx, cy
moved = 0
encounters = 0

for i in range(500):
    if i % 10 == 0:
        w8(base, ADDR_WILD_DISABLED_1, 0)
        w8(base, ADDR_WILD_DISABLED_2, 0)

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
        break

    if i % 100 == 0:
        cmg = rval(base, sb1 + 4, 8)
        cmn = rval(base, sb1 + 5, 8)
        print(f"  tap={i:3d}: ({cx},{cy}) Map({cmg},{cmn}) moved={moved}", flush=True)

print(f"\n{'='*60}", flush=True)
print(f"RESULTS: {moved} moves, {encounters} encounters", flush=True)
print(f"{'='*60}", flush=True)

if encounters > 0:
    print("*** ENCOUNTERS WORK! ***", flush=True)
elif moved > 5:
    p = 0.889 ** moved
    print(f"P(0 enc in {moved} steps) = {p:.8f}", flush=True)
else:
    print("Player barely moved. Still stuck or in wrong area.", flush=True)
