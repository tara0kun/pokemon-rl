"""Find the counter position in Oldale PokeCenter by walking north."""
import requests, time, sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
BASE = f"http://localhost:{PORT}"

def r8(a): return requests.get(f"{BASE}/core/read8", params={"address": a}).json()
def r16(a): return r8(a) | (r8(a+1) << 8)
def tap(btn, frames=16):
    requests.post(f"{BASE}/button/tap", params={"button": btn, "frames": frames})
def advance(n=1):
    for _ in range(n):
        requests.post(f"{BASE}/core/advance-frame")

def player_pos():
    return r16(0x02037000), r16(0x02037002)

print(f"=== Counter Position Finder (port {PORT}) ===")

# First, move player to center-west area (away from the fisherman)
px, py = player_pos()
print(f"Start: ({px},{py})")

# Walk left a few tiles to get clear of the fisherman
print("\n1. Moving to clear area...")
for i in range(3):
    tap("Left", 16)
    advance(16)
px, py = player_pos()
print(f"   Now at ({px},{py})")

# Now walk north until blocked
print("\n2. Walking NORTH to find counter...")
prev_y = py
for i in range(10):
    tap("Up", 16)
    advance(16)
    px, py = player_pos()
    if py == prev_y:
        print(f"   BLOCKED at ({px},{py}), can't go to y={py-1}")
        break
    else:
        print(f"   Moved to ({px},{py})")
        prev_y = py

counter_y = py - 1  # The tile that blocked us
print(f"\n   Counter/wall likely at y={counter_y} (player blocked at y={py})")

# Now scan the counter row by walking east-west
print(f"\n3. Scanning row at y={py} (tiles to the east/west)...")
# First go east and check if we can go up at different x positions
tap("Right", 16); advance(16)
px, py = player_pos()
print(f"   Moved to ({px},{py})")

# Try going up from multiple x positions
for target_x in range(px-3, px+4):
    # Navigate to target_x
    while True:
        cx, cy = player_pos()
        if cx == target_x:
            break
        elif cx < target_x:
            tap("Right", 16); advance(16)
        else:
            tap("Left", 16); advance(16)

    # Try going up
    cy_before = player_pos()[1]
    tap("Up", 16); advance(16)
    cy_after = player_pos()[1]
    if cy_after < cy_before:
        print(f"   x={target_x}: CAN go up (no wall at y={cy_before-1})")
        # Go back down
        tap("Down", 16); advance(16)
    else:
        print(f"   x={target_x}: BLOCKED (wall/counter at y={cy_before-1})")

# Now try pressing A at the counter position
print(f"\n4. Testing A at counter...")
# Go to the first blocked position
cx, cy = player_pos()
print(f"   Current pos: ({cx},{cy})")
print(f"   Facing Up, pressing A...")
tap("Up", 4); advance(8)  # Face up
tap("A", 8); advance(30)
tap("A", 8); advance(30)
tap("A", 8); advance(30)

# Record GIF to see result
print("\n5. Recording GIF...")
import subprocess
subprocess.run([
    "c:/pokemon-rl/poke-rl/Scripts/python.exe",
    "record_mgba.py", str(PORT), "3", "10"
], cwd="c:/pokemon-rl")
