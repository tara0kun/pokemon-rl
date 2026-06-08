"""
Battle coordinate diagnostic - monitors X/Y from both ADDR_X/Y and SB1 during battles.
Run while an emulator on port 5000 has a game running.
"""
import time
import requests

PORT = 5000
BASE = f"http://localhost:{PORT}"

# Addresses
ADDR_X = 0x02037000
ADDR_Y = 0x02037002
SB1_PTR = 0x03005AEC
ADDR_GMAIN_CB2 = 0x03002364

# gBattleMons addresses to test (both US and JP)
BMON_US_BASE = 0x02023F28
BMON_JP_BASE = 0x02023DCC
BMON_SIZE = 0x58
BMON_OFF_HP = 0x28
BMON_OFF_MAXHP = 0x2C
BMON_OFF_LEVEL = 0x2A
BMON_OFF_SPECIES = 0x00

session = requests.Session()


def _read(endpoint, addr):
    try:
        r = session.get(f"{BASE}/core/{endpoint}", params={"address": hex(addr)}, timeout=0.5)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except Exception:
        return None


def read8(addr): return _read("read8", addr)
def read16(addr): return _read("read16", addr)
def read32(addr): return _read("read32", addr)


def press(key):
    try:
        session.post(f"{BASE}/input/keydown", json={"key": key}, timeout=0.5)
        time.sleep(0.08)
        session.post(f"{BASE}/input/keyup", json={"key": key}, timeout=0.5)
        time.sleep(0.08)
    except:
        pass


def main():
    print("Battle coordinate diagnostic - monitoring...")
    print("Walking right to trigger a wild encounter.\n")

    prev_x = prev_y = None
    same_count = 0

    for step in range(500):
        # Read coordinates from direct address
        raw_x = read16(ADDR_X)
        raw_y = read16(ADDR_Y)

        # Read coordinates from SB1
        sb1 = read32(SB1_PTR)
        sb1_x = read16(sb1 + 0) if sb1 and sb1 > 0x02000000 else None
        sb1_y = read16(sb1 + 2) if sb1 and sb1 > 0x02000000 else None

        # Read CB2
        cb2 = read32(ADDR_GMAIN_CB2) or 0

        # Position tracking
        if raw_x == prev_x and raw_y == prev_y:
            same_count += 1
        else:
            same_count = 0
        prev_x, prev_y = raw_x, raw_y

        # Read enemy data when frozen
        enemy_info = ""
        if same_count >= 2:
            for label, base in [("US", BMON_US_BASE), ("JP", BMON_JP_BASE)]:
                e_base = base + BMON_SIZE
                species = read16(e_base + BMON_OFF_SPECIES) or 0
                hp = read16(e_base + BMON_OFF_HP) or 0
                mhp = read16(e_base + BMON_OFF_MAXHP) or 0
                lv = read8(e_base + BMON_OFF_LEVEL) or 0
                enemy_info += f" | {label}: sp={species} HP={hp}/{mhp} Lv={lv}"

        # Log
        is_battle_cb2 = (cb2 == 0x080380FD)
        is_transition = (cb2 in (0x080857C5, 0x080857B9))
        cb2_tag = " BATTLE" if is_battle_cb2 else " TRANS" if is_transition else ""

        if same_count >= 1 or step % 20 == 0:
            print(f"[{step:3d}] raw=({raw_x},{raw_y}) sb1=({sb1_x},{sb1_y}) "
                  f"CB2=0x{cb2:08X}{cb2_tag} freeze={same_count}{enemy_info}",
                  flush=True)

        # Press A during freeze, Right otherwise
        if same_count >= 3:
            press("A")
        else:
            press("Right")

        time.sleep(0.05)


if __name__ == "__main__":
    main()
