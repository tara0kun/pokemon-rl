"""
エンカウントテスト: mGBAで草むらを歩いてエンカウントが発生するか確認
"""
import requests
import time
import struct

BASE_URL = "http://localhost:5000"
session = requests.Session()

def read16(addr):
    try:
        r = session.get(f"{BASE_URL}/core/read16", params={"address": addr}, timeout=1)
        return int(r.text)
    except:
        return None

def read8(addr):
    try:
        r = session.get(f"{BASE_URL}/core/read8", params={"address": addr}, timeout=1)
        return int(r.text)
    except:
        return None

def read32(addr):
    try:
        r = session.get(f"{BASE_URL}/core/read32", params={"address": addr}, timeout=1)
        return int(r.text)
    except:
        return None

def write16(addr, val):
    try:
        session.post(f"{BASE_URL}/core/write16", params={"address": addr, "value": val}, timeout=1)
    except:
        pass

def tap(button):
    try:
        session.post(f"{BASE_URL}/mgba-http/button/tap", params={"button": button}, timeout=0.5)
    except:
        pass

def get_pos():
    """SaveBlock1から座標を読む"""
    sb1 = read32(0x03005D8C)
    if sb1 and sb1 > 0x02000000:
        x = read16(sb1)
        y = read16(sb1 + 2)
        mg = read8(sb1 + 4)
        mn = read8(sb1 + 5)
        return x, y, mg, mn
    return None, None, None, None

# セーブステート slot 1 ロード
print("Loading slot 1...")
session.post(f"{BASE_URL}/core/loadstateslot", params={"slot": 1}, timeout=5)
time.sleep(1)

# Repelクリア
sb1 = read32(0x03005D8C)
if sb1 and sb1 > 0x02000000:
    repel = read16(sb1 + 0x2E)
    print(f"Repel at +2E: {repel}")
    write16(sb1 + 0x2E, 0)
    print(f"Repel cleared to 0")

    # SB1のダンプ (0x00-0x40)
    print("\nSB1 dump (0x00 - 0x40):")
    for off in range(0, 0x40, 2):
        v = read16(sb1 + off)
        print(f"  +{off:02X}: {v} (0x{v:04X})" if v is not None else f"  +{off:02X}: None")

# 現在位置
x, y, mg, mn = get_pos()
print(f"\nStart pos: ({x},{y}) Map({mg},{mn})")

# HP確認
hp_cur = read16(0x020244E0)
hp_max = read16(0x020244E2)
print(f"HP: {hp_cur}/{hp_max}")

# gBattleTypeFlags確認
btl_flags = read32(0x02022E90)
print(f"gBattleTypeFlags: {btl_flags} (0x{btl_flags:08X})" if btl_flags else "gBattleTypeFlags: None")

# 草むらを歩くテスト
print(f"\n=== Walking test (200 steps with delay) ===")
prev_x, prev_y = x, y
steps_taken = 0
encounters = 0

for i in range(200):
    # 上に歩く（Route 101は北がOldale方面）
    direction = ["Up", "Left", "Right", "Down"][i % 4]
    tap(direction)
    time.sleep(0.05)  # 50ms待機（3フレーム）

    x, y, mg, mn = get_pos()
    if x != prev_x or y != prev_y:
        steps_taken += 1
        prev_x, prev_y = x, y

    # エンカウントチェック: gBattleTypeFlagsが非0
    btl = read32(0x02022E90)
    if btl and 0 < btl < 0x10000:
        encounters += 1
        hp = read16(0x020244E0)
        print(f"  [ENCOUNTER!] Step {i}, pos=({x},{y}) Map({mg},{mn}) "
              f"btlFlags=0x{btl:04X} HP={hp}")
        # 戦闘をスキップ (逃走)
        for _ in range(50):
            tap("A")
            time.sleep(0.05)
        break

    if i % 50 == 0:
        repel = read16(sb1 + 0x2E)
        print(f"  Step {i}: pos=({x},{y}) Map({mg},{mn}) moved={steps_taken} repel={repel}")

print(f"\nResult: {steps_taken} actual moves, {encounters} encounters in 200 taps")
print(f"Final pos: ({x},{y}) Map({mg},{mn})")

# Repel最終チェック
repel = read16(sb1 + 0x2E)
print(f"Repel at +2E: {repel}")
