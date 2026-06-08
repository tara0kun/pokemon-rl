"""
Fix game state v6: set flags, exit house, navigate to route - NO soft reset.
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


def tap(button, delay=0.08):
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
    print("=" * 60)
    print("  Fix and Navigate v6 (no reset)")
    print("=" * 60)

    # Check current state
    x, y = get_pos()
    mg, mn = get_map()
    hp = read16(0x020241E6)
    hp_max = read16(0x020241E8)
    level = read8(0x020241E4)
    party = read8(0x0202418D)
    print(f"\n[1] Current: ({x}, {y}) Map({mg}, {mn})")
    print(f"    HP:{hp}/{hp_max} Lv{level} Party:{party}")

    # Set wild encounter flags
    print("\n[2] Setting flags...")
    sb1 = read32(0x03005AEC)
    flag_addr = sb1 + 0x1270 + 256
    cur = read8(flag_addr)
    write8(flag_addr, cur | 0x03)
    after = read8(flag_addr)
    print(f"    POKEMON_GET={bool(after & 1)}, POKEDEX_GET={bool(after & 2)}")

    # If indoor (map group != 0), exit building
    mg, mn = get_map()
    if mg != 0:
        print(f"\n[3] In building Map({mg}, {mn}), exiting...")
        start_map = (mg, mn)
        for i in range(3000):
            tap(random.choice(["Up", "Down", "Down", "Left", "Right", "A", "B"]), 0.04)
            mg, mn = get_map()
            if mg == 0:
                x, y = get_pos()
                print(f"    Exited to Map({mg}, {mn}) at ({x}, {y}) after {i} steps")
                break
            if i % 500 == 499:
                x, y = get_pos()
                print(f"    [{i+1}] Still Map({mg}, {mn}) at ({x}, {y})")
        else:
            print("    Failed to exit after 3000 steps!")
            return False

    # Now outdoors - save backup
    x, y = get_pos()
    mg, mn = get_map()
    print(f"\n[4] OUTDOOR: ({x}, {y}) Map({mg}, {mn})")
    save_slot(3)
    print("    Saved backup to slot 3")

    # Navigate to find wild pokemon
    # Strategy: go North, but if we enter a building, immediately exit by spamming Down
    print("\n[5] Walking to find wild encounters...")
    maps_seen = {(mg, mn)}

    for i in range(8000):
        mg, mn = get_map()

        # If entered building, escape immediately
        if mg != 0:
            for _ in range(30):
                tap("Down", 0.04)
                mg, mn = get_map()
                if mg == 0:
                    break
            if mg != 0:
                for _ in range(50):
                    tap(random.choice(["Down", "Left", "Right", "B"]), 0.04)
                    mg, mn = get_map()
                    if mg == 0:
                        break
            continue

        # Check battle
        if is_battle():
            x, y = get_pos()
            print(f"\n    WILD BATTLE at ({x}, {y}) Map({mg}, {mn})!")
            handle_battle()

            # Save to all slots
            print("\n[6] SUCCESS! Saving...")
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

        cur = (mg, mn)
        if cur not in maps_seen:
            maps_seen.add(cur)
            x, y = get_pos()
            print(f"    [{i}] New map: ({mg}, {mn}) at ({x}, {y})")

        # Movement: mostly Up (North), some lateral
        cycle = i % 20
        if cycle < 12:
            tap("Up", 0.04)
        elif cycle < 15:
            tap("Right", 0.04)
        elif cycle < 18:
            tap("Left", 0.04)
        else:
            tap("B", 0.04)

        if i % 1000 == 999:
            x, y = get_pos()
            print(f"    [{i+1}] ({x}, {y}) Map({mg}, {mn}) maps={len(maps_seen)}")

    print("\n    No wild battles in 8000 steps")
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
