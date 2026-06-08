"""
Verify a clean save state created by manual play-through.
Tests:
1. Game state correctness (position, map, flags)
2. Movement (player can walk freely)
3. Encounters WITHOUT any sWildEncountersDisabled clearing
4. Encounters WITH sWildEncountersDisabled clearing
5. Address verification for sWildEncountersDisabled
"""
import requests
import time
import random
import sys

session = requests.Session()
sys.stdout.reconfigure(line_buffering=True)

SB1_PTR = 0x03005AEC
ADDR_BATTLE_FLAGS = 0x02022E90
ADDR_WILD_DISABLED_CANDIDATE = 0x02038AA4  # US-0x15C estimate

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

def walk_test(base, sb1, n_taps=200, clear_wild=False, label=""):
    """Walk randomly and count moves + encounters."""
    print(f"\n--- {label} (clear_wild={clear_wild}) ---", flush=True)
    prev_x = rval(base, sb1, 16)
    prev_y = rval(base, sb1 + 2, 16)
    moved = 0
    encounters = 0

    for i in range(n_taps):
        if clear_wild and i % 10 == 0:
            w8(base, ADDR_WILD_DISABLED_CANDIDATE, 0)

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
            mg = rval(base, sb1 + 4, 8)
            mn = rval(base, sb1 + 5, 8)
            print(f"  *** ENCOUNTER at tap {i}! ({cx},{cy}) Map({mg},{mn}) ***", flush=True)
            # Run away
            time.sleep(1)
            for _ in range(3):
                tap(base, "B", 0.2)
            tap(base, "Down", 0.3)
            tap(base, "Right", 0.3)
            tap(base, "A", 0.5)
            time.sleep(2)
            for _ in range(10):
                tap(base, "A", 0.3)
            # Continue walking
            prev_x = rval(base, sb1, 16)
            prev_y = rval(base, sb1 + 2, 16)

        if i % 50 == 49:
            mg = rval(base, sb1 + 4, 8)
            mn = rval(base, sb1 + 5, 8)
            print(f"  tap {i+1}: ({cx},{cy}) Map({mg},{mn}) moves={moved} enc={encounters}", flush=True)

    print(f"  Result: {moved} moves, {encounters} encounters", flush=True)
    if moved > 0 and encounters == 0:
        p = 0.889 ** moved
        print(f"  P(0 enc | {moved} moves) = {p:.8f}", flush=True)
    return moved, encounters

for port in [5000, 5001, 5002]:
    base = f"http://localhost:{port}"
    print(f"\n{'='*60}", flush=True)
    print(f"VERIFY CLEAN SAVE on port {port}", flush=True)
    print(f"{'='*60}", flush=True)

    # Check if emulator is responding
    try:
        sb1 = rval(base, SB1_PTR, 32)
        if not sb1 or sb1 < 0x02000000:
            print(f"  SB1 invalid: {sb1}. Emulator may not be running.", flush=True)
            continue
    except:
        print(f"  Connection failed. Emulator may not be running.", flush=True)
        continue

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
    print(f"\n[State] SB1=0x{sb1:08X} Pos=({x},{y}) Map({mg},{mn})", flush=True)

    # Check important flags
    print("\n[Flags]", flush=True)
    flag_checks = [
        (0x860, "SYS_POKEMON_GET"),
        (0x861, "SYS_POKEDEX_GET"),
        (0x74, "ADVENTURE_STARTED"),
        (0x52, "RESCUED_BIRCH"),
        (0x82, "DEFEATED_RIVAL_ROUTE103"),
        (0x8C0, "SYS_B_DASH"),
    ]
    for fid, fname in flag_checks:
        byte_offset = fid // 8
        bit = fid % 8
        val = rval(base, flags_base + byte_offset, 8)
        flag_set = (val >> bit) & 1 if val is not None else '?'
        print(f"  {fname} (0x{fid:03X}) = {flag_set}", flush=True)

    # Check vars
    for vid, vname in [(0x4050, "LITTLEROOT_STATE"), (0x4060, "ROUTE101_STATE")]:
        offset = (vid - 0x4000) * 2
        v = rval(base, vars_base + offset, 16)
        print(f"  {vname} (0x{vid:04X}) = {v}", flush=True)

    # Check party
    hp = rval(base, 0x020241E6, 16)
    hp_max = rval(base, 0x020241E8, 16)
    lv = rval(base, 0x020241E4, 8)
    print(f"\n[Party] Lv{lv} HP={hp}/{hp_max}", flush=True)

    # Check sWildEncountersDisabled at candidate address
    wd = rval(base, ADDR_WILD_DISABLED_CANDIDATE, 8)
    print(f"\n[Wild] sWildEncountersDisabled@0x{ADDR_WILD_DISABLED_CANDIDATE:08X} = {wd}", flush=True)

    # Check frame counter (is game running?)
    fc1 = rval(base, 0x03000F34, 32)
    time.sleep(0.3)
    fc2 = rval(base, 0x03000F34, 32)
    running = "RUNNING" if fc2 and fc1 and fc2 > fc1 else "PAUSED?"
    print(f"[Frame] {fc1} -> {fc2} = {running}", flush=True)

    if running == "PAUSED?":
        print("  WARNING: Emulator may be paused. Skipping walk test.", flush=True)
        continue

    # Test 1: Walk WITHOUT clearing sWildEncountersDisabled
    session.post(f'{base}/core/loadstateslot', params={'slot': 1}, timeout=5)
    time.sleep(0.3)
    m1, e1 = walk_test(base, sb1, 300, clear_wild=False, label="Test 1: Natural (no clear)")

    # Test 2: Walk WITH clearing sWildEncountersDisabled
    session.post(f'{base}/core/loadstateslot', params={'slot': 1}, timeout=5)
    time.sleep(0.3)
    m2, e2 = walk_test(base, sb1, 300, clear_wild=True, label="Test 2: With wild clear")

    # Summary
    print(f"\n{'='*60}", flush=True)
    print(f"PORT {port} SUMMARY:", flush=True)
    print(f"  Test 1 (natural): {m1} moves, {e1} encounters", flush=True)
    print(f"  Test 2 (clear):   {m2} moves, {e2} encounters", flush=True)
    if e1 > 0:
        print(f"  -> Encounters work NATURALLY (no clearing needed)", flush=True)
    elif e2 > 0:
        print(f"  -> Encounters work WITH clearing (address 0x{ADDR_WILD_DISABLED_CANDIDATE:08X} correct)", flush=True)
    elif m1 > 20 and m2 > 20:
        print(f"  -> Encounters BROKEN (address may be wrong or other issue)", flush=True)
    else:
        print(f"  -> Insufficient movement data", flush=True)
    print(f"{'='*60}", flush=True)
