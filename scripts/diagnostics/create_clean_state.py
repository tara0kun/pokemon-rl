"""
Create a clean save state by:
1. Loading the problematic slot 1
2. Rapidly pressing A to advance through scripts
3. Waiting for scripts to complete
4. Navigating to Route 101 grass
5. Saving to slot 1
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

def tap(button, delay=0.05):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/tap", params={"button": button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

WILD_DISABLED_ADDR = 0x020397E4
SB1_PTR = 0x03005AEC

def get_state():
    sb1 = read32(SB1_PTR)
    if not sb1 or sb1 < 0x02000000:
        return None
    x = read16(sb1)
    y = read16(sb1 + 2)
    mg = read8(sb1 + 4)
    mn = read8(sb1 + 5)
    wild = read8(WILD_DISABLED_ADDR)
    return {"sb1": sb1, "x": x, "y": y, "mg": mg, "mn": mn, "wild": wild}

# Step 1: Load slot 1
print("Step 1: Loading slot 1...")
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(0.5)

state = get_state()
print(f"  Initial: ({state['x']},{state['y']}) Map({state['mg']},{state['mn']}) wild={state['wild']}")

# Step 2: Rapidly press A to advance through scripts
print("\nStep 2: Advancing scripts (200 A presses, 100ms each)...")
for i in range(200):
    tap("A", 0.1)
    if i % 50 == 49:
        state = get_state()
        print(f"  After {i+1} presses: ({state['x']},{state['y']}) Map({state['mg']},{state['mn']}) wild={state['wild']}")

# Step 3: Press B to close any remaining dialogs
print("\nStep 3: Closing dialogs (50 B presses)...")
for i in range(50):
    tap("B", 0.1)

state = get_state()
print(f"  After B: ({state['x']},{state['y']}) Map({state['mg']},{state['mn']}) wild={state['wild']}")

# Step 4: Try walking to check if player is free
print("\nStep 4: Testing movement...")
prev_x, prev_y = state['x'], state['y']
moved = 0
for i in range(20):
    tap("Down", 0.2)
    state = get_state()
    if state['x'] != prev_x or state['y'] != prev_y:
        moved += 1
        prev_x, prev_y = state['x'], state['y']
        print(f"  Moved to ({state['x']},{state['y']}) Map({state['mg']},{state['mn']})")

if moved == 0:
    print("  Player is stuck! Trying more B presses...")
    for i in range(100):
        tap("B", 0.08)
    # Try walking again
    for i in range(20):
        tap("Down", 0.2)
        state = get_state()
        if state['x'] != prev_x or state['y'] != prev_y:
            moved += 1
            prev_x, prev_y = state['x'], state['y']
            print(f"  Moved to ({state['x']},{state['y']}) Map({state['mg']},{state['mn']})")

print(f"  Total moves: {moved}")

# Step 5: Navigate to Route 101 (walk south if in Oldale, north if in Littleroot)
state = get_state()
print(f"\nStep 5: Current state: ({state['x']},{state['y']}) Map({state['mg']},{state['mn']}) wild={state['wild']}")

# If we're in Petalburg (0,0) or other map, try to navigate
# Walk south to try to get to Route 101
if state['mg'] == 0 and state['mn'] == 0:
    print("  In Petalburg City, navigating south to Route 102...")
    for i in range(100):
        tap("Down", 0.15)
elif state['mg'] == 0 and state['mn'] in [9]:
    print("  In Littleroot, navigating north to Route 101...")
    for i in range(30):
        tap("Up", 0.2)
elif state['mg'] == 0 and state['mn'] == 10:
    print("  In Oldale, navigating south to Route 101...")
    for i in range(30):
        tap("Down", 0.2)

state = get_state()
print(f"  After navigation: ({state['x']},{state['y']}) Map({state['mg']},{state['mn']}) wild={state['wild']}")

# Final check
print("\nStep 6: Final state check...")
state = get_state()
print(f"  Position: ({state['x']},{state['y']})")
print(f"  Map: ({state['mg']},{state['mn']})")
print(f"  sWildEncountersDisabled: {state['wild']}")

if state['wild'] == 0 and state['mg'] == 0 and state['mn'] == 16:
    print("\n*** PERFECT! Player on Route 101 with encounters enabled! ***")
    print("Saving to slot 1...")
    session.post(f"{BASE_URL}/core/savestateslot", params={"slot": 1}, timeout=5)
    print("Saved! Verifying...")
    session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
    time.sleep(0.5)
    state = get_state()
    print(f"  Verified: ({state['x']},{state['y']}) Map({state['mg']},{state['mn']}) wild={state['wild']}")
elif state['wild'] == 0:
    print(f"\n*** Encounters enabled but not on Route 101! ***")
    print(f"Saving to slot 3 for testing...")
    session.post(f"{BASE_URL}/core/savestateslot", params={"slot": 3}, timeout=5)
else:
    print(f"\n*** Still has problems. Need manual intervention. ***")
    print(f"Try saving the game manually in mGBA when the player is freely walking on Route 101.")
