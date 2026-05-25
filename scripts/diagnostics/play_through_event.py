"""
Play through the Birch Lab event NATURALLY using button presses.
DO NOT modify any flags - let the game handle everything.

Game state: Player is in Birch's Lab at (5,2), just got starter Pokemon.
Need to:
1. Talk to Birch / advance dialog (A presses)
2. Walk out of lab (Down, then out the door)
3. Mom gives Running Shoes on way north
4. Walk north to Route 101
5. Walk north through Route 101 to Oldale Town
6. Walk east to Route 103
7. Battle rival on Route 103
8. Walk back south to Littleroot Town
9. Talk to Birch, receive Pokedex
10. NOW save state on Route 101
"""
import requests
import time
import sys

session = requests.Session()
sys.stdout.reconfigure(line_buffering=True)

SB1_PTR = 0x03005AEC
ADDR_BATTLE_FLAGS = 0x02022E90

def rval(base, addr, bits=32):
    ep = {8: 'read8', 16: 'read16', 32: 'read32'}[bits]
    try:
        v = session.get(f'{base}/core/{ep}', params={'address': hex(addr)}, timeout=2).json()
        return int(v['value']) if isinstance(v, dict) else int(v)
    except:
        return None

def tap(base, button, delay=0.4):
    try:
        session.post(f'{base}/mgba-http/button/tap', params={'button': button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

def get_pos(base):
    sb1 = rval(base, SB1_PTR, 32)
    if not sb1 or sb1 < 0x02000000:
        return None, None, None, None, sb1
    x = rval(base, sb1, 16)
    y = rval(base, sb1 + 2, 16)
    mg = rval(base, sb1 + 4, 8)
    mn = rval(base, sb1 + 5, 8)
    return x, y, mg, mn, sb1

def print_pos(base, label=""):
    x, y, mg, mn, sb1 = get_pos(base)
    print(f"  {label}({x},{y}) Map({mg},{mn})", flush=True)
    return x, y, mg, mn

def spam_a(base, n, delay=0.3, label=""):
    """Press A n times to advance dialog"""
    if label:
        print(f"  {label}", flush=True)
    for i in range(n):
        tap(base, "A", delay)
        if i % 20 == 19:
            x, y, mg, mn = print_pos(base, f"  A x{i+1}: ")

def walk(base, direction, n, delay=0.35, label=""):
    """Walk in a direction n times"""
    if label:
        print(f"  {label}", flush=True)
    for i in range(n):
        tap(base, direction, delay)

base = "http://localhost:5000"

print(f"{'='*60}", flush=True)
print(f"PLAY THROUGH EVENT NATURALLY", flush=True)
print(f"{'='*60}", flush=True)

# Step 1: Reset and load in-game save
print("\n[1] Resetting and loading in-game save...", flush=True)
session.post(f'{base}/coreadapter/reset', timeout=5)
time.sleep(2)

# Press A to get past title screen (need to wait for intro)
for i in range(50):
    tap(base, "A", 0.5)
    x, y, mg, mn, sb1 = get_pos(base)
    if sb1 and sb1 > 0x02000000 and x and x > 0 and y and y > 0:
        if i > 8:  # Give enough time for game to fully load
            break

print_pos(base, "In-game save loaded: ")

# Step 2: We're in Birch's Lab. Press A to advance any dialog
print("\n[2] Advancing dialog in Birch's Lab...", flush=True)
spam_a(base, 40, 0.3, "Pressing A to clear dialog...")
print_pos(base, "After dialog: ")

# Step 3: Walk out of the lab (south/down then through door)
print("\n[3] Walking out of lab...", flush=True)
walk(base, "Down", 10, 0.35, "Walking south...")
print_pos(base, "After walking down: ")

# Press A to handle any NPC dialog triggered
spam_a(base, 10, 0.3)
print_pos(base, "After A: ")

# Continue walking down and out
walk(base, "Down", 10, 0.35)
print_pos(base, "More south: ")

# Step 4: We should be outside now. Walk north
print("\n[4] Walking north toward Route 101...", flush=True)
for i in range(50):
    tap(base, "Up", 0.3)
    # Occasionally press A for dialogs (Mom's running shoes)
    if i % 8 == 7:
        tap(base, "A", 0.2)
    if i % 15 == 14:
        print_pos(base, f"  Step {i}: ")

print_pos(base, "After north walk: ")

# Step 5: Keep going north through Route 101 to Oldale Town
print("\n[5] Continuing north to Oldale Town...", flush=True)
for i in range(80):
    tap(base, "Up", 0.3)
    if i % 8 == 7:
        tap(base, "A", 0.2)
    if i % 20 == 19:
        print_pos(base, f"  Step {i}: ")

print_pos(base, "After long north walk: ")

# Step 6: Walk east to Route 103
print("\n[6] Walking east to Route 103...", flush=True)
# First need to reach Oldale Town exit east
for i in range(30):
    tap(base, "Right", 0.3)
    if i % 8 == 7:
        tap(base, "A", 0.2)
    if i % 10 == 9:
        print_pos(base, f"  Step {i}: ")

# Keep going east and slightly north
for i in range(40):
    if i % 3 < 2:
        tap(base, "Right", 0.3)
    else:
        tap(base, "Up", 0.3)
    if i % 8 == 7:
        tap(base, "A", 0.2)
    if i % 10 == 9:
        print_pos(base, f"  Step {i}: ")

# Walk north on Route 103 to find rival
for i in range(30):
    tap(base, "Up", 0.3)
    if i % 8 == 7:
        tap(base, "A", 0.2)

print_pos(base, "At Route 103: ")

# Step 7: Battle rival - press A to talk, then battle
print("\n[7] Looking for rival battle...", flush=True)
# Walk around and press A to find rival
for i in range(60):
    dirs = ["Up", "Right", "Left", "Up"]
    tap(base, dirs[i % len(dirs)], 0.3)
    tap(base, "A", 0.2)

    btl = rval(base, ADDR_BATTLE_FLAGS, 32)
    if btl and btl > 0:
        print(f"  *** BATTLE STARTED! btl=0x{btl:08X} ***", flush=True)
        break

# In battle: press A repeatedly to attack (move 1)
print("\n[8] Fighting rival...", flush=True)
for i in range(200):
    tap(base, "A", 0.25)
    btl = rval(base, ADDR_BATTLE_FLAGS, 32)
    if btl == 0 or btl is None:
        print(f"  Battle ended at press {i}", flush=True)
        break
    if i % 30 == 29:
        print(f"  Still in battle (press {i})...", flush=True)

# Post-battle: advance dialog
spam_a(base, 30, 0.3, "Post-battle dialog...")
print_pos(base, "After rival battle: ")

# Step 8: Walk back south to Littleroot Town
print("\n[9] Walking back to Littleroot...", flush=True)
for i in range(120):
    if i % 3 < 2:
        tap(base, "Down", 0.3)
    else:
        tap(base, "Left", 0.3)
    if i % 8 == 7:
        tap(base, "A", 0.2)
    if i % 30 == 29:
        print_pos(base, f"  Step {i}: ")

# Step 9: Enter Birch's Lab and talk to him
print("\n[10] Entering Birch's Lab for Pokedex...", flush=True)
# Walk around town to find lab
for i in range(40):
    dirs = ["Down", "Right", "Up", "Left"]
    tap(base, dirs[i % len(dirs)], 0.3)
    if i % 8 == 7:
        tap(base, "A", 0.3)

print_pos(base, "Looking for lab: ")

# Press A a lot to handle Pokedex dialog
spam_a(base, 60, 0.3, "Getting Pokedex...")

# Step 10: Walk to Route 101 and save
print("\n[11] Walking to Route 101...", flush=True)
for i in range(40):
    tap(base, "Up", 0.3)
    if i % 8 == 7:
        tap(base, "A", 0.2)

x, y, mg, mn = print_pos(base, "Final position: ")

# Check if we have Pokedex
sb1 = rval(base, SB1_PTR, 32)
flags_base = sb1 + 0x1270
pokedex = (rval(base, flags_base + 268, 8) >> 1) & 1  # FLAG_SYS_POKEDEX_GET bit 1
pokemon = (rval(base, flags_base + 268, 8) >> 0) & 1  # FLAG_SYS_POKEMON_GET bit 0
print(f"  FLAG_SYS_POKEMON_GET={pokemon}, FLAG_SYS_POKEDEX_GET={pokedex}", flush=True)

# Save state
print("\nSaving state to slot 1...", flush=True)
session.post(f'{base}/core/savestateslot', params={'slot': 1}, timeout=5)
session.post(f'{base}/core/savestateslot', params={'slot': 3}, timeout=5)
print("Done!", flush=True)
