"""
Test using /core/step for frame-precise movement and encounter testing
"""
import requests
import time
import random

BASE_URL = "http://localhost:5000"
session = requests.Session()

def read8(addr):
    try:
        r = session.get(f"{BASE_URL}/core/read8", params={"address": hex(addr)}, timeout=1)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None

def read16(addr):
    try:
        r = session.get(f"{BASE_URL}/core/read16", params={"address": hex(addr)}, timeout=1)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None

def read32(addr):
    try:
        r = session.get(f"{BASE_URL}/core/read32", params={"address": hex(addr)}, timeout=1)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None

def write8(addr, val):
    try:
        session.post(f"{BASE_URL}/core/write8", params={"address": hex(addr), "value": val}, timeout=1)
    except:
        pass

def hold(button):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/hold", params={"button": button}, timeout=0.5)
    except:
        pass

def release(button):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/release", params={"button": button}, timeout=0.5)
    except:
        pass

def release_all():
    try:
        session.post(f"{BASE_URL}/mgba-http/button/releaseall", timeout=0.5)
    except:
        pass

def step_frames(n):
    """Advance n frames using /core/step"""
    for _ in range(n):
        try:
            session.post(f"{BASE_URL}/core/step", timeout=1)
        except:
            pass

def tap_frames(button, hold_frames=4, wait_frames=16):
    """Press button for hold_frames, then wait wait_frames"""
    hold(button)
    step_frames(hold_frames)
    release(button)
    step_frames(wait_frames)

WILD_DISABLED_ADDR = 0x020397E4
SB1_PTR = 0x03005AEC

# Load save state slot 2 (original training state)
print("Loading save state slot 2 (original)...")
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 2}, timeout=5)
time.sleep(0.3)

sb1 = read32(SB1_PTR)
x = read16(sb1)
y = read16(sb1 + 2)
mg = read8(sb1 + 4)
mn = read8(sb1 + 5)
wild = read8(WILD_DISABLED_ADDR)
print(f"Start: ({x},{y}) Map({mg},{mn}) wild={wild}")

# Clear encounters disabled
write8(WILD_DISABLED_ADDR, 0)

# First, test if /core/step actually advances the game
print("\n=== Test: Frame advance works? ===")
release_all()
x_before = read16(sb1)
y_before = read16(sb1 + 2)

# Hold Up and advance 30 frames (should complete one step)
hold("Up")
step_frames(30)
release("Up")
step_frames(5)

x_after = read16(sb1)
y_after = read16(sb1 + 2)
print(f"Before: ({x_before},{y_before}) After: ({x_after},{y_after})")
if x_after != x_before or y_after != y_before:
    print("Frame advance works! Player moved.")
else:
    print("Frame advance didn't move player. Trying longer hold...")
    hold("Up")
    step_frames(60)
    release("Up")
    step_frames(10)
    x2 = read16(sb1)
    y2 = read16(sb1 + 2)
    print(f"After 60+10 frames: ({x2},{y2})")

# === Main test: Walk with frame-precise timing ===
print("\n=== Walking test with frame advance (200 steps) ===")

# Reload save state
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 2}, timeout=5)
time.sleep(0.3)
sb1 = read32(SB1_PTR)
write8(WILD_DISABLED_ADDR, 0)

prev_x = read16(sb1)
prev_y = read16(sb1 + 2)
moved = 0
encounters = 0

for i in range(200):
    # Clear the flag every step
    write8(WILD_DISABLED_ADDR, 0)

    # Pick direction
    d = random.choice(["Up", "Down", "Left", "Right"])

    # Hold direction, advance 20 frames (enough for a full step)
    hold(d)
    step_frames(4)  # Hold for 4 frames
    release(d)
    step_frames(16)  # Wait for movement to complete

    # Check position
    cx = read16(sb1)
    cy = read16(sb1 + 2)
    if cx != prev_x or cy != prev_y:
        moved += 1
        prev_x, prev_y = cx, cy

    # Check for battle
    btl = read32(0x02022E90)
    if btl and btl > 0:
        encounters += 1
        mg2 = read8(sb1 + 4)
        mn2 = read8(sb1 + 5)
        hp = read16(0x020241E6)
        print(f"\n  *** ENCOUNTER! *** step={i} btl=0x{btl:08X}")
        print(f"  pos=({cx},{cy}) Map({mg2},{mn2}) HP={hp}")
        print(f"  Moved: {moved} steps")
        break

    if i % 25 == 0:
        mg2 = read8(sb1 + 4)
        mn2 = read8(sb1 + 5)
        w = read8(WILD_DISABLED_ADDR)
        print(f"  i={i}: ({cx},{cy}) Map({mg2},{mn2}) moved={moved} wild={w}")

if encounters == 0:
    mg2 = read8(sb1 + 4)
    mn2 = read8(sb1 + 5)
    w = read8(WILD_DISABLED_ADDR)
    print(f"\nNo encounters in {moved} steps. wild={w}")

    # Try with LONGER hold time
    print("\n=== Retry: hold 8 frames, wait 20 ===")
    session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 2}, timeout=5)
    time.sleep(0.3)
    sb1 = read32(SB1_PTR)
    write8(WILD_DISABLED_ADDR, 0)

    prev_x = read16(sb1)
    prev_y = read16(sb1 + 2)
    moved = 0

    for i in range(200):
        write8(WILD_DISABLED_ADDR, 0)
        d = random.choice(["Up", "Down", "Left", "Right"])
        hold(d)
        step_frames(8)
        release(d)
        step_frames(20)

        cx = read16(sb1)
        cy = read16(sb1 + 2)
        if cx != prev_x or cy != prev_y:
            moved += 1
            prev_x, prev_y = cx, cy

        btl = read32(0x02022E90)
        if btl and btl > 0:
            encounters += 1
            print(f"\n  *** ENCOUNTER! *** step={i} moved={moved}")
            break

        if i % 50 == 0:
            mg2 = read8(sb1 + 4)
            mn2 = read8(sb1 + 5)
            w = read8(WILD_DISABLED_ADDR)
            print(f"  i={i}: ({cx},{cy}) Map({mg2},{mn2}) moved={moved} wild={w}")

    if encounters == 0:
        print(f"Still no encounters in {moved} steps")
else:
    print(f"\n*** FRAME ADVANCE ENCOUNTERS WORK! ***")
