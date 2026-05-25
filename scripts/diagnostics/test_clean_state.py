"""
Test the clean save state (slot 1) for encounters
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

def write16(addr, val):
    try:
        session.post(f"{BASE_URL}/core/write16", params={"address": hex(addr), "value": val}, timeout=1)
    except:
        pass

def tap(button, delay=0.3):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/tap", params={"button": button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

WILD_DISABLED_ADDR = 0x020397E4
SB1_PTR = 0x03005AEC

print("Loading clean save state slot 1...")
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

sb1 = read32(SB1_PTR)
wild = read8(WILD_DISABLED_ADDR)
x = read16(sb1)
y = read16(sb1 + 2)
mg = read8(sb1 + 4)
mn = read8(sb1 + 5)
repel = read16(sb1 + 0x13DE)
print(f"State: ({x},{y}) Map({mg},{mn}) wild={wild} repel={repel}")

print(f"\n=== Long Walk Test (500 steps, 200ms delay) ===")
prev_x, prev_y = x, y
steps = 0
encounters = 0

for i in range(500):
    # Random directions biased up
    r = random.random()
    if r < 0.35:
        d = "Up"
    elif r < 0.55:
        d = "Down"
    elif r < 0.75:
        d = "Left"
    else:
        d = "Right"

    tap(d, 0.2)

    cx = read16(sb1)
    cy = read16(sb1 + 2)
    if cx != prev_x or cy != prev_y:
        steps += 1
        prev_x, prev_y = cx, cy

    # Battle check
    btl = read32(0x02022E90)
    if btl and btl > 0:
        encounters += 1
        mg2 = read8(sb1 + 4)
        mn2 = read8(sb1 + 5)
        hp = read16(0x020241E6)
        w = read8(WILD_DISABLED_ADDR)
        print(f"\n  *** WILD ENCOUNTER! ***")
        print(f"  Step {i}, btlFlags=0x{btl:08X}")
        print(f"  Pos: ({cx},{cy}) Map({mg2},{mn2})")
        print(f"  HP: {hp}, wild_disabled: {w}")
        print(f"  Actual steps: {steps}")
        break

    if i % 50 == 0:
        mg2 = read8(sb1 + 4)
        mn2 = read8(sb1 + 5)
        w = read8(WILD_DISABLED_ADDR)
        print(f"  i={i}: ({cx},{cy}) Map({mg2},{mn2}) "
              f"steps={steps} wild={w}")

if encounters == 0:
    mg2 = read8(sb1 + 4)
    mn2 = read8(sb1 + 5)
    w = read8(WILD_DISABLED_ADDR)
    print(f"\n*** NO ENCOUNTERS in {steps} steps (500 taps) ***")
    print(f"Final: ({prev_x},{prev_y}) Map({mg2},{mn2}) wild={w}")
    print("The encounter system is still broken.")
else:
    print(f"\n*** ENCOUNTERS WORK! ***")
    print(f"First encounter after {steps} actual steps")
