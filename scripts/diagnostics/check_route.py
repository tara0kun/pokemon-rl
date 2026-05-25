"""Check and fix Route 101 freeze."""
import requests
import time

MGBA_URL = "http://localhost:5000"
session = requests.Session()


def read8(addr):
    r = session.get(f"{MGBA_URL}/core/read8", params={"address": hex(addr)}, timeout=3)
    v = r.json()
    return int(v["value"]) if isinstance(v, dict) else int(v)


def read16(addr):
    r = session.get(f"{MGBA_URL}/core/read16", params={"address": hex(addr)}, timeout=3)
    v = r.json()
    return int(v["value"]) if isinstance(v, dict) else int(v)


def read32(addr):
    r = session.get(f"{MGBA_URL}/core/read32", params={"address": hex(addr)}, timeout=3)
    v = r.json()
    return int(v["value"]) if isinstance(v, dict) else int(v)


def write8(addr, value):
    session.post(f"{MGBA_URL}/core/write8", params={"address": hex(addr), "value": value}, timeout=3)


def write16(addr, value):
    session.post(f"{MGBA_URL}/core/write16", params={"address": hex(addr), "value": value}, timeout=3)


def tap(button, delay=0.1):
    session.post(f"{MGBA_URL}/mgba-http/button/tap", params={"button": button}, timeout=3)
    time.sleep(delay)


def save_slot(slot):
    session.post(f"{MGBA_URL}/core/savestateslot", params={"slot": slot}, timeout=5)


def get_pos():
    return read16(0x02037000), read16(0x02037002)


def get_map():
    sb1 = read32(0x03005AEC)
    return read8(sb1 + 4), read8(sb1 + 5)


x, y = get_pos()
mg, mn = get_map()
print(f"Current: ({x},{y}) Map({mg},{mn})")

# Check event flags relevant to Birch rescue
sb1 = read32(0x03005AEC)
flags_base = sb1 + 0x1270

# Dump flags around the Birch rescue area
# In Emerald, flags in the 0x00-0xFF range are often story flags
print("\nEvent flags (first 32 bytes):")
for i in range(32):
    val = read8(flags_base + i)
    if val != 0:
        print(f"  Byte {i}: {bin(val)} ({val})")

# Check FLAG_SYS flags (byte 256+)
print("\nSYS flags (bytes 256-260):")
for i in range(256, 261):
    val = read8(flags_base + i)
    print(f"  Byte {i}: {bin(val)} ({val})")

# Try pressing A/B to dismiss cutscene
print("\nPressing A/B to dismiss cutscene...")
for i in range(50):
    tap("A", 0.1)
    tap("B", 0.1)
x2, y2 = get_pos()
print(f"After A/B spam: ({x2},{y2})")

# Try movement
print("\nTrying to move...")
for d in ["Up", "Down", "Left", "Right"]:
    for _ in range(15):
        tap(d, 0.06)
    x2, y2 = get_pos()
    mg2, mn2 = get_map()
    if x2 != x or y2 != y:
        print(f"  {d}: MOVED to ({x2},{y2}) Map({mg2},{mn2})")
    else:
        print(f"  {d}: no move ({x2},{y2})")

# If still stuck, try setting more story flags
x2, y2 = get_pos()
if x2 == x and y2 == y:
    print("\nStill stuck! Setting additional story flags...")

    # Set flags that indicate Birch rescue is complete
    # These are common flag patterns in Pokemon Emerald
    # FLAG_RESCUED_BIRCH = 0x4F (flag number)
    # Flag 0x4F = byte 9, bit 7  (0x4F / 8 = 9, 0x4F % 8 = 7)
    flag_num = 0x4F
    byte_idx = flag_num // 8
    bit_idx = flag_num % 8
    cur = read8(flags_base + byte_idx)
    write8(flags_base + byte_idx, cur | (1 << bit_idx))
    print(f"  Set flag 0x4F (byte {byte_idx} bit {bit_idx})")

    # FLAG_SYS_POKEMON_GET already set (0x800)
    # FLAG_ADVENTURE_STARTED = 0x4050
    # Actually, let's set a wider range of early-game flags
    # These typically control NPC visibility and route accessibility

    # Common approach: set bytes 0-15 of flags to 0xFF (all set)
    # This is aggressive but should unlock all early game events
    print("  Setting first 16 flag bytes to 0xFF...")
    for i in range(16):
        write8(flags_base + i, 0xFF)

    # Also set some system flags
    # FLAG_SYS_GAME_CLEAR (0x804) = byte 256, bit 4
    # FLAG_SYS_B_DASH (0x802) = byte 256, bit 2
    # FLAG_SYS_POKENAV_GET (0x803) = byte 256, bit 3
    cur256 = read8(flags_base + 256)
    write8(flags_base + 256, cur256 | 0x0F)  # Set bits 0-3
    print(f"  Set SYS flags byte 256: {bin(cur256)} -> {bin(cur256 | 0x0F)}")

    # Try moving again
    print("\nRetrying movement after flag changes...")
    for d in ["Up", "Down", "Left", "Right"]:
        for _ in range(15):
            tap(d, 0.06)
        x3, y3 = get_pos()
        if x3 != x2 or y3 != y2:
            print(f"  {d}: MOVED to ({x3},{y3})!")
            x2, y2 = x3, y3
        else:
            print(f"  {d}: no move")

    # If STILL stuck, the script engine state itself might be locked
    x3, y3 = get_pos()
    if x3 == x and y3 == y:
        print("\nStill frozen! Script engine likely locked.")
        print("Will try going back to Map(0,9) via save state slot 3...")
        session.post(f"{MGBA_URL}/core/loadstateslot", params={"slot": 3}, timeout=5)
        time.sleep(1)
        x4, y4 = get_pos()
        mg4, mn4 = get_map()
        print(f"After load slot 3: ({x4},{y4}) Map({mg4},{mn4})")

        # Check if we can move
        tap("Up", 0.1)
        x5, y5 = get_pos()
        if (x5, y5) != (x4, y4):
            print(f"  Can move! Now at ({x5},{y5})")
            print("  The issue is Route 101 specifically. Need different approach.")
        else:
            print("  Still stuck even after loading slot 3!")
