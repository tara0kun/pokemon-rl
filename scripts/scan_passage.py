"""Route101 北進スキャン: どのX位置がY<8に到達できるか確認"""
import requests, time, sys

PORT = 5002
BASE = f"http://localhost:{PORT}"
SB1_ADDR = 0x03005AEC

def r16(addr):
    try:
        r = requests.get(f"{BASE}/core/read16", params={"address": hex(addr)}, timeout=2)
        v = r.json(); return int(v["value"]) if isinstance(v, dict) else int(v)
    except: return None

def r32(addr):
    try:
        r = requests.get(f"{BASE}/core/read32", params={"address": hex(addr)}, timeout=2)
        v = r.json(); return int(v["value"]) if isinstance(v, dict) else int(v)
    except: return None

def get_pos():
    ptr = r32(SB1_ADDR)
    if not ptr or ptr <= 0x02000000: return None, None, None, None
    sx = r16(ptr); sy = r16(ptr + 2)
    mr = r16(ptr + 4)
    mg = (mr & 0xFF) if mr is not None else None
    mn = ((mr >> 8) & 0xFF) if mr is not None else None
    return sx, sy, mg, mn

def tap(btn):
    try:
        requests.post(f"{BASE}/button", json={"button": btn, "pressed": True}, timeout=1)
        time.sleep(0.05)
        requests.post(f"{BASE}/button", json={"button": btn, "pressed": False}, timeout=1)
        time.sleep(0.05)
    except: pass

print("=== Route101 通路スキャン ===")
print("各X位置でUPを試してY<8に到達できるか確認")

# まず現在位置確認
sx, sy, mg, mn = get_pos()
print(f"Initial: SB1({sx},{sy}) map=({mg},{mn})")

# 60秒間、各X=0-12でUPを試す
results = {}
for target_x in [2, 3, 4, 5, 6, 7, 8, 9, 10]:
    print(f"\n--- X={target_x}を試す ---")
    
    # まず現在位置を確認
    sx, sy, mg, mn = get_pos()
    if sy is None: print("  Error reading pos"); continue
    
    # X方向を調整
    moves_needed = target_x - sx
    for _ in range(abs(moves_needed) + 2):
        sx2, sy2, mg2, mn2 = get_pos()
        if sx2 is None: break
        if abs(sx2 - target_x) <= 1: break
        if sx2 < target_x: tap("Right")
        else: tap("Left")
    
    time.sleep(0.1)
    sx, sy, mg, mn = get_pos()
    print(f"  Positioned at: SB1({sx},{sy})")
    
    # 5回UP試行
    best_y = sy if sy else 99
    for i in range(5):
        sx2, sy2, mg2, mn2 = get_pos()
        if sy2 is None: break
        if mn2 != 16:  # Route101を出た
            print(f"  *** MAP CHANGE: ({mg2},{mn2}) at SB1({sx2},{sy2}) ***")
            break
        if sy2 < best_y:
            best_y = sy2
            print(f"  Y improved to {sy2} at X={sx2}")
        tap("Up")
        sx3, sy3, mg3, mn3 = get_pos()
        if sy3 != sy2 or mg3 != mg2:
            if mn3 == 10:
                print(f"  *** OLDALE REACHED at step {i}! SB1({sx3},{sy3}) ***")
                sys.exit(0)
            if sy3 < sy2:
                print(f"  MOVED NORTH: Y={sy2}→{sy3} at X={sx3}")
    
    results[target_x] = best_y
    print(f"  Result: best_y={results[target_x]}")
    time.sleep(0.1)

print(f"\n=== 結果 ===")
for x, y in sorted(results.items()):
    status = "OK!" if y < 8 else "BLOCKED"
    print(f"  X={x}: min_Y={y} → {status}")
