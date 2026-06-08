"""Load slot 3 (good state) and copy to slot 2."""
import requests
import time

MGBA_URL = "http://localhost:5000"
session = requests.Session()


def read_val(endpoint, addr):
    r = session.get(f"{MGBA_URL}/core/{endpoint}", params={"address": hex(addr)}, timeout=3)
    v = r.json()
    if isinstance(v, dict):
        return int(v["value"])
    return int(v)


def tap(button, delay=0.1):
    session.post(f"{MGBA_URL}/mgba-http/button/tap", params={"button": button}, timeout=3)
    time.sleep(delay)


# Load slot 3
print("Loading slot 3...")
session.post(f"{MGBA_URL}/core/loadstateslot", params={"slot": 3}, timeout=5)
time.sleep(1)

sb1 = read_val("read32", 0x03005AEC)
x = read_val("read16", 0x02037000)
y = read_val("read16", 0x02037002)
mg = read_val("read8", sb1 + 4)
mn = read_val("read8", sb1 + 5)
print(f"Slot 3: ({x},{y}) Map({mg},{mn})")

# Verify movement
tap("Up", 0.1)
x2 = read_val("read16", 0x02037000)
y2 = read_val("read16", 0x02037002)
print(f"After Up: ({x2},{y2}) - {'CAN MOVE' if (x2,y2) != (x,y) else 'FROZEN'}")

# Set flags
flag_addr = sb1 + 0x1270 + 256
cur = read_val("read8", flag_addr)
session.post(f"{MGBA_URL}/core/write8", params={"address": hex(flag_addr), "value": cur | 0x03}, timeout=3)
fb = read_val("read8", flag_addr)
print(f"Flags: POKEMON_GET={bool(fb & 1)}, POKEDEX_GET={bool(fb & 2)}")

# Save to slots 1, 2, 3
for slot in [1, 2, 3]:
    session.post(f"{MGBA_URL}/core/savestateslot", params={"slot": slot}, timeout=5)
print("Saved to slots 1, 2, 3")
