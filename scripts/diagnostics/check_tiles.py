"""
Check if current tile and nearby tiles are grass by reading map layout data.
Also verify Pokemon party data is valid.
In Emerald, the map grid stores metatile IDs. Behavior 0x02 = tall grass.
"""
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


x, y = get_pos()
mg, mn = get_map()
print(f"Position: ({x},{y}) Map({mg},{mn})")

# Read map layout structure
# In Emerald, gMapHeader is at a known address
# gBackupMapLayout is what contains the actual tile data
# JP Emerald: gBackupMapLayout should be in EWRAM

# Method 1: Read the map layout pointer
# In US Emerald, gBackupMapLayout = 0x02037318 (pointer to MapLayout struct)
# JP offset: -0x15C from US = not reliable
# Let's try reading the map layout directly

# MapLayout struct:
# width: u32 at +0
# height: u32 at +4
# border: u32* at +8
# map: u16* at +12 (pointer to tile data)
# primaryTileset: u32* at +16
# secondaryTileset: u32* at +20

# Try finding gBackupMapLayout
# In JP, it might be at 0x020371BC (0x02037318 - 0x15C)
for candidate in [0x020371BC, 0x02037318, 0x020371A0, 0x020371D0]:
    width = read_val("read32", candidate)
    height = read_val("read32", candidate + 4)
    if 10 < width < 100 and 10 < height < 100:
        map_ptr = read_val("read32", candidate + 12)
        if 0x02000000 < map_ptr < 0x0203FFFF:
            print(f"\nFound map layout at {hex(candidate)}")
            print(f"  Width: {width}, Height: {height}")
            print(f"  Map data ptr: {hex(map_ptr)}")

            # Read current tile
            # Tile at (x, y) is at map_ptr + ((y + 7) * (width + 15) + (x + 7)) * 2
            # The +7 is for the border padding
            stride = width + 15
            tile_offset = ((y + 7) * stride + (x + 7)) * 2
            tile = read_val("read16", map_ptr + tile_offset)
            metatile_id = tile & 0x3FF
            collision = (tile >> 10) & 0x3
            elevation = (tile >> 12) & 0xF
            print(f"  Current tile ({x},{y}): metatile={hex(metatile_id)} collision={collision} elevation={elevation}")

            # Check a few tiles around us
            print(f"\n  Nearby tile metatile IDs:")
            for dy in range(-3, 4):
                line = f"    y={y+dy:3d}: "
                for dx in range(-3, 4):
                    tx, ty = x + dx, y + dy
                    off = ((ty + 7) * stride + (tx + 7)) * 2
                    t = read_val("read16", map_ptr + off)
                    mid = t & 0x3FF
                    marker = "*" if dx == 0 and dy == 0 else " "
                    line += f"{mid:3x}{marker}"
                print(line)

            # Read metatile behavior to find grass
            # Behavior is stored in the tileset metatile attributes
            # primaryTileset ptr at +16, secondaryTileset at +20
            primary_ts = read_val("read32", candidate + 16)
            secondary_ts = read_val("read32", candidate + 20)
            print(f"\n  Primary tileset: {hex(primary_ts)}")
            print(f"  Secondary tileset: {hex(secondary_ts)}")

            # Tileset struct has metatileAttributes pointer at offset +16
            if primary_ts > 0x08000000:
                # ROM address
                pri_attrs_ptr = read_val("read32", primary_ts + 16)
                print(f"  Primary attrs ptr: {hex(pri_attrs_ptr)}")

                # Check behavior of current metatile
                if metatile_id < 512:
                    if metatile_id < 512:
                        behavior = read_val("read16", pri_attrs_ptr + metatile_id * 2)
                        print(f"  Current metatile {hex(metatile_id)} behavior: {hex(behavior)}")
                        if behavior & 0xFF == 0x02:
                            print(f"  >>> THIS IS TALL GRASS! <<<")

            break

