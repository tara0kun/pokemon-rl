"""
Bootstrap v6: Direct coordinate navigation to Route 101.
Known exits from Map(0,9): (18,26) via Up, (10,21) via Left.
"""
import requests
import time
import random
import sys

MGBA_URL = "http://localhost:5000"
PYTHON = r"C:\pokemon-rl\poke-rl\Scripts\python.exe"
session = requests.Session()


def read_val(ep, addr):
    for _ in range(3):
        try:
            v = session.get(f"{MGBA_URL}/core/{ep}", params={"address": hex(addr)}, timeout=5).json()
            return int(v["value"]) if isinstance(v, dict) else int(v)
        except:
            time.sleep(0.3)
    return 0


def write_val(ep, addr, value):
    try:
        session.post(f"{MGBA_URL}/core/{ep}", params={"address": hex(addr), "value": value}, timeout=3)
    except:
        pass


def tap(button, delay=0.06):
    try:
        session.post(f"{MGBA_URL}/mgba-http/button/tap", params={"button": button}, timeout=3)
    except:
        pass
    time.sleep(delay)


def get_pos():
    return read_val("read16", 0x02037000), read_val("read16", 0x02037002)


def get_map():
    sb1 = read_val("read32", 0x03005AEC)
    if not sb1 or sb1 < 0x02000000 or sb1 > 0x0203FFFF:
        return -1, -1
    return read_val("read8", sb1 + 4), read_val("read8", sb1 + 5)


def save_slot(slot):
    session.post(f"{MGBA_URL}/core/savestateslot", params={"slot": slot}, timeout=5)


def load_slot(slot):
    session.post(f"{MGBA_URL}/core/loadstateslot", params={"slot": slot}, timeout=5)
    time.sleep(0.5)


def screenshot(name="screenshot"):
    path = f"C:\\pokemon-rl\\{name}.png"
    session.post(f"{MGBA_URL}/core/screenshot", params={"path": path}, timeout=5)
    return path


def set_flag(sb1, flag_num):
    byte_idx = flag_num // 8
    bit_idx = flag_num % 8
    addr = sb1 + 0x1270 + byte_idx
    cur = read_val("read8", addr)
    write_val("write8", addr, cur | (1 << bit_idx))


def set_var(sb1, var_num, value):
    index = var_num - 0x4000
    addr = sb1 + 0x139C + (index * 2)
    write_val("write16", addr, value)


def set_minimal_flags(sb1):
    """Set flags that enable Route 101 wild encounters."""
    set_flag(sb1, 0x860)  # FLAG_SYS_POKEMON_GET (US)
    set_flag(sb1, 0x861)  # FLAG_SYS_POKEDEX_GET (US)
    set_flag(sb1, 0x800)  # JP fallback
    set_flag(sb1, 0x801)  # JP fallback
    set_flag(sb1, 0x52)   # FLAG_RESCUED_BIRCH
    set_flag(sb1, 0x2BC)  # HIDE bag NPC
    set_flag(sb1, 0x2D0)  # HIDE rescue NPC
    set_flag(sb1, 0x2EE)  # HIDE Zigzagoon
    set_var(sb1, 0x4060, 3)  # VAR_ROUTE101_STATE = rescue complete
    set_var(sb1, 0x4049, 3)  # VAR_BIRCH_STATE
    set_var(sb1, 0x4050, 6)  # VAR_LITTLEROOT_TOWN_STATE
    set_var(sb1, 0x4084, 4)  # VAR_BIRCH_LAB_STATE


def navigate_to(target_x, target_y, max_steps=500):
    """Navigate to specific coordinates on the current map."""
    for step in range(max_steps):
        cx, cy = get_pos()
        dx = target_x - cx
        dy = target_y - cy

        if abs(dx) <= 1 and abs(dy) <= 1:
            return True

        # Move toward target
        if abs(dx) > abs(dy):
            tap("Right" if dx > 0 else "Left", 0.04)
        else:
            tap("Up" if dy < 0 else "Down", 0.04)

        # Check if stuck
        if step % 30 == 29:
            new_x, new_y = get_pos()
            if (new_x, new_y) == (cx, cy):
                # Try random direction to unstick
                tap(random.choice(["Up", "Down", "Left", "Right"]), 0.04)
                tap("A", 0.06)
                tap("B", 0.06)

    return False


