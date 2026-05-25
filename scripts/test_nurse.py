"""Test nurse interaction step by step."""
import requests, time, sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
BASE = f"http://localhost:{PORT}"

def r8(a): return requests.get(f"{BASE}/core/read8", params={"address": a}).json()
def r16(a): return r8(a) | (r8(a+1) << 8)
def tap(btn, frames=8):
    requests.post(f"{BASE}/button/tap", params={"button": btn, "frames": frames})

def player_pos():
    return r16(0x02037000), r16(0x02037002)

def nurse_pos():
    return r16(0x02037014 + 0x10), r16(0x02037014 + 0x12)

def nurse_init():
    return r16(0x02037014 + 0x0C), r16(0x02037014 + 0x0E)

def read_hp():
    sb1 = r8(0x03005AEC) | (r8(0x03005AED)<<8) | (r8(0x03005AEE)<<16) | (r8(0x03005AEF)<<24)
    # Party mon 1 HP: SB2 + offset... use known party address
    # Actually read from pokemon_env known addresses
    # SB2 pointer at 0x03005AF0 for JP
    sb2 = r8(0x03005AF0) | (r8(0x03005AF1)<<8) | (r8(0x03005AF2)<<16) | (r8(0x03005AF3)<<24)
    # Party data at SB2 + 0x234 (JP offset for party)
    # Mon 1 HP current at party + 0x58 (after 100-byte Pokemon struct header)
    # Actually, let's try reading from the battle/party struct
    # For now just print raw sb2
    return sb2

# Check nurse movement state
def nurse_movement_status():
    base = 0x02037014
    # In ObjectEvent struct, there are movement-related fields
    # +0x00: flags (bit 0 = active)
    # +0x02: some state?
    # +0x04: some state?
    flags = r8(base)
    byte1 = r8(base + 1)
    byte2 = r8(base + 2)
    byte3 = r8(base + 3)
    byte4 = r8(base + 4)
    # Movement action at some offset
    # Let's read several bytes to see NPC state
    state_bytes = [r8(base + i) for i in range(0x24)]
    return state_bytes

print(f"=== Nurse Interaction Test (port {PORT}) ===")
px, py = player_pos()
nx, ny = nurse_pos()
nix, niy = nurse_init()
print(f"Player: ({px},{py})")
print(f"Nurse current: ({nx},{ny})")
print(f"Nurse init: ({nix},{niy})")
print(f"Manhattan dist: {abs(nx-px) + abs(ny-py)}")

# Read nurse object state
print("\n--- Nurse ObjectEvent raw bytes ---")
state = nurse_movement_status()
for i in range(0, len(state), 8):
    hex_str = ' '.join(f'{b:02X}' for b in state[i:i+8])
    print(f"  +0x{i:02X}: {hex_str}")

# Now try manual interaction sequence
print("\n--- Manual interaction test ---")
print("Step 1: Walk toward nurse (face her)")
# Determine direction to nurse
dx = nx - px
dy = ny - py
if abs(dy) >= abs(dx):
    btn = "Up" if dy < 0 else "Down"
else:
    btn = "Right" if dx > 0 else "Left"
print(f"  Pressing {btn} (dx={dx}, dy={dy})")
tap(btn, 16)  # Hold for 16 frames (1 full tile movement)
time.sleep(0.5)

px2, py2 = player_pos()
print(f"  Player after: ({px2},{py2})")
if px2 == px and py2 == py:
    print(f"  -> BLOCKED (nurse/wall at ({nx},{ny}))")
else:
    print(f"  -> MOVED (nurse wasn't blocking!)")

# Step 2: Press A
print("Step 2: Press A")
tap("A", 8)
time.sleep(0.5)

px3, py3 = player_pos()
nx3, ny3 = nurse_pos()
print(f"  Player: ({px3},{py3}), Nurse: ({nx3},{ny3})")

# Check if dialogue might have started by looking at screen state
# We can check callback/script state, but for now just observe

# Step 3: Press A several more times
print("Step 3: Press A x5")
for i in range(5):
    tap("A", 8)
    time.sleep(0.3)
    px_i, py_i = player_pos()
    nx_i, ny_i = nurse_pos()
    print(f"  [{i}] Player: ({px_i},{py_i}), Nurse: ({nx_i},{ny_i})")

# Final check
print("\n--- Final state ---")
px_f, py_f = player_pos()
nx_f, ny_f = nurse_pos()
print(f"Player: ({px_f},{py_f})")
print(f"Nurse: ({nx_f},{ny_f})")
print(f"Distance: {abs(nx_f-px_f) + abs(ny_f-py_f)}")
