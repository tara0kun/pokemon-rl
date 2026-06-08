"""Test nurse interaction with careful timing."""
import requests, time, sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
BASE = f"http://localhost:{PORT}"

def r8(a): return requests.get(f"{BASE}/core/read8", params={"address": a}).json()
def r16(a): return r8(a) | (r8(a+1) << 8)
def tap(btn, frames=8):
    requests.post(f"{BASE}/button/tap", params={"button": btn, "frames": frames})
def advance(n=1):
    for _ in range(n):
        requests.post(f"{BASE}/core/advance-frame")

def player_pos():
    return r16(0x02037000), r16(0x02037002)

def nurse_pos():
    return r16(0x02037014 + 0x10), r16(0x02037014 + 0x12)

print(f"=== Nurse Test v2 (port {PORT}) ===")
px, py = player_pos()
nx, ny = nurse_pos()
print(f"Player: ({px},{py}), Nurse: ({nx},{ny}), Dist: {abs(nx-px)+abs(ny-py)}")

# First, advance many frames to let everything settle
print("\n1. Advancing 60 frames (1 sec) with no input...")
advance(60)
px, py = player_pos()
nx, ny = nurse_pos()
print(f"   Player: ({px},{py}), Nurse: ({nx},{ny})")

# Now walk toward nurse
print("\n2. Walking toward nurse...")
dx = nx - px
dy = ny - py
if abs(dx) >= abs(dy) and dx != 0:
    btn = "Right" if dx > 0 else "Left"
elif dy != 0:
    btn = "Up" if dy < 0 else "Down"
else:
    btn = "A"
print(f"   Direction: {btn} (dx={dx}, dy={dy})")
tap(btn, 16)  # 16 frames for full tile move
time.sleep(0.3)
advance(16)  # Extra frames to settle
px2, py2 = player_pos()
nx2, ny2 = nurse_pos()
print(f"   Player: ({px2},{py2}), Nurse: ({nx2},{ny2})")

# Wait for nurse to be idle
print("\n3. Wait for nurse to stop moving...")
for i in range(10):
    advance(8)
    nx_a, ny_a = nurse_pos()
    advance(8)
    nx_b, ny_b = nurse_pos()
    if nx_a == nx_b and ny_a == ny_b:
        print(f"   Nurse stable at ({nx_a},{ny_a}) after {i*16} frames")
        break
    else:
        print(f"   Nurse moving: ({nx_a},{ny_a}) -> ({nx_b},{ny_b})")

# Check adjacency
px3, py3 = player_pos()
nx3, ny3 = nurse_pos()
mdist = abs(nx3-px3) + abs(ny3-py3)
print(f"\n4. Current: Player ({px3},{py3}), Nurse ({nx3},{ny3}), dist={mdist}")

if mdist > 1:
    print("   Not adjacent, moving closer...")
    dx3 = nx3 - px3
    dy3 = ny3 - py3
    if abs(dx3) >= abs(dy3) and dx3 != 0:
        btn3 = "Right" if dx3 > 0 else "Left"
    elif dy3 != 0:
        btn3 = "Up" if dy3 < 0 else "Down"
    else:
        btn3 = "Right"
    tap(btn3, 16)
    advance(16)
    px3, py3 = player_pos()
    nx3, ny3 = nurse_pos()
    mdist = abs(nx3-px3) + abs(ny3-py3)
    print(f"   Now: Player ({px3},{py3}), Nurse ({nx3},{ny3}), dist={mdist}")

if mdist == 1:
    print("\n5. Adjacent! Testing A press...")
    # Face nurse direction
    dx4 = nx3 - px3
    dy4 = ny3 - py3
    if dy4 < 0: face_btn = "Up"
    elif dy4 > 0: face_btn = "Down"
    elif dx4 < 0: face_btn = "Left"
    else: face_btn = "Right"

    print(f"   Facing {face_btn}...")
    tap(face_btn, 4)  # Short tap to face
    advance(8)  # Let it settle

    # Now check nurse is still there
    nx4, ny4 = nurse_pos()
    print(f"   Nurse now at ({nx4},{ny4})")

    if abs(nx4-px3) + abs(ny4-py3) == 1:
        print(f"   Pressing A (1 frame)...")
        tap("A", 1)
        advance(4)

        print(f"   Pressing A (frames=8, then wait)...")
        tap("A", 8)
        advance(30)

        # Check if anything changed
        px5, py5 = player_pos()
        nx5, ny5 = nurse_pos()
        print(f"   Player: ({px5},{py5}), Nurse: ({nx5},{ny5})")

        # Try many more A presses with frame advances
        print("\n6. Spamming A with frame advances...")
        for i in range(10):
            tap("A", 4)
            advance(8)
            px_i, py_i = player_pos()
            nx_i, ny_i = nurse_pos()
            print(f"   [{i}] P:({px_i},{py_i}) N:({nx_i},{ny_i})")
    else:
        print(f"   Nurse moved away! Dist={abs(nx4-px3)+abs(ny4-py3)}")
else:
    print(f"   Not adjacent (dist={mdist}), can't test")

# Record GIF
print("\n7. Recording GIF...")
import subprocess
subprocess.run([
    "c:/pokemon-rl/poke-rl/Scripts/python.exe",
    "record_mgba.py", str(PORT), "3", "10"
], cwd="c:/pokemon-rl")
