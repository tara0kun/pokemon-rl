"""Quick: Find Nurse Joy's SB1 coords by reading object events."""
import requests, sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
BASE = f"http://localhost:{PORT}"

def r8(a): return requests.get(f"{BASE}/core/read8", params={"address": a}).json()
def r16(a): return r8(a) | (r8(a+1) << 8)
def r32(a): return r16(a) | (r16(a+2) << 16)

# SB1 player coords
sb1 = r32(0x03005AEC)
px, py = r16(sb1), r16(sb1+2)
mg, mn = r8(sb1+4), r8(sb1+5)
print(f"Player: ({px},{py}) map=({mg},{mn}) SB1=0x{sb1:08X}")

# gObjectEvents: try to find by matching player coords at obj[0]+0x10
# Start with known English address and scan nearby
for base in range(0x02036C00, 0x02037200, 0x44):
    ox = r16(base + 0x10)
    oy = r16(base + 0x12)
    if ox == px and oy == py:
        print(f"\nFound gObjectEvents at 0x{base:08X}")
        # Read first 6 objects
        for i in range(6):
            a = base + i * 0x44
            active = r8(a) & 1
            gfx = r8(a + 0x05)
            lid = r8(a + 0x08)
            cx, cy = r16(a+0x10), r16(a+0x12)
            if cx > 0x7FFF: cx -= 0x10000
            if cy > 0x7FFF: cy -= 0x10000
            print(f"  obj[{i}] active={active} gfx=0x{gfx:02X} lid={lid} pos=({cx},{cy})")
        break
else:
    print("Not found via coord match. Trying direct read at English addr...")
    base = 0x02036E38
    for i in range(4):
        a = base + i * 0x44
        cx, cy = r16(a+0x10), r16(a+0x12)
        if cx > 0x7FFF: cx -= 0x10000
        if cy > 0x7FFF: cy -= 0x10000
        print(f"  obj[{i}] pos=({cx},{cy})")
