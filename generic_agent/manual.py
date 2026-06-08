"""Manual brain mode — execute a button sequence, save screenshot, read state.

Usage from bash:
  python -m generic_agent.manual A,A,A,Down,Down
  python -m generic_agent.manual wait 60
  python -m generic_agent.manual state   # just snapshot + state, no input

Each invocation:
  1. Executes the requested sequence (default 15 frames per tap).
  2. Sleeps briefly per tap to let the emulator advance.
  3. Captures one screenshot to logs/screens/manual_<N>.png.
  4. Reads map/pos via RAM bridge.
  5. Prints a one-line summary and the screenshot path.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from . import config, state as state_mod
from .io import MGBAClient


def _next_index() -> int:
    config.ensure_runtime_dirs()
    existing = list(config.SCREENSHOT_DIR.glob("manual_*.png"))
    if not existing:
        return 1
    nums = []
    for p in existing:
        try:
            nums.append(int(p.stem.split("_")[1]))
        except (ValueError, IndexError):
            continue
    return (max(nums) + 1) if nums else 1


def main(argv: list[str]) -> int:
    config.ensure_runtime_dirs()
    client = MGBAClient()
    if not client.ping():
        print("[FAIL] mGBA port 8895 unreachable")
        return 1

    sequence: list[tuple[str, int]] = []

    if not argv or argv[0].lower() == "state":
        pass
    elif argv[0].lower() == "wait":
        frames = int(argv[1]) if len(argv) > 1 else 60
        time.sleep(max(0.05, frames / 60.0))
    else:
        for token in argv:
            for piece in token.split(","):
                piece = piece.strip()
                if not piece:
                    continue
                if ":" in piece:
                    btn, _, fr = piece.partition(":")
                    sequence.append((btn, int(fr)))
                else:
                    sequence.append((piece, 15))

        for btn, frames in sequence:
            client.tap(btn, frames=frames)
            time.sleep(max(0.05, frames / 60.0 + 0.05))

    idx = _next_index()
    shot = config.SCREENSHOT_DIR / f"manual_{idx:04d}.png"
    client.screenshot(shot)
    time.sleep(0.2)

    gs = state_mod.read_state(client)
    seq_str = (
        ",".join(f"{b}:{f}" for b, f in sequence) if sequence else "(no input)"
    )
    print(f"seq: {seq_str}")
    print(f"state: {gs.short()}")
    print(f"shot: {shot.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
