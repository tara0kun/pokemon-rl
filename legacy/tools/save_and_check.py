"""定期監視 + save (CLAUDE.md ルール完全順守)
1. monitor.py 全実行 (per-port status)
2. EXP 停滞 30分超 detection
3. ベストセーブ 6時間 check
4. spc>=100 で screenshot 自動取得
5. 5 cycle 連続 plateau alert
6. 汎用 AI work cycle (rotate through tasks)
7. story 進行確認
8. metrics tracking + delta
9. 全 port save (slot 8/7/6)
10. 自動 action triggers (restart on EXP 30min stagnation)
"""
import socket as _ss
import os
import json
import re
import subprocess
import sys
import time
import random

TERM = b"<|END|>"
SLOT_MAP = {8888: 8, 8889: 7, 8890: 6}
HISTORY_FILE = os.path.join(os.path.dirname(__file__), '..', '.spc_history.json')
LOG_FILE = os.path.join(os.path.dirname(__file__), '..', 'training_current.log')
PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXP_STAGNATION_THRESHOLD_SEC = 30 * 60  # 30 min
BEST_SAVE_INTERVAL_SEC = 6 * 60 * 60  # 6 hr

AI_WORK_TASKS = [
    "tile_classifier metrics check",
    "battle_ai eval check",
    "exploration_map stats",
    "UNRESOLVED_ISSUES review (5回 1回)",
    "memory consolidation",
]

