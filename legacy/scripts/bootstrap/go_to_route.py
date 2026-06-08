"""
Navigate to Route 101 exit at (10, 21) via Left, then walk on route to find wild battle.
"""
import requests
import time
import random

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
    r = session.post(f"{MGBA_URL}/core/write8", params={"address": hex(addr), "value": value}, timeout=3)
    return r.status_code == 200


def tap(button, delay=0.06):
    session.post(f"{MGBA_URL}/mgba-http/button/tap", params={"button": button}, timeout=3)
    time.sleep(delay)


def get_map():
    sb1 = read32(0x03005AEC)
    if not sb1 or sb1 < 0x02000000 or sb1 > 0x0203FFFF:
        return -1, -1
    return read8(sb1 + 4), read8(sb1 + 5)


def get_pos():
    return read16(0x02037000), read16(0x02037002)


def save_slot(slot):
    r = session.post(f"{MGBA_URL}/core/savestateslot", params={"slot": slot}, timeout=5)
    return r.status_code == 200


def is_battle():
    bf = read32(0x02022E90)
    return bf is not None and 0 < bf < 0x10000


def handle_battle():
    print("    Fighting...", end="", flush=True)
    for k in range(300):
        tap("A", 0.06)
        if k % 50 == 49:
            tap("B", 0.06)
        if not is_battle():
            hp = read16(0x020241E6)
            hp_max = read16(0x020241E8)
            level = read8(0x020241E4)
            print(f" Done! HP:{hp}/{hp_max} Lv{level}")
            return True
    print(" Timeout")
    return False


def main():
    print("=" * 50)
    print("  Go to Route 101")
    print("=" * 50)

    # Ensure flags are set
    sb1 = read32(0x03005AEC)
    flag_addr = sb1 + 0x1270 + 256
    write8(flag_addr, read8(flag_addr) | 0x03)
    fb = read8(flag_addr)
    print(f"Flags: POKEMON_GET={bool(fb & 1)}, POKEDEX_GET={bool(fb & 2)}")

    x, y = get_pos()
    mg, mn = get_map()
    print(f"Start: ({x}, {y}) Map({mg}, {mn})")

    # Step 1: Navigate to exit at (10, 21)
    print("\n[1] Navigating to exit (10, 21)...")
    for i in range(500):
        x, y = get_pos()
        mg, mn = get_map()

        # If we accidentally left Map(0,9), come back
        if (mg, mn) != (0, 9):
            print(f"    Left town! Map({mg}, {mn}), checking if Route 101...")
            if (mg, mn) == (0, 16):
                print("    Already on Route 101!")
                break
            # Go right to get back
            for _ in range(10):
                tap("Right", 0.05)
                if get_map() == (0, 9):
                    break
            continue

        # Move towards (10, 21)
        if x > 10:
            tap("Left", 0.05)
        elif x < 10:
            tap("Right", 0.05)
        elif y > 21:
            tap("Up", 0.05)
        elif y < 21:
            tap("Down", 0.05)
        else:
            # At (10, 21), go Left to enter Route 101
            tap("Left", 0.05)
            mg, mn = get_map()
            if (mg, mn) == (0, 16):
                x, y = get_pos()
                print(f"    Entered Route 101! ({x}, {y})")
                break

        if i % 50 == 49:
            print(f"    [{i+1}] ({x}, {y}) Map({mg}, {mn})")

    # Step 2: Walk on Route 101 to find wild battle
    mg, mn = get_map()
    x, y = get_pos()
    print(f"\n[2] On Map({mg}, {mn}) at ({x}, {y})")
    print("    Walking to trigger wild encounter...")

    maps_seen = {(mg, mn)}
    for i in range(5000):
        mg, mn = get_map()

        # Check for battle
        if is_battle():
            x, y = get_pos()
            print(f"\n    WILD BATTLE at ({x}, {y}) Map({mg}, {mn})!")
            handle_battle()

            print("\n[3] SUCCESS! Saving to all slots...")
            for slot in [1, 2, 3]:
                ok = save_slot(slot)
                print(f"    Slot {slot}: {'OK' if ok else 'FAIL'}")

            x, y = get_pos()
            mg, mn = get_map()
            hp = read16(0x020241E6)
            hp_max = read16(0x020241E8)
            level = read8(0x020241E4)
            party = read8(0x0202418D)
            sb1 = read32(0x03005AEC)
            fb = read8(sb1 + 0x1270 + 256)
            print(f"\n    Final: ({x}, {y}) Map({mg}, {mn})")
            print(f"    HP:{hp}/{hp_max} Lv{level} Party:{party}")
            print(f"    Flags: POKEMON_GET={bool(fb & 1)}, POKEDEX_GET={bool(fb & 2)}")
            return True

        # If we accidentally enter a building, get out
        if mg != 0:
            for _ in range(20):
                tap("Down", 0.04)
                if get_map()[0] == 0:
                    break
            continue

        # Track new maps
        cur = (mg, mn)
        if cur not in maps_seen:
            maps_seen.add(cur)
            x, y = get_pos()
            print(f"    [{i}] New map: ({mg}, {mn}) at ({x}, {y})")

        # Walk around on the route - random to cover grass tiles
        d = random.choice(["Up", "Up", "Down", "Left", "Right"])
        tap(d, 0.04)

        if i % 500 == 499:
            x, y = get_pos()
            print(f"    [{i+1}] ({x}, {y}) Map({mg}, {mn}) maps={len(maps_seen)}")

    print("\n    No wild battles in 5000 steps")
    return False


if __name__ == "__main__":
    try:
        success = main()
        if success:
            print("\n  Bootstrap COMPLETE!")
        else:
            print("\n  Bootstrap incomplete.")
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
