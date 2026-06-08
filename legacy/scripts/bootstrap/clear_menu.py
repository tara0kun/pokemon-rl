"""Clear any open menu/dialogue and restore clean overworld state."""
import requests
import time

MGBA_URL = "http://localhost:5000"
session = requests.Session()


def read_val(ep, addr):
    for _ in range(3):
        try:
            v = session.get(f"{MGBA_URL}/core/{ep}", params={"address": hex(addr)}, timeout=5).json()
            return int(v["value"]) if isinstance(v, dict) else int(v)
        except:
            time.sleep(0.3)
    return 0


def tap(button, delay=0.08):
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


def screenshot(name):
    path = f"C:/pokemon-rl/{name}.png"
    session.post(f"{MGBA_URL}/core/screenshot", params={"path": path}, timeout=5)


# Check current state
screenshot("current_state")
x, y = get_pos()
mg, mn = get_map()
print(f"Current: ({x},{y}) Map({mg},{mn})")

# Spam B to close any menus
print("Spamming B to close menus...")
for i in range(50):
    tap("B", 0.1)

screenshot("after_b_spam")
x, y = get_pos()
print(f"After B spam: ({x},{y})")

# Test movement
print("\nTesting movement:")
for d in ["Up", "Down", "Left", "Right"]:
    before = get_pos()
    for _ in range(10):
        tap(d, 0.04)
    after = get_pos()
    if after != before:
        print(f"  {d}: {before} -> {after} MOVED")
    else:
        print(f"  {d}: {before} blocked")

screenshot("after_move_test")
x, y = get_pos()
mg, mn = get_map()
print(f"\nFinal: ({x},{y}) Map({mg},{mn})")
