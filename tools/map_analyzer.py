"""
マップ分析ツール -exploration_map.jsonを分析して安全ルートを計算

使い方:
  python tools/map_analyzer.py route            # PC→R116の安全ルート
  python tools/map_analyzer.py deadzones        # NPCデッドゾーン一覧
  python tools/map_analyzer.py visualize 0 3    # マップ可視化
  python tools/map_analyzer.py stuck            # 完全壁タイル一覧
  python tools/map_analyzer.py health 0 3       # グラフ健全性分析
  python tools/map_analyzer.py health 0 3 0 31  # 健全性+到達可能性分析
"""

import json
import sys
from collections import deque

MAP_FILE = "exploration_map.json"

def load_map():
    with open(MAP_FILE) as f:
        return json.load(f)

def parse_key(k):
    parts = k.split(",")
    return (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))

def find_deadzones(data):
    """全方向wallのタイル = デッドゾーン候補"""
    deadzones = []
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        walls = [d for d in v if d.startswith("wall_")]
        edges = [d for d in v if d in ("Up", "Down", "Left", "Right")]
        if len(walls) >= 3 and len(edges) <= 1:
            mg, mn, x, y = parse_key(k)
            deadzones.append((mg, mn, x, y, walls, edges))
    return deadzones

def find_high_stuck_tiles(data):
    """wall_hitsが高いタイル = 過去にスタックした場所"""
    # wall_hitsはExplorationMapオブジェクト内部なのでJSONにはない
    # 代わりに全方向wall + edgeなしのタイルを抽出
    stuck = []
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        walls = set(d for d in v if d.startswith("wall_"))
        edges = set(d for d in v if d in ("Up", "Down", "Left", "Right", "warp"))
        if len(walls) == 4 and len(edges) == 0:
            mg, mn, x, y = parse_key(k)
            stuck.append((mg, mn, x, y))
    return stuck

def safe_bfs(data, start, target_mg, target_mn, avoid_tiles=None):
    """デッドゾーンを回避するBFS"""
    if avoid_tiles is None:
        avoid_tiles = set()

    queue = deque([(start, [])])
    visited = {start}

    while queue:
        node, path = queue.popleft()
        mg, mn, x, y = node

        if mg == target_mg and mn == target_mn:
            return path

        k = f"{mg},{mn},{x},{y}"
        if k not in data:
            continue

        edges = data[k]
        for direction in ("Up", "Down", "Left", "Right", "warp"):
            if direction not in edges:
                continue
            if f"wall_{direction}" in edges:
                continue

            next_node = tuple(edges[direction])
            if next_node in visited:
                continue
            if next_node in avoid_tiles:
                continue

            visited.add(next_node)
            queue.append((next_node, path + [direction]))

    return []  # no path

def visualize_map(data, target_mg, target_mn):
    """指定マップのASCII可視化"""
    tiles = {}
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        mg, mn, x, y = parse_key(k)
        if mg == target_mg and mn == target_mn:
            walls = set(d for d in v if d.startswith("wall_"))
            edges = set(d for d in v if d in ("Up", "Down", "Left", "Right"))
            if len(walls) >= 4:
                tiles[(x, y)] = "X"  # fully walled
            elif len(walls) >= 2:
                tiles[(x, y)] = "#"  # partially walled
            elif "warp" in v:
                tiles[(x, y)] = "D"  # door/warp
            else:
                tiles[(x, y)] = "."  # walkable

    if not tiles:
        print(f"No tiles found for map ({target_mg},{target_mn})")
        return

    xs = [t[0] for t in tiles]
    ys = [t[1] for t in tiles]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    print(f"Map ({target_mg},{target_mn}) x={min_x}-{max_x} y={min_y}-{max_y}")
    print(f"Legend: .=walkable #=partial_wall X=full_wall D=door/warp")
    print()

    # Header
    header = "    "
    for x in range(min_x, max_x + 1):
        if x % 5 == 0:
            header += str(x % 100).rjust(2)[-1]
        else:
            header += " "
    print(header)

    for y in range(min_y, max_y + 1):
        row = f"{y:3d} "
        for x in range(min_x, max_x + 1):
            row += tiles.get((x, y), " ")
        print(row)

