"""
監視ヘルパースクリプト — 各ポートの状態を構造化表示

使い方:
  poke-rl/Scripts/python.exe tools/monitor.py

出力: 各ポートのmap, pos, nav_target, spc, PP, FA, WIN, 問題フラグ
"""

import re
import sys
import subprocess
from collections import defaultdict

LOG_FILE = "training_current.log"


def check_train_alive():
    """★ v261am v2: train.py プロセス生存確認 (自己 PID 除外)
    wmic CommandLine で train.py 文字列含むプロセスをカウント
    """
    try:
        import os as _os
        _self_pid = _os.getpid()
        r = subprocess.run(
            ["powershell", "-Command",
             "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | "
             "Select-Object ProcessId, CommandLine | Format-List"],
            capture_output=True, text=True, timeout=10
        )
        train_count = 0
        cur_pid = None
        for ln in r.stdout.splitlines():
            ln = ln.strip()
            if ln.startswith("ProcessId"):
                try:
                    cur_pid = int(ln.split(":")[-1].strip())
                except Exception:
                    cur_pid = None
            elif ln.startswith("CommandLine") and cur_pid:
                cmd = ln.split(":", 1)[-1].strip().lower()
                if "train.py" in cmd and cur_pid != _self_pid:
                    train_count += 1
                cur_pid = None
        return train_count > 0, train_count
    except Exception:
        return True, -1

