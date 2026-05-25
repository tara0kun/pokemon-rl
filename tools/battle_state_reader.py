"""battle_state_reader.py — RAM 直接読みで battle semantic state を取得 (innovative monitoring)

GIF screenshot ベース監視の代替/補完。 mGBA-http の core/read API で
battle 関連 RAM を読み取り、 high-level state を JSON 出力。

使い方:
  python tools/battle_state_reader.py                # 全 port
  python tools/battle_state_reader.py 8888           # 指定 port のみ
  python tools/battle_state_reader.py --watch        # 1 秒毎 streaming

Reads:
- battle in_battle flag
- my pokemon: HP, max HP, level, species, PP (4 moves)
- enemy pokemon: HP, max HP, level, species, type
- move selection cursor
- player party (6 slots: HP, max HP, level)
"""
import socket
import sys
import time
import json

TERM = b"<|END|>"

# Address table (JP Pokemon Emerald, from pokemon_env.py)
ADDR_BATTLE_MON_BASE = 0x02023D28
ADDR_MOVE_CURSOR     = 0x02024154
PARTY_BASE           = 0x02024190
MON_SIZE             = 100
BATTLE_MON_SIZE      = 88
# BattleMon offsets (within battle struct)
BMON_OFF_SPECIES     = 0x00
BMON_OFF_HP          = 0x28
BMON_OFF_MAXHP       = 0x2C
BMON_OFF_LEVEL       = 0x2A
BMON_OFF_PP          = 0x24
BMON_OFF_TYPES       = 0x21
# Party offset (within party mon struct)
PARTY_OFF_HP         = 0x56
PARTY_OFF_MAXHP      = 0x58
PARTY_OFF_LEVEL      = 0x54


def connect(port: int):
    s = socket.socket()
    s.settimeout(3)
    s.connect(("127.0.0.1", port))
    return s


def raw(s, cmd: str) -> str:
    s.sendall(cmd.encode() + TERM)
    buf = b""
    while TERM not in buf:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf.split(TERM)[0].decode(errors="replace")


def read8(s, addr: int) -> int:
    try:
        return int(raw(s, f"core.read8,{addr}"))
    except Exception:
        return 0


def read16(s, addr: int) -> int:
    try:
        return int(raw(s, f"core.read16,{addr}"))
    except Exception:
        return 0


def read_battle_state(s) -> dict:
    """Read all battle-related RAM and return as semantic dict."""
    my_hp = read16(s, ADDR_BATTLE_MON_BASE + BMON_OFF_HP)
    my_mhp = read16(s, ADDR_BATTLE_MON_BASE + BMON_OFF_MAXHP)
    my_lv = read8(s, ADDR_BATTLE_MON_BASE + BMON_OFF_LEVEL)
    my_sp = read16(s, ADDR_BATTLE_MON_BASE + BMON_OFF_SPECIES)
    my_pp = [read8(s, ADDR_BATTLE_MON_BASE + BMON_OFF_PP + i) for i in range(4)]

    en_base = ADDR_BATTLE_MON_BASE + BATTLE_MON_SIZE
    en_hp = read16(s, en_base + BMON_OFF_HP)
    en_mhp = read16(s, en_base + BMON_OFF_MAXHP)
    en_lv = read8(s, en_base + BMON_OFF_LEVEL)
    en_sp = read16(s, en_base + BMON_OFF_SPECIES)
    en_t1 = read8(s, en_base + BMON_OFF_TYPES)
    en_t2 = read8(s, en_base + BMON_OFF_TYPES + 1)

    cursor = read8(s, ADDR_MOVE_CURSOR)

    # Party
    party = []
    for i in range(6):
        base = PARTY_BASE + i * MON_SIZE
        party.append({
            "lv": read8(s, base + PARTY_OFF_LEVEL),
            "hp": read16(s, base + PARTY_OFF_HP),
            "mhp": read16(s, base + PARTY_OFF_MAXHP),
        })

    in_battle = my_mhp > 0 and my_hp >= 0 and my_lv > 0
    return {
        "in_battle": in_battle,
        "my": {"sp": my_sp, "lv": my_lv, "hp": my_hp, "mhp": my_mhp, "pp": my_pp},
        "enemy": {"sp": en_sp, "lv": en_lv, "hp": en_hp, "mhp": en_mhp,
                  "type": [en_t1, en_t2]},
        "move_cursor": cursor,
        "party": party,
    }


def main():
    args = sys.argv[1:]
    watch = "--watch" in args
    ports = [int(a) for a in args if a.isdigit()] or [8888, 8889, 8890]

    while True:
        print(f"=== {time.strftime('%H:%M:%S')} ===")
        for port in ports:
            try:
                s = connect(port)
                state = read_battle_state(s)
                s.close()
                # compact summary
                m = state["my"]
                e = state["enemy"]
                p_lvs = [p["lv"] for p in state["party"]]
                inb = "BATTLE" if state["in_battle"] else "field"
                print(f"  {port} [{inb}] my=sp{m['sp']}Lv{m['lv']} {m['hp']}/{m['mhp']} "
                      f"PP={m['pp']} | enemy=sp{e['sp']}Lv{e['lv']} {e['hp']}/{e['mhp']} "
                      f"type={e['type']} | cur={state['move_cursor']} | party_lv={p_lvs}")
            except Exception as ex:
                print(f"  {port} ERROR: {ex}")
        if not watch:
            break
        time.sleep(1)


if __name__ == "__main__":
    main()
