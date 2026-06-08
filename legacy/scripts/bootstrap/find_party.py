"""Find the actual party count and party data addresses in JP Emerald."""
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


sb1 = read_val("read32", 0x03005AEC)
print(f"SaveBlock1: {hex(sb1)}")

# Known JP addresses from previous sessions
print("\n--- Known party addresses ---")
# These addresses showed valid data before
addrs = {
    "0x0202418D (party count?)": (0x0202418D, "read8"),
    "0x020241E4 (level)": (0x020241E4, "read8"),
    "0x020241E6 (hp)": (0x020241E6, "read16"),
    "0x020241E8 (max_hp)": (0x020241E8, "read16"),
}
for name, (addr, ep) in addrs.items():
    val = read_val(ep, addr)
    print(f"  {name}: {val}")

# Search for party count near known party data
# gPlayerParty JP should be around 0x02024190
# Party count should be nearby
print("\n--- Searching for party count near 0x02024190 ---")
for offset in range(-16, 17):
    addr = 0x02024190 + offset
    val = read_val("read8", addr)
    if val in [1, 2, 3, 4, 5, 6]:
        print(f"  {hex(addr)} (+{offset}): {val} <-- possible party count")
    elif val == 0 and offset in [-4, -3, -2, -1, 0]:
        print(f"  {hex(addr)} (+{offset}): {val}")

# Check SaveBlock1 party area more thoroughly
print(f"\n--- SaveBlock1 party area (sb1 + 0x230 to 0x250) ---")
for offset in range(0x230, 0x250):
    val = read_val("read8", sb1 + offset)
    if val != 0:
        print(f"  sb1+{hex(offset)}: {val}")

# Check if the SaveBlock1 party count is at a different JP offset
# Try common offsets
print(f"\n--- Scanning SaveBlock1 for party-like data ---")
for offset in range(0x200, 0x280):
    val = read_val("read8", sb1 + offset)
    if val in [1, 2, 3, 4, 5, 6]:
        # Check if next bytes could be species ID
        species = read_val("read16", sb1 + offset + 1)
        if 1 <= species <= 440:
            print(f"  sb1+{hex(offset)}: count={val}, next u16={species} (valid species!)")
        elif offset < 0x240:
            print(f"  sb1+{hex(offset)}: count={val}")

# Check the decrypted/working party data starting from 0x02024190
print(f"\n--- Party data at 0x02024190 (JP gPlayerParty) ---")
party_base = 0x02024190
for i in range(1):  # Just first Pokemon
    base = party_base + i * 100
    print(f"  Pokemon {i} at {hex(base)}:")
    personality = read_val("read32", base)
    otid = read_val("read32", base + 4)
    nickname_bytes = []
    for b in range(10):
        nickname_bytes.append(read_val("read8", base + 8 + b))
    species_growth = read_val("read16", base + 32)
    print(f"    Personality: {hex(personality)}")
    print(f"    OT ID: {hex(otid)}")
    print(f"    Nickname bytes: {nickname_bytes}")
    print(f"    Substructure[0] u16: {species_growth}")

    # Decrypted stats area (after encrypted substructures)
    # In the Pokemon struct, decrypted stats start at offset 0x50
    hp = read_val("read16", base + 0x56)
    max_hp = read_val("read16", base + 0x58)
    level = read_val("read8", base + 0x54)
    attack = read_val("read16", base + 0x5A)
    print(f"    Stats: HP={hp}/{max_hp} Lv{level} Atk={attack}")

# The actual answer: what does the GAME see as party count?
# In pokeemerald, gPlayerPartyCount is a separate EWRAM variable
# US: let's scan nearby the gPlayerParty address
print(f"\n--- Scanning for gPlayerPartyCount in EWRAM ---")
# Search a wider area around 0x02024190
for addr in range(0x02024180, 0x020241A0):
    val = read_val("read8", addr)
    if val > 0:
        print(f"  {hex(addr)}: {val}")

# Also check lower addresses
for addr in range(0x02024080, 0x02024100):
    val = read_val("read8", addr)
    if val in [1, 2, 3, 4, 5, 6]:
        print(f"  {hex(addr)}: {val} (possible count)")
