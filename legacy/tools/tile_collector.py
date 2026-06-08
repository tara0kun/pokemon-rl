"""タイルデータ収集ツール — 画面タイル分類CNN学習用データを自動収集する。

★ v10.9z255 L3警告:
    バトル画面 (dialog UI) 中の収集はdoor誤分類ノイズを生む。
    v6e実験で val_acc 78.9%→69.2%劣化確認。
    将来: overworld限定の収集 (CB2=0x080857C5 チェック) 推奨。

mGBAスクリーンショットを16x16タイルに分割し、ExplorationMapの情報で
仮ラベルを付与して保存する。

使い方:
    python tools/tile_collector.py [port] [interval_sec] [count]
    python tools/tile_collector.py 5000 2.0 100   # 2秒間隔で100枚収集
    python tools/tile_collector.py all 3.0 50      # 全ポートから3秒間隔で50枚

タイル分類（仮ラベル）:
    - walkable:  歩行可能（ExplorationMapにノードあり）
    - wall:      壁（wall_エントリあり）
    - unknown:   未探索（ノードなし）
    - door:      warpエッジあり（建物入口等）
    - npc_area:  NPC付近（_g_safe保護あり）

出力構造:
    tile_data/
        walkable/tile_XXXX.png
        wall/tile_XXXX.png
        unknown/tile_XXXX.png
        door/tile_XXXX.png
"""

import sys
import os
import time
import json
import tempfile
import urllib.request
from pathlib import Path

# GBA screen: 240x160, tile: 16x16 = 15x10 tiles
SCREEN_W, SCREEN_H = 240, 160
TILE_SIZE = 16
TILES_X = SCREEN_W // TILE_SIZE   # 15
TILES_Y = SCREEN_H // TILE_SIZE   # 10

# プレイヤーは画面中央付近に表示される（GBAの仕様）
# 正確な位置はスクロールオフセット依存だが、概算で中央
PLAYER_SCREEN_TX = TILES_X // 2   # 7
PLAYER_SCREEN_TY = TILES_Y // 2   # 5


def capture_screenshot(port: int) -> str:
    """mGBA ソケットプロトコルでスクリーンショットを取得"""
    import socket
    TERM = b"<|END|>"
    tmp = os.path.join(tempfile.gettempdir(), f"tile_cap_{port}.png")
    tmp_win = tmp.replace("/", os.sep)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        s.sendall(f"core.screenshot,{tmp_win}".encode() + TERM)
        buf = b""
        while TERM not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        if os.path.exists(tmp):
            return tmp
    except Exception:
        pass
    return ""


def _api_get_json(port: int, endpoint: str, params: dict):
    """mGBA ソケットプロトコルでコマンドを送り、値を返す"""
    import socket
    TERM = b"<|END|>"
    cmd = endpoint.replace("/", ".")
    values = ",".join(str(v) for v in params.values())
    msg = f"{cmd},{values}" if values else cmd
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        s.sendall(msg.encode() + TERM)
        buf = b""
        while TERM not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        resp = buf.split(TERM)[0].decode()
        if resp == "<|SUCCESS|>":
            return 0
        return int(resp)
    except Exception:
        return 0


def _api_read_range(port: int, addr: int, length: int) -> bytes:
    """mGBA ソケットプロトコルでreadRangeを呼び、バイト列を返す"""
    import socket
    TERM = b"<|END|>"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", port))
        s.sendall(f"core.readRange,{addr},{length}".encode() + TERM)
        buf = b""
        while TERM not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        hex_str = buf.split(TERM)[0].decode().strip()
        return bytes(int(b, 16) for b in hex_str.split(","))
    except Exception:
        return b"\x00" * length


# RAM addresses (Pokemon Emerald JP) — same as pokemon_env.py
ADDR_X       = 0x02037000   # プレイヤーX座標 (read16)
ADDR_Y       = 0x02037002   # プレイヤーY座標 (read16)
ADDR_SB1_PTR = 0x03005AEC   # SaveBlock1ポインタ (read32, IWRAM)


