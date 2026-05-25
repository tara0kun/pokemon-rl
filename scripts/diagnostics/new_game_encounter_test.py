"""
Test encounters by starting a brand NEW GAME.
No save manipulation - pure natural gameplay.
This is the definitive test to see if the encounter system works.

Steps:
1. Reset emulator
2. Start New Game
3. Play through intro (name, moving truck, etc.)
4. Play through starter selection
5. Walk to Route 101
6. Test encounters on grass
"""
import requests
import time
import random
import sys

sys.stdout.reconfigure(line_buffering=True)
session = requests.Session()
SB1_PTR = 0x03005AEC
ADDR_BATTLE_FLAGS = 0x02022E90

def rval(base, addr, bits=32):
    ep = {8: 'read8', 16: 'read16', 32: 'read32'}[bits]
    try:
        v = session.get(f'{base}/core/{ep}', params={'address': hex(addr)}, timeout=2).json()
        return int(v['value']) if isinstance(v, dict) else int(v)
    except:
        return None

def tap(base, button, delay=0.3):
    try:
        session.post(f'{base}/mgba-http/button/tap', params={'button': button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

def hold(base, button, frames=30):
    try:
        session.post(f'{base}/mgba-http/button/hold', params={'button': button, 'duration': frames}, timeout=5)
    except:
        pass

def get_pos(base):
    sb1 = rval(base, SB1_PTR, 32)
    if not sb1 or sb1 < 0x02000000:
        return None, None, None, None, sb1
    x = rval(base, sb1, 16)
    y = rval(base, sb1 + 2, 16)
    mg = rval(base, sb1 + 4, 8)
    mn = rval(base, sb1 + 5, 8)
    return x, y, mg, mn, sb1

def spam_a(base, n, delay=0.3, label=""):
    if label:
        print(f"  {label}", flush=True)
    for i in range(n):
        tap(base, "A", delay)
        if i % 20 == 19:
            x, y, mg, mn, _ = get_pos(base)
            print(f"    A x{i+1}: ({x},{y}) Map({mg},{mn})", flush=True)

base = "http://localhost:5000"

print("=" * 60, flush=True)
print("NEW GAME ENCOUNTER TEST", flush=True)
print("=" * 60, flush=True)

# Step 1: Reset emulator
print("\n[1] Resetting emulator...", flush=True)
session.post(f'{base}/coreadapter/reset', timeout=5)
time.sleep(3)

# Step 2: Navigate to NEW GAME
# Title screen -> press Start/A
print("\n[2] Navigating menus...", flush=True)
# Wait for title screen
time.sleep(5)

# Press A to start
for i in range(10):
    tap(base, "A", 1.0)

# We should be at the "NEW GAME / CONTINUE" menu
# If CONTINUE exists (in-game save), need to go DOWN to NEW GAME
# If no save, NEW GAME is already selected
# Press Down to select NEW GAME just in case
tap(base, "Down", 0.5)
tap(base, "A", 1.0)

session.post(f'{base}/core/screenshot', params={'path': 'C:/pokemon-rl/screenshots/ng_menu.png'}, timeout=5)

# Step 3: Professor Birch intro
print("\n[3] Professor Birch intro...", flush=True)
spam_a(base, 60, 0.4, "Advancing Birch dialog...")
session.post(f'{base}/core/screenshot', params={'path': 'C:/pokemon-rl/screenshots/ng_birch.png'}, timeout=5)

# Step 4: Choose gender (Boy)
print("\n[4] Choosing gender...", flush=True)
# Should be on boy/girl selection - just press A for Boy
tap(base, "A", 0.5)
spam_a(base, 20, 0.3, "Confirming...")
session.post(f'{base}/core/screenshot', params={'path': 'C:/pokemon-rl/screenshots/ng_gender.png'}, timeout=5)

# Step 5: Enter name
print("\n[5] Entering name...", flush=True)
# Default name is highlighted, just confirm
# Navigate to "OK" on the name entry screen and press A
spam_a(base, 30, 0.3, "Name entry...")

# There might be more dialog and then rival naming
spam_a(base, 40, 0.3, "More dialog...")
session.post(f'{base}/core/screenshot', params={'path': 'C:/pokemon-rl/screenshots/ng_name.png'}, timeout=5)

# Press A through rival intro
spam_a(base, 60, 0.4, "Rival intro...")
session.post(f'{base}/core/screenshot', params={'path': 'C:/pokemon-rl/screenshots/ng_rival.png'}, timeout=5)

# Step 6: Moving truck scene
print("\n[6] Moving truck scene...", flush=True)
spam_a(base, 30, 0.5, "Moving truck...")
session.post(f'{base}/core/screenshot', params={'path': 'C:/pokemon-rl/screenshots/ng_truck.png'}, timeout=5)

# Need to walk out of truck and go downstairs
# In the truck: press A to exit
spam_a(base, 10, 0.3)

# Check position
x, y, mg, mn, sb1 = get_pos(base)
print(f"  Position: ({x},{y}) Map({mg},{mn})", flush=True)

# Walk around and press A for cutscenes
print("\n[7] House scenes...", flush=True)
for i in range(200):
    if i % 4 == 0:
        tap(base, "A", 0.3)
    elif i % 4 == 1:
        tap(base, "Down", 0.3)
    elif i % 4 == 2:
        tap(base, "A", 0.3)
    else:
        tap(base, random.choice(["Left", "Right", "Up", "Down"]), 0.3)

    if i % 40 == 39:
        x, y, mg, mn, sb1 = get_pos(base)
        print(f"  Step {i+1}: ({x},{y}) Map({mg},{mn})", flush=True)
        session.post(f'{base}/core/screenshot', params={'path': f'C:/pokemon-rl/screenshots/ng_step{i+1:03d}.png'}, timeout=5)

x, y, mg, mn, sb1 = get_pos(base)
print(f"\n  Current: ({x},{y}) Map({mg},{mn})", flush=True)
session.post(f'{base}/core/screenshot', params={'path': 'C:/pokemon-rl/screenshots/ng_current.png'}, timeout=5)
print("\nContinuing play-through... (this takes time)", flush=True)

# Continue pressing A and walking for more progress
for phase in range(5):
    print(f"\n  Phase {phase+1}...", flush=True)
    spam_a(base, 30, 0.3)
    for _ in range(20):
        tap(base, random.choice(["Up", "Down", "Left", "Right"]), 0.3)
    spam_a(base, 20, 0.3)

    x, y, mg, mn, sb1 = get_pos(base)
    print(f"  ({x},{y}) Map({mg},{mn})", flush=True)
    session.post(f'{base}/core/screenshot', params={'path': f'C:/pokemon-rl/screenshots/ng_phase{phase+1}.png'}, timeout=5)

print("\n" + "=" * 60, flush=True)
print("NEW GAME TEST - Final state:", flush=True)
x, y, mg, mn, sb1 = get_pos(base)
print(f"  Position: ({x},{y}) Map({mg},{mn})", flush=True)
print("=" * 60, flush=True)