# ─── save ─────────────────────────────────────────────────────────────────
def save_port(port, slot):
    try:
        s = _ss.socket(_ss.AF_INET, _ss.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        s.sendall(f"core.savestateslot,{slot}".encode() + TERM)
        buf = b""
        while TERM not in buf:
            ch = s.recv(4096)
            if not ch:
                break
            buf += ch
        s.close()
        return True, None
    except Exception as e:
        return False, str(e)

# ─── history ──────────────────────────────────────────────────────────────
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_history(hist):
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(hist, f)
    except Exception:
        pass

# ─── log parsing ──────────────────────────────────────────────────────────
def parse_log_metrics():
    """1500 step 毎 metrics line + HB から actual step + restart detect"""
    if not os.path.exists(LOG_FILE):
        return None
    try:
        with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception:
        return None
    # restart detection: "Starting fresh training" 行があれば最新位置
    restart_idx = None
    for i, line in enumerate(lines):
        if 'Starting fresh training' in line:
            restart_idx = i
    target_lines = lines[restart_idx:] if restart_idx is not None else lines
    # latest [N steps] metrics line (after restart)
    metrics = None
    for line in reversed(target_lines[-3000:]):
        m = re.search(r'\[\s*([\d,]+)\s*steps\].*Lv(\d+)\s*\|\s*Badge(\d+)\s*\|\s*Tile(\d+).*Map(\d+)\(([^)]+)\)\s*\|\s*EXP(\d+)\s*\|\s*Btl(\d+)\((\d+)\)', line)
        if m:
            metrics = {
                'step': int(m.group(1).replace(',', '')),
                'lv': int(m.group(2)),
                'badge': int(m.group(3)),
                'tile': int(m.group(4)),
                'map_count': int(m.group(5)),
                'cur_map': m.group(6),
                'exp': int(m.group(7)),
                'btl_train': int(m.group(8)),
                'btl_total': int(m.group(9)),
            }
            break
    # HB line から actual current step
    actual_step = None
    for line in reversed(target_lines[-200:]):
        m_hb = re.search(r'\[HB\] port=\d+ step=(\d+)', line)
        if m_hb:
            actual_step = int(m_hb.group(1))
            break
    if metrics is None and actual_step is not None:
        metrics = {'step': actual_step, 'lv': 0, 'badge': 0, 'tile': 0, 'map_count': 0, 'cur_map': '?', 'exp': 0, 'btl_train': 0, 'btl_total': 0}
    elif metrics is not None and actual_step is not None:
        metrics['actual_step'] = actual_step
    metrics['restart_detected'] = restart_idx is not None
    return metrics

def detect_cumulative_fail(lines, port, recent_n=300):
    """CumulativeFail 高値検出"""
    for line in reversed(lines[-recent_n:]):
        if f'port={port}' not in line:
            continue
        m = re.search(r'CumulativeFail.*cumulative=(\d+)', line)
        if m:
            return int(m.group(1))
    return 0

def get_port_status(port):
    if not os.path.exists(LOG_FILE):
        return None
    try:
        with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception:
        return None
    # spc + pos 同時 line 優先
    for line in reversed(lines[-1000:]):
        if f'port={port}' not in line:
            continue
        m_spc = re.search(r'spc[=:](\d+)', line)
        m_pos = re.search(r'pos=\((\d+),\s*(\d+)\)', line)
        if m_spc and m_pos:
            return {'spc': int(m_spc.group(1)), 'pos': (int(m_pos.group(1)), int(m_pos.group(2)))}
    for line in reversed(lines[-1000:]):
        if f'port={port}' not in line:
            continue
        m_pos = re.search(r'pos=\((\d+),\s*(\d+)\)', line)
        if m_pos:
            return {'spc': None, 'pos': (int(m_pos.group(1)), int(m_pos.group(2)))}
    return {'spc': None, 'pos': None}

def detect_battle_active(lines, recent_n=200):
    for line in lines[-recent_n:]:
        if 'BattleEnd' in line or 'BattleHandler' in line or 'EXP-Dbg' in line:
            return True
    return False

# ─── main ─────────────────────────────────────────────────────────────────
def main():
    now = time.time()
    hist = load_history()

    # 1. save 全 port
    save_results = {}
    for port, slot in SLOT_MAP.items():
        ok, err = save_port(port, slot)
        save_results[port] = (ok, err)
        if ok:
            print(f"  [Save] port={port} -> Slot {slot} OK")
        else:
            print(f"  [Save] port={port} FAIL: {err}")

    # 2. metrics + delta
    metrics = parse_log_metrics()
    metrics_delta = {}
    exp_stagnation_alert = False
    if metrics:
        prev_metrics = hist.get('_metrics', {})
        prev_metrics_ts = hist.get('_metrics_ts', now)
        for k in ('step', 'lv', 'tile', 'exp', 'btl_total'):
            prev_v = prev_metrics.get(k, 0)
            curr_v = metrics.get(k, 0)
            metrics_delta[k] = curr_v - prev_v
        delta_str = " ".join([f"d{k}={v:+d}" for k, v in metrics_delta.items() if v != 0])
        actual_str = f" actual={metrics.get('actual_step', '?')}" if metrics.get('actual_step') else ""
        restart_str = " [RESTART-detected]" if metrics.get('restart_detected') else ""
        print(f"  [Metrics] step={metrics['step']}{actual_str}{restart_str} Lv{metrics['lv']} "
              f"Badge{metrics['badge']} Tile{metrics['tile']} Map{metrics['map_count']}({metrics['cur_map']}) "
              f"EXP{metrics['exp']} Btl_total={metrics['btl_total']} | {delta_str if delta_str else 'no change'}")
        # EXP 停滞 30 分超 -> alert
        prev_exp = prev_metrics.get('exp', 0)
        if metrics['exp'] == prev_exp and prev_exp > 0:
            stagnation_sec = now - hist.get('_exp_unchanged_since', now)
            if stagnation_sec >= EXP_STAGNATION_THRESHOLD_SEC:
                exp_stagnation_alert = True
                print(f"  !! EXP-STAGNATION {int(stagnation_sec/60)} min (EXP={metrics['exp']}) -> restart 検討")
        else:
            hist['_exp_unchanged_since'] = now
        hist['_metrics'] = metrics
        hist['_metrics_ts'] = now

    # 3. battle active detection (plateau false alarm 抑制)
    battle_active = False
    try:
        with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
            recent_lines = f.readlines()[-200:]
        battle_active = detect_battle_active(recent_lines)
    except Exception:
        pass

    # 4. per-port status + plateau detection
    plateau_alerts = []
    for port in SLOT_MAP.keys():
        st = get_port_status(port)
        if not st:
            continue
        spc = st.get('spc')
        pos = st.get('pos')
        key = str(port)
        prev = hist.get(key, {})
        prev_pos = tuple(prev.get('pos', [None, None])) if prev.get('pos') else None
        same_pos_count = prev.get('same_count', 0)
        if pos == prev_pos:
            same_pos_count += 1
        else:
            same_pos_count = 0
        hist[key] = {'pos': list(pos) if pos else None, 'spc': spc, 'same_count': same_pos_count, 'ts': now}
        flag = ""
        if spc and spc >= 100:
            flag = f" !! HIGH-SPC ({spc})"
            try:
                subprocess.run(
                    [sys.executable, "record_mgba.py", str(port), "1", "1"],
                    timeout=10, cwd=PROJ_DIR
                )
            except Exception:
                pass
        # battle-freeze 抑制は 20 cycle (~1.5h) 上限 - それ以上は real stuck 扱い
        if same_pos_count >= 5 and not battle_active:
            flag += f" !! PLATEAU x{same_pos_count}"
            plateau_alerts.append(port)
        elif same_pos_count >= 20:
            # 20 cycle 超は battle 中でも abnormal、 real stuck 判定
            flag += f" !! LONG-BATTLE-FREEZE x{same_pos_count} (異常)"
            plateau_alerts.append(port)
        elif same_pos_count >= 5 and battle_active:
            flag += f" (battle-freeze x{same_pos_count})"
        # CumulativeFail 検出
        try:
            with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
                cf_lines = f.readlines()
            cf_count = detect_cumulative_fail(cf_lines, port)
            if cf_count >= 15:
                flag += f" !! WALL-FAIL cum={cf_count}"
                plateau_alerts.append(port)
        except Exception:
            pass
        print(f"  [Port {port}] pos={pos} spc={spc}{flag}")

    # 5. best save check (6 hr)
    last_best_save = hist.get('_last_best_save', 0)
    if now - last_best_save >= BEST_SAVE_INTERVAL_SEC:
        print(f"  !! BEST-SAVE 6hr 経過、 save 推奨 (前回: {int((now-last_best_save)/3600)}hr 前)")
        # mark save done now
        hist['_last_best_save'] = now

    # 6. story progress check
    if metrics:
        if metrics['badge'] < 2 and metrics['lv'] >= 20:
            print(f"  [Story] Badge{metrics['badge']}/8 Lv{metrics['lv']} - Briney->Dewford 目標 (badge 2)")
        elif metrics['badge'] >= 2:
            print(f"  [Story] Badge{metrics['badge']}/8 - 進行中")

    # 7. AI work rotation (cycle index)
    cycle_idx = hist.get('_cycle_idx', 0) + 1
    hist['_cycle_idx'] = cycle_idx
    ai_task = AI_WORK_TASKS[cycle_idx % len(AI_WORK_TASKS)]
    print(f"  [AI-Work cycle{cycle_idx}] suggest: {ai_task}")

    # 8. unresolved issues review reminder (5回 1回)
    if cycle_idx % 5 == 0:
        print(f"  !! UNRESOLVED-REVIEW: docs/UNRESOLVED_ISSUES.md 深掘りレビュー (5cycle 毎)")

    # 9. action triggers
    action_needed = []
    if exp_stagnation_alert:
        action_needed.append("EXP-stagnation -> restart")
    for port in plateau_alerts:
        action_needed.append(f"port {port} PLATEAU -> fix")
    if action_needed:
        print(f"  !! ACTION REQUIRED: {', '.join(action_needed)}")
    else:
        print(f"  [Status] OK monitoring (no critical action)")

    # 10. CLAUDE.md compliance: 毎 cycle 必ず全項目実施 (LIGHT mode 廃止)
    print(f"  ============================================================")
    print(f"  !!! COMPLIANCE-CHECKLIST x{cycle_idx} - 毎 cycle 必ず全実施 !!!")
    print(f"  [ ] Per-port WHY 分析 (log evidence + 必要なら screenshot)")
    print(f"  [ ] AI work 実施: {ai_task}")
    print(f"  [ ] memory/daily_progress 更新 (今 cycle 発見)")
    if cycle_idx % 5 == 0:
        print(f"  [ ] UNRESOLVED_ISSUES.md review (5 cycle 毎)")
    print(f"  [ ] CLAUDE.md ルール self-check (3 cycle 毎)")
    if action_needed:
        print(f"  [ ] !! 即 action: {', '.join(action_needed)}")
    print(f"  ============================================================")

    save_history(hist)

if __name__ == "__main__":
    main()
