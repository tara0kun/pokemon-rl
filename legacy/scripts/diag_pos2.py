import requests, time, sys

ports = [5000, 5001, 5002]
SB1_ADDR = 0x03005AEC
ADDR_X = 0x02037000
ADDR_Y = 0x02037002

def r16(base, addr):
    try:
        r = requests.get(f"{base}/core/read16", params={"address": hex(addr)}, timeout=2)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None

def r32(base, addr):
    try:
        r = requests.get(f"{base}/core/read32", params={"address": hex(addr)}, timeout=2)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None

print("=== Current game state ===")
for port in ports:
    base = f"http://localhost:{port}"
    ax = r16(base, ADDR_X)
    ay = r16(base, ADDR_Y)
    ptr = r32(base, SB1_ADDR)
    sb1x = sb1y = mg = mn = None
    if ptr and ptr > 0x02000000:
        sb1x = r16(base, ptr)
        sb1y = r16(base, ptr + 2)
        mr = r16(base, ptr + 4)
        if mr is not None:
            mg = mr & 0xFF
            mn = (mr >> 8) & 0xFF
    dx = (ax - sb1x) if ax and sb1x else "?"
    dy = (ay - sb1y) if ay and sb1y else "?"
    print(f"Port{port}: ADDR({ax},{ay}) SB1({sb1x},{sb1y}) offset(dx={dx},dy={dy}) map=({mg},{mn})")
    sys.stdout.flush()

print("\n=== Monitor 30s (port 5000) ===")
base = "http://localhost:5000"
prev = None
ptr = r32(base, SB1_ADDR)
for _ in range(300):
    ax = r16(base, ADDR_X)
    ay = r16(base, ADDR_Y)
    if ax and ay and (ax, ay) != prev:
        sb1x = sb1y = mg = mn = None
        if ptr and ptr > 0x02000000:
            sb1x = r16(base, ptr)
            sb1y = r16(base, ptr + 2)
            mr = r16(base, ptr + 4)
            if mr is not None:
                mg = mr & 0xFF
                mn = (mr >> 8) & 0xFF
        dx = ax - sb1x if ax and sb1x else "?"
        dy = ay - sb1y if ay and sb1y else "?"
        print(f"ADDR({ax},{ay}) SB1({sb1x},{sb1y}) offset(dx={dx},dy={dy}) map=({mg},{mn})")
        prev = (ax, ay)
        sys.stdout.flush()
    time.sleep(0.1)
