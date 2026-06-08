"""
Long walk test: clear sWildEncountersDisabled every step, 200+ steps
NO A/B presses (avoid triggering warps/events)
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

# Load save state
print("Loading save state slot 1...")
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

sb1 = read32(SB1_PTR)
print(f"SB1: 0x{sb1:08X}")

# Clear flags
write8(WILD_DISABLED_ADDR, 0)
write16(sb1 + 0x13DE, 0)

x = read16(sb1)
y = read16(sb1 + 2)
mg = read8(sb1 + 4)
mn = read8(sb1 + 5)
wild = read8(WILD_DISABLED_ADDR)
print(f"Start: ({x},{y}) Map({mg},{mn}) wild={wild}")

print(f"\n=== Walking 300 steps (Up/Down/Left/Right, 300ms delay) ===")
prev_x, prev_y = x, y
steps = 0
encounters = 0
flag_resets = 0

for i in range(300):
    # Clear flag every step
    w = read8(WILD_DISABLED_ADDR)
    if w != 0:
        write8(WILD_DISABLED_ADDR, 0)
        flag_resets += 1

    # Random directions, biased Up (to explore grass)
    r = random.random()
    if r < 0.40:
        d = "Up"
    elif r < 0.60:
        d = "Down"
    elif r < 0.80:
        d = "Left"
    else:
        d = "Right"
    tap(d, 0.3)

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
        print(f"\n  *** ENCOUNTER at step {i}! ***")
        print(f"  btl=0x{btl:08X} pos=({cx},{cy}) Map({mg2},{mn2}) HP={hp}")
        print(f"  Moved {steps} actual steps, flag reset {flag_resets} times")
        break

    if i % 50 == 0:
        mg2 = read8(sb1 + 4)
        mn2 = read8(sb1 + 5)
        print(f"  i={i}: ({cx},{cy}) Map({mg2},{mn2}) steps={steps} "
              f"flagResets={flag_resets}")

if encounters == 0:
    print(f"\n*** NO ENCOUNTERS in {steps} actual steps (300 taps) ***")
    print(f"Flag was reset {flag_resets} times")
    mg2 = read8(sb1 + 4)
    mn2 = read8(sb1 + 5)
    print(f"Final pos: ({prev_x},{prev_y}) Map({mg2},{mn2})")
    print("\nConclusion: sWildEncountersDisabled=1 is NOT the sole issue.")
    print("There must be another blocker.")
