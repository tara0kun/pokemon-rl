"""Test all directions from current position."""
import requests
import time
import random

MGBA_URL = "http://localhost:5000"
session = requests.Session()


def read_val(endpoint, addr):
    r = session.get(f"{MGBA_URL}/core/{endpoint}", params={"address": hex(addr)}, timeout=3)
    v = r.json()
    if isinstance(v, dict):
        return int(v["value"])
    return int(v)


def tap(button, delay=0.08):
    session.post(f"{MGBA_URL}/mgba-http/button/tap", params={"button": button}, timeout=3)
    time.sleep(delay)


def get_pos():
    return read_val("read16", 0x02037000), read_val("read16", 0x02037002)


def get_map():
    sb1 = read_val("read32", 0x03005AEC)
    return read_val("read8", sb1 + 4), read_val("read8", sb1 + 5)


x, y = get_pos()
mg, mn = get_map()
print(f"Current: ({x},{y}) Map({mg},{mn})")

# Test each direction with more attempts
print("\nTesting each direction (20 taps each):")
for d in ["Up", "Down", "Left", "Right"]:
    before = get_pos()
    for _ in range(20):
        tap(d, 0.05)
    after = get_pos()
    moved = "MOVED" if after != before else "blocked"
    print(f"  {d}: {before} -> {after} [{moved}]")

# Do a random walk test
print("\nRandom walk test (200 steps):")
positions = set()
start = get_pos()
positions.add(start)
for i in range(200):
    tap(random.choice(["Up", "Down", "Left", "Right"]), 0.04)
    pos = get_pos()
    positions.add(pos)

end = get_pos()
print(f"  Start: {start}, End: {end}")
print(f"  Unique positions: {len(positions)}")
if len(positions) > 1:
    print("  CHARACTER CAN MOVE!")
    min_x = min(p[0] for p in positions)
    max_x = max(p[0] for p in positions)
    min_y = min(p[1] for p in positions)
    max_y = max(p[1] for p in positions)
    print(f"  Range: x={min_x}-{max_x}, y={min_y}-{max_y}")
else:
    print("  CHARACTER IS FROZEN!")
    # Try A and B spam
    print("\n  Trying A/B spam...")
    for i in range(50):
        tap("A", 0.1)
        tap("B", 0.1)

    # Test again
    for d in ["Up", "Down", "Left", "Right"]:
        before = get_pos()
        for _ in range(10):
            tap(d, 0.05)
        after = get_pos()
        if after != before:
            print(f"  After A/B: {d} works! {before} -> {after}")
            break
    else:
        print("  Still frozen after A/B spam")
