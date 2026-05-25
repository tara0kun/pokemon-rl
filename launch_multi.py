"""
マルチmGBA-http起動スクリプト
==============================
3つのmGBA-httpインスタンスを異なるポートで起動する。

前提条件:
  1. 3つのmGBAウィンドウが起動し、エメラルドROMを読み込み済み
  2. 各mGBAでLuaスクリプトをロード済み:
     - mGBA #1 → mGBASocketServer_1.lua (socket 8888)
     - mGBA #2 → mGBASocketServer_2.lua (socket 8889)
     - mGBA #3 → mGBASocketServer_3.lua (socket 8890)

Usage:
  python launch_multi.py         # 3インスタンス起動
  python launch_multi.py --stop  # 全インスタンス停止
"""
import subprocess
import sys
import os
import time
import requests

MGBA_HTTP_EXE = r"C:\Users\tiita\Downloads\mGBA-http-0.8.1-win-x64-self-contained.exe"

# (HTTP port, Lua socket port)
INSTANCES = [
    (5000, 8888),
    (5001, 8889),
    (5002, 8890),
]


def start_instances():
    processes = []
    for http_port, socket_port in INSTANCES:
        env = os.environ.copy()
        env["mgba-http__Socket__Port"] = str(socket_port)

        cmd = [MGBA_HTTP_EXE, "--urls", f"http://127.0.0.1:{http_port}"]
        print(f"  Starting mGBA-http: HTTP={http_port} Socket={socket_port}")

        p = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        processes.append((http_port, p))
        time.sleep(1)

    # 接続テスト
    print()
    time.sleep(2)
    all_ok = True
    for http_port, _ in processes:
        url = f"http://localhost:{http_port}/core/getGameTitle"
        try:
            r = requests.get(url, timeout=3)
            if r.status_code == 200:
                print(f"  Port {http_port}: OK ({r.text.strip()})")
            else:
                print(f"  Port {http_port}: HTTP {r.status_code}")
                all_ok = False
        except Exception as e:
            print(f"  Port {http_port}: FAIL ({e})")
            all_ok = False

    if all_ok:
        print(f"\n  全{len(INSTANCES)}インスタンス起動成功!")
        print(f"  学習開始: python train.py")
    else:
        print(f"\n  一部のインスタンスが失敗しました。")
        print(f"  各mGBAでLuaスクリプトがロードされているか確認してください。")

    print(f"\n  停止: python launch_multi.py --stop")
    print(f"  PID: {', '.join(str(p.pid) for _, p in processes)}")

    # PIDをファイルに保存（停止用）
    with open("mgba_http_pids.txt", "w") as f:
        for http_port, p in processes:
            f.write(f"{p.pid}\n")


def stop_instances():
    try:
        with open("mgba_http_pids.txt") as f:
            pids = [int(line.strip()) for line in f if line.strip()]
    except FileNotFoundError:
        print("  mgba_http_pids.txt が見つかりません")
        return

    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                         capture_output=True)
            print(f"  PID {pid}: 停止")
        except Exception:
            pass

    os.remove("mgba_http_pids.txt")
    print("  全インスタンス停止完了")


if __name__ == "__main__":
    print("=" * 50)
    print("  mGBA-http マルチインスタンス起動")
    print("=" * 50)

    if "--stop" in sys.argv:
        stop_instances()
    else:
        start_instances()