def parse_log():
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print("ERROR: training_current.log not found")
        sys.exit(1)

    # Per-port state - initialize with defaults
    ports = {
        8888: {'mg': -1, 'mn': -1, 'x': 0, 'y': 0, 'nav': '?', 'spc': 0, 'pp': '?', 'fa': 0, 'osc': 'False', 'mega': 'False'},
        8889: {'mg': -1, 'mn': -1, 'x': 0, 'y': 0, 'nav': '?', 'spc': 0, 'pp': '?', 'fa': 0, 'osc': 'False', 'mega': 'False'},
        8890: {'mg': -1, 'mn': -1, 'x': 0, 'y': 0, 'nav': '?', 'spc': 0, 'pp': '?', 'fa': 0, 'osc': 'False', 'mega': 'False'},
    }

    # Global counters
    total_win = 0
    total_enc = 0
    total_fa = 0
    total_fight = 0
    total_run = 0
    last_step = 0
    # ★ 追加: SLOW-STEP出現数 / 最近のEXP停滞 / 進化検出 / mGBA hung
    slow_step_count = 0
    evolution_count = 0
    watchdog_hung = 0

    # ★ 直近200行だけでrecent_battleフラグを判定するため、2パス方式
    recent_battle_ports = set()
    recent_slow_count = {8888: 0, 8889: 0, 8890: 0}
    for line in lines[-200:]:
        if ("BattleHandler" in line or "FIGHT-Move" in line
                or "DMG-PP-Struggle" in line):
            pm = re.search(r'port=(\d+)', line)
            if pm:
                recent_battle_ports.add(int(pm.group(1)))
        if "SLOW-STEP" in line:
            pm = re.search(r'port=(\d+)', line)
            if pm:
                _sp = int(pm.group(1))
                if _sp in recent_slow_count:
                    recent_slow_count[_sp] += 1
    for _sp, _cnt in recent_slow_count.items():
        if _sp in ports:
            ports[_sp]['recent_slow'] = _cnt

    for line in lines:
        # SLOW-STEP counter
        if "SLOW-STEP" in line:
            slow_step_count += 1
        # Evolution detection (game text or state)
        if "Evolution" in line or "Evolve" in line or "しんか" in line:
            evolution_count += 1
        # mGBA hung detection
        if "WATCHDOG" in line and "hung" in line:
            watchdog_hung += 1
        # WIN count — ★ v258: BH-WIN-AltSlot を除外 (二重カウント防止)
        # 旧: "BH-WIN" が AltSlot 行にもマッチ → 1 win = 2 count
        if ("BH-WIN]" in line or "Battle] WIN" in line) and "AltSlot" not in line:
            total_win += 1

        # Encounter count
        if "Encounter] Battle #" in line:
            total_enc += 1

        # FA count
        if "FalseAlarm" in line or "ForcedSwap-Stale" in line:
            total_fa += 1

        # FIGHT/RUN
        if "FIGHT act=" in line:
            total_fight += 1
        if "RUN act=" in line:
            total_run += 1

        # Per-port: extract port number
        port_match = re.search(r'port=(\d+)', line)
        if not port_match:
            continue
        port = int(port_match.group(1))
        if port not in ports:
            continue
        p = ports[port]
        p['recent_battle'] = port in recent_battle_ports

        # HB line: step and PP
        hb_match = re.search(r'\[HB\] port=\d+ step=(\d+).*PP=[\[\(]?([\d,/ ]+)', line)
        if hb_match:
            p['step'] = int(hb_match.group(1))
            last_step = max(last_step, p['step'])
            pp_str = hb_match.group(2).replace('/', ',').replace(' ', '')
            p['pp'] = pp_str

        # NavGuard: nav_target, spc, fa, mega
        nav_match = re.search(r'\[NavGuard\] nav_t=(\S*) osc=(\S+) mega=(\S+) bh=(\S+) fa=(\d+).*spc=(\d+)', line)
        if nav_match:
            p['nav'] = nav_match.group(1) or '(empty)'
            p['osc'] = nav_match.group(2)
            p['mega'] = nav_match.group(3)
            p['fa'] = int(nav_match.group(5))
            p['spc'] = int(nav_match.group(6))

        # FINAL: map and position
        final_match = re.search(r'\[FINAL\] act=\d+ mg=(\d+) mn=(\d+) x=(\d+) y=(\d+)', line)
        if final_match:
            _mg_f = int(final_match.group(1))
            _mn_f = int(final_match.group(2))
            _x_f = int(final_match.group(3))
            _y_f = int(final_match.group(4))
            # ★ v10.9z256: (0,0) Littleroot で x>=25 or y>=25 は stale RAM 過渡読みの可能性
            #   Littleroot は 20x20 以下、それ超えは map transition 中の stale
            #   非stale値が既にあればそれを優先、なければ stale も許容(default回避)
            _stale_littleroot = (_mg_f == 0 and _mn_f == 0 and (_x_f >= 25 or _y_f >= 25))
            if _stale_littleroot and p.get('mg', -1) >= 0 and not (p.get('mg') == 0 and p.get('mn') == 0):
                pass  # 既に正当な map 情報がある → skip stale
            else:
                p['mg'] = _mg_f
                p['mn'] = _mn_f
                p['x'] = _x_f
                p['y'] = _y_f

        # MapChange
        mc_match = re.search(r'\[MapChange\].*pos=\((\d+),(\d+)\).*port=(\d+)', line)
        if mc_match and int(mc_match.group(3)) == port:
            pass  # FINAL already captures position

        # KanazumiForceExit
        if "KanazumiForceExit" in line:
            stay_match = re.search(r'stay=(\d+)', line)
            if stay_match:
                p['kz_stay'] = int(stay_match.group(1))

        # PC-Door
        if "PC-Door] nav" in line:
            p['pc_door'] = True

        # HealCorridor-Escape
        if "HealCorridor-Escape" in line:
            p['hce'] = True

        # MegaStuck
        if "MegaStuck]" in line and "pos=" in line:
            stuck_match = re.search(r'stuck=(\d+)', line)
            if stuck_match:
                p['mega_stuck'] = int(stuck_match.group(1))

        # WeakSlot0-Swap count
        if "WeakSlot0-Swap" in line:
            p['ws_count'] = p.get('ws_count', 0) + 1
        if "WS-Case2-Miss" in line:
            p['ws2_miss'] = p.get('ws2_miss', 0) + 1

        # GenericStayExit tracking
        gse_match = re.search(r'\[GenericStayExit\].*stay=(\d+)', line)
        if gse_match:
            p['gse_stay'] = int(gse_match.group(1))

        # MapChange loop detection (same pair repeated)
        if "[MapChange]" in line:
            mc_pair = re.search(r'\((\d+,\d+)\).*\((\d+,\d+)\)', line)
            if mc_pair:
                _pair = (mc_pair.group(1), mc_pair.group(2))
                _prev_pair = p.get('_mc_prev', None)
                if _prev_pair and _pair == _prev_pair:
                    p['mc_loop'] = p.get('mc_loop', 0) + 1
                else:
                    p['mc_loop'] = 0
                p['_mc_prev'] = _pair

        # Ralts EXP tracking — OW-EXP-Sync と PostWIN-EXP を優先
        ow_exp = re.search(r'\[OW-EXP-Sync\].*→(\d+)', line)
        if ow_exp:
            p['ralts_exp'] = int(ow_exp.group(1))
        pw_exp = re.search(r'\[PostWIN-EXP\].*EXP=(\d+) maxHP=2[0-9]', line)
        if pw_exp:
            p['ralts_exp'] = int(pw_exp.group(1))
        bh_exp = re.search(r'\[BH-WIN\].*\bs0 EXP:\d+\D+(\d+)', line)
        if bh_exp:
            p['ralts_exp'] = int(bh_exp.group(1))
        # Fallback: EXP-Dbg (バトル中読み、swap の影響あり)
        if 'ralts_exp' not in p:
            exp_match = re.search(r'\[EXP-Dbg\].*fresh=(\d+)', line)
            if exp_match:
                _exp_val = int(exp_match.group(1))
                if 0 < _exp_val < 5000:
                    p['ralts_exp'] = _exp_val

        # recent_battle は上の2パス目で設定済

        # Party Levels
        level_match = re.search(r'\[Party\] Levels: (.*)', line)
        if level_match:
            p['levels'] = level_match.group(1).strip()
            # slot0 (Ralts等) Lv抽出 — Lv0は無視(読み取りレース残存)
            _sl0 = re.search(r'Lv(\d+)', p['levels'])
            if _sl0 and int(_sl0.group(1)) > 0:
                p['slot0_lv'] = int(_sl0.group(1))

    return (ports, total_win, total_enc, total_fa, total_fight, total_run,
            last_step, slow_step_count, evolution_count, watchdog_hung)


