"""
Fast scan EWRAM for gBattleMons using readRange (bulk read).
Run while emulator port 5000 is IN a wild battle (CB2=0x080857C5).
"""
import struct
import requests

PORT = 5000
BASE = f"http://localhost:{PORT}"
session = requests.Session()

BMON_SIZE = 0x58
OFF_SPECIES = 0x00
OFF_HP = 0x28
OFF_LEVEL = 0x2A
OFF_MAXHP = 0x2C
OFF_TYPE1 = 0x21


def read32(addr):
    try:
        r = session.get(f"{BASE}/core/read32", params={"address": hex(addr)}, timeout=0.5)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None


def read_range(addr, length):
    """Read a range of bytes from memory. Returns bytes or None."""
    try:
        r = session.get(f"{BASE}/core/readRange",
                        params={"address": hex(addr), "length": length},
                        timeout=2.0)
        data = r.text.strip()
        return bytes(int(x, 16) for x in data.split(","))
    except Exception as e:
        print(f"  readRange error at 0x{addr:08X}: {e}")
        return None


def check_battler_in_data(data, offset):
    """Check if offset within data looks like a BattlePokemon struct."""
    if offset + BMON_SIZE > len(data):
        return None
    species = struct.unpack_from("<H", data, offset + OFF_SPECIES)[0]
    hp = struct.unpack_from("<H", data, offset + OFF_HP)[0]
    maxhp = struct.unpack_from("<H", data, offset + OFF_MAXHP)[0]
    level = data[offset + OFF_LEVEL]
    type1 = data[offset + OFF_TYPE1]

    if 1 <= species <= 400 and 2 <= level <= 100 and 5 <= maxhp <= 999 and type1 <= 17:
        return {"species": species, "hp": hp, "maxhp": maxhp, "level": level, "type1": type1}
    return None


def main():
    cb2 = read32(0x03002364) or 0
    print(f"CB2 = 0x{cb2:08X}")
    if cb2 != 0x080857C5:
        print("WARNING: Not in battle (CB2 != 0x080857C5)")

    print("Scanning EWRAM for gBattleMons (bulk read)...\n")

    found = []
    scan_start = 0x02020000
    scan_end = 0x02040000
    chunk_size = 4096  # Read 4KB at a time

    for addr in range(scan_start, scan_end, chunk_size):
        progress = (addr - scan_start) / (scan_end - scan_start) * 100
        if addr % 0x4000 == 0:
            print(f"  Scanning 0x{addr:08X}... ({progress:.0f}%)", flush=True)

        data = read_range(addr, chunk_size + BMON_SIZE * 2)
        if data is None:
            continue

        # Scan at 4-byte alignment for BattlePokemon pairs
        for off in range(0, chunk_size, 4):
            result = check_battler_in_data(data, off)
            if result:
                # Check next slot too (gBattleMons[1])
                result2 = check_battler_in_data(data, off + BMON_SIZE)
                real_addr = addr + off
                if result2:
                    print(f"\n  *** FOUND gBattleMons at 0x{real_addr:08X} ***")
                    print(f"  [0] player: species={result['species']:3d} "
                          f"HP={result['hp']:3d}/{result['maxhp']:3d} "
                          f"Lv{result['level']:2d} type={result['type1']}")
                    print(f"  [1] enemy:  species={result2['species']:3d} "
                          f"HP={result2['hp']:3d}/{result2['maxhp']:3d} "
                          f"Lv{result2['level']:2d} type={result2['type1']}")
                    found.append((real_addr, result, result2))

    print(f"\n\nScan complete. Found {len(found)} gBattleMons candidates:")
    for addr, p, e in found:
        us_diff = addr - 0x02023F28
        print(f"  0x{addr:08X} (diff from US: {us_diff:+#06x})")
        print(f"    Player: species={p['species']} Lv{p['level']} HP={p['hp']}/{p['maxhp']}")
        print(f"    Enemy:  species={e['species']} Lv{e['level']} HP={e['hp']}/{e['maxhp']}")


if __name__ == "__main__":
    main()
