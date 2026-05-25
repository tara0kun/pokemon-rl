"""
Scan EWRAM to find correct JP addresses for gPlayerAvatar and gMapHeader.
The -0x15C offset may not apply uniformly across all EWRAM BSS sections.
"""
import requests
import time

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

def tap(button, delay=0.4):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/tap", params={"button": button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

SB1_PTR = 0x03005AEC

print("=" * 60)
print("EWRAM Address Scanner for JP Emerald")
print("=" * 60)

# Load slot 1
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

sb1 = read32(SB1_PTR)
x = read16(sb1) if sb1 else None
y = read16(sb1 + 2) if sb1 else None
print(f"SB1=0x{sb1:08X} Position=({x},{y})")

# === Scan 1: Find gMapHeader ===
# gMapHeader should be a ROM pointer (0x08xxxxxx) pointing to the current map's header
# Scan EWRAM 0x02037000-0x02038000 (expected range)
print("\n--- Scanning for gMapHeader (ROM pointers in EWRAM) ---")
rom_ptrs = []
for addr in range(0x02036000, 0x02039000, 4):
    v = read32(addr)
    if v and 0x08000000 < v < 0x09000000:
        rom_ptrs.append((addr, v))

print(f"Found {len(rom_ptrs)} ROM pointers in 0x02036000-0x02039000:")
for addr, val in rom_ptrs:
    us_equiv = addr + 0x15C
    print(f"  0x{addr:08X}: 0x{val:08X}  (US equiv: 0x{us_equiv:08X})")

# === Scan 2: Find gPlayerAvatar by tileTransitionState pattern ===
# gPlayerAvatar should have small values (0-10) for its fields
# tileTransitionState at +3 should be 0, 1, or 2
print("\n--- Scanning for gPlayerAvatar (small struct with tts) ---")
avatar_candidates = []
for addr in range(0x02036000, 0x02039000):
    b0 = read8(addr)      # flags
    b1 = read8(addr + 1)  # bikeState (0-2)
    b2 = read8(addr + 2)  # acroBikeState (0-6)
    b3 = read8(addr + 3)  # tileTransitionState (0-2)
    if b1 is not None and b3 is not None:
        if 0 <= b1 <= 2 and 0 <= b2 <= 6 and 0 <= b3 <= 2:
            if b0 is not None and b0 < 0x20:  # flags should be small
                avatar_candidates.append((addr, b0, b1, b2, b3))

print(f"Found {len(avatar_candidates)} candidates:")
for addr, b0, b1, b2, b3 in avatar_candidates[:20]:
    us_equiv = addr + 0x15C
    print(f"  0x{addr:08X}: flags=0x{b0:02X} bike={b1} acro={b2} tts={b3} "
          f"(US equiv: 0x{us_equiv:08X})")

# === Scan 3: Walk and look for tileTransitionState changing ===
# This is the most reliable way - find any byte in EWRAM that changes between 0,1,2
# during player movement
print("\n--- Dynamic scan: walking to find tileTransitionState ---")

# First, snapshot a region of EWRAM while standing still
print("  Taking snapshot while standing still...")
snapshot = {}
for addr in range(0x02036000, 0x02038000):
    v = read8(addr)
    if v is not None:
        snapshot[addr] = v

# Now walk one step
print("  Walking one step Up...")
tap("Up", 0.5)

# Read all values again and find changes
changed = []
for addr in range(0x02036000, 0x02038000):
    v = read8(addr)
    if v is not None and addr in snapshot:
        if v != snapshot[addr]:
            changed.append((addr, snapshot[addr], v))

print(f"  {len(changed)} bytes changed during one step:")
for addr, old, new in changed[:30]:
    us_equiv = addr + 0x15C
    print(f"    0x{addr:08X}: {old} -> {new} (US equiv: 0x{us_equiv:08X})")

# === Scan 4: Check if game is actually running ===
print("\n--- Check: Is the game actually advancing frames? ---")
# Read a known timer or frame counter
# VBlank counter is in gMain struct, but we don't know gMain's address
# Try reading IWRAM around the expected gMain area
for base in [0x03000F14, 0x030024D0, 0x03002470, 0x03002710]:
    v1 = read32(base + 0x20)  # vblankCounter1
    time.sleep(0.1)
    v2 = read32(base + 0x20)
    if v1 is not None and v2 is not None and v2 > v1:
        print(f"  0x{base:08X}: vblank counter advancing ({v1} -> {v2})")
        print(f"  This is likely gMain!")
        # Also check callback2 at +0x04
        cb2 = read32(base + 0x04)
        print(f"  callback2 = 0x{cb2:08X}")
        break
else:
    # Try scanning more broadly
    print("  No advancing counter found in expected range. Scanning...")
    for base in range(0x03000000, 0x03004000, 0x100):
        v1 = read32(base + 0x20)
        time.sleep(0.05)
        v2 = read32(base + 0x20)
        if v1 is not None and v2 is not None and v2 != v1 and abs(v2 - v1) < 100:
            print(f"  0x{base:08X}+0x20: counter {v1} -> {v2} (diff={v2-v1})")
