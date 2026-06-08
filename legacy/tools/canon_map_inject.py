#!/usr/bin/env python
"""
canon_map_inject.py — pokeemerald decomp の map.bin から canonical
passable/wall データを抽出し、 exploration_map.json に注入する汎用 tool。

2026-05-21 Brawly Gym (3,3) で初実装 → 61 tiles から 151 tiles に拡大、
BFS path to (11,10) 実現。 これを他 phase でも再利用できる汎用化。

使い方:
  python tools/canon_map_inject.py <map_name> <mg> <mn>
    例: python tools/canon_map_inject.py DewfordTown_Gym 3 3

  python tools/canon_map_inject.py --list
    canonical 寸法登録済 map 一覧

map.bin format (pokeemerald):
  16-bit little-endian blocks
  bits 0-9: metatile ID
  bits 10-11: collision (0=passable, 1=wall)
  bits 12-15: elevation

my coord = canon coord + 7 (border offset)
"""
import argparse
import json
import struct
import sys
import urllib.request
from pathlib import Path

# canonical 寸法登録 (from layouts.json)
# Add new maps here when expanding to new phases
KNOWN_MAPS = {
    "DewfordTown_Gym": (18, 28),
    # 2026-05-29: ポケセン内部は decomp 上 town 別でなく共有 layout。
    # 旧 "DewfordTown_PokemonCenter_2F" は 404 (未検証登録だった) → 共有名に修正。
    "PokemonCenter_1F": (14, 9),
    "PokemonCenter_2F": (14, 10),
    "DewfordTown_Hall": (17, 9),
    "DewfordTown": (20, 20),  # outdoor
    "PetalburgWoods": (48, 44),  # mg=24 mn=11; 8889 chronic wall loop at (19,30)/(22,30) Up = gap (19,28-29)
    # 2026-06-02: 8888 cave B1F (11,29)=canon(4,22) と 8890 cave 1F (24,19)=canon(17,12) 診断用
    "GraniteCave_1F": (42, 15),
    "GraniteCave_B1F": (32, 26),
    "GraniteCave_B2F": (32, 26),
    "GraniteCave_StevensRoom": (15, 14),
    # TODO: 追加 phase で随時拡張
    # "MauvilleCity_Gym": (...),
    # "LavaridgeTown_Gym": (...),
}

BORDER_OFFSET = 7  # my coord = canon coord + 7


def fetch_map_bin(map_name: str, cache_dir: Path = Path("docs")) -> bytes:
    cache_file = cache_dir / f"canon_data_{map_name}_map.bin"
    if cache_file.exists():
        return cache_file.read_bytes()
    url = f"https://raw.githubusercontent.com/pret/pokeemerald/master/data/layouts/{map_name}/map.bin"
    print(f"Downloading {url}")
    data = urllib.request.urlopen(url).read()
    cache_dir.mkdir(exist_ok=True)
    cache_file.write_bytes(data)
    print(f"Cached: {cache_file}")
    return data


def parse_passable_set(data: bytes, w: int, h: int) -> set:
    if len(data) != w * h * 2:
        raise ValueError(f"size mismatch: {len(data)} bytes for {w}x{h} ({w*h*2} expected)")
    blocks = struct.unpack(f"<{w*h}H", data)
    passable = set()
    for cy in range(h):
        for cx in range(w):
            b = blocks[cy * w + cx]
            coll = (b >> 10) & 0x3
            if coll == 0:
                passable.add((cx, cy))
    return passable


def inject(
    map_name: str,
    mg: int,
    mn: int,
    exploration_map_path: Path = Path("exploration_map.json"),
    dry_run: bool = False,
) -> dict:
    if map_name not in KNOWN_MAPS:
        raise KeyError(f"{map_name} not in KNOWN_MAPS. Add it with width/height first.")
    w, h = KNOWN_MAPS[map_name]
    data = fetch_map_bin(map_name)
    passable = parse_passable_set(data, w, h)

    if not exploration_map_path.exists():
        raise FileNotFoundError(exploration_map_path)
    m = json.load(open(exploration_map_path))
    pref = f"{mg},{mn},"
    before = sum(1 for k in m if k.startswith(pref))

    DIRS = {"Up": (0, -1), "Down": (0, 1), "Left": (-1, 0), "Right": (1, 0)}
    WALL_KEY = {d: f"wall_{d}" for d in DIRS}

    added_tiles = 0
    updated_tiles = 0
    edges_added = 0
    walls_added = 0

    for cx, cy in passable:
        my_x = cx + BORDER_OFFSET
        my_y = cy + BORDER_OFFSET
        key = f"{mg},{mn},{my_x},{my_y}"
        if key not in m:
            m[key] = {}
            added_tiles += 1
        else:
            updated_tiles += 1
        tile = m[key]
        for dn, (dx, dy) in DIRS.items():
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < w and 0 <= ny < h:
                if (nx, ny) in passable:
                    neighbor_x = nx + BORDER_OFFSET
                    neighbor_y = ny + BORDER_OFFSET
                    if dn not in tile:
                        tile[dn] = [mg, mn, neighbor_x, neighbor_y]
                        edges_added += 1
                else:
                    if not tile.get(WALL_KEY[dn]):
                        tile[WALL_KEY[dn]] = True
                        walls_added += 1
            else:
                if not tile.get(WALL_KEY[dn]):
                    tile[WALL_KEY[dn]] = True
                    walls_added += 1

    after = sum(1 for k in m if k.startswith(pref))
    stats = {
        "map_name": map_name,
        "mg": mg,
        "mn": mn,
        "dims": (w, h),
        "canon_passable": len(passable),
        "tiles_before": before,
        "tiles_after": after,
        "added": added_tiles,
        "updated": updated_tiles,
        "edges_added": edges_added,
        "walls_added": walls_added,
    }
    if not dry_run:
        backup = exploration_map_path.with_suffix(f".json.bak_canon_{map_name}")
        json.dump(json.load(open(exploration_map_path)), open(backup, "w"), indent=2)
        json.dump(m, open(exploration_map_path, "w"), indent=2)
        stats["backup"] = str(backup)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("map_name", nargs="?", help="e.g. DewfordTown_Gym")
    ap.add_argument("mg", nargs="?", type=int, help="map group")
    ap.add_argument("mn", nargs="?", type=int, help="map num")
    ap.add_argument("--list", action="store_true", help="list known maps")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.list:
        for n, (w, h) in sorted(KNOWN_MAPS.items()):
            print(f"  {n}: {w}x{h}")
        return

    if not (args.map_name and args.mg is not None and args.mn is not None):
        ap.print_help()
        sys.exit(1)

    stats = inject(args.map_name, args.mg, args.mn, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
