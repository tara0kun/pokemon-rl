"""Simple test: stand at y=11 and press Up+A for counter-through healing."""
import requests, time, sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
BASE = f"http://localhost:{PORT}"
sess = requests.Session()  # Reuse connections

def r8(a): return sess.get(f"{BASE}/core/read8", params={"address": a}).json()
def r16(a): return r8(a) | (r8(a+1) << 8)
def tap(btn, frames=16):
    sess.post(f"{BASE}/button/tap", params={"button": btn, "frames": frames})
    time.sleep(0.05)

def player_pos():
    return r16(0x02037000), r16(0x02037002)

def read_hp():
    """Read party mon 1 current HP and max HP."""
    # SB2 pointer
    sb2 = r8(0x03005AF0) | (r8(0x03005AF1)<<8) | (r8(0x03005AF2)<<16) | (r8(0x03005AF3)<<24)
    # Party offset from SB2: +0x234 in JP Emerald
    party_base = sb2 + 0x234
    # Mon 1 structure: first 100 bytes are encrypted data
    # HP current at +0x56 (16-bit), HP max at +0x58 (16-bit) in decrypted pokemon struct
    # Actually, in Gen 3 party struct, after the 80-byte encrypted data block:
    # The visible stats are at fixed offsets in the party pokemon struct
    # Pokemon struct is 100 bytes (box) or 100+extra for party
    # For party: +0x56 = HP current, +0x58 = HP max
    hp_cur = r16(party_base + 0x56)
    hp_max = r16(party_base + 0x58)
    return hp_cur, hp_max

print(f"=== Healing Test (port {PORT}) ===")
px, py = player_pos()
hp_cur, hp_max = read_hp()
print(f"Player: ({px},{py}), HP: {hp_cur}/{hp_max}")

# Navigate to y=11 if not there
if py > 11:
    print(f"Moving north to y=11...")
    for i in range(py - 11):
        tap("Up")
        time.sleep(0.3)
    px, py = player_pos()
    print(f"  Now at ({px},{py})")

if py == 11:
    print(f"\nAt counter row (y=11). Testing Up+A...")
    # Try different X positions
    for test_x in [11, 12, 13, 14]:
        # Navigate to test_x
        px, py = player_pos()
        while px != test_x:
            if px < test_x:
                tap("Right")
            else:
                tap("Left")
            time.sleep(0.3)
            px, py = player_pos()
            if py != 11:
                print(f"  Moved off row! ({px},{py})")
                break

        if py != 11:
            continue

        print(f"\n  Testing x={test_x}: ", end="", flush=True)
        # Face Up
        tap("Up", 8)
        time.sleep(0.2)
        # Press A
        tap("A", 8)
        time.sleep(0.5)
        # Press A several more times (for dialogue)
        for i in range(8):
            tap("A", 8)
            time.sleep(0.3)

        # Check HP
        hp_cur2, hp_max2 = read_hp()
        px2, py2 = player_pos()
        print(f"pos=({px2},{py2}) HP={hp_cur2}/{hp_max2}", end="")
        if hp_cur2 > hp_cur:
            print(f" *** HEALED! ***")
            break
        elif hp_cur2 == hp_max2:
            print(f" *** FULL HP! ***")
            break
        else:
            print(f" (no change)")

        # Dismiss any dialogue with B
        for i in range(3):
            tap("B", 8)
            time.sleep(0.2)
else:
    print(f"Not at y=11 (at y={py}), trying to navigate...")

# Final state
hp_final, hp_max_final = read_hp()
px_f, py_f = player_pos()
print(f"\nFinal: ({px_f},{py_f}) HP={hp_final}/{hp_max_final}")

# Record GIF
print("\nRecording GIF...")
import subprocess
subprocess.run([
    "c:/pokemon-rl/poke-rl/Scripts/python.exe",
    "record_mgba.py", str(PORT), "2", "8"
], cwd="c:/pokemon-rl")
