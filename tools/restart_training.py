"""train.py の安全な再起動ヘルパー

使い方:
    poke-rl/Scripts/python.exe tools/restart_training.py

動作:
    1. 既存の train.py プロセスを全て確実に停止
    2. 重複がないことを確認
    3. 新しい train.py を起動
    4. 起動確認（ログ出力待ち）
"""
import subprocess
import time
import sys
import os

LOG_FILE = "training_current.log"


def find_train_pids():
    """train.py を実行中の python.exe PID を全て取得"""
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | "
             "Select-Object ProcessId, CommandLine | Format-List"],
            capture_output=True, text=True, timeout=10
        )
        pids = []
        current_pid = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("ProcessId"):
                current_pid = int(line.split(":")[-1].strip())
            elif line.startswith("CommandLine") and current_pid:
                cmd = line.split(":", 1)[-1].strip()
                # ★ v261e: working dir フィルタ — c:\pokemon-rl 配下のみ kill
                # 旧: train.py 含む全 python kill リスク
                # 新: pokemon-rl パスを必須にして無関係 python を保護
                _is_pokemon = ("pokemon-rl" in cmd.lower() or "poke-rl" in cmd.lower())
                if "train.py" in cmd and _is_pokemon and current_pid != os.getpid():
                    pids.append(current_pid)
                current_pid = None
        return pids
    except Exception as e:
        print(f"  PID検索エラー: {e}")
        return []


def kill_pid(pid):
    """Windows で PID を確実に停止"""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True, timeout=10
        )
        return True
    except Exception:
        return False


def save_all_to_slot8(skip_ports=None):
    """全 mGBA インスタンスを独立 Slot にセーブ (競合防止)
    v261f: 8888→8, 8889→7, 8890→6
    v261r: skip_ports でスタック中ポートの save 禁止 (stuck state 永続化防止)
    """
    import socket as _ss
    TERM = b"<|END|>"
    saved = []
    skipped = []
    skip_ports = skip_ports or set()
    _slot_map = {8888: 8, 8889: 7, 8890: 6}
    for port, slot in _slot_map.items():
        if port in skip_ports:
            skipped.append(f"{port}->S{slot}(SKIP)")
            continue
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
            saved.append(f"{port}->S{slot}")
        except Exception:
            pass
    if skipped:
        print(f"  Skipped (stuck): {skipped}")
    return saved


def main():
    print("=== train.py 再起動 ===")

    # Step 0: ★ 進捗ロス防止 - kill 前に全ポート Slot 8 セーブ
    # v261an: monitor.py 方式で stuck 検出 (spc>=100 or KZ-ForceExit stay>=500)
    _skip_set = set()
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as _lf:
                _tail = _lf.readlines()[-1000:]  # 300→1000 に拡大
            import re
            _spc_pat = {p: re.compile(rf"spc=(\d+)\s+port={p}") for p in (8888, 8889, 8890)}
            for _port in (8888, 8889, 8890):
                for _ln in reversed(_tail):
                    _m = _spc_pat[_port].search(_ln)
                    if _m:
                        _spc = int(_m.group(1))
                        # ★ v261aw: 10→50 緩和 (正常移動は save 許可)
                        # v261as の spc>=10 は厳格すぎて全 save 停止していた
                        if _spc >= 50:
                            _skip_set.add(_port)
                            print(f"  Detected stuck port={_port} spc={_spc} (ABS-SAFE)")
                        break
    except Exception as _e:
        print(f"  Pre-save stuck-detect err: {_e}")
    print(f"  Pre-kill save (Slot 8), skip={_skip_set}...")
    saved = save_all_to_slot8(skip_ports=_skip_set)
    print(f"  Saved ports: {saved}")

    # Step 0.5: ★ v261e: ログローテーション (50MB 超なら .1 に退避)
    if os.path.exists(LOG_FILE):
        _size = os.path.getsize(LOG_FILE)
        if _size > 50 * 1024 * 1024:
            _rot = LOG_FILE + ".1"
            if os.path.exists(_rot):
                os.remove(_rot)
            os.rename(LOG_FILE, _rot)
            print(f"  Log rotated: {_size//1024//1024}MB → {_rot}")

    # Step 1: 既存プロセス検出・停止
    pids = find_train_pids()
    if pids:
        print(f"  既存 train.py プロセス: {pids}")
        for pid in pids:
            print(f"  PID {pid} を停止中...")
            kill_pid(pid)
        time.sleep(2)

        # 確認
        remaining = find_train_pids()
        if remaining:
            print(f"  !! 停止失敗: {remaining}")
            for pid in remaining:
                kill_pid(pid)
            time.sleep(1)
            remaining2 = find_train_pids()
            if remaining2:
                print(f"  !! 強制停止も失敗: {remaining2}")
                print("  手動で停止してください")
                sys.exit(1)
    else:
        print("  既存プロセスなし")

    # Step 2: 新規起動
    print("  新しい train.py を起動中...")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    with open(LOG_FILE, "w") as log:
        proc = subprocess.Popen(
            [sys.executable, "-u", "train.py"],
            stdout=log, stderr=subprocess.STDOUT,
            env=env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )

    print(f"  PID: {proc.pid}")

    # Step 3: 起動確認
    time.sleep(3)
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        if len(lines) >= 3:
            print(f"  起動確認OK ({len(lines)} 行出力)")
        else:
            print(f"  起動中... ({len(lines)} 行)")
    except Exception:
        print("  ログ確認失敗")

    # Step 4: 重複チェック (新プロセスを除外)
    final_pids = [p for p in find_train_pids() if p != proc.pid]
    if len(final_pids) > 0:
        print(f"  !! 警告: 旧プロセス残存: {final_pids}")
    else:
        print(f"  単一プロセス確認OK: PID {proc.pid}")

    print("=== 再起動完了 ===")


if __name__ == "__main__":
    main()
