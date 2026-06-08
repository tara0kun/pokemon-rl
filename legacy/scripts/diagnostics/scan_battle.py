"""
Battle address scanner for JP Emerald.
Triggers a battle by walking on Route 101, then scans EWRAM for battle data.

Usage:
  1. Make sure mGBA + mGBA-http are running with Emerald loaded
  2. Run: python scan_battle.py

This will:
  - Load save state slot 1 (Route 101)
  - Walk until a battle starts (position freeze detected)
  - Scan for gBattleTypeFlags and gBattleMons addresses
  - Report findings
"""
import requests
import time
import random
import sys

MGBA_URL = "http://localhost:5000"
session = requests.Session()


def r8(addr):
    try:
        v = session.get(f"{MGBA_URL}/core/read8", params={"address": hex(addr)}, timeout=3).json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None


def r16(addr):
    try:
        v = session.get(f"{MGBA_URL}/core/read16", params={"address": hex(addr)}, timeout=3).json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None


def r32(addr):
    try:
        v = session.get(f"{MGBA_URL}/core/read32", params={"address": hex(addr)}, timeout=3).json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except:
        return None


def tap(button, delay=0.04):
    try:
        session.post(f"{MGBA_URL}/mgba-http/button/tap", params={"button": button}, timeout=3)
    except:
        pass
    time.sleep(delay)


def screenshot(name):
    path = f"C:/pokemon-rl/screenshots/{name}.png"
    session.post(f"{MGBA_URL}/core/screenshot", params={"path": path}, timeout=5)
    return path


def get_pos():
    return r16(0x02037000), r16(0x02037002)


print("=" * 60)
print("  Battle Address Scanner for JP Pokemon Emerald")
print("=" * 60)

