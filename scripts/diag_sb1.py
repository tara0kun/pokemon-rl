import requests, time, sys

ports = [5000, 5001, 5002]
SB1_ADDR = 0x03005AEC

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

def get_state(port):
    base = f"http://localhost:{port}"
    ptr = r32(base, SB1_ADDR)
    if not ptr or ptr <= 0x02000000:
        return None
    sx = r16(base, ptr)
    sy = r16(base, ptr + 2)
    mr = r16(base, ptr + 4)
    mg = (mr & 0xFF) if mr is not None else None
    mn = ((mr >> 8) & 0xFF) if mr is not None else None
    return {"sx": sx, "sy": sy, "mg": mg, "mn": mn}

print("=== 60-second SB1 position monitor (all 3 ports) ===")
print("Watching for SB1_Y<=7 or map changes to Oldale (0,10)")
sys.stdout.flush()

prev = {p: None for p in ports}
min_y = {p: 99 for p in ports}
oldale_count = 0

for t in range(600):
    for port in ports:
        s = get_state(port)
        if not s:
            continue
        sx, sy, mg, mn = s["sx"], s["sy"], s["mg"], s["mn"]
        key = (sx, sy, mg, mn)
        if key != prev[port]:
            if sy is not None and sy <= 7:
                print(f"t+{t*0.1:.1f}s Port{port}: SB1({sx},{sy}) map=({mg},{mn}) *** Y<=7! ***")
                sys.stdout.flush()
            elif mn == 10 and mg == 0:
                print(f"t+{t*0.1:.1f}s Port{port}: SB1({sx},{sy}) map=({mg},{mn}) *** OLDALE REACHED! ***")
                sys.stdout.flush()
                oldale_count += 1
            elif t % 20 == 0 or (prev[port] and prev[port][1] != sy):
                print(f"t+{t*0.1:.1f}s Port{port}: SB1({sx},{sy}) map=({mg},{mn})")
                sys.stdout.flush()
            if sy is not None and sy < min_y[port]:
                min_y[port] = sy
            prev[port] = key
    time.sleep(0.1)

print(f"\n=== Summary ===")
print(f"Min SB1_Y reached: {min_y}")
print(f"Oldale arrivals: {oldale_count}")
sys.stdout.flush()
