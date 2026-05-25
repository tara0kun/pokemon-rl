"""
Fully automated play-through of the early game events.
Resets emulator, loads in-game save, plays through:
1. Exit Birch's Lab
2. Navigate to Route 101 → Oldale Town → Route 103
3. Battle rival on Route 103
4. Return to Littleroot → get Pokedex from Birch
5. Go to Route 101 grass
6. Save state to slot 1

Uses map number changes to detect transitions, not coordinates.
"""
import requests
import time
import random
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

def tap(base, button, delay=0.35):
    try:
        session.post(f'{base}/mgba-http/button/tap', params={'button': button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

def get_state(base):
    sb1 = rval(base, SB1_PTR, 32)
    if not sb1 or sb1 < 0x02000000:
        return {'sb1': 0, 'x': 0, 'y': 0, 'mg': 0, 'mn': 0}
    return {
        'sb1': sb1,
        'x': rval(base, sb1, 16) or 0,
        'y': rval(base, sb1 + 2, 16) or 0,
        'mg': rval(base, sb1 + 4, 8) or 0,
        'mn': rval(base, sb1 + 5, 8) or 0,
    }

def in_battle(base):
    btl = rval(base, ADDR_BATTLE_FLAGS, 32)
    return btl is not None and btl > 0

def walk_direction(base, direction, steps, a_interval=10, show_progress=True):
    """Walk in a direction for N steps, pressing A periodically for dialogs."""
    st_start = get_state(base)
    for i in range(steps):
        if i % a_interval == a_interval - 1:
            tap(base, "A", 0.2)
        else:
            tap(base, direction, 0.3)

        if show_progress and i % 20 == 19:
            st = get_state(base)
            print(f"    step {i+1}: ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

        # Check for map change
        st = get_state(base)
        if st['mn'] != st_start['mn'] or st['mg'] != st_start['mg']:
            print(f"    Map changed at step {i+1}: ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)
            return st, True

        # Check for battle
        if in_battle(base):
            return get_state(base), 'battle'

    return get_state(base), False

def spam_a(base, n, delay=0.25):
    """Press A n times to advance dialog."""
    for i in range(n):
        tap(base, "A", delay)

def walk_until_map(base, direction, target_mn, max_steps=200, a_interval=8):
    """Walk in a direction until reaching a specific map number."""
    for i in range(max_steps):
        if i % a_interval == a_interval - 1:
            tap(base, "A", 0.2)
        else:
            tap(base, direction, 0.3)

        st = get_state(base)
        if st['mn'] == target_mn:
            print(f"    Reached Map({st['mg']},{st['mn']}) at ({st['x']},{st['y']}) step {i+1}", flush=True)
            return st, True

        if in_battle(base):
            return get_state(base), 'battle'

        if i % 30 == 29:
            print(f"    step {i+1}: ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

    st = get_state(base)
    print(f"    Timeout: ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)
    return st, False

def fight_battle(base, max_presses=300):
    """Fight a battle by pressing A to attack. Return when battle ends."""
    print("  [Battle] Fighting...", flush=True)
    for i in range(max_presses):
        tap(base, "A", 0.25)
        if not in_battle(base):
            print(f"  [Battle] Ended at press {i+1}", flush=True)
            # Advance post-battle text
            spam_a(base, 20, 0.25)
            return True
        if i % 50 == 49:
            print(f"  [Battle] Still fighting (press {i+1})...", flush=True)
    print("  [Battle] Timeout!", flush=True)
    return False

def run_from_battle(base):
    """Run away from a wild battle."""
    print("  [Battle] Running away...", flush=True)
    time.sleep(0.5)
    for _ in range(3):
        tap(base, "B", 0.2)
    tap(base, "Down", 0.3)
    tap(base, "Right", 0.3)
    tap(base, "A", 0.5)
    time.sleep(1)
    spam_a(base, 15, 0.25)


def play_through(port):
    base = f"http://localhost:{port}"
    print(f"\n{'='*60}", flush=True)
    print(f"AUTO PLAY-THROUGH on port {port}", flush=True)
    print(f"{'='*60}", flush=True)

    # Step 1: Reset and load in-game save
    print("\n[1] Reset + load in-game save...", flush=True)
    session.post(f'{base}/coreadapter/reset', timeout=5)
    time.sleep(2)

    # Press A through title screen
    for i in range(60):
        tap(base, "A", 0.4)
        st = get_state(base)
        if st['sb1'] > 0x02000000 and st['x'] > 0 and i > 10:
            break
    time.sleep(0.5)

    st = get_state(base)
    print(f"  Loaded: ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

    # Step 2: Exit Birch's Lab
    # Player is in Birch's Lab Map(1,2). After getting starter, there's dialog.
    # Need LOTS of A presses to advance through dialog, then walk south to door.
    print("\n[2] Exiting Birch's Lab...", flush=True)

    # The post-starter dialog can be VERY long. Birch talks, assistant talks, etc.
    # Strategy: walk to door (y=8+), then massive A spam to advance all dialog.
    exited = False

    # First: walk south to door area
    for _ in range(15):
        tap(base, "Down", 0.3)

    # Now press A a LOT (100+ times) to advance through all dialog
    for phase in range(20):
        # 50 A presses per phase
        for i in range(50):
            tap(base, "A", 0.15)
            if i % 25 == 24:
                st = get_state(base)
                if st['mn'] != 2 or st['mg'] != 1:
                    print(f"  Exited lab during A spam phase {phase}!", flush=True)
                    exited = True
                    break
        if exited:
            break

        # Try walking through the door
        for i in range(10):
            tap(base, "Down", 0.3)
            st = get_state(base)
            if st['mn'] != 2 or st['mg'] != 1:
                print(f"  Exited lab (walk phase {phase})!", flush=True)
                exited = True
                break
        if exited:
            break

        st = get_state(base)
        if phase % 3 == 2:
            print(f"  Phase {phase}: ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

    st = get_state(base)
    print(f"  Position: ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

    # Step 3: Navigate north through Littleroot → Route 101
    print("\n[3] Heading north to Route 101...", flush=True)

    # If still in lab, keep trying to exit
    st = get_state(base)
    if st['mg'] == 1 and st['mn'] == 2:
        print("  Still in lab! More A + Down...", flush=True)
        for _ in range(20):
            spam_a(base, 10, 0.2)
            for _ in range(10):
                tap(base, "Down", 0.3)
            st = get_state(base)
            if st['mn'] != 2 or st['mg'] != 1:
                break

    # Now in Littleroot Town. Route 101 exit is at north edge, x=10-11
    # First walk right to center, then try multiple x positions going north
    st = get_state(base)
    print(f"  Starting from ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

    if st['mn'] == 9:  # Littleroot Town
        # Try different x positions to find the exit
        for target_x in [10, 11, 9, 12, 8]:
            # Move to target x
            cx = rval(base, st['sb1'], 16) or 0
            while cx < target_x:
                tap(base, "Right", 0.25)
                cx = rval(base, st['sb1'], 16) or cx
            while cx > target_x:
                tap(base, "Left", 0.25)
                cx = rval(base, st['sb1'], 16) or cx

            # Walk north with A for dialogs (Mom's Running Shoes event)
            for j in range(30):
                tap(base, "Up", 0.3)
                if j % 4 == 3:
                    tap(base, "A", 0.2)
                st = get_state(base)
                if st['mn'] == 16:  # Route 101!
                    print(f"  Reached Route 101 via x={target_x}!", flush=True)
                    break
            if st['mn'] == 16:
                break
            print(f"    x={target_x}: stuck at ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

    # Handle Running Shoes event (Mom dialog at exit)
    st = get_state(base)
    if st['mn'] == 9:
        print("  Mom's Running Shoes event? Pressing A a lot...", flush=True)
        spam_a(base, 50, 0.2)
        # Try again
        for _ in range(20):
            tap(base, "Up", 0.3)
            if _ % 3 == 2:
                tap(base, "A", 0.2)
        st = get_state(base)

    print(f"  Now at ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

    # Continue north through Route 101 to Oldale Town (Map 0,10)
    print("\n[4] Walking through Route 101 to Oldale Town...", flush=True)
    retries = 0
    while st['mn'] != 10 and retries < 3:
        st, result = walk_until_map(base, "Up", target_mn=10, max_steps=150, a_interval=6)
        if result == 'battle':
            # Random wild encounter - run away
            run_from_battle(base)
            st = get_state(base)
            retries += 1
        elif not result:
            # Might be stuck, try left/right
            d = random.choice(["Left", "Right"])
            for _ in range(5):
                tap(base, d, 0.25)
            retries += 1

    print(f"  Now at ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

    if st['mn'] != 10:
        print(f"  WARNING: Not in Oldale Town. Current map: ({st['mg']},{st['mn']})", flush=True)
        # Continue anyway, maybe at Route 103 already

    # Step 4: Walk east from Oldale to Route 103 (Map 0,18)
    print("\n[5] Heading east to Route 103...", flush=True)
    st, result = walk_until_map(base, "Right", target_mn=18, max_steps=100, a_interval=8)
    if result == 'battle':
        run_from_battle(base)
        st = get_state(base)

    if st['mn'] != 18:
        # Try north then east (Route 103 might require going north first)
        for _ in range(20):
            tap(base, "Up", 0.3)
        st, result = walk_until_map(base, "Right", target_mn=18, max_steps=80, a_interval=8)
        if result == 'battle':
            run_from_battle(base)
            st = get_state(base)

    print(f"  Now at ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

    # Step 5: Find and battle rival on Route 103
    print("\n[6] Looking for rival on Route 103...", flush=True)
    battle_won = False

    # Walk around and press A to trigger rival
    for attempt in range(200):
        # Walk in patterns
        dirs = ["Right", "Up", "Left", "Up", "Right", "Right", "Up", "Up"]
        tap(base, dirs[attempt % len(dirs)], 0.3)

        if attempt % 3 == 2:
            tap(base, "A", 0.2)

        if in_battle(base):
            print(f"  Rival battle started at attempt {attempt}!", flush=True)
            battle_won = fight_battle(base)
            break

    if not battle_won and not in_battle(base):
        print("  Rival not found. Walking more...", flush=True)
        for attempt in range(100):
            d = random.choice(["Up", "Down", "Left", "Right"])
            tap(base, d, 0.3)
            tap(base, "A", 0.2)
            if in_battle(base):
                battle_won = fight_battle(base)
                break

    st = get_state(base)
    print(f"  After rival: ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

    # Step 6: Return to Littleroot (south through Route 103 → Oldale → Route 101 → Littleroot)
    print("\n[7] Returning to Littleroot Town...", flush=True)

    # Walk south/left to Oldale
    retries = 0
    while st['mn'] != 10 and retries < 5:
        if st['mn'] == 18:  # Route 103
            st, result = walk_until_map(base, "Left", target_mn=10, max_steps=100)
            if result == 'battle':
                run_from_battle(base)
                st = get_state(base)
                retries += 1
        else:
            st, result = walk_until_map(base, "Down", target_mn=10, max_steps=60)
            if result == 'battle':
                run_from_battle(base)
                st = get_state(base)
                retries += 1
            if not result:
                retries += 1

    # From Oldale south to Route 101
    if st['mn'] == 10:
        print(f"  In Oldale. Going south to Route 101...", flush=True)
        retries = 0
        while st['mn'] != 16 and retries < 3:
            st, result = walk_until_map(base, "Down", target_mn=16, max_steps=60)
            if result == 'battle':
                run_from_battle(base)
                st = get_state(base)
                retries += 1

    # Through Route 101 to Littleroot
    if st['mn'] == 16:
        print(f"  On Route 101. Going south to Littleroot...", flush=True)
        retries = 0
        while st['mn'] != 9 and retries < 5:
            st, result = walk_until_map(base, "Down", target_mn=9, max_steps=150)
            if result == 'battle':
                run_from_battle(base)
                st = get_state(base)
                retries += 1

    print(f"  Now at ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

    # Step 7: Enter Birch's Lab (Map 1,2) and talk to Birch for Pokedex
    print("\n[8] Entering Birch's Lab for Pokedex...", flush=True)
    if st['mn'] == 9:  # In Littleroot
        # Birch's lab entrance is on the left side of town
        # Walk down and left to find it
        for _ in range(10):
            tap(base, "Down", 0.3)
        for _ in range(10):
            tap(base, "Left", 0.3)

        # Walk up to find lab entrance
        for i in range(40):
            tap(base, "Up", 0.3)
            if i % 4 == 3:
                tap(base, "A", 0.2)
            st = get_state(base)
            if st['mn'] == 2 and st['mg'] == 1:
                print(f"  Entered lab!", flush=True)
                break

        if not (st['mn'] == 2 and st['mg'] == 1):
            # Try right side
            for _ in range(15):
                tap(base, "Right", 0.3)
            for i in range(30):
                tap(base, "Up", 0.3)
                if i % 4 == 3:
                    tap(base, "A", 0.2)
                st = get_state(base)
                if st['mn'] == 2 and st['mg'] == 1:
                    print(f"  Entered lab!", flush=True)
                    break

    # In the lab: walk up to Birch and press A for Pokedex
    if st['mn'] == 2 and st['mg'] == 1:
        for _ in range(10):
            tap(base, "Up", 0.3)
        spam_a(base, 80, 0.25)  # Long dialog for Pokedex
        print("  Pokedex dialog completed.", flush=True)
    else:
        print(f"  Not in lab! At Map({st['mg']},{st['mn']})", flush=True)

    st = get_state(base)
    print(f"  After Pokedex: ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

    # Step 8: Exit lab and go to Route 101 grass
    print("\n[9] Going to Route 101 grass...", flush=True)
    # Exit lab (walk south)
    for _ in range(20):
        tap(base, "Down", 0.3)

    st = get_state(base)
    # Walk north to Route 101
    if st['mn'] == 9:  # Littleroot
        for _ in range(10):
            tap(base, "Right", 0.3)
        retries = 0
        while st['mn'] != 16 and retries < 3:
            st, result = walk_until_map(base, "Up", target_mn=16, max_steps=60, a_interval=5)
            if result == 'battle':
                run_from_battle(base)
                st = get_state(base)
                retries += 1

    print(f"  Now at ({st['x']},{st['y']}) Map({st['mg']},{st['mn']})", flush=True)

    # Check if Pokedex was obtained
    sb1 = rval(base, SB1_PTR, 32)
    flags_base = sb1 + 0x1270
    pokedex = (rval(base, flags_base + 268, 8) >> 1) & 1
    print(f"  FLAG_SYS_POKEDEX_GET = {pokedex}", flush=True)

    # Save state
    print("\n[10] Saving state...", flush=True)
    session.post(f'{base}/core/savestateslot', params={'slot': 1}, timeout=5)
    session.post(f'{base}/core/savestateslot', params={'slot': 3}, timeout=5)
    print(f"  Saved to slots 1 and 3!", flush=True)

    return st['mn'] == 16 and pokedex == 1

# Run on all 3 ports
results = {}
for port in [5000, 5001, 5002]:
    try:
        success = play_through(port)
        results[port] = success
        print(f"\nPort {port}: {'SUCCESS' if success else 'PARTIAL'}", flush=True)
    except Exception as e:
        print(f"\nPort {port}: ERROR - {e}", flush=True)
        results[port] = False

print(f"\n{'='*60}", flush=True)
print(f"FINAL RESULTS", flush=True)
print(f"{'='*60}", flush=True)
for port, success in results.items():
    print(f"  Port {port}: {'SUCCESS' if success else 'NEEDS WORK'}", flush=True)
