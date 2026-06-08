"""
NEW GAME full playthrough - v7 (STATE MACHINE)

Uses position monitoring to adapt behavior:
  State BOOT:    Down-A pairs to select New Game
  State INTRO:   A presses until we reach the truck
  State TRUCK:   Walk south to exit (Map 25,40)
  State SCENE:   A presses through cutscenes
  State HOUSE:   Navigate out of house
  State TOWN:    Walk north to Route 101
  State EVENT:   Birch rescue + starter selection + battle
  State RETURN:  Return to lab for Pokedex
  State ROUTE:   Encounter test on Route 101
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
    """Return (x, y, map_group, map_num) or None"""
    sb1 = rval(SB1_PTR, 32)
    if not sb1 or sb1 < 0x02000000 or sb1 > 0x03000000:
        return None
    x = rval(sb1, 16)
    y = rval(sb1 + 2, 16)
    mg = rval(sb1 + 4, 8)
    mn = rval(sb1 + 5, 8)
    return (x, y, mg, mn)

def pos_str():
    m = get_map()
    if m:
        return f"({m[0]},{m[1]}) Map({m[2]},{m[3]})"
    return "??"

print("=" * 60, flush=True)
print("NEW GAME FULL PLAYTHROUGH (v7)", flush=True)
print("=" * 60, flush=True)

# ============================================================
# PHASE 1: BOOT - Select New Game
# ============================================================
print("\n=== PHASE 1: BOOT ===", flush=True)
session.post(f'{base}/coreadapter/reset', timeout=5)
time.sleep(3)

# Down-A pairs: skip intro screens + select New Game
for i in range(25):
    tap("Down", 0.5)
    tap("A", 2.0)
    if i % 5 == 4:
        print(f"  pair {i}: {pos_str()}", flush=True)

ss("p1_boot_done")

# A few more confirms for save deletion dialog
for i in range(5):
    tap("A", 1.0)

# ============================================================
# PHASE 2: INTRO - Birch dialog + gender + name + rival
# Monitor for truck map (25,40)
# ============================================================
print("\n=== PHASE 2: INTRO ===", flush=True)

in_truck = False
for i in range(250):
    tap("A", 0.35)

    if i % 30 == 29:
        m = get_map()
        if m:
            print(f"  A{i+1}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)
            # Detect truck
            if m[2] == 25:
                print(f"  *** TRUCK DETECTED! ***", flush=True)
                in_truck = True
                ss("p2_truck_found")
                break

    # Try Start+A at intervals for name entry screens
    if i in [40, 80]:
        print(f"  Trying Start+A for name entry at A{i+1}", flush=True)
        tap("Start", 0.5)
        tap("A", 0.5)

if not in_truck:
    print("  Truck not detected yet, pressing more A...", flush=True)
    for i in range(100):
        tap("A", 0.35)
        if i % 20 == 19:
            m = get_map()
            if m:
                print(f"  extra A{i+1}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)
                if m[2] == 25:
                    in_truck = True
                    break

# ============================================================
# PHASE 3: TRUCK - Walk south to exit
# ============================================================
print("\n=== PHASE 3: TRUCK EXIT ===", flush=True)
m = get_map()
print(f"  Current: {pos_str()}", flush=True)
ss("p3_truck")

# Press A a few times to clear any dialog
for i in range(10):
    tap("A", 0.3)

# Walk south to exit the truck
print("  Walking south...", flush=True)
for i in range(8):
    tap("Down", 0.4)
    m = get_map()
    if m and m[2] != 25:
        print(f"  Exited truck! Now: {pos_str()}", flush=True)
        break
    if i == 3:
        # Try hold for warp
        hold("Down", 60)
        time.sleep(2)
        m = get_map()
        if m and m[2] != 25:
            print(f"  Exited with hold! Now: {pos_str()}", flush=True)
            break

m = get_map()
print(f"  After south: {pos_str()}", flush=True)
ss("p3_after_exit")

# If still in truck, try more aggressively
if m and m[2] == 25:
    print("  Still in truck. More aggressive exit...", flush=True)
    # Clear dialogs
    for i in range(20):
        tap("A", 0.2)
    # Walk to various positions to find exit
    for i in range(10):
        tap("Down", 0.3)
    hold("Down", 120)
    time.sleep(4)
    m = get_map()
    print(f"  After aggressive: {pos_str()}", flush=True)

# ============================================================
# PHASE 4: MOM CUTSCENE
# Press A through all dialog + animations
# ============================================================
print("\n=== PHASE 4: CUTSCENE ===", flush=True)
ss("p4_cutscene")

# Mom approaches, dialog, enter house
for i in range(100):
    tap("A", 0.35)
    if i % 25 == 24:
        m = get_map()
        if m:
            print(f"  Scene A{i+1}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)
            ss(f"p4_scene_{i+1}")

# ============================================================
# PHASE 5: IN HOUSE
# BrendansHouse_1F: exit at (8,8)/(9,8) - door at south
# Need to go downstairs, interact with NPCs, exit
# ============================================================
print("\n=== PHASE 5: IN HOUSE ===", flush=True)
m = get_map()
print(f"  Current: {pos_str()}", flush=True)
ss("p5_house")

# Press A for any Vigoroth/Mom dialogs
for i in range(30):
    tap("A", 0.3)

# Walk around house (explore + trigger events)
# First floor: walk down to clock setting event
for i in range(5):
    tap("Down", 0.3)
for i in range(5):
    tap("Left", 0.3)
for i in range(5):
    tap("Down", 0.3)

# Clock setting dialog - press A
for i in range(30):
    tap("A", 0.3)

m = get_map()
print(f"  After walking: {pos_str()}", flush=True)

# Walk down to exit
for i in range(10):
    tap("Down", 0.3)
    if i % 3 == 2:
        tap("A", 0.3)

# Try hold to exit through door
tap("Down", 0.1)
time.sleep(0.3)
hold("Down", 120)
time.sleep(4)

m = get_map()
print(f"  After hold: {pos_str()}", flush=True)
ss("p5_exit1")

# Keep trying exit
for attempt in range(5):
    if m and m[2] == 0:  # Outside
        print(f"  Outside! {pos_str()}", flush=True)
        break
    print(f"  Exit attempt {attempt+1}...", flush=True)
    # Press A for dialogs
    for i in range(15):
        tap("A", 0.25)
    # Walk down
    for i in range(8):
        tap("Down", 0.3)
    # Hold for warp
    hold("Down", 120)
    time.sleep(4)
    m = get_map()
    print(f"  {pos_str()}", flush=True)
    ss(f"p5_exit{attempt+2}")

# ============================================================
# PHASE 6: LITTLEROOT TOWN
# Walk north to Route 101
# ============================================================
print("\n=== PHASE 6: LITTLEROOT ===", flush=True)
m = get_map()
print(f"  Current: {pos_str()}", flush=True)
ss("p6_town")

# Press A for any outdoor dialogs (Mom's running shoes etc in later event)
for i in range(30):
    tap("A", 0.3)

# Move LEFT to avoid re-entering house door
for i in range(6):
    tap("Left", 0.3)

# Walk NORTH toward Route 101
# Route 101 entrance is at y=0 (northern border)
print("  Walking north...", flush=True)
for i in range(40):
    tap("Up", 0.3)
    if i % 8 == 7:
        tap("A", 0.3)  # Handle any triggered dialog
    if i % 10 == 9:
        m = get_map()
        if m:
            print(f"  N{i+1}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)
            if m[2] == 0 and m[3] == 16:  # Route 101
                print("  *** ON ROUTE 101! ***", flush=True)
                break

ss("p6_north")
m = get_map()
print(f"  Position: {pos_str()}", flush=True)

# ============================================================
# PHASE 7: BIRCH EVENT
# On Route 101, Birch is being chased.
# Dialog auto-triggers, then we pick starter from bag.
# ============================================================
print("\n=== PHASE 7: BIRCH EVENT ===", flush=True)

# Press A through dialog + walk up to find event
for i in range(120):
    tap("A", 0.35)

    # Check for battle
    btl = rval(ADDR_BATTLE_FLAGS, 32)
    if btl and btl > 0 and i > 15:
        print(f"  BATTLE at press {i}!", flush=True)
        ss("p7_battle")
        break

    if i % 30 == 29:
        m = get_map()
        if m:
            print(f"  E{i+1}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)

# ============================================================
# PHASE 8: ZIGZAGOON BATTLE
# ============================================================
print("\n=== PHASE 8: BATTLE ===", flush=True)
in_btl = False
for i in range(250):
    tap("A", 0.25)
    btl = rval(ADDR_BATTLE_FLAGS, 32)
    if btl and btl > 0:
        in_btl = True
    elif in_btl:
        print(f"  Battle ended at {i}", flush=True)
        break
    if i % 50 == 49:
        print(f"  Fighting... ({i+1})", flush=True)

ss("p8_after_battle")

# Post-battle dialog
for i in range(60):
    tap("A", 0.35)

m = get_map()
print(f"  After battle: {pos_str()}", flush=True)

# ============================================================
# PHASE 9: RETURN TO LAB
# Birch takes us to lab, gives Pokedex
# ============================================================
print("\n=== PHASE 9: LAB ===", flush=True)

# Walk south and press A
for i in range(30):
    tap("Down", 0.3)
    if i % 5 == 4:
        tap("A", 0.3)

# Lab dialog (Birch gives stuff)
for i in range(80):
    tap("A", 0.35)
    if i % 20 == 19:
        m = get_map()
        if m:
            print(f"  Lab A{i+1}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)

# More walk + dialog
for i in range(30):
    tap(["Down","Right","Up","Left"][i%4], 0.3)
    if i % 3 == 2:
        tap("A", 0.3)

for i in range(60):
    tap("A", 0.35)

# ============================================================
# PHASE 10: GO TO ROUTE 101
# ============================================================
print("\n=== PHASE 10: ROUTE 101 ===", flush=True)

# Exit lab (walk south)
for i in range(10):
    tap("Down", 0.3)
hold("Down", 120)
time.sleep(4)
m = get_map()
print(f"  After lab exit: {pos_str()}", flush=True)

# Move left, then north
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
ss("p10_final")

# ============================================================
# ENCOUNTER TEST
# ============================================================
print("\n" + "=" * 60, flush=True)
print("ENCOUNTER TEST", flush=True)
print("=" * 60, flush=True)

sb1 = rval(SB1_PTR, 32)
if not sb1 or sb1 < 0x02000000:
    print("SB1 invalid, cannot test", flush=True)
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
        print(f"\n  *** ENC #{encounters} s{i} ({cx},{cy}) Map({cmg},{cmn}) ***", flush=True)
        ss(f"p_enc_{encounters}")
        # Run away
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
    print("*** ENCOUNTERS WORK! ***", flush=True)
elif moved > 20:
    p = 0.889 ** moved
    print(f"P(0|{moved}mv) = {p:.2e}", flush=True)
print(f"{'='*60}", flush=True)
