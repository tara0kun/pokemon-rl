"""
sWildEncountersDisabled を毎ステップクリアしてエンカウントテスト
"""
import requests
import time

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

# Check initial state
wild = read8(WILD_DISABLED_ADDR)
x = read16(sb1)
y = read16(sb1 + 2)
mg = read8(sb1 + 4)
mn = read8(sb1 + 5)
print(f"Initial: pos=({x},{y}) Map({mg},{mn}) wildDisabled={wild}")

# Clear the flag
write8(WILD_DISABLED_ADDR, 0)
# Clear repel
write16(sb1 + 0x13DE, 0)

# NO script clearing - go directly to walking
# Walk ONLY Up/Down to stay on Route 101 grass
print("\n=== Walk Test (clear flag every step) ===")
prev_x, prev_y = x, y
steps = 0
encounters = 0
flag_resets = 0

for i in range(150):
    # Clear sWildEncountersDisabled EVERY step
    cur_wild = read8(WILD_DISABLED_ADDR)
    if cur_wild != 0:
        write8(WILD_DISABLED_ADDR, 0)
        flag_resets += 1

    # Alternate directions to stay in grass area
    direction = ["Up", "Up", "Up", "Down", "Down", "Down"][i % 6]
    tap(direction, 0.3)

    cx = read16(sb1)
    cy = read16(sb1 + 2)
    if cx != prev_x or cy != prev_y:
        steps += 1
        prev_x, prev_y = cx, cy

    # Check for battle
    btl = read32(0x02022E90)
    if btl and btl > 0:
        encounters += 1
        mg = read8(sb1 + 4)
        mn = read8(sb1 + 5)
        hp = read16(0x020241E6)
        print(f"\n  *** ENCOUNTER! *** Step {i}, btlFlags=0x{btl:08X}")
        print(f"  pos=({cx},{cy}) Map({mg},{mn}) HP={hp}")
        print(f"  Flag was reset {flag_resets} times before this encounter")
        break

    if i % 25 == 0:
        mg = read8(sb1 + 4)
        mn = read8(sb1 + 5)
        print(f"  Step {i}: pos=({cx},{cy}) Map({mg},{mn}) "
              f"moved={steps} flagResets={flag_resets} wild={cur_wild}")

if encounters == 0:
    print(f"\nNo encounters in {steps} steps, flag was reset {flag_resets} times.")
    print("sWildEncountersDisabled is NOT the issue, or there's another blocker.")
else:
    print(f"\n*** SUCCESS! Clearing sWildEncountersDisabled WORKS! ***")
    print(f"Total flag resets: {flag_resets}")
