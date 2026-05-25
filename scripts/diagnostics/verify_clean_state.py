"""
Verify the manually created clean save state.
Check gMapHeader, tileTransitionState, and test encounters.
"""
import requests
import time
import random
import sys

BASE_URL = "http://localhost:5000"
session = requests.Session()
sys.stdout.reconfigure(line_buffering=True)

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

def read32(addr):
    try:
        r = session.get(f"{BASE_URL}/core/read32", params={"address": hex(addr)}, timeout=1)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None

def write8(addr, val):
    try:
        session.post(f"{BASE_URL}/core/write8", params={"address": hex(addr), "value": val}, timeout=1)
    except:
        pass

def tap(button, delay=0.35):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/tap", params={"button": button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

SB1_PTR = 0x03005AEC
ADDR_BATTLE_FLAGS = 0x02022E90
ADDR_TILE_TRANSITION = 0x02037437
ADDR_MAP_HEADER = 0x020371BC
ADDR_WILD_DISABLED_NEW = 0x02038AA4
ADDR_WILD_DISABLED_OLD = 0x020397E4
ADDR_PLAYER_AVATAR = 0x02037434

print("=" * 60, flush=True)
print("CLEAN STATE VERIFICATION", flush=True)
print("=" * 60, flush=True)

# Load slot 1
print("\nLoading slot 1...", flush=True)
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

sb1 = read32(SB1_PTR)
x = read16(sb1) if sb1 and sb1 > 0x02000000 else None
y = read16(sb1 + 2) if sb1 and sb1 > 0x02000000 else None
mg = read8(sb1 + 4) if sb1 and sb1 > 0x02000000 else None
mn = read8(sb1 + 5) if sb1 and sb1 > 0x02000000 else None
print(f"SB1: 0x{sb1:08X}", flush=True)
print(f"Position: ({x},{y}) Map({mg},{mn})", flush=True)

# Check gMapHeader
mh = read32(ADDR_MAP_HEADER)
valid_mh = mh is not None and 0x08000000 < mh < 0x09000000
print(f"gMapHeader (0x020371BC): 0x{mh:08X} {'VALID' if valid_mh else 'INVALID'}", flush=True)

# If -0x15C doesn't work, try scanning nearby for the correct gMapHeader
if not valid_mh:
    print("\n-0x15C offset didn't work. Scanning for gMapHeader...", flush=True)
    found_mh = None
    for addr in range(0x02036E00, 0x02037400, 4):
        v = read32(addr)
        if v and 0x08000000 < v < 0x09000000:
            print(f"  ROM ptr at 0x{addr:08X}: 0x{v:08X}", flush=True)
            if found_mh is None:
                found_mh = (addr, v)

# Check tileTransitionState
tts = read8(ADDR_TILE_TRANSITION)
print(f"\ntileTransitionState (0x02037437): {tts}", flush=True)

# If tts is invalid, scan for it
if tts is None or tts > 2:
    print("tts invalid at -0x15C. Scanning for gPlayerAvatar...", flush=True)
    for addr in range(0x02036E00, 0x02037600):
        b3 = read8(addr + 3)
        if b3 is not None and 0 <= b3 <= 2:
            b0 = read8(addr)
            b1 = read8(addr + 1)
            b2 = read8(addr + 2)
            if b0 is not None and b1 is not None and b2 is not None:
                if b1 <= 2 and b2 <= 6 and b0 < 0x20:
                    print(f"  Candidate at 0x{addr:08X}: flags=0x{b0:02X} bike={b1} "
                          f"acro={b2} tts={b3}", flush=True)

# Check wild disabled
wd_new = read8(ADDR_WILD_DISABLED_NEW)
wd_old = read8(ADDR_WILD_DISABLED_OLD)
print(f"\nsWildEncountersDisabled:", flush=True)
print(f"  NEW (0x02038AA4): {wd_new}", flush=True)
print(f"  OLD (0x020397E4): {wd_old}", flush=True)

# Check repel
if sb1 and sb1 > 0x02000000:
    repel = read16(sb1 + 0x13DE)
    print(f"Repel (SB1+0x13DE): {repel}", flush=True)

# Check HP
hp = read16(0x020241E6)
hp_max = read16(0x020241E8)
lv = read8(0x020241E4)
party = read8(0x0202418D)
print(f"Party: {party} Pokemon, Lead: Lv{lv} HP={hp}/{hp_max}", flush=True)

# Walk test
print("\n" + "=" * 60, flush=True)
print("WALK + ENCOUNTER TEST (200 steps)", flush=True)
print("=" * 60, flush=True)

prev_x = x
prev_y = y
moved = 0
encounters = 0
tts_center = 0

for i in range(200):
    # Clear wild disabled flags
    write8(ADDR_WILD_DISABLED_NEW, 0)
    write8(ADDR_WILD_DISABLED_OLD, 0)

    d = random.choice(["Up", "Down", "Up", "Down"])
    tap(d, 0.35)

    sb1 = read32(SB1_PTR)
    cx = read16(sb1) if sb1 else 0
    cy = read16(sb1 + 2) if sb1 else 0
    tts = read8(ADDR_TILE_TRANSITION)

    if cx != prev_x or cy != prev_y:
        moved += 1
        prev_x, prev_y = cx, cy
    if tts == 2:
        tts_center += 1

    btl = read32(ADDR_BATTLE_FLAGS)
    if btl and 0 < btl < 0x10000:
        encounters += 1
        mg2 = read8(sb1 + 4) if sb1 else 0
        mn2 = read8(sb1 + 5) if sb1 else 0
        print(f"\n  *** WILD ENCOUNTER at step {i}! ***", flush=True)
        print(f"  btlFlags=0x{btl:08X} pos=({cx},{cy}) Map({mg2},{mn2})", flush=True)
        print(f"  Moved: {moved}, TileCenters: {tts_center}", flush=True)
        break

    if i % 25 == 0:
        mg2 = read8(sb1 + 4) if sb1 else 0
        mn2 = read8(sb1 + 5) if sb1 else 0
        wd = read8(ADDR_WILD_DISABLED_NEW)
        print(f"  i={i:3d}: ({cx},{cy}) Map({mg2},{mn2}) moved={moved} "
              f"centers={tts_center} tts={tts} wild={wd}", flush=True)

print(f"\n{'=' * 60}", flush=True)
print(f"RESULTS", flush=True)
print(f"{'=' * 60}", flush=True)
print(f"  Steps moved: {moved}", flush=True)
print(f"  T_TILE_CENTER: {tts_center}", flush=True)
print(f"  Encounters: {encounters}", flush=True)

if encounters > 0:
    print(f"\n*** ENCOUNTERS WORK! State is CLEAN! ***", flush=True)
elif tts_center > 0:
    print(f"\n  Overworld OK (tts reaches 2), encounters may need more steps.", flush=True)
else:
    print(f"\n  WARNING: tts never reached 2. Overworld may still be broken.", flush=True)