def read_game_position(port: int):
    """mGBA APIでプレイヤー位置を読み取る。

    X/Y は gPlayerAvatar 領域 (0x02037000) から read16 で取得。
    map_group / map_num は SaveBlock1 先頭 (SB1+4, SB1+5) から取得。
    pokemon_env.py の _get_game_state() と同じ方式。
    """
    try:
        # X, Y: readRangeで4バイト一括 (little-endian u16 x2)
        xy_data = _api_read_range(port, ADDR_X, 4)
        x = int.from_bytes(xy_data[0:2], "little")
        y = int.from_bytes(xy_data[2:4], "little")

        # SB1ポインタを間接参照 → SB1+0:X(2), +2:Y(2), +4:MapGroup(1), +5:MapNum(1)
        sb1_ptr = _api_get_json(port, "core/read32",
                                {"address": hex(ADDR_SB1_PTR)})
        if sb1_ptr is None or sb1_ptr == 0:
            return None

        sb1_data = _api_read_range(port, sb1_ptr, 6)
        mg = sb1_data[4]
        mn = sb1_data[5]

        return mg, mn, x, y
    except Exception as e:
        print(f"  read_game_position error: {e}")
        return None


def load_exploration_map(path="exploration_map.json"):
    """ExplorationMapのグラフデータを読み込む"""
    if not os.path.exists(path):
        print(f"Warning: {path} not found")
        return {}

    with open(path, "r") as f:
        data = json.load(f)

    graph = {}
    for key, edges in data.items():
        # key format: "(mg, mn, x, y)"
        parts = key.strip("()").split(",")
        if len(parts) == 4:
            node = (int(parts[0]), int(parts[1]),
                    int(parts[2]), int(parts[3]))
            graph[node] = edges
    return graph


def classify_tile(graph, mg, mn, tile_x, tile_y):
    """ExplorationMapの情報でタイルにラベルを付与。

    壁タイル判定を改善:
    1. グラフに存在し、3方向以上wall_ → wall（従来通り）
    2. グラフに存在しないが、隣接walkableタイルがwall_エッジで
       このタイルを指している → wall（推定壁）
    3. グラフに存在しない＋隣接にwalkableなし → unknown
    """
    node = (mg, mn, tile_x, tile_y)

    if node in graph:
        edges = graph[node]

        # warpエッジがあればdoor
        for direction, dest in edges.items():
            if direction.startswith("wall_"):
                continue
            if isinstance(dest, (list, tuple)) and len(dest) == 4:
                dest_mg, dest_mn = dest[0], dest[1]
                if dest_mg != mg or dest_mn != mn:
                    return "door"

        # 壁の数をカウント
        wall_count = sum(1 for d in edges if d.startswith("wall_"))
        move_count = sum(1 for d in edges
                         if d in ("Up", "Down", "Left", "Right"))

        if wall_count >= 3 and move_count <= 1:
            return "wall"  # ほぼ壁に囲まれている

        return "walkable"

    # グラフに存在しないタイル → 隣接ノードのwall_エッジで壁を推定
    # 隣接タイルがwall_方向でこのタイルを指しているか確認
    wall_evidence = 0
    direction_map = {
        "wall_Up": (0, -1),     # Up方向の壁 = 1つ上のタイルが壁
        "wall_Down": (0, 1),
        "wall_Left": (-1, 0),
        "wall_Right": (1, 0),
    }
    for wall_dir, (dx, dy) in direction_map.items():
        # このタイルの逆方向の隣接タイルを確認
        neighbor = (mg, mn, tile_x - dx, tile_y - dy)
        if neighbor in graph:
            neighbor_edges = graph[neighbor]
            if wall_dir in neighbor_edges:
                wall_evidence += 1

    if wall_evidence >= 1:
        return "wall"  # 隣接walkableが壁として認識している

    return "unknown"


