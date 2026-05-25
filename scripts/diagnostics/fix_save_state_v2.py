"""
Fix save state v2: Set CORRECT event flags for Birch rescue event completion.

Previous attempt used WRONG flag IDs. Corrected values from pokeemerald decomp:
- FLAG_RESCUED_BIRCH = 0x52
- FLAG_ADVENTURE_STARTED = 0x74 (received Pokedex)
- FLAG_DEFEATED_RIVAL_ROUTE103 = 0x82
- FLAG_HIDE_ROUTE_101_BIRCH_STARTERS_BAG = 0x2BC
- FLAG_HIDE_ROUTE_101_BIRCH_ZIGZAGOON_BATTLE = 0x2D0
- FLAG_SYS_POKEMON_GET = 0x860 (already set)
- FLAG_SYS_POKEDEX_GET = 0x861
- VAR_ROUTE101_STATE = 0x4060 (set to 3 = completed)

Also try to clear the script context to stop any running event script.
"""
import requests
import time
import random
import sys

session = requests.Session()
sys.stdout.reconfigure(line_buffering=True)

def rval(base, addr, bits=32):
    ep = {8: 'read8', 16: 'read16', 32: 'read32'}[bits]
    v = session.get(f'{base}/core/{ep}', params={'address': hex(addr)}, timeout=2).json()
    return int(v['value']) if isinstance(v, dict) else int(v)

def w8(base, addr, val):
    session.post(f'{base}/core/write8', params={'address': hex(addr), 'value': val}, timeout=2)

def w16(base, addr, val):
    session.post(f'{base}/core/write16', params={'address': hex(addr), 'value': val}, timeout=2)

def w32(base, addr, val):
    session.post(f'{base}/core/write32', params={'address': hex(addr), 'value': val}, timeout=2)

def set_flag(base, flags_base, flag_id, name=""):
    """Set an event flag by ID"""
    byte_offset = flag_id // 8
    bit = flag_id % 8
    addr = flags_base + byte_offset
    current = rval(base, addr, 8)
    new_val = current | (1 << bit)
    w8(base, addr, new_val)
    verify = rval(base, addr, 8)
    flag_set = (verify >> bit) & 1
    label = f" ({name})" if name else ""
    print(f"  Flag 0x{flag_id:03X}{label}: byte@+{byte_offset} bit{bit} = {flag_set}", flush=True)

def check_flag(base, flags_base, flag_id, name=""):
    """Check if a flag is set"""
    byte_offset = flag_id // 8
    bit = flag_id % 8
    addr = flags_base + byte_offset
    current = rval(base, addr, 8)
    flag_set = (current >> bit) & 1
    label = f" ({name})" if name else ""
    print(f"  Flag 0x{flag_id:03X}{label} = {flag_set}", flush=True)
    return flag_set

