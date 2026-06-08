"""
Fix save state v3: Complete fix with ALL necessary flags + dialog advancement.

Key finding: VAR_LITTLEROOT_TOWN_STATE (0x4050) was reset to 0,
but needs to be 4 (Running Shoes received, all events done).

Also missing:
- FLAG_SYS_B_DASH (0x8C0) - Running Shoes enabled
- FLAG_RECEIVED_RUNNING_SHOES (0x112)
- FLAG_RECEIVED_POKEDEX_FROM_BIRCH (0x8E4)
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

def w32(base, addr, val):
    try:
        session.post(f'{base}/core/write32', params={'address': hex(addr), 'value': val}, timeout=2)
    except:
        pass

def set_flag(base, flags_base, flag_id, name=""):
    byte_offset = flag_id // 8
    bit = flag_id % 8
    addr = flags_base + byte_offset
    current = rval(base, addr, 8)
    if current is None:
        print(f"  Flag 0x{flag_id:03X} ({name}): READ FAILED", flush=True)
        return
    new_val = current | (1 << bit)
    w8(base, addr, new_val)
    verify = rval(base, addr, 8)
    flag_set = (verify >> bit) & 1
    print(f"  Flag 0x{flag_id:03X} ({name}) = {flag_set}", flush=True)

def set_var(base, vars_base, var_id, value, name=""):
    offset = (var_id - 0x4000) * 2
    w16(base, vars_base + offset, value)
    verify = rval(base, vars_base + offset, 16)
    print(f"  VAR 0x{var_id:04X} ({name}) = {verify}", flush=True)

def tap(base, button, delay=0.3):
    try:
        session.post(f'{base}/mgba-http/button/tap', params={'button': button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

# All flags needed for post-Pokedex free roam
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

# Vars to set
ALL_VARS = {
    0x4050: ("VAR_LITTLEROOT_TOWN_STATE", 4),  # Running Shoes received
    0x4060: ("VAR_ROUTE101_STATE", 3),           # Route 101 cleared
}

for port in [5000, 5001, 5002]:
    base = f"http://localhost:{port}"
    print(f"\n{'='*60}", flush=True)
    print(f"FIX v3 on port {port}", flush=True)
    print(f"{'='*60}", flush=True)

    # Load slot 1
    session.post(f'{base}/core/loadstateslot', params={'slot': 1}, timeout=5)
    time.sleep(0.5)

    sb1 = rval(base, SB1_PTR, 32)
    flags_base = sb1 + 0x1270
    vars_base = sb1 + 0x139C

    x = rval(base, sb1, 16)
    y = rval(base, sb1 + 2, 16)
    mg = rval(base, sb1 + 4, 8)
    mn = rval(base, sb1 + 5, 8)
    print(f"SB1=0x{sb1:08X} Pos=({x},{y}) Map({mg},{mn})", flush=True)

    # Set all flags
    print("\n--- Setting all flags ---", flush=True)
    for fid, fname in ALL_FLAGS.items():
        set_flag(base, flags_base, fid, fname)

    # Set all vars
    print("\n--- Setting vars ---", flush=True)
    for vid, (vname, vval) in ALL_VARS.items():
        set_var(base, vars_base, vid, vval, vname)

    # Clear wild encounters disabled
    w8(base, ADDR_WILD_DISABLED_1, 0)
    w8(base, ADDR_WILD_DISABLED_2, 0)
    # Clear repel
    w16(base, sb1 + 0x13DE, 0)

    # Now try to advance any active dialog/script with aggressive A/B pressing
    print("\n--- Advancing dialog (200 A/B presses) ---", flush=True)
    for i in range(200):
        tap(base, "A" if i % 2 == 0 else "B", 0.1)

    # Check if player can move now
    x2 = rval(base, sb1, 16)
    y2 = rval(base, sb1 + 2, 16)
    mg2 = rval(base, sb1 + 4, 8)
    mn2 = rval(base, sb1 + 5, 8)
    print(f"After dialog: ({x2},{y2}) Map({mg2},{mn2})", flush=True)

    # Quick movement test
    print("--- Quick walk test ---", flush=True)
    moved = 0
    px, py = x2, y2
    for i in range(50):
        d = random.choice(["Up", "Down", "Left", "Right"])
        tap(base, d, 0.2)
        cx = rval(base, sb1, 16)
        cy = rval(base, sb1 + 2, 16)
        if cx != px or cy != py:
            moved += 1
            px, py = cx, cy
    cmg = rval(base, sb1 + 4, 8)
    cmn = rval(base, sb1 + 5, 8)
    print(f"  50 taps: {moved} moves, pos=({px},{py}) Map({cmg},{cmn})", flush=True)

    if moved > 3:
        print("  Player is MOVING! Saving state...", flush=True)
        session.post(f'{base}/core/savestateslot', params={'slot': 1}, timeout=5)
        session.post(f'{base}/core/savestateslot', params={'slot': 3}, timeout=5)
        print(f"  Saved to slots 1 and 3", flush=True)
    else:
        print("  Player still stuck. Saving anyway...", flush=True)
        session.post(f'{base}/core/savestateslot', params={'slot': 1}, timeout=5)
        session.post(f'{base}/core/savestateslot', params={'slot': 3}, timeout=5)

# === ENCOUNTER TEST on port 5001 ===
print(f"\n{'='*60}", flush=True)
print(f"ENCOUNTER TEST on port 5001", flush=True)
print(f"{'='*60}", flush=True)

base = "http://localhost:5001"
session.post(f'{base}/core/loadstateslot', params={'slot': 1}, timeout=5)
time.sleep(0.5)

sb1 = rval(base, SB1_PTR, 32)
cx = rval(base, sb1, 16)
cy = rval(base, sb1 + 2, 16)
prev_x, prev_y = cx, cy
moved = 0
encounters = 0

print(f"Start: ({cx},{cy})", flush=True)

for i in range(500):
    if i % 10 == 0:
        w8(base, ADDR_WILD_DISABLED_1, 0)
        w8(base, ADDR_WILD_DISABLED_2, 0)

    # Mix movement with A/B
    r = random.random()
    if r < 0.05:
        tap(base, "A", 0.15)
    elif r < 0.08:
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
        print(f"\n  *** ENCOUNTER at tap {i}! ***", flush=True)
        print(f"  pos=({cx},{cy}) Map({cmg},{cmn}) moved={moved}", flush=True)
        break

    if i % 100 == 0:
        cmg = rval(base, sb1 + 4, 8)
        cmn = rval(base, sb1 + 5, 8)
        print(f"  tap={i:3d}: ({cx},{cy}) Map({cmg},{cmn}) moved={moved}", flush=True)

print(f"\n{'='*60}", flush=True)
print(f"FINAL: {moved} moves, {encounters} encounters", flush=True)
print(f"{'='*60}", flush=True)

if encounters > 0:
    print("*** ENCOUNTERS WORK! ***", flush=True)
elif moved > 10:
    p = 0.889 ** moved
    print(f"P(0 enc in {moved} steps) = {p:.8f}", flush=True)
    if p < 0.01:
        print("Encounters likely BROKEN.", flush=True)
elif moved > 0:
    print(f"Only {moved} moves - player barely moved.", flush=True)
else:
    print("Player completely stuck.", flush=True)
