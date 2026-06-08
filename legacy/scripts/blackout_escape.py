"""Blackout escape v9: Fix button stuck issue, proper timing."""
import requests
import time
import urllib.request

PORT = 5001
s = requests.Session()
base = f'http://localhost:{PORT}'

def release_all():
    """Release all buttons to prevent stuck state."""
    for btn in ['A', 'B', 'Up', 'Down', 'Left', 'Right']:
        try: s.post(f'{base}/mgba-http/button/release', params={'button': btn}, timeout=0.3)
        except: pass

def tap(btn, d=0.08):
    try: s.post(f'{base}/mgba-http/button/tap', params={'button': btn}, timeout=0.3)
    except: pass
    time.sleep(d)

def stable_read():
    try:
        sb1 = int(urllib.request.urlopen(f'{base}/core/read32?address=0x03005AEC', timeout=1).read())
        mg = int(urllib.request.urlopen(f'{base}/core/read8?address={sb1+4}', timeout=1).read())
        mn = int(urllib.request.urlopen(f'{base}/core/read8?address={sb1+5}', timeout=1).read())
        x = int(urllib.request.urlopen(f'{base}/core/read16?address={sb1}', timeout=1).read())
        y = int(urllib.request.urlopen(f'{base}/core/read16?address={sb1+2}', timeout=1).read())
        time.sleep(0.15)
        mg2 = int(urllib.request.urlopen(f'{base}/core/read8?address={sb1+4}', timeout=1).read())
        mn2 = int(urllib.request.urlopen(f'{base}/core/read8?address={sb1+5}', timeout=1).read())
        if mg == mg2 and mn == mn2 and x < 65000:
            return mg, mn, x, y
    except:
        pass
    return None

print('=== BLACKOUT v9 ===', flush=True)
release_all()
time.sleep(0.3)

for cycle in range(5000):
    # Periodic check
    if cycle % 30 == 0:
        release_all()  # Prevent button stuck
        time.sleep(0.2)
        sr = stable_read()
        if sr:
            mg, mn, x, y = sr
            if mn != 17 and mn != -1:
                print(f'*** LEFT R102 at cycle {cycle}! Map=({mg},{mn}) ({x},{y}) ***', flush=True)
                break
            print(f'Cycle {cycle}: ({mg},{mn}) ({x},{y})', flush=True)

    # === FIGHT SEQUENCE ===
    # 1. A to enter Fight or advance text (0.1s delay for reliability)
    tap('A', 0.1)
    tap('A', 0.1)
    tap('A', 0.1)

    # 2. Down to navigate move list (skip PP=0 moves)
    tap('Down', 0.08)

    # 3. A to select move and advance
    tap('A', 0.1)
    tap('A', 0.1)
    tap('A', 0.1)
    tap('A', 0.1)

    # 4. Wait for battle animation
    time.sleep(0.3)

    # 5. A to advance battle text
    tap('A', 0.1)
    tap('A', 0.1)
    tap('A', 0.1)

    # 6. Down + A for Pokemon switch (if needed)
    # A selects Pokemon, A selects いれかえる (first submenu option)
    # Down skips fainted Pokemon
    tap('Down', 0.08)
    tap('A', 0.15)  # Select Pokemon
    tap('A', 0.15)  # Confirm いれかえる

    # 7. More A to advance any text
    tap('A', 0.1)
    tap('A', 0.1)

    # 8. Walk to trigger encounters
    tap('Up', 0.06)
    tap('Down', 0.06)

print('Done', flush=True)
sr = stable_read()
if sr:
    print(f'Final: ({sr[0]},{sr[1]}) ({sr[2]},{sr[3]})', flush=True)
