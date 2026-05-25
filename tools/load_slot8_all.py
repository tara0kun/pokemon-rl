"""全 mGBA インスタンスから Slot 8 を読み込む

mGBA を新規起動した後に実行することで、最後にセーブされた最良の状態 (Slot 8)
から学習を再開できる。

注意: CLAUDE.md ルール「セーブステートロード完全禁止」は train.py コード内での
ロードを禁止する。ユーザーが起動時に手動でロード相当の操作を行うのは許可される。
本スクリプトはユーザー操作の自動化補助。

使い方:
    poke-rl/Scripts/python.exe tools/load_slot8_all.py

前提:
    - 3つの mGBA が起動済み
    - 各 mGBA で Slot 8 にセーブ済み (monitor.py 監視時に自動保存)
"""
import socket
import sys

TERM = b"<|END|>"


def load_slot(port: int) -> bool:
    """指定ポートの mGBA で Slot 8 をロード"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        s.sendall(b"core.loadstateslot,8" + TERM)
        buf = b""
        while TERM not in buf:
            ch = s.recv(4096)
            if not ch:
                break
            buf += ch
        s.close()
        resp = buf.split(TERM)[0].decode(errors="replace")
        return resp == "<|SUCCESS|>"
    except Exception as e:
        print(f"  Port {port}: Connection failed - {e}")
        return False


def main():
    print("=== Slot 8 一括ロード ===")
    print("  注意: 進行中の学習がある場合、先に train.py を停止してください")

    success = []
    failed = []
    for port in (8888, 8889, 8890):
        ok = load_slot(port)
        if ok:
            success.append(port)
            print(f"  Port {port}: ✓ Slot 8 loaded")
        else:
            failed.append(port)
            print(f"  Port {port}: ✗ Failed")

    print(f"\n結果: 成功 {len(success)}/3")
    if failed:
        print(f"  失敗ポート: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
