"""
Complete house events on 2F and play through to Route 101 encounter test.

Current state: (8,7) Map(1,1) = BrendansHouse_2F
The clock event needs to complete, then:
  - Mom calls downstairs for TV
  - Watch TV
  - Exit house
  - Walk north to Route 101
  - Birch event + starter + battle
  - Encounter test
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
    if not sb1 or sb1 < 0x02000000 or sb1 > 0x03000000:
        return None
    return (rval(sb1, 16), rval(sb1+2, 16), rval(sb1+4, 8), rval(sb1+5, 8))

def pos_str():
    m = get_map()
    return f"({m[0]},{m[1]}) Map({m[2]},{m[3]})" if m else "??"

def wait_map_change(target_group=None, target_num=None, timeout=30):
    """Wait until map changes to target (or any change if target=None)"""
    start = get_map()
    for i in range(int(timeout / 0.3)):
        time.sleep(0.3)
        m = get_map()
        if m and target_group is not None and m[2] == target_group:
            if target_num is None or m[3] == target_num:
                return m
        elif m and target_group is None and start and (m[2] != start[2] or m[3] != start[3]):
            return m
    return get_map()

print("=" * 60, flush=True)
print("HOUSE TO ROUTE 101 PLAYTHROUGH", flush=True)
print("=" * 60, flush=True)
print(f"Start: {pos_str()}", flush=True)

# ============================================================
# PHASE 1: Complete 2F events (clock setting)
# ============================================================
print("\n[1] 2F Clock Event...", flush=True)
ss("h2r_01_2f_start")

# The clock is on the wall. In Brendan's room, it's typically
# at the top-left area. We need to face it and press A.
# First, let's walk to different wall positions and try A.

# Strategy: Walk to each position along the top wall and press A
# The clock is usually around x=1-3, y=2 (facing up)

# First, walk LEFT to reach the left side of the room
print("  Walking to clock area...", flush=True)
for i in range(8):
    tap("Left", 0.25)

m = get_map()
print(f"  After left: {pos_str()}", flush=True)

# Walk UP to face the wall
for i in range(6):
    tap("Up", 0.25)

m = get_map()
print(f"  After up: {pos_str()}", flush=True)
ss("h2r_01_at_wall")

# Try pressing A at various positions along the top wall
# The clock event: when you press A facing the clock
# Dialog should appear for time setting
print("  Trying A at wall positions...", flush=True)
for x_pos in range(9):  # Try x=0 to x=8
    # Face up and press A
    tap("Up", 0.15)
    tap("A", 0.5)

    # Check if dialog appeared by taking screenshot every few positions
    if x_pos % 3 == 0:
        ss(f"h2r_01_wall_x{x_pos}")
        m = get_map()
        print(f"  x~{x_pos}: {pos_str()}", flush=True)

    # If a dialog appeared, we need to handle the clock setting
    # Press A a few times (for initial dialog text)
    for j in range(3):
        tap("A", 0.3)

    # Move right to try next position
    tap("Right", 0.25)

# The clock dialog in JP Emerald:
# 1. "とけいを あわせましょう" (Let's set the clock)
# 2. Up/Down to set hours, A to confirm
# 3. Up/Down to set minutes, A to confirm
# 4. "この じかんで いい？" → はい(Left)/いいえ(Right)
# Default might be on いいえ, so we MUST press Left before A

# Now try the clock setting sequence more carefully
# Walk back to center-left area where clock usually is
print("\n  Trying clock setting sequence...", flush=True)
for i in range(5):
    tap("Left", 0.25)

# Face up and interact
tap("Up", 0.15)
tap("A", 0.5)
ss("h2r_01_clock_interact")

# Press A through "let's set the clock" text
for i in range(5):
    tap("A", 0.5)

# Set hours: just press A (accept default hour)
tap("A", 0.8)
ss("h2r_01_clock_hours")

# Set minutes: just press A (accept default)
tap("A", 0.8)
ss("h2r_01_clock_minutes")

# Confirmation dialog: "この じかんで いい？"
# Press LEFT to select はい (Yes), then A
tap("Left", 0.3)
tap("A", 0.8)
ss("h2r_01_clock_confirm")

# Press A through any remaining dialog
for i in range(5):
    tap("A", 0.4)

print(f"  After clock attempt: {pos_str()}", flush=True)

# If clock wasn't found, try interacting with the PC (which triggers
# the "would you like to withdraw POTION?" dialog in some versions)
# and the bed/TV etc.
print("  Trying all interactable objects...", flush=True)

# Walk around the room systematically pressing A everywhere
for y_walk in range(6):
    # Walk to left wall
    for i in range(9):
        tap("Left", 0.2)
    # Walk right across the room, pressing A at each step while facing up
    tap("Up", 0.15)
    for x_walk in range(9):
        tap("A", 0.3)
        tap("Right", 0.2)
    # One step down
    tap("Down", 0.2)

# Handle any clock dialog that may have appeared
# Spam A with occasional Left (for はい selection)
for i in range(20):
    if i % 5 == 3:
        tap("Left", 0.2)
    tap("A", 0.4)

m = get_map()
print(f"  After room scan: {pos_str()}", flush=True)
ss("h2r_01_after_scan")

# ============================================================
# PHASE 2: Go downstairs and handle 1F events
# ============================================================
print("\n[2] Going to 1F...", flush=True)

# Stairs from 2F to 1F: go to x=8, face up / hold up to trigger warp
# Walk to right side first
for i in range(9):
    tap("Right", 0.2)

# Walk up
for i in range(6):
    tap("Up", 0.2)

# Now at right edge top area - the stairs should be here
# Hold Up to trigger stair warp
hold("Up", 60)
time.sleep(2)

m = get_map()
print(f"  After stairs attempt: {pos_str()}", flush=True)
ss("h2r_02_stairs")

# If still on 2F, try different approaches to find stairs
if m and m[2] == 1 and m[3] == 1:
    print("  Still on 2F. Trying systematic stair search...", flush=True)
    # The stairs might be at a specific tile. Try walking to various
    # positions and holding Up
    for try_x in range(9):
        # Walk to position
        for i in range(9):
            tap("Left", 0.15)
        for i in range(try_x):
            tap("Right", 0.15)
        # Walk to top
        for i in range(6):
            tap("Up", 0.15)
        # Try hold up for warp
        hold("Up", 60)
        time.sleep(1.5)
        m = get_map()
        if m and not (m[2] == 1 and m[3] == 1):
            print(f"  Found stairs at x~{try_x}! Now: {pos_str()}", flush=True)
            break
    else:
        # Try hold Down at bottom of room (some houses have stairs going down)
        for i in range(6):
            tap("Down", 0.15)
        hold("Down", 60)
        time.sleep(1.5)
        m = get_map()
        print(f"  After down attempt: {pos_str()}", flush=True)

m = get_map()
print(f"  Current: {pos_str()}", flush=True)
ss("h2r_02_floor")

# ============================================================
# PHASE 3: 1F Events (Mom + TV)
# ============================================================
print("\n[3] 1F Events...", flush=True)

# If we're on 1F Map(1,0), we need to:
# 1. Talk to Mom near TV
# 2. Watch TV scene
# 3. Exit through door at south

# Press A for Mom's dialog
for i in range(30):
    tap("A", 0.35)
    if i % 10 == 9:
        m = get_map()
        print(f"  1F A{i+1}: {pos_str()}", flush=True)

# If Mom sends us back to 2F, we need to complete clock first
m = get_map()
if m and m[2] == 1 and m[3] == 1:
    print("  Sent back to 2F! Clock event incomplete.", flush=True)
    print("  Re-attempting clock setting...", flush=True)
    ss("h2r_03_back_2f")

    # The clock is on the WALL of the room
    # In Brendan's room 2F, the clock is at the top-left wall area
    # Walk to position and interact facing UP

    # Walk to top-left
    for i in range(9):
        tap("Left", 0.15)
    for i in range(6):
        tap("Up", 0.15)

    # The clock position: try x=1, facing up (typical JP Emerald)
    tap("Right", 0.2)  # x=1
    tap("Up", 0.15)    # face up
    tap("A", 0.8)      # interact
    ss("h2r_03_clock_try2")

    # Handle the dialog properly this time
    # Phase 1: Opening text - just press A
    for i in range(8):
        tap("A", 0.5)
    ss("h2r_03_clock_dialog1")

    # Phase 2: Hour setting - press A to accept
    tap("A", 1.0)
    ss("h2r_03_clock_hours")

    # Phase 3: Minute setting - press A to accept
    tap("A", 1.0)
    ss("h2r_03_clock_min")

    # Phase 4: Confirmation - MUST select はい (Yes)
    # In JP games, はい is typically LEFT option
    # Try: Left, then A
    tap("Left", 0.3)
    tap("A", 1.0)
    ss("h2r_03_clock_yes")

    # If that didn't work, try pressing A a few more times
    for i in range(10):
        tap("A", 0.5)

    # Check if clock is done by trying different positions
    # Maybe the clock is at x=2 or x=3
    for cx in range(5):
        tap("Right", 0.2) if cx > 0 else None
        tap("Up", 0.15)
        tap("A", 0.8)
        # Clock dialog sequence
        for j in range(5):
            tap("A", 0.5)
        tap("A", 1.0)  # hours
        tap("A", 1.0)  # minutes
        tap("Left", 0.3)  # select はい
        tap("A", 1.0)  # confirm
        for j in range(5):
            tap("A", 0.4)

    ss("h2r_03_clock_done")
    print(f"  After clock retry: {pos_str()}", flush=True)

    # Try going downstairs again
    for i in range(9):
        tap("Right", 0.15)
    for i in range(6):
        tap("Up", 0.15)
    hold("Up", 60)
    time.sleep(2)
    m = get_map()
    print(f"  After stairs: {pos_str()}", flush=True)

# ============================================================
# PHASE 3b: More 1F handling
# ============================================================
m = get_map()
if m and m[2] == 1 and m[3] == 0:
    print("  On 1F! Handling events...", flush=True)

    # Press A through Mom/TV dialog
    for i in range(50):
        tap("A", 0.35)
        if i % 15 == 14:
            m = get_map()
            print(f"  1F A{i+1}: {pos_str()}", flush=True)
            # Check if we got sent back to 2F
            if m and m[2] == 1 and m[3] == 1:
                print("  Back to 2F again!", flush=True)
                break

    # Walk south to exit door
    for i in range(10):
        tap("Down", 0.3)

    # Hold down to trigger door warp
    hold("Down", 120)
    time.sleep(4)

    m = get_map()
    print(f"  After exit attempt: {pos_str()}", flush=True)
    ss("h2r_03_exit")

# ============================================================
# PHASE 4: Keep trying until we're outside
# ============================================================
print("\n[4] Getting outside...", flush=True)

for big_attempt in range(10):
    m = get_map()
    if m and m[2] == 0:  # Outside (map group 0)
        print(f"  OUTSIDE! {pos_str()}", flush=True)
        break

    print(f"  Attempt {big_attempt+1}: {pos_str()}", flush=True)
    ss(f"h2r_04_attempt_{big_attempt}")

    if m and m[2] == 1 and m[3] == 1:  # 2F
        # Try clock event again with different approach
        # Walk to EVERY wall position and try A
        for wall_y in [2, 3]:  # Top wall area
            for wall_x in range(9):
                # Navigate to position
                # Go to top-left first
                for _ in range(9): tap("Left", 0.12)
                for _ in range(8): tap("Up", 0.12)
                # Then to target
                for _ in range(wall_x): tap("Right", 0.12)
                for _ in range(wall_y - 2): tap("Down", 0.12)
                # Face up and interact
                tap("Up", 0.12)
                tap("A", 0.6)

                # Quick check if dialog appeared - try clock sequence
                tap("A", 0.4)
                tap("A", 0.4)
                tap("A", 0.4)
                tap("A", 0.8)  # hours confirm
                tap("A", 0.8)  # minutes confirm
                tap("Left", 0.2)  # select Yes
                tap("A", 0.8)  # confirm
                tap("A", 0.4)
                tap("A", 0.4)

                m = get_map()
                if m and not (m[2] == 1 and m[3] == 1):
                    print(f"  Map changed at wall ({wall_x},{wall_y})! {pos_str()}", flush=True)
                    break
            else:
                continue
            break

        # Try stairs again
        for _ in range(9): tap("Right", 0.12)
        for _ in range(8): tap("Up", 0.12)
        hold("Up", 60)
        time.sleep(2)
        m = get_map()
        print(f"  After stairs: {pos_str()}", flush=True)

    elif m and m[2] == 1 and m[3] == 0:  # 1F
        # Press A for events, then try to exit
        for i in range(30):
            tap("A", 0.3)

        # Walk to exit (south)
        for _ in range(10): tap("Down", 0.25)
        hold("Down", 120)
        time.sleep(4)
        m = get_map()
        print(f"  After 1F exit: {pos_str()}", flush=True)

    else:
        # Unknown location - press A and walk around
        for i in range(20):
            tap("A", 0.3)
        for i in range(10):
            tap(["Down","Left","Right","Up"][i%4], 0.3)

ss("h2r_04_outside")
m = get_map()
print(f"  Final position: {pos_str()}", flush=True)

# ============================================================
# PHASE 5: Walk to Route 101
# ============================================================
print("\n[5] Walking to Route 101...", flush=True)

# Press A to handle any outdoor dialogs
for i in range(20):
    tap("A", 0.3)

# Move LEFT to avoid house doors, then NORTH
for i in range(8):
    tap("Left", 0.25)

on_route = False
for i in range(60):
    tap("Up", 0.25)
    if i % 5 == 4:
        tap("A", 0.3)  # Handle any dialog
    if i % 10 == 9:
        m = get_map()
        if m:
            print(f"  N{i+1}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)
            if m[2] == 0 and m[3] == 16:  # Route 101
                print("  ON ROUTE 101!", flush=True)
                on_route = True
                break

ss("h2r_05_north")

# If not on Route 101, might need to navigate around obstacles
if not on_route:
    m = get_map()
    print(f"  Not on route yet: {pos_str()}", flush=True)
    # Try different paths
    for i in range(3):
        tap("Right", 0.25)
    for i in range(30):
        tap("Up", 0.25)
        if i % 10 == 9:
            m = get_map()
            if m and m[2] == 0 and m[3] == 16:
                print("  ON ROUTE 101!", flush=True)
                on_route = True
                break

# ============================================================
# PHASE 6: Birch rescue event + starter
# ============================================================
print("\n[6] Birch event + starter...", flush=True)
m = get_map()
print(f"  Current: {pos_str()}", flush=True)

# Walk up further to trigger the Birch event
for i in range(20):
    tap("Up", 0.3)
    if i % 5 == 4:
        tap("A", 0.3)

# Press A through dialog (Birch being chased, pick starter from bag)
battle_found = False
for i in range(200):
    tap("A", 0.35)

    # Check for battle
    btl = rval(ADDR_BATTLE_FLAGS, 32)
    if btl and btl > 0 and i > 20:
        print(f"  BATTLE at press {i}!", flush=True)
        ss("h2r_06_battle")
        battle_found = True
        break

    if i % 40 == 39:
        m = get_map()
        if m:
            print(f"  E{i+1}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)
        ss(f"h2r_06_event_{i+1}")

# ============================================================
# PHASE 7: Battle Zigzagoon
# ============================================================
print("\n[7] Zigzagoon battle...", flush=True)
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

ss("h2r_07_after_battle")

# Post-battle dialog
for i in range(60):
    tap("A", 0.35)

m = get_map()
print(f"  After battle: {pos_str()}", flush=True)

# ============================================================
# PHASE 8: Lab + Pokedex
# ============================================================
print("\n[8] Lab + Pokedex...", flush=True)

# Walk south toward lab (Birch leads you back)
for i in range(30):
    tap("Down", 0.3)
    if i % 5 == 4:
        tap("A", 0.3)

# Lab dialog
for i in range(120):
    tap("A", 0.35)
    if i % 30 == 29:
        m = get_map()
        if m:
            print(f"  Lab A{i+1}: ({m[0]},{m[1]}) Map({m[2]},{m[3]})", flush=True)

# More walk + dialog for leaving lab
for i in range(30):
    tap(["Down","Right","Up","Left"][i%4], 0.3)
    if i % 3 == 2:
        tap("A", 0.3)

for i in range(60):
    tap("A", 0.35)

# ============================================================
# PHASE 9: Exit lab and go to Route 101 for encounters
# ============================================================
print("\n[9] To Route 101 for encounters...", flush=True)

# Exit lab
for i in range(10):
    tap("Down", 0.3)
hold("Down", 120)
time.sleep(4)

m = get_map()
print(f"  After lab exit: {pos_str()}", flush=True)

# Move left then north
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
ss("h2r_09_final")

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
        ss(f"h2r_enc_{encounters}")
        time.sleep(1)
        # Run away: Down to RUN, A to confirm
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
