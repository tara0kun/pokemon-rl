#!/usr/bin/env python
"""
canon_ascii.py — pokeemerald decomp の map.bin を ASCII grid で可視化する診断 tool。

chronic stuck の root cause 特定用。推測パッチを当てる前に「canon 上で実際に
どこが passable / wall か」「stuck 位置はどんな壁配置に囲まれているか」を目視確認する。
(feedback_canon_visualization 準拠)

使い方:
  python tools/canon_ascii.py <map_name> [--mark x,y[:label] ...]
    例: python tools/canon_ascii.py DewfordTown_PokemonCenter_2F --mark 4,9:8888 --mark 1,9:8889

  座標は canon 座標 (my座標 - 7)。--mark に my座標を渡す場合は --my を付ける。

凡例: '.'=passable  '#'=wall  数字/英字=mark (stuck位置)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from canon_map_inject import KNOWN_MAPS, fetch_map_bin, BORDER_OFFSET
import struct


def build_grid(data: bytes, w: int, h: int):
    if len(data) != w * h * 2:
        raise ValueError(f"size mismatch: {len(data)} bytes for {w}x{h} ({w*h*2} expected)")
    blocks = struct.unpack(f"<{w*h}H", data)
    grid = []
    for cy in range(h):
        row = []
        for cx in range(w):
            b = blocks[cy * w + cx]
            coll = (b >> 10) & 0x3
            row.append("." if coll == 0 else "#")
        grid.append(row)
    return grid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("map_name")
    ap.add_argument("--mark", action="append", default=[],
                    help="x,y[:label] を passable grid 上にマーク (canon座標)")
    ap.add_argument("--my", action="store_true",
                    help="--mark を my座標で渡す (内部で -7 して canon に変換)")
    args = ap.parse_args()

    if args.map_name not in KNOWN_MAPS:
        print(f"{args.map_name} not in KNOWN_MAPS. known:")
        for n in sorted(KNOWN_MAPS):
            print(f"  {n}")
        sys.exit(1)

    w, h = KNOWN_MAPS[args.map_name]
    data = fetch_map_bin(args.map_name)
    grid = build_grid(data, w, h)

    marks = {}
    for m in args.mark:
        coord, _, label = m.partition(":")
        mx, my = (int(v) for v in coord.split(","))
        if args.my:
            mx -= BORDER_OFFSET
            my -= BORDER_OFFSET
        marks[(mx, my)] = (label or "*")[0]

    print(f"=== {args.map_name} canon {w}x{h} (border_offset={BORDER_OFFSET}) ===")
    print("    " + "".join(str(x % 10) for x in range(w)))
    for cy in range(h):
        row = ""
        for cx in range(w):
            row += marks.get((cx, cy), grid[cy][cx])
        print(f"{cy:2d}  {row}")
    n_pass = sum(r.count(".") for r in grid)
    print(f"passable={n_pass}/{w*h}")
    for (mx, my), lb in marks.items():
        cell = grid[my][mx] if (0 <= my < h and 0 <= mx < w) else "?"
        print(f"  mark '{lb}' canon=({mx},{my}) my=({mx+BORDER_OFFSET},{my+BORDER_OFFSET}) "
              f"cell={'passable' if cell == '.' else 'WALL/oob'}")


if __name__ == "__main__":
    main()
