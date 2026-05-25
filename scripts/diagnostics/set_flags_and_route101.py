"""
Set flags on running game state (NOT during an event), then walk to Route 101.
The game is in normal overworld mode, so flag changes are safe.
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

def w16(base, addr, val):
    try:
        session.post(f'{base}/core/write16', params={'address': hex(addr), 'value': val}, timeout=2)
    except:
        pass

def set_flag(base, flags_base, flag_id):
    byte_offset = flag_id // 8
    bit = flag_id % 8
    addr = flags_base + byte_offset
    current = rval(base, addr, 8)
    if current is None:
        return
    w8(base, addr, current | (1 << bit))

def set_var(base, vars_base, var_id, value):
    offset = (var_id - 0x4000) * 2
    w16(base, vars_base + offset, value)

def tap(base, button, delay=0.3):
    try:
        session.post(f'{base}/mgba-http/button/tap', params={'button': button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

base = "http://localhost:5000"

print(f"{'='*60}", flush=True)
print(f"SET FLAGS + NAVIGATE TO ROUTE 101", flush=True)
print(f"{'='*60}", flush=True)

# Load the movable state
session.post(f'{base}/core/loadstateslot', params={'slot': 1}, timeout=5)
time.sleep(0.5)

sb1 = rval(base, SB1_PTR, 32)
flags_base = sb1 + 0x1270
vars_base = sb1 + 0x139C

x = rval(base, sb1, 16)
y = rval(base, sb1 + 2, 16)
mg = rval(base, sb1 + 4, 8)
mn = rval(base, sb1 + 5, 8)
print(f"Start: ({x},{y}) Map({mg},{mn})", flush=True)

# Set flags to skip all events (player is in normal overworld, safe to modify)
print("\n--- Setting flags ---", flush=True)
flags_to_set = [
    0x52,   # FLAG_RESCUED_BIRCH
    0x74,   # FLAG_ADVENTURE_STARTED
    0x82,   # FLAG_DEFEATED_RIVAL_ROUTE103
    0x112,  # FLAG_RECEIVED_RUNNING_SHOES
    0x2BC,  # FLAG_HIDE_BIRCH_STARTERS_BAG
    0x2D0,  # FLAG_HIDE_BIRCH_ZIGZAGOON
    0x860,  # FLAG_SYS_POKEMON_GET
    0x861,  # FLAG_SYS_POKEDEX_GET
    0x862,  # FLAG_SYS_POKENAV_GET
    0x86F,  # FLAG_VISITED_LITTLEROOT_TOWN
    0x8C0,  # FLAG_SYS_B_DASH
    0x8E4,  # FLAG_RECEIVED_POKEDEX_FROM_BIRCH
]
for fid in flags_to_set:
    set_flag(base, flags_base, fid)

set_var(base, vars_base, 0x4050, 4)  # LITTLEROOT_TOWN_STATE = 4
set_var(base, vars_base, 0x4060, 3)  # ROUTE101_STATE = 3
print("  All flags and vars set.", flush=True)

# Verify flags
pokedex = (rval(base, flags_base + 268, 8) >> 1) & 1
running_shoes = (rval(base, flags_base + 0x8C0 // 8, 8) >> (0x8C0 % 8)) & 1
littleroot = rval(base, vars_base + (0x4050 - 0x4000) * 2, 16)
route101 = rval(base, vars_base + (0x4060 - 0x4000) * 2, 16)
print(f"  Verify: Pokedex={pokedex}, B_DASH={running_shoes}, LITTLEROOT={littleroot}, ROUTE101={route101}", flush=True)

# Walk test first
print("\n--- Quick movement test ---", flush=True)
prev_x, prev_y = x, y
moved = 0
for i in range(20):
    d = random.choice(["Up", "Down", "Left", "Right"])
    tap(base, d, 0.2)
    cx = rval(base, sb1, 16)
    cy = rval(base, sb1 + 2, 16)
    if cx != prev_x or cy != prev_y:
        moved += 1
        prev_x, prev_y = cx, cy

st_mg = rval(base, sb1 + 4, 8)
st_mn = rval(base, sb1 + 5, 8)
print(f"  {moved} moves in 20 taps. At ({prev_x},{prev_y}) Map({st_mg},{st_mn})", flush=True)

if moved == 0:
    print("  Player stuck! Aborting.", flush=True)
    sys.exit(1)

# Walk NORTH specifically to reach Route 101
# Route 101 exit is at the top of Littleroot Town
# Need to navigate to approximately (10,0) area
print("\n--- Walking north to Route 101 exit ---", flush=True)

# First, walk right to get near center of town (x~10)
cx = rval(base, sb1, 16)
if cx < 8:
    print(f"  Walking right from x={cx}...", flush=True)
    for i in range(20):
        tap(base, "Right", 0.25)
        if i % 5 == 4:
            tap(base, "A", 0.15)  # Clear any dialog

cx = rval(base, sb1, 16)
cy = rval(base, sb1 + 2, 16)
print(f"  After right: ({cx},{cy})", flush=True)

# Now walk north
print(f"  Walking north...", flush=True)
for i in range(60):
    tap(base, "Up", 0.25)
    if i % 8 == 7:
        tap(base, "A", 0.15)

    cx = rval(base, sb1, 16)
    cy = rval(base, sb1 + 2, 16)
    cmg = rval(base, sb1 + 4, 8)
    cmn = rval(base, sb1 + 5, 8)

    if cmn != mn:
        print(f"  *** MAP CHANGED at step {i}! ({cx},{cy}) Map({cmg},{cmn}) ***", flush=True)
        mn = cmn
        break

    if i % 15 == 14:
        print(f"  step {i}: ({cx},{cy}) Map({cmg},{cmn})", flush=True)

cx = rval(base, sb1, 16)
cy = rval(base, sb1 + 2, 16)
cmg = rval(base, sb1 + 4, 8)
cmn = rval(base, sb1 + 5, 8)
print(f"\nCurrent: ({cx},{cy}) Map({cmg},{cmn})", flush=True)

# If still on Map(0,9), try walking to specific exit points
if cmn == 9:
    print("\n  Still in Littleroot. Trying exit coordinates...", flush=True)
    # Try walking to known exit points: (10,1), (11,1)
    # Walk right then up
    for target_x in [10, 11]:
        cx = rval(base, sb1, 16)
        while cx < target_x:
            tap(base, "Right", 0.25)
            cx = rval(base, sb1, 16)
        while cx > target_x:
            tap(base, "Left", 0.25)
            cx = rval(base, sb1, 16)

        # Now walk up
        for j in range(20):
            tap(base, "Up", 0.25)
            if j % 3 == 2:
                tap(base, "A", 0.15)
            cmn_new = rval(base, sb1 + 5, 8)
            if cmn_new != 9:
                cx = rval(base, sb1, 16)
                cy = rval(base, sb1 + 2, 16)
                cmg = rval(base, sb1 + 4, 8)
                cmn = cmn_new
                print(f"  *** EXITED at ({cx},{cy}) Map({cmg},{cmn})! ***", flush=True)
                break
        if cmn != 9:
            break

    cx = rval(base, sb1, 16)
    cy = rval(base, sb1 + 2, 16)
    cmg = rval(base, sb1 + 4, 8)
    cmn = rval(base, sb1 + 5, 8)
    print(f"\n  Final position: ({cx},{cy}) Map({cmg},{cmn})", flush=True)

# Encounter test
if cmn == 16:  # Route 101
    print(f"\n--- On Route 101! Testing encounters ---", flush=True)
else:
    print(f"\n--- Encounter test on current map ---", flush=True)

w8(base, ADDR_WILD_DISABLED_1, 0)
w8(base, ADDR_WILD_DISABLED_2, 0)

prev_x = rval(base, sb1, 16)
prev_y = rval(base, sb1 + 2, 16)
moved = 0
encounters = 0

for i in range(600):
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
        print(f"\n  *** ENCOUNTER at tap {i}! ***", flush=True)
        print(f"  pos=({cx},{cy}) Map({cmg},{cmn}) moved={moved}", flush=True)
        break

    if i % 100 == 0:
        cmg = rval(base, sb1 + 4, 8)
        cmn = rval(base, sb1 + 5, 8)
        print(f"  tap={i:3d}: ({cx},{cy}) Map({cmg},{cmn}) moved={moved}", flush=True)

print(f"\n{'='*60}", flush=True)
print(f"RESULT: {moved} moves, {encounters} encounters", flush=True)
print(f"{'='*60}", flush=True)

if encounters > 0:
    print("*** ENCOUNTERS WORK! ***", flush=True)
    # Run away and save
    time.sleep(1)
    for _ in range(3):
        tap(base, "B", 0.2)
    tap(base, "Down", 0.3)
    tap(base, "Right", 0.3)
    tap(base, "A", 0.5)
    time.sleep(2)
    for _ in range(15):
        tap(base, "A", 0.3)

    # Save the working state
    session.post(f'{base}/core/savestateslot', params={'slot': 1}, timeout=5)
    session.post(f'{base}/core/savestateslot', params={'slot': 3}, timeout=5)
    print("Saved working state!", flush=True)
elif moved > 30:
    p = 0.889 ** moved
    print(f"P(0 enc in {moved} steps) = {p:.10f}", flush=True)
    # Save anyway for navigation progress
    session.post(f'{base}/core/savestateslot', params={'slot': 1}, timeout=5)
    session.post(f'{base}/core/savestateslot', params={'slot': 3}, timeout=5)
    print("Saved progress.", flush=True)