def tap(base, button, delay=0.3):
    try:
        session.post(f'{base}/mgba-http/button/tap', params={'button': button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

# Correct flag IDs from pokeemerald decomp
FLAGS_TO_SET = {
    0x52:  "FLAG_RESCUED_BIRCH",
    0x74:  "FLAG_ADVENTURE_STARTED",
    0x82:  "FLAG_DEFEATED_RIVAL_ROUTE103",
    0x2BC: "FLAG_HIDE_ROUTE_101_BIRCH_STARTERS_BAG",
    0x2D0: "FLAG_HIDE_ROUTE_101_BIRCH_ZIGZAGOON_BATTLE",
    0x860: "FLAG_SYS_POKEMON_GET",
    0x861: "FLAG_SYS_POKEDEX_GET",
}

# Also clear the WRONG flags we set previously (they may cause issues)
WRONG_FLAGS_TO_CLEAR = [0x801, 0x804, 0x3A3]

SB1_PTR = 0x03005AEC
ADDR_WILD_DISABLED_1 = 0x02038AA4
ADDR_WILD_DISABLED_2 = 0x020397E4

for port in [5000, 5001, 5002]:
    base = f"http://localhost:{port}"
    print(f"\n{'='*60}", flush=True)
    print(f"Fixing save state on port {port} (v2 - correct flags)", flush=True)
    print(f"{'='*60}", flush=True)

    # Load slot 1
    session.post(f'{base}/core/loadstateslot', params={'slot': 1}, timeout=5)
    time.sleep(0.5)

    sb1 = rval(base, SB1_PTR, 32)
    flags_base = sb1 + 0x1270
    vars_base = sb1 + 0x139C
    print(f"SB1=0x{sb1:08X}, flags@0x{flags_base:08X}, vars@0x{vars_base:08X}", flush=True)

    # Check position
    x = rval(base, sb1, 16)
    y = rval(base, sb1 + 2, 16)
    mg = rval(base, sb1 + 4, 8)
    mn = rval(base, sb1 + 5, 8)
    print(f"Position: ({x},{y}) Map({mg},{mn})", flush=True)

    # Check current flag state before fix
    print("\n--- Current flag state ---", flush=True)
    for fid, fname in FLAGS_TO_SET.items():
        check_flag(base, flags_base, fid, fname)

    # Clear wrong flags from previous attempt
    print("\n--- Clearing wrong flags from v1 ---", flush=True)
    for fid in WRONG_FLAGS_TO_CLEAR:
        byte_offset = fid // 8
        bit = fid % 8
        addr = flags_base + byte_offset
        current = rval(base, addr, 8)
        new_val = current & ~(1 << bit)
        w8(base, addr, new_val)
        verify = rval(base, addr, 8)
        cleared = not ((verify >> bit) & 1)
        print(f"  Cleared flag 0x{fid:03X}: {'OK' if cleared else 'FAILED'}", flush=True)

    # Set correct flags
    print("\n--- Setting correct flags ---", flush=True)
    for fid, fname in FLAGS_TO_SET.items():
        set_flag(base, flags_base, fid, fname)

    # Set VAR_ROUTE101_STATE (0x4060) = 3
    # Offset: (0x4060 - 0x4000) * 2 = 0x60 * 2 = 0xC0
    var_offset = (0x4060 - 0x4000) * 2
    w16(base, vars_base + var_offset, 3)
    route101 = rval(base, vars_base + var_offset, 16)
    print(f"\n  VAR_ROUTE101_STATE (0x4060) = {route101}", flush=True)

    # Also fix the wrong var we set before (0x4050 -> reset to 0)
    old_var_offset = (0x4050 - 0x4000) * 2
    w16(base, vars_base + old_var_offset, 0)
    old_var = rval(base, vars_base + old_var_offset, 16)
    print(f"  VAR_0x4050 (was wrongly set to 6) = {old_var}", flush=True)

    # Try to find and clear script context
    # The script context struct has: mode (u8), scriptPtr (u32)
    # If mode != 0, a script is running
    # We'll scan EWRAM for the pattern: stackDepth=small, mode=1or2, compResult, then ROM pointer
    print("\n--- Scanning for active script context ---", flush=True)
    script_ctx_found = False
    for addr in range(0x02020000, 0x02028000, 4):
        # ScriptContext: stackDepth(u8), mode(u8), compResult(u8), padding(u8), nativePtr(u32), scriptPtr(u32)
        b0 = rval(base, addr, 8)      # stackDepth (0-20)
        b1 = rval(base, addr + 1, 8)  # mode (0=stopped, 1=bytecode, 2=native)
        if b0 is not None and b1 in (1, 2) and b0 <= 20:
            # Check if scriptPtr looks like a ROM address
            scriptPtr = rval(base, addr + 8, 32)
            if scriptPtr and 0x08000000 < scriptPtr < 0x09000000:
                print(f"  Script context candidate at 0x{addr:08X}:", flush=True)
                print(f"    stackDepth={b0}, mode={b1}, scriptPtr=0x{scriptPtr:08X}", flush=True)
                # Clear it: set mode=0, scriptPtr=0
                w8(base, addr + 1, 0)  # mode = STOPPED
                w32(base, addr + 8, 0)  # scriptPtr = NULL
                verify_mode = rval(base, addr + 1, 8)
                print(f"    -> Set mode=0, verify={verify_mode}", flush=True)
                script_ctx_found = True
                break
    if not script_ctx_found:
        print("  No active script context found (may already be stopped)", flush=True)

    # Clear sWildEncountersDisabled
    w8(base, ADDR_WILD_DISABLED_1, 0)
    w8(base, ADDR_WILD_DISABLED_2, 0)

    # Clear Repel
    w16(base, sb1 + 0x13DE, 0)

    # Save the fixed state
    session.post(f'{base}/core/savestateslot', params={'slot': 1}, timeout=5)
    print(f"\nSaved fixed state to slot 1", flush=True)
    session.post(f'{base}/core/savestateslot', params={'slot': 3}, timeout=5)
    print(f"Backup saved to slot 3", flush=True)

# === ENCOUNTER TEST on port 5001 ===
print(f"\n{'='*60}", flush=True)
print(f"ENCOUNTER TEST (port 5001) after v2 flag fix", flush=True)
print(f"{'='*60}", flush=True)

base = "http://localhost:5001"
session.post(f'{base}/core/loadstateslot', params={'slot': 1}, timeout=5)
time.sleep(0.5)

sb1 = rval(base, SB1_PTR, 32)
prev_x = rval(base, sb1, 16)
prev_y = rval(base, sb1 + 2, 16)
moved = 0
encounters = 0

for i in range(400):
    if i % 5 == 0:
        w8(base, ADDR_WILD_DISABLED_1, 0)
        w8(base, ADDR_WILD_DISABLED_2, 0)

    d = random.choice(['Up', 'Down', 'Left', 'Right'])
    tap(base, d, 0.25)

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
        if p < 0.001:
            print("Encounters still BROKEN.", flush=True)
        elif p < 0.05:
            print("Very unlikely. Encounters may be broken.", flush=True)
        else:
            print("Could be bad luck. Try more steps.", flush=True)
    else:
        print("Player didn't move! Game may be stuck.", flush=True)
