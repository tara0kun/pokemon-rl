"""
自動でクリーンなセーブステートを作成する
1. ゲームをリセット
2. タイトル画面から「つづきから」
3. Route 101の草むらまで移動
4. セーブステート作成
"""
import requests
import time
import random

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

def tap(button, delay=0.5):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/tap", params={"button": button}, timeout=0.5)
    except:
        pass
    time.sleep(delay)

def hold(button):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/hold", params={"button": button}, timeout=0.5)
    except:
        pass

def release_all():
    try:
        session.post(f"{BASE_URL}/mgba-http/button/releaseall", timeout=0.5)
    except:
        pass

WILD_DISABLED_ADDR = 0x020397E4
SB1_PTR = 0x03005AEC

def get_state():
    sb1 = read32(SB1_PTR)
    if not sb1 or sb1 < 0x02000000:
        return {"sb1": None, "x": 0, "y": 0, "mg": 0, "mn": 0, "wild": None}
    x = read16(sb1)
    y = read16(sb1 + 2)
    mg = read8(sb1 + 4)
    mn = read8(sb1 + 5)
    wild = read8(WILD_DISABLED_ADDR)
    return {"sb1": sb1, "x": x, "y": y, "mg": mg, "mn": mn, "wild": wild}

print("=" * 60)
print("AUTO CLEAN STATE CREATOR")
print("=" * 60)

# === Step 1: Try /core/reset to reset the emulator ===
print("\nStep 1: Attempting core reset...")
endpoints = ["/core/reset", "/mgba-http/emu/reset"]
for ep in endpoints:
    try:
        r = session.post(f"{BASE_URL}{ep}", timeout=5)
        print(f"  {ep}: status={r.status_code} resp={r.text[:100]}")
    except Exception as e:
        print(f"  {ep}: {e}")

# Wait for reset
time.sleep(2)

# Check if game reset (SB1 might be invalid during title screen)
sb1 = read32(SB1_PTR)
print(f"  After reset: SB1=0x{sb1:08X}" if sb1 else "  After reset: SB1=None")

# === Step 2: Alternative - use A+B+Start+Select combo via tap ===
print("\nStep 2: Trying soft reset combo...")
# Press all 4 buttons simultaneously using rapid holds
try:
    hold("A")
    hold("B")
    hold("Start")
    hold("Select")
    time.sleep(3)  # Hold for 3 seconds
    release_all()
    time.sleep(3)  # Wait for reset
except Exception as e:
    print(f"  Soft reset error: {e}")

# Check state
sb1 = read32(SB1_PTR)
wild = read8(WILD_DISABLED_ADDR)
print(f"  After soft reset: SB1=0x{sb1:08X}, wild={wild}" if sb1 else f"  SB1=None, wild={wild}")

# === Step 3: Navigate title screen ===
print("\nStep 3: Navigating title screen...")

# The title screen needs several A presses:
# 1. Press A/Start to skip intro (Game Freak logo, etc)
# 2. Press A on title screen
# 3. Select "つづきから" (Continue) - should be the first option
# 4. Press A to confirm

# Wait and press Start/A repeatedly
for i in range(10):
    tap("Start", 0.8)  # Skip intro animations
    s = get_state()
    if s['sb1'] and s['wild'] is not None and s['wild'] != 1:
        print(f"  Game state changed! wild={s['wild']}")

# Press A to advance through title
for i in range(20):
    tap("A", 1.0)
    s = get_state()
    if s['sb1'] and s['sb1'] > 0x02000000:
        if s['mg'] > 0 or s['mn'] > 0 or s['x'] > 0 or s['y'] > 0:
            print(f"  Press {i}: ({s['x']},{s['y']}) Map({s['mg']},{s['mn']}) wild={s['wild']}")
            if s['wild'] == 0:
                print("  *** ENCOUNTERS ENABLED! ***")
                break