def map_name(mg, mn):
    names = {
        (0, 31): "R116",
        (0, 3): "Kanazumi",
        (0, 30): "Route030",
        (0, 0): "Littleroot",
        (11, 5): "KanazumiPC",
        (11, 3): "KanazumiGym",
        (11, 4): "KanazumiBuilding",
        (11, 12): "KanazumiBuilding2",
        (24, 4): "RusturfTunnel",
        (24, 11): "PetalburgForest",
    }
    return names.get((mg, mn), f"({mg},{mn})")


def check_problems(p):
    """各ポートの問題を検出"""
    problems = []

    mg = p.get('mg', -1)
    mn = p.get('mn', -1)
    spc = p.get('spc', 0)
    nav = p.get('nav', '')

    # カナズミに長時間滞在
    if mg == 0 and mn == 3 and spc >= 100:
        problems.append(f"!! Kanazumi spc={spc}")

    # KanazumiForceExit高値（R116上では無視）
    kz = p.get('kz_stay', 0)
    _on_r116 = (mg == 0 and mn == 31)
    if kz >= 200 and not _on_r116:
        problems.append(f"!! KZ-ForceExit stay={kz}")

    # MegaStuck
    ms = p.get('mega_stuck', 0)
    if ms >= 200:
        problems.append(f"!! MegaStuck={ms}")

    # PC-Door永久ループ
    if p.get('pc_door') and spc >= 100:
        problems.append(f"!! PC-Door spc={spc}")

    # R116境界バウンス (x<=8)
    if mg == 0 and mn == 31 and p.get('x', 99) <= 8:
        problems.append(f"!! R116 west edge x={p.get('x')}")

    # OscTrap (バトル中は除外、真のナビスタックのみ)
    if (p.get('osc') == 'True' and spc >= 50
            and not p.get('recent_battle', False)):
        problems.append(f"!! OscTrap spc={spc}")

    # FA高値 (バトル中は _false_alarm_nav_block 回復カウンタで誤検出)
    fa = p.get('fa', 0)
    if fa >= 20 and not p.get('recent_battle', False):
        problems.append(f"!! FA={fa}")

    # Route030ループ
    if mg == 0 and mn == 30:
        problems.append("!! Route030 (wrong area)")

    # ★ v261eh: 想定外マップ検出 (現在 Badge1 / R116 leveling stage)
    # Expected: R116, Kanazumi, KanazumiPC, KanazumiGym, KanazumiBuilding, 民家(warp accidental)
    # Devon Corp / RusturfTunnel は story進行で expected
    EXPECTED_MAPS = {
        (0, 31),   # R116 (現 leveling area)
        (0, 3),    # Kanazumi (heal trip経由)
        (11, 5),   # KanazumiPC (heal完了地)
        (11, 3),   # KanazumiGym (Badge1取得済)
        (11, 4),   # Kanazumi Building (transit)
        (11, 12),  # Kanazumi Building 2 (transit)
        (11, 10),  # 民家 (warp accidental、 v261eg対象)
        (11, 8),   # 別建物 (warp accidental)
        (11, 1),   # Devon Corp 1F (story進行先)
        (24, 4),   # RusturfTunnel (story進行先)
        (-1, -1),  # default (RAM未取得)
    }
    if mg >= 0 and (mg, mn) not in EXPECTED_MAPS:
        # (0,0) Littleroot は stale RAM 可能性既存処理 → 警告だけ
        if not (mg == 0 and mn == 0 and (p.get('x', 0) >= 25 or p.get('y', 0) >= 25)):
            problems.append(f"!! UnexpectedMap=({mg},{mn}) {map_name(mg, mn)}")

    # ★ カナシダトンネル長期滞在 (mapping or attack failure)
    if mg == 24 and mn == 4 and spc >= 150:
        problems.append(f"!! RusturfTunnel stuck spc={spc}")

    # ★ 任意マップで超長期スタック (一般的フォールバック)
    if spc >= 300:
        problems.append(f"!! LongStuck spc={spc}")

    # ★ ジム内長期滞在 (11,3=カナズミジム、Badge取得後は滞留しない)
    if mg == 11 and mn == 3 and spc >= 150:
        problems.append(f"!! GymStuck spc={spc}")

    # PP全枯渇で放置
    pp_str = p.get('pp', '')
    if pp_str and pp_str not in ('?', ''):
        try:
            pp_vals = [int(x) for x in pp_str.split(',')]
            if sum(pp_vals) == 0:
                problems.append("!! PP全枯渇")
        except ValueError:
            pass

    # nav_targetが不適切
    if nav == 'kanazumi_gym':
        problems.append("!! GymNav(Badge取得済?)")

    # GenericStayExit 発火中
    gse = p.get('gse_stay', 0)
    if gse >= 300:
        problems.append(f"!! GenericStayExit stay={gse}")

    # MapChange ループ検出 (Kanazumi↔Route030)
    mc_loop = p.get('mc_loop', 0)
    if mc_loop >= 3:
        problems.append(f"!! MapChange loop={mc_loop}")

    # 連続 SLOW-STEP (直近で頻発)
    recent_slow = p.get('recent_slow', 0)
    if recent_slow >= 10:
        problems.append(f"!! SLOW-STEP burst={recent_slow}")

    # R116にいるのにnav_targetがroute116 — R116内部のBFS目的地なので正常
    # (削除: 誤検出だった)

    return problems


