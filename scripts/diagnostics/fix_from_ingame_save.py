"""
Fix from in-game save: Reset all 3 emulators, load in-game save,
apply correct flags, navigate out, test encounters, save state.
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

def set_flag(base, flags_base, flag_id, name=""):
    byte_offset = flag_id // 8
    bit = flag_id % 8
    addr = flags_base + byte_offset
    current = rval(base, addr, 8)
    if current is None:
        return
    new_val = current | (1 << bit)
    w8(base, addr, new_val)

def set_var(base, vars_base, var_id, value):
    offset = (var_id - 0x4000) * 2
    w16(base, vars_base + offset, value)

def tap(base, button, delay=0.3):
    try:
        session.post(f'{base}/mgba-http/button/tap', params={'button': button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

# All flags needed
ALL_FLAGS = {
    0x52:  "FLAG_RESCUED_BIRCH",
    0x74:  "FLAG_ADVENTURE_STARTED",
    0x82:  "FLAG_DEFEATED_RIVAL_ROUTE103",
    0x112: "FLAG_RECEIVED_RUNNING_SHOES",
    0x2BC: "FLAG_HIDE_BIRCH_STARTERS_BAG",
    0x2D0: "FLAG_HIDE_BIRCH_ZIGZAGOON",
    0x860: "FLAG_SYS_POKEMON_GET",
    0x861: "FLAG_SYS_POKEDEX_GET",
    0x862: "FLAG_SYS_POKENAV_GET",
    0x86F: "FLAG_VISITED_LITTLEROOT_TOWN",
    0x8C0: "FLAG_SYS_B_DASH",
    0x8E4: "FLAG_RECEIVED_POKEDEX_FROM_BIRCH",
}

for port in [5000, 5001, 5002]:
    base = f"http://localhost:{port}"
    print(f"\n{'='*60}", flush=True)
    print(f"RESET + FIX on port {port}", flush=True)
    print(f"{'='*60}", flush=True)

    # Reset emulator
    print("Resetting...", flush=True)
    session.post(f'{base}/coreadapter/reset', timeout=5)
    time.sleep(2)

    # Press A to load game from title screen
    print("Loading in-game save...", flush=True)
    for i in range(40):
        tap(base, "A", 0.4)
        sb1 = rval(base, SB1_PTR, 32)
        if sb1 and sb1 > 0x02000000 and sb1 < 0x03000000:
            x = rval(base, sb1, 16)
            y = rval(base, sb1 + 2, 16)
            if x is not None and x > 0 and i > 5:
                # Game seems loaded
                break

    sb1 = rval(base, SB1_PTR, 32)
    if not sb1 or sb1 < 0x02000000:
        print(f"  Failed to load game. SB1=0x{sb1:08X if sb1 else 0}", flush=True)
        continue

    flags_base = sb1 + 0x1270
    vars_base = sb1 + 0x139C

    x = rval(base, sb1, 16)
    y = rval(base, sb1 + 2, 16)
    mg = rval(base, sb1 + 4, 8)
    mn = rval(base, sb1 + 5, 8)
    print(f"Loaded: ({x},{y}) Map({mg},{mn}) SB1=0x{sb1:08X}", flush=True)

    # Check current flags
    print("\n--- Current flags ---", flush=True)
    for fid, fname in ALL_FLAGS.items():
        byte_offset = fid // 8
        bit = fid % 8
        current = rval(base, flags_base + byte_offset, 8)
        val = (current >> bit) & 1 if current is not None else '?'
        print(f"  0x{fid:03X} ({fname}) = {val}", flush=True)

    # Check current vars
    for vid, val_name in [(0x4050, "LITTLEROOT_STATE"), (0x4060, "ROUTE101_STATE")]:
        offset = (vid - 0x4000) * 2
        v = rval(base, vars_base + offset, 16)
        print(f"  VAR 0x{vid:04X} ({val_name}) = {v}", flush=True)

    # Set all flags
    print("\n--- Applying flags ---", flush=True)
    for fid, fname in ALL_FLAGS.items():
        set_flag(base, flags_base, fid, fname)
    print("  All flags set.", flush=True)

    # Set vars
    set_var(base, vars_base, 0x4050, 4)  # LITTLEROOT_TOWN_STATE = 4
    set_var(base, vars_base, 0x4060, 3)  # ROUTE101_STATE = 3
    print("  Vars set: LITTLEROOT=4, ROUTE101=3", flush=True)

    # Clear wild disabled
    w8(base, ADDR_WILD_DISABLED_1, 0)
    w8(base, ADDR_WILD_DISABLED_2, 0)
    w16(base, sb1 + 0x13DE, 0)  # Clear repel

    # Clear any dialog with A/B
    print("\n--- Clearing dialog ---", flush=True)
    for i in range(100):
        tap(base, "A" if i % 2 == 0 else "B", 0.1)

    # Walk test
    print("\n--- Walk test ---", flush=True)
    prev_x = rval(base, sb1, 16)
    prev_y = rval(base, sb1 + 2, 16)
    moved = 0

    for i in range(80):
        d = random.choice(["Up", "Down", "Left", "Right"])
        tap(base, d, 0.2)
        cx = rval(base, sb1, 16)
        cy = rval(base, sb1 + 2, 16)
        if cx != prev_x or cy != prev_y:
            moved += 1
            prev_x, prev_y = cx, cy
        if i % 20 == 0:
            cmg = rval(base, sb1 + 4, 8)
            cmn = rval(base, sb1 + 5, 8)
            print(f"  i={i}: ({cx},{cy}) Map({cmg},{cmn}) moved={moved}", flush=True)

    cx = rval(base, sb1, 16)
    cy = rval(base, sb1 + 2, 16)
    cmg = rval(base, sb1 + 4, 8)
    cmn = rval(base, sb1 + 5, 8)
    print(f"  Final: ({cx},{cy}) Map({cmg},{cmn}) moved={moved}", flush=True)

    if moved > 3:
        print(f"  Player MOVING! Saving state to slot 1...", flush=True)
        session.post(f'{base}/core/savestateslot', params={'slot': 1}, timeout=5)
        session.post(f'{base}/core/savestateslot', params={'slot': 3}, timeout=5)
        print(f"  Saved!", flush=True)
    else:
        print(f"  Player still stuck ({moved} moves).", flush=True)

# === ENCOUNTER TEST on port 5000 ===
print(f"\n{'='*60}", flush=True)
print(f"ENCOUNTER TEST on port 5000", flush=True)
print(f"{'='*60}", flush=True)

base = "http://localhost:5000"
session.post(f'{base}/core/loadstateslot', params={'slot': 1}, timeout=5)
time.sleep(0.5)

sb1 = rval(base, SB1_PTR, 32)
cx = rval(base, sb1, 16)
cy = rval(base, sb1 + 2, 16)
prev_x, prev_y = cx, cy
moved = 0
encounters = 0

# Walk north first to get to grass
print("Walking north to grass...", flush=True)
for i in range(30):
    tap(base, "Up", 0.25)
    cx = rval(base, sb1, 16)
    cy = rval(base, sb1 + 2, 16)
    if cx != prev_x or cy != prev_y:
        moved += 1
        prev_x, prev_y = cx, cy

cmg = rval(base, sb1 + 4, 8)
cmn = rval(base, sb1 + 5, 8)
print(f"After north walk: ({cx},{cy}) Map({cmg},{cmn}) moved={moved}", flush=True)

# Random walk in grass
print("Random walk for encounters...", flush=True)
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
        print(f"\n  *** ENCOUNTER at tap {i}! ***", flush=True)
        print(f"  pos=({cx},{cy}) Map({cmg},{cmn}) moved={moved}", flush=True)
        break

    if i % 100 == 0:
        cmg = rval(base, sb1 + 4, 8)
        cmn = rval(base, sb1 + 5, 8)
        print(f"  tap={i:3d}: ({cx},{cy}) Map({cmg},{cmn}) moved={moved}", flush=True)

print(f"\n{'='*60}", flush=True)
print(f"FINAL: {moved} total moves, {encounters} encounters", flush=True)
print(f"{'='*60}", flush=True)

if encounters > 0:
    print("*** ENCOUNTERS WORK! ***", flush=True)
elif moved > 10:
    p = 0.889 ** moved
    print(f"P(0 enc in {moved} steps) = {p:.10f}", flush=True)
else:
    print(f"Only {moved} moves.", flush=True)
