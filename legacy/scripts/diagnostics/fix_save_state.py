"""
Fix save state: Set event flags to indicate Birch rescue event is complete.
The save is from RIGHT AFTER getting the starter but BEFORE completing the
Route 101 Birch/Zigzagoon event and receiving the Pokedex.

We need to set these flags:
- FLAG_SYS_POKEDEX_GET (0x861) - have Pokedex
- FLAG_RECEIVED_POKEDEX_FROM_BIRCH (0x801)
- FLAG_DEFEATED_RIVAL_ROUTE101 (0x804)
- FLAG_HIDE_ROUTE_101_BIRCH_ZIGZAGOON_BATTLE (0x3A3)
- VAR_ROUTE101_STATE = 6 (completed)
- Clear sWildEncountersDisabled
"""
import requests
import time
import random
import sys

BASE_URL = "http://localhost:{port}"
session = requests.Session()
sys.stdout.reconfigure(line_buffering=True)

def rval(base, addr, bits=32):
    ep = {8: 'read8', 16: 'read16', 32: 'read32'}[bits]
    v = session.get(f'{base}/core/{ep}', params={'address': hex(addr)}, timeout=1).json()
    return int(v['value']) if isinstance(v, dict) else int(v)

def w8(base, addr, val):
    session.post(f'{base}/core/write8', params={'address': hex(addr), 'value': val}, timeout=1)

def w16(base, addr, val):
    session.post(f'{base}/core/write16', params={'address': hex(addr), 'value': val}, timeout=1)

def set_flag(base, flags_base, flag_id):
    """Set an event flag by ID"""
    byte_offset = flag_id // 8
    bit = flag_id % 8
    addr = flags_base + byte_offset
    current = rval(base, addr, 8)
    new_val = current | (1 << bit)
    w8(base, addr, new_val)
    verify = rval(base, addr, 8)
    flag_set = (verify >> bit) & 1
    print(f"  Set flag 0x{flag_id:03X} ({flag_id}): byte@+{byte_offset} "
          f"bit{bit} -> {flag_set}", flush=True)

def tap(base, button, delay=0.3):
    try:
        session.post(f'{base}/mgba-http/button/tap', params={'button': button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

for port in [5000, 5001, 5002]:
    base = f"http://localhost:{port}"
    print(f"\n{'='*60}", flush=True)
    print(f"Fixing save state on port {port}", flush=True)
    print(f"{'='*60}", flush=True)

    # Load slot 1
    session.post(f'{base}/core/loadstateslot', params={'slot': 1}, timeout=5)
    time.sleep(0.3)

    sb1 = rval(base, 0x03005AEC, 32)
    flags_base = sb1 + 0x1270
    vars_base = sb1 + 0x139C
    print(f"SB1=0x{sb1:08X}", flush=True)

    # Check current state
    x = rval(base, sb1, 16)
    y = rval(base, sb1 + 2, 16)
    mg = rval(base, sb1 + 4, 8)
    mn = rval(base, sb1 + 5, 8)
    print(f"Position: ({x},{y}) Map({mg},{mn})", flush=True)

    # Set all required flags
    print("\nSetting event flags:", flush=True)

    # FLAG_SYS_POKEDEX_GET = 0x861 (2145)
    set_flag(base, flags_base, 0x861)

    # FLAG_RECEIVED_POKEDEX_FROM_BIRCH = 0x801 (2049)
    set_flag(base, flags_base, 0x801)

    # FLAG_DEFEATED_RIVAL_ROUTE101 = 0x804 (2052)
    set_flag(base, flags_base, 0x804)

    # FLAG_HIDE_ROUTE_101_BIRCH_ZIGZAGOON_BATTLE = 0x3A3 (931)
    set_flag(base, flags_base, 0x3A3)

    # FLAG_SYS_POKEMON_GET should already be set, verify
    byte_860 = rval(base, flags_base + 268, 8)
    print(f"  FLAG_SYS_POKEMON_GET: {(byte_860 >> 0) & 1}", flush=True)

    # Set VAR_ROUTE101_STATE = 6 (completed)
    # VAR_0x4050 at Vars + (0x4050 - 0x4000) * 2 = 0x139C + 0xA0
    w16(base, vars_base + 0xA0, 6)
    route101 = rval(base, vars_base + 0xA0, 16)
    print(f"  VAR_ROUTE101_STATE: {route101}", flush=True)

    # Clear Repel
    w16(base, sb1 + 0x13DE, 0)

    # Clear sWildEncountersDisabled (both candidate addresses)
    w8(base, 0x02038AA4, 0)
    w8(base, 0x020397E4, 0)

    # Save the fixed state
    session.post(f'{base}/core/savestateslot', params={'slot': 1}, timeout=5)
    print(f"Saved fixed state to slot 1", flush=True)

    # Backup to slot 3
    session.post(f'{base}/core/savestateslot', params={'slot': 3}, timeout=5)
    print(f"Backup saved to slot 3", flush=True)

# === Test encounters on port 5001 ===
print(f"\n{'='*60}", flush=True)
print(f"ENCOUNTER TEST (port 5001) after flag fix", flush=True)
print(f"{'='*60}", flush=True)

base = "http://localhost:5001"
session.post(f'{base}/core/loadstateslot', params={'slot': 1}, timeout=5)
time.sleep(0.3)

sb1 = rval(base, 0x03005AEC, 32)
prev_x = rval(base, sb1, 16)
prev_y = rval(base, sb1 + 2, 16)
moved = 0
encounters = 0

for i in range(300):
    if i % 5 == 0:
        w8(base, 0x02038AA4, 0)
        w8(base, 0x020397E4, 0)

    d = random.choice(['Up', 'Down', 'Left', 'Right'])
    tap(base, d, 0.3)

    cx = rval(base, sb1, 16)
    cy = rval(base, sb1 + 2, 16)
    if cx != prev_x or cy != prev_y:
        moved += 1
        prev_x, prev_y = cx, cy

    btl = rval(base, 0x02022E90, 32)
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

print(f"\nResult: {moved} moves, {encounters} encounters", flush=True)
if encounters > 0:
    print("*** ENCOUNTERS WORK! ***", flush=True)
else:
    if moved > 0:
        p = 0.889 ** moved
        print(f"P(0 enc in {moved} steps) = {p:.8f}", flush=True)