# === Step 4: Check if we're in the game ===
print("\nStep 4: Checking game state...")
s = get_state()
print(f"  Position: ({s['x']},{s['y']}) Map({s['mg']},{s['mn']})")
print(f"  sWildEncountersDisabled: {s['wild']}")

if s['sb1'] and s['sb1'] > 0x02000000:
    hp = read16(0x020241E6)
    lv = read8(0x020241E4)
    party = read8(0x0202418D)
    print(f"  Party: {party}, Lead Lv{lv}, HP={hp}")

# === Step 5: Try walking ===
print("\nStep 5: Testing movement...")
if s['sb1'] and s['sb1'] > 0x02000000:
    prev_x, prev_y = s['x'], s['y']
    moved = 0
    for i in range(30):
        tap("Up", 0.25)
        cx = read16(s['sb1'])
        cy = read16(s['sb1'] + 2)
        if cx != prev_x or cy != prev_y:
            moved += 1
            prev_x, prev_y = cx, cy
    s2 = get_state()
    print(f"  After 30 Up taps: ({s2['x']},{s2['y']}) Map({s2['mg']},{s2['mn']}) moved={moved} wild={s2['wild']}")

    # Walk more to reach Route 101 if needed
    if s2['mg'] == 0 and s2['mn'] == 9:  # Littleroot Town
        print("  In Littleroot, walking north to Route 101...")
        for i in range(50):
            tap("Up", 0.25)
        s3 = get_state()
        print(f"  Now at: ({s3['x']},{s3['y']}) Map({s3['mg']},{s3['mn']}) wild={s3['wild']}")

    # === Step 6: Test for encounters ===
    print("\nStep 6: Testing encounters (100 steps)...")
    s = get_state()
    prev_x, prev_y = s['x'], s['y']
    moved = 0
    encounter = False

    for i in range(100):
        d = random.choice(["Up", "Down", "Left", "Right"])
        tap(d, 0.25)

        cx = read16(s['sb1'])
        cy = read16(s['sb1'] + 2)
        if cx != prev_x or cy != prev_y:
            moved += 1
            prev_x, prev_y = cx, cy

        btl = read32(0x02022E90)
        if btl and btl > 0:
            encounter = True
            mg2 = read8(s['sb1'] + 4)
            mn2 = read8(s['sb1'] + 5)
            print(f"\n  *** ENCOUNTER! *** step={i} moved={moved}")
            print(f"  btl=0x{btl:08X} ({cx},{cy}) Map({mg2},{mn2})")
            break

        if i % 25 == 0:
            mg2 = read8(s['sb1'] + 4)
            mn2 = read8(s['sb1'] + 5)
            w = read8(WILD_DISABLED_ADDR)
            print(f"  Step {i}: ({cx},{cy}) Map({mg2},{mn2}) moved={moved} wild={w}")

    if encounter:
        print("\n*** ENCOUNTERS WORK! ***")
        # Save to all slots on all ports
        for slot in [1]:
            print(f"Saving clean state to slot {slot}...")
            session.post(f"{BASE_URL}/core/savestateslot", params={"slot": slot}, timeout=5)

        # Also save to ports 5001 and 5002
        for port in [5001, 5002]:
            try:
                s2 = requests.Session()
                # First load the state from port 5000
                s2.post(f"http://localhost:{port}/core/loadstateslot", params={"slot": 1}, timeout=5)
                time.sleep(0.3)
                # Load from port 5000's slot 1 won't work cross-port, so we need to save on each port separately
                # For now, just note that we need to copy
                print(f"  Note: Port {port} needs to load its own save state slot 1")
                s2.close()
            except:
                pass

        print("\nDone! Save state slot 1 updated with clean state.")
    else:
        s_final = get_state()
        print(f"\nNo encounters in {moved} steps.")
        print(f"Final: ({s_final['x']},{s_final['y']}) Map({s_final['mg']},{s_final['mn']}) wild={s_final['wild']}")
        print("\nThe issue may not be the save state.")
        print("Consider: the mGBA-http tap API might not properly simulate walking.")
