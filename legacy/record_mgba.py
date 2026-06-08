"""mGBA画面録画スクリプト - 短時間のGIF録画でClaudeが確認できるようにする。

使い方:
    python record_mgba.py [port] [duration_sec] [fps]
    python record_mgba.py 5000 10 4      # port 5000を10秒間、4fpsで録画
    python record_mgba.py all 8 3         # 全ポート(5000-5002)を8秒間、3fpsで録画
"""

import sys
import os
import time
import tempfile
import shutil
import urllib.request
from pathlib import Path

def capture_frame(port: int, output_path: str) -> bool:
    """mGBA-httpソケットプロトコルでスクリーンショットを撮る"""
    import socket
    TERM = b"<|END|>"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        # パスをOS形式に変換
        win_path = output_path.replace("/", os.sep)
        s.sendall(f"core.screenshot,{win_path}".encode() + TERM)
        buf = b""
        while TERM not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        return b"<|SUCCESS|>" in buf or os.path.exists(output_path)
    except Exception:
        return False


def record_gif(port: int, duration: float = 10.0, fps: float = 4.0,
               output_dir: str = "screenshots") -> str:
    """指定ポートのmGBAを録画してGIFを生成する"""
    from PIL import Image

    os.makedirs(output_dir, exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix="mgba_rec_")

    interval = 1.0 / fps
    frames_needed = int(duration * fps)
    frames = []

    print(f"Recording port {port}: {duration}s @ {fps}fps ({frames_needed} frames)...")

    for i in range(frames_needed):
        t0 = time.time()
        fpath = os.path.join(tmpdir, f"frame_{i:04d}.png")
        # Windows path needs backslashes for mGBA
        fpath_win = fpath.replace("/", "\\")
        ok = capture_frame(port, fpath_win)
        if ok and os.path.exists(fpath) and os.path.getsize(fpath) > 0:
            frames.append(fpath)
        else:
            print(f"  Frame {i} failed")

        elapsed = time.time() - t0
        sleep_time = interval - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

    if not frames:
        print(f"No frames captured for port {port}")
        shutil.rmtree(tmpdir, ignore_errors=True)
        return ""

    # GIF生成
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    gif_path = os.path.join(output_dir, f"recording_{port}_{timestamp}.gif")

    imgs = []
    for fp in frames:
        try:
            img = Image.open(fp)
            imgs.append(img.copy())
            img.close()
        except Exception as e:
            print(f"  Error loading {fp}: {e}")

    if imgs:
        frame_duration = int(1000 / fps)  # ms per frame
        imgs[0].save(
            gif_path,
            save_all=True,
            append_images=imgs[1:],
            duration=frame_duration,
            loop=0,
            optimize=True
        )
        print(f"Saved: {gif_path} ({len(imgs)} frames, {os.path.getsize(gif_path)//1024}KB)")
    else:
        gif_path = ""

    # クリーンアップ
    shutil.rmtree(tmpdir, ignore_errors=True)
    return gif_path


def main():
    port_arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    fps = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0

    if port_arg == "all":
        ports = [8888, 8889, 8890]
    else:
        ports = [int(port_arg)]

    results = []
    for port in ports:
        gif = record_gif(port, duration, fps)
        if gif:
            results.append(gif)

    if results:
        print(f"\nDone! {len(results)} recording(s):")
        for r in results:
            print(f"  {r}")
    else:
        print("No recordings created.")


if __name__ == "__main__":
    main()
