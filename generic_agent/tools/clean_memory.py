"""Utility — wipe the agent's persistent learning state.

Backs up `frame_cache.json`, `tile_map.json`, `path_memory.json` under
`memory/<name>.pre-wipe-<utc-tag>.json.bak` and removes the live files
so the next auto_loop run starts from scratch.

Why: cycle 21 retrospective showed that long-running sessions
accumulated stale Brain decisions in the cache and over-blocked tiles
in tile_map that cycle 19's cleanup_phantom_walls partially repairs but
does not reset. v42 (cycle 14-20, accumulated state) = 79 pos. v43
(same code, wiped state) = 261 pos. The persistent layer is load-bearing
for some failure modes; periodic wipes are a maintenance procedure, not
a code change.

Run:
    poke-rl/Scripts/python.exe -m generic_agent.tools.clean_memory

Skip the prompt and proceed:
    poke-rl/Scripts/python.exe -m generic_agent.tools.clean_memory --yes
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .. import config

TARGETS = ("frame_cache.json", "tile_map.json", "path_memory.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--yes", action="store_true",
        help="skip confirmation",
    )
    args = parser.parse_args()

    memory_dir = Path(config.MEMORY_DIR)
    existing = [p for p in (memory_dir / t for t in TARGETS) if p.exists()]
    if not existing:
        print(f"[clean_memory] nothing to wipe in {memory_dir}")
        return 0

    print(f"[clean_memory] target files in {memory_dir}:")
    for p in existing:
        print(f"  {p.name}  ({p.stat().st_size:,} bytes)")

    if not args.yes:
        ans = input("proceed? type 'yes' to confirm: ").strip().lower()
        if ans != "yes":
            print("[clean_memory] aborted")
            return 1

    tag = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    for p in existing:
        bak = p.with_name(f"{p.stem}.pre-wipe-{tag}{p.suffix}.bak")
        p.replace(bak)
        print(f"  wiped {p.name} -> backup {bak.name}")
    print("[clean_memory] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
