"""
Continue new game playthrough from truck exit (Mom cutscene).
Current state: (4,10) Map(0,9) Littleroot Town, Mom talking.

Flow:
1. A through Mom dialog -> auto-walk into house
2. House events (clock, Vigoroth, TV)
3. Exit house
4. Walk north to Route 101
5. Birch rescue event -> pick starter
6. Battle Zigzagoon
7. Return to lab -> Pokedex
8. Walk to Route 101 for encounter test
"""
import requests
import time
import random
import sys

sys.stdout.reconfigure(line_buffering=True)
session = requests.Session()
base = "http://localhost:5000"

SB1_PTR = 0x03005AEC
ADDR_BATTLE_FLAGS = 0x02022E90

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

def get_map():
    sb1 = rval(SB1_PTR, 32)
    if not sb1 or sb1 < 0x02000000:
        return None
    return (rval(sb1, 16), rval(sb1+2, 16), rval(sb1+4, 8), rval(sb1+5, 8))

def pos_str():
    m = get_map()
    return f"({m[0]},{m[1]}) Map({m[2]},{m[3]})" if m else "??"

print("=" * 60, flush=True)
print("CONTINUE FROM TRUCK EXIT", flush=True)
print("=" * 60, flush=True)
print(f"Start: {pos_str()}", flush=True)

