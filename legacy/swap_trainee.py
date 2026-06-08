"""★ v10.9z249: 適応型トレーニー先頭配置スクリプト
学習開始前に実行して、最適なトレーニーをslot0に配置する。
使い方: poke-rl/Scripts/python.exe swap_trainee.py [restore]
  - 引数なし: 最適なトレーニーをslot0に配置
  - restore: Blazikenをslot0に復帰
"""
import socket, time, sys, os

TERM = b"<|END|>"
PORTS = [8888, 8889, 8890]
PARTY_BASE = 0x02024190
MON_SIZE = 100
REC_LV = 16
ENEMY_AVG_LV = 7
SLOT0_MIN_LV = ENEMY_AVG_LV + 3  # Lv10+: slot0で安全に戦える
PASSIVE_SP = {392: 6}  # Ralts: Lv6で攻撃技習得

# Growth substructure slot table
GROWTH_SLOT = [0,0,0,0,0,0,1,1,2,3,2,3,1,1,2,3,2,3,1,1,2,3,2,3]

def connect(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(('127.0.0.1', port))
    return s

def raw(s, cmd):
    s.sendall(cmd.encode() + TERM)
    buf = b''
    while TERM not in buf:
        buf += s.recv(4096)
    return buf.split(TERM)[0].decode()

def press(s, btn):
    raw(s, f'mgba-http.button.hold,{btn},8')
    time.sleep(0.5)

def read_levels(s):
    levels = []
    for i in range(6):
        lv = int(raw(s, f'core.read8,{PARTY_BASE + i * MON_SIZE + 0x54}'))
        levels.append(lv)
    return levels

def read_species(s, slot):
    base = PARTY_BASE + slot * MON_SIZE
    pid = int(raw(s, f'core.read32,{base}'))
    otid = int(raw(s, f'core.read32,{base + 4}'))
    if pid == 0:
        return 0
    key = (pid ^ otid) & 0xFFFFFFFF
    gpos = GROWTH_SLOT[pid % 24]
    gdata = int(raw(s, f'core.read32,{base + 0x20 + gpos * 12}'))
    return ((gdata ^ key) & 0xFFFFFFFF) & 0xFFFF

def do_swap(s, target_slot):
    """ブルートフォースでslot target_slot とslot 0を入替"""
    levels = read_levels(s)
    old_lv0 = levels[0]

    # Clear menus + escape battle
    for _ in range(15): press(s, 'B')
    for _ in range(3):
        press(s, 'Down'); press(s, 'Right'); press(s, 'A')
    for _ in range(15): press(s, 'B')
    time.sleep(0.5)

    for attempt in range(8):
        press(s, 'Start'); time.sleep(0.3)
        press(s, 'A'); time.sleep(0.8)
        # Swap operations
        press(s, 'Right')
        for _ in range(target_slot - 1): press(s, 'Down')
        press(s, 'A'); time.sleep(0.5)
        press(s, 'Down')  # ならびかえ
        press(s, 'A'); time.sleep(0.5)
        press(s, 'Left')
        press(s, 'A'); time.sleep(0.5)
        for _ in range(10): press(s, 'B')
        time.sleep(0.3)

        new_lv0 = int(raw(s, f'core.read8,{PARTY_BASE + 0x54}'))
        if new_lv0 != old_lv0 and new_lv0 > 0:
            print(f'    OK at try {attempt}! Lv{old_lv0}->Lv{new_lv0}')
            return True

        # Fail: close, Start, Down for next item
        for _ in range(10): press(s, 'B')
        press(s, 'Start'); time.sleep(0.3)
        press(s, 'Down')
        for _ in range(5): press(s, 'B')

    print(f'    FAILED after 8 tries')
    return False

def main():
    restore_mode = len(sys.argv) > 1 and sys.argv[1] == 'restore'

    for port in PORTS:
        print(f'\n=== Port {port} ===')
        s = connect(port)
        levels = read_levels(s)
        print(f'  Levels: {levels}')

        if restore_mode:
            # Blazikenをslot0に復帰
            blaziken_slot = next((i for i, lv in enumerate(levels) if lv >= 40), -1)
            if blaziken_slot == 0:
                print(f'  Blaziken already in slot0')
            elif blaziken_slot > 0:
                print(f'  Restoring Blaziken from slot{blaziken_slot}')
                do_swap(s, blaziken_slot)
            else:
                print(f'  Blaziken not found!')
        else:
            # 適応型: 最適トレーニーをslot0に配置
            if levels[0] >= 40:
                # Blazikenがslot0 → slot0配置可能なトレーニーを探す
                best_slot = -1
                best_lv = 999
                for i in range(1, 6):
                    lv = levels[i]
                    if lv <= 0 or lv >= REC_LV:
                        continue
                    # PASSIVE check
                    sp = read_species(s, i)
                    if sp in PASSIVE_SP and lv < PASSIVE_SP[sp]:
                        continue
                    if lv >= SLOT0_MIN_LV and lv < best_lv:
                        best_lv = lv
                        best_slot = i

                if best_slot > 0:
                    print(f'  slot0 mode: slot{best_slot}(Lv{best_lv}) (can solo Lv{ENEMY_AVG_LV} enemies)')
                    do_swap(s, best_slot)
                else:
                    print(f'  bcc5 mode: no slot0 candidate >= Lv{SLOT0_MIN_LV}')
                    print(f'  Blaziken stays in slot0, TrainSwitch bcc=5 will handle leveling')
            else:
                # Trainee already in slot0
                if levels[0] >= SLOT0_MIN_LV:
                    print(f'  Trainee Lv{levels[0]} already in slot0 (can solo, OK)')
                else:
                    # Too weak for slot0, restore Blaziken
                    blaziken_slot = next((i for i, lv in enumerate(levels) if lv >= 40), -1)
                    if blaziken_slot > 0:
                        print(f'  Trainee Lv{levels[0]} too weak for slot0, restoring Blaziken')
                        do_swap(s, blaziken_slot)
                    else:
                        print(f'  Trainee Lv{levels[0]} in slot0, no Blaziken found')

        # Final state
        final_levels = read_levels(s)
        print(f'  Final: {final_levels}')
        s.close()

if __name__ == '__main__':
    main()