def dismiss_dialogue(presses=50):
    """Press A/B to dismiss any dialogue or cutscene."""
    for _ in range(presses):
        tap("A", 0.08)
        tap("B", 0.08)


print("=" * 60)
print("  Bootstrap v6 - Direct navigation")
print("=" * 60)

x, y = get_pos()
mg, mn = get_map()
print(f"\n[1] Current: ({x},{y}) Map({mg},{mn})")

# Set flags
sb1 = read_val("read32", 0x03005AEC)
set_minimal_flags(sb1)
print("[2] Minimal flags set")

# Dismiss any dialogue
print("[3] Clearing dialogue...")
dismiss_dialogue(30)

# Step 1: Get outdoors if in a building
mg, mn = get_map()
if mg != 0:
    print(f"[4] In building Map({mg},{mn}), exiting...")
    # Try going down first (most exits are south)
    for _ in range(50):
        tap("Down", 0.04)
        mg, mn = get_map()
        if mg == 0:
            break
    # If still inside, try all directions
    if mg != 0:
        for _ in range(500):
            tap(random.choice(["Up", "Down", "Left", "Right"]), 0.04)
            mg, mn = get_map()
            if mg == 0:
                break
    x, y = get_pos()
    print(f"    Now at ({x},{y}) Map({mg},{mn})")
    dismiss_dialogue(20)

# Step 2: Navigate to Route 101 entrance
# Known exits from Map(0,9): Up at y~0 (north edge) leads to Route 101
mg, mn = get_map()
x, y = get_pos()
print(f"\n[5] On Map({mg},{mn}) at ({x},{y})")

if (mg, mn) == (0, 16):
    print("    Already on Route 101!")
elif mg == 0:
    # Try exit 1: Go north (Route 101 is north of Littleroot)
    print("    Navigating north to Route 101...")

    # First go to center-ish x position and head north
    for attempt in range(3):
        # Move north aggressively
        for i in range(200):
            # Bias strongly upward with some left/right to avoid obstacles
            r = random.random()
            if r < 0.6:
                tap("Up", 0.03)
            elif r < 0.75:
                tap("Left", 0.03)
            elif r < 0.90:
                tap("Right", 0.03)
            else:
                tap("Down", 0.03)

            mg, mn = get_map()
            if (mg, mn) == (0, 16):
                x, y = get_pos()
                print(f"    Entered Route 101 at ({x},{y})!")
                break
            # If entered a building, exit immediately
            if mg != 0:
                for _ in range(30):
                    tap("Down", 0.04)
                    if get_map()[0] == 0:
                        break

        if (mg, mn) == (0, 16):
            break

        # If not found, try from different x positions
        x, y = get_pos()
        print(f"    Attempt {attempt+1}: at ({x},{y}) Map({mg},{mn}), retrying...")
        dismiss_dialogue(10)

mg, mn = get_map()
if (mg, mn) != (0, 16):
    # Last resort: scan left edge (known exit at x=10, y=21)
    print("    Trying left edge exit...")
    navigate_to(10, 21)
    for _ in range(20):
        tap("Left", 0.05)
        mg, mn = get_map()
        if (mg, mn) == (0, 16):
            x, y = get_pos()
            print(f"    Entered Route 101 via left edge at ({x},{y})!")
            break

mg, mn = get_map()
if (mg, mn) != (0, 16):
    x, y = get_pos()
    print(f"\n    FAILED to reach Route 101. At ({x},{y}) Map({mg},{mn})")
    screenshot("failed_nav")
    sys.exit(1)

# Step 3: On Route 101! Set flags again (in case map transition cleared them)
sb1 = read_val("read32", 0x03005AEC)
set_minimal_flags(sb1)
dismiss_dialogue(20)

