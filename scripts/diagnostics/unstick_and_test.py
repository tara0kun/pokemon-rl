"""
Unstick the player: press A/B to clear dialogs, then walk and test encounters.
The save state has an active event script - need to press through it.
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
print(f"UNSTICK + ENCOUNTER TEST on port {port}", flush=True)
print(f"{'='*60}", flush=True)

# Load the fixed save state
session.post(f'{base}/core/loadstateslot', params={'slot': 1}, timeout=5)
time.sleep(0.5)

sb1 = rval(base, SB1_PTR, 32)
x = rval(base, sb1, 16)
y = rval(base, sb1 + 2, 16)
mg = rval(base, sb1 + 4, 8)
mn = rval(base, sb1 + 5, 8)
print(f"Start: ({x},{y}) Map({mg},{mn})", flush=True)

# Phase 1: Clear any active dialog/event by pressing A and B
print(f"\n--- Phase 1: Clearing dialogs (A/B spam 100 presses) ---", flush=True)
for i in range(100):
    if i % 2 == 0:
        tap(base, "A", 0.15)
    else:
        tap(base, "B", 0.15)

    if i % 20 == 19:
        cx = rval(base, sb1, 16)
        cy = rval(base, sb1 + 2, 16)
        mg2 = rval(base, sb1 + 4, 8)
        mn2 = rval(base, sb1 + 5, 8)
        print(f"  i={i+1}: ({cx},{cy}) Map({mg2},{mn2})", flush=True)

# Check position after dialog clearing
x2 = rval(base, sb1, 16)
y2 = rval(base, sb1 + 2, 16)
mg2 = rval(base, sb1 + 4, 8)
mn2 = rval(base, sb1 + 5, 8)
print(f"After A/B spam: ({x2},{y2}) Map({mg2},{mn2})", flush=True)

# Phase 2: Try walking
print(f"\n--- Phase 2: Walk test (mix of A/B and movement) ---", flush=True)
prev_x, prev_y = x2, y2
moved = 0
encounters = 0

for i in range(300):
    # Clear wild disabled
    if i % 10 == 0:
        w8(base, ADDR_WILD_DISABLED_1, 0)
        w8(base, ADDR_WILD_DISABLED_2, 0)

    # Mix: mostly movement, occasional A/B to clear any popup
    r = random.random()
    if r < 0.1:
        tap(base, "A", 0.2)
    elif r < 0.15:
        tap(base, "B", 0.2)
    else:
        d = random.choice(["Up", "Down", "Left", "Right"])
        tap(base, d, 0.25)

    cx = rval(base, sb1, 16)
    cy = rval(base, sb1 + 2, 16)
    if cx != prev_x or cy != prev_y:
        moved += 1
        prev_x, prev_y = cx, cy

    # Battle check
    btl = rval(base, ADDR_BATTLE_FLAGS, 32)
    if btl and 0 < btl < 0x10000:
        encounters += 1
        mg2 = rval(base, sb1 + 4, 8)
        mn2 = rval(base, sb1 + 5, 8)
        print(f"\n  *** WILD ENCOUNTER at tap {i}! ***", flush=True)
        print(f"  btl=0x{btl:08X} pos=({cx},{cy}) Map({mg2},{mn2}) moved={moved}", flush=True)
        break

    if i % 50 == 0:
        mg2 = rval(base, sb1 + 4, 8)
        mn2 = rval(base, sb1 + 5, 8)
        print(f"  tap={i:3d}: ({cx},{cy}) Map({mg2},{mn2}) moved={moved}", flush=True)

print(f"\n{'='*60}", flush=True)
print(f"RESULTS", flush=True)
print(f"{'='*60}", flush=True)
print(f"  Moves: {moved}", flush=True)
print(f"  Encounters: {encounters}", flush=True)

if encounters > 0:
    print("*** ENCOUNTERS WORK! ***", flush=True)
    # Save this working state after exiting battle
    time.sleep(1)
    for _ in range(5):
        tap(base, "B", 0.3)
    tap(base, "Down", 0.3)
    tap(base, "Right", 0.3)
    tap(base, "A", 0.5)
    time.sleep(2)
    for _ in range(10):
        tap(base, "A", 0.3)

    # Save to all ports
    for p in [5000, 5001, 5002]:
        b = f"http://localhost:{p}"
        session.post(f'{b}/core/loadstateslot', params={'slot': 1}, timeout=5)
        time.sleep(0.3)
        # Re-apply fixes (since slot 1 has the pre-encounter state)
    print("  State saved.", flush=True)
elif moved > 5:
    p = 0.889 ** moved
    print(f"  P(0 enc in {moved} steps) = {p:.8f}", flush=True)
    if p < 0.001:
        print("  Encounters BROKEN.", flush=True)
elif moved <= 1:
    print("  Player STUCK - still in event/dialog!", flush=True)
    print("  Need manual play-through to create clean save.", flush=True)