# Step 1: Load slot 1 (Route 101)
print("\n[1] Loading slot 1...")
session.post(f"{MGBA_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(1)

x, y = get_pos()
hp = r16(0x020241E6)
hp_max = r16(0x020241E8)
print(f"    Position: ({x},{y}), HP: {hp}/{hp_max}")

# Pre-battle: Take a snapshot of EWRAM ranges
print("\n[2] Pre-battle memory snapshot...")
pre_battle_snapshot = {}
# Scan region around estimated gBattleTypeFlags (0x02022E90)
for addr in range(0x02022000, 0x02024000, 4):
    val = r32(addr)
    if val is not None:
        pre_battle_snapshot[addr] = val

# Also scan near gBattleMons (0x02023F28)
for addr in range(0x02023E00, 0x02024800, 4):
    val = r32(addr)
    if val is not None:
        pre_battle_snapshot[addr] = val

print(f"    Captured {len(pre_battle_snapshot)} u32 values")

# Step 2: Walk to trigger a battle
print("\n[3] Walking on Route 101 to trigger battle...")
last_pos = get_pos()
freeze_count = 0
walk_steps = 0

for i in range(3000):
    d = random.choice(["Up", "Down", "Left", "Right", "Up", "Down"])
    tap(d, 0.03)
    walk_steps += 1

    cur_pos = get_pos()
    if cur_pos == last_pos:
        freeze_count += 1
    else:
        freeze_count = 0
        last_pos = cur_pos

    # Battle detected when position frozen for 15+ reads
    if freeze_count >= 15:
        hp_now = r16(0x020241E6)
        print(f"\n    FROZEN at {cur_pos} after {walk_steps} steps! (HP: {hp_now})")
        screenshot("scan_battle_detected")

        # Wait a moment for battle to fully load
        time.sleep(0.5)

        # === SCAN PHASE ===
        print("\n[4] Scanning for battle addresses...")

        # 4A: Find gBattleTypeFlags by comparing to pre-battle snapshot
        print("\n  --- gBattleTypeFlags candidates (changed non-zero u32) ---")
        battle_flag_candidates = []
        for addr in sorted(pre_battle_snapshot.keys()):
            pre_val = pre_battle_snapshot[addr]
            cur_val = r32(addr)
            if cur_val is None:
                continue
            # Look for: was 0 before, now non-zero and < 0x10000
            if pre_val == 0 and 0 < cur_val < 0x10000:
                battle_flag_candidates.append((addr, cur_val))
                print(f"    {hex(addr)}: 0 -> {hex(cur_val)} ({cur_val})")

        if not battle_flag_candidates:
            print("    No obvious candidates found (0->nonzero)")
            # Broader scan: any value that became non-zero
            for addr in sorted(pre_battle_snapshot.keys()):
                if 0x02022C00 <= addr <= 0x02023200:
                    pre_val = pre_battle_snapshot[addr]
                    cur_val = r32(addr)
                    if cur_val is not None and cur_val != pre_val:
                        print(f"    {hex(addr)}: {hex(pre_val)} -> {hex(cur_val)}")

        # 4B: Scan for BattlePokemon structures
        print("\n  --- gBattleMons candidates (valid Pokemon data) ---")
        # Player mon: should match our Torchic Lv5 with HP ~20
        # Enemy mon: should be a low-level wild Pokemon (Lv2-3)

        # Torchic species ID = 255
        # Common Route 101 Pokemon:
        #   Wurmple=265, Zigzagoon=263, Poochyena=261
        player_species = 255
        player_level = r8(0x020241E4) or 5

        found_bases = []
        # Scan EWRAM region where gBattleMons should be
        for base in range(0x02023800, 0x02024800, 4):
            species = r16(base)
            if species is None or species != player_species:
                continue
            # Check if this looks like our BattlePokemon
            mon_hp = r16(base + 0x28)
            mon_maxhp = r16(base + 0x2C)
            mon_level = r8(base + 0x2A)
            if mon_hp is None or mon_maxhp is None or mon_level is None:
                continue
            if mon_maxhp > 0 and mon_maxhp < 100 and 1 <= mon_level <= 100:
                # Found potential player BattlePokemon!
                print(f"\n  PLAYER MON at {hex(base)}:")
                print(f"    Species: {species} (Torchic=255)")
                print(f"    HP: {mon_hp}/{mon_maxhp}")
                print(f"    Level: {mon_level}")

                # Check moves
                moves = []
                for j in range(4):
                    m = r16(base + 0x0C + j * 2)
                    moves.append(m if m else 0)
                print(f"    Moves: {moves}")

                # Check types
                t1 = r8(base + 0x21)
                t2 = r8(base + 0x21 + 1)
                print(f"    Types: {t1}, {t2}")

                # Check enemy at base + 0x58
                enemy_base = base + 0x58
                e_species = r16(enemy_base)
                e_hp = r16(enemy_base + 0x28)
                e_maxhp = r16(enemy_base + 0x2C)
                e_level = r8(enemy_base + 0x2A)
                e_t1 = r8(enemy_base + 0x21)
                e_t2 = r8(enemy_base + 0x21 + 1)

                if e_species and 1 <= e_species <= 440 and e_maxhp and e_maxhp < 100:
                    print(f"\n  ENEMY MON at {hex(enemy_base)}:")
                    print(f"    Species: {e_species}")
                    print(f"    HP: {e_hp}/{e_maxhp}")
                    print(f"    Level: {e_level}")
                    print(f"    Types: {e_t1}, {e_t2}")
                    e_moves = []
                    for j in range(4):
                        m = r16(enemy_base + 0x0C + j * 2)
                        e_moves.append(m if m else 0)
                    print(f"    Moves: {e_moves}")

                    found_bases.append(base)
                    print(f"\n  >>> gBattleMons[0] = {hex(base)} <<<")
                    print(f"  >>> gBattleMons[1] = {hex(enemy_base)} <<<")

        if not found_bases:
            print("    No valid BattlePokemon structures found")
            print("\n  Trying broader species scan (any valid Pokemon)...")
            for base in range(0x02023000, 0x02025000, 2):
                species = r16(base)
                if species in [255, 261, 263, 265]:  # Torchic, Poochyena, Zigzagoon, Wurmple
                    hp_at = r16(base + 0x28)
                    maxhp_at = r16(base + 0x2C)
                    level_at = r8(base + 0x2A)
                    if hp_at and maxhp_at and 1 < maxhp_at < 100 and level_at and 1 <= level_at <= 100:
                        print(f"    {hex(base)}: species={species} HP={hp_at}/{maxhp_at} Lv{level_at}")

        # 4C: Check gMoveSelectionCursor candidates
        print("\n  --- gMoveSelectionCursor candidates ---")
        for addr in range(0x02024300, 0x02024400):
            val = r8(addr)
            pre = pre_battle_snapshot.get(addr & ~3, None)
            if val is not None and 0 <= val <= 3:
                # This could be the cursor
                pass  # Too many candidates, skip broad scan

        # Summary
        print("\n" + "=" * 60)
        print("  SCAN RESULTS SUMMARY")
        print("=" * 60)
        if battle_flag_candidates:
            print(f"  gBattleTypeFlags candidates: {len(battle_flag_candidates)}")
            for addr, val in battle_flag_candidates[:5]:
                print(f"    {hex(addr)} = {hex(val)}")
        if found_bases:
            print(f"  gBattleMons[0] confirmed: {hex(found_bases[0])}")
            print(f"  gBattleMons[1] confirmed: {hex(found_bases[0] + 0x58)}")
        else:
            print("  gBattleMons: NOT FOUND")
        print("=" * 60)

        # Save this state for later analysis
        session.post(f"{MGBA_URL}/core/savestateslot", params={"slot": 9}, timeout=5)
        print("  Saved battle state to slot 9")

        sys.exit(0)

    if walk_steps % 500 == 0:
        x, y = get_pos()
        print(f"    [{walk_steps}] at ({x},{y})")

print(f"\n    No battle triggered in {walk_steps} steps")
print("    Make sure slot 1 is on Route 101 with flags set")
