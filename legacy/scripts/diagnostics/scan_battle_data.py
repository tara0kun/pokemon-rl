"""
Scan EWRAM for gBattleMons data during a battle.
Run while emulator port 5000 is IN a wild battle (CB2=0x080857C5).
Searches for valid BattlePokemon structs (species > 0, HP > 0, level 2-100).
"""
import time
import requests

PORT = 5000
BASE = f"http://localhost:{PORT}"
session = requests.Session()

# BattlePokemon struct size = 0x58 (88 bytes)
# Key fields:
#   +0x00: species (u16)
#   +0x28: HP (u16)
#   +0x2A: level (u8)
#   +0x2C: maxHP (u16)
#   +0x21: type1 (u8)
#   +0x22: type2 (u8)

BMON_SIZE = 0x58
OFF_SPECIES = 0x00
OFF_HP = 0x28
OFF_LEVEL = 0x2A
OFF_MAXHP = 0x2C
OFF_TYPE1 = 0x21


def _read(endpoint, addr):
    try:
        r = session.get(f"{BASE}/core/{endpoint}", params={"address": hex(addr)}, timeout=0.5)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except Exception:
        return None


def read8(addr): return _read("read8", addr)
def read16(addr): return _read("read16", addr)
def read32(addr): return _read("read32", addr)


def check_battler(base):
    """Check if base looks like a valid BattlePokemon struct."""
    species = read16(base + OFF_SPECIES)
    hp = read16(base + OFF_HP)
    maxhp = read16(base + OFF_MAXHP)
    level = read8(base + OFF_LEVEL)
    type1 = read8(base + OFF_TYPE1)

    if species is None or hp is None or maxhp is None or level is None:
        return None

    # Valid BattlePokemon: species 1-386, level 2-100, maxhp 10-999, type 0-17
    if 1 <= species <= 400 and 2 <= level <= 100 and 5 <= maxhp <= 999 and type1 <= 17:
        return {
            "species": species, "hp": hp, "maxhp": maxhp,
            "level": level, "type1": type1
        }
    return None


def main():
    # First verify we're in battle
    cb2 = read32(0x03002364) or 0
    print(f"CB2 = 0x{cb2:08X}")
    if cb2 != 0x080857C5:
        print("WARNING: Not in battle (CB2 != 0x080857C5)")
        print("Start a wild battle first!")
        return

    print("In battle! Scanning EWRAM for gBattleMons...")
    print()

    # Scan EWRAM 0x02020000 - 0x02040000 (128KB) in steps
    # gBattleMons[0] should contain player's pokemon
    # gBattleMons[1] (offset +0x58) should contain enemy's pokemon
    # Scan at 4-byte alignment

    found = []
    scan_start = 0x02020000
    scan_end = 0x02040000

    for addr in range(scan_start, scan_end, 4):
        if (addr - scan_start) % 0x1000 == 0:
            progress = (addr - scan_start) / (scan_end - scan_start) * 100
            print(f"  Scanning 0x{addr:08X}... ({progress:.0f}%)", flush=True)

        result = check_battler(addr)
        if result:
            # Also check the next slot (gBattleMons[1])
            result2 = check_battler(addr + BMON_SIZE)
            if result2:
                print(f"\n  *** FOUND gBattleMons candidate at 0x{addr:08X} ***")
                print(f"  [0] (player): species={result['species']} "
                      f"HP={result['hp']}/{result['maxhp']} "
                      f"Lv{result['level']} type={result['type1']}")
                print(f"  [1] (enemy):  species={result2['species']} "
                      f"HP={result2['hp']}/{result2['maxhp']} "
                      f"Lv{result2['level']} type={result2['type1']}")
                found.append(addr)
            else:
                # Single valid entry, still interesting
                print(f"\n  Partial match at 0x{addr:08X}: "
                      f"species={result['species']} HP={result['hp']}/{result['maxhp']} "
                      f"Lv{result['level']} type={result['type1']}")

    print(f"\n\nScan complete. Found {len(found)} gBattleMons candidates:")
    for addr in found:
        jp_off = addr - 0x02023F28
        print(f"  0x{addr:08X} (US offset: {jp_off:+#x})")


if __name__ == "__main__":
    main()
