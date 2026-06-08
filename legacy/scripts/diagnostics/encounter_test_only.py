"""
Encounter test on Route 101 (or wherever the player currently is).
Run this AFTER the player has reached Route 101 with a starter.
Tests 500 random steps and reports encounters.
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

# ============================================================
# PRE-CHECK
# ============================================================
print("=" * 60, flush=True)
print("ENCOUNTER TEST", flush=True)
print("=" * 60, flush=True)

m = get_map()
print(f"Current position: {pos_str()}", flush=True)
ss("enc_test_start")

# Check party
PARTY_COUNT = 0x020244E9
count = rval(PARTY_COUNT, 8)
print(f"Party count: {count}", flush=True)

sb1 = rval(SB1_PTR, 32)
if not sb1 or sb1 < 0x02000000:
    print("SB1 invalid!", flush=True)
    sys.exit(1)

if count and count > 0:
    PARTY_DATA = 0x020244EC
    for i in range(min(count, 6)):
        base_addr = PARTY_DATA + i * 100
        species = rval(base_addr, 16)
        level = rval(base_addr + 0x54, 8)
        hp = rval(base_addr + 0x56, 16)
        maxhp = rval(base_addr + 0x58, 16)
        print(f"  Party[{i}]: species={species} lv{level} HP={hp}/{maxhp}", flush=True)
else:
    print("WARNING: No party Pokemon detected!", flush=True)

# ============================================================
# ENCOUNTER TEST: 500 random steps
# ============================================================
print(f"\n{'='*60}", flush=True)
print("Starting encounter test (500 steps)...", flush=True)
print(f"{'='*60}", flush=True)

prev_x = rval(sb1, 16) or 0
prev_y = rval(sb1 + 2, 16) or 0
moved = 0
encounters = 0
steps_in_grass = 0

for i in range(500):
    d = random.choice(["Up", "Down", "Left", "Right"])
    tap(d, 0.18)

    sb1 = rval(SB1_PTR, 32)
    if not sb1 or sb1 < 0x02000000:
        continue

    cx = rval(sb1, 16)
    cy = rval(sb1 + 2, 16)
    if cx is not None and cy is not None and (cx != prev_x or cy != prev_y):
        moved += 1
        prev_x, prev_y = cx, cy

    btl = rval(ADDR_BATTLE_FLAGS, 32)
    if btl and 0 < btl < 0x10000:
        encounters += 1
        cmg = rval(sb1 + 4, 8)
        cmn = rval(sb1 + 5, 8)
        print(f"\n  *** ENCOUNTER #{encounters} step {i}! ({cx},{cy}) Map({cmg},{cmn}) ***", flush=True)
        ss(f"enc_test_{encounters}")

        # Wait for battle to fully start
        time.sleep(1.5)

        # Try to run away: B to cancel, Down+Right to RUN, A to confirm
        for _ in range(3):
            tap("B", 0.2)
        tap("Down", 0.3)   # Move to RUN option
        tap("Right", 0.3)  # RUN is bottom-right
        tap("A", 0.5)      # Confirm RUN
        time.sleep(2)

        # Press A through "Got away safely" text
        for _ in range(10):
            tap("A", 0.3)

        # Update position after battle
        sb1 = rval(SB1_PTR, 32)
        if sb1 and sb1 >= 0x02000000:
            prev_x = rval(sb1, 16) or 0
            prev_y = rval(sb1 + 2, 16) or 0

        if encounters >= 5:
            break

    if i % 50 == 49:
        cmg = rval(sb1 + 4, 8)
        cmn = rval(sb1 + 5, 8)
        print(f"  step {i+1}: ({cx},{cy}) Map({cmg},{cmn}) moves={moved} enc={encounters}", flush=True)
        ss(f"enc_test_s{i+1}")

# ============================================================
# RESULTS
# ============================================================
print(f"\n{'='*60}", flush=True)
print(f"RESULT: {moved} moves, {encounters} encounters in 500 steps", flush=True)

if encounters > 0:
    rate = encounters / max(moved, 1)
    print(f"Encounter rate: {rate:.3f} per move ({rate*100:.1f}%)", flush=True)
    print("*** ENCOUNTERS WORK ON NEW GAME! ***", flush=True)
elif moved > 20:
    # Probability of 0 encounters in N moves with ~11.1% rate per step in grass
    p = 0.889 ** moved
    print(f"P(0 encounters | {moved} moves) = {p:.2e}", flush=True)
    if p < 0.001:
        print("*** ENCOUNTERS BROKEN - EXTREMELY UNLIKELY TO BE CHANCE ***", flush=True)
    else:
        print("Note: Low move count, might not have been in grass", flush=True)
else:
    print("Too few moves to determine encounter rate", flush=True)

print(f"{'='*60}", flush=True)
ss("enc_test_final")
