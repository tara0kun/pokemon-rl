"""
Week 0 smoke test.

mGBA + lua が手動で起動済 (STARTUP.md 参照) の前提で、 socket 越しに:
  1. port 8895 接続
  2. game title / code 取得
  3. current frame 取得
  4. screenshot 1 枚保存
  5. ボタン (B) を 1 回 tap

すべて OK なら Week 1 に進める。

実行:
  cd c:/pokemon-rl
  poke-rl/Scripts/python.exe -m generic_agent.smoke_test
"""
from __future__ import annotations

import sys
import time

from . import config, state as state_mod
from .io import EmulatorError, MGBAClient


def main() -> int:
    config.ensure_runtime_dirs()
    client = MGBAClient()

    print(f"[..] connecting to {config.SOCKET_HOST}:{config.SOCKET_PORT}")
    if not client.ping():
        print(
            "[FAIL] port unreachable. "
            "mGBA + lua が起動済みか確認 (STARTUP.md)"
        )
        return 1
    print(f"[OK] port {config.SOCKET_PORT} reachable")

    try:
        title = client.get_game_title()
        print(f"[OK] game title: {title}")

        code = client.get_game_code()
        print(f"[OK] game code:  {code}")

        frame = client.current_frame()
        print(f"[OK] frame: {frame}")

        shot_path = config.SCREENSHOT_DIR / "smoke.png"
        client.screenshot(shot_path)
        time.sleep(0.3)
        if not shot_path.exists():
            print(f"[FAIL] screenshot not written: {shot_path}")
            return 2
        size = shot_path.stat().st_size
        print(f"[OK] screenshot saved: {shot_path} ({size} bytes)")

        gs = state_mod.read_state(client)
        print(f"[OK] state: {gs.short()}")
        if not code.startswith("BPEE"):
            print(
                f"[WARN] game code {code} != BPEE (English Emerald). "
                "RAM addresses are tuned for English ROM."
            )

        client.tap("B", frames=5)
        print("[OK] button B tapped")

    except EmulatorError as exc:
        print(f"[FAIL] emulator error: {exc}")
        return 3
    except Exception as exc:
        print(f"[FAIL] unexpected: {exc!r}")
        return 4

    print("[ALL OK]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