def main():
    # ★ v261au: train.py 自動再起動 (CRITICAL 検出時)
    alive, py_count = check_train_alive()
    if not alive:
        print(f"{'='*60}")
        print(f"  !!! CRITICAL: train.py プロセス消失! 自動再起動中...")
        print(f"{'='*60}")
        try:
            import os as _amos
            _proj = _amos.path.dirname(_amos.path.dirname(_amos.path.abspath(__file__)))
            subprocess.run([sys.executable, "tools/restart_training.py"],
                           timeout=30, cwd=_proj)
            print(f"  !!! 自動再起動完了")
            alive = True
        except Exception as _ae:
            print(f"  !!! 自動再起動失敗: {_ae}")

    (ports, wins, encs, fas, fights, runs, step,
     slow_count, evo_count, watchdog_count) = parse_log()

    print(f"{'='*60}")
    print(f"  Step {step} | WIN {wins} | Enc {encs} | FA {fas}")
    print(f"  FIGHT {fights} / RUN {runs} | SLOW-STEP {slow_count} | Evo {evo_count}")
    if not alive:
        print(f"  !! train.py DEAD (python.exe count={py_count})")
    # ★ Ralts/slot0 progress — max across ports (ignore Lv=0 race, ignore stale Blaziken lv>=30)
    _max_ralts = 0
    for _p in ports.values():
        _lv = _p.get('slot0_lv', 0)
        # Ralts/Kirlia系: Lv 1-29 を想定。Lv30+ はBlazikenローテーション時なので除外
        if 1 <= _lv < 30 and _lv > _max_ralts:
            _max_ralts = _lv
    if _max_ralts > 0:
        print(f"  Ralts/slot0 progress: Lv{_max_ralts} (target Lv16, goal Devon Corp)")
    print(f"{'='*60}")
    # ★ グローバル警告
    if slow_count >= 30:
        print(f"  !! GLOBAL: SLOW-STEP multiple ({slow_count}) - check python procs")
    if evo_count > 0:
        print(f"  ** Evolution detected ({evo_count}) - verify animation not interrupted")
    if watchdog_count > 0:
        print(f"  !! GLOBAL: WATCHDOG hung {watchdog_count} times - mGBA may be stuck")

    all_ok = True
    for port_num in [8888, 8889, 8890]:
        p = ports[port_num]
        mg = p.get('mg', '?')
        mn = p.get('mn', '?')
        mname = map_name(mg, mn) if isinstance(mg, int) else '?'
        x = p.get('x', '?')
        y = p.get('y', '?')
        nav = p.get('nav', '?')
        spc = p.get('spc', '?')
        pp = p.get('pp', '?')
        fa = p.get('fa', 0)

        problems = check_problems(p)
        status = "!! PROBLEM" if problems else "OK"
        if problems:
            all_ok = False

        print(f"\n  Port {port_num}: {status}")
        print(f"    Map: {mname} pos=({x},{y})")
        print(f"    nav_t: {nav} | spc: {spc} | fa: {fa}")
        print(f"    PP: [{pp}]")
        if p.get('levels'):
            _sl0 = p.get('slot0_lv')
            _sl0_str = f" [slot0=Lv{_sl0}]" if _sl0 else ""
            print(f"    Party: {p['levels']}{_sl0_str}")
        _rexp = p.get('ralts_exp')
        _wsc = p.get('ws_count', 0)
        _ws2m = p.get('ws2_miss', 0)
        if _rexp or _wsc or _ws2m:
            _rexp_str = f"EXP={_rexp}" if _rexp else "EXP=?"
            _miss_str = f" C2miss={_ws2m}" if _ws2m else ""
            print(f"    Ralts: {_rexp_str} | WS-Swap={_wsc}{_miss_str}")
        for prob in problems:
            print(f"    {prob}")

    print(f"\n{'='*60}")
    # ★ mGBA プロセス数チェック (3 でなければ警告)
    try:
        import subprocess as _sp
        _r = _sp.run(["tasklist"], capture_output=True, text=True, timeout=5)
        _mgba_count = _r.stdout.lower().count("mgba.exe")
        if _mgba_count != 3:
            print(f"  !! GLOBAL: mGBA process count = {_mgba_count} (expected 3)")
            all_ok = False
    except Exception:
        pass
    # ★ v261e: ログサイズ警告 (50MB 超で再起動推奨)
    try:
        import os as _os
        if _os.path.exists("training_current.log"):
            _ls = _os.path.getsize("training_current.log")
            if _ls > 40 * 1024 * 1024:
                print(f"  !! GLOBAL: training_current.log = {_ls//1024//1024}MB (rotation @50MB)")
    except Exception:
        pass
    if all_ok:
        print("  ALL PORTS OK")
    else:
        print("  !! PROBLEMS DETECTED -- action required")
    print(f"{'='*60}")

    # ★ 進捗ロス防止: 最高EXPポートを Slot 8 に自動セーブ (10分毎監視時)
    _best_port = None
    _best_exp = 0
    for _pn in [8888, 8889, 8890]:
        _pp = ports[_pn]
        _re = _pp.get('ralts_exp', 0) or 0
        if _re > _best_exp:
            _best_exp = _re
            _best_port = _pn
    if _best_port and _best_exp > 0:
        try:
            import socket as _ss
            TERM = b"<|END|>"
            # ★ v261f: ポート別 Slot セーブ (競合防止)
            # 8888→Slot 8, 8889→Slot 7, 8890→Slot 6 (重複なし)
            # ★ v261h: スタック中のポートはスキップ (スタック状態の永続化防止)
            # ★ v261ar: Clean-Save 厳格化 (recurring stuck 対策)
            # 旧: spc>=200 or fa>=50 で skip (緩すぎ、stuck 半端状態で save)
            # 新: spc>=30 で skip (移動確実時のみ save)
            # さらに Clean Backup Slot 4 に別保存 — 非常時の手動復旧用
            _slot_map = {8888: 8, 8889: 7, 8890: 6}
            _skipped = []
            for _save_port, _slot in _slot_map.items():
                _pp_check = ports[_save_port]
                _spc_check = _pp_check.get('spc', 0)
                _fa_check = _pp_check.get('fa', 0)
                # v261aw: 10→50 に緩和 (正常移動中は save 許可、stuck のみ skip)
                # v261as の spc>=10 は厳しすぎ → 全 port save skip → progress ロスリスク
                if _spc_check >= 50 or _fa_check >= 50:
                    _skipped.append(f"{_save_port}(spc={_spc_check})")
                    continue
                s = _ss.socket(_ss.AF_INET, _ss.SOCK_STREAM)
                s.settimeout(3)
                s.connect(("127.0.0.1", _save_port))
                s.sendall(f"core.savestateslot,{_slot}".encode() + TERM)
                buf = b""
                while TERM not in buf:
                    ch = s.recv(4096)
                    if not ch:
                        break
                    buf += ch
                s.close()
            print(f"\n  [BestSave] Ports → independent slots (8888→8, 8889→7, 8890→6)")
            if _skipped:
                print(f"  [BestSave-Skip] Stuck ports: {_skipped}")
        except Exception as _e:
            print(f"\n  [BestSave] failed: {_e}")

    # ★ v10.9z262 (2026-05-08): cadence 自動 check (silent violation 再発防止)
    # daily_progress 12h、 汎用 AI work 24h、 UNRESOLVED_ISSUES 7d を自動 enforce
    import os as _cos
    import datetime as _cdt
    _cad_now = _cdt.datetime.now().timestamp()
    _cad_violations = []
    # daily_progress 12h check
    try:
        _dpd = "daily_progress"
        if _cos.path.exists(_dpd):
            _today = _cdt.date.today().isoformat()
            _today_md = _cos.path.join(_dpd, f"{_today}.md")
            if not _cos.path.exists(_today_md):
                # find latest dated file (skip non-dated)
                _md_files = [f for f in _cos.listdir(_dpd)
                             if f.endswith(".md") and len(f) == 13 and f[4] == "-"]
                if _md_files:
                    _md_files.sort()
                    _latest = _md_files[-1]
                    _lp = _cos.path.join(_dpd, _latest)
                    _age_h = (_cad_now - _cos.path.getmtime(_lp)) / 3600
                    if _age_h >= 12:
                        _cad_violations.append(
                            f"daily_progress: {_latest} は {_age_h:.0f}h 前 "
                            f"(today {_today}.md 未作成、 12h ルール違反)")
    except Exception as _e:
        _cad_violations.append(f"daily_progress check fail: {_e}")
    # 汎用 AI work cadence: history.json OR ai_work_marker のうち新しい方が 24h 以内
    try:
        _ai_files = ["tools/tile_classifier_history.json",
                     "tools/.ai_work_marker"]
        _ai_latest = 0
        for _af in _ai_files:
            if _cos.path.exists(_af):
                _mt = _cos.path.getmtime(_af)
                if _mt > _ai_latest:
                    _ai_latest = _mt
        if _ai_latest > 0:
            _age_h = (_cad_now - _ai_latest) / 3600
            if _age_h >= 24:
                _cad_violations.append(
                    f"AI work: 最終 {_age_h/24:.1f}d 前 "
                    f"(毎セッション ルール違反、 train/eval/collect 実行 → "
                    f"`touch tools/.ai_work_marker` で記録)")
    except Exception:
        pass
    # UNRESOLVED_ISSUES freshness (7d 経過で stale)
    try:
        _uip = "docs/UNRESOLVED_ISSUES.md"
        if _cos.path.exists(_uip):
            _age_h = (_cad_now - _cos.path.getmtime(_uip)) / 3600
            if _age_h >= 7 * 24:
                _cad_violations.append(
                    f"UNRESOLVED_ISSUES: {_age_h/24:.1f}d 未更新 "
                    f"(7d ルール、 深掘りレビュー + 現状反映必要)")
    except Exception:
        pass
    if _cad_violations:
        print()
        print("=" * 60)
        print("  !! CADENCE VIOLATIONS (CLAUDE.md ルール違反、 即対策)")
        print("=" * 60)
        for _v in _cad_violations:
            print(f"  !! {_v}")

    # Mandatory checklist
    print()
    print("=" * 60)
    print("  MANDATORY CHECKLIST (address every item)")
    print("=" * 60)
    if not all_ok:
        print("  [ ] PROBLEM -> screenshot (record_mgba.py)")
        print("  [ ] Root cause with DATA (not assumptions)")
        print("  [ ] Fix NOW (no 'next cycle')")
    print("  [ ] Log read? Each port WHY analyzed?")
    print("  [ ] Any stuck port? (spc>100, PP unchanged)")
    print("  [ ] EXP stagnation? (30min -> restart)")
    print("  [ ] Best save? (6hr -> save)")
    print("  [ ] AI dev task this cycle?")
    print("  [ ] Story progressing? (not leveling loop)")
    import os as _os
    _cf = "tools/.monitor_calls"
    _cc = 0
    try:
        if _os.path.exists(_cf):
            with open(_cf) as _f:
                _cc = int(_f.read().strip())
    except Exception:
        pass
    _cc += 1
    try:
        with open(_cf, "w") as _f:
            _f.write(str(_cc))
    except Exception:
        pass
    if _cc % 3 == 0:
        print("  [ ] !! RULE CHECK: Re-read CLAUDE.md")
    if _cc % 5 == 0:
        print("  [ ] !! FULL REVIEW: Party/strategy/AI/story")
    print("=" * 60)


if __name__ == "__main__":
    main()
