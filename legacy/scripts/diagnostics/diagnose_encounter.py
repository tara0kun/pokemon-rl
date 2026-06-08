"""
エンカウント診断: セーブステートの状態を詳細に調査
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

def write8(addr, val):
    try:
        session.post(f"{BASE_URL}/core/write8", params={"address": hex(addr), "value": val}, timeout=1)
        return True
    except:
        return False

def write16(addr, val):
    try:
        session.post(f"{BASE_URL}/core/write16", params={"address": hex(addr), "value": val}, timeout=1)
        return True
    except:
        return False

def tap(button, delay=0.05):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/tap", params={"button": button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

# === Load save state slot 1 ===
print("=" * 60)
print("ENCOUNTER DIAGNOSTIC")
print("=" * 60)

print("\n--- Loading save state slot 1 ---")
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

# === SaveBlock1 Analysis ===
SB1_PTR_ADDR = 0x03005AEC  # JP ROM
sb1 = read32(SB1_PTR_ADDR)
print(f"\nSB1 pointer: 0x{sb1:08X}" if sb1 else "SB1 pointer: FAILED")

if sb1 and sb1 > 0x02000000:
    # Position
    x = read16(sb1)
    y = read16(sb1 + 2)
    mg = read8(sb1 + 4)
    mn = read8(sb1 + 5)
    print(f"Position: ({x}, {y}) Map({mg},{mn})")

    # Repel
    repel = read16(sb1 + 0x13DE)
    print(f"VAR_REPEL_STEP_COUNT (+13DE): {repel}")

    # Route 101 state: VAR_ROUTE101_STATE = 0x4050
    # Vars at SB1+0x139C, index = 0x4050-0x4000 = 0x50, offset = 0x50*2 = 0xA0
    route101_state = read16(sb1 + 0x139C + 0xA0)
    print(f"VAR_ROUTE101_STATE (+143C): {route101_state}")

    # Other important vars
    # VAR_LITTLEROOT_STATE = 0x4050 + something...
    # Let's scan a range of vars
    print("\n--- Game Variables (VAR_0x4000 to VAR_0x4060) ---")
    for i in range(0x00, 0x61):
        var_addr = sb1 + 0x139C + i * 2
        v = read16(var_addr)
        if v is not None and v != 0:
            var_id = 0x4000 + i
            print(f"  VAR_{var_id:04X} (+{0x139C + i*2:04X}): {v} (0x{v:04X})")

    # Key flags
    print("\n--- Key Flags ---")
    flags_base = sb1 + 0x1270

    # FLAG_SYS_POKEMON_GET = 0x860
    # Flag byte = 0x860 / 8 = 0x10C, bit = 0x860 % 8 = 0
    flag_byte = read8(flags_base + 0x10C)
    pokemon_get = (flag_byte >> 0) & 1 if flag_byte is not None else None
    print(f"  FLAG_SYS_POKEMON_GET (0x860): {pokemon_get} (byte=0x{flag_byte:02X})" if flag_byte else "  FLAG_SYS_POKEMON_GET: FAILED")

    # FLAG_SYS_POKEDEX_GET = 0x801
    flag_byte = read8(flags_base + 0x100)
    pokedex_get = (flag_byte >> 1) & 1 if flag_byte is not None else None
    print(f"  FLAG_SYS_POKEDEX_GET (0x801): {pokedex_get}" if flag_byte is not None else "  FLAG_SYS_POKEDEX_GET: FAILED")

    # FLAG_SYS_NATIONAL_DEX = 0x840
    flag_byte = read8(flags_base + 0x108)
    nat_dex = (flag_byte >> 0) & 1 if flag_byte is not None else None
    print(f"  FLAG_SYS_NATIONAL_DEX (0x840): {nat_dex}" if flag_byte is not None else "  FLAG_SYS_NATIONAL_DEX: FAILED")

    # FLAG_RECEIVED_POKEDEX_FROM_BIRCH = 0x0C1
    flag_byte = read8(flags_base + 0x18)
    birch_dex = (flag_byte >> 1) & 1 if flag_byte is not None else None
    print(f"  FLAG_RECEIVED_POKEDEX_FROM_BIRCH (0x0C1): {birch_dex}" if flag_byte is not None else "  FAILED")

# === EWRAM Scan for sWildEncountersDisabled ===
print("\n--- Scanning for sWildEncountersDisabled ---")
print("(Searching EWRAM 0x02039000 - 0x0203B000 for isolated byte=1 patterns)")

# The variable is typically near other wild_encounter.c statics
# In US ROM it's around 0x02039xxx. JP ROM offset might differ.
# Let's check a broader range
candidates = []
for addr in range(0x02039000, 0x0203B000, 1):
    v = read8(addr)
    if v == 1:
        # Check if it's isolated (neighbors are 0 or small)
        prev_v = read8(addr - 1)
        next_v = read8(addr + 1)
        if prev_v == 0 and next_v == 0:
            candidates.append(addr)

print(f"Found {len(candidates)} candidates where byte=1 with 0-neighbors:")
for addr in candidates[:20]:
    print(f"  0x{addr:08X}")

# === Party Check ===
print("\n--- Party Status ---")
party_count = read8(0x0202418D)
print(f"Party count: {party_count}")

hp_cur = read16(0x020241E6)
hp_max = read16(0x020241E8)
level = read8(0x020241E4)
print(f"Lead: Lv{level} HP={hp_cur}/{hp_max}")

# === gBattleTypeFlags ===
btl = read32(0x02022E90)
print(f"\ngBattleTypeFlags: {btl} (0x{btl:08X})" if btl else "gBattleTypeFlags: 0")

# === Walk Test with longer delay ===
print("\n--- Walking Test (50 steps, 300ms delay) ---")
print("Testing if encounters happen with proper step timing...")

# First clear any scripts by pressing A/B a few times
for _ in range(20):
    tap("A", 0.1)
for _ in range(10):
    tap("B", 0.1)

# Clear repel just in case
write16(sb1 + 0x13DE, 0)

# Walk with long delay
prev_x, prev_y = read16(sb1), read16(sb1 + 2)
steps = 0
encounters = 0

for i in range(50):
    direction = ["Up", "Down", "Up", "Left", "Up", "Right", "Up", "Up"][i % 8]
    tap(direction, 0.3)  # 300ms = 18 frames, enough for full step

    cx = read16(sb1)
    cy = read16(sb1 + 2)
    if cx != prev_x or cy != prev_y:
        steps += 1
        prev_x, prev_y = cx, cy

    # Check for battle
    btl = read32(0x02022E90)
    if btl and btl > 0:
        mg = read8(sb1 + 4)
        mn = read8(sb1 + 5)
        encounters += 1
        print(f"  [ENCOUNTER!] Step {i}, btlFlags=0x{btl:08X}, "
              f"pos=({cx},{cy}) Map({mg},{mn})")
        break

    if i % 10 == 0:
        mg = read8(sb1 + 4)
        mn = read8(sb1 + 5)
        repel_v = read16(sb1 + 0x13DE)
        print(f"  Step {i}: pos=({cx},{cy}) Map({mg},{mn}) "
              f"moved={steps} repel={repel_v}")

print(f"\nResult: {steps} actual steps, {encounters} encounters in 50 taps")

# === Walk Test 2: Even longer delay ===
if encounters == 0:
    print("\n--- Walking Test 2 (50 steps, 500ms delay) ---")
    for i in range(50):
        direction = ["Up", "Down", "Up", "Left", "Up", "Right", "Up", "Up"][i % 8]
        tap(direction, 0.5)  # 500ms = 30 frames

        cx = read16(sb1)
        cy = read16(sb1 + 2)
        if cx != prev_x or cy != prev_y:
            steps += 1
            prev_x, prev_y = cx, cy

        btl = read32(0x02022E90)
        if btl and btl > 0:
            encounters += 1
            print(f"  [ENCOUNTER!] Step {i}, btlFlags=0x{btl:08X}")
            break

        if i % 10 == 0:
            mg = read8(sb1 + 4)
            mn = read8(sb1 + 5)
            print(f"  Step {i}: pos=({cx},{cy}) Map({mg},{mn}) moved={steps}")

    print(f"Result: {steps} total steps, {encounters} encounters")

print("\n" + "=" * 60)
print("DIAGNOSTIC COMPLETE")
print("=" * 60)
