"""
Wild encounter root cause diagnosis v2
- Correct JP addresses from pokeemerald decomp research
- Check sWildEncountersDisabled at CORRECT address (0x02038AA4)
- Check tileTransitionState at 0x02037437
- Check gMapHeader at 0x020371BC
- Verify callback2 is set to CB2_Overworld
"""
import requests
import time
import random

BASE_URL = "http://localhost:5000"
session = requests.Session()

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

# === JP Emerald addresses (verified from pokeemerald decomp) ===
SB1_PTR               = 0x03005AEC
ADDR_BATTLE_FLAGS      = 0x02022E90  # gBattleTypeFlags (confirmed)
ADDR_PLAYER_AVATAR     = 0x02037434  # gPlayerAvatar (confirmed from JP sym)
ADDR_TILE_TRANSITION   = 0x02037437  # gPlayerAvatar.tileTransitionState (+0x03)
ADDR_MAP_HEADER        = 0x020371BC  # gMapHeader (US 0x02037318 - 0x15C)
ADDR_WILD_DISABLED_OLD = 0x020397E4  # Old (wrong?) address from EWRAM scan
ADDR_WILD_DISABLED_NEW = 0x02038AA4  # sWildEncountersDisabled (US 0x02038C00 - 0x15C)

print("=" * 60)
print("ENCOUNTER DIAGNOSIS v2 - Correct JP Addresses")
print("=" * 60)

# Load save state slot 1
print("\n--- Loading save state slot 1 ---")
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

sb1 = read32(SB1_PTR)
print(f"SB1 pointer: 0x{sb1:08X}" if sb1 else "SB1: FAILED")

# Basic state
x = read16(sb1) if sb1 else None
y = read16(sb1 + 2) if sb1 else None
mg = read8(sb1 + 4) if sb1 else None
mn = read8(sb1 + 5) if sb1 else None
print(f"Position: ({x}, {y}) Map({mg}, {mn})")

# === Check BOTH sWildEncountersDisabled candidates ===
print("\n--- sWildEncountersDisabled ---")
old_val = read8(ADDR_WILD_DISABLED_OLD)
new_val = read8(ADDR_WILD_DISABLED_NEW)
print(f"  OLD address (0x020397E4): {old_val}")
print(f"  NEW address (0x02038AA4): {new_val}")

# Check bytes around the new address
print(f"\n  Bytes around NEW address (0x02038AA4):")
for offset in range(-8, 9):
    addr = ADDR_WILD_DISABLED_NEW + offset
    v = read8(addr)
    marker = " <<< sWildEncountersDisabled" if offset == 0 else ""
    if v is not None:
        print(f"    0x{addr:08X} ({offset:+d}): {v} (0x{v:02X}){marker}")

# === Check tileTransitionState ===
print("\n--- tileTransitionState ---")
tts = read8(ADDR_TILE_TRANSITION)
print(f"  gPlayerAvatar.tileTransitionState (0x02037437): {tts}")
print(f"  (0=NOT_MOVING, 1=TILE_TRANSITION, 2=TILE_CENTER)")

# Full gPlayerAvatar struct (first 8 bytes)
print(f"\n  gPlayerAvatar bytes (0x02037434):")
for i in range(8):
    v = read8(ADDR_PLAYER_AVATAR + i)
    labels = {0: "flags", 1: "bikeState", 2: "acroBikeState",
              3: "tileTransitionState", 4: "running2", 5: "running1",
              6: "spriteId", 7: "objectEventId"}
    label = labels.get(i, "?")
    print(f"    +{i}: {v} (0x{v:02X}) [{label}]")

# === Check gMapHeader ===
print("\n--- gMapHeader ---")
map_header = read32(ADDR_MAP_HEADER)
if map_header and map_header > 0x08000000:
    print(f"  gMapHeader pointer: 0x{map_header:08X} (ROM - valid)")
    # Read map layout from the header (first few fields)
    # struct MapHeader { MapLayout*, ... EventData*, MapScripts*, ... }
    layout_ptr = read32(map_header)
    events_ptr = read32(map_header + 4)
    scripts_ptr = read32(map_header + 8)
    connections_ptr = read32(map_header + 12)
    print(f"    layout_ptr:      0x{layout_ptr:08X}" if layout_ptr else "    layout: None")
    print(f"    events_ptr:      0x{events_ptr:08X}" if events_ptr else "    events: None")
    print(f"    scripts_ptr:     0x{scripts_ptr:08X}" if scripts_ptr else "    scripts: None")
    print(f"    connections_ptr: 0x{connections_ptr:08X}" if connections_ptr else "    conns: None")
else:
    print(f"  gMapHeader: 0x{map_header:08X} (INVALID!)")