def _is_overworld(port: int) -> bool:
    """CB2 state で overworld 判定 — v10.9z255 L3 対策
    バトル中/メニュー中はノイズだらけの dialog UI が含まれるため除外。
    """
    try:
        addr = 0x030022C0  # gMain.callback2
        r = _api_read_range(port, addr, 4)
        cb2 = int.from_bytes(r, "little")
        # Emerald JP overworld CB2 候補: 0x080857C5 / 0x080380FD
        return cb2 in (0x080857C5, 0x080380FD)
    except Exception:
        return False


def extract_tiles(screenshot_path, port, graph, output_dir, counter):
    """スクリーンショットからタイルを切り出してラベル付きで保存"""
    from PIL import Image

    # ★ v10.9z255 M1: overworld 以外は収集スキップ (dialog UI ノイズ回避)
    if not _is_overworld(port):
        print(f"  Port {port}: skip (not overworld)")
        return counter

    pos = read_game_position(port)
    if pos is None:
        return counter

    mg, mn, px, py = pos
    img = Image.open(screenshot_path)

    if img.size != (SCREEN_W, SCREEN_H):
        print(f"  Unexpected screen size: {img.size}")
        return counter

    saved = 0
    for ty in range(TILES_Y):
        for tx in range(TILES_X):
            # 画面上のタイル位置 → ゲーム内座標に変換
            # プレイヤーは画面中央なので、オフセット計算
            game_x = px + (tx - PLAYER_SCREEN_TX)
            game_y = py + (ty - PLAYER_SCREEN_TY)

            # タイルを切り出し
            left = tx * TILE_SIZE
            top = ty * TILE_SIZE
            tile_img = img.crop((left, top,
                                 left + TILE_SIZE,
                                 top + TILE_SIZE))

            # ラベル付与
            label = classify_tile(graph, mg, mn, game_x, game_y)

            # 保存
            label_dir = os.path.join(output_dir, label)
            os.makedirs(label_dir, exist_ok=True)

            fname = f"tile_{counter:06d}_m{mg}_{mn}_p{game_x}_{game_y}.png"
            tile_img.save(os.path.join(label_dir, fname))
            counter += 1
            saved += 1

    img.close()
    print(f"  Port {port}: ({px},{py}) map=({mg},{mn}) → {saved} tiles")
    return counter


def main():
    port_arg = sys.argv[1] if len(sys.argv) > 1 else "8888"
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 10

    if port_arg == "all":
        ports = [8888, 8889, 8890]
    else:
        ports = [int(port_arg)]

    output_dir = "tile_data"
    os.makedirs(output_dir, exist_ok=True)

    print("Loading exploration map...")
    graph = load_exploration_map()
    print(f"  {len(graph)} nodes loaded")

    tile_counter = 0
    # 既存ファイル数をカウントして継続番号にする
    for label_dir in Path(output_dir).iterdir():
        if label_dir.is_dir():
            tile_counter += len(list(label_dir.glob("*.png")))

    print(f"Starting collection: {count} screenshots, "
          f"{interval}s interval, ports={ports}")
    print(f"  Existing tiles: {tile_counter}")

    for i in range(count):
        t0 = time.time()
        for port in ports:
            ss = capture_screenshot(port)
            if ss and os.path.exists(ss):
                tile_counter = extract_tiles(
                    ss, port, graph, output_dir, tile_counter)
            else:
                print(f"  Port {port}: capture failed")

        elapsed = time.time() - t0
        remaining = count - i - 1
        if remaining > 0:
            sleep = max(0, interval - elapsed)
            if sleep > 0:
                time.sleep(sleep)

    # 集計
    print(f"\n=== Collection complete ===")
    total = 0
    for label_dir in sorted(Path(output_dir).iterdir()):
        if label_dir.is_dir():
            n = len(list(label_dir.glob("*.png")))
            total += n
            print(f"  {label_dir.name}: {n} tiles")
    print(f"  Total: {total} tiles")


if __name__ == "__main__":
    main()