# ============================================================
# PHASE 1: Mom dialog + enter house
# ============================================================
print("\n[1] Mom dialog...", flush=True)
for i in range(80):
    tap("A", 0.35)
    if i % 20 == 19:
        m = get_map()
        if m:
            print(f"  A{i+1}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)
            ss(f"ct_01_{i+1}")
            # Check if we entered the house (map group changes)
            if m[2] != 0:
                print(f"  Entered interior!", flush=True)

print(f"After mom: {pos_str()}", flush=True)
ss("ct_02_after_mom")

# ============================================================
# PHASE 2: House events
# Clock setting, Vigoroth moving furniture, Mom shows TV
# ============================================================
print("\n[2] House events...", flush=True)

# Walk around + press A for events
for phase in range(3):
    # Press A for dialogs
    for i in range(30):
        tap("A", 0.3)

    # Walk around
    for i in range(10):
        tap(["Down", "Left", "Up", "Right"][i%4], 0.3)

    m = get_map()
    if m:
        print(f"  Phase {phase}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)
    ss(f"ct_03_house_{phase}")

# More A for clock and TV events
for i in range(60):
    tap("A", 0.35)
    if i % 30 == 29:
        print(f"  A{i+1}: {pos_str()}", flush=True)

# ============================================================
# PHASE 3: Exit house
# ============================================================
print("\n[3] Exit house...", flush=True)
m = get_map()
print(f"  Current: {pos_str()}", flush=True)
ss("ct_04_in_house")

# Walk down to exit
for i in range(15):
    tap("B", 0.1)  # Close dialogs
    tap("Down", 0.3)

m = get_map()
print(f"  After down: {pos_str()}", flush=True)

# Hold to trigger door warp
tap("Down", 0.1)
time.sleep(0.3)
hold("Down", 120)
time.sleep(4)
m = get_map()
print(f"  After hold: {pos_str()}", flush=True)
ss("ct_05_exit")

# Retry if still inside
for attempt in range(5):
    m = get_map()
    if m and m[2] == 0:  # Outside (map group 0)
        print(f"  Outside! {pos_str()}", flush=True)
        break
    print(f"  Retry {attempt}...", flush=True)
    # Try A (might need to interact with door)
    for i in range(10):
        tap("A", 0.2)
    for i in range(8):
        tap("Down", 0.3)
    # Try hold
    hold("Down", 120)
    time.sleep(4)
    # If on 2F, need stairs first
    m = get_map()
    if m:
        print(f"  {pos_str()}", flush=True)
        # Try walking to staircase (usually at specific position)
        for i in range(5):
            tap("Right", 0.3)
        for i in range(5):
            tap("Down", 0.3)
        for i in range(5):
            tap("Left", 0.3)
        tap("A", 0.3)
        hold("Down", 120)
        time.sleep(4)
        print(f"  After stairs attempt: {pos_str()}", flush=True)
    ss(f"ct_05_retry_{attempt}")

# ============================================================
# PHASE 4: Littleroot Town - walk north to Route 101
# ============================================================
print("\n[4] Walk to Route 101...", flush=True)
m = get_map()
print(f"  Current: {pos_str()}", flush=True)
ss("ct_06_outside")

# Press A for any outdoor dialogs
for i in range(20):
    tap("A", 0.3)

# Move LEFT to avoid house doors, then NORTH
for i in range(6):
    tap("Left", 0.3)

for i in range(50):
    tap("Up", 0.3)
    if i % 8 == 7:
        tap("A", 0.3)
    if i % 10 == 9:
        m = get_map()
        if m:
            print(f"  N{i+1}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)
            if m[2] == 0 and m[3] == 16:
                print("  ON ROUTE 101!", flush=True)
                break

ss("ct_07_north")

# ============================================================
# PHASE 5: Birch rescue event
# Birch is being chased by Zigzagoon on Route 101
# Dialog triggers, player picks starter from Birch's bag
# ============================================================
print("\n[5] Birch event + starter...", flush=True)
m = get_map()
print(f"  Current: {pos_str()}", flush=True)

for i in range(150):
    tap("A", 0.35)
    btl = rval(ADDR_BATTLE_FLAGS, 32)
    if btl and btl > 0 and i > 15:
        print(f"  BATTLE at press {i}!", flush=True)
        ss("ct_08_battle")
        break
    if i % 30 == 29:
        m = get_map()
        if m:
            print(f"  E{i+1}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)

# ============================================================
# PHASE 6: Battle Zigzagoon
# ============================================================
print("\n[6] Zigzagoon battle...", flush=True)
in_btl = False
for i in range(300):
    tap("A", 0.25)
    btl = rval(ADDR_BATTLE_FLAGS, 32)
    if btl and btl > 0:
        in_btl = True
    elif in_btl:
        print(f"  Battle ended at {i}", flush=True)
        break
    if i % 60 == 59:
        print(f"  Fighting... ({i+1})", flush=True)

ss("ct_09_after_battle")
for i in range(50):
    tap("A", 0.35)
m = get_map()
print(f"  After battle: {pos_str()}", flush=True)

# ============================================================
# PHASE 7: Return to lab / Pokedex
# After battle, Birch takes player back to lab
# ============================================================
print("\n[7] Lab + Pokedex...", flush=True)
for i in range(40):
    tap("Down", 0.3)
    if i % 5 == 4:
        tap("A", 0.3)

for i in range(30):
    tap(["Down","Right","Up","Left"][i%4], 0.3)
    if i % 3 == 2:
        tap("A", 0.3)

for i in range(120):
    tap("A", 0.35)
    if i % 30 == 29:
        m = get_map()
        if m:
            print(f"  Lab A{i+1}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)

# ============================================================
# PHASE 8: Route 101 for encounter test
# ============================================================
print("\n[8] Go to Route 101...", flush=True)

# Exit lab
for i in range(10):
    tap("Down", 0.3)
hold("Down", 120)
time.sleep(4)

m = get_map()
print(f"  After lab exit: {pos_str()}", flush=True)

for i in range(5):
    tap("Left", 0.3)

for i in range(50):
    tap("Up", 0.3)
    if i % 5 == 4:
        tap("A", 0.3)
    if i % 10 == 9:
        m = get_map()
        if m:
            print(f"  N{i+1}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)
            if m[2] == 0 and m[3] == 16:
                print("  ON ROUTE 101!", flush=True)
                break

m = get_map()
print(f"\n  FINAL: {pos_str()}", flush=True)
ss("ct_10_final")

# ============================================================
# ENCOUNTER TEST
# ============================================================
print("\n" + "=" * 60, flush=True)
print("ENCOUNTER TEST ON NEW GAME", flush=True)
print("=" * 60, flush=True)

sb1 = rval(SB1_PTR, 32)
if not sb1 or sb1 < 0x02000000:
    print("SB1 invalid!", flush=True)
    sys.exit(1)

prev_x = rval(sb1, 16) or 0
prev_y = rval(sb1 + 2, 16) or 0
moved = 0
encounters = 0

for i in range(500):
    d = random.choice(["Up", "Down", "Left", "Right"])
    tap(d, 0.18)
    sb1 = rval(SB1_PTR, 32)
    if not sb1 or sb1 < 0x02000000:
        continue
    cx = rval(sb1, 16)
    cy = rval(sb1 + 2, 16)
    if cx and cy and (cx != prev_x or cy != prev_y):
        moved += 1
        prev_x, prev_y = cx, cy
    btl = rval(ADDR_BATTLE_FLAGS, 32)
    if btl and 0 < btl < 0x10000:
        encounters += 1
        cmg = rval(sb1 + 4, 8)
        cmn = rval(sb1 + 5, 8)
        print(f"\n  *** ENCOUNTER #{encounters} step {i}! ({cx},{cy}) Map({cmg},{cmn}) ***", flush=True)
        ss(f"ct_enc_{encounters}")
        time.sleep(1)
        for _ in range(3):
            tap("B", 0.2)
        tap("Down", 0.3)
        tap("Right", 0.3)
        tap("A", 0.5)
        time.sleep(2)
        for _ in range(10):
            tap("A", 0.3)
        prev_x = rval(sb1, 16) or 0
        prev_y = rval(sb1 + 2, 16) or 0
        if encounters >= 3:
            break
    if i % 100 == 99:
        cmg = rval(sb1 + 4, 8)
        cmn = rval(sb1 + 5, 8)
        print(f"  s{i+1}: ({cx},{cy}) Map({cmg},{cmn}) mv={moved} enc={encounters}", flush=True)

print(f"\n{'='*60}", flush=True)
print(f"RESULT: {moved} moves, {encounters} encounters", flush=True)
if encounters > 0:
    print("*** ENCOUNTERS WORK ON NEW GAME! ***", flush=True)
elif moved > 20:
    p = 0.889 ** moved
    print(f"P(0|{moved}mv) = {p:.2e}", flush=True)
    print("*** ENCOUNTERS STILL BROKEN ***", flush=True)
print(f"{'='*60}", flush=True)
