#!/usr/bin/env python
"""
mapping_audit.py — exploration_map.json を pokeemerald decomp 正準寸法と照合し、
canonical bounds を超える tile を検出 / purge する。

使い方:
  python tools/mapping_audit.py            # 監査のみ (report)
  python tools/mapping_audit.py --purge    # bounds 超え tile を実際に削除
  python tools/mapping_audit.py --fetch    # 未登録 map の canonical 寸法を decomp から取得

canonical bounds 出典:
  https://raw.githubusercontent.com/pret/pokeemerald/master/data/layouts/layouts.json
  border offset = +7 (border block size)
  my_x_max = canon_width  + 7 - 1
  my_y_max = canon_height + 7 - 1
"""
import json
import sys
import argparse
from collections import Counter
from pathlib import Path

# canonical bounds (my coords, with +7 border offset)
# 形式: (mg, mn): (x_min, x_max, y_min, y_max, name)
# 追加ルール: pokeemerald `data/layouts/layouts.json` で width/height 確認後追加
CANON_BOUNDS = {
    # Dewford phase (2026-05-19 verified)
    (0, 11): (7, 26, 7, 26, "Dewford Town outdoor 20x20"),
    (3, 0):  (7, 13, 7, 14, "Dewford House1 7x8"),
    (3, 1):  (7, 20, 7, 15, "Dewford PC 1F 14x9"),
    (3, 2):  (7, 20, 7, 16, "Dewford PC 2F 14x10"),
    (3, 3):  (7, 24, 7, 34, "Dewford Gym (Brawly) 18x28"),
    (3, 4):  (7, 23, 7, 15, "Dewford Hall 17x9"),
    (3, 5):  (7, 13, 7, 14, "Dewford House2 7x8"),
}


def audit(map_path: Path, purge: bool = False) -> int:
    m = json.load(open(map_path))
    removed = []
    new_m = {}
    coverage = {}

    for k, v in m.items():
        parts = k.split(",")
        if len(parts) != 4:
            new_m[k] = v
            continue
        try:
            mg, mn, x, y = (int(p) for p in parts)
        except ValueError:
            new_m[k] = v
            continue
        if (mg, mn) in CANON_BOUNDS:
            x_min, x_max, y_min, y_max, _ = CANON_BOUNDS[(mg, mn)]
            if x < x_min or x > x_max or y < y_min or y > y_max:
                removed.append((mg, mn, x, y))
                continue
        coverage.setdefault((mg, mn), []).append((x, y))
        new_m[k] = v

    print(f"Total tiles: {len(m)}")
    print(f"Out-of-bounds: {len(removed)}")
    print()
    print(f"{'Map':<10} {'Tiles':>6} {'X range':<10} {'Y range':<10} {'Coverage%':>10} {'Name'}")
    print("-" * 80)
    for (mg, mn), tiles in sorted(coverage.items()):
        if (mg, mn) not in CANON_BOUNDS:
            continue
        x_min, x_max, y_min, y_max, name = CANON_BOUNDS[(mg, mn)]
        cap = (x_max - x_min + 1) * (y_max - y_min + 1)
        cov_pct = len(tiles) / cap * 100 if cap else 0
        xs = [t[0] for t in tiles]
        ys = [t[1] for t in tiles]
        print(
            f"({mg},{mn})    {len(tiles):>6} "
            f"{min(xs):>2}-{max(xs):<5} {min(ys):>2}-{max(ys):<5} "
            f"{cov_pct:>9.1f}% {name}"
        )

    if removed:
        print()
        print("Out-of-bounds tile counts per map:")
        for (mg, mn), c in sorted(Counter([(r[0], r[1]) for r in removed]).items()):
            name = CANON_BOUNDS.get((mg, mn), (0, 0, 0, 0, "?"))[4]
            print(f"  ({mg},{mn}) {name}: {c} purged")
        if len(removed) <= 20:
            for r in removed:
                print(f"    {r}")

    print()
    unmapped_known = [
        (mg, mn) for (mg, mn), _ in CANON_BOUNDS.items() if (mg, mn) not in coverage
    ]
    if unmapped_known:
        print(f"Known maps with no tiles: {unmapped_known}")

    if purge and removed:
        backup = map_path.with_suffix(".json.bak_audit")
        json.dump(m, open(backup, "w"), indent=2)
        json.dump(new_m, open(map_path, "w"), indent=2)
        print()
        print(f"PURGED {len(removed)} tiles. Backup: {backup}")
    elif removed:
        print()
        print("(dry-run — pass --purge to actually remove)")

    return len(removed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--map",
        default="exploration_map.json",
        help="path to exploration_map.json",
    )
    ap.add_argument(
        "--purge", action="store_true", help="actually remove out-of-bounds tiles"
    )
    args = ap.parse_args()
    n = audit(Path(args.map), purge=args.purge)
    sys.exit(0 if n == 0 else 1)


if __name__ == "__main__":
    main()
