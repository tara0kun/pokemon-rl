"""
Quick targeted scan using read32 (4 bytes per call) to find addresses efficiently.
Focus on:
1. Finding gMapHeader (ROM pointer in EWRAM)
2. Finding gPlayerAvatar.tileTransitionState
3. Checking if the game is running
"""
import requests
import time
import sys

BASE_URL = "http://localhost:5000"
session = requests.Session()

def read32(addr):
    try:
        r = session.get(f"{BASE_URL}/core/read32", params={"address": hex(addr)}, timeout=1)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None

def read8(addr):
    try:
        r = session.get(f"{BASE_URL}/core/read8", params={"address": hex(addr)}, timeout=1)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None

def read16(addr):
    try:
        r = session.get(f"{BASE_URL}/core/read16", params={"address": hex(addr)}, timeout=1)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None

def tap(button, delay=0.4):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/tap", params={"button": button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

SB1_PTR = 0x03005AEC
sys.stdout.reconfigure(line_buffering=True)  # Flush on every line

# Load slot 1
print("Loading slot 1...", flush=True)
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

sb1 = read32(SB1_PTR)
print(f"SB1=0x{sb1:08X}", flush=True)

# === Check 1: Is the game running? ===
print("\n=== Game Running Check ===", flush=True)
# Check IWRAM for frame counters - read at candidate gMain+0x20
# From the previous scan, 0x03000F14 had vblank data
candidates_gmain = [0x03000F14, 0x03000E9C, 0x030024D0, 0x03002710, 0x03002470]
for base in candidates_gmain:
    v1 = read32(base + 0x20)
    time.sleep(0.2)
    v2 = read32(base + 0x20)
    if v1 is not None and v2 is not None:
        diff = v2 - v1 if v2 >= v1 else 0
        if diff > 0:
            cb1 = read32(base)
            cb2 = read32(base + 0x04)
            print(f"  gMain candidate: 0x{base:08X}", flush=True)
            print(f"    vblankCounter: {v1} -> {v2} (diff={diff})", flush=True)
            print(f"    callback1: 0x{cb1:08X}", flush=True)
            print(f"    callback2: 0x{cb2:08X}", flush=True)

# === Check 2: Scan for ROM pointers near gMapHeader area ===
print("\n=== ROM Pointer Scan (gMapHeader candidates) ===", flush=True)
# Scan with read32 (4 bytes per call) - much faster than byte-by-byte
# Expected range: 0x02036000 - 0x02038000
rom_ptrs = []
for addr in range(0x02036800, 0x02037800, 4):
    v = read32(addr)
    if v and 0x08000000 < v < 0x09000000:
        rom_ptrs.append((addr, v))
print(f"Found {len(rom_ptrs)} ROM pointers in 0x02036800-0x02037800:", flush=True)
for addr, val in rom_ptrs[:15]:
    print(f"  0x{addr:08X}: 0x{val:08X} (US equiv: 0x{addr+0x15C:08X})", flush=True)

# Also check wider range
for addr in range(0x02037800, 0x02038800, 4):
    v = read32(addr)
    if v and 0x08000000 < v < 0x09000000:
        rom_ptrs.append((addr, v))
        print(f"  0x{addr:08X}: 0x{val:08X} (wider scan)", flush=True)

# === Check 3: Find tileTransitionState by walking ===
print("\n=== Walk + Check tileTransitionState ===", flush=True)
# Instead of scanning all EWRAM, check specific candidate offsets
# The US gPlayerAvatar is at 0x02037590
# JP could be at: 0x02037434 (-0x15C), 0x02037590 (same), or other offsets
# Also try: -0x100, -0x120, -0x140, -0x160, -0x180, -0x1A0
offsets_to_try = [0x15C, 0x100, 0x120, 0x140, 0x160, 0x180, 0x1A0, 0x0]
base_us = 0x02037590  # US gPlayerAvatar

print(f"Checking tileTransitionState (+3) at various offsets from US addr:", flush=True)
for off in offsets_to_try:
    jp_addr = base_us - off
    tts = read8(jp_addr + 3)
    flags = read8(jp_addr)
    bike = read8(jp_addr + 1)
    acro = read8(jp_addr + 2)
    sprite = read8(jp_addr + 6)
    obj = read8(jp_addr + 7)
    print(f"  -0x{off:03X} (0x{jp_addr:08X}): flags=0x{flags:02X} bike={bike} "
          f"acro={acro} tts={tts} sprite={sprite} obj={obj}", flush=True)

# === Check 4: gMapHeader at various offsets ===
print(f"\nChecking gMapHeader at various offsets:", flush=True)
base_mh_us = 0x02037318  # US gMapHeader
for off in offsets_to_try:
    jp_addr = base_mh_us - off
    v = read32(jp_addr)
    valid = "ROM" if (v and 0x08000000 < v < 0x09000000) else "INVALID"
    print(f"  -0x{off:03X} (0x{jp_addr:08X}): 0x{v:08X} ({valid})", flush=True)

# === Check 5: Quick walk test to verify frame advancement ===
print(f"\n=== Quick walk test ===", flush=True)
x_before = read16(sb1) if sb1 else 0
y_before = read16(sb1 + 2) if sb1 else 0
print(f"Before: ({x_before},{y_before})", flush=True)

tap("Up", 0.5)
tap("Up", 0.5)
tap("Up", 0.5)

x_after = read16(sb1) if sb1 else 0
y_after = read16(sb1 + 2) if sb1 else 0
print(f"After 3 taps: ({x_after},{y_after})", flush=True)
if x_after != x_before or y_after != y_before:
    print(f"Player MOVED - game is running.", flush=True)
else:
    print(f"Player DID NOT MOVE - game may be stuck.", flush=True)

# === Check 6: Try to find sWildEncountersDisabled ===
print(f"\n=== sWildEncountersDisabled scan ===", flush=True)
base_wd_us = 0x02038C00  # US sWildEncountersDisabled
for off in offsets_to_try:
    jp_addr = base_wd_us - off
    v = read8(jp_addr)
    # Also check surrounding bytes for context
    v_before = read8(jp_addr - 1)
    v_after = read8(jp_addr + 1)
    print(f"  -0x{off:03X} (0x{jp_addr:08X}): {v} (neighbors: {v_before},{v_after})", flush=True)

print("\nDone!", flush=True)