# Also try approach 2: scan for metatile 0x01 (tall grass metatile in Emerald)
# Tall grass metatile ID varies by game/version but is typically 0x01 or 0x02
print("\n--- Scanning Route 101 for potential grass metatiles ---")
# Use the found map layout
for candidate in [0x020371BC, 0x02037318, 0x020371A0, 0x020371D0]:
    width = read_val("read32", candidate)
    height = read_val("read32", candidate + 4)
    if 10 < width < 100 and 10 < height < 100:
        map_ptr = read_val("read32", candidate + 12)
        if 0x02000000 < map_ptr < 0x0203FFFF:
            stride = width + 15

            # Scan all tiles on the map
            grass_tiles = []
            metatile_counts = {}
            for ty in range(height):
                for tx in range(width):
                    off = ((ty + 7) * stride + (tx + 7)) * 2
                    t = read_val("read16", map_ptr + off)
                    mid = t & 0x3FF
                    metatile_counts[mid] = metatile_counts.get(mid, 0) + 1

            print(f"  Metatile ID distribution (top 15):")
            sorted_tiles = sorted(metatile_counts.items(), key=lambda x: -x[1])
            for mid, count in sorted_tiles[:15]:
                print(f"    {hex(mid)}: {count} tiles")

            # Check behaviors of most common metatiles
            primary_ts = read_val("read32", candidate + 16)
            if primary_ts > 0x08000000:
                pri_attrs_ptr = read_val("read32", primary_ts + 16)
                secondary_ts = read_val("read32", candidate + 20)
                sec_attrs_ptr = 0
                if secondary_ts > 0x08000000:
                    sec_attrs_ptr = read_val("read32", secondary_ts + 16)

                print(f"\n  Metatile behaviors:")
                for mid, count in sorted_tiles[:15]:
                    if mid < 512:
                        behavior = read_val("read16", pri_attrs_ptr + mid * 2)
                    elif sec_attrs_ptr:
                        behavior = read_val("read16", sec_attrs_ptr + (mid - 512) * 2)
                    else:
                        behavior = 0
                    tag = ""
                    btype = behavior & 0xFF
                    if btype == 0x02:
                        tag = " <<< TALL GRASS"
                    elif btype == 0x03:
                        tag = " <<< LONG GRASS"
                    elif btype == 0x00:
                        tag = " (normal)"
                    print(f"    metatile {hex(mid)}: behavior {hex(behavior)} ({count} tiles){tag}")

            break

# Check party Pokemon thoroughly
print("\n\n--- Detailed Pokemon party data ---")
party_base = 0x02024190
personality = read_val("read32", party_base)
otid = read_val("read32", party_base + 4)
print(f"Personality: {hex(personality)}")
print(f"OT ID: {hex(otid)}")

# Stats (decrypted section at +0x50)
level = read_val("read8", party_base + 0x54)
hp = read_val("read16", party_base + 0x56)
max_hp = read_val("read16", party_base + 0x58)
atk = read_val("read16", party_base + 0x5A)
defense = read_val("read16", party_base + 0x5C)
speed = read_val("read16", party_base + 0x5E)
spatk = read_val("read16", party_base + 0x60)
spdef = read_val("read16", party_base + 0x62)
print(f"Level: {level}")
print(f"HP: {hp}/{max_hp}")
print(f"Stats: Atk={atk} Def={defense} Spd={speed} SpAtk={spatk} SpDef={spdef}")

# Species from substructure (encrypted - need to XOR with personality^otid)
key = personality ^ otid
print(f"Encryption key: {hex(key)}")

# The substructure order depends on personality % 24
order_table = [
    "GAEM", "GAME", "GEAM", "GEMA", "GMAE", "GMEA",
    "AGEM", "AGME", "AEGM", "AEMG", "AMGE", "AMEG",
    "EGAM", "EGMA", "EAGM", "EAMG", "EMGA", "EMAG",
    "MGAE", "MGEA", "MAGE", "MAEG", "MEGA", "MEAG"
]
order_idx = personality % 24
order = order_table[order_idx]
print(f"Substructure order: {order} (index {order_idx})")

# Find Growth substructure (contains species)
growth_pos = order.index('G')
growth_offset = 32 + growth_pos * 12  # 32 byte header + 12 bytes per sub

# Read encrypted growth data
g0 = read_val("read32", party_base + growth_offset)
g1 = read_val("read32", party_base + growth_offset + 4)
g2 = read_val("read32", party_base + growth_offset + 8)

# Decrypt
dg0 = g0 ^ key
dg1 = g1 ^ key
dg2 = g2 ^ key

species = dg0 & 0xFFFF
item = (dg0 >> 16) & 0xFFFF
exp = dg1
pp_bonuses = dg2 & 0xFF
friendship = (dg2 >> 8) & 0xFF
print(f"Species: {species}")
print(f"Item: {item}")
print(f"Experience: {exp}")
print(f"Friendship: {friendship}")

# Find Attacks substructure
atk_pos = order.index('A')
atk_offset = 32 + atk_pos * 12
a0 = read_val("read32", party_base + atk_offset)
a1 = read_val("read32", party_base + atk_offset + 4)
a2 = read_val("read32", party_base + atk_offset + 8)
da0 = a0 ^ key
da1 = a1 ^ key
da2 = a2 ^ key
move1 = da0 & 0xFFFF
move2 = (da0 >> 16) & 0xFFFF
move3 = da1 & 0xFFFF
move4 = (da1 >> 16) & 0xFFFF
pp1 = da2 & 0xFF
pp2 = (da2 >> 8) & 0xFF
pp3 = (da2 >> 16) & 0xFF
pp4 = (da2 >> 24) & 0xFF
print(f"Moves: {move1}, {move2}, {move3}, {move4}")
print(f"PP: {pp1}, {pp2}, {pp3}, {pp4}")