# === Check gMain.callback2 ===
# gMain address unknown for JP - scan IWRAM for CB2_Overworld pattern
print("\n--- Scanning for gMain.callback2 (CB2_Overworld) ---")
# CB2_Overworld is a ROM function. We look for 0x08xxxxxx pattern in IWRAM
# gMain is roughly around 0x03002470 (estimated)
# Let's scan a wider range
candidates = []
for addr in range(0x03000000, 0x03008000, 4):
    v = read32(addr)
    if v and 0x08000000 < v < 0x09000000:
        # Check if the next word is also a ROM pointer (callback1, callback2 are adjacent)
        v_prev = read32(addr - 4)
        if v_prev and 0x08000000 < v_prev < 0x09000000:
            # Two consecutive ROM pointers = likely callback1 + callback2
            candidates.append((addr - 4, v_prev, v))

print(f"  Found {len(candidates)} consecutive ROM pointer pairs in IWRAM:")
for base, cb1, cb2 in candidates[:10]:
    # Check if this looks like gMain (vblankCounter at +0x20 should be large)
    vblank = read32(base + 0x20)
    print(f"    0x{base:08X}: cb1=0x{cb1:08X} cb2=0x{cb2:08X} vblank@+0x20={vblank}")

# === Check Repel ===
print("\n--- Repel Status ---")
if sb1:
    repel = read16(sb1 + 0x13DE)
    print(f"  VAR_REPEL_STEP_COUNT (SB1+0x13DE): {repel}")

# === Check battle flags ===
print("\n--- Battle Flags ---")
btl = read32(ADDR_BATTLE_FLAGS)
print(f"  gBattleTypeFlags: 0x{btl:08X}" if btl else "  gBattleTypeFlags: None")

# === Walk test with tileTransitionState monitoring ===
print("\n" + "=" * 60)
print("WALK TEST - Monitoring tileTransitionState")
print("=" * 60)

# Clear BOTH sWildEncountersDisabled candidates
write8(ADDR_WILD_DISABLED_OLD, 0)
write8(ADDR_WILD_DISABLED_NEW, 0)

prev_x = read16(sb1) if sb1 else 0
prev_y = read16(sb1 + 2) if sb1 else 0
moved = 0
tile_center_count = 0
encounters = 0

for i in range(100):
    # Clear both wild disabled addresses every step
    write8(ADDR_WILD_DISABLED_OLD, 0)
    write8(ADDR_WILD_DISABLED_NEW, 0)

    # Tap a direction
    d = random.choice(["Up", "Down", "Up", "Down"])  # Stay on Route 101
    tap(d, 0.35)

    # Check tileTransitionState
    tts = read8(ADDR_TILE_TRANSITION)

    # Check position
    cx = read16(sb1) if sb1 else 0
    cy = read16(sb1 + 2) if sb1 else 0
    if cx != prev_x or cy != prev_y:
        moved += 1
        prev_x, prev_y = cx, cy

    if tts == 2:
        tile_center_count += 1

    # Battle check
    btl = read32(ADDR_BATTLE_FLAGS)
    if btl and btl > 0:
        encounters += 1
        print(f"\n  *** ENCOUNTER at step {i}! ***")
        print(f"  btlFlags=0x{btl:08X} pos=({cx},{cy})")
        break

    if i % 10 == 0:
        mg2 = read8(sb1 + 4) if sb1 else 0
        mn2 = read8(sb1 + 5) if sb1 else 0
        wd_old = read8(ADDR_WILD_DISABLED_OLD)
        wd_new = read8(ADDR_WILD_DISABLED_NEW)
        print(f"  i={i:3d}: ({cx},{cy}) Map({mg2},{mn2}) "
              f"tts={tts} moved={moved} center={tile_center_count} "
              f"wild_old={wd_old} wild_new={wd_new}")

print(f"\n--- Walk Test Results ---")
print(f"  Steps: {moved}")
print(f"  T_TILE_CENTER reached: {tile_center_count} times")
print(f"  Encounters: {encounters}")
if tile_center_count == 0 and moved > 0:
    print(f"  WARNING: Player moved but tileTransitionState never reached CENTER!")
    print(f"  This means the encounter check function is NEVER called.")

# === Additional: Check what tileTransitionState looks like during movement ===
print(f"\n--- Detailed tileTransitionState during single step ---")
# Hold Up for a while and sample tileTransitionState rapidly
try:
    session.post(f"{BASE_URL}/mgba-http/button/hold", params={"button": "Up"}, timeout=0.5)
except:
    pass

tts_samples = []
for _ in range(30):
    tts = read8(ADDR_TILE_TRANSITION)
    tts_samples.append(tts)
    time.sleep(0.02)  # 20ms between samples

try:
    session.post(f"{BASE_URL}/mgba-http/button/release", params={"button": "Up"}, timeout=0.5)
except:
    pass
time.sleep(0.3)

print(f"  tileTransitionState samples during Up hold: {tts_samples}")
print(f"  Unique values: {set(tts_samples)}")
print(f"  Value 2 (CENTER) appeared: {tts_samples.count(2)} times")
