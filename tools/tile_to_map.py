"""Tile Classification → Map Construction Pipeline (Stub)

5層汎用AIアーキテクチャの第2層。
画面認識CNN (tile_classifier) の結果から自動的にマップグラフを構築する。

## 現状 (v0 スタブ)
- mGBAスクリーンショット取得 (record_mgba.py)
- 16x16タイル分割
- tile_classifier で各タイルを walkable/wall/unknown/door に分類
- 隣接関係から exploration_map 互換グラフを生成

## 制限
- 現状は単一スクリーンショットのみ (スクロール未対応)
- warp 情報は取れない (別途イベントデータ必要)
- キャラクター位置からの相対座標のみ (絶対座標不明)

## 今後の拡張
- スクロール追従 (カメラ移動検出)
- 複数フレームマージ
- exploration_map.json との merge
- pokemon_env.py への統合
"""
import argparse
import sys
import os
from pathlib import Path

# tile_classifier をインポート可能にする
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import numpy as np
    from PIL import Image
    import torch
    import torch.nn.functional as F
    from tile_classifier import TileCNN, CLASSES, DEFAULT_CHECKPOINT
except ImportError as e:
    print(f"Missing dependencies: {e}")
    sys.exit(1)


GBA_WIDTH = 240
GBA_HEIGHT = 160
TILE_SIZE = 16
GRID_W = GBA_WIDTH // TILE_SIZE   # 15
GRID_H = GBA_HEIGHT // TILE_SIZE  # 10


def screenshot_to_tiles(image_path: str) -> list:
    """16x16タイルに分割した numpy 配列リストを返す (左→右、上→下)"""
    img = Image.open(image_path).convert("RGB")
    if img.size != (GBA_WIDTH, GBA_HEIGHT):
        img = img.resize((GBA_WIDTH, GBA_HEIGHT))
    arr = np.array(img, dtype=np.uint8)  # (H, W, 3)
    tiles = []
    for row in range(GRID_H):
        for col in range(GRID_W):
            y0 = row * TILE_SIZE
            x0 = col * TILE_SIZE
            tile = arr[y0:y0+TILE_SIZE, x0:x0+TILE_SIZE]
            tiles.append(tile)
    return tiles


def classify_tiles(tiles: list, checkpoint: str = DEFAULT_CHECKPOINT) -> list:
    """タイルを分類。 [(class_name, confidence), ...]"""
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = TileCNN(num_classes=len(CLASSES)).to(device)
    ckpt = torch.load(checkpoint, map_location=device)
    sd = ckpt.get("model_state_dict", ckpt)
    model.load_state_dict(sd)
    model.eval()

    # Stack as batch
    batch = np.stack(tiles, axis=0).astype(np.float32) / 255.0  # (N, 16, 16, 3)
    batch = batch.transpose(0, 3, 1, 2)  # (N, 3, 16, 16)
    x = torch.from_numpy(batch).to(device)

    with torch.no_grad():
        logits = model(x)
        probs = F.softmax(logits, dim=1)
        conf, pred = probs.max(dim=1)

    results = []
    for p, c in zip(pred.cpu().numpy(), conf.cpu().numpy()):
        results.append((CLASSES[int(p)], float(c)))
    return results


def build_grid_graph(classifications: list) -> dict:
    """分類結果 (長さ 150) から隣接グラフを構築。
    画面中心 (7,4) を (0,0) 原点とした相対座標系で出力。

    グラフ形式:
    {
        (rel_x, rel_y): {
            'class': 'walkable'|'wall'|'door'|'unknown',
            'conf': 0.XX,
            'neighbors': {'Up': (x,y)|None, ...}  # walkableのみ
        }
    }
    """
    grid = {}
    center_col = GRID_W // 2  # 7
    center_row = GRID_H // 2  # 5

    for row in range(GRID_H):
        for col in range(GRID_W):
            idx = row * GRID_W + col
            cls, conf = classifications[idx]
            rel_x = col - center_col
            rel_y = row - center_row
            grid[(rel_x, rel_y)] = {
                'class': cls,
                'conf': conf,
                'neighbors': {}
            }

    # 隣接判定: 両方 walkable なら接続
    _dirs = {'Up': (0, -1), 'Down': (0, 1), 'Left': (-1, 0), 'Right': (1, 0)}
    for (x, y), info in grid.items():
        if info['class'] != 'walkable' and info['class'] != 'door':
            continue
        for d, (dx, dy) in _dirs.items():
            nb = (x + dx, y + dy)
            if nb in grid:
                nb_cls = grid[nb]['class']
                if nb_cls in ('walkable', 'door'):
                    info['neighbors'][d] = nb

    return grid


def print_grid(grid: dict) -> None:
    """ASCII 表示: ' '=walkable, '#'=wall, '?'=unknown, 'D'=door"""
    symbols = {'walkable': '.', 'wall': '#', 'unknown': '?', 'door': 'D'}
    print("  " + " ".join(f"{c:2d}" for c in range(-GRID_W//2, GRID_W//2+1)))
    for row in range(GRID_H):
        y = row - GRID_H // 2
        line = [f"{y:3d}"]
        for col in range(GRID_W):
            x = col - GRID_W // 2
            cls = grid.get((x, y), {}).get('class', ' ')
            line.append(f" {symbols.get(cls, '?')}")
        print("".join(line))


def main():
    parser = argparse.ArgumentParser(description="Tile → Map Graph Pipeline")
    parser.add_argument("image", help="GBA screenshot path (PNG or GIF)")
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--ascii", action="store_true", help="ASCII グリッド表示")
    args = parser.parse_args()

    tiles = screenshot_to_tiles(args.image)
    print(f"Extracted {len(tiles)} tiles")

    classifications = classify_tiles(tiles, args.checkpoint)
    print(f"Classified tiles:")
    from collections import Counter
    cnt = Counter(c for c, _ in classifications)
    for cls, n in cnt.most_common():
        print(f"  {cls}: {n}")

    grid = build_grid_graph(classifications)
    walkable = sum(1 for v in grid.values() if v['class'] in ('walkable', 'door'))
    edges = sum(len(v['neighbors']) for v in grid.values()) // 2
    print(f"\nGraph: {walkable} walkable nodes, {edges} edges")

    if args.ascii:
        print("\n--- ASCII Grid (.=walkable #=wall D=door ?=unknown) ---")
        print_grid(grid)


if __name__ == "__main__":
    main()
