"""Check game state: party, flags, battle readiness."""
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
x = read_val("read16", 0x02037000)
y = read_val("read16", 0x02037002)
mg = read_val("read8", sb1 + 4)
mn = read_val("read8", sb1 + 5)
print(f"Position: ({x},{y}) Map({mg},{mn})")
print(f"SaveBlock1: {hex(sb1)}")

# Party info
print("\n--- Party ---")
party_count = read_val("read8", sb1 + 0x234)
print(f"Party count: {party_count}")
party_base = sb1 + 0x238
for i in range(min(party_count, 6)):
    pokemon_base = party_base + (i * 100)
    species = read_val("read16", pokemon_base)
    # Read from Pokemon structure
    hp = read_val("read16", pokemon_base + 0x56)
    max_hp = read_val("read16", pokemon_base + 0x58)
    level = read_val("read8", pokemon_base + 0x54)
    print(f"  Pokemon {i}: species={species} HP={hp}/{max_hp} Lv{level}")

# Also check the "last used" party addresses
print("\n--- Alt party addresses ---")
hp_alt = read_val("read16", 0x020241E6)
hp_max_alt = read_val("read16", 0x020241E8)
level_alt = read_val("read8", 0x020241E4)
party_alt = read_val("read8", 0x0202418D)
print(f"HP: {hp_alt}/{hp_max_alt} Lv{level_alt} Party:{party_alt}")

# Check key flags
print("\n--- Flags ---")
flags_base = sb1 + 0x1270

# FLAG_SYS_POKEMON_GET (0x860) = byte 0x10C, bit 0
byte_860 = read_val("read8", flags_base + (0x860 // 8))
bit_860 = (byte_860 >> (0x860 % 8)) & 1
print(f"FLAG_SYS_POKEMON_GET (0x860): {bit_860} (byte at +{0x860//8}: {bin(byte_860)})")

# FLAG_SYS_POKEDEX_GET (0x861) = byte 0x10C, bit 1
byte_861 = read_val("read8", flags_base + (0x861 // 8))
bit_861 = (byte_861 >> (0x861 % 8)) & 1
print(f"FLAG_SYS_POKEDEX_GET (0x861): {bit_861}")

# FLAG_RESCUED_BIRCH (0x52)
byte_52 = read_val("read8", flags_base + (0x52 // 8))
bit_52 = (byte_52 >> (0x52 % 8)) & 1
print(f"FLAG_RESCUED_BIRCH (0x52): {bit_52}")

# JP fallback flags (0x800, 0x801)
byte_800 = read_val("read8", flags_base + (0x800 // 8))
bit_800 = (byte_800 >> (0x800 % 8)) & 1
bit_801 = (byte_800 >> (0x801 % 8)) & 1
print(f"JP flag 0x800: {bit_800}, 0x801: {bit_801} (byte: {bin(byte_800)})")

# Check vars
print("\n--- Vars ---")
vars_base = sb1 + 0x139C
var_route101 = read_val("read16", vars_base + (0x4060 - 0x4000) * 2)
var_birch = read_val("read16", vars_base + (0x4049 - 0x4000) * 2)
var_littleroot = read_val("read16", vars_base + (0x4050 - 0x4000) * 2)
var_birchlab = read_val("read16", vars_base + (0x4084 - 0x4000) * 2)
print(f"VAR_ROUTE101_STATE (0x4060): {var_route101}")
print(f"VAR_BIRCH_STATE (0x4049): {var_birch}")
print(f"VAR_LITTLEROOT_TOWN_STATE (0x4050): {var_littleroot}")
print(f"VAR_BIRCH_LAB_STATE (0x4084): {var_birchlab}")

# Check encounter-related: repel steps, etc
print("\n--- Encounter check ---")
# Repel steps counter (if > 0, no encounters)
# In Emerald, repel counter is at SaveBlock1 + 0x24
repel_steps = read_val("read16", sb1 + 0x24)
print(f"Repel steps: {repel_steps}")

# Check if wild encounter is enabled by reading callback state
# gBattleTypeFlags
btf = read_val("read32", 0x02022E90)
print(f"gBattleTypeFlags: {hex(btf) if btf else 0}")

# Check the script state / callback pointers
# Script engine state
print("\n--- Script/callback state ---")
# These are IWRAM addresses for the main game loop callbacks
cb1 = read_val("read32", 0x030022C0)
cb2 = read_val("read32", 0x030022C4)
print(f"gMain.callback1: {hex(cb1)}")
print(f"gMain.callback2: {hex(cb2)}")