def health_check(data, target_mg, target_mn, reach_mg=None, reach_mn=None):
    """指定マップのグラフ健全性を分析"""
    MAP_NAMES = {
        (0, 0): "R101", (0, 1): "R102", (0, 3): "Kanazumi", (0, 4): "R104",
        (0, 9): "Touka", (0, 25): "Mishiro", (0, 28): "R103",
        (0, 31): "R116", (0, 32): "R110", (0, 33): "Kinsetsu",
    }
    map_name = MAP_NAMES.get((target_mg, target_mn), f"({target_mg},{target_mn})")

    # Filter tiles for target map
    map_tiles = {}
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        parts = k.split(",")
        mg, mn = int(parts[0]), int(parts[1])
        if mg == target_mg and mn == target_mn:
            map_tiles[k] = v

    if not map_tiles:
        print(f"マップ ({target_mg},{target_mn}) にタイルが見つかりません")
        return

    # Count walls, edges, warps
    total_walls = 0
    total_edges = 0
    warp_count = 0
    fully_walled = 0
    high_risk = 0
    for k, v in map_tiles.items():
        walls = [d for d in v if d.startswith("wall_")]
        edges = [d for d in v if d in ("Up", "Down", "Left", "Right")]
        total_walls += len(walls)
        total_edges += len(edges)
        if "warp" in v:
            warp_count += 1
        if len(walls) >= 4 and len(edges) == 0:
            fully_walled += 1
        if len(walls) >= 3 and len(edges) <= 1:
            high_risk += 1

    print(f"=== {map_name} ({target_mg},{target_mn}) グラフ健全性 ===")
    print(f"総タイル数: {len(map_tiles)}")
    print(f"エッジ数 (移動可能方向): {total_edges}")
    print(f"Wall数 (壁判定): {total_walls}")
    print(f"Warp数: {warp_count}")
    print(f"Wall/Edge比率: {total_walls/max(total_edges,1):.3f}")
    print(f"完全壁タイル: {fully_walled}")
    print(f"孤立リスクタイル (wall>=3, edge<=1): {high_risk}")
    print()

    # Connected components (within this map only)
    visited = set()
    components = []
    for k in map_tiles:
        node = parse_key(k)
        if node in visited:
            continue
        component = set()
        queue = deque([node])
        while queue:
            cur = queue.popleft()
            if cur in component:
                continue
            component.add(cur)
            visited.add(cur)
            ck = f"{cur[0]},{cur[1]},{cur[2]},{cur[3]}"
            if ck not in map_tiles:
                continue
            edges = map_tiles[ck]
            for direction in ("Up", "Down", "Left", "Right"):
                if direction in edges and f"wall_{direction}" not in edges:
                    next_node = tuple(edges[direction])
                    if next_node[0] == target_mg and next_node[1] == target_mn and next_node not in visited:
                        queue.append(next_node)
        components.append(component)

    components.sort(key=len, reverse=True)
    print(f"=== 接続コンポーネント ({map_name}内移動のみ) ===")
    print(f"コンポーネント数: {len(components)}")
    island_count = 0
    for i, comp in enumerate(components):
        xs = [c[2] for c in comp]
        ys = [c[3] for c in comp]
        label = ""
        if i == 0:
            label = " [メイン]"
        elif len(comp) <= 3:
            island_count += 1
            label = " [孤立島]"
        print(f"  Component {i}: {len(comp)} tiles, x={min(xs)}-{max(xs)}, y={min(ys)}-{max(ys)}{label}")

    if island_count > 0:
        print(f"  !! 孤立島 {island_count} 個検出 -マッピング不完全の可能性")
    print()

    # Cross-map reachability
    if reach_mg is not None and reach_mn is not None:
        reach_name = MAP_NAMES.get((reach_mg, reach_mn), f"({reach_mg},{reach_mn})")
        reach_tiles = {}
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            parts = k.split(",")
            mg, mn = int(parts[0]), int(parts[1])
            if mg == reach_mg and mn == reach_mn:
                reach_tiles[k] = v

        # BFS from main component across all maps
        main_comp = components[0] if components else set()
        start = next(iter(main_comp)) if main_comp else None
        reachable_target = set()

        if start:
            bfs_visited = set()
            queue = deque([start])
            while queue:
                cur = queue.popleft()
                if cur in bfs_visited:
                    continue
                bfs_visited.add(cur)
                if cur[0] == reach_mg and cur[1] == reach_mn:
                    reachable_target.add(cur)
                ck = f"{cur[0]},{cur[1]},{cur[2]},{cur[3]}"
                if ck not in data or not isinstance(data[ck], dict):
                    continue
                edges_d = data[ck]
                for direction in ("Up", "Down", "Left", "Right", "warp"):
                    if direction in edges_d:
                        if direction != "warp" and f"wall_{direction}" in edges_d:
                            continue
                        next_node = tuple(edges_d[direction])
                        if next_node not in bfs_visited:
                            queue.append(next_node)

        print(f"=== {reach_name} ({reach_mg},{reach_mn}) への到達可能性 ===")
        print(f"{reach_name} 総タイル数: {len(reach_tiles)}")
        print(f"{map_name}から到達可能な{reach_name}タイル: {len(reachable_target)} / {len(reach_tiles)}")
        if reachable_target:
            xs = [c[2] for c in reachable_target]
            ys = [c[3] for c in reachable_target]
            print(f"到達可能範囲: x={min(xs)}-{max(xs)}, y={min(ys)}-{max(ys)}")
            coverage = len(reachable_target) / max(len(reach_tiles), 1) * 100
            print(f"カバー率: {coverage:.1f}%")
            if coverage < 80:
                print(f"  !! カバー率低い -warp/接続の不足の可能性")
        else:
            print(f"  !! {reach_name}に到達不可能 -warp接続が不足")
        print()

    # Summary
    print("=== サマリ ===")
    issues = []
    if len(components) > 20:
        issues.append(f"コンポーネント数多い ({len(components)}): マッピング精度に課題")
    if total_walls / max(total_edges, 1) > 0.5:
        issues.append(f"Wall/Edge比率高い ({total_walls/max(total_edges,1):.2f}): 壁が多く移動困難")
    if fully_walled > 0:
        issues.append(f"完全壁タイル {fully_walled} 個: スタックリスク")
    if not issues:
        print("健全性: 良好")
    else:
        for issue in issues:
            print(f"  !! {issue}")


