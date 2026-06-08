"""
メモリアドレス確認スクリプト
=============================
mGBA + mGBA-http が起動している状態で実行してください。
ゲーム内で動き回ったり、バトルしながら実行すると各値が変化するのを確認できます。

使い方:
  python check_memory.py          # 5秒ごとに10回表示
  python check_memory.py --once   # 1回だけ表示して終了
"""

import sys
import time
import requests

BASE_URL = "http://localhost:5000"

# 確認するアドレス
ADDRS = {
    "プレイヤーX (32bit)": (0x0202E4C0, 32),
    "プレイヤーY (32bit)": (0x0202E4C4, 32),
    "バッジbitfield (8bit)": (0x020297BD, 8),
    "HP+MaxHP word (32bit)": (0x020244EC, 32),
}


def read8(addr: int) -> int:
    r = requests.get(f"{BASE_URL}/core/read8", params={"address": hex(addr)}, timeout=2)
    v = r.json()
    return int(v["value"]) if isinstance(v, dict) else int(v)


def read16(addr: int) -> int:
    r = requests.get(f"{BASE_URL}/core/read16", params={"address": hex(addr)}, timeout=2)
    v = r.json()
    return int(v["value"]) if isinstance(v, dict) else int(v)


def read32(addr: int) -> int:
    r = requests.get(f"{BASE_URL}/core/read32", params={"address": hex(addr)}, timeout=2)
    v = r.json()
    return int(v["value"]) if isinstance(v, dict) else int(v)


def check():
    print("-" * 50)
    try:
        x = read32(0x02037000) & 0xFFFF
        y = read32(0x02037004) & 0xFFFF
        badges_raw = read8(0x020297BD)
        badges = bin(badges_raw).count("1")
        hp_cur = read16(0x020241E6)
        hp_max = read16(0x020241E8)

        print(f"  座標          : X={x}, Y={y}")
        print(f"  バッジ        : {badges}個 (raw={badges_raw:#010b})")
        print(f"  HP            : {hp_cur} / {hp_max}")
        if hp_max > 0:
            print(f"  HP割合        : {hp_cur/hp_max*100:.1f}%")
    except requests.exceptions.ConnectionError:
        print("  [エラー] mGBA-http に接続できません")
        print("  mGBA と mGBA-http-0.8.1-win-x64-self-contained.exe が起動しているか確認してください")
    except Exception as e:
        print(f"  [エラー] {e}")


def main():
    once = "--once" in sys.argv
    print("=== メモリアドレス確認 ===")
    if once:
        check()
    else:
        print("5秒ごとに10回表示します。Ctrl+C で止められます。\n")
        for i in range(10):
            print(f"[{i+1}/10]")
            check()
            if i < 9:
                time.sleep(5)
    print("\n完了。")


if __name__ == "__main__":
    main()