# Save safe point on Route 101
save_slot(8)
x, y = get_pos()
print(f"\n[6] On Route 101 at ({x},{y}). Saved slot 8.")
screenshot("route101_start")

# Step 4: Walk around on Route 101 to trigger wild encounters
print("\n[7] Walking on Route 101 to find wild encounters...")
last_pos = get_pos()
static_count = 0
battle_count = 0
total_steps = 0

for i in range(15000):
    total_steps += 1

    # Walk pattern: up/down on grass with some horizontal variation
    cycle = i % 20
    if cycle < 8:
        tap("Up", 0.025)
    elif cycle < 14:
        tap("Down", 0.025)
    elif cycle < 16:
        tap("Left", 0.025)
    elif cycle < 18:
        tap("Right", 0.025)
    elif cycle < 19:
        tap("A", 0.025)
    else:
        tap("B", 0.025)

    cur_pos = get_pos()

    if cur_pos == last_pos:
        static_count += 1
    else:
        static_count = 0
        last_pos = cur_pos

    # Battle detection: position static for 15+ steps
    if static_count >= 15:
        screenshot(f"battle_{i}")
        print(f"\n    [{i}] Static at {cur_pos} for {static_count} steps - likely battle")

        # A-spam to fight through the battle
        pre_hp = read_val("read16", 0x020241E6)
        for j in range(300):
            tap("A", 0.05)
            if j % 60 == 59:
                tap("B", 0.05)

        new_pos = get_pos()
        new_map = get_map()
        post_hp = read_val("read16", 0x020241E6)
        hp_max = read_val("read16", 0x020241E8)
        level = read_val("read8", 0x020241E4)

        if new_pos != cur_pos or post_hp != pre_hp:
            battle_count += 1
            print(f"    Battle #{battle_count}! HP:{post_hp}/{hp_max} Lv{level}")
            print(f"    Now at {new_pos} Map{new_map}")

            if battle_count >= 2:
                print(f"\n[8] {battle_count} battles confirmed! Saving...")
                for slot in [1, 2, 3]:
                    save_slot(slot)
                screenshot("final_success")
                print(f"    Saved slots 1,2,3. HP:{post_hp}/{hp_max} Lv{level}")
                print("\n    BOOTSTRAP COMPLETE!")
                sys.exit(0)
        else:
            print(f"    Still at {cur_pos}, trying more...")
            # Extra aggressive A/B
            for _ in range(150):
                tap("A", 0.06)
                tap("B", 0.06)

            # Check if game is responsive
            tap("Down", 0.1)
            test_pos = get_pos()
            tap("Up", 0.1)
            test_pos2 = get_pos()
            if test_pos == test_pos2 == cur_pos:
                print("    Game frozen, loading slot 8...")
                load_slot(8)
                sb1 = read_val("read32", 0x03005AEC)
                set_minimal_flags(sb1)
                dismiss_dialogue(20)

        static_count = 0

    # Re-enter Route 101 if we left
    if i % 50 == 49:
        mg, mn = get_map()
        if (mg, mn) != (0, 16):
            if mg != 0:
                # In a building, exit
                for _ in range(30):
                    tap("Down", 0.04)
                    if get_map()[0] == 0:
                        break
            # Try to get back to Route 101
            for _ in range(100):
                tap("Up", 0.03)
                if get_map() == (0, 16):
                    break

    if i % 2000 == 1999:
        x, y = get_pos()
        mg, mn = get_map()
        hp = read_val("read16", 0x020241E6)
        hp_max = read_val("read16", 0x020241E8)
        level = read_val("read8", 0x020241E4)
        screenshot(f"progress_{i+1}")
        print(f"    [{i+1}] ({x},{y}) Map({mg},{mn}) HP:{hp}/{hp_max} Lv{level} battles={battle_count}")

print(f"\n    Finished {total_steps} steps, {battle_count} battles")
screenshot("final_state")
if battle_count > 0:
    print("    Saving final state...")
    for slot in [1, 2, 3]:
        save_slot(slot)
    print("    BOOTSTRAP COMPLETE!")
else:
    print("    No battles detected. Check screenshots.")
