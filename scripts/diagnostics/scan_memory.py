"""
メモリスキャン: 正しいHPアドレスを探す
========================================
スターターポケモン（Lv5）の現在HP・最大HPは概ね 18〜30 の範囲。
0x02024000 〜 0x02025500 をスキャンして該当する 16bit 値のペアを探す。
"""

import requests

BASE_URL = "http://localhost:5000"


def read16(addr: int) -> int:
    try:
        r = requests.get(f"{BASE_URL}/core/read16",
                         params={"address": hex(addr)}, timeout=2)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except Exception:
        return -1


def read32(addr: int) -> int:
    try:
        r = requests.get(f"{BASE_URL}/core/read32",
                         params={"address": hex(addr)}, timeout=2)
        v = r.json()
        return int(v["value"]) if isinstance(v, dict) else int(v)
    except Exception:
        return -1


def scan(start: int, end: int, hp_min: int = 10, hp_max: int = 50):
    """
    start〜end をスキャンして HP らしい 16bit 値を探す。
    現在HPと最大HPが同じか現在HP<=最大HPのペアを探す。
    """
    print(f"スキャン中: {hex(start)} 〜 {hex(end)}  HP範囲: {hp_min}〜{hp_max}")
    hits = []
    addr = start
    while addr < end:
        cur_hp = read16(addr)
        max_hp = read16(addr + 2)
        if (hp_min <= cur_hp <= hp_max and
                hp_min <= max_hp <= hp_max and
                cur_hp <= max_hp):
            hits.append((addr, cur_hp, max_hp))
            print(f"  候補: {hex(addr)}  現在HP={cur_hp}  最大HP={max_hp}")
        addr += 2  # 2バイト刻みでスキャン
    return hits


def check_known():
    """既知のアドレス候補を直接チェック"""
    candidates = {
        "コード内のアドレス (0x020244EC)": 0x020244EC,
        "US版エメラルド標準 (0x020242DA)":  0x020242DA,
        "候補2 (0x02024496)":               0x02024496,
    }
    print("\n=== 既知アドレス確認 ===")
    for name, addr in candidates.items():
        cur = read16(addr)
        max_ = read16(addr + 2)
        word = read32(addr)
        print(f"  {name}")
        print(f"    read16({hex(addr)})       = {cur}")
        print(f"    read16({hex(addr+2)})     = {max_}")
        print(f"    read32({hex(addr)}) (word) = {word:#010x}")


if __name__ == "__main__":
    # まず既知アドレスを確認
    check_known()

    # 広めにスキャン
    print("\n=== メモリスキャン ===")
    hits = scan(0x02024000, 0x02025500, hp_min=10, hp_max=60)
    if not hits:
        print("  見つかりませんでした。スキャン範囲を広げます...")
        hits = scan(0x02023000, 0x02027000, hp_min=10, hp_max=60)

    print(f"\n合計 {len(hits)} 件の候補が見つかりました。")
