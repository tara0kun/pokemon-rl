"""Run health analyzer — read the latest run from memory/run_log.jsonl and
print every metric needed to decide whether the agent is making genuine
progress or stuck in a loop.

Usage:
    poke-rl/Scripts/python.exe -m generic_agent.tools.analyze_run

Or with a specific tail size:
    poke-rl/Scripts/python.exe -m generic_agent.tools.analyze_run --last 1500

Output: one block per metric. Anomalies are prefixed with [WARN] or [ERROR]
so they are grep-able from CI / cron / watchful eyes.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .. import config


def split_runs(lines: list[dict]) -> list[list[dict]]:
    """Group consecutive log entries into runs. A new run begins when
    turn drops back to 1 after a higher turn."""
    runs: list[list[dict]] = []
    cur: list[dict] = []
    for d in lines:
        if d["turn"] == 1 and cur:
            runs.append(cur)
            cur = []
        cur.append(d)
    if cur:
        runs.append(cur)
    return runs


def analyze(run: list[dict]) -> None:
    n = len(run)
    if n == 0:
        print("[ERROR] empty run")
        return

    first = run[0]
    last = run[-1]
    cost = last.get("cost_usd_total", 0.0)
    print(f"=== run summary ===")
    print(f"  turns: {n}")
    print(f"  final turn: {last['turn']}")
    print(f"  final pos:  {last.get('pos')}")
    print(f"  final map:  {tuple(last['map'])}")
    print(f"  cost USD:   ${cost:.4f}")
    print()

    positions: set[tuple] = set()
    maps: list[tuple] = []
    sources: Counter = Counter()
    buttons: Counter = Counter()
    same_pos_streak = 0
    max_same_pos_streak = 0
    max_same_pos_at: tuple | None = None
    prev: tuple | None = None
    for d in run:
        m = tuple(d["map"])
        p = tuple(d.get("pos") or (0, 0))
        positions.add((m, p))
        maps.append(m)
        src = d["source"].split("(")[0].split("[")[0]
        sources[src] += 1
        buttons[d["button"]] += 1
        cur = (m, p)
        if cur == prev:
            same_pos_streak += 1
            if same_pos_streak > max_same_pos_streak:
                max_same_pos_streak = same_pos_streak
                max_same_pos_at = cur
        else:
            same_pos_streak = 1
        prev = cur

    switches = sum(1 for i in range(1, n) if maps[i] != maps[i - 1])
    switch_rate = switches / n if n else 0.0
    # longest single-map run
    max_same_map = 0
    cur_same_map = 1
    max_same_map_id: tuple | None = None
    for i in range(1, n):
        if maps[i] == maps[i - 1]:
            cur_same_map += 1
            if cur_same_map > max_same_map:
                max_same_map = cur_same_map
                max_same_map_id = maps[i]
        else:
            cur_same_map = 1

    print("=== coverage ===")
    print(f"  unique maps:      {len(set(maps))}: {sorted(set(maps))}")
    print(f"  unique positions: {len(positions)}")
    print(f"  pos / turn ratio: {len(positions) / n:.3f} (low = stuck)")
    print()

    print("=== oscillation / cycling ===")
    flag = "[WARN] " if switch_rate > 0.3 else ""
    print(
        f"  {flag}map switch rate: {switches}/{n} = {switch_rate:.3f} /turn "
        f"(healthy < 0.1, suspect > 0.3)"
    )
    flag = "[WARN] " if max_same_pos_streak > 20 else ""
    print(
        f"  {flag}max consecutive same-(map,pos) turns: "
        f"{max_same_pos_streak} at {max_same_pos_at}"
    )
    print(
        f"  max consecutive same-map turns: {max_same_map} "
        f"on map {max_same_map_id}"
    )

    # Within-map small-cycle detector: sliding window
    cycle_windows = []
    win = 20
    pos_seq = [(tuple(d["map"]), tuple(d.get("pos") or (0, 0))) for d in run]
    for i in range(0, n - win + 1, 5):
        w = pos_seq[i : i + win]
        u = len(set(w))
        if u <= 6:
            cycle_windows.append((i, u))
    flag = "[WARN] " if cycle_windows else ""
    print(
        f"  {flag}within-map small cycles "
        f"(<=6 uniq in 20t window): {len(cycle_windows)}"
    )
    for i, u in cycle_windows[:3]:
        print(f"      turn {run[i]['turn']:+}..+19 uniq={u}")
    print()

    print("=== source breakdown ===")
    for s, c in sources.most_common(12):
        pct = c / n * 100
        print(f"  {s:30s}: {c:5d} ({pct:5.1f}%)")
    print()

    print("=== button breakdown ===")
    for b, c in buttons.most_common():
        pct = c / n * 100
        print(f"  {b:6s}: {c:5d} ({pct:5.1f}%)")
    print()

    print("=== map turn breakdown ===")
    for m, c in Counter(maps).most_common():
        pct = c / n * 100
        ratio = sum(1 for d in run if tuple(d["map"]) == m and d.get("pos"))
        unique_pos_in_map = len(
            {tuple(d["pos"]) for d in run if tuple(d["map"]) == m and d.get("pos")}
        )
        flag = ""
        if c >= 100 and unique_pos_in_map <= 8:
            flag = "[WARN cycling on this map] "
        print(
            f"  {flag}{str(m):12s}: {c:5d} turns ({pct:5.1f}%) "
            f"{unique_pos_in_map} unique positions"
        )
    print()

    # Final verdict
    is_healthy = (
        switch_rate < 0.3
        and max_same_pos_streak < 20
        and not cycle_windows
    )
    print("=== verdict ===")
    if is_healthy:
        print("  OK - no oscillation / cycle pathologies detected.")
    else:
        problems = []
        if switch_rate >= 0.3:
            problems.append(f"map_switch_rate={switch_rate:.2f}")
        if max_same_pos_streak >= 20:
            problems.append(f"same_pos_streak={max_same_pos_streak}")
        if cycle_windows:
            problems.append(f"small_cycle_windows={len(cycle_windows)}")
        print(f"  [WARN] anomalies: {', '.join(problems)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log",
        default=str(config.MEMORY_DIR / "run_log.jsonl"),
        help="Path to run_log.jsonl (default: memory/run_log.jsonl)",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=0,
        help="If >0, restrict to the last N turns of the latest run.",
    )
    parser.add_argument(
        "--all-runs",
        action="store_true",
        help="Analyze every run in the log (default: only the latest).",
    )
    args = parser.parse_args()

    path = Path(args.log)
    if not path.exists():
        print(f"[ERROR] log file not found: {path}")
        return 1

    lines = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    runs = split_runs(lines)
    if not runs:
        print("[ERROR] no runs found")
        return 1

    target_runs = runs if args.all_runs else runs[-1:]
    for i, r in enumerate(target_runs):
        if args.last > 0:
            r = r[-args.last :]
        print(f"\n{'#' * 60}\n# run {i + 1}/{len(target_runs)}\n{'#' * 60}")
        analyze(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
