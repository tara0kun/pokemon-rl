import requests, time

BASE = "http://localhost:5000"
SB1_ADDR_JP = 0x03005AEC
ADDR_X = 0x02037000
ADDR_Y = 0x02037002

def r16(addr):
    try:
        r = requests.get(f"{BASE}/core/read16?address={addr:#x}", timeout=2)
        return r.json().get("value", None)
    except:
        return None

def r32(addr):
    try:
        r = requests.get(f"{BASE}/core/read32?address={addr:#x}", timeout=2)
        return r.json().get("value", None)
    except:
        return None

sb1_ptr = r32(SB1_ADDR_JP)
print(f"SB1 ptr: {hex(sb1_ptr) if sb1_ptr else None}")
ax = r16(ADDR_X)
ay = r16(ADDR_Y)
print(f"ADDR X={ax} Y={ay} => SB1_X={ax-8 if ax else None} SB1_Y={ay-7 if ay else None}")

if sb1_ptr and sb1_ptr > 0x02000000:
    sb1_x = r16(sb1_ptr)
    sb1_y = r16(sb1_ptr + 2)
    map_raw = r16(sb1_ptr + 4)
    print(f"SB1 coords: X={sb1_x} Y={sb1_y} map_raw={hex(map_raw) if map_raw else None}")
    if map_raw is not None:
        print(f"  MapGroup={map_raw & 0xFF} MapNum={(map_raw >> 8) & 0xFF}")

print("\nMonitoring 30s...")
prev = None
for _ in range(300):
    ax2 = r16(ADDR_X)
    ay2 = r16(ADDR_Y)
    if ax2 and ay2:
        p = (ax2, ay2)
        if p != prev:
            sb1x = ax2 - 8
            sb1y = ay2 - 7
            print(f"ADDR({ax2},{ay2}) SB1({sb1x},{sb1y})")
            prev = p
    time.sleep(0.1)
