"""
Quick test: Exit the truck by pressing B (not A!) to close dialogs,
then walking south.
"""
import requests
import time
import sys

sys.stdout.reconfigure(line_buffering=True)
session = requests.Session()
base = "http://localhost:5000"

SB1_PTR = 0x03005AEC

def rval(addr, bits=32):
    ep = {8: 'read8', 16: 'read16', 32: 'read32'}[bits]
    try:
        v = session.get(f'{base}/core/{ep}', params={'address': hex(addr)}, timeout=2).json()
        return int(v['value']) if isinstance(v, dict) else int(v)
    except:
        return None

def tap(button, delay=0.3):
    try:
        session.post(f'{base}/mgba-http/button/tap', params={'button': button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

def hold(button, frames=30):
    try:
        session.post(f'{base}/mgba-http/button/hold', params={'button': button, 'duration': frames}, timeout=5)
    except:
        pass

def ss(name):
    try:
        session.post(f'{base}/core/screenshot',
                     params={'path': f'C:/pokemon-rl/screenshots/{name}.png'}, timeout=5)
    except:
        pass

def pos_str():
    sb1 = rval(SB1_PTR, 32)
    if not sb1 or sb1 < 0x02000000:
        return "??"
    x = rval(sb1, 16)
    y = rval(sb1 + 2, 16)
    mg = rval(sb1 + 4, 8)
    mn = rval(sb1 + 5, 8)
    return f"({x},{y}) Map({mg},{mn})"

print(f"Current: {pos_str()}", flush=True)
ss("truck_01_start")

# Step 1: Press B many times to close ALL open dialogs
print("Pressing B to close dialogs...", flush=True)
for i in range(20):
    tap("B", 0.2)

ss("truck_02_after_b")
print(f"After B: {pos_str()}", flush=True)

# Step 2: Walk in each direction to test movement
print("\nTesting movement...", flush=True)

for direction in ["Down", "Left", "Right", "Up"]:
    before = pos_str()
    tap(direction, 0.4)
    after = pos_str()
    moved = before != after
    print(f"  {direction}: {before} -> {after} {'MOVED!' if moved else 'stuck'}", flush=True)

ss("truck_03_after_test")

# Step 3: Try walking south aggressively (B between steps to prevent dialog)
print("\nWalking south with B...", flush=True)
for i in range(10):
    tap("B", 0.1)  # Close any dialog first
    tap("Down", 0.4)
    print(f"  step {i}: {pos_str()}", flush=True)

ss("truck_04_south")

# Step 4: Try hold south
print("\nHolding south...", flush=True)
tap("B", 0.2)
hold("Down", 120)
time.sleep(4)
print(f"After hold: {pos_str()}", flush=True)
ss("truck_05_hold")

# Step 5: If still in truck, try different approaches
sb1 = rval(SB1_PTR, 32)
mg = rval(sb1 + 4, 8)
if mg == 25:
    print("\nStill in truck! Trying alternative exits...", flush=True)

    # Maybe need to walk to specific tile
    # Truck might be: exit at bottom-right or bottom-left
    # Try walking right then down
    for i in range(3):
        tap("B", 0.1)
        tap("Right", 0.3)
    for i in range(5):
        tap("B", 0.1)
        tap("Down", 0.3)
    print(f"  Right+Down: {pos_str()}", flush=True)

    # Try left then down
    for i in range(5):
        tap("B", 0.1)
        tap("Left", 0.3)
    for i in range(5):
        tap("B", 0.1)
        tap("Down", 0.3)
    print(f"  Left+Down: {pos_str()}", flush=True)

    ss("truck_06_alt")

    # Last resort: maybe the warp needs A press on exit tile?
    print("\nTrying A on various tiles...", flush=True)
    for dy in range(4):
        for dx in range(-2, 3):
            # Walk to position
            if dx > 0:
                for _ in range(dx):
                    tap("Right", 0.2)
            elif dx < 0:
                for _ in range(-dx):
                    tap("Left", 0.2)
            tap("A", 0.3)
            m = pos_str()
            sb1 = rval(SB1_PTR, 32)
            mg2 = rval(sb1 + 4, 8)
            if mg2 != 25:
                print(f"  EXITED at tile offset ({dx},{dy})! {m}", flush=True)
                break
        else:
            tap("Down", 0.3)
            continue
        break

    print(f"  Final: {pos_str()}", flush=True)
    ss("truck_07_final")

print(f"\nDone. Position: {pos_str()}", flush=True)
