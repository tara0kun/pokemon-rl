"""Check all save state slots and find a good one."""
import requests
import time

MGBA_URL = "http://localhost:5000"
session = requests.Session()


def read_val(ep, addr):
    for _ in range(3):
        try:
            v = session.get(f"{MGBA_URL}/core/{ep}", params={"address": hex(addr)}, timeout=5).json()
            return int(v["value"]) if isinstance(v, dict) else int(v)
        except:
            time.sleep(0.3)
    return 0


def tap(button, delay=0.04):
    try:
        session.post(f"{MGBA_URL}/mgba-http/button/tap", params={"button": button}, timeout=3)
    except:
        pass
    time.sleep(delay)


def get_pos():
    return read_val("read16", 0x02037000), read_val("read16", 0x02037002)


def get_map():
    sb1 = read_val("read32", 0x03005AEC)
    if not sb1 or sb1 < 0x02000000 or sb1 > 0x0203FFFF:
        return -1, -1
    return read_val("read8", sb1 + 4), read_val("read8", sb1 + 5)


# Save current state first
print("Saving current state to slot 9...")
session.post(f"{MGBA_URL}/core/savestateslot", params={"slot": 9}, timeout=5)
time.sleep(0.3)

# Check each slot
for slot in range(1, 10):
    print(f"\n--- Slot {slot} ---")
    try:
        session.post(f"{MGBA_URL}/core/loadstateslot", params={"slot": slot}, timeout=5)
        time.sleep(0.5)

        x, y = get_pos()
        mg, mn = get_map()
        sb1 = read_val("read32", 0x03005AEC)

        if sb1 < 0x02000000 or sb1 > 0x0203FFFF:
            print(f"  Invalid SaveBlock1: {hex(sb1)}")
            continue

        # Party info
        party_count = read_val("read8", 0x0202418D)
        hp = read_val("read16", 0x020241E6)
        hp_max = read_val("read16", 0x020241E8)
        level = read_val("read8", 0x020241E4)

        # Flags
        flags_base = sb1 + 0x1270
        byte_860 = read_val("read8", flags_base + (0x860 // 8))
        flag_pokemon = (byte_860 >> (0x860 % 8)) & 1
        byte_800 = read_val("read8", flags_base + (0x800 // 8))
        flag_jp = (byte_800 >> (0x800 % 8)) & 1

        # Test movement
        before = get_pos()
        tap("Up", 0.05)
        tap("Down", 0.05)
        after = get_pos()
        can_move = before != after or True  # Just check position changed

        # Quick movement test
        pos1 = get_pos()
        for _ in range(5):
            tap("Up", 0.04)
        pos2 = get_pos()
        moved = pos1 != pos2

        print(f"  Pos: ({x},{y}) Map({mg},{mn})")
        print(f"  Party: {party_count} HP:{hp}/{hp_max} Lv{level}")
        print(f"  Flags: SYS_POKEMON={flag_pokemon} JP_800={flag_jp}")
        print(f"  Can move: {moved}")

    except Exception as e:
        print(f"  Error loading slot: {e}")

# Restore current state
print("\n\nRestoring slot 9...")
session.post(f"{MGBA_URL}/core/loadstateslot", params={"slot": 9}, timeout=5)
time.sleep(0.5)
x, y = get_pos()
mg, mn = get_map()
print(f"Restored: ({x},{y}) Map({mg},{mn})")
