"""Read Rustboro City warp table from memory via mGBA HTTP API."""
import requests
import struct

BASE = "http://localhost:5000"
session = requests.Session()

def read_range(addr, length):
    r = session.get(f"{BASE}/core/readRange",
                    params={"address": hex(addr), "length": length}, timeout=2)
    hex_str = r.text.strip().strip('"')
    return bytes(int(b, 16) for b in hex_str.split(","))

# Player position
pos = read_range(0x02037000, 4)
px, py = struct.unpack_from("<HH", pos)
print(f"Player position: ({px}, {py})")

# Check candidate MapHeader at 0x02036FB8
mh = read_range(0x02036FB8, 0x20)
print(f"\nMapHeader raw (32 bytes):")
for i in range(0, len(mh), 4):
    val = struct.unpack_from("<I", mh, i)[0]
    print(f"  +0x{i:02X}: 0x{val:08X}")

# The events pointer is at +0x04 = 0x084FF294
events_ptr = struct.unpack_from("<I", mh, 4)[0]
print(f"\nEvents pointer: 0x{events_ptr:08X}")

# Read events struct - it's in ROM
# MapEvents struct in Emerald:
# +0x00: u8 objectEventCount
# +0x01: u8 warpCount
# +0x02: u8 coordEventCount
# +0x03: u8 bgEventCount
# +0x04: ObjectEventTemplate* (ROM ptr)
# +0x08: WarpEvent* (ROM ptr)
# +0x0C: CoordEvent* (ROM ptr)
# +0x10: BgEvent* (ROM ptr)
ev = read_range(events_ptr, 0x14)
print(f"\nEvents struct raw ({len(ev)} bytes):")
for i in range(0, len(ev)):
    print(f"  +0x{i:02X}: 0x{ev[i]:02X}", end="")
    if i < 4:
        print(f"  (count={ev[i]})")
    elif i % 4 == 3 and i >= 4:
        ptr = struct.unpack_from("<I", ev, i-3)[0]
        print(f"  -> ptr=0x{ptr:08X}")
    else:
        print()

n_obj = ev[0]
n_warp = ev[1]
n_coord = ev[2]
n_bg = ev[3]
obj_ptr = struct.unpack_from("<I", ev, 4)[0]
warp_ptr = struct.unpack_from("<I", ev, 8)[0]
coord_ptr = struct.unpack_from("<I", ev, 12)[0]
bg_ptr = struct.unpack_from("<I", ev, 16)[0]

print(f"\nObjects: {n_obj} @ 0x{obj_ptr:08X}")
print(f"Warps:   {n_warp} @ 0x{warp_ptr:08X}")
print(f"Coords:  {n_coord} @ 0x{coord_ptr:08X}")
print(f"BGs:     {n_bg} @ 0x{bg_ptr:08X}")

if n_warp > 0 and 0x08000000 <= warp_ptr <= 0x09FFFFFF:
    wd = read_range(warp_ptr, min(n_warp, 30) * 8)
    print(f"\n=== RUSTBORO CITY WARP TABLE ({n_warp} warps) ===")
    print(f"{'#':>3} {'X':>4} {'Y':>4} {'Elev':>4} {'WID':>4} {'MG':>4} {'MN':>4}  Dest")
    print("-" * 60)
    for i in range(min(n_warp, 30)):
        o = i * 8
        wx = struct.unpack_from("<h", wd, o)[0]
        wy = struct.unpack_from("<h", wd, o+2)[0]
        we, wid, wmn, wmg = wd[o+4], wd[o+5], wd[o+6], wd[o+7]
        tag = ""
        if wmg == 11 and wmn == 0: tag = "*** POKECENTER ***"
        elif wmg == 11 and wmn == 3: tag = "*** GYM ***"
        elif wmg == 11: tag = f"Bldg(11,{wmn})"
        elif wmg == 0: tag = f"Route/City(0,{wmn})"
        else: tag = f"({wmg},{wmn})"
        print(f"{i:3d} {wx:4d} {wy:4d} {we:4d} {wid:4d} {wmg:4d} {wmn:4d}  {tag}")
else:
    print(f"\nWarp data not accessible (n_warp={n_warp}, ptr=0x{warp_ptr:08X})")
    # Maybe the struct layout is different. Let's dump more data
    print("\nDumping 64 bytes from events ptr:")
    data = read_range(events_ptr, 64)
    for i in range(0, 64, 8):
        hexdump = " ".join(f"{data[i+j]:02X}" for j in range(8))
        print(f"  +0x{i:02X}: {hexdump}")