def find_route(data):
    """PC→R116の安全ルートを計算"""
    # デッドゾーンを特定
    stuck_tiles = set()
    for t in find_high_stuck_tiles(data):
        stuck_tiles.add(t)

    # 既知のNPCデッドゾーンも追加
    known_npc = [
        (0, 3, 42, 33), (0, 3, 42, 34), (0, 3, 42, 39), (0, 3, 42, 40),
        (0, 3, 42, 27), (0, 3, 42, 28), (0, 3, 42, 21), (0, 3, 42, 22),
        (0, 3, 33, 42), (0, 3, 36, 15),
    ]
    for t in known_npc:
        stuck_tiles.add(t)

    # PC exit (23,45) in Kanazumi
    start = (0, 3, 23, 45)

    print(f"Finding safe route from PC exit {start} to R116...")
    print(f"Avoiding {len(stuck_tiles)} dead zone tiles")

    path = safe_bfs(data, start, 0, 31, stuck_tiles)

    if path:
        print(f"Safe route found: {len(path)} steps")
        # Show key waypoints
        pos = list(start)
        deltas = {"Up": (0, -1), "Down": (0, 1), "Left": (-1, 0), "Right": (1, 0)}
        waypoints = [start]
        for i, d in enumerate(path):
            if d in deltas:
                pos[2] += deltas[d][0]
                pos[3] += deltas[d][1]
            if d == "warp" or i == len(path) - 1:
                waypoints.append(tuple(pos))

        print(f"Waypoints: {waypoints[:5]}...{waypoints[-3:]}")
        print(f"Directions: {' '.join(path[:20])}...")
    else:
        print("No safe route found!")
        # Try without avoidance
        path2 = safe_bfs(data, start, 0, 31)
        if path2:
            print(f"Route without avoidance: {len(path2)} steps")
        else:
            print("No route at all!")

    return path

def main():
    data = load_map()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python tools/map_analyzer.py route          # PC->R116 safe route")
        print("  python tools/map_analyzer.py deadzones       # NPC dead zones")
        print("  python tools/map_analyzer.py visualize 0 3   # Map visualization")
        print("  python tools/map_analyzer.py stuck           # Fully walled tiles")
        print("  python tools/map_analyzer.py health 0 3      # Graph health analysis")
        print("  python tools/map_analyzer.py health 0 3 0 31 # Health + reachability")
        return

    cmd = sys.argv[1]

    if cmd == "route":
        find_route(data)

    elif cmd == "deadzones":
        dz = find_deadzones(data)
        print(f"Found {len(dz)} dead zone candidates:")
        for mg, mn, x, y, walls, edges in sorted(dz):
            map_name = {(0,3): "Kanazumi", (0,31): "R116"}.get((mg,mn), f"({mg},{mn})")
            print(f"  {map_name} ({x},{y}): walls={walls} edges={edges}")

    elif cmd == "stuck":
        stuck = find_high_stuck_tiles(data)
        print(f"Found {len(stuck)} fully walled (stuck) tiles:")
        for mg, mn, x, y in sorted(stuck):
            map_name = {(0,3): "Kanazumi", (0,31): "R116"}.get((mg,mn), f"({mg},{mn})")
            print(f"  {map_name} ({x},{y})")

    elif cmd == "visualize":
        if len(sys.argv) >= 4:
            mg, mn = int(sys.argv[2]), int(sys.argv[3])
        else:
            mg, mn = 0, 3  # default: Kanazumi
        visualize_map(data, mg, mn)

    elif cmd == "health":
        if len(sys.argv) >= 4:
            mg, mn = int(sys.argv[2]), int(sys.argv[3])
        else:
            mg, mn = 0, 3  # default: Kanazumi
        reach_mg, reach_mn = None, None
        if len(sys.argv) >= 6:
            reach_mg, reach_mn = int(sys.argv[4]), int(sys.argv[5])
        health_check(data, mg, mn, reach_mg, reach_mn)

    else:
        print(f"Unknown command: {cmd}")

if __name__ == "__main__":
    main()
