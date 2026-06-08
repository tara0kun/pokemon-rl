"""
Test different save state slots and find a clean one
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

for slot in [1, 2, 3]:
    print(f"\n{'='*50}")
    print(f"Testing save state SLOT {slot}")
    print(f"{'='*50}")

    try:
        r = session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": slot}, timeout=5)
        time.sleep(0.5)
    except Exception as e:
        print(f"  Failed to load slot {slot}: {e}")
        continue

    sb1 = read32(SB1_PTR)
    if not sb1 or sb1 < 0x02000000:
        print(f"  SB1 invalid: {sb1}")
        continue

    x = read16(sb1)
    y = read16(sb1 + 2)
    mg = read8(sb1 + 4)
    mn = read8(sb1 + 5)
    wild = read8(WILD_DISABLED_ADDR)
    hp = read16(0x020241E6)
    hp_max = read16(0x020241E8)
    level = read8(0x020241E4)
    party = read8(0x0202418D)
    repel = read16(sb1 + 0x13DE)

    print(f"  Position: ({x},{y}) Map({mg},{mn})")
    print(f"  sWildEncountersDisabled: {wild}")
    print(f"  Party: {party} pokemon, Lead Lv{level} HP={hp}/{hp_max}")
    print(f"  Repel: {repel}")

    # Quick walk test (10 steps with flag clearing)
    write8(WILD_DISABLED_ADDR, 0)
    write16(sb1 + 0x13DE, 0)

    prev_x, prev_y = x, y
    moved = 0
    encounter = False
    for i in range(30):
        write8(WILD_DISABLED_ADDR, 0)
        tap("Up", 0.3)
        cx = read16(sb1)
        cy = read16(sb1 + 2)
        if cx != prev_x or cy != prev_y:
            moved += 1
            prev_x, prev_y = cx, cy
        btl = read32(0x02022E90)
        if btl and btl > 0:
            encounter = True
            print(f"  ENCOUNTER at step {i}! btl=0x{btl:08X}")
            break

    mg2 = read8(sb1 + 4)
    mn2 = read8(sb1 + 5)
    wild2 = read8(WILD_DISABLED_ADDR)
    print(f"  After walk: pos=({prev_x},{prev_y}) Map({mg2},{mn2}) "
          f"moved={moved} wild={wild2} encounter={encounter}")

# Also try: load slot 1, let scripts run, then save to slot 3
print(f"\n{'='*50}")
print("Creating CLEAN save state (slot 3)...")
print(f"{'='*50}")

session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

# Let the game run with A presses to advance scripts
print("Advancing scripts with A/B presses...")
for i in range(100):
    if i % 3 == 0:
        tap("A", 0.15)
    elif i % 3 == 1:
        tap("B", 0.15)
    else:
        tap("Up", 0.15)

sb1 = read32(SB1_PTR)
x = read16(sb1)
y = read16(sb1 + 2)
mg = read8(sb1 + 4)
mn = read8(sb1 + 5)
wild = read8(WILD_DISABLED_ADDR)
print(f"After script advancement: ({x},{y}) Map({mg},{mn}) wild={wild}")

# Now clear the flag and navigate to grass
write8(WILD_DISABLED_ADDR, 0)

# Walk around to find grass
for i in range(50):
    write8(WILD_DISABLED_ADDR, 0)
    directions = ["Up", "Up", "Left", "Up", "Right", "Up"][i % 6]
    tap(directions, 0.2)

sb1 = read32(SB1_PTR)
x = read16(sb1)
y = read16(sb1 + 2)
mg = read8(sb1 + 4)
mn = read8(sb1 + 5)
wild = read8(WILD_DISABLED_ADDR)
print(f"After navigation: ({x},{y}) Map({mg},{mn}) wild={wild}")

# Save to slot 3
print("Saving to slot 3...")
session.post(f"{BASE_URL}/core/savestateslot", params={"slot": 3}, timeout=5)
time.sleep(0.5)

# Verify slot 3
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 3}, timeout=5)
time.sleep(0.5)
wild3 = read8(WILD_DISABLED_ADDR)
print(f"Slot 3 verification: wild={wild3}")
