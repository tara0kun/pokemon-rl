"""
Scan Route 101 to find grass patches by walking around and taking screenshots.
Record all reachable positions to map the area.
"""
import requests
import time
import random

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


def screenshot(name):
    path = f"C:/pokemon-rl/{name}.png"
    session.post(f"{MGBA_URL}/core/screenshot", params={"path": path}, timeout=5)
    return path


print("Scanning Route 101 area...")
x, y = get_pos()
mg, mn = get_map()
print(f"Start: ({x},{y}) Map({mg},{mn})")

# Save current state
session.post(f"{MGBA_URL}/core/savestateslot", params={"slot": 9}, timeout=5)

# Walk around recording all positions and periodically screenshot
positions = set()
positions.add((x, y))
screenshots_taken = []

# First, walk right to find grass
print("\nWalking right to find grass area...")
for i in range(50):
    tap("Right", 0.04)
    pos = get_pos()
    mg, mn = get_map()
    if (mg, mn) != (0, 16):
        print(f"  Left Route 101 at step {i}, pos {pos} Map({mg},{mn})")
        break
    positions.add(pos)

pos = get_pos()
print(f"  After going right: {pos}")
screenshot("scan_right")

# Now walk up-right diagonal
print("\nWalking up-right...")
for i in range(50):
    tap("Up", 0.04)
    tap("Right", 0.04)
    pos = get_pos()
    mg, mn = get_map()
    if (mg, mn) != (0, 16):
        print(f"  Left Route 101 at step {i}")
        break
    positions.add(pos)

pos = get_pos()
print(f"  After up-right: {pos}")
screenshot("scan_upright")

# Walk down from current pos
print("\nWalking down...")
for i in range(50):
    tap("Down", 0.04)
    pos = get_pos()
    mg, mn = get_map()
    if (mg, mn) != (0, 16):
        print(f"  Left Route 101 at step {i}")
        break
    positions.add(pos)

pos = get_pos()
print(f"  After down: {pos}")
screenshot("scan_down")

# Walk left
print("\nWalking left...")
for i in range(50):
    tap("Left", 0.04)
    pos = get_pos()
    mg, mn = get_map()
    if (mg, mn) != (0, 16):
        print(f"  Left Route 101 at step {i}")
        break
    positions.add(pos)

pos = get_pos()
print(f"  After left: {pos}")
screenshot("scan_left")

# Full random walk
print("\nRandom walk to map entire area...")
for i in range(500):
    d = random.choice(["Up", "Down", "Left", "Right"])
    tap(d, 0.03)
    pos = get_pos()
    mg, mn = get_map()
    if (mg, mn) != (0, 16):
        # Walked off route 101, go back
        opposite = {"Up": "Down", "Down": "Up", "Left": "Right", "Right": "Left"}
        for _ in range(5):
            tap(opposite[d], 0.04)
            if get_map() == (0, 16):
                break
        continue
    positions.add(pos)

    if i % 100 == 99:
        screenshot(f"scan_random_{i+1}")

# Print position map
print(f"\n{len(positions)} unique positions found on Route 101:")
min_x = min(p[0] for p in positions)
max_x = max(p[0] for p in positions)
min_y = min(p[1] for p in positions)
max_y = max(p[1] for p in positions)
print(f"X range: {min_x}-{max_x}")
print(f"Y range: {min_y}-{max_y}")

# Print as grid
print("\nMap grid (. = visited, # = not visited):")
for row_y in range(min_y, max_y + 1):
    line = f"{row_y:3d} "
    for col_x in range(min_x, max_x + 1):
        if (col_x, row_y) in positions:
            line += "."
        else:
            line += "#"
    print(line)

# Final screenshot
pos = get_pos()
screenshot("scan_final")
print(f"\nFinal position: {pos}")
print("Check scan_*.png files to see the route layout")
