"""
sWildEncountersDisabled をクリアしてエンカウントテスト
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
        return True
    except:
        return False

def write16(addr, val):
    try:
        session.post(f"{BASE_URL}/core/write16", params={"address": hex(addr), "value": val}, timeout=1)
        return True
    except:
        return False

def tap(button, delay=0.3):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/tap", params={"button": button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

# Load save state
print("Loading save state slot 1...")
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

# Read current sWildEncountersDisabled
WILD_DISABLED_ADDR = 0x020397E4
val = read8(WILD_DISABLED_ADDR)
print(f"\nsWildEncountersDisabled (0x{WILD_DISABLED_ADDR:08X}): {val}")

# Clear it!
print("Writing 0 to sWildEncountersDisabled...")
write8(WILD_DISABLED_ADDR, 0)
val_after = read8(WILD_DISABLED_ADDR)
print(f"After clear: {val_after}")

# Also clear Repel just in case
sb1 = read32(0x03005AEC)
if sb1 and sb1 > 0x02000000:
    write16(sb1 + 0x13DE, 0)
    print(f"Repel cleared to 0")

# Clear any pending scripts by pressing A/B
print("\nClearing scripts...")
for _ in range(10):
    tap("A", 0.1)
for _ in range(5):
    tap("B", 0.1)

# Walk test
print("\n=== Walking Test (100 steps, 300ms delay) ===")
prev_x = read16(sb1) if sb1 else 0
prev_y = read16(sb1 + 2) if sb1 else 0
steps = 0
encounters = 0

for i in range(100):
    direction = ["Up", "Down", "Up", "Left", "Up", "Right", "Up", "Up"][i % 8]
    tap(direction, 0.3)

    cx = read16(sb1)
    cy = read16(sb1 + 2)
    if cx != prev_x or cy != prev_y:
        steps += 1
        prev_x, prev_y = cx, cy

    # Check for battle
    btl = read32(0x02022E90)
    if btl and btl > 0:
        mg = read8(sb1 + 4)
        mn = read8(sb1 + 5)
        encounters += 1
        print(f"  *** ENCOUNTER! *** Step {i}, btlFlags=0x{btl:08X}, "
              f"pos=({cx},{cy}) Map({mg},{mn})")
        # Check sWildEncountersDisabled again
        wild_val = read8(WILD_DISABLED_ADDR)
        print(f"  sWildEncountersDisabled during battle: {wild_val}")
        break

    # Check if the flag got reset
    if i % 20 == 0:
        wild_val = read8(WILD_DISABLED_ADDR)
        mg = read8(sb1 + 4)
        mn = read8(sb1 + 5)
        print(f"  Step {i}: pos=({cx},{cy}) Map({mg},{mn}) "
              f"moved={steps} wildDisabled={wild_val}")

if encounters == 0:
    print(f"\nNo encounters in {steps} steps.")
    print("The flag might not be sWildEncountersDisabled, or there's another issue.")
else:
    print(f"\nSUCCESS! Encounters work after clearing 0x{WILD_DISABLED_ADDR:08X}!")
